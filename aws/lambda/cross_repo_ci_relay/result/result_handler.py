from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

import utils.redis_helper as redis_helper
from utils.allowlist import AllowlistLevel, load_allowlist
from utils.config import RelayConfig
from utils.types import HTTPException, ResultCallbackPayload


logger = logging.getLogger(__name__)


def validate_payload(payload: dict) -> ResultCallbackPayload:
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
        if field not in payload:
            logger.error("Missing required field in payload: %s", field)
            raise HTTPException(400, f"Missing required field: {field}")

    return payload


def handle(config: RelayConfig, payload: dict) -> dict:
    allowlist = load_allowlist(config)
    l2_repos, _ = allowlist.get_repos_at_or_above_level(AllowlistLevel.L2)

    if payload["downstream_repo"] not in l2_repos:
        logger.info(
            "downstream_repo %s is not configured for L2+ features, ignoring result",
            payload["downstream_repo"],
        )
        return {"ok": True, "status": "ignored"}

    _REDIS_ERROR = object()  # sentinel: Redis unavailable, state unknown
    try:
        client = redis_helper.create_client(config)
        existing = redis_helper.get_oot_status(
            config,
            payload["downstream_repo"],
            payload["head_sha"],
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
    if payload["status"] == "completed" and existing is not _REDIS_ERROR:
        if existing is None:
            logger.warning(
                "Rejecting completed callback with no prior in_progress record: "
                "downstream_repo=%s head_sha=%s",
                payload["downstream_repo"],
                payload["head_sha"],
            )
            raise HTTPException(
                409,
                "Cannot report 'completed' before reporting 'in_progress'",
            )
        if existing.get("status") == "completed":
            logger.warning(
                "Rejecting duplicate completed callback: downstream_repo=%s head_sha=%s",
                payload["downstream_repo"],
                payload["head_sha"],
            )
            raise HTTPException(409, "Status 'completed' has already been reported")

    try:
        if client is None:
            client = redis_helper.create_client(config)
        redis_helper.set_oot_status(
            config,
            payload["downstream_repo"],
            payload["head_sha"],
            {"status": payload["status"]},
            client,
        )
    except Exception:
        logger.exception("Redis OOT status write failed")

    # Compute and attach infra timing information (best-effort)
    infra = {"queue_time": None, "execution_time": None}
    try:
        if payload["status"] == "in_progress":
            in_progress_at = time.time()
            timing = redis_helper.get_timing(
                config, payload["downstream_repo"], payload["head_sha"], client
            )
            dispatch_at = (timing or {}).get("dispatch_at")
            if dispatch_at:
                infra["queue_time"] = round(in_progress_at - dispatch_at, 3)
            try:
                redis_helper.update_timing(
                    config,
                    payload["downstream_repo"],
                    payload["head_sha"],
                    {"in_progress_at": in_progress_at},
                    client,
                )
            except Exception:
                logger.exception("failed to record in_progress time")
        elif payload["status"] == "completed":
            completed_at = time.time()
            timing = redis_helper.get_timing(
                config, payload["downstream_repo"], payload["head_sha"], client
            )
            dispatch_at = (timing or {}).get("dispatch_at")
            in_progress_at = (timing or {}).get("in_progress_at")
            if dispatch_at and in_progress_at:
                infra["queue_time"] = round(in_progress_at - dispatch_at, 3)
            if in_progress_at:
                infra["execution_time"] = round(completed_at - in_progress_at, 3)
    except Exception:
        logger.exception("failed to compute timing")

    try:
        _write_to_hud(config, payload, infra)
    except Exception:
        logger.exception("HUD write failed")

    return {"ok": True, "status": payload["status"]}


def _write_to_hud(config: RelayConfig, record: dict, infra: dict) -> None:
    body = json.dumps({"downstream": dict(record), "infra": dict(infra)}).encode(
        "utf-8"
    )
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
