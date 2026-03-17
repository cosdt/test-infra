"""Redis-backed upstream PR info cache (shared by both Lambda functions).

Manages one key group:
  oot:pr_info:{upstream_repo}:{sha}  →  JSON {pr_number, installation_id, labeled_devices}

The installation_id is the GitHub App installation ID for the upstream repo,
needed by the result handler to mint an installation token for creating/updating
check runs without storing any long-lived credentials.
"""

import json
import logging

from config import RelayConfig
from redis_client_helper import RedisClientFactory

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "oot:pr_info"


def _pr_key(upstream_repo: str, sha: str) -> str:
    return f"{_REDIS_KEY_PREFIX}:{upstream_repo}:{sha}"


def cache_pr_info(
    config: RelayConfig,
    upstream_repo: str,
    sha: str,
    pr_number: int,
    installation_id: int,
    labeled_devices: list[str],
) -> None:
    """Store PR metadata and current ciflow/oot label state; resets TTL on every call."""
    r = RedisClientFactory.get_client()
    key = _pr_key(upstream_repo, sha)
    value = json.dumps(
        {
            "pr_number": pr_number,
            "installation_id": installation_id,
            "labeled_devices": labeled_devices,
        }
    )
    r.setex(key, config.pr_info_ttl_seconds, value)
    logger.debug(
        "pr_info cached repo=%s sha=%.12s pr=%s devices=%s",
        upstream_repo,
        sha,
        pr_number,
        labeled_devices,
    )


def get_pr_info(config: RelayConfig, upstream_repo: str, sha: str) -> dict | None:
    """Return PR info dict or None if not found / TTL expired."""
    r = RedisClientFactory.get_client()
    raw = r.get(_pr_key(upstream_repo, sha))
    if raw is None:
        return None
    return json.loads(raw)


def add_labeled_device(
    config: RelayConfig,
    upstream_repo: str,
    sha: str,
    device: str,
) -> dict | None:
    """Append device to labeled_devices list; return updated pr_info or None if key not found.

    This is called when the webhook receives a pull_request labeled event for
    a ciflow/oot/<device> label.  A missing key means the webhook dispatch event
    has not been seen yet (e.g. cold-start race) — callers should handle None gracefully.
    """
    r = RedisClientFactory.get_client()
    key = _pr_key(upstream_repo, sha)
    raw = r.get(key)
    if raw is None:
        logger.warning(
            "add_labeled_device: pr_info not found in Redis repo=%s sha=%.12s device=%s",
            upstream_repo,
            sha,
            device,
        )
        return None
    info = json.loads(raw)
    devices: list[str] = info.get("labeled_devices") or []
    if device not in devices:
        devices.append(device)
        info["labeled_devices"] = devices
        r.setex(key, config.pr_info_ttl_seconds, json.dumps(info))
        logger.debug(
            "labeled_device added repo=%s sha=%.12s device=%s",
            upstream_repo,
            sha,
            device,
        )
    return info
