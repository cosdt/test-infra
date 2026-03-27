"""Redis-backed helpers for webhook Lambda allowlist caching."""

import logging
from urllib.parse import quote, urlparse

import redis as redis_lib
import yaml
from config import RelayConfig
from github import Github
from github.GithubException import GithubException
from utils import parse_allowlist_info_map


logger = logging.getLogger(__name__)

WHITELIST_REDIS_KEY = "oot:whitelist_yaml"
PROCESSED_DELIVERY_PREFIX = "oot:github_delivery:"
PROCESSED_DELIVERY_DEFAULT_TTL = 900  # the longest time of AWS lambda survival

_redis_client: redis_lib.Redis | None = None


def _split_endpoint_host_port(endpoint: str) -> tuple[str, int]:
    host = endpoint.strip()
    port = 6379

    if host.startswith(("redis://", "rediss://")):
        raise RuntimeError(
            "REDIS_ENDPOINT must be an AWS ElastiCache endpoint hostname or host:port, not a redis URL"
        )

    if "/" in host:
        raise RuntimeError(
            "REDIS_ENDPOINT must be an AWS ElastiCache endpoint hostname or host:port"
        )

    if ":" in host:
        maybe_host, maybe_port = host.rsplit(":", 1)
        if maybe_port.isdigit():
            host = maybe_host
            port = int(maybe_port)

    if not host:
        raise RuntimeError("REDIS_ENDPOINT must not be empty")

    return host, port


def _build_redis_url(config: RelayConfig) -> str:
    endpoint = (config.redis_endpoint or "").strip()
    login = (config.redis_login or "").strip()

    host, port = _split_endpoint_host_port(endpoint)

    auth = ""
    if login:
        username, password = (login.split(":", 1) + [""])[:2]
        if password:
            auth = f"{quote(username, safe='')}:{quote(password, safe='')}@"
        else:
            auth = f"{quote(username, safe='')}@"

    return f"rediss://{auth}{host}:{port}/0"


def _read_whitelist_from_github_url(url: str) -> str:
    """Fetch whitelist YAML from a GitHub blob URL (https://github.com/<owner>/<repo>/blob/<ref>/<path>)."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if (
        parsed.scheme not in ("http", "https")
        or parsed.netloc != "github.com"
        or len(parts) < 5
        or parts[2] != "blob"
    ):
        raise RuntimeError(
            "Invalid GitHub whitelist URL. Expected format: "
            "https://github.com/<owner>/<repo>/blob/<ref>/<path/to/file>"
        )

    owner, repo, _, ref = parts[:4]
    file_path = "/".join(parts[4:])

    try:
        gh = Github(timeout=20)
        repo_obj = gh.get_repo(f"{owner}/{repo}")
        content_file = repo_obj.get_contents(file_path, ref=ref)
        if isinstance(content_file, list):
            raise RuntimeError(f"GitHub URL points to a directory, not a file: {url}")
        return content_file.decoded_content.decode("utf-8")
    except GithubException as exc:
        raise RuntimeError(
            f"Failed to fetch whitelist from GitHub URL {url}: {exc}"
        ) from exc


def _get_redis(config: RelayConfig) -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(
            _build_redis_url(config),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis_client


def load_allowlist_info_map(config: RelayConfig) -> dict[str, dict]:
    """Return repo metadata loaded from Redis cache or a GitHub URL."""
    redis_client = None
    yaml_str = None

    try:
        redis_client = _get_redis(config)
        yaml_str = redis_client.get(WHITELIST_REDIS_KEY)
    except redis_lib.exceptions.RedisError as exc:
        logger.warning("redis cache read failed, falling back to GitHub: %s", exc)

    if yaml_str is not None:
        logger.debug("whitelist cache hit key=%s", WHITELIST_REDIS_KEY)
    else:
        logger.info(
            "whitelist cache miss - loading %s and caching for %ss",
            config.whitelist_url,
            config.whitelist_ttl_seconds,
        )

        yaml_str = _read_whitelist_from_github_url(config.whitelist_url)

        if redis_client is not None:
            try:
                redis_client.setex(
                    WHITELIST_REDIS_KEY, config.whitelist_ttl_seconds, yaml_str
                )
                logger.debug(
                    "whitelist cached %d bytes in Redis key=%s",
                    len(yaml_str),
                    WHITELIST_REDIS_KEY,
                )
            except redis_lib.exceptions.RedisError as exc:
                logger.warning(
                    "redis cache write failed, continuing without cache: %s", exc
                )

    raw: dict = yaml.safe_load(yaml_str) or {}
    mapping = parse_allowlist_info_map(raw)
    logger.debug("allowlist loaded: %d device(s)", len(mapping))
    return mapping


def has_seen_delivery(config: RelayConfig, delivery_id: str) -> bool:
    """Return True if this delivery ID was already recorded in Redis."""
    try:
        redis_client = _get_redis(config)
        key = PROCESSED_DELIVERY_PREFIX + delivery_id
        try:
            return redis_client.exists(key) == 1
        except redis_lib.exceptions.RedisError as exc:
            logger.warning("redis exists check failed for %s: %s", key, exc)
            return False
    except Exception as exc:
        logger.warning("failed to get redis client for delivery check: %s", exc)
        return False


def mark_delivery_processed(
    config: RelayConfig,
    delivery_id: str,
    ttl_seconds: int = PROCESSED_DELIVERY_DEFAULT_TTL,
) -> None:
    """Mark a delivery ID as processed in Redis with a TTL. Best-effort: failures are logged but do not raise."""
    try:
        redis_client = _get_redis(config)
        key = PROCESSED_DELIVERY_PREFIX + delivery_id
        try:
            redis_client.setex(key, int(ttl_seconds), "1")
            logger.debug("marked delivery processed key=%s ttl=%s", key, ttl_seconds)
        except redis_lib.exceptions.RedisError as exc:
            logger.warning("redis setex failed for %s: %s", key, exc)
    except Exception as exc:
        logger.warning("failed to get redis client to mark delivery: %s", exc)
