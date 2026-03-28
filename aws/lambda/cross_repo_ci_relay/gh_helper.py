"""GitHub API helpers — the only module that imports PyGithub."""

import logging

import aiohttp
import github
from github import GithubException, GithubIntegration
from utils import PRDispatchPayload


_GITHUB_API_BASE = "https://api.github.com"

logger = logging.getLogger(__name__)


def create_access_token(app_id: str, private_key: str, installation_id: int) -> str:
    """Return a short-lived installation access token for the given GitHub App installation."""
    try:
        app_id_int = int(app_id)
    except ValueError:
        raise RuntimeError(f"GITHUB_APP_ID must be a valid integer, got {app_id!r}")
    return (
        GithubIntegration(app_id_int, private_key)
        .get_access_token(installation_id)
        .token
    )


async def create_repository_dispatch(
    *,
    session: aiohttp.ClientSession,
    token: str,
    repo_full_name: str,
    event_type: str,
    client_payload: PRDispatchPayload,
    timeout: int = 20,
) -> None:
    # The reason why not using pyGithub is that it doesn't support async and we want to
    # avoid blocking the event loop when doing GitHub API calls, which can be slow
    # due to network latency. Using aiohttp allows us to make non-blocking HTTP requests to GitHub's API.
    url = f"{_GITHUB_API_BASE}/repos/{repo_full_name}/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"event_type": event_type, "client_payload": dict(client_payload)}
    logger.debug(
        "repository_dispatch repo=%s event_type=%s", repo_full_name, event_type
    )
    async with session.post(
        url,
        headers=headers,
        json=payload,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status not in (200, 204):
            data = await resp.json(content_type=None)
            raise GithubException(resp.status, data, None)


def get_repo_file(owner: str, repo: str, file_path: str, ref: str) -> str:
    """Fetch a file's decoded text content from a GitHub repository (unauthenticated)."""
    content_file = (
        github.Github(timeout=20)
        .get_repo(f"{owner}/{repo}")
        .get_contents(file_path, ref=ref)
    )

    if isinstance(content_file, list):
        raise RuntimeError(
            f"Path is a directory, not a file: {owner}/{repo}/{file_path}@{ref}"
        )

    return content_file.decoded_content.decode("utf-8")
