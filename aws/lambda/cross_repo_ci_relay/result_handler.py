import time

import yaml
from fastapi import HTTPException, Request
from github import Auth, Github

import utils
from config import CONFIG
from clickhouse_client_helper import CHCliFactory


CHCliFactory.setup_client(
    url=CONFIG.clickhouse_url,
    username=CONFIG.clickhouse_user,
    password=CONFIG.clickhouse_password,
    database=CONFIG.clickhouse_database,
)


_allowlist_cache: dict[str, dict] | None = None
_allowlist_cache_ts: float = 0.0
_ALLOWLIST_TTL: float = float(CONFIG.oot_allowlist_ttl_seconds)  # seconds


def _gh_client_for(repo_full_name: str) -> Github:
    """Return a PyGithub client authenticated as the App installation for repo_full_name.

    Falls back to an unauthenticated client (works for public repos) when the
    App is not installed on that repo.
    """
    try:
        jwt_token = utils.create_app_jwt()
        installation_id = utils.get_repo_installation_id(jwt_token, repo_full_name)
        token = utils.get_installation_token(jwt_token, installation_id)
        return Github(auth=Auth.Token(token))
    except Exception as e:
        print(
            f"[gh_client] app auth failed for {repo_full_name}, using unauthenticated: {e}"
        )
        return Github()


def _load_allowlist() -> dict[str, dict]:
    """Fetch allowlist yaml from GitHub and return device → {level, repo, url, oncall}.

    Result is cached for _ALLOWLIST_TTL seconds to avoid hitting the GitHub API
    on every incoming request.
    """
    global _allowlist_cache, _allowlist_cache_ts
    now = time.monotonic()
    if _allowlist_cache is not None and (now - _allowlist_cache_ts) < _ALLOWLIST_TTL:
        return _allowlist_cache

    def _load_from_local() -> dict[str, dict]:
        return utils.load_allowlist_info_map(CONFIG.whitelist_path)

    try:
        gh = _gh_client_for(CONFIG.oot_whitelist_repo)
        repo = gh.get_repo(CONFIG.oot_whitelist_repo)
        file_content = repo.get_contents(CONFIG.oot_whitelist_file)  # type: ignore[arg-type]
        raw: dict = yaml.safe_load(file_content.decoded_content) or {}  # type: ignore[union-attr]
    except Exception as e:
        # Common in local/dev when the App is not installed or when the path is misCONFIGured.
        # Falling back to local whitelist.yaml keeps /ci/result functional.
        print(
            f"[allowlist] failed to fetch {CONFIG.oot_whitelist_repo}:{CONFIG.oot_whitelist_file}, falling back to local {CONFIG.WHITELIST_PATH}: {e}"
        )
        mapping = _load_from_local()
        _allowlist_cache = mapping
        _allowlist_cache_ts = now
        return mapping

    # Just for local testing
    raw = yaml.safe_load(open("whitelist.yaml")) or {}

    mapping: dict[str, dict] = {}
    for level in ("L1", "L2", "L3", "L4"):
        entries = raw.get(level) or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            device = entry.get("device")
            if not device:
                continue
            mapping[device] = {
                "level": level,
                "repo": entry.get("repo", ""),
                "url": (entry.get("url") or "").rstrip("/"),
                "oncall": entry.get("oncall") or [],
            }

    _allowlist_cache = mapping
    _allowlist_cache_ts = now
    return mapping


def _ensure_device_from_allowlist(run_url: str, allowlist: dict) -> str:
    """Validate run_url against allowlist and return the matching device name."""
    if not run_url:
        raise HTTPException(status_code=400, detail="Missing url")

    repo_html_url = utils._repo_html_url_from_actions_run_url(run_url)
    if not repo_html_url:
        raise HTTPException(status_code=400, detail=f"Unsupported url: {run_url}")

    norm = repo_html_url.rstrip("/")
    for device, info in allowlist.items():
        if info["url"] == norm:
            return device

    raise HTTPException(
        status_code=403,
        detail={
            "message": "ci/result rejected: run url repo is not allowlisted",
            "repo_html_url": repo_html_url,
            "allowed": sorted(info["url"] for info in allowlist.values()),
        },
    )


_ch_table_ensured = False


def _ch_ensure_table():
    """Create oot_ci_results table if it does not exist (idempotent)."""
    global _ch_table_ensured
    if _ch_table_ensured:
        return
    sql = """
CREATE TABLE IF NOT EXISTS oot_ci_results (
    recorded_at   DateTime DEFAULT now(),
    device        String,
    upstream_repo String,
    commit_sha    String,
    workflow_name String,
    conclusion    String,
    status        String,
    run_url       String
) ENGINE = MergeTree()
ORDER BY (upstream_repo, commit_sha, device)
""".strip()
    CHCliFactory().client.command(sql)
    _ch_table_ensured = True


def _ch_write(
    *,
    device: str,
    upstream_repo: str,
    commit_sha: str,
    workflow_name: str,
    status: str,
    conclusion: str,
    run_url: str,
):
    """Insert one result row into oot_ci_results."""
    CHCliFactory().client.insert(
        "oot_ci_results",
        [
            [
                device,
                upstream_repo,
                commit_sha,
                workflow_name,
                conclusion,
                status,
                run_url,
            ]
        ],
        column_names=[
            "device",
            "upstream_repo",
            "commit_sha",
            "workflow_name",
            "conclusion",
            "status",
            "run_url",
        ],
    )


async def handle_ci_result(req: Request):
    data = await req.json()

    run_url = data.get("url", "")
    allowlist = _load_allowlist()
    device = _ensure_device_from_allowlist(run_url, allowlist)
    info = allowlist[device]
    level = info["level"]

    status = data.get("status")
    workflow_name = data["workflow_name"]
    upstream_repo = data["upstream_repo"]
    commit_sha = data["commit_sha"]
    conclusion = data["conclusion"]  # success / failure / cancelled
    print(f"[{device}] CI finished: {conclusion} (level={level})")

    # ── L1: forward only, no feedback to upstream ──────────────────────────
    if level == "L1":
        return {"ok": True, "action": "ignored"}

    # ── L2+: write result to ClickHouse (OOT HUD) ──────────────────────────
    _ch_ensure_table()
    _ch_write(
        device=device,
        upstream_repo=upstream_repo,
        commit_sha=commit_sha,
        workflow_name=workflow_name,
        status=status,
        conclusion=conclusion,
        run_url=run_url,
    )

    if level == "L2":
        return {"ok": True, "action": "hud_only"}
