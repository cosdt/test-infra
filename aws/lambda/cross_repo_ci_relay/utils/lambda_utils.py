"""Shared helpers for Lambda function handlers."""

from __future__ import annotations

import base64


JSON_HEADERS = {"content-type": "application/json"}


def parse_lambda_event(event: dict) -> tuple[str, str, bytes, dict]:
    """Extract method, path, body bytes, and lower-cased headers from a Lambda event dict."""
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
    return method, path, body_bytes, headers
