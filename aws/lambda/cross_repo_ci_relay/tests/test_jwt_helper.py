import unittest
from unittest.mock import MagicMock, patch

from utils.jwt_helper import (
    create_relay_dispatch_token,
    verify_downstream_identity,
    verify_relay_dispatch_token,
)
from utils.types import HTTPException


def _cfg(secret="test-secret", ttl=3600):
    cfg = MagicMock()
    cfg.github_app_secret = secret
    cfg.callback_token_ttl = ttl
    return cfg


class TestCreateRelayDispatchToken(unittest.TestCase):
    @patch("utils.jwt_helper.jwt.encode", return_value="minted.token")
    def test_returns_encoded_token(self, mock_encode):
        payload = {
            "pull_request": {"head": {"sha": "abc123"}, "number": 42},
            "repository": {"full_name": "pytorch/pytorch"},
        }
        token = create_relay_dispatch_token(
            config=_cfg(),
            downstream_repo="org/repo",
            delivery_id="d-1",
            payload=payload,
        )
        self.assertEqual(token, "minted.token")
        mock_encode.assert_called_once()

    @patch("utils.jwt_helper.jwt.encode", return_value="tok")
    def test_claims_contain_expected_fields(self, mock_encode):
        payload = {
            "pull_request": {"head": {"sha": "abc123"}, "number": 42},
            "repository": {"full_name": "pytorch/pytorch"},
        }
        create_relay_dispatch_token(
            config=_cfg(secret="s", ttl=3600),
            downstream_repo="org/repo",
            delivery_id="d-1",
            payload=payload,
        )
        claims = mock_encode.call_args[0][0]
        self.assertEqual(claims["downstream_repo"], "org/repo")
        self.assertEqual(claims["upstream_repo"], "pytorch/pytorch")
        self.assertEqual(claims["head_sha"], "abc123")
        self.assertEqual(claims["delivery_id"], "d-1")
        self.assertIn("iat", claims)
        self.assertIn("exp", claims)

    @patch("utils.jwt_helper.jwt.encode", return_value="tok")
    def test_pr_number_included_when_present(self, mock_encode):
        payload = {
            "pull_request": {"head": {"sha": "sha"}, "number": 99},
            "repository": {"full_name": "org/upstream"},
        }
        create_relay_dispatch_token(
            config=_cfg(), downstream_repo="org/ds", delivery_id="d", payload=payload
        )
        claims = mock_encode.call_args[0][0]
        self.assertEqual(claims["pr_number"], 99)

    @patch("utils.jwt_helper.jwt.encode", return_value="tok")
    def test_pr_number_omitted_when_absent(self, mock_encode):
        payload = {
            "pull_request": {"head": {"sha": "sha"}},
            "repository": {"full_name": "org/upstream"},
        }
        create_relay_dispatch_token(
            config=_cfg(), downstream_repo="org/ds", delivery_id="d", payload=payload
        )
        claims = mock_encode.call_args[0][0]
        self.assertNotIn("pr_number", claims)

    @patch("utils.jwt_helper.jwt.encode", return_value="tok")
    def test_uses_hs256_algorithm(self, mock_encode):
        payload = {
            "pull_request": {"head": {"sha": "s"}},
            "repository": {"full_name": "o/r"},
        }
        create_relay_dispatch_token(
            config=_cfg(), downstream_repo="o/r", delivery_id="d", payload=payload
        )
        self.assertEqual(mock_encode.call_args[1].get("algorithm"), "HS256")


class TestVerifyRelayDispatchToken(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("utils.jwt_helper.jwt.decode")
        self.mock_decode = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_empty_token_raises_401(self):
        with self.assertRaises(HTTPException) as ctx:
            verify_relay_dispatch_token(_cfg(), "")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Missing", ctx.exception.detail)
        self.mock_decode.assert_not_called()

    def test_invalid_jwt_raises_401(self):
        self.mock_decode.side_effect = Exception("bad signature")
        with self.assertRaises(HTTPException) as ctx:
            verify_relay_dispatch_token(_cfg(), "bad.token.here")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Invalid", ctx.exception.detail)

    def test_valid_token_returns_claims(self):
        expected = {
            "downstream_repo": "org/repo",
            "head_sha": "abc123",
            "iat": 0,
            "exp": 9999,
        }
        self.mock_decode.return_value = expected

        claims = verify_relay_dispatch_token(_cfg(), "valid.jwt.token")

        self.assertEqual(claims, expected)

    def test_decode_called_with_hs256(self):
        self.mock_decode.return_value = {}
        verify_relay_dispatch_token(_cfg(secret="s"), "some.token")
        call_kwargs = self.mock_decode.call_args[1]
        self.assertIn("HS256", call_kwargs.get("algorithms", []))


class TestVerifyDownstreamIdentity(unittest.TestCase):
    def setUp(self):
        self.patcher_jwks = patch(
            "utils.jwt_helper._jwks_client.get_signing_key_from_jwt"
        )
        self.mock_signing_key = self.patcher_jwks.start()
        self.mock_signing_key.return_value = MagicMock(key="fake-key")

        self.patcher_decode = patch("utils.jwt_helper.jwt.decode")
        self.mock_decode = self.patcher_decode.start()

    def tearDown(self):
        self.patcher_jwks.stop()
        self.patcher_decode.stop()

    def test_valid_token_returns_claims(self):
        expected = {
            "repository": "org/repo",
            "sub": "repo:org/repo:ref:refs/heads/main",
        }
        self.mock_decode.return_value = expected

        claims = verify_downstream_identity(_cfg(), "some.oidc.token")

        self.assertEqual(claims, expected)

    def test_bearer_prefix_stripped_before_jwks_lookup(self):
        self.mock_decode.return_value = {"repository": "org/repo"}

        verify_downstream_identity(_cfg(), "Bearer some.oidc.token")

        self.mock_signing_key.assert_called_once_with("some.oidc.token")

    def test_bearer_prefix_case_insensitive(self):
        self.mock_decode.return_value = {"repository": "org/repo"}

        verify_downstream_identity(_cfg(), "BEARER some.oidc.token")

        self.mock_signing_key.assert_called_once_with("some.oidc.token")

    def test_jwks_lookup_failure_raises_401(self):
        self.mock_signing_key.side_effect = Exception("JWKS fetch failed")

        with self.assertRaises(HTTPException) as ctx:
            verify_downstream_identity(_cfg(), "bad.token")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_decode_failure_raises_401(self):
        self.mock_decode.side_effect = Exception("decode error")

        with self.assertRaises(HTTPException) as ctx:
            verify_downstream_identity(_cfg(), "some.token")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_decode_called_with_rs256_and_issuer(self):
        self.mock_decode.return_value = {"repository": "org/repo"}

        verify_downstream_identity(_cfg(), "tok")

        call_kwargs = self.mock_decode.call_args[1]
        self.assertIn("RS256", call_kwargs.get("algorithms", []))
        self.assertEqual(
            call_kwargs.get("issuer"),
            "https://token.actions.githubusercontent.com",
        )


if __name__ == "__main__":
    unittest.main()
