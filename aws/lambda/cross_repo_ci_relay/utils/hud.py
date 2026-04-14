import json
import logging
import urllib.error
import urllib.request

from .config import RelayConfig


logger = logging.getLogger(__name__)


def write_hud(config: RelayConfig, record: dict, infra: dict) -> None:
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
        logger.exception("HUD write failed status=%d reason=%s", exc.code, exc.reason)
        raise RuntimeError(f"HUD API returned HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        logger.exception("HUD API unreachable reason=%s", exc.reason)
        raise RuntimeError(f"HUD API unreachable: {exc.reason}") from exc
