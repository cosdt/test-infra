import json
import logging
import urllib.error
import urllib.request

from .config import RelayConfig
from .types import HTTPException


logger = logging.getLogger(__name__)


def write_hud(
    config: RelayConfig, body: dict, verified_repo: str, infra: dict
) -> None:
    """POST a callback record to HUD.

    The HUD request body has three top-level fields:

    - ``body``: the downstream workflow's callback body, forwarded verbatim.
    - ``verified_repo``: the OIDC-authenticated downstream repository.  HUD
      should treat this as the sole trusted identity of the caller and prefer
      it over any self-reported repo field inside ``body``.
    - ``infra``: Relay-computed metadata (queue_time, execution_time).

    Relay is a transparent proxy: HUD owns schema validation and storage, so
    HUD's HTTP status is propagated back to the original caller.  A non-2xx
    from HUD becomes an ``HTTPException`` with the same status; network-level
    unreachability becomes a 502.
    """
    if not config.hud_api_url:
        # No HUD configured (e.g. local dev before HUD endpoint exists) —
        # log and no-op rather than 500.  Remove this branch once HUD is
        # mandatory in every environment.
        logger.info("HUD_API_URL not configured, skipping HUD write")
        return

    hud_payload = json.dumps(
        {
            "body": dict(body),
            "verified_repo": verified_repo,
            "infra": dict(infra),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        config.hud_api_url,
        data=hud_payload,
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
        detail = f"HUD returned HTTP {exc.code}: {exc.reason}"
        logger.exception(detail)
        raise HTTPException(exc.code, detail) from exc
    except urllib.error.URLError as exc:
        detail = f"HUD unreachable: {exc.reason}"
        logger.exception(detail)
        raise HTTPException(502, detail) from exc
