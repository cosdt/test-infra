from __future__ import annotations

import base64
import json
import logging

import jwt
from utils.allowlist import AllowlistLevel, load_allowlist
from utils.config import RelayConfig
from utils.types import HTTPException


try:
    from . import result_handler
except ImportError:
    import result_handler  # type: ignore[no-redef]

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

_cached_config: RelayConfig | None = None

_jwks_client = jwt.PyJWKClient(
    "https://token.actions.githubusercontent.com/.well-known/jwks"
)


_JSON_HEADERS = {"content-type": "application/json"}


def _verify_callback_token(config: RelayConfig, token: str, payload: dict) -> None:
    # This token is minted by the webhook lambda and passed through the
    # downstream workflow, so it proves the callback belongs to a relay-issued
    # dispatch rather than an arbitrary external request.
    if not token:
        raise HTTPException(401, "Missing callback token")

    try:
        claims = jwt.decode(token, config.github_app_secret, algorithms=["HS256"])
    except Exception as exc:
        logger.exception("Callback token verification error")
        raise HTTPException(401, "Invalid callback token") from exc

    expected_pairs = {
        "downstream_repo": payload.get("downstream_repo"),
        "upstream_repo": payload.get("upstream_repo"),
        "head_sha": payload.get("head_sha"),
    }

    if payload.get("pr_number") is not None:
        expected_pairs["pr_number"] = int(payload["pr_number"])

    for key, expected in expected_pairs.items():
        if expected is None:
            continue
        if claims.get(key) != expected:
            logger.error(
                "Callback token claim mismatch for %s: expected %s, got %s",
                key,
                expected,
                claims.get(key),
            )
            raise HTTPException(401, "Invalid callback token")


def _verify_github_oidc_token(config: RelayConfig, token: str) -> None:
    try:
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        # GitHub signs the OIDC token with its own keypair; here we only need
        # to verify that the caller is an allowlisted downstream repository.
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        data = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer="https://token.actions.githubusercontent.com",
            options={"verify_aud": False},
        )
        repo = data.get("repository")
        allowlist = load_allowlist(config)
        allowed_repos, _ = allowlist.get_repos_at_or_above_level(AllowlistLevel.L2)
        if repo not in allowed_repos:
            logger.error(
                "OIDC token repository not in allowlist: %s",
                repo,
            )
            raise HTTPException(401, "Invalid authorization token")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OIDC token verification error")
        raise HTTPException(401, "Invalid authorization token") from exc


def _get_config() -> RelayConfig:
    global _cached_config
    if _cached_config is None:
        _cached_config = RelayConfig.from_env()
    return _cached_config


def lambda_handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "").upper()
    path = http.get("path", "")

    raw_body = event.get("body") or ""
    body_bytes = (
        base64.b64decode(raw_body)
        if event.get("isBase64Encoded")
        else raw_body.encode("utf-8")
    )
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    logger.info("request method=%s path=%s", method, path)

    if method != "POST" or path != "/github/result":
        if path == "/github/result":
            return {
                "statusCode": 405,
                "headers": _JSON_HEADERS,
                "body": json.dumps({"detail": "Method not allowed"}),
            }
        return {
            "statusCode": 404,
            "headers": _JSON_HEADERS,
            "body": json.dumps({"detail": "Not found"}),
        }

    try:
        config = _get_config()
        token = headers.get("authorization", "")
        payload = json.loads(body_bytes) if body_bytes else {}
        if not token:
            raise HTTPException(401, "Missing authorization token")
        # The callback token ties the payload back to the relay dispatch, while
        # the OIDC token proves which GitHub Actions workflow is calling us.
        _verify_callback_token(config, payload.get("callback_token", ""), payload)
        _verify_github_oidc_token(config, token)
        result = result_handler.handle(config, payload)
        return {"statusCode": 200, "headers": _JSON_HEADERS, "body": json.dumps(result)}

    except json.JSONDecodeError:
        return {
            "statusCode": 400,
            "headers": _JSON_HEADERS,
            "body": json.dumps({"detail": "Invalid JSON body"}),
        }
    except HTTPException as exc:
        return {
            "statusCode": exc.status_code,
            "headers": _JSON_HEADERS,
            "body": json.dumps({"detail": exc.detail}),
        }
    except Exception:
        logger.exception("Internal server error")
        return {
            "statusCode": 500,
            "headers": _JSON_HEADERS,
            "body": json.dumps({"detail": "Internal server error"}),
        }
