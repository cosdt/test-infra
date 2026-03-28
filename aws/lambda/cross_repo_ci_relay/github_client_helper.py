import logging

import aiohttp
from github.GithubException import GithubException
from utils import PRDispatchPayload


logger = logging.getLogger(__name__)

_GITHUB_API_BASE = "https://api.github.com"
_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def create_repository_dispatch(
    *,
    token: str,
    repo_full_name: str,
    event_type: str,
    client_payload: PRDispatchPayload,
    timeout: int = 20,
) -> None:
    # The reason why not using pyGithub is that it doesn't support async and we want to
    # avoid blocking the event loop when doing GitHub API calls, which can be slow
    # due to network latency. Using aiohttp allows us to make non-blocking HTTP requests to GitHub's API.
    session = _get_session()
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
