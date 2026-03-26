import random
import time
from typing import TypedDict


WHITELIST_LEVELS = ("L1", "L2", "L3", "L4")


class _TryAgain(Exception):
    pass


class _Attempt:
    def __init__(self, ctrl):
        self._c = ctrl

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._c._done = True
            return False

        if self._c._attempt >= self._c.max_retries:
            return False

        delay = self._c.base_delay * (2 ** (self._c._attempt - 1))
        if self._c.jitter:
            delay += random.uniform(0, 0.1 * delay)
        time.sleep(delay)

        return True


class RetryWithBackoff:
    def __init__(self, max_retries=5, base_delay=0.5, jitter=True):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.jitter = jitter

    def __iter__(self):
        self._attempt = 1
        self._done = False
        while True:
            yield _Attempt(self)
            if self._done:
                return
            self._attempt += 1


class PRDispatchPayload(TypedDict):
    # Full upstream repository name, for example `pytorch/pytorch`.
    upstream_repo: str
    # Exact head commit SHA of the upstream pull request.
    head_sha: str
    # Numeric pull request identifier in the upstream repository.
    pr_number: int
    # Source branch name of the upstream pull request.
    head_ref: str
    # Target branch name that the upstream pull request merges into.
    base_ref: str


class RelayHTTPException(Exception):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail


def parse_allowlist_info_map(raw: dict) -> dict[str, dict]:
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Invalid whitelist: expected dict with L1 entries, got {type(raw).__name__}"
        )

    mapping: dict[str, dict] = {}

    for level in WHITELIST_LEVELS:
        entries = raw.get(level) or []
        if not isinstance(entries, list):
            raise RuntimeError(
                f"Invalid whitelist: key {level} must map to a list, got {type(entries).__name__}"
            )
        for idx, entry in enumerate(entries):
            if not isinstance(entry, str):
                raise RuntimeError(
                    f"Invalid whitelist: {level}[{idx}] must be a repo string, got {type(entry).__name__}"
                )

            repo = entry.strip()

            if not repo or "/" not in repo:
                raise RuntimeError(
                    f"Invalid whitelist: {level}[{idx}] must be in owner/repo format"
                )

            repo = repo.strip("/")
            prev = mapping.get(repo)
            if prev:
                raise RuntimeError(f"Invalid whitelist: duplicate repo entry {repo!r}")

            mapping[repo] = {
                "level": level,
                "repo": repo,
                "url": f"https://github.com/{repo}",
            }

    return mapping


def pick_repo_full_name_by_allowlist(repos, allow_url: str):
    allow_url_n = allow_url.rstrip("/") if allow_url else None
    matches = [
        repo
        for repo in repos
        if (repo.get("html_url").rstrip("/") if repo.get("html_url") else None)
        == allow_url_n
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0].get("full_name")
    return {"ambiguous": [repo.get("full_name") for repo in matches]}
