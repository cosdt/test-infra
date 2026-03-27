import asyncio
import hashlib
import hmac
import logging

from github import GithubIntegration
from github.GithubException import GithubException

import github_client_helper
import redis_helper
from config import RelayConfig
from utils import PRDispatchPayload, RelayHTTPException

logger = logging.getLogger(__name__)

_integration: GithubIntegration | None = None


def verify_signature(config: RelayConfig, body: bytes, signature: str) -> None:
    mac = hmac.new(config.github_webhook_secret_bytes, body, hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    if not hmac.compare_digest(expected, signature):
        logger.warning("webhook signature mismatch")
        raise RelayHTTPException(status_code=401, detail="Bad signature")


def get_installation_token(config: RelayConfig, installation_id: int) -> str:
    global _integration
    if _integration is None:
        private_key = config.github_app_private_key
        if not private_key:
            raise RuntimeError("GITHUB_APP_PRIVATE_KEY is not configured")
        _integration = GithubIntegration(int(config.github_app_id), private_key)
        logger.debug("GithubIntegration initialized app_id=%s", config.github_app_id)

    token = _integration.get_access_token(int(installation_id)).token
    logger.debug("installation token obtained installation_id=%s", installation_id)
    return token


def _dispatch_to_allowlist(
    *,
    installation_token: str,
    allowlist_map: dict[str, str],
    event_type: str,
    client_payload: PRDispatchPayload,
    sha: str,
    action: str,
) -> tuple[list[dict], list[dict]]:
    async def _dispatch_async() -> tuple[list[dict], list[dict]]:
        dispatched: list[dict] = []
        failed: list[dict] = []

        targets = sorted(allowlist_map.items())
        max_concurrency = min(20, len(targets))
        semaphore = asyncio.Semaphore(max_concurrency)

        async def __dispatch_one(downstream_label: str, downstream_repo: str) -> dict:
            async with semaphore:
                logger.info(
                    "dispatching %s target=%s repo=%s sha=%.12s action=%s",
                    event_type,
                    downstream_label,
                    downstream_repo,
                    sha,
                    action,
                )
                try:
                    await asyncio.to_thread(
                        github_client_helper.create_repository_dispatch,
                        token=installation_token,
                        repo_full_name=downstream_repo,
                        event_type=event_type,
                        client_payload=client_payload,
                        timeout=20,
                    )
                    logger.info(
                        "dispatch succeeded event_type=%s target=%s repo=%s",
                        event_type,
                        downstream_label,
                        downstream_repo,
                    )
                    return {
                        "ok": True,
                        "result": {"target": downstream_label, "repo": downstream_repo},
                    }
                except GithubException as e:
                    logger.error(
                        "dispatch failed event_type=%s target=%s repo=%s status=%s data=%s",
                        event_type,
                        downstream_label,
                        downstream_repo,
                        getattr(e, "status", None),
                        getattr(e, "data", None),
                    )
                    return {
                        "ok": False,
                        "result": {
                            "target": downstream_label,
                            "repo": downstream_repo,
                            "error": f"GitHub dispatch failed: status={getattr(e, 'status', None)} data={getattr(e, 'data', None)}",
                        },
                    }
                except Exception as e:
                    logger.error(
                        "dispatch failed event_type=%s target=%s repo=%s error=%s",
                        event_type,
                        downstream_label,
                        downstream_repo,
                        e,
                    )
                    return {
                        "ok": False,
                        "result": {
                            "target": downstream_label,
                            "repo": downstream_repo,
                            "error": f"GitHub dispatch failed: {e}",
                        },
                    }

        dispatch_results = await asyncio.gather(
            *(
                __dispatch_one(downstream_label, downstream_repo)
                for downstream_label, downstream_repo in targets
            )
        )

        for dispatch_result in dispatch_results:
            if dispatch_result["ok"]:
                dispatched.append(dispatch_result["result"])
            else:
                failed.append(dispatch_result["result"])

        return dispatched, failed

    return asyncio.run(_dispatch_async())


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

    if event != "pull_request":
        logger.debug("event=%s ignored", event)
        return {"ignored": True}

    repo = payload["repository"]["full_name"]
    sha = payload["pull_request"]["head"]["sha"]
    pr_number = payload["pull_request"]["number"]
    head_ref = payload["pull_request"]["head"]["ref"]
    base_ref = payload["pull_request"]["base"]["ref"]
    installation_id = payload["installation"]["id"]
    action = payload["action"]

    if repo.lower() != config.upstream_repo.lower():
        logger.debug("pull_request repo=%s not upstream, ignored", repo)
        return {"ignored": True}

    installation_token = get_installation_token(config, int(installation_id))

    allowlist_info_map = redis_helper.load_allowlist_info_map(config)
    allowlist_map = {repo: info["repo"] for repo, info in allowlist_info_map.items()}
    if not allowlist_map:
        raise RelayHTTPException(status_code=400, detail="allowlist is empty")

    if action not in ("opened", "reopened", "synchronize"):
        logger.debug("pull_request action=%s ignored", action)
        return {"ignored": True}

    dispatched, failed = _dispatch_to_allowlist(
        installation_token=installation_token,
        allowlist_map=allowlist_map,
        event_type="pytorch-pr-trigger",
        client_payload={
            "upstream_repo": repo,
            "head_sha": sha,
            "pr_number": pr_number,
            "head_ref": head_ref,
            "base_ref": base_ref,
        },
        sha=sha,
        action=action,
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
