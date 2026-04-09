from __future__ import annotations

import base64
import json
import logging

from utils.config import RelayConfig
from utils.misc import HTTPException

from . import result_handler


logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

_cached_config: RelayConfig | None = None


_JSON_HEADERS = {"content-type": "application/json"}


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
        result = result_handler.handle(config, token, payload)
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
