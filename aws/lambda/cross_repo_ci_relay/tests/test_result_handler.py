import unittest
from unittest.mock import MagicMock, patch

from result.result_handler import handle, validate_payload
from utils.types import HTTPException


def _cfg():
    cfg = MagicMock()
    cfg.hud_api_url = "http://hud/api/oot-ci-events"
    cfg.hud_bot_key = "bot-key-123"
    cfg.redis_endpoint = "host:6379"
    cfg.redis_login = ""
    cfg.oot_status_ttl = 259200
    return cfg


def _token_data():
    return {
        "downstream_repo": "org/repo",
        "head_sha": "abc123",
        "upstream_repo": "pytorch/pytorch",
        "pr_number": 42,
    }


def _payload(status="completed", conclusion: str | None = "success"):
    return {
        "head_sha": "abc123",
        "status": status,
        "conclusion": conclusion,
        "workflow_name": "CI",
        "workflow_url": "http://ci.example.com/run/1",
        "downstream_repo": "org/repo",
        "upstream_repo": "pytorch/pytorch",
        "pr_number": 42,
    }


class TestValidatePayload(unittest.TestCase):
    def test_valid_completed_payload(self):
        result = validate_payload(
            {
                "head_sha": "abc",
                "status": "completed",
                "conclusion": "success",
                "workflow_name": "CI",
                "workflow_url": "http://x",
                "downstream_repo": "org/repo",
                "upstream_repo": "pytorch/pytorch",
                "pr_number": 42,
            }
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["conclusion"], "success")

    def test_valid_in_progress_payload(self):
        result = validate_payload(
            {
                "head_sha": "abc",
                "status": "in_progress",
                "conclusion": None,
                "workflow_name": "CI",
                "workflow_url": "http://x",
                "downstream_repo": "org/repo",
                "upstream_repo": "pytorch/pytorch",
                "pr_number": 42,
            }
        )
        self.assertEqual(result["status"], "in_progress")
        self.assertIsNone(result["conclusion"])

    def test_missing_required_field(self):
        with self.assertRaises(HTTPException) as ctx:
            validate_payload({"status": "completed"})
        self.assertEqual(ctx.exception.status_code, 400)

    def test_invalid_status(self):
        with self.assertRaises(HTTPException) as ctx:
            validate_payload(
                {
                    "head_sha": "abc",
                    "status": "unknown",
                    "conclusion": "success",
                    "workflow_name": "CI",
                    "workflow_url": "http://x",
                    "downstream_repo": "org/repo",
                    "upstream_repo": "pytorch/pytorch",
                    "pr_number": 42,
                }
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_completed_without_conclusion(self):
        with self.assertRaises(HTTPException) as ctx:
            validate_payload(
                {
                    "head_sha": "abc",
                    "status": "completed",
                    "conclusion": None,
                    "workflow_name": "CI",
                    "workflow_url": "http://x",
                    "downstream_repo": "org/repo",
                    "upstream_repo": "pytorch/pytorch",
                    "pr_number": 42,
                }
            )
        self.assertEqual(ctx.exception.status_code, 400)


class TestResultHandler(unittest.TestCase):
    def setUp(self):
        self.patcher_allowlist = patch("result.result_handler.load_allowlist")
        self.mock_load_allowlist = self.patcher_allowlist.start()

        mock_allowlist_map = MagicMock()
        mock_allowlist_map.get_repos_at_or_above_level.return_value = (["org/repo"], [])
        self.mock_load_allowlist.return_value = mock_allowlist_map

        self.patcher_oidc = patch("result.result_handler.verify_github_oidc_token")
        self.mock_verify_oidc = self.patcher_oidc.start()

        # By default, simulate an existing in_progress record in Redis
        # so that completed callbacks are accepted in the happy-path tests.
        self.patcher_redis = patch("result.result_handler.redis_helper")
        self.mock_redis = self.patcher_redis.start()
        self.mock_redis.get_oot_status.return_value = {
            "status": "in_progress",
            "downstream_repo": "org/repo",
            "head_sha": "abc123",
        }

    def tearDown(self):
        self.patcher_allowlist.stop()
        self.patcher_oidc.stop()
        self.patcher_redis.stop()

    @patch("result.result_handler._write_to_hud")
    def test_happy_path_completed(self, mock_hud):
        result = handle(_cfg(), "tok", _payload())
        self.assertEqual(result, {"ok": True, "status": "completed"})
        self.assertTrue(self.mock_redis.set_oot_status.called)
        self.assertTrue(mock_hud.called)
        self.mock_verify_oidc.assert_called_once_with("tok", "org/repo")

    @patch("result.result_handler._write_to_hud")
    def test_happy_path_in_progress(self, mock_hud):
        # in_progress does not require a pre-existing record
        self.mock_redis.get_oot_status.return_value = None
        result = handle(_cfg(), "tok", _payload(status="in_progress", conclusion=None))
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["ok"], True)
        self.mock_verify_oidc.assert_called_once_with("tok", "org/repo")

    @patch("result.result_handler._write_to_hud")
    def test_invalid_token(self, mock_hud):
        self.mock_verify_oidc.side_effect = HTTPException(401, "Invalid token")
        with self.assertRaises(HTTPException) as ctx:
            handle(_cfg(), "bad-tok", _payload())
        self.assertEqual(ctx.exception.status_code, 401)

    @patch("result.result_handler._write_to_hud")
    def test_hud_write_failure_does_not_raise(self, mock_hud):
        mock_hud.side_effect = Exception("HUD down")
        result = handle(_cfg(), "tok", _payload())
        self.assertEqual(result, {"ok": True, "status": "completed"})

    @patch("result.result_handler._write_to_hud")
    def test_redis_oot_write_failure_does_not_raise(self, mock_hud):
        self.mock_redis.set_oot_status.side_effect = Exception("Redis down")
        # set_oot_status is best-effort: exception must NOT propagate
        result = handle(_cfg(), "tok", _payload())
        self.assertEqual(result, {"ok": True, "status": "completed"})

    @patch("result.result_handler._write_to_hud")
    def test_repo_not_in_l2_returns_ignored(self, mock_hud):
        mock_allowlist_map = MagicMock()
        mock_allowlist_map.get_repos_at_or_above_level.return_value = (
            ["some/other"],
            [],
        )
        self.mock_load_allowlist.return_value = mock_allowlist_map

        result = handle(_cfg(), "tok", _payload())
        self.assertEqual(result, {"ok": True, "status": "ignored"})
        self.assertFalse(self.mock_redis.set_oot_status.called)
        self.assertFalse(mock_hud.called)

    @patch("result.result_handler._write_to_hud")
    def test_completed_without_prior_in_progress_rejected(self, mock_hud):
        """completed is rejected when no in_progress record exists in Redis."""
        self.mock_redis.get_oot_status.return_value = None
        with self.assertRaises(HTTPException) as ctx:
            handle(_cfg(), "tok", _payload(status="completed", conclusion="success"))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertFalse(self.mock_redis.set_oot_status.called)
        self.assertFalse(mock_hud.called)

    @patch("result.result_handler._write_to_hud")
    def test_duplicate_completed_rejected(self, mock_hud):
        """A second completed callback is rejected when Redis already shows completed."""
        self.mock_redis.get_oot_status.return_value = {"status": "completed"}
        with self.assertRaises(HTTPException) as ctx:
            handle(_cfg(), "tok", _payload(status="completed", conclusion="success"))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertFalse(self.mock_redis.set_oot_status.called)

    @patch("result.result_handler._write_to_hud")
    def test_completed_allowed_when_redis_read_fails(self, mock_hud):
        """When Redis raises during the ordering check, the handler fails-open
        (proceeds without the guard) to avoid blocking legitimate callbacks
        due to infrastructure issues."""
        # Simulate create_client raising
        self.mock_redis.create_client.side_effect = Exception("Redis unreachable")
        result = handle(
            _cfg(), "tok", _payload(status="completed", conclusion="success")
        )
        self.assertEqual(result, {"ok": True, "status": "completed"})

    @patch("result.result_handler._write_to_hud")
    def test_completed_allowed_when_redis_get_fails(self, mock_hud):
        """When get_oot_status raises a RedisError, the handler fails-open."""
        from redis.exceptions import RedisError as _RedisError

        self.mock_redis.get_oot_status.side_effect = _RedisError("timeout")
        result = handle(
            _cfg(), "tok", _payload(status="completed", conclusion="success")
        )
        self.assertEqual(result, {"ok": True, "status": "completed"})


if __name__ == "__main__":
    unittest.main()
