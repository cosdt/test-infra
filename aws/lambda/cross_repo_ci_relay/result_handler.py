import logging
import re

from fastapi import HTTPException

from config import RelayConfig
from clickhouse_client_helper import CHCliFactory
import whitelist_redis_helper as whitelist_redis_helper

logger = logging.getLogger(__name__)


def handle_ci_result(config: RelayConfig, data: dict):
    # Initialize ClickHouse client configuration for the current request.
    CHCliFactory.setup_client(
        url=config.clickhouse_url,
        username=config.clickhouse_user,
        password=config.clickhouse_password,
        database=config.clickhouse_database,
    )

    run_url = data.get("url", "")
    allowlist = whitelist_redis_helper.load_allowlist_info_map(config)

    # Validate run URL and resolve the source device from allowlist.
    if not run_url:
        raise HTTPException(status_code=400, detail="Missing url")

    # Support both GitHub HTML run URLs and API run URLs.
    matched = re.search(r"github\.com/([^/]+)/([^/]+)/(?:actions/)?runs/\d+", run_url)
    if matched:
        repo_html_url = f"https://github.com/{matched.group(1)}/{matched.group(2)}"
    else:
        matched = re.search(
            r"api\.github\.com/repos/([^/]+)/([^/]+)/actions/runs/\d+", run_url
        )
        repo_html_url = (
            f"https://github.com/{matched.group(1)}/{matched.group(2)}"
            if matched
            else None
        )

    if not repo_html_url:
        raise HTTPException(status_code=400, detail=f"Unsupported url: {run_url}")

    norm = repo_html_url.rstrip("/")
    device = next(
        (name for name, info in allowlist.items() if info["url"] == norm), None
    )
    if not device:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "ci/result rejected: run url repo is not allowlisted",
                "repo_html_url": repo_html_url,
                "allowed": sorted(info["url"] for info in allowlist.values()),
            },
        )

    info = allowlist[device]
    level = info["level"]

    status = data.get("status")
    workflow_name = data["workflow_name"]
    upstream_repo = data["upstream_repo"]
    commit_sha = data["commit_sha"]
    conclusion = data["conclusion"]  # success / failure / cancelled
    logger.info(
        "[%s] CI finished: conclusion=%s status=%s level=%s workflow=%s",
        device,
        conclusion,
        status,
        level,
        workflow_name,
    )

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
