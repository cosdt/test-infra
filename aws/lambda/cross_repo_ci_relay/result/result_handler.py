from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

import utils.redis_helper as redis_helper
from utils.allowlist import AllowlistLevel, load_allowlist
from utils.config import RelayConfig
from utils.types import (
    HTTPException,
    OOTStatusRecord,
    ResultCallbackPayload,
)


logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"in_progress", "completed"})
VALID_CONCLUSIONS = frozenset({"success", "failure"})


def validate_payload(body: dict) -> ResultCallbackPayload:
    required = [
        "head_sha",
        "status",
        "conclusion",
        "workflow_name",
        "workflow_url",
        "downstream_repo",
        "upstream_repo",
        "pr_number",
    ]
    for field in required:
        if field not in body:
            logger.error("Missing required field in payload: %s", field)
            raise HTTPException(400, f"Missing required field: {field}")

    status = body["status"]
    if status not in VALID_STATUSES:
        logger.error("Invalid status in payload: %s", status)
        raise HTTPException(
            400, f"Invalid status: {status!r}. Must be one of {sorted(VALID_STATUSES)}"
        )

    conclusion = body.get("conclusion")
    if status == "completed" and (
        not conclusion or conclusion not in VALID_CONCLUSIONS
    ):
        logger.error(
            "Invalid (status, conclusion) combination in payload: (%s, %s)",
            status,
            conclusion,
        )
        raise HTTPException(
            400,
            "Invalid conclusion for completed status: "
            f"{conclusion!r}. Must be one of {sorted(VALID_CONCLUSIONS)}",
        )

    if status == "in_progress":
        conclusion = None
    try:
        payload = {
            "head_sha": body["head_sha"],
            "status": status,
            "conclusion": conclusion,
            "workflow_name": body["workflow_name"],
            "workflow_url": body["workflow_url"],
            "downstream_repo": body["downstream_repo"],
            "upstream_repo": body["upstream_repo"],
            "pr_number": int(body["pr_number"]),
            **({} if body.get("run_id") is None else {"run_id": int(body["run_id"])}),
            **({} if body.get("job_id") is None else {"job_id": int(body["job_id"])}),
        }
    except (ValueError, TypeError) as exc:
        logger.exception("Invalid field type in payload")
        raise HTTPException(400, f"Invalid field type: {exc}") from exc
    return payload


def handle(config: RelayConfig, payload: dict) -> dict:
    cb_payload = validate_payload(payload)

    allowlist = load_allowlist(config)
    l2_repos, _ = allowlist.get_repos_at_or_above_level(AllowlistLevel.L2)

    if cb_payload["downstream_repo"] not in l2_repos:
        logger.info(
            "downstream_repo %s is not configured for L2+ features, ignoring result",
            cb_payload["downstream_repo"],
        )
        return {"ok": True, "status": "ignored"}

    _REDIS_ERROR = object()  # sentinel: Redis unavailable, state unknown
    try:
        client = redis_helper.create_client(config)
        existing = redis_helper.get_oot_status(
            config,
            cb_payload["downstream_repo"],
            cb_payload["head_sha"],
            client,
        )
    except Exception:
        logger.exception("Redis read failed during status ordering check; proceeding")
        existing = _REDIS_ERROR
        client = None

    # Enforce status ordering: only accept `completed` when a prior `in_progress`
    # record exists in Redis.  This prevents a downstream workflow from reporting
    # `completed` before it has ever reported `in_progress` (e.g. due to a bug or
    # a malicious early callback).
    # When Redis is unavailable (existing is _REDIS_ERROR) we fail-open to avoid
    # blocking legitimate callbacks due to infrastructure issues.
    if cb_payload["status"] == "completed" and existing is not _REDIS_ERROR:
        if existing is None:
            logger.warning(
                "Rejecting completed callback with no prior in_progress record: "
                "downstream_repo=%s head_sha=%s",
                cb_payload["downstream_repo"],
                cb_payload["head_sha"],
            )
            raise HTTPException(
                409,
                "Cannot report 'completed' before reporting 'in_progress'",
            )
        if existing.get("status") == "completed":
            logger.warning(
                "Rejecting duplicate completed callback: downstream_repo=%s head_sha=%s",
                cb_payload["downstream_repo"],
                cb_payload["head_sha"],
            )
            raise HTTPException(409, "Status 'completed' has already been reported")

    status_record: OOTStatusRecord = {
        "downstream_repo": cb_payload["downstream_repo"],
        "upstream_repo": cb_payload["upstream_repo"],
        "head_sha": cb_payload["head_sha"],
        "pr_number": cb_payload["pr_number"],
        "status": cb_payload["status"],
        "conclusion": cb_payload["conclusion"],
        "workflow_name": cb_payload["workflow_name"],
        "workflow_url": cb_payload["workflow_url"],
        **(
            {} if cb_payload.get("run_id") is None else {"run_id": cb_payload["run_id"]}
        ),
        **(
            {} if cb_payload.get("job_id") is None else {"job_id": cb_payload["job_id"]}
        ),
    }

    try:
        if client is None:
            client = redis_helper.create_client(config)
        redis_helper.set_oot_status(
            config,
            cb_payload["downstream_repo"],
            cb_payload["head_sha"],
            {"status": cb_payload["status"]},
            client,
        )
    except Exception:
        logger.exception("Redis OOT status write failed")

    try:
        _write_to_hud(config, status_record)
    except Exception:
        logger.exception("HUD write failed")

    return {"ok": True, "status": cb_payload["status"]}


def _write_to_hud(config: RelayConfig, record: OOTStatusRecord) -> None:
    body = json.dumps(dict(record)).encode("utf-8")
    req = urllib.request.Request(
        config.hud_api_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": config.hud_bot_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("HUD write succeeded status=%d", resp.status)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HUD API returned HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HUD API unreachable: {exc.reason}") from exc
