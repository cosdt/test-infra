"""Handler for GitHub pull_request webhook events."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aiohttp
import gh_helper
import redis_helper
from allowlist import AllowlistLevel, load_allowlist
from config import RelayConfig
from github import GithubException
from utils import HTTPException, PRDispatchPayload


@dataclass(frozen=True)
class PREvent:
    repo: str
    sha: str
    pr_number: int
    head_ref: str
    base_ref: str
    installation_id: int
    action: str


def extract_pr_fields(payload: dict) -> PREvent:
    try:
        return PREvent(
            repo=payload["repository"]["full_name"],
            sha=payload["pull_request"]["head"]["sha"],
            pr_number=payload["pull_request"]["number"],
            head_ref=payload["pull_request"]["head"]["ref"],
            base_ref=payload["pull_request"]["base"]["ref"],
            installation_id=payload["installation"]["id"],
            action=payload["action"],
        )
    except KeyError as e:
        raise HTTPException(
            status_code=400, detail=f"Missing required field: {e}"
        ) from e


logger = logging.getLogger(__name__)


def _dispatch_to_allowlist(
    *,
    config: RelayConfig,
    installation_id: int,
    client_payload: PRDispatchPayload,
    action: str,
    event_type: str = "pytorch-pr-trigger",
) -> tuple[list[dict], list[dict]]:
    # Due to the 10s timeout of Github webhooks, we need to dispatch to downstream
    # repositories concurrently to ensure we can trigger as many downstream workflows
    # as possible within the time limit. This function uses asyncio to dispatch to
    # multiple downstream repositories concurrently while respecting a maximum
    # concurrency limit to avoid overwhelming the GitHub API or the Lambda
    # environment.
    # Check allowlist first — avoid unnecessary token fetch if there's nothing to dispatch
    allowlist = load_allowlist(config)
    backends, _ = allowlist.get_from_level(AllowlistLevel.L1)
    if not backends:
        logger.info("allowlist is empty, nothing to dispatch")
        return [], []

    installation_token = gh_helper.create_access_token(
        config.github_app_id, config.github_app_private_key, installation_id
    )

    sha = client_payload["head_sha"]
    targets = sorted(backends)

    async def _dispatch_async(
        session: aiohttp.ClientSession,
    ) -> tuple[list[dict], list[dict]]:
        max_concurrency = min(20, len(targets) or 1)
        semaphore = asyncio.Semaphore(max_concurrency)

        async def __dispatch_one(downstream_repo: str) -> tuple[bool, dict]:
            async with semaphore:
                logger.info(
                    "dispatching %s repo=%s sha=%.12s action=%s",
                    event_type,
                    downstream_repo,
                    sha,
                    action,
                )
                try:
                    await gh_helper.create_repository_dispatch(
                        session=session,
                        token=installation_token,
                        repo_full_name=downstream_repo,
                        event_type=event_type,
                        client_payload=client_payload,
                        timeout=20,
                    )
                    logger.info(
                        "dispatch succeeded event_type=%s repo=%s",
                        event_type,
                        downstream_repo,
                    )
                    return True, {"repo": downstream_repo}
                except GithubException as e:
                    logger.error(
                        "dispatch failed event_type=%s repo=%s status=%s data=%s",
                        event_type,
                        downstream_repo,
                        e.status,
                        e.data,
                    )
                    return False, {
                        "repo": downstream_repo,
                        "error": f"GitHub dispatch failed: status={e.status} data={e.data}",
                    }
                except Exception as e:
                    logger.error(
                        "dispatch failed event_type=%s repo=%s error=%s",
                        event_type,
                        downstream_repo,
                        e,
                    )
                    return False, {
                        "repo": downstream_repo,
                        "error": f"GitHub dispatch failed: {e}",
                    }

        results = await asyncio.gather(*(__dispatch_one(repo) for repo in targets))
        dispatched = [r for ok, r in results if ok]
        failed = [r for ok, r in results if not ok]
        return dispatched, failed

    async def _run() -> tuple[list[dict], list[dict]]:
        # Create the ClientSession inside asyncio.run() so its lifetime matches the
        # event loop. Reusing a global session across asyncio.run() calls is unsafe
        # because each call creates a new event loop, invalidating the previous session.
        async with aiohttp.ClientSession() as session:
            return await _dispatch_async(session)

    return asyncio.run(_run())


def handle(config: RelayConfig, payload: dict, delivery_id: str) -> dict:
    # Atomically deduplicate using SET NX — avoids the TOCTOU race of a separate
    # exists-check + set pair. Returns False when the key already existed (duplicate).
    if delivery_id:
        if not redis_helper.set_delivery_if_unseen(config, delivery_id):
            logger.info(
                "delivery_id=%s already processed, ignoring duplicate", delivery_id
            )
            return {"ignored": True}
    event: PREvent = extract_pr_fields(payload)

    if event.action not in ("opened", "reopened", "synchronize"):
        logger.debug("pull_request action=%s ignored", event.action)
        return {"ignored": True}

    dispatched, failed = _dispatch_to_allowlist(
        config=config,
        installation_id=event.installation_id,
        client_payload={
            "upstream_repo": event.repo,
            "head_sha": event.sha,
            "pr_number": event.pr_number,
            "head_ref": event.head_ref,
            "base_ref": event.base_ref,
        },
        action=event.action,
    )

    if failed and not dispatched:
        logger.error("no downstream dispatch succeeded failed=%s", failed)
        raise HTTPException(
            status_code=502,
            detail={"message": "No downstream dispatch succeeded", "failed": failed},
        )

    return {"ok": True, "dispatched": dispatched, "failed": failed}
