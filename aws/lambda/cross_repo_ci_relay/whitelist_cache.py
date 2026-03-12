"""Redis-backed whitelist cache.

The raw whitelist YAML text is stored in Redis under _REDIS_KEY with a TTL of
CONFIG.whitelist_ttl_seconds (default 1200 s / 20 min).

Read path:
  1. Check Redis for the cached YAML string.
  2. Hit  → yaml.safe_load + parse → return mapping.
  3. Miss → read local file → write to Redis with TTL → parse → return mapping.

Both result_handler and webhook_handler use this module so that a whitelist
update takes effect within at most one TTL period without a server restart.
Calling invalidate() forces an immediate cache miss (useful in tests).
"""

import logging

import yaml
import redis as redis_lib

import utils
from config import RelayConfig

logger = logging.getLogger(__name__)

_REDIS_KEY = "oot:whitelist_yaml"

_redis_client: redis_lib.Redis | None = None


def _get_redis(config: RelayConfig) -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(config.redis_url, decode_responses=True)
    return _redis_client


def _read_yaml_str(config: RelayConfig) -> str:
    """Return the raw whitelist YAML string, using Redis as a read-through cache."""
    r = _get_redis(config)
    cached = r.get(_REDIS_KEY)
    if cached is not None:
        logger.debug("whitelist cache hit key=%s", _REDIS_KEY)
        return cached

    # Cache miss: load from local file and populate Redis.
    logger.info("whitelist cache miss – loading %s and caching for %ss",
                config.whitelist_path, config.whitelist_ttl_seconds)
    with open(config.whitelist_path, "r", encoding="utf-8") as f:
        yaml_str = f.read()
    r.setex(_REDIS_KEY, config.whitelist_ttl_seconds, yaml_str)
    logger.debug("whitelist cached %d bytes in Redis key=%s", len(yaml_str), _REDIS_KEY)
    return yaml_str


def load_allowlist_info_map(config: RelayConfig) -> dict[str, dict]:
    """Return device → {level, repo, url, oncall} from the Redis-cached whitelist."""
    raw: dict = yaml.safe_load(_read_yaml_str(config)) or {}
    mapping = utils.parse_allowlist_info_map(raw)
    logger.debug("allowlist loaded: %d device(s)", len(mapping))
    return mapping


def load_allowlist_map(config: RelayConfig) -> dict[str, str]:
    """Return device → repo html url from the Redis-cached whitelist."""
    info_map = load_allowlist_info_map(config)
    return {device: info.get("url", "") for device, info in info_map.items()}


def invalidate(config: RelayConfig) -> None:
    """Delete the cached YAML from Redis (forces re-read on next request)."""
    _get_redis(config).delete(_REDIS_KEY)
    logger.info("whitelist cache invalidated key=%s", _REDIS_KEY)
