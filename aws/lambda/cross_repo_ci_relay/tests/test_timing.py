import json
import time
import unittest
from unittest.mock import MagicMock, patch

import utils.redis_helper as redis_helper
from result import result_handler
from webhook import event_handler


class TestRedisTimingHelpers(unittest.TestCase):
    def setUp(self):
        self.cfg = MagicMock()
        self.cfg.oot_status_ttl = 3600
        self.mock_client = MagicMock()
        self.patcher_create = patch(
            "utils.redis_helper.create_client", return_value=self.mock_client
        )
        self.mock_create = self.patcher_create.start()

    def tearDown(self):
        self.patcher_create.stop()

    def test_set_and_get_dispatch_time(self):
        # set_dispatch_time should setex a JSON with dispatch_at
        ts = 12345.6
        redis_helper.set_dispatch_time(self.cfg, "org/repo", "abc123", ts)
        self.mock_client.setex.assert_called_once()
        # Simulate get returning the JSON
        self.mock_client.get.return_value = json.dumps({"dispatch_at": ts})
        val = redis_helper.get_timing(self.cfg, "org/repo", "abc123")
        self.assertEqual(val.get("dispatch_at"), ts)

    def test_update_timing_merges_fields(self):
        # existing value present
        self.mock_client.get.return_value = json.dumps({"dispatch_at": 1.0})
        redis_helper.update_timing(
            self.cfg, "org/repo", "abc123", {"in_progress_at": 2.0}
        )
        # setex called with merged data
        self.mock_client.setex.assert_called()
        args = self.mock_client.setex.call_args[0]
        # payload is third arg
        payload = json.loads(args[2])
        self.assertEqual(payload["dispatch_at"], 1.0)
        self.assertEqual(payload["in_progress_at"], 2.0)


class TestEventHandlerDispatchTiming(unittest.TestCase):
    @patch("webhook.event_handler.gh_helper.create_repository_dispatch")
    @patch("webhook.event_handler.redis_helper.set_dispatch_time")
    @patch("webhook.event_handler.gh_helper.get_repo_access_token", return_value="tok")
    def test_dispatch_records_dispatch_time(
        self, mock_token, mock_set_dispatch, mock_create_dispatch
    ):
        cfg = MagicMock()
        cfg.github_app_secret = "secret"
        cfg.callback_token_ttl = 3600
        payload = {
            "event_type": "pull_request",
            "delivery_id": "d1",
            "payload": {
                "pull_request": {"head": {"sha": "abc123"}},
                "repository": {"full_name": "pytorch/pytorch"},
            },
        }
        # Call _dispatch_one directly
        event_handler._dispatch_one(
            config=cfg,
            downstream_repo="org/repo",
            event_type="ci",
            client_payload=payload,
        )
        # set_dispatch_time should be called with the sha
        mock_set_dispatch.assert_called_once()
        args = mock_set_dispatch.call_args[0]
        self.assertEqual(args[1], "org/repo")
        self.assertEqual(args[2], "abc123")


class TestResultHandlerTimingIntegration(unittest.TestCase):
    def setUp(self):
        # Patch redis_helper functions used by result_handler
        self.patcher_get = patch("result.result_handler.redis_helper.get_timing")
        self.mock_get = self.patcher_get.start()
        self.patcher_update = patch("result.result_handler.redis_helper.update_timing")
        self.mock_update = self.patcher_update.start()
        self.patcher_write = patch("result.result_handler._write_to_hud")
        self.mock_write = self.patcher_write.start()
        # Patch load_allowlist to allow downstream_repo to be L2
        self.patcher_allow = patch("result.result_handler.load_allowlist")
        self.mock_allow = self.patcher_allow.start()
        mock_map = MagicMock()
        mock_map.get_repos_at_or_above_level.return_value = (["org/repo"], [])
        self.mock_allow.return_value = mock_map
        self.cfg = MagicMock()
        self.cfg.hud_api_url = "http://hud"
        self.cfg.hud_bot_key = "bot"
        # ensure redis_helper.create_client won't fail; patch to return None where used
        self.patcher_create_client = patch(
            "result.result_handler.redis_helper.create_client", return_value=None
        )
        self.mock_create_client = self.patcher_create_client.start()

    def tearDown(self):
        self.patcher_get.stop()
        self.patcher_update.stop()
        self.patcher_write.stop()
        self.patcher_allow.stop()
        self.patcher_create_client.stop()

    def test_in_progress_computes_queue_time_and_updates_redis(self):
        # Simulate dispatch_at present
        self.mock_get.return_value = {"dispatch_at": time.time() - 30}
        payload = {
            "head_sha": "abc123",
            "status": "in_progress",
            "conclusion": None,
            "workflow_name": "CI",
            "workflow_url": "http://ci",
            "downstream_repo": "org/repo",
            "upstream_repo": "pytorch/pytorch",
            "pr_number": 1,
        }
        result_handler.handle(self.cfg, payload)
        # _write_to_hud called with infra containing queue_time
        self.mock_write.assert_called_once()
        infra = self.mock_write.call_args[0][2]
        self.assertIsNotNone(infra.get("queue_time"))
        # update_timing should have been called to store in_progress_at
        self.mock_update.assert_called_once()

    def test_completed_computes_both_durations(self):
        now = time.time()
        # dispatch at 60s ago, in_progress at 30s ago
        self.mock_get.return_value = {
            "dispatch_at": now - 60,
            "in_progress_at": now - 30,
        }
        payload = {
            "head_sha": "abc123",
            "status": "completed",
            "conclusion": "success",
            "workflow_name": "CI",
            "workflow_url": "http://ci",
            "downstream_repo": "org/repo",
            "upstream_repo": "pytorch/pytorch",
            "pr_number": 1,
        }
        result_handler.handle(self.cfg, payload)
        self.mock_write.assert_called_once()
        infra = self.mock_write.call_args[0][2]
        self.assertAlmostEqual(infra.get("queue_time"), 30, delta=1.0)
        self.assertAlmostEqual(infra.get("execution_time"), 30, delta=1.0)


if __name__ == "__main__":
    unittest.main()
