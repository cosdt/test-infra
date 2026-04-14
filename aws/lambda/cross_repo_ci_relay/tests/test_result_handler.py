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

    def test_in_progress_without_dispatch_timing_queue_time_is_none(self):
        self.mock_redis.get_timing.return_value = None

        result = handle(_cfg(), _payload(status="in_progress"))

        self.assertEqual(result, {"ok": True, "status": "in_progress"})
        _, _, infra = self.mock_hud.call_args[0]
        self.assertIsNone(infra["queue_time"])

    def test_in_progress_negative_queue_time_clamped_to_zero(self):
        # dispatch timestamp is in the future relative to in_progress (abnormal clock skew)
        self.mock_redis.get_timing.return_value = time.time() + 100

        handle(_cfg(), _payload(status="in_progress"))

        _, _, infra = self.mock_hud.call_args[0]
        self.assertEqual(infra["queue_time"], 0)

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

    def test_completed_without_dispatch_timing_no_queue_time(self):
        in_progress_at = time.time() - 20

        def _side_effect(config, repo, sha, phase, client=None):
            return in_progress_at if phase == "in_progress" else None

        self.mock_redis.get_timing.side_effect = _side_effect

        handle(_cfg(), _payload(status="completed"))

        _, _, infra = self.mock_hud.call_args[0]
        self.assertIsNone(infra["queue_time"])
        self.assertAlmostEqual(infra["execution_time"], 20, delta=1.0)

    def test_completed_without_in_progress_timing_no_execution_time(self):
        dispatch_at = time.time() - 60

        def _side_effect(config, repo, sha, phase, client=None):
            return dispatch_at if phase == "dispatch" else None

        self.mock_redis.get_timing.side_effect = _side_effect

        handle(_cfg(), _payload(status="completed"))

        _, _, infra = self.mock_hud.call_args[0]
        self.assertIsNone(infra["execution_time"])

    def test_completed_negative_queue_time_clamped_to_zero(self):
        now = time.time()

        def _side_effect(config, repo, sha, phase, client=None):
            # in_progress_at < dispatch_at (abnormal)
            return {
                "dispatch": now + 10,
                "in_progress": now - 5,
            }.get(phase)

        self.mock_redis.get_timing.side_effect = _side_effect

        handle(_cfg(), _payload(status="completed"))

        _, _, infra = self.mock_hud.call_args[0]
        self.assertEqual(infra["queue_time"], 0)

    def test_completed_negative_execution_time_clamped_to_zero(self):
        now = time.time()

        def _side_effect(config, repo, sha, phase, client=None):
            # in_progress_at is in the future relative to completed_at (abnormal)
            return {
                "dispatch": now - 60,
                "in_progress": now + 100,  # far in the future
            }.get(phase)

        self.mock_redis.get_timing.side_effect = _side_effect

        handle(_cfg(), _payload(status="completed"))

        _, _, infra = self.mock_hud.call_args[0]
        self.assertEqual(infra["execution_time"], 0)

    # --- HUD write ---

    def test_hud_written_with_payload_and_infra(self):
        payload = _payload(status="in_progress")
        handle(_cfg(), payload)

        self.mock_hud.assert_called_once()
        _, hud_payload, _ = self.mock_hud.call_args[0]
        self.assertIs(hud_payload, payload)

    def test_hud_write_failure_propagates(self):
        self.mock_hud.side_effect = RuntimeError("HUD unreachable")

        with self.assertRaises(RuntimeError, msg="HUD unreachable"):
            handle(_cfg(), _payload())

    # --- Redis error propagation ---

    def test_get_timing_redis_error_propagates(self):
        self.mock_redis.get_timing.side_effect = RedisError("timeout")

        with self.assertRaises(RedisError):
            handle(_cfg(), _payload(status="completed"))

    def test_create_client_failure_propagates(self):
        self.mock_redis.create_client.side_effect = RuntimeError(
            "Failed to create Redis client"
        )

        with self.assertRaises(RuntimeError):
            handle(_cfg(), _payload())


if __name__ == "__main__":
    unittest.main()
