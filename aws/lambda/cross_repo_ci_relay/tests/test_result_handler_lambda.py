import json
import unittest
from unittest.mock import patch

from result.lambda_function import lambda_handler
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
        mock_verify_oidc.assert_called_once_with("tok", "org/repo")
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
        mock_verify_oidc.assert_called_once_with("tok", "org/repo")
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
