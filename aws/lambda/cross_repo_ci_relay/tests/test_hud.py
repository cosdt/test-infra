import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from utils.hud import write_hud
from utils.types import HTTPException


def _cfg(url="http://hud/api/oot-ci-events", key="bot-key"):
    cfg = MagicMock()
    cfg.hud_api_url = url
    cfg.hud_bot_key = key
    return cfg


class TestWriteHud(unittest.TestCase):
    @patch("utils.hud.urllib.request.urlopen")
    def test_empty_url_skips_request(self, mock_urlopen):
        write_hud(_cfg(url=""), {"status": "completed"}, "org/repo", {})
        mock_urlopen.assert_not_called()

    @patch("utils.hud.urllib.request.urlopen")
    def test_hud_payload_has_three_top_level_fields(self, mock_urlopen):
        resp = MagicMock()
        resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = resp

        body = {"status": "completed", "head_sha": "abc"}
        infra = {"queue_time": 1.0, "execution_time": 2.0}
        write_hud(_cfg(), body, "org/repo", infra)

        sent = json.loads(mock_urlopen.call_args[0][0].data)
        self.assertEqual(sent["body"], body)
        self.assertEqual(sent["verified_repo"], "org/repo")
        self.assertEqual(sent["infra"], infra)

    @patch("utils.hud.urllib.request.urlopen")
    def test_http_error_propagates_with_huds_status(self, mock_urlopen):
        # Transparent proxy: HUD's 4xx/5xx comes straight back to the caller.
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://hud", 422, "bad schema", {}, None
        )

        with self.assertRaises(HTTPException) as ctx:
            write_hud(_cfg(), {}, "org/repo", {})
        self.assertEqual(ctx.exception.status_code, 422)

    @patch("utils.hud.urllib.request.urlopen")
    def test_url_error_becomes_502(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("unreachable")

        with self.assertRaises(HTTPException) as ctx:
            write_hud(_cfg(), {}, "org/repo", {})
        self.assertEqual(ctx.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
