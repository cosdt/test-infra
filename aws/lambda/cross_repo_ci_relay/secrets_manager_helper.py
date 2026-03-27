import base64
import json
import logging
from dataclasses import dataclass

import boto3

from utils import RetryWithBackoff

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RelaySecretsFromStore:
    github_webhook_secret: str = ""
    github_app_private_key: str = ""


def _decode_private_key(value: str) -> str:
    if not value:
        return ""
    if "-----BEGIN" in value:
        return value
    try:
        decoded = base64.b64decode(value).decode("utf-8")
    except Exception:
        return value
    return decoded if "-----BEGIN" in decoded else value


def get_secret_from_aws(secret_store_arn: str) -> RelaySecretsFromStore:
    try:
        for attempt in RetryWithBackoff(max_retries=3, base_delay=1, jitter=False):
            with attempt:
                session = boto3.session.Session()
                client = session.client(
                    service_name="secretsmanager", region_name="us-east-1"
                )
                get_secret_value_response = client.get_secret_value(
                    SecretId=secret_store_arn
                )
                secret_value = json.loads(get_secret_value_response["SecretString"])
                return RelaySecretsFromStore(
                    github_webhook_secret=secret_value.get("GITHUB_WEBHOOK_SECRET", ""),
                    github_app_private_key=_decode_private_key(
                        secret_value.get("GITHUB_APP_PRIVATE_KEY", "")
                    ),
                )
    except Exception as exc:
        logger.exception("Failed to retrieve secrets from AWS Secrets Manager")
        raise RuntimeError(
            "Failed to retrieve secrets from AWS Secrets Manager: "
            f"{secret_store_arn} - {type(exc).__name__}: {exc}"
        ) from exc


def get_runtime_secrets(secret_store_arn: str) -> RelaySecretsFromStore:
    if not secret_store_arn:
        raise RuntimeError("SECRET_STORE_ARN is not configured")

    secrets = get_secret_from_aws(secret_store_arn)
    logger.info("Secrets loaded from secret store %s", secret_store_arn)
    return secrets
