"""Lambda entrypoint for cross_repo_ci_webhook — handles POST /github/webhook."""

from __future__ import annotations

import base64
import json
import logging
import os

import boto3

import webhook_handler
from config import RelayConfig
from utils import RelayHTTPException

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


_secrets_client = None


def _get_secrets_client():
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager")
    return _secrets_client


def _fetch_secret_text(secret_arn: str) -> str:
    client = _get_secrets_client()
    resp = client.get_secret_value(SecretId=secret_arn)

    if "SecretString" in resp and resp["SecretString"] is not None:
        return resp["SecretString"]

    secret_binary = resp.get("SecretBinary")
    if not secret_binary:
        raise RuntimeError("Secrets Manager response missing SecretString/SecretBinary")
    return base64.b64decode(secret_binary).decode("utf-8")


def _maybe_set_env_from_secret(*, env_key: str, secret_arn_env_key: str) -> None:
    secret_arn = os.getenv(secret_arn_env_key)
    if not secret_arn:
        return
    if os.getenv(env_key):
        return
    os.environ[env_key] = _fetch_secret_text(secret_arn)


# One-time cold-start initialization.
_maybe_set_env_from_secret(env_key="GITHUB_WEBHOOK_SECRET", secret_arn_env_key="GITHUB_WEBHOOK_SECRET_SECRET_ARN")
_maybe_set_env_from_secret(env_key="REDIS_URL", secret_arn_env_key="REDIS_URL_SECRET_ARN")
_maybe_set_env_from_secret(env_key="GITHUB_APP_PRIVATE_KEY", secret_arn_env_key="GITHUB_APP_PRIVATE_KEY_SECRET_ARN")

_config = RelayConfig.from_env()

_JSON_HEADERS = {"content-type": "application/json"}


def _ok(data) -> dict:
    return {"statusCode": 200, "headers": _JSON_HEADERS, "body": json.dumps(data)}


def _error(status_code: int, detail) -> dict:
    return {"statusCode": status_code, "headers": _JSON_HEADERS, "body": json.dumps({"detail": detail})}


def lambda_handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "").upper()
    path = http.get("path", "")

    raw_body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body_bytes = base64.b64decode(raw_body)
    else:
        body_bytes = raw_body.encode("utf-8")

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    if method != "POST" or path != "/github/webhook":
        if path == "/github/webhook":
            return _error(405, "Method not allowed")
        return _error(404, "Not found")

    logger.info("request method=%s path=%s", method, path)
    try:
        payload = json.loads(body_bytes) if body_bytes else {}
        result = webhook_handler.handle_github_webhook(
            _config,
            body_bytes,
            payload,
            headers.get("x-hub-signature-256", ""),
            headers.get("x-github-event", ""),
        )
        return _ok(result)
    except RelayHTTPException as exc:
        return _error(exc.status_code, exc.detail)
