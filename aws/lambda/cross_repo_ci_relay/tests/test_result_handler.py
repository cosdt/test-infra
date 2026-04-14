import time
import unittest
from unittest.mock import MagicMock, patch

from redis.exceptions import RedisError
from result.result_handler import handle


def _cfg():
    cfg = MagicMock()
    cfg.hud_api_url = "http://hud/api/oot-ci-events"
    cfg.hud_bot_key = "bot-key-123"
    cfg.redis_endpoint = "host:6379"
    cfg.redis_login = ""
    cfg.oot_status_ttl = 259200
    return cfg


def _payload(status="completed"):
    return {
        "head_sha": "abc123",
        "status": status,
        "conclusion": "success" if status == "completed" else None,
        "workflow_name": "CI",
        "workflow_url": "http://ci.example.com/run/1",
        "downstream_repo": "org/repo",
        "upstream_repo": "pytorch/pytorch",
        "pr_number": 42,
    }


class TestResultHandler(unittest.TestCase):
    def setUp(self):
        self.patcher_allowlist = patch("result.result_handler.load_allowlist")
        self.mock_load_allowlist = self.patcher_allowlist.start()
        mock_map = MagicMock()
        mock_map.get_repos_at_or_above_level.return_value = (["org/repo"], [])
        self.mock_load_allowlist.return_value = mock_map

        self.patcher_redis = patch("result.result_handler.redis_helper")
        self.mock_redis = self.patcher_redis.start()
        self.mock_redis.create_client.return_value = MagicMock()
        self.mock_redis.get_timing.return_value = None  # no timing data by default

        self.patcher_hud = patch("result.result_handler.write_hud")
        self.mock_hud = self.patcher_hud.start()

    def tearDown(self):
        self.patcher_allowlist.stop()
        self.patcher_redis.stop()
        self.patcher_hud.stop()

    # --- allowlist checks ---

    def test_repo_not_in_l2_returns_ignored(self):
        mock_map = MagicMock()
        mock_map.get_repos_at_or_above_level.return_value = (["other/repo"], [])
        self.mock_load_allowlist.return_value = mock_map

        result = handle(_cfg(), _payload())

        self.assertEqual(result, {"ok": True, "status": "ignored"})
        self.assertFalse(self.mock_redis.create_client.called)
        self.assertFalse(self.mock_hud.called)

    # --- in_progress status ---

    def test_in_progress_records_timing_and_computes_queue_time(self):
        dispatch_at = time.time() - 30
        self.mock_redis.get_timing.return_value = dispatch_at

        result = handle(_cfg(), _payload(status="in_progress"))

        self.assertEqual(result, {"ok": True, "status": "in_progress"})
        # set_timing must be called for the "in_progress" phase
        self.mock_redis.set_timing.assert_called_once()
        self.assertEqual(self.mock_redis.set_timing.call_args[0][3], "in_progress")
        # get_timing must read the "dispatch" phase
        self.assertEqual(self.mock_redis.get_timing.call_args[0][3], "dispatch")
        # queue_time should be approximately 30s
        _, _, infra = self.mock_hud.call_args[0]
        self.assertAlmostEqual(infra["queue_time"], 30, delta=1.0)
        self.assertIsNone(infra["execution_time"])

    # --- completed status ---

    def test_completed_computes_both_queue_and_execution_time(self):
        now = time.time()
        dispatch_at = now - 60
        in_progress_at = now - 30

        def _side_effect(config, repo, sha, phase, client=None):
            return {
                "dispatch": dispatch_at,
                "in_progress": in_progress_at,
            }.get(phase)

        self.mock_redis.get_timing.side_effect = _side_effect

        result = handle(_cfg(), _payload(status="completed"))

        self.assertEqual(result, {"ok": True, "status": "completed"})
        _, _, infra = self.mock_hud.call_args[0]
        self.assertAlmostEqual(infra["queue_time"], 30, delta=1.0)
        self.assertAlmostEqual(infra["execution_time"], 30, delta=1.0)

    def test_hud_write_failure_propagates(self):
        self.mock_hud.side_effect = RuntimeError("HUD unreachable")

        with self.assertRaises(RuntimeError, msg="HUD unreachable"):
            handle(_cfg(), _payload())

    # --- Redis error propagation ---

    def test_get_timing_redis_error_propagates(self):
        self.mock_redis.get_timing.side_effect = RedisError("timeout")

        with self.assertRaises(RedisError):
            handle(_cfg(), _payload(status="completed"))

if __name__ == "__main__":
    unittest.main()
