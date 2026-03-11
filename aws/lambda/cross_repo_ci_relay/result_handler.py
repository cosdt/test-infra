from fastapi import HTTPException, Request

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


def _load_allowlist() -> dict[str, dict]:
    """Load local whitelist.yaml and return device → {level, repo, url, oncall}.

    This endpoint should stay functional in local dev without relying on GitHub.
    The allowlist is cached in-process; restart the server to pick up changes.
    """
    global _allowlist_cache
    if _allowlist_cache is None:
        _allowlist_cache = utils.load_allowlist_info_map(CONFIG.whitelist_path)
    return _allowlist_cache


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
