"""GitHub App client factory (shared by both Lambda functions).

Call GithubAppFactory.setup_client() once at cold start — typically in the
Lambda module initializer, after secrets have been injected into env vars —
before calling any method that requires App authentication.
"""

import logging
import threading

from github import Auth, Github, GithubIntegration
from github.GithubException import GithubException

logger = logging.getLogger(__name__)


class GithubAppFactory:
    """Class-level GitHub App integration cache.

    Owns three responsibilities:
    - Minting installation access tokens (get_installation_token)
    - Providing per-call GitHub clients for installation tokens (get_repo_client)
    - Dispatching repository_dispatch events (create_repository_dispatch)
    - Listing installation repositories (list_installation_repositories)
    """

    _lock = threading.Lock()
    _integration: GithubIntegration | None = None
    _api_timeout: int = 30

    @classmethod
    def setup_client(
        cls,
        app_id: str | int,
        private_key: str,
        api_timeout: int = 30,
    ) -> None:
        """Configure the GitHub App integration.

        Must be called before get_installation_token() or get_repo_client().
        Replaces any previously configured integration.
        """
        if not private_key:
            raise RuntimeError("GITHUB_APP_PRIVATE_KEY is not configured")
        with cls._lock:
            cls._integration = GithubIntegration(int(app_id), private_key)
            cls._api_timeout = api_timeout
        logger.debug("GithubAppFactory configured app_id=%s timeout=%s", app_id, api_timeout)

    @classmethod
    def _get_integration(cls) -> GithubIntegration:
        if cls._integration is None:
            raise RuntimeError(
                "GitHub App not configured. "
                "Call GithubAppFactory.setup_client() first."
            )
        return cls._integration

    @classmethod
    def get_installation_token(cls, installation_id: int) -> str:
        """Mint a fresh short-lived installation access token."""
        token = cls._get_integration().get_access_token(int(installation_id)).token
        logger.debug("installation token obtained installation_id=%s", installation_id)
        return token

    @classmethod
    def get_repo_client(cls, installation_token: str) -> Github:
        """Return a Github client authenticated with a temporary installation token."""
        return Github(auth=Auth.Token(installation_token), timeout=cls._api_timeout)

    @classmethod
    def create_repository_dispatch(
        cls,
        *,
        installation_token: str,
        repo_full_name: str,
        event_type: str,
        client_payload: dict,
    ) -> None:
        """Send a repository_dispatch event to a downstream repo."""
        gh = cls.get_repo_client(installation_token)
        logger.debug("repository_dispatch repo=%s event_type=%s", repo_full_name, event_type)
        gh.get_repo(repo_full_name).create_repository_dispatch(event_type, client_payload)

    @classmethod
    def list_installation_repositories(
        cls, installation_token: str, max_results: int = 1000
    ) -> list[dict]:
        """List repositories accessible to a GitHub App installation token."""
        repos = []
        page = 1
        per_page = 100
        while True:
            gh = Github(auth=Auth.Token(installation_token), timeout=cls._api_timeout)
            requester = gh._Github__requester
            _resp_headers, data = requester.requestJsonAndCheck(
                "GET",
                "/installation/repositories",
                parameters={"per_page": per_page, "page": page},
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            chunk = (data or {}).get("repositories", [])
            if not chunk:
                break
            repos.extend(
                {
                    "name": repo.get("name"),
                    "full_name": repo.get("full_name"),
                    "html_url": repo.get("html_url"),
                }
                for repo in chunk
            )
            total_count = (data or {}).get("total_count")
            if (
                isinstance(total_count, int)
                and total_count >= 0
                and len(repos) >= total_count
            ):
                break
            if len(repos) >= max_results:
                break
            page += 1
        logger.info("installation repositories listed count=%d", len(repos))
        return repos
