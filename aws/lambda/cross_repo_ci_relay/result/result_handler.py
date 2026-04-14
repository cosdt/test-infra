from __future__ import annotations

import logging
import time

import utils.redis_helper as redis_helper
from utils.allowlist import AllowlistLevel, load_allowlist
from utils.config import RelayConfig
from utils.hud import write_hud


logger = logging.getLogger(__name__)


def handle(config: RelayConfig, payload: dict) -> dict:
    allowlist = load_allowlist(config)
    l2_repos, _ = allowlist.get_repos_at_or_above_level(AllowlistLevel.L2)

    if payload["downstream_repo"] not in l2_repos:
        logger.info(
            "downstream_repo %s is not configured for L2+ features, ignoring result",
            payload["downstream_repo"],
        )
        return {"ok": True, "status": "ignored"}

    # Compute and attach infra timing information (best-effort)
    client = redis_helper.create_client(config)
    infra = {"queue_time": None, "execution_time": None}
    try:
        if payload["status"] == "in_progress":
            in_progress_at = time.time()
            dispatch_timing = redis_helper.get_timing(
                config,
                payload["downstream_repo"],
                payload["head_sha"],
                "dispatch",
                client,
            )
            if dispatch_timing:
                queue_time = round(in_progress_at - dispatch_timing, 3)
                if queue_time < 0:
                    logger.warning(
                        "negative queue_time computed, dispatch_timing=%s in_progress_at=%s",
                        dispatch_timing,
                        in_progress_at,
                    )
                    queue_time = 0
                infra["queue_time"] = queue_time
            try:
                redis_helper.set_timing(
                    config,
                    payload["downstream_repo"],
                    payload["head_sha"],
                    "in_progress",
                    in_progress_at,
                    client,
                )
            except Exception:
                logger.exception("failed to record in_progress time")
        elif payload["status"] == "completed":
            completed_at = time.time()
            dispatch_timing = redis_helper.get_timing(
                config,
                payload["downstream_repo"],
                payload["head_sha"],
                "dispatch",
                client,
            )
            in_progress_timing = redis_helper.get_timing(
                config,
                payload["downstream_repo"],
                payload["head_sha"],
                "in_progress",
                client,
            )
            if dispatch_timing and in_progress_timing:
                queue_time = round(in_progress_timing - dispatch_timing, 3)
                if queue_time < 0:
                    logger.warning(
                        "negative queue_time computed, dispatch_timing=%s in_progress_timing=%s",
                        dispatch_timing,
                        in_progress_timing,
                    )
                    queue_time = 0
                infra["queue_time"] = queue_time
            if in_progress_timing:
                excution_time = round(completed_at - in_progress_timing, 3)
                if excution_time < 0:
                    logger.warning(
                        "negative execution_time computed, in_progress_timing=%s completed_at=%s",
                        in_progress_timing,
                        completed_at,
                    )
                    excution_time = 0
                infra["execution_time"] = excution_time
    except Exception:
        logger.exception("failed to compute timing")

    try:
        write_hud(config, payload, infra)
    except Exception:
        logger.exception("HUD write failed")

    return {"ok": True, "status": payload["status"]}
