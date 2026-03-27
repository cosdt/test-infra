"""GitHub API helpers — the only module that imports PyGithub."""

import github
from github import GithubIntegration
from github.GithubException import GithubException


class GHError(RuntimeError):
    """Raised when a GitHub API call fails."""

    def __init__(self, message: str, *, status: int | None = None, data=None):
        super().__init__(message)
        self.status = status
        self.data = data


def create_access_token(app_id: str, private_key: str, installation_id: int) -> str:
    """Return a short-lived installation access token for the given GitHub App installation."""
    try:
        app_id_int = int(app_id)
    except ValueError:
        raise RuntimeError(f"GITHUB_APP_ID must be a valid integer, got {app_id!r}")
    try:
        return GithubIntegration(app_id_int, private_key).get_access_token(installation_id).token
    except GithubException as exc:
        raise GHError(
            f"Failed to obtain installation access token for installation_id={installation_id}",
            status=exc.status,
            data=exc.data,
        ) from exc
    except Exception as exc:
        raise GHError(
            f"Unexpected error obtaining installation access token for installation_id={installation_id}: {exc}"
        ) from exc


def create_repository_dispatch(
    *,
    token: str,
    repo_full_name: str,
    event_type: str,
    client_payload: dict,
    timeout: int = 20,
) -> None:
    try:
        github.Github(auth=github.Auth.Token(token), timeout=timeout).get_repo(
            repo_full_name
        ).create_repository_dispatch(event_type, client_payload)
    except GithubException as exc:
        raise GHError(
            f"repository_dispatch failed: repo={repo_full_name} event_type={event_type}",
            status=exc.status,
            data=exc.data,
        ) from exc
    except Exception as exc:
        raise GHError(
            f"Unexpected error during repository_dispatch: repo={repo_full_name} event_type={event_type}: {exc}"
        ) from exc


def get_repo_file(owner: str, repo: str, file_path: str, ref: str) -> str:
    """Fetch a file's decoded text content from a GitHub repository (unauthenticated)."""
    try:
        content_file = github.Github(timeout=20).get_repo(f"{owner}/{repo}").get_contents(
            file_path, ref=ref
        )
    except GithubException as exc:
        raise GHError(
            f"Failed to fetch {owner}/{repo}/{file_path}@{ref}",
            status=exc.status,
            data=exc.data,
        ) from exc
    except Exception as exc:
        raise GHError(
            f"Unexpected error fetching {owner}/{repo}/{file_path}@{ref}: {exc}"
        ) from exc
    if isinstance(content_file, list):
        raise RuntimeError(f"Path is a directory, not a file: {owner}/{repo}/{file_path}@{ref}")
    return content_file.decoded_content.decode("utf-8")
