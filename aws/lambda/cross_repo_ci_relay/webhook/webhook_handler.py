import logging
import re

from github.GithubException import GithubException

import checkrun_helper
import github_client_helper
import pr_redis_helper
import whitelist_redis_helper
from clickhouse_client_helper import CHCliFactory
from config import RelayConfig
from utils import (
    RelayHTTPException,
    get_installation_token,
    list_installation_repositories,
    pick_repo_full_name_by_allowlist,
    verify_signature,
)

# Matches 'ciflow/oot/<device>' PR labels (used for L3 label-gated check runs).
_CIFLOW_OOT_RE = re.compile(r"^ciflow/oot/(.+)$")

logger = logging.getLogger(__name__)


def _handle_pr_dispatch(
    config: RelayConfig,
    payload: dict,
    installation_token: str,
    installation_id: int,
    repo: str,
    sha: str,
    action: str,
) -> dict:
    """Handle PR opened/reopened/synchronize: cache PR info and dispatch to downstream repos."""
    pr_number = payload["pull_request"]["number"]
    raw_labels = [
        lbl.get("name")
        for lbl in ((payload.get("pull_request") or {}).get("labels") or [])
        if isinstance(lbl, dict)
    ]
    logger.info(
        "pull_request action=%s repo=%s sha=%.12s labels=%s", action, repo, sha, raw_labels
    )

    repos = list_installation_repositories(installation_token)
    allowlist_info_map = whitelist_redis_helper.load_allowlist_info_map(config)
    allowlist_map = {
        device: info.get("url", "")
        for device, info in allowlist_info_map.items()
        if info.get("url")
    }
    if not allowlist_map:
        raise RelayHTTPException(status_code=400, detail="allowlist is empty")

    # Collect ciflow/oot/<device> labels that correspond to L3 devices in the allowlist.
    # These are persisted in Redis so the result handler can gate check run creation
    # without re-reading PR labels from the GitHub API.
    labeled_devices: list[str] = [
        m.group(1)
        for name in raw_labels
        if name and (m := _CIFLOW_OOT_RE.match(name))
        and m.group(1) in allowlist_info_map
        and allowlist_info_map[m.group(1)]["level"] == "L3"
    ]

    pr_redis_helper.cache_pr_info(
        config,
        upstream_repo=repo,
        sha=sha,
        pr_number=int(pr_number),
        installation_id=installation_id,
        labeled_devices=labeled_devices,
    )

    dispatched: list[dict] = []
    failed: list[dict] = []
    for downstream_device, allow_url in sorted(allowlist_map.items()):
        picked = pick_repo_full_name_by_allowlist(repos, allow_url)
        if not picked:
            logger.warning(
                "dispatch skipped device=%s allow_url=%s reason=repo_not_accessible",
                downstream_device,
                allow_url,
            )
            failed.append(
                {
                    "downstream_device": downstream_device,
                    "allow_url": allow_url,
                    "error": "This installation cannot access repo; ensure the app installation includes it",
                }
            )
            continue
        if isinstance(picked, dict) and picked.get("ambiguous"):
            logger.warning(
                "dispatch skipped device=%s allow_url=%s reason=ambiguous candidates=%s",
                downstream_device,
                allow_url,
                picked["ambiguous"],
            )
            failed.append(
                {
                    "downstream_device": downstream_device,
                    "allow_url": allow_url,
                    "error": "Multiple repos matched allowlist; refine allowlist_map",
                    "candidates": picked["ambiguous"],
                }
            )
            continue

        logger.info(
            "dispatching pytorch-pr-trigger device=%s repo=%s sha=%.12s action=%s",
            downstream_device,
            picked,
            sha,
            action,
        )
        try:
            github_client_helper.create_repository_dispatch(
                token=installation_token,
                repo_full_name=picked,
                event_type="pytorch-pr-trigger",
                client_payload={"upstream_repo": repo, "commit_sha": sha},
                timeout=20,
            )
            dispatched.append({"downstream_device": downstream_device, "repo": picked})
            logger.info(
                "dispatch succeeded device=%s repo=%s", downstream_device, picked
            )
        except GithubException as e:
            logger.error(
                "dispatch failed device=%s repo=%s status=%s data=%s",
                downstream_device,
                picked,
                getattr(e, "status", None),
                getattr(e, "data", None),
            )
            failed.append(
                {
                    "downstream_device": downstream_device,
                    "repo": picked,
                    "error": f"GitHub dispatch failed: status={getattr(e, 'status', None)} data={getattr(e, 'data', None)}",
                }
            )
        except Exception as e:
            logger.error(
                "dispatch failed device=%s repo=%s error=%s",
                downstream_device,
                picked,
                e,
            )
            failed.append(
                {
                    "downstream_device": downstream_device,
                    "repo": picked,
                    "error": f"GitHub dispatch failed: {e}",
                }
            )

    if not dispatched:
        logger.error("no downstream dispatch succeeded failed=%s", failed)
        raise RelayHTTPException(
            status_code=403,
            detail={
                "message": "No downstream dispatch succeeded",
                "failed": failed,
            },
        )

    return {"ok": True, "dispatched": dispatched, "failed": failed}


def _handle_pr_labeled(
    config: RelayConfig,
    payload: dict,
    installation_id: int,
    repo: str,
    sha: str,
) -> dict:
    """Handle PR labeled: update Redis label state and create/update check runs for L3 devices.

    Three situations are covered:
    - No CH rows yet (label added before any dispatch call): just cache the label;
      the result handler will create the check run on the first downstream report.
    - Some rows exist with status='in_progress' (label added mid-run, Call 1 done):
      create an in_progress check run and persist its ID to CH so Call 2 can update it.
    - Some rows exist with status='completed' (label added after all workflows finished):
      create a completed check run directly; no further callbacks expected.
    """
    label_name = (payload.get("label") or {}).get("name", "")
    m = _CIFLOW_OOT_RE.match(label_name)
    if not m:
        logger.debug("labeled event label=%s not ciflow/oot/; ignored", label_name)
        return {"ignored": True}
    device = m.group(1)

    allowlist_info_map = whitelist_redis_helper.load_allowlist_info_map(config)
    info = allowlist_info_map.get(device)
    if not info or info["level"] != "L3":
        logger.debug("labeled device=%s not L3 in allowlist; ignored", device)
        return {"ignored": True}

    logger.info("pr_labeled device=%s repo=%s sha=%.12s", device, repo, sha)

    updated_info = pr_redis_helper.add_labeled_device(config, repo, sha, device)
    if updated_info is None:
        # Race condition: the labeled event arrived before the opened/synchronize
        # event was processed and pr_info was never written.  Bootstrap a minimal
        # entry so the result handler can still gate on the label correctly.
        # pr_number is available from the payload in this context.
        pr_number = payload["pull_request"]["number"]
        pr_redis_helper.cache_pr_info(
            config,
            upstream_repo=repo,
            sha=sha,
            pr_number=int(pr_number),
            installation_id=installation_id,
            labeled_devices=[device],
        )
        logger.info(
            "pr_labeled bootstrapped pr_info repo=%s sha=%.12s device=%s",
            repo, sha, device,
        )

    # Query ClickHouse for any workflows already reported for this device.
    CHCliFactory.setup_client(
        url=config.clickhouse_url,
        username=config.clickhouse_user,
        password=config.clickhouse_password,
        database=config.clickhouse_database,
    )
    CHCliFactory.ensure_table()
    rows = CHCliFactory.query_workflows_by_sha_device(repo, sha, device)

    if not rows:
        logger.info(
            "pr_labeled device=%s no CH rows yet; result handler will create on first report",
            device,
        )
        return {"ok": True, "action": "label_cached", "check_runs_created": 0}

    installation_token = get_installation_token(config, installation_id)
    created = 0
    updated = 0

    for row in rows:
        workflow_name = row["workflow_name"]
        status = row["status"]
        conclusion = row["conclusion"]
        run_url = row["run_url"]
        existing_cr_id = row["upstream_check_run_id"]

        if existing_cr_id > 0:
            # Check run was already created (e.g. label applied twice); update to current state.
            checkrun_helper.update_check_run(
                config=config,
                installation_token=installation_token,
                upstream_repo=repo,
                upstream_check_run_id=existing_cr_id,
                device=device,
                workflow_name=workflow_name,
                status=status,
                conclusion=conclusion,
                run_url=run_url,
            )
            updated += 1
        else:
            cr_id = checkrun_helper.create_check_run(
                config=config,
                installation_token=installation_token,
                upstream_repo=repo,
                sha=sha,
                device=device,
                workflow_name=workflow_name,
                status=status,
                conclusion=conclusion,
                run_url=run_url,
            )
            created += 1
            if status == "in_progress":
                # Persist the check run ID so Call 2 from the downstream can update
                # it rather than creating a duplicate completed check run.
                CHCliFactory.write_ci_result(
                    device=device,
                    upstream_repo=repo,
                    commit_sha=sha,
                    workflow_name=workflow_name,
                    status=status,
                    conclusion=conclusion,
                    run_url=run_url,
                    upstream_check_run_id=cr_id,
                )

    logger.info(
        "pr_labeled device=%s check_runs_created=%s check_runs_updated=%s",
        device, created, updated,
    )
    return {
        "ok": True,
        "action": "label_cached",
        "check_runs_created": created,
        "check_runs_updated": updated,
    }


def handle_github_webhook(
    config: RelayConfig,
    body: bytes,
    payload: dict,
    signature: str,
    event: str,
):
    if not signature:
        raise RelayHTTPException(status_code=400, detail="No signature")
    verify_signature(config, body, signature)

    # Only pull_request events are consumed by this relay.
    if event != "pull_request":
        logger.debug("event=%s ignored", event)
        return {"ignored": True}

    repo = payload["repository"]["full_name"]
    sha = payload["pull_request"]["head"]["sha"]
    installation_id = payload["installation"]["id"]
    action = payload["action"]

    if repo.lower() != config.upstream_repo.lower():
        logger.debug("pull_request repo=%s not upstream, ignored", repo)
        return {"ignored": True}

    if action in ("opened", "reopened", "synchronize"):
        installation_token = get_installation_token(config, int(installation_id))
        return _handle_pr_dispatch(
            config, payload, installation_token, int(installation_id), repo, sha, action
        )
    elif action == "labeled":
        return _handle_pr_labeled(config, payload, int(installation_id), repo, sha)
    else:
        logger.debug("pull_request action=%s ignored", action)
        return {"ignored": True}
