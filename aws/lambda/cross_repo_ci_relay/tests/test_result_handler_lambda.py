import json
import unittest
from unittest.mock import MagicMock, patch

from result.lambda_function import (
    _verify_callback_token,
    _verify_github_oidc_token,
    lambda_handler,
)
from utils.types import HTTPException


class TestResultLambdaFunction(unittest.TestCase):
    def setUp(self):
        import result.lambda_function as rl

        rl._cached_config = None

    @patch("result.lambda_function._get_config")
    @patch("result.lambda_function._verify_github_oidc_token")
    @patch("result.lambda_function.result_handler")
    def test_invalid_method(self, mock_handler, mock_verify_oidc, mock_get_config):
        event = {
            "requestContext": {"http": {"method": "GET", "path": "/github/result"}}
        }
        response = lambda_handler(event, {})
        self.assertEqual(response["statusCode"], 405)
        self.assertEqual(json.loads(response["body"])["detail"], "Method not allowed")
        self.assertFalse(mock_handler.handle.called)
        self.assertFalse(mock_verify_oidc.called)

    @patch("result.lambda_function._get_config")
    @patch("result.lambda_function._verify_github_oidc_token")
    @patch("result.lambda_function.result_handler")
    def test_invalid_path(self, mock_handler, mock_verify_oidc, mock_get_config):
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/github/wrong"}}
        }
        response = lambda_handler(event, {})
        self.assertEqual(response["statusCode"], 404)
        self.assertEqual(json.loads(response["body"])["detail"], "Not found")
        self.assertFalse(mock_handler.handle.called)
        self.assertFalse(mock_verify_oidc.called)

    @patch("result.lambda_function._get_config")
    @patch("result.lambda_function._verify_github_oidc_token")
    @patch("result.lambda_function.result_handler")
    def test_invalid_json(self, mock_handler, mock_verify_oidc, mock_get_config):
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/github/result"}},
            "body": "not-json",
            "isBase64Encoded": False,
        }
        response = lambda_handler(event, {})
        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(json.loads(response["body"])["detail"], "Invalid JSON body")
        self.assertFalse(mock_handler.handle.called)
        self.assertFalse(mock_verify_oidc.called)

    @patch("result.lambda_function._get_config")
    @patch("result.lambda_function._verify_callback_token")
    @patch("result.lambda_function._verify_github_oidc_token")
    @patch("result.lambda_function.result_handler")
    def test_http_exception(
        self, mock_handler, mock_verify_oidc, mock_verify_callback, mock_get_config
    ):
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/github/result"}},
            "headers": {"authorization": "tok"},
            "body": '{"status": "completed", "downstream_repo": "org/repo"}',
            "isBase64Encoded": False,
        }
        mock_verify_oidc.side_effect = HTTPException(401, "Invalid token")
        response = lambda_handler(event, {})
        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(json.loads(response["body"])["detail"], "Invalid token")
        self.assertFalse(mock_handler.handle.called)

    @patch("result.lambda_function.logger")
    @patch("result.lambda_function._get_config")
    @patch("result.lambda_function._verify_callback_token")
    @patch("result.lambda_function._verify_github_oidc_token")
    @patch("result.lambda_function.result_handler")
    def test_unhandled_exception(
        self,
        mock_handler,
        mock_verify_oidc,
        mock_verify_callback,
        mock_get_config,
        mock_logger,
    ):
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/github/result"}},
            "body": '{"status": "completed", "downstream_repo": "org/repo"}',
            "isBase64Encoded": False,
            "headers": {"authorization": "tok"},
        }
        mock_verify_oidc.return_value = None
        mock_handler.handle.side_effect = Exception("Boom")
        response = lambda_handler(event, {})
        self.assertEqual(response["statusCode"], 500)
        self.assertEqual(
            json.loads(response["body"])["detail"], "Internal server error"
        )
        self.assertTrue(mock_logger.exception.called)

    @patch("result.lambda_function._get_config")
    @patch("result.lambda_function._verify_callback_token")
    @patch("result.lambda_function._verify_github_oidc_token")
    @patch("result.lambda_function.result_handler")
    def test_happy_path(
        self, mock_handler, mock_verify_oidc, mock_verify_callback, mock_get_config
    ):
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/github/result"}},
            "headers": {"authorization": "tok"},
            "body": '{"status": "completed", "downstream_repo": "org/repo"}',
            "isBase64Encoded": False,
        }
        mock_handler.handle.return_value = {"ok": True, "status": "completed"}
        response = lambda_handler(event, {})
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(
            json.loads(response["body"]), {"ok": True, "status": "completed"}
        )
        mock_verify_oidc.assert_called_once_with(mock_get_config.return_value, "tok")
        mock_handler.handle.assert_called_once_with(
            mock_get_config.return_value,
            {"status": "completed", "downstream_repo": "org/repo"},
        )

    @patch("result.lambda_function._get_config")
    @patch("result.lambda_function._verify_callback_token")
    @patch("result.lambda_function._verify_github_oidc_token")
    @patch("result.lambda_function.result_handler")
    def test_base64_encoded_body(
        self, mock_handler, mock_verify_oidc, mock_verify_callback, mock_get_config
    ):
        import base64

        body = base64.b64encode(
            b'{"status": "completed", "downstream_repo": "org/repo"}'
        ).decode("utf-8")
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/github/result"}},
            "headers": {"authorization": "tok"},
            "body": body,
            "isBase64Encoded": True,
        }
        mock_handler.handle.return_value = {"ok": True, "status": "completed"}
        response = lambda_handler(event, {})
        self.assertEqual(response["statusCode"], 200)
        mock_verify_oidc.assert_called_once_with(mock_get_config.return_value, "tok")
        mock_handler.handle.assert_called_once_with(
            mock_get_config.return_value,
            {"status": "completed", "downstream_repo": "org/repo"},
        )

    @patch("result.lambda_function._get_config")
    @patch("result.lambda_function._verify_github_oidc_token")
    @patch("result.lambda_function.result_handler")
    def test_missing_authorization(
        self, mock_handler, mock_verify_oidc, mock_get_config
    ):
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/github/result"}},
            "body": '{"status": "completed", "downstream_repo": "org/repo"}',
            "isBase64Encoded": False,
        }
        response = lambda_handler(event, {})
        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(
            json.loads(response["body"])["detail"], "Missing authorization token"
        )
        self.assertFalse(mock_verify_oidc.called)
        self.assertFalse(mock_handler.handle.called)


class TestVerifyGithubOidcToken(unittest.TestCase):
    def setUp(self):
        self.patcher_jwks = patch(
            "result.lambda_function._jwks_client.get_signing_key_from_jwt"
        )
        self.mock_get_signing_key = self.patcher_jwks.start()
        self.mock_get_signing_key.return_value = MagicMock(key="fake-key")

        self.patcher_decode = patch("result.lambda_function.jwt.decode")
        self.mock_decode = self.patcher_decode.start()

        self.patcher_allowlist = patch("result.lambda_function.load_allowlist")
        self.mock_load_allowlist = self.patcher_allowlist.start()
        mock_map = MagicMock()
        mock_map.get_repos_at_or_above_level.return_value = (["org/repo"], [])
        self.mock_load_allowlist.return_value = mock_map

    def tearDown(self):
        self.patcher_jwks.stop()
        self.patcher_decode.stop()
        self.patcher_allowlist.stop()

    def test_valid_token_repo_in_allowlist(self):
        self.mock_decode.return_value = {"repository": "org/repo"}
        # Should not raise
        _verify_github_oidc_token(MagicMock(), "some.jwt.token")

    def test_repo_not_in_allowlist_raises_401(self):
        self.mock_decode.return_value = {"repository": "not/allowed"}
        with self.assertRaises(HTTPException) as ctx:
            _verify_github_oidc_token(MagicMock(), "some.jwt.token")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_bearer_prefix_stripped(self):
        self.mock_decode.return_value = {"repository": "org/repo"}
        _verify_github_oidc_token(MagicMock(), "Bearer some.jwt.token")
        self.mock_get_signing_key.assert_called_once_with("some.jwt.token")

    def test_invalid_token_raises_401(self):
        self.mock_get_signing_key.side_effect = Exception("bad JWT")
        with self.assertRaises(HTTPException) as ctx:
            _verify_github_oidc_token(MagicMock(), "bad.token")
        self.assertEqual(ctx.exception.status_code, 401)


class TestVerifyCallbackToken(unittest.TestCase):
    def setUp(self):
        self.patcher_decode = patch("result.lambda_function.jwt.decode")
        self.mock_decode = self.patcher_decode.start()

    def tearDown(self):
        self.patcher_decode.stop()

    def _config(self):
        cfg = MagicMock()
        cfg.github_app_secret = "secret"
        return cfg

    def _payload(self, **overrides):
        base = {
            "downstream_repo": "org/repo",
            "upstream_repo": "pytorch/pytorch",
            "head_sha": "abc123",
            "pr_number": 42,
        }
        base.update(overrides)
        return base

    def _matching_claims(self):
        return {
            "downstream_repo": "org/repo",
            "upstream_repo": "pytorch/pytorch",
            "head_sha": "abc123",
            "pr_number": 42,
        }

    def test_missing_token_raises_401(self):
        with self.assertRaises(HTTPException) as ctx:
            _verify_callback_token(self._config(), "", self._payload())
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Missing", ctx.exception.detail)

    def test_invalid_jwt_raises_401(self):
        self.mock_decode.side_effect = Exception("bad token")
        with self.assertRaises(HTTPException) as ctx:
            _verify_callback_token(self._config(), "bad.token", self._payload())
        self.assertEqual(ctx.exception.status_code, 401)

    def test_valid_token_all_claims_match(self):
        self.mock_decode.return_value = self._matching_claims()
        # Should not raise
        _verify_callback_token(self._config(), "valid.token", self._payload())

    def test_downstream_repo_mismatch_raises_401(self):
        self.mock_decode.return_value = {
            **self._matching_claims(),
            "downstream_repo": "other/repo",
        }
        with self.assertRaises(HTTPException) as ctx:
            _verify_callback_token(self._config(), "valid.token", self._payload())
        self.assertEqual(ctx.exception.status_code, 401)

    def test_pr_number_mismatch_raises_401(self):
        self.mock_decode.return_value = {**self._matching_claims(), "pr_number": 99}
        with self.assertRaises(HTTPException) as ctx:
            _verify_callback_token(self._config(), "valid.token", self._payload())
        self.assertEqual(ctx.exception.status_code, 401)

    def test_pr_number_none_in_payload_not_checked(self):
        # pr_number absent from claims, but payload has pr_number=None → skipped
        self.mock_decode.return_value = {
            "downstream_repo": "org/repo",
            "upstream_repo": "pytorch/pytorch",
            "head_sha": "abc123",
        }
        # Should not raise — pr_number=None means it's not added to expected_pairs
        _verify_callback_token(
            self._config(), "valid.token", self._payload(pr_number=None)
        )
