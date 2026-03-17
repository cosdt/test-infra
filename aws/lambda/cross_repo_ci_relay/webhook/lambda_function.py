"""Lambda entrypoint for cross_repo_ci_webhook — handles POST /github/webhook."""

from __future__ import annotations

import base64
import json
import logging
import os

import webhook_handler
from config import RelayConfig
from secrets_manager_helper import get_runtime_secrets
from utils import RelayHTTPException

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

_bootstrap_config = RelayConfig.from_env()
_runtime_secrets = get_runtime_secrets(_bootstrap_config.secret_store_arn)
_config = RelayConfig.from_env(_runtime_secrets)

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
