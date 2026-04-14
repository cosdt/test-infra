from __future__ import annotations

import json
import logging

from utils import jwt_helper
from utils.config import get_config
from utils.lambda_utils import JSON_HEADERS, parse_lambda_event
from utils.types import HTTPException

from . import result_handler


logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def lambda_handler(event, context):
    method, path, body_bytes, headers = parse_lambda_event(event)

    logger.info("request method=%s path=%s", method, path)

    if method != "POST" or path != "/github/result":
        if path == "/github/result":
            return {
                "statusCode": 405,
                "headers": JSON_HEADERS,
                "body": json.dumps({"detail": "Method not allowed"}),
            }
        return {
            "statusCode": 404,
            "headers": JSON_HEADERS,
            "body": json.dumps({"detail": "Not found"}),
        }

    try:
        config = get_config()
        token = headers.get("authorization", "")
        payload = json.loads(body_bytes) if body_bytes else {}
        if not token:
            raise HTTPException(401, "Missing authorization token")
        oidc_claims = jwt_helper.verify_downstream_identity(config, token)
        dispatch_claims = jwt_helper.verify_relay_dispatch_token(
            config, payload.get("callback_token", "")
        )
        payload["downstream_repo"] = oidc_claims.get("repository", "")
        payload["head_sha"] = dispatch_claims.get("head_sha", "")
        result = result_handler.handle(config, payload)
        return {"statusCode": 200, "headers": JSON_HEADERS, "body": json.dumps(result)}

    except json.JSONDecodeError:
        logger.exception("Invalid JSON body")
        return {
            "statusCode": 400,
            "headers": JSON_HEADERS,
            "body": json.dumps({"detail": "Invalid JSON body"}),
        }
    except HTTPException as exc:
        logger.exception(exc.detail)
        return {
            "statusCode": exc.status_code,
            "headers": JSON_HEADERS,
            "body": json.dumps({"detail": exc.detail}),
        }
    except Exception:
        logger.exception("Internal server error")
        return {
            "statusCode": 500,
            "headers": JSON_HEADERS,
            "body": json.dumps({"detail": "Internal server error"}),
        }
