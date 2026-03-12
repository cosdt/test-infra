import hmac
import hashlib
import logging
import re
import yaml
from fastapi import HTTPException

from github import Auth, Github, GithubIntegration

from config import RelayConfig

logger = logging.getLogger(__name__)


WHITELIST_LEVELS = ("L1", "L2", "L3", "L4")


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


def parse_allowlist_info_map(raw: dict) -> dict[str, dict]:
    """Parse an already-loaded whitelist dict and return device → {level, repo, url, oncall}.

    This is the core parsing logic shared by load_allowlist_info_map (file-based)
    and whitelist_cache (Redis-backed).  Accepts the top-level dict as returned
    by yaml.safe_load on a whitelist YAML.
    """
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Invalid whitelist: expected dict (with L1-L4), got {type(raw).__name__}"
        )
    # Backward-compatible: bare list treated as L1 entries.
    if isinstance(raw, list):
        raw = {"L1": raw}

    mapping: dict[str, dict] = {}
    errors: list[str] = []

    for level in WHITELIST_LEVELS:
        entries = raw.get(level) or []
        if not isinstance(entries, list):
            raise RuntimeError(
                f"Invalid whitelist: key {level} must map to a list, got {type(entries).__name__}"
            )
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
            "Invalid whitelist; fix config errors: " + "; ".join(preview)
        )

    return mapping


def load_allowlist_info_map(path: str) -> dict[str, dict]:
    """Parse whitelist.yaml and return device → {level, repo, url, oncall}.

    - Preserves L1/L2/L3/L4 semantics in the returned info.
    - `url` can be provided directly or derived from `repo`.
    """
    by_level = load_whitelist_by_level(path)
    return parse_allowlist_info_map(by_level)


def load_allowlist_map(path: str) -> dict[str, str]:
    """Compatibility wrapper: device -> repo html url."""
    info_map = load_allowlist_info_map(path)
    return {device: info.get("url", "") for device, info in info_map.items()}


def get_private_key(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError as e:
        raise RuntimeError(
            f"GitHub App private key file not found: {path}. Set GITHUB_APP_PRIVATE_KEY_PATH or create the file."
        ) from e
    except Exception as e:
        raise RuntimeError(f"Failed to read GitHub App private key: {path}: {e}") from e


_integration: GithubIntegration | None = None


def _get_integration(config: RelayConfig) -> GithubIntegration:
    global _integration
    if _integration is None:
        _integration = GithubIntegration(int(config.github_app_id), get_private_key(config.github_app_private_key_path))
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


def verify_signature(config: RelayConfig, body: bytes, signature: str):
    mac = hmac.new(config.github_webhook_secret_bytes, body, hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Bad signature")


def get_installation_token(config: RelayConfig, installation_id):
    # Keep signature for compatibility; use PyGithub integration internally.
    return _get_integration(config).get_access_token(int(installation_id)).token


def list_installation_repositories(installation_token: str, max_results: int = 1000) -> list[dict]:
    """List repositories accessible to a GitHub App installation.
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
        if len(repos) >= max_results:
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
