"""AWS Lambda entrypoint for cross_repo_ci_relay.

This repo's Lambda convention expects `lambda_function.lambda_handler`.
We adapt the FastAPI app defined in `server.py` using Mangum.

Secrets note:
- If `GITHUB_APP_PRIVATE_KEY_SECRET_ARN` is set, the secret is fetched from
  AWS Secrets Manager at cold start and written to `/tmp/`.
- `GITHUB_APP_PRIVATE_KEY_PATH` will be set to that `/tmp/` file path.
"""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path

import boto3
from mangum import Mangum

from server import app


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

    # If env var already has a non-empty value, respect it.
    if os.getenv(env_key):
        return

    os.environ[env_key] = _fetch_secret_text(secret_arn)


def _write_private_key_from_secrets_manager() -> None:
    secret_arn = os.getenv("GITHUB_APP_PRIVATE_KEY_SECRET_ARN")
    if not secret_arn:
        return

    # If the caller doesn't specify a path, default to /tmp (writable on Lambda).
    private_key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH") or "/tmp/github_app_private_key.pem"
    path = Path(private_key_path)

    # Avoid repeated network calls on warm invocations.
    if path.exists() and path.stat().st_size > 0:
        os.environ["GITHUB_APP_PRIVATE_KEY_PATH"] = str(path)
        return

    secret_text = _fetch_secret_text(secret_arn)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret_text, encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    os.environ["GITHUB_APP_PRIVATE_KEY_PATH"] = str(path)


# One-time cold-start initialization.
_maybe_set_env_from_secret(env_key="GITHUB_WEBHOOK_SECRET", secret_arn_env_key="GITHUB_WEBHOOK_SECRET_SECRET_ARN")
_maybe_set_env_from_secret(env_key="CLICKHOUSE_PASSWORD", secret_arn_env_key="CLICKHOUSE_PASSWORD_SECRET_ARN")
_maybe_set_env_from_secret(env_key="REDIS_URL", secret_arn_env_key="REDIS_URL_SECRET_ARN")
_write_private_key_from_secrets_manager()


# Lambda handler (API Gateway / Function URL / ALB).
_lambda = Mangum(app)


def lambda_handler(event, context):
    return _lambda(event, context)
