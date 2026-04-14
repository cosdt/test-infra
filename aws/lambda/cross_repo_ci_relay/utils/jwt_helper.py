"""JWT utilities for the cross-repo CI relay.

- ``create_relay_dispatch_token`` — mints an HS256 JWT tying a callback to a
  relay-issued dispatch event.
- ``verify_relay_dispatch_token`` — decodes and verifies that token, returning
  the claims.  Callers are responsible for validating the claims against the
  request context.
- ``verify_downstream_identity`` — decodes a GitHub Actions OIDC token (RS256)
  and returns the claims.  Callers are responsible for checking the allowlist.
"""

from __future__ import annotations

import logging
import time

import jwt
from utils.config import RelayConfig
from utils.types import HTTPException


logger = logging.getLogger(__name__)

_jwks_client = jwt.PyJWKClient(
    "https://token.actions.githubusercontent.com/.well-known/jwks"
)


def create_relay_dispatch_token(
    *,
    config: RelayConfig,
    downstream_repo: str,
    delivery_id: str,
    payload: dict,
) -> str:
    """Mint an HS256 JWT that proves a callback belongs to a relay-issued dispatch."""
    pull_request = payload.get("pull_request") or {}
    head = pull_request.get("head") or {}
    now = int(time.time())
    claims = {
        "downstream_repo": downstream_repo,
        "upstream_repo": (payload.get("repository") or {}).get("full_name", ""),
        "head_sha": head.get("sha", ""),
        "delivery_id": delivery_id,
        "iat": now,
        "exp": now + config.callback_token_ttl,
    }
    pr_number = pull_request.get("number")
    if pr_number is not None:
        claims["pr_number"] = pr_number
    return jwt.encode(claims, config.github_app_secret, algorithm="HS256")


def verify_relay_dispatch_token(config: RelayConfig, token: str) -> dict:
    """Decode and verify the relay dispatch JWT signature. Returns the claims.

    Raises ``HTTPException(401)`` when the token is missing or the signature
    is invalid.
    """
    if not token:
        raise HTTPException(401, "Missing callback token")

    try:
        return jwt.decode(token, config.github_app_secret, algorithms=["HS256"])
    except Exception as exc:
        logger.exception("Callback token verification error")
        raise HTTPException(401, "Invalid callback token") from exc


def verify_downstream_identity(config: RelayConfig, token: str) -> dict:
    """Decode a GitHub Actions OIDC token and return the claims.

    Raises ``HTTPException(401)`` on any verification failure.
    """
    try:
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer="https://token.actions.githubusercontent.com",
            options={"verify_aud": False},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OIDC token verification error")
        raise HTTPException(401, "Invalid authorization token") from exc
