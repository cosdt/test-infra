import base64
import json
import unittest
from unittest.mock import MagicMock, patch

from result.lambda_function import lambda_handler
from utils.types import HTTPException


def _event(
    *,
    method="POST",
    path="/github/result",
    body=None,
    headers=None,
    base64_encoded=False,
):
    if body is None:
        body = json.dumps({"status": "completed", "callback_token": "cb.tok"})
    if base64_encoded:
        body = base64.b64encode(body.encode()).decode()
    if headers is None:
        hdrs = {"authorization": "Bearer oidc.tok"}
    else:
        hdrs = dict(headers)
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "body": body,
        "isBase64Encoded": base64_encoded,
        "headers": hdrs,
    }


class TestResultLambdaHandler(unittest.TestCase):
    def setUp(self):
        import utils.config

        utils.config._cached_config = None

    # --- routing ---

    def test_route_validation(self):
        response = lambda_handler(_event(path="/other"), {})
        self.assertEqual(response["statusCode"], 404)
        self.assertEqual(json.loads(response["body"])["detail"], "Not found")
        response = lambda_handler(_event(method="GET"), {})
        self.assertEqual(response["statusCode"], 405)
        self.assertEqual(json.loads(response["body"])["detail"], "Method not allowed")

    # --- auth / body validation (before token checks) ---

    @patch("result.lambda_function.get_config")
    def test_missing_authorization_header_returns_401(self, mock_get_config):
        response = lambda_handler(_event(headers={}), {})
        self.assertEqual(response["statusCode"], 401)
        self.assertIn("Missing", json.loads(response["body"])["detail"])

    @patch("result.lambda_function.get_config")
    def test_invalid_json_body_returns_400(self, mock_get_config):
        response = lambda_handler(_event(body="not-json"), {})
        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(json.loads(response["body"])["detail"], "Invalid JSON body")

    # --- JWT verification ---

    @patch("result.lambda_function.get_config")
    @patch("result.lambda_function.jwt_helper.verify_downstream_identity")
    def test_oidc_failure_returns_401(self, mock_oidc, mock_get_config):
        mock_oidc.side_effect = HTTPException(401, "Invalid authorization token")

        response = lambda_handler(_event(), {})

        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(
            json.loads(response["body"])["detail"], "Invalid authorization token"
        )

    @patch("result.lambda_function.get_config")
    @patch("result.lambda_function.jwt_helper.verify_relay_dispatch_token")
    @patch("result.lambda_function.jwt_helper.verify_downstream_identity")
    def test_dispatch_token_failure_returns_401(
        self, mock_oidc, mock_relay, mock_get_config
    ):
        mock_oidc.return_value = {"repository": "org/repo"}
        mock_relay.side_effect = HTTPException(401, "Invalid callback token")

        response = lambda_handler(_event(), {})

        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(
            json.loads(response["body"])["detail"], "Invalid callback token"
        )

    # --- happy path ---

    @patch("result.lambda_function.get_config")
    @patch("result.lambda_function.jwt_helper.verify_relay_dispatch_token")
    @patch("result.lambda_function.jwt_helper.verify_downstream_identity")
    @patch("result.lambda_function.result_handler.handle")
    def test_happy_path_enriches_payload_and_returns_200(
        self, mock_handle, mock_oidc, mock_relay, mock_get_config
    ):
        mock_oidc.return_value = {"repository": "org/repo"}
        mock_relay.return_value = {"head_sha": "abc123"}
        mock_handle.return_value = {"ok": True, "status": "completed"}

        response = lambda_handler(_event(), {})

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(
            json.loads(response["body"]), {"ok": True, "status": "completed"}
        )
        # Both JWT functions called with the config
        mock_oidc.assert_called_once_with(
            mock_get_config.return_value, "Bearer oidc.tok"
        )
        mock_relay.assert_called_once_with(mock_get_config.return_value, "cb.tok")
        # Payload must be enriched with claims before handler is called
        call_payload = mock_handle.call_args[0][1]
        self.assertEqual(call_payload["downstream_repo"], "org/repo")
        self.assertEqual(call_payload["head_sha"], "abc123")

    # --- error handling ---

    @patch("result.lambda_function.get_config")
    @patch("result.lambda_function.jwt_helper.verify_relay_dispatch_token")
    @patch("result.lambda_function.jwt_helper.verify_downstream_identity")
    @patch("result.lambda_function.result_handler.handle")
    def test_http_exception_from_handler_forwarded(
        self, mock_handle, mock_oidc, mock_relay, mock_get_config
    ):
        mock_oidc.return_value = {"repository": "org/repo"}
        mock_relay.return_value = {"head_sha": "abc123"}
        mock_handle.side_effect = HTTPException(409, "Conflict")

        response = lambda_handler(_event(), {})

        self.assertEqual(response["statusCode"], 409)
        self.assertEqual(json.loads(response["body"])["detail"], "Conflict")

    @patch("result.lambda_function.get_config")
    @patch("result.lambda_function.jwt_helper.verify_relay_dispatch_token")
    @patch("result.lambda_function.jwt_helper.verify_downstream_identity")
    @patch("result.lambda_function.result_handler.handle")
    def test_unhandled_exception_returns_500(
        self, mock_handle, mock_oidc, mock_relay, mock_get_config
    ):
        mock_oidc.return_value = {"repository": "org/repo"}
        mock_relay.return_value = {"head_sha": "abc123"}
        mock_handle.side_effect = Exception("Unexpected boom")

        response = lambda_handler(_event(), {})

        self.assertEqual(response["statusCode"], 500)
        self.assertEqual(
            json.loads(response["body"])["detail"], "Internal server error"
        )

if __name__ == "__main__":
    unittest.main()
