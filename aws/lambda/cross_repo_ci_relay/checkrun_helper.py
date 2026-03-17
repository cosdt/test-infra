"""GitHub Check Run create/update helpers (shared by both Lambda functions).

Naming convention (per RFC §Demo):
  "oot / <device> / <workflow_name>"

All calls use a fresh Github client from an installation token; no long-lived
clients are kept because tokens expire after 1 hour.
"""

import logging

from github import Auth, Github

from config import RelayConfig

logger = logging.getLogger(__name__)


def check_run_name(device: str, workflow_name: str) -> str:
    """Return the canonical upstream check run name for a downstream workflow."""
    return f"oot / {device} / {workflow_name}"


def create_check_run(
    *,
    config: RelayConfig,
    installation_token: str,
    upstream_repo: str,
    sha: str,
    device: str,
    workflow_name: str,
    status: str,
    conclusion: str,
    run_url: str,
    summary: str = "",
) -> int:
    """Create a new check run on the upstream repo commit.

    Args:
        status:     GitHub check run status: "in_progress" or "completed".
        conclusion: Required when status=="completed"; ignored otherwise.
                    Values: "success", "failure", "neutral", "cancelled",
                    "skipped", "timed_out", "action_required".
        run_url:    URL to the downstream workflow run (used as details_url).
        summary:    Optional human-readable summary for the check run output panel.

    Returns:
        The integer check run ID assigned by GitHub.
    """
    gh = Github(auth=Auth.Token(installation_token), timeout=config.github_api_timeout)
    repo = gh.get_repo(upstream_repo)
    name = check_run_name(device, workflow_name)

    kwargs: dict = dict(
        name=name,
        head_sha=sha,
        details_url=run_url,
        status=status,
        output={"title": name, "summary": summary or run_url},
    )
    if status == "completed":
        kwargs["conclusion"] = conclusion

    cr = repo.create_check_run(**kwargs)
    logger.info(
        "check_run created id=%s name=%s status=%s upstream=%s sha=%.12s",
        cr.id,
        name,
        status,
        upstream_repo,
        sha,
    )
    return cr.id


def update_check_run(
    *,
    config: RelayConfig,
    installation_token: str,
    upstream_repo: str,
    upstream_check_run_id: int,
    device: str,
    workflow_name: str,
    status: str,
    conclusion: str,
    run_url: str,
    summary: str = "",
) -> None:
    """Update an existing check run identified by upstream_check_run_id."""
    gh = Github(auth=Auth.Token(installation_token), timeout=config.github_api_timeout)
    repo = gh.get_repo(upstream_repo)
    name = check_run_name(device, workflow_name)

    cr = repo.get_check_run(upstream_check_run_id)
    kwargs: dict = dict(
        name=name,
        details_url=run_url,
        status=status,
        output={"title": name, "summary": summary or run_url},
    )
    if status == "completed":
        kwargs["conclusion"] = conclusion

    cr.edit(**kwargs)
    logger.info(
        "check_run updated id=%s name=%s status=%s upstream=%s",
        upstream_check_run_id,
        name,
        status,
        upstream_repo,
    )
