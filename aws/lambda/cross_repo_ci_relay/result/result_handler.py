import logging
import re

from config import RelayConfig
from utils import RelayHTTPException
from clickhouse_client_helper import CHCliFactory
import whitelist_redis_helper

logger = logging.getLogger(__name__)


def handle_ci_result(config: RelayConfig, data: dict):
    CHCliFactory.setup_client(
        url=config.clickhouse_url,
        username=config.clickhouse_user,
        password=config.clickhouse_password,
        database=config.clickhouse_database,
    )

    run_url = data.get("url", "")
    if not run_url:
        raise RelayHTTPException(status_code=400, detail="Missing url")

    logger.debug("ci/result received url=%s", run_url)
    allowlist = whitelist_redis_helper.load_allowlist_info_map(config)

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
        raise RelayHTTPException(status_code=400, detail=f"Unsupported url: {run_url}")

    norm = repo_html_url.rstrip("/")
    device = next(
        (name for name, info in allowlist.items() if info["url"] == norm), None
    )
    if not device:
        logger.warning("ci/result rejected repo=%s not in allowlist", repo_html_url)
        raise RelayHTTPException(
            status_code=403,
            detail={
                "message": "ci/result rejected: repo not in allowlist",
                "repo_html_url": repo_html_url,
                "allowed": sorted(info["url"] for info in allowlist.values()),
            },
        )

    info = allowlist[device]
    level = info["level"]

    try:
        workflow_name = data["workflow_name"]
        upstream_repo = data["upstream_repo"]
        commit_sha = data["commit_sha"]
        conclusion = data["conclusion"]
    except KeyError as e:
        raise RelayHTTPException(status_code=400, detail=f"Missing required field: {e}") from e

    status = data.get("status", "")

    logger.info(
        "ci/result device=%s level=%s conclusion=%s workflow=%s sha=%.12s",
        device, level, conclusion, workflow_name, commit_sha,
    )

    if level == "L1":
        return {"ok": True, "action": "ignored"}

    CHCliFactory.ensure_table()
    CHCliFactory.write_ci_result(
        device=device,
        upstream_repo=upstream_repo,
        commit_sha=commit_sha,
        workflow_name=workflow_name,
        status=status,
        conclusion=conclusion,
        run_url=run_url,
    )
    logger.info("ci/result written to ClickHouse device=%s", device)

    if level == "L2":
        return {"ok": True, "action": "hud_only"}

    return {"ok": True, "action": "recorded"}
