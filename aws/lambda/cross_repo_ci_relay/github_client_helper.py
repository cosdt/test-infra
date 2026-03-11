import threading

import github


class GHClientFactory:
    """Thread-local cached PyGithub clients keyed by installation token.

    This mirrors the idea used in pytorch-auto-revert, but is simplified for this
    lambda: we authenticate using an installation access token.
    """

    _lock = threading.Lock()
    _tlocal = threading.local()

    @classmethod
    def get_client(cls, *, token: str, timeout: int = 20) -> github.Github:
        if not token:
            raise RuntimeError("Missing GitHub installation token")

        if not hasattr(cls._tlocal, "_gh_clients"):
            cls._tlocal._gh_clients = {}

        cache: dict[tuple[str, int], github.Github] = cls._tlocal._gh_clients
        key = (token, int(timeout))
        if key in cache:
            return cache[key]

        # Ensure two concurrent initializations don't both create a client.
        with cls._lock:
            if key in cache:
                return cache[key]
            auth = github.Auth.Token(token)
            cache[key] = github.Github(auth=auth, timeout=timeout)
            return cache[key]


def create_repository_dispatch(
    *,
    token: str,
    repo_full_name: str,
    event_type: str,
    client_payload: dict,
    timeout: int = 20,
) -> None:
    gh = GHClientFactory.get_client(token=token, timeout=timeout)
    gh.get_repo(repo_full_name).create_repository_dispatch(event_type, client_payload)


def rerun_workflow_run(
    *,
    token: str,
    repo_full_name: str,
    run_id: int,
    timeout: int = 20,
) -> None:
    gh = GHClientFactory.get_client(token=token, timeout=timeout)
    gh.get_repo(repo_full_name).get_workflow_run(int(run_id)).rerun()
