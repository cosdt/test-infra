"""Helpers for loading the whitelist directly from GitHub."""

import logging
from urllib.parse import urlparse

from github import Github
from github.GithubException import GithubException
import yaml

from config import RelayConfig
from utils import parse_allowlist_info_map

logger = logging.getLogger(__name__)


def _read_whitelist_from_github_url(url: str) -> str:
    """Fetch whitelist YAML from a GitHub blob URL (https://github.com/<owner>/<repo>/blob/<ref>/<path>)."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if (
        parsed.scheme not in ("http", "https")
        or parsed.netloc != "github.com"
        or len(parts) < 5
        or parts[2] != "blob"
    ):
        raise RuntimeError(
            "Invalid GitHub whitelist URL. Expected format: "
            "https://github.com/<owner>/<repo>/blob/<ref>/<path/to/file>"
        )

    owner, repo, _, ref = parts[:4]
    file_path = "/".join(parts[4:])

    try:
        gh = Github(timeout=20)
        repo_obj = gh.get_repo(f"{owner}/{repo}")
        content_file = repo_obj.get_contents(file_path, ref=ref)
        if isinstance(content_file, list):
            raise RuntimeError(f"GitHub URL points to a directory, not a file: {url}")
        return content_file.decoded_content.decode("utf-8")
    except GithubException as exc:
        raise RuntimeError(
            f"Failed to fetch whitelist from GitHub URL {url}: {exc}"
        ) from exc


def load_allowlist_info_map(config: RelayConfig) -> dict[str, dict]:
    """Return repo metadata loaded directly from the GitHub whitelist URL."""
    logger.info("loading whitelist from %s", config.whitelist_url)
    yaml_str = _read_whitelist_from_github_url(config.whitelist_url)
    raw: dict = yaml.safe_load(yaml_str) or {}
    mapping = parse_allowlist_info_map(raw)
    logger.debug("allowlist loaded: %d device(s)", len(mapping))
    return mapping
