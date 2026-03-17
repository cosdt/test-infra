"""Redis client factory (shared by both Lambda functions).

Call RedisClientFactory.setup_client() once at cold start — typically in the
Lambda module initializer, after secrets have been injected into env vars —
before using any helper that interacts with Redis (whitelist_redis_helper,
pr_redis_helper).
"""

import logging
import threading

import redis as redis_lib

logger = logging.getLogger(__name__)


class RedisClientFactory:
    """Class-level Redis client cache (one connection pool per process)."""

    _lock = threading.Lock()
    _client: redis_lib.Redis | None = None

    @classmethod
    def setup_client(cls, url: str, decode_responses: bool = True) -> None:
        """Configure and connect the Redis client.

        Replaces any previously configured client.  Must be called before
        get_client().
        """
        with cls._lock:
            cls._client = redis_lib.from_url(url, decode_responses=decode_responses)
        logger.debug("RedisClientFactory configured url=%s", url)

    @classmethod
    def get_client(cls) -> redis_lib.Redis:
        """Return the shared Redis client.

        Raises RuntimeError if setup_client() has not been called.
        """
        if cls._client is None:
            raise RuntimeError(
                "Redis client not configured. "
                "Call RedisClientFactory.setup_client() first."
            )
        return cls._client
