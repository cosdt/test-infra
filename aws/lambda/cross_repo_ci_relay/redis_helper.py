"""Redis client."""

import logging
from urllib.parse import quote

import redis as redis_lib
from config import RelayConfig


logger = logging.getLogger(__name__)

_ALLOWLIST_CACHE_KEY = "cross_repo_ci:allowlist_yaml"
PROCESSED_DELIVERY_PREFIX = "oot:github_delivery:"
PROCESSED_DELIVERY_DEFAULT_TTL = 900  # the longest time of AWS lambda survival

_client: redis_lib.Redis | None = None


def _parse_endpoint(endpoint: str) -> tuple[str, int]:
    host = endpoint.strip()

    if not host:
        raise RuntimeError("REDIS_ENDPOINT must not be empty")

    if host.startswith(("redis://", "rediss://")):
        raise RuntimeError(
            "REDIS_ENDPOINT must be a hostname or host:port, not a redis URL"
        )

    if "/" in host:
        raise RuntimeError("REDIS_ENDPOINT must be a hostname or host:port")

    port = 6379
    if ":" in host:
        maybe_host, maybe_port = host.rsplit(":", 1)
        if not maybe_port.isdigit():
            raise RuntimeError(f"REDIS_ENDPOINT has invalid port: {maybe_port!r}")
        host, port = maybe_host, int(maybe_port)

    return host, port


def _build_url(config: RelayConfig) -> str:
    host, port = _parse_endpoint(config.redis_endpoint or "")
    auth = ""
    login = (config.redis_login or "").strip()
    if login:
        username, password = (login.split(":", 1) + [""])[:2]
        if password:
            auth = f"{quote(username, safe='')}:{quote(password, safe='')}@"
        else:
            auth = f"{quote(username, safe='')}@"
    return f"rediss://{auth}{host}:{port}/0"


def _get_client(config: RelayConfig) -> redis_lib.Redis:
    global _client
    if _client is None:
        _client = redis_lib.from_url(
            _build_url(config),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client


def get_cached_yaml(config: RelayConfig) -> str | None:
    """Return cached allowlist YAML string, or None on cache miss or Redis error."""
    try:
        value = _get_client(config).get(_ALLOWLIST_CACHE_KEY)
        if value is not None:
            logger.debug("allowlist cache hit key=%s", _ALLOWLIST_CACHE_KEY)
        return value
    except redis_lib.exceptions.RedisError as exc:
        logger.warning("redis cache read failed, falling back to source: %s", exc)
        return None


def set_cached_yaml(config: RelayConfig, yaml_str: str) -> None:
    """Cache allowlist YAML string with TTL. Logs and ignores Redis errors."""
    try:
        _get_client(config).setex(
            _ALLOWLIST_CACHE_KEY, config.allowlist_ttl_seconds, yaml_str
        )
        logger.debug(
            "allowlist cached %d bytes key=%s", len(yaml_str), _ALLOWLIST_CACHE_KEY
        )
    except redis_lib.exceptions.RedisError as exc:
        logger.warning("redis cache write failed, continuing without cache: %s", exc)


def set_delivery_if_unseen(
    config: RelayConfig,
    delivery_id: str,
    ttl_seconds: int = PROCESSED_DELIVERY_DEFAULT_TTL,
) -> bool:
    """Atomically register a delivery ID in Redis using SET NX.

    Returns True if this is the first time the delivery is seen (caller should
    proceed with processing). Returns False if it was already present (duplicate).
    On Redis errors, returns True so the webhook is processed rather than silently
    dropped.
    """
    try:
        redis_client = _get_client(config)
        key = PROCESSED_DELIVERY_PREFIX + delivery_id
        try:
            # SET key value NX EX ttl — atomic check-and-set
            result = redis_client.set(key, "1", nx=True, ex=int(ttl_seconds))
            if result is None:
                # NX rejected: key already existed → duplicate delivery
                logger.info("duplicate delivery detected key=%s", key)
                return False
            logger.debug("new delivery registered key=%s ttl=%s", key, ttl_seconds)
            return True
        except redis_lib.exceptions.RedisError as exc:
            logger.warning(
                "redis SET NX failed for %s, processing anyway", key, exc_info=exc
            )
            return True
    except Exception as exc:
        logger.warning(
            "failed to get redis client for delivery set_nx, processing anyway",
            exc_info=exc,
        )
        return True
