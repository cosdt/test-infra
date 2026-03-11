import hmac
import hashlib
import re
import time

import jwt
import yaml
from fastapi import HTTPException

from github import Auth, Github, GithubIntegration

import config

# ================= 配置 =================

GITHUB_API = "https://api.github.com"

_CFG = config.get_config()

GITHUB_APP_ID = _CFG.github_app_id

GITHUB_WEBHOOK_SECRET = _CFG.github_webhook_secret_bytes

GITHUB_APP_PRIVATE_KEY_PATH = _CFG.github_app_private_key_path

WHITELIST_PATH = _CFG.whitelist_path

WHITELIST_LEVELS = ("L1", "L2", "L3", "L4")

UPSTREAM_REPO = _CFG.upstream_repo


def load_whitelist_by_level(path: str) -> dict[str, list[dict]]:
    """Load whitelist.yaml and preserve the L1/L2/L3/L4 bucket structure.

    Returns a dict with exactly keys L1-L4. Missing keys are filled with empty lists.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise RuntimeError(
            f"whitelist.yaml not found: {path}. Set WHITELIST_PATH or create the file."
        ) from e
    except Exception as e:
        raise RuntimeError(f"Failed to parse whitelist.yaml: {path}: {e}") from e

    if raw is None:
        raw = {}
    # Backward-compatible: if the whole file is a list, treat it as L1 entries.
    if isinstance(raw, list):
        raw = {"L1": raw}
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Invalid whitelist.yaml: expected dict (with L1-L4), got {type(raw).__name__}"
        )

    by_level: dict[str, list[dict]] = {}
    for level in WHITELIST_LEVELS:
        entries = raw.get(level) or []
        if not isinstance(entries, list):
            raise RuntimeError(
                f"Invalid whitelist.yaml: key {level} must map to a list, got {type(entries).__name__}"
            )
        by_level[level] = entries

    return by_level


def load_allowlist_info_map(path: str) -> dict[str, dict]:
    """Parse whitelist.yaml and return device → {level, repo, url, oncall}.

    - Preserves L1/L2/L3/L4 semantics in the returned info.
    - `url` can be provided directly or derived from `repo`.
    """
    by_level = load_whitelist_by_level(path)
    mapping: dict[str, dict] = {}
    errors: list[str] = []

    for level in WHITELIST_LEVELS:
        entries = by_level.get(level) or []
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                errors.append(
                    f"{level}[{idx}] must be a dict, got {type(entry).__name__}"
                )
                continue
            device = entry.get("device")
            if not device or not isinstance(device, str):
                errors.append(f"{level}[{idx}].device is required and must be a string")
                continue

            repo = entry.get("repo") or ""
            url = entry.get("url")
            if (not url) and repo and isinstance(repo, str):
                url = f"https://github.com/{repo}"
            if not url or not isinstance(url, str):
                errors.append(
                    f"{level}[{idx}].url is required (or provide repo to derive url)"
                )
                continue

            norm_url = url.rstrip("/")
            prev = mapping.get(device)
            if prev and prev.get("url") != norm_url:
                errors.append(
                    f"device {device!r} has conflicting urls: {prev.get('url')!r} vs {norm_url!r}"
                )
                continue

            mapping[device] = {
                "level": level,
                "repo": repo,
                "url": norm_url,
                "oncall": entry.get("oncall") or [],
            }

    if errors:
        preview = errors[:10]
        raise RuntimeError(
            "Invalid whitelist.yaml; fix config errors: " + "; ".join(preview)
        )

    return mapping


def load_allowlist_map(path: str) -> dict[str, str]:
    """Compatibility wrapper: device -> repo html url."""
    info_map = load_allowlist_info_map(path)
    return {device: info.get("url", "") for device, info in info_map.items()}


whitelist_by_level: dict[str, list[dict]] = load_whitelist_by_level(WHITELIST_PATH)
allowlist_info_map: dict[str, dict] = load_allowlist_info_map(WHITELIST_PATH)
allowlist_map: dict[str, str] = {
    k: v.get("url", "") for k, v in allowlist_info_map.items()
}


with open(GITHUB_APP_PRIVATE_KEY_PATH, "r") as f:
    PRIVATE_KEY = f.read()


_integration: GithubIntegration | None = None


def _get_integration() -> GithubIntegration:
    global _integration
    if _integration is None:
        _integration = GithubIntegration(int(GITHUB_APP_ID), PRIVATE_KEY)
    return _integration


def _gh_request_json(
    *,
    token: str,
    verb: str,
    url: str,
    parameters: dict | None = None,
    headers: dict[str, str] | None = None,
    input: object | None = None,
):
    gh = Github(auth=Auth.Token(token), timeout=20)
    requester = gh._Github__requester
    _resp_headers, data = requester.requestJsonAndCheck(
        verb,
        url,
        parameters=parameters,
        headers=headers,
        input=input,
    )
    return data


# ================= 基础工具 =================


def verify_signature(body: bytes, signature: str):
    mac = hmac.new(GITHUB_WEBHOOK_SECRET, body, hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Bad signature")


def create_app_jwt():
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": GITHUB_APP_ID,
    }
    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")


def get_installation_token(jwt_token, installation_id):
    # Keep signature for compatibility; use PyGithub integration internally.
    return _get_integration().get_access_token(int(installation_id)).token


def list_installation_repositories(installation_token: str):
    """List repositories accessible to a GitHub App installation.

    Uses the installation access token (not the App JWT).
    """
    repos = []
    page = 1
    while True:
        data = _gh_request_json(
            token=installation_token,
            verb="GET",
            url="/installation/repositories",
            parameters={"per_page": 100, "page": page},
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        chunk = (data or {}).get("repositories", [])
        # Keep only what we need (avoid passing huge objects around)
        repos.extend(
            {
                "name": repo.get("name"),
                "full_name": repo.get("full_name"),
                "html_url": repo.get("html_url"),
            }
            for repo in chunk
        )
        if len(chunk) < 100:
            break
        page += 1
    return repos


def _norm_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.rstrip("/")


def pick_repo_full_name_by_allowlist(repos, allow_url: str):
    allow_url_n = _norm_url(allow_url)
    matches = [r for r in repos if _norm_url(r.get("html_url")) == allow_url_n]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0].get("full_name")
    return {"ambiguous": [r.get("full_name") for r in matches]}


def allowlist_find_device_by_repo_html_url(repo_html_url: str) -> str | None:
    repo_html_url_n = _norm_url(repo_html_url)
    for device, allow_url in allowlist_map.items():
        if _norm_url(allow_url) == repo_html_url_n:
            return device
    return None


def allowlist_pick_single_device() -> str:
    if len(allowlist_map) != 1:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "allowlist_map must contain exactly one downstream device for pull_request dispatch",
                "devices": sorted(allowlist_map.keys()),
            },
        )
    return next(iter(allowlist_map.keys()))


def pick_repo_full_name(repos, *, name: str, preferred_full_name: str | None = None):
    """Pick a repo full_name from a list by repo name.

    Returns:
      - str full_name when uniquely resolved
      - None when not found
      - {"ambiguous": [...]} when multiple matches exist
    """
    print(
        f"Looking for repo name={name} among {[r.get('full_name') for r in repos]} with preferred_full_name={preferred_full_name}"
    )
    matches = [r for r in repos if r.get("name") == name]
    if not matches:
        return None
    if preferred_full_name:
        for r in matches:
            if r.get("full_name") == preferred_full_name:
                return preferred_full_name
    if len(matches) == 1:
        return matches[0].get("full_name")
    return {"ambiguous": [r.get("full_name") for r in matches]}


def parse_actions_run_from_url(url: str):
    """Parse owner/repo and run_id from a GitHub Actions run URL.

    Supports:
      - https://github.com/<owner>/<repo>/actions/runs/<run_id>
      - https://github.com/<owner>/<repo>/runs/<run_id>
    """
    if not url:
        return None
    m = re.search(r"github\.com/([^/]+)/([^/]+)/(?:actions/)?runs/(\d+)", url)
    if not m:
        return None
    owner, repo, run_id = m.group(1), m.group(2), int(m.group(3))
    return owner, repo, run_id


def get_repo_installation_id(jwt_token: str, repo_full_name: str) -> int:
    if "/" not in repo_full_name:
        raise ValueError(f"Invalid repo full_name: {repo_full_name}")
    owner, repo = repo_full_name.split("/", 1)
    inst = _get_integration().get_repo_installation(owner, repo)
    return int(inst.id)


def _repo_html_url_from_actions_run_url(run_url: str) -> str | None:
    u = run_url or ""
    # Support both html URL and API URL forms
    m = re.search(r"github\.com/([^/]+)/([^/]+)/(?:actions/)?runs/\d+", u)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
    m = re.search(r"api\.github\.com/repos/([^/]+)/([^/]+)/actions/runs/\d+", u)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
    return None


def ensure_ci_result_from_allowed_repo(data: dict) -> str:
    run_url = data.get("url")
    if not run_url:
        raise HTTPException(status_code=400, detail="Missing url")

    repo_html_url = _repo_html_url_from_actions_run_url(run_url)
    if not repo_html_url:
        raise HTTPException(status_code=400, detail=f"Unsupported url: {run_url}")

    device = allowlist_find_device_by_repo_html_url(repo_html_url)
    if device:
        return device

    allowed_urls = {_norm_url(v) for v in allowlist_map.values()}
    raise HTTPException(
        status_code=403,
        detail={
            "message": "ci/result rejected: run url repo is not allowlisted",
            "repo_html_url": repo_html_url,
            "allowed": sorted(u for u in allowed_urls if u),
        },
    )
