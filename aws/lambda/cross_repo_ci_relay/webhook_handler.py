import logging

from fastapi import HTTPException

from github.GithubException import GithubException

import github_client_helper
import utils
import whitelist_redis_helper as whitelist_redis_helper

from config import RelayConfig

logger = logging.getLogger(__name__)


def handle_github_webhook(
    config: RelayConfig,
    body: bytes,
    payload: dict,
    signature: str,
    event: str,
):
    if not signature:
        raise HTTPException(status_code=400, detail="No signature")
    utils.verify_signature(config, body, signature)

    # Only pull_request events are consumed by this relay.
    if event != "pull_request":
        logger.debug("webhook event=%s ignored", event)
        return {"ignored": True}

    repo = payload["repository"]["full_name"]
    sha = payload["pull_request"]["head"]["sha"]
    installation_id = payload["installation"]["id"]
    action = payload["action"]

    if repo.lower() != config.upstream_repo.lower():
        return {"ignored": True}

    if action not in ("opened", "reopened", "synchronize"):
        return {"ignored": True}

    installation_token = utils.get_installation_token(config, int(installation_id))

    labels = [
        l.get("name")
        for l in ((payload.get("pull_request") or {}).get("labels") or [])
        if isinstance(l, dict)
    ]
    logger.info(
        "pull_request action=%s repo=%s sha=%.12s labels=%s", action, repo, sha, labels
    )

    # Resolve allowlisted repositories and dispatch downstream events.
    repos = utils.list_installation_repositories(installation_token)
    allowlist_info_map = whitelist_redis_helper.load_allowlist_info_map(config)
    allowlist_map = {
        device: info.get("url", "")
        for device, info in allowlist_info_map.items()
        if info.get("url")
    }
    if not allowlist_map:
        raise HTTPException(status_code=400, detail="allowlist_map is empty")

    dispatched: list[dict] = []
    failed: list[dict] = []
    for downstream_device, allow_url in sorted(allowlist_map.items()):
        picked = utils.pick_repo_full_name_by_allowlist(repos, allow_url)
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
        raise HTTPException(
            status_code=403,
            detail={
                "message": "No downstream dispatch succeeded",
                "failed": failed,
            },
        )

    return {"ok": True, "dispatched": dispatched, "failed": failed}
