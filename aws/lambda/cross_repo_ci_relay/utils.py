import hmac
import hashlib
import logging

from config import RelayConfig

logger = logging.getLogger(__name__)


WHITELIST_LEVELS = ("L1", "L2", "L3", "L4")


def parse_allowlist_info_map(raw: dict) -> dict[str, dict]:
    """Parse a whitelist dict and return device → {level, repo, url, oncall}."""
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Invalid whitelist: expected dict (with L1-L4), got {type(raw).__name__}"
        )

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


# ================= Core utilities =================


class RelayHTTPException(Exception):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail


def verify_signature(config: RelayConfig, body: bytes, signature: str) -> None:
    mac = hmac.new(config.github_webhook_secret_bytes, body, hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    if not hmac.compare_digest(expected, signature):
        logger.warning("webhook signature mismatch")
        raise RelayHTTPException(status_code=401, detail="Bad signature")


def pick_repo_full_name_by_allowlist(repos, allow_url: str):
    allow_url_n = allow_url.rstrip("/") if allow_url else None
    matches = [
        r
        for r in repos
        if (r.get("html_url").rstrip("/") if r.get("html_url") else None) == allow_url_n
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0].get("full_name")
    return {"ambiguous": [r.get("full_name") for r in matches]}
