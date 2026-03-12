"""Redis-backed whitelist cache.

The raw whitelist YAML text is stored in Redis under _REDIS_KEY with a TTL of
CONFIG.whitelist_ttl_seconds (default 1200 s / 20 min).

Read path:
  1. Check Redis for the cached YAML string.
  2. Hit  → yaml.safe_load + parse → return mapping.
  3. Miss → read local file → write to Redis with TTL → parse → return mapping.

Both result_handler and webhook_handler use this module so that a whitelist
update takes effect within at most one TTL period without a server restart.
"""

import logging
from urllib.parse import urlparse

import yaml
import redis as redis_lib
from github import Github
from github.GithubException import GithubException

import utils
from config import RelayConfig

logger = logging.getLogger(__name__)

_REDIS_KEY = "oot:whitelist_yaml"

_redis_client: redis_lib.Redis | None = None


def _read_whitelist_from_github_url(url: str) -> str:
    """Read whitelist YAML content from a GitHub blob URL via PyGithub SDK.

    Supported format:
      https://github.com/<owner>/<repo>/blob/<ref>/<path/to/file.yaml>
    """
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
    except GithubException as e:
        raise RuntimeError(
            f"Failed to fetch whitelist from GitHub URL {url}: {e}"
        ) from e


def _get_redis(config: RelayConfig) -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(config.redis_url, decode_responses=True)
    return _redis_client


def load_allowlist_info_map(config: RelayConfig) -> dict[str, dict]:
    """Return device → {level, repo, url, oncall} from the Redis-cached whitelist."""
    r = _get_redis(config)
    cached = r.get(_REDIS_KEY)
    if cached is not None:
        logger.debug("whitelist cache hit key=%s", _REDIS_KEY)
        yaml_str = cached
    else:
        logger.info(
            "whitelist cache miss – loading %s and caching for %ss",
            config.whitelist_path,
            config.whitelist_ttl_seconds,
        )

        if config.whitelist_path.startswith("https://github.com/"):
            yaml_str = _read_whitelist_from_github_url(config.whitelist_path)
        else:
            with open(config.whitelist_path, "r", encoding="utf-8") as f:
                yaml_str = f.read()

        r.setex(_REDIS_KEY, config.whitelist_ttl_seconds, yaml_str)
        logger.debug(
            "whitelist cached %d bytes in Redis key=%s", len(yaml_str), _REDIS_KEY
        )

    raw: dict = yaml.safe_load(yaml_str) or {}
    mapping = utils.parse_allowlist_info_map(raw)
    logger.debug("allowlist loaded: %d device(s)", len(mapping))
    return mapping
