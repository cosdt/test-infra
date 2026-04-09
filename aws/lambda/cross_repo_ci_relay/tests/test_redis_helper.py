import json
import unittest
from unittest.mock import MagicMock

import redis as redis_lib
from redis.exceptions import RedisError
from utils import redis_helper
from utils.redis_helper import (
    _ALLOWLIST_CACHE_KEY,
    _OOT_STATUS_PREFIX,
    create_client,
    get_cached_yaml,
    get_oot_status,
    set_cached_yaml,
    set_oot_status,
)


def _cfg():
    cfg = MagicMock()
    cfg.redis_endpoint = "host:6379"
    cfg.redis_login = ""
    cfg.allowlist_ttl_seconds = 600
    cfg.callback_token_ttl = 259200
    cfg.oot_status_ttl = 259200
    return cfg


class TestCachedYaml(unittest.TestCase):
    def setUp(self):
        redis_helper._cached_client = None
        redis_helper._cached_client_url = None

    def test_cache_hit(self):
        client = MagicMock()
        client.get.return_value = "L1:\n  - org/repo\n"
        self.assertEqual(get_cached_yaml(_cfg(), client=client), "L1:\n  - org/repo\n")
        client.get.assert_called_once_with(_ALLOWLIST_CACHE_KEY)

    def test_redis_error_returns_none(self):
        client = MagicMock()
        client.get.side_effect = RedisError("boom")
        self.assertIsNone(get_cached_yaml(_cfg(), client=client))

    def test_set_writes_with_ttl(self):
        client = MagicMock()
        set_cached_yaml(_cfg(), "yaml", client=client)
        client.setex.assert_called_once_with(_ALLOWLIST_CACHE_KEY, 600, "yaml")

    def test_create_client_reuses_cached_client_for_same_url(self):
        original_from_url = redis_lib.from_url
        client = MagicMock()
        mock_from_url = MagicMock(return_value=client)
        redis_lib.from_url = mock_from_url
        try:
            first = create_client(_cfg())
            second = create_client(_cfg())
        finally:
            redis_lib.from_url = original_from_url

        self.assertIs(first, client)
        self.assertIs(second, client)
        mock_from_url.assert_called_once()


class TestOOTStatus(unittest.TestCase):
    def setUp(self):
        redis_helper._cached_client = None
        redis_helper._cached_client_url = None

    def test_set_stores_json_with_ttl(self):
        client = MagicMock()
        set_oot_status(
            _cfg(), "org/repo", "abc123", {"status": "completed"}, client=client
        )
        args = client.setex.call_args[0]
        self.assertEqual(args[0], _OOT_STATUS_PREFIX + "org:repo:abc123")
        self.assertEqual(args[1], 259200)
        self.assertEqual(json.loads(args[2]), {"status": "completed"})

    def test_set_swallows_redis_error(self):
        client = MagicMock()
        client.setex.side_effect = RedisError("boom")
        # Should NOT raise
        set_oot_status(_cfg(), "org/repo", "sha", {}, client=client)

    def test_get_returns_parsed_dict(self):
        client = MagicMock()
        client.get.return_value = '{"status": "in_progress"}'
        result = get_oot_status(_cfg(), "org/repo", "sha123", client=client)
        self.assertEqual(result, {"status": "in_progress"})
        client.get.assert_called_once_with(_OOT_STATUS_PREFIX + "org:repo:sha123")

    def test_get_returns_none_on_miss(self):
        client = MagicMock()
        client.get.return_value = None
        result = get_oot_status(_cfg(), "org/repo", "sha", client=client)
        self.assertIsNone(result)

    def test_get_raises_on_redis_error(self):
        """RedisError must propagate so callers can apply fail-open logic."""
        client = MagicMock()
        client.get.side_effect = RedisError("down")
        with self.assertRaises(RedisError):
            get_oot_status(_cfg(), "org/repo", "sha", client=client)


if __name__ == "__main__":
    unittest.main()
