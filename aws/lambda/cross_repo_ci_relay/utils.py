import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import TypedDict

logger = logging.getLogger(__name__)


class HTTPException(Exception):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail


class PRDispatchPayload(TypedDict):
    upstream_repo: str
    head_sha: str
    pr_number: int
    head_ref: str
    base_ref: str


@dataclass
class PREvent:
    repo: str
    sha: str
    pr_number: int
    head_ref: str
    base_ref: str
    installation_id: int
    action: str


def extract_pr_fields(payload: dict) -> PREvent:
    try:
        return PREvent(
            repo=payload["repository"]["full_name"],
            sha=payload["pull_request"]["head"]["sha"],
            pr_number=payload["pull_request"]["number"],
            head_ref=payload["pull_request"]["head"]["ref"],
            base_ref=payload["pull_request"]["base"]["ref"],
            installation_id=payload["installation"]["id"],
            action=payload["action"],
        )
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing required field: {e}") from e


def verify_signature(secret: str, body: bytes, signature: str) -> None:
    if not signature:
        raise HTTPException(status_code=400, detail="No signature")
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    if not hmac.compare_digest(expected, signature):
        logger.warning("webhook signature mismatch")
        raise HTTPException(status_code=401, detail="Bad signature")
