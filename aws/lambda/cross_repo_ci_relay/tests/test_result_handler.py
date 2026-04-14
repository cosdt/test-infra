import time
import unittest
from unittest.mock import MagicMock, patch

from callback.result_handler import handle
from utils.types import TimingPhase


def _cfg():
    cfg = MagicMock()
    cfg.hud_api_url = "http://hud/api/oot-ci-events"
    cfg.hud_bot_key = "bot-key-123"
    cfg.redis_endpoint = "host:6379"
    cfg.redis_login = ""
    cfg.oot_status_ttl = 259200
    return cfg


def _body(status="completed"):
    return {
        "head_sha": "abc123",
        "status": status,
        "conclusion": "success" if status == "completed" else None,
        "workflow_name": "CI",
        "workflow_url": "http://ci.example.com/run/1",
        "downstream_repo": "org/repo",  # self-reported; Relay ignores it
        "upstream_repo": "pytorch/pytorch",
        "pr_number": 42,
    }


class TestResultHandler(unittest.TestCase):
    def setUp(self):
        self.patcher_allowlist = patch("callback.result_handler.load_allowlist")
        self.mock_load_allowlist = self.patcher_allowlist.start()
        mock_map = MagicMock()
        mock_map.get_repos_at_or_above_level.return_value = (["org/repo"], [])
        self.mock_load_allowlist.return_value = mock_map

        self.patcher_redis = patch("callback.result_handler.redis_helper")
        self.mock_redis = self.patcher_redis.start()
        self.mock_redis.create_client.return_value = MagicMock()
        self.mock_redis.get_timing.return_value = None

        self.patcher_hud = patch("callback.result_handler.write_hud")
        self.mock_hud = self.patcher_hud.start()

    def tearDown(self):
        self.patcher_allowlist.stop()
        self.patcher_redis.stop()
        self.patcher_hud.stop()

    # --- allowlist uses the OIDC-verified repo, not the body ---

    def test_verified_repo_not_in_l2_returns_ignored(self):
        mock_map = MagicMock()
        mock_map.get_repos_at_or_above_level.return_value = (["other/repo"], [])
        self.mock_load_allowlist.return_value = mock_map

        result = handle(_cfg(), _body(), verified_repo="org/repo")

        self.assertEqual(result, {"ok": True, "status": "ignored"})
        self.assertFalse(self.mock_redis.create_client.called)
        self.assertFalse(self.mock_hud.called)

    def test_body_downstream_repo_is_ignored_for_allowlist(self):
        # A tampered body cannot bypass the allowlist — Relay indexes by the
        # OIDC-verified repo.
        mock_map = MagicMock()
        mock_map.get_repos_at_or_above_level.return_value = (["attacker/repo"], [])
        self.mock_load_allowlist.return_value = mock_map

        body = _body()
        body["downstream_repo"] = "attacker/repo"  # lies

        result = handle(_cfg(), body, verified_repo="org/repo")

        self.assertEqual(result, {"ok": True, "status": "ignored"})

    # --- body is forwarded to HUD verbatim; infra carries verified_repo ---

    def test_body_is_passed_to_hud_unchanged(self):
        body = _body()
        handle(_cfg(), body, verified_repo="org/repo")

        # write_hud(config, body, verified_repo, infra)
        _, body_arg, verified_repo_arg, infra_arg = self.mock_hud.call_args[0]
        self.assertIs(body_arg, body)
        self.assertEqual(verified_repo_arg, "org/repo")
        # verified_repo is a sibling of infra, not nested inside it.
        self.assertNotIn("verified_repo", infra_arg)

    # --- timing ---

    def test_in_progress_records_timing_and_computes_queue_time(self):
        dispatch_at = time.time() - 30
        self.mock_redis.get_timing.return_value = dispatch_at

        result = handle(_cfg(), _body(status="in_progress"), verified_repo="org/repo")

        self.assertEqual(result, {"ok": True, "status": "in_progress"})
        # set_timing called with the verified repo, not body's downstream_repo.
        args, _ = self.mock_redis.set_timing.call_args
        self.assertEqual(args[1], "org/repo")
        self.assertEqual(args[3], TimingPhase.IN_PROGRESS)
        _, _, _, infra = self.mock_hud.call_args[0]
        self.assertAlmostEqual(infra["queue_time"], 30, delta=1.0)
        self.assertIsNone(infra["execution_time"])

    def test_completed_computes_both_queue_and_execution_time(self):
        now = time.time()
        dispatch_at = now - 60
        in_progress_at = now - 30

        def _side_effect(config, repo, sha, phase, client=None):
            return {
                TimingPhase.DISPATCH: dispatch_at,
                TimingPhase.IN_PROGRESS: in_progress_at,
            }.get(phase)

        self.mock_redis.get_timing.side_effect = _side_effect

        result = handle(_cfg(), _body(status="completed"), verified_repo="org/repo")

        self.assertEqual(result, {"ok": True, "status": "completed"})
        _, _, _, infra = self.mock_hud.call_args[0]
        self.assertAlmostEqual(infra["queue_time"], 30, delta=1.0)
        self.assertAlmostEqual(infra["execution_time"], 30, delta=1.0)

    # --- best-effort redis infra ---

    def test_get_timing_redis_error_does_not_break_handler(self):
        self.mock_redis.get_timing.return_value = None

        result = handle(_cfg(), _body(status="completed"), verified_repo="org/repo")

        self.assertEqual(result, {"ok": True, "status": "completed"})
        self.assertTrue(self.mock_hud.called)
        _, _, _, infra = self.mock_hud.call_args[0]
        self.assertIsNone(infra["queue_time"])
        self.assertIsNone(infra["execution_time"])

    def test_redis_client_unavailable_skips_timing(self):
        self.mock_redis.create_client.side_effect = RuntimeError("redis down")

        result = handle(_cfg(), _body(status="completed"), verified_repo="org/repo")

        self.assertEqual(result, {"ok": True, "status": "completed"})
        self.assertTrue(self.mock_hud.called)

    # --- HUD errors are propagated (transparent proxy) ---

    def test_hud_error_propagates(self):
        from utils.types import HTTPException

        self.mock_hud.side_effect = HTTPException(503, "HUD down")

        with self.assertRaises(HTTPException) as ctx:
            handle(_cfg(), _body(), verified_repo="org/repo")
        self.assertEqual(ctx.exception.status_code, 503)



if __name__ == "__main__":
    unittest.main()
