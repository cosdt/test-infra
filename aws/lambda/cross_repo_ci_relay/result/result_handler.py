import logging
import re

from config import RelayConfig
from utils import RelayHTTPException
from clickhouse_client_helper import CHCliFactory
from github_client_helper import GithubAppFactory
import whitelist_redis_helper
import pr_redis_helper
import checkrun_helper

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
        raise RelayHTTPException(
            status_code=400, detail=f"Missing required field: {e}"
        ) from e

    status = data.get("status", "")
    summary = data.get("summary", "")

    logger.info(
        "ci/result device=%s level=%s status=%s conclusion=%s workflow=%s sha=%.12s",
        device,
        level,
        status,
        conclusion,
        workflow_name,
        commit_sha,
    )

    if level == "L1":
        return {"ok": True, "action": "ignored"}

    # --- Determine upstream check run action (L3 / L4 only) ---
    # This must happen before the ClickHouse write so we can persist the
    # upstream_check_run_id in the same row.
    upstream_check_run_id = 0
    cr_action = "hud_only"

    if level in ("L3", "L4"):
        pr_info = pr_redis_helper.get_pr_info(config, upstream_repo, commit_sha)
        if pr_info is None:
            logger.warning(
                "pr_info missing in Redis repo=%s sha=%.12s device=%s; check run skipped",
                upstream_repo,
                commit_sha,
                device,
            )
        else:
            # L4 always acts; L3 only when the ciflow/oot/<device> label is present.
            should_act = (level == "L4") or (
                device in pr_info.get("labeled_devices", [])
            )
            if should_act:
                installation_id = pr_info["installation_id"]
                installation_token = GithubAppFactory.get_installation_token(
                    int(installation_id)
                )

                if status == "completed":
                    # Call 2: try to update the check run created during Call 1.
                    existing_cr_id = CHCliFactory.get_upstream_check_run_id(
                        upstream_repo, commit_sha, device, workflow_name
                    )
                    # If upstream already has a check run for this
                    # sha/device/workflow in in_progress state, update it to completed
                    if existing_cr_id > 0:
                        checkrun_helper.update_check_run(
                            installation_token=installation_token,
                            upstream_repo=upstream_repo,
                            upstream_check_run_id=existing_cr_id,
                            device=device,
                            workflow_name=workflow_name,
                            status=status,
                            conclusion=conclusion,
                            run_url=run_url,
                            summary=summary,
                        )
                        upstream_check_run_id = existing_cr_id
                        cr_action = "check_run_updated"
                    else:
                        # Label was added after Call 1 (or Call 1 had no label yet);
                        # create a completed check run directly.
                        upstream_check_run_id = checkrun_helper.create_check_run(
                            installation_token=installation_token,
                            upstream_repo=upstream_repo,
                            sha=commit_sha,
                            device=device,
                            workflow_name=workflow_name,
                            status=status,
                            conclusion=conclusion,
                            run_url=run_url,
                            summary=summary,
                        )
                        cr_action = "check_run_created"
                else:
                    # Call 1 (in_progress): always create a fresh check run.
                    upstream_check_run_id = checkrun_helper.create_check_run(
                        installation_token=installation_token,
                        upstream_repo=upstream_repo,
                        sha=commit_sha,
                        device=device,
                        workflow_name=workflow_name,
                        status=status,
                        conclusion=conclusion,
                        run_url=run_url,
                        summary=summary,
                    )
                    cr_action = "check_run_created"

    # --- Persist to ClickHouse (all L2+) ---
    CHCliFactory.ensure_table()
    CHCliFactory.write_ci_result(
        device=device,
        upstream_repo=upstream_repo,
        commit_sha=commit_sha,
        workflow_name=workflow_name,
        status=status,
        conclusion=conclusion,
        run_url=run_url,
        upstream_check_run_id=upstream_check_run_id,
    )
    logger.info(
        "ci/result written to ClickHouse device=%s upstream_check_run_id=%s",
        device,
        upstream_check_run_id,
    )

    return {"ok": True, "action": cr_action}
