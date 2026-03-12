import logging

from fastapi import HTTPException

import utils
from config import RelayConfig
from clickhouse_client_helper import CHCliFactory
import whitelist_cache

logger = logging.getLogger(__name__)


def _ensure_device_from_allowlist(run_url: str, allowlist: dict) -> str:
    """Validate run_url against allowlist and return the matching device name."""
    if not run_url:
        raise HTTPException(status_code=400, detail="Missing url")

    repo_html_url = utils._repo_html_url_from_actions_run_url(run_url)
    if not repo_html_url:
        raise HTTPException(status_code=400, detail=f"Unsupported url: {run_url}")

    norm = repo_html_url.rstrip("/")
    for device, info in allowlist.items():
        if info["url"] == norm:
            return device

    raise HTTPException(
        status_code=403,
        detail={
            "message": "ci/result rejected: run url repo is not allowlisted",
            "repo_html_url": repo_html_url,
            "allowed": sorted(info["url"] for info in allowlist.values()),
        },
    )


def handle_ci_result(config: RelayConfig, data: dict):

    CHCliFactory.setup_client(
        url=config.clickhouse_url,
        username=config.clickhouse_user,
        password=config.clickhouse_password,
        database=config.clickhouse_database,
    )

    run_url = data.get("url", "")
    allowlist = whitelist_cache.load_allowlist_info_map(config)
    device = _ensure_device_from_allowlist(run_url, allowlist)
    info = allowlist[device]
    level = info["level"]

    status = data.get("status")
    workflow_name = data["workflow_name"]
    upstream_repo = data["upstream_repo"]
    commit_sha = data["commit_sha"]
    conclusion = data["conclusion"]  # success / failure / cancelled
    logger.info("[%s] CI finished: conclusion=%s status=%s level=%s workflow=%s",
                device, conclusion, status, level, workflow_name)

    # ── L1: forward only, no feedback to upstream ──────────────────────────
    if level == "L1":
        logger.debug("[%s] L1 device - ignored", device)
        return {"ok": True, "action": "ignored"}

    # ── L2+: write result to ClickHouse (OOT HUD) ──────────────────────────
    ch = CHCliFactory()
    ch.ensure_table()
    ch.write_ci_result(
        device=device,
        upstream_repo=upstream_repo,
        commit_sha=commit_sha,
        workflow_name=workflow_name,
        status=status,
        conclusion=conclusion,
        run_url=run_url,
    )
    logger.info("[%s] result written to ClickHouse", device)

    if level == "L2":
        logger.debug("[%s] L2 device - hud_only", device)
        return {"ok": True, "action": "hud_only"}
