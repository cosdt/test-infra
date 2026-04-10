from __future__ import annotations

import base64
import json
import logging

import jwt
from utils.config import RelayConfig
from utils.types import HTTPException


try:
    from . import result_handler
except ImportError:
    import result_handler

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

_cached_config: RelayConfig | None = None

_jwks_client = jwt.PyJWKClient(
    "https://token.actions.githubusercontent.com/.well-known/jwks.json"
)


_JSON_HEADERS = {"content-type": "application/json"}


def _verify_github_oidc_token(token: str, expected_repo: str) -> None:
    try:
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        data = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer="https://token.actions.githubusercontent.com",
            options={"verify_audience": False},
        )
        if data.get("repository") != expected_repo:
            logger.error(
                "OIDC token repository mismatch: expected %s, got %s",
                expected_repo,
                data.get("repository"),
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
        _verify_github_oidc_token(token, payload["downstream_repo"])
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
