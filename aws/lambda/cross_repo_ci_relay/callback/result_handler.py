from __future__ import annotations

import logging
import time

import utils.redis_helper as redis_helper
from utils.allowlist import AllowlistLevel, load_allowlist
from utils.config import RelayConfig
from utils.hud import write_hud
from utils.types import HTTPException, TimingPhase


logger = logging.getLogger(__name__)


def _safe_delta(
    start_ts: float | None, end_ts: float | None, label: str
) -> float | None:
    """Compute end-start, clamping tiny negatives to 0 and returning None
    whenever either endpoint is missing (e.g. Redis cache miss)."""
    if start_ts is None or end_ts is None:
        return None
    delta = round(end_ts - start_ts, 3)
    if delta < 0:
        logger.warning(
            "negative %s computed, start=%s end=%s", label, start_ts, end_ts
        )
        return 0
    return delta


def handle(config: RelayConfig, body: dict, verified_repo: str) -> dict:
    """Forward a downstream callback to HUD.

    ``body`` is the callback request body, passed through to HUD unchanged.
    ``verified_repo`` is the OIDC-authenticated downstream repository — it is
    used for the allowlist / timing lookups and surfaced to HUD under
    ``infra.verified_repo`` so HUD can trust it over anything self-reported
    in the body.
    """
    allowlist = load_allowlist(config)
    l2_repos, _ = allowlist.get_repos_at_or_above_level(AllowlistLevel.L2)

    if verified_repo not in l2_repos:
        logger.info(
            "verified_repo %s is not configured for L2+ features, ignoring result",
            verified_repo,
        )
        return {"ok": True, "status": "ignored"}

    # delivery_id and status are required fields on the callback body — the
    # relay-callback action always sets them.  A missing value is a contract
    # violation from the downstream, so fail loudly rather than silently
    # producing a HUD row with no timing.
    try:
        delivery_id = body["delivery_id"]
        status = body["status"]
    except (KeyError, TypeError) as exc:
        raise HTTPException(
            400, f"callback body missing required field: {exc}"
        ) from exc

    # Timing keys are indexed by the body-reported delivery_id and the
    # OIDC-verified repo.  delivery_id is not independently authenticated —
    # a tampered value just misses the timing cache, which only hurts the
    # attacker's own HUD row.
    infra: dict = {"queue_time": None, "execution_time": None}
    if status == "in_progress":
        in_progress_at = time.time()
        redis_helper.set_timing(
            config, delivery_id, verified_repo, TimingPhase.IN_PROGRESS, in_progress_at
        )
        dispatch_at = redis_helper.get_timing(
            config, delivery_id, verified_repo, TimingPhase.DISPATCH
        )
        infra["queue_time"] = _safe_delta(dispatch_at, in_progress_at, "queue_time")
    elif status == "completed":
        completed_at = time.time()
        dispatch_at = redis_helper.get_timing(
            config, delivery_id, verified_repo, TimingPhase.DISPATCH
        )
        in_progress_at = redis_helper.get_timing(
            config, delivery_id, verified_repo, TimingPhase.IN_PROGRESS
        )
        infra["queue_time"] = _safe_delta(dispatch_at, in_progress_at, "queue_time")
        infra["execution_time"] = _safe_delta(
            in_progress_at, completed_at, "execution_time"
        )

    # Transparent proxy: HUD owns schema validation; its HTTP response is
    # propagated back to the caller by write_hud raising on non-2xx.
    write_hud(config, body, verified_repo, infra)

    return {"ok": True, "status": status}
