from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Optional

try:
    from dotenv import find_dotenv, load_dotenv

    _HAS_DOTENV = True
except Exception:
    find_dotenv = None
    load_dotenv = None
    _HAS_DOTENV = False


# Load environment variables from a local .env file when present.
# - Safe in Lambda: if no .env exists, this is a no-op.
# - Safe in dev: allows running uvicorn with a checked-in .env.example.
if _HAS_DOTENV:
    load_dotenv(find_dotenv(usecwd=True), override=False)


def _env(name: str, default: str | None = None) -> str:
    val = os.getenv(name)
    if val is None or val == "":
        return default or ""
    return val


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return int(default)
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Invalid int env var {name}={raw!r}") from e


@dataclass(frozen=True)
class RelayConfig:
    # ---------------------------------------------------------------------
    # GitHub App / Webhook
    # ---------------------------------------------------------------------
    github_app_id: str
    github_webhook_secret: str
    github_app_private_key_path: str

    # ---------------------------------------------------------------------
    # Relay behavior
    # ---------------------------------------------------------------------
    whitelist_path: str
    upstream_repo: str

    # ---------------------------------------------------------------------
    # ClickHouse
    # ---------------------------------------------------------------------
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_database: str

    # ---------------------------------------------------------------------
    # Allowlist fetch (used by result_handler)
    # ---------------------------------------------------------------------
    oot_whitelist_repo: str
    oot_whitelist_file: str
    oot_allowlist_ttl_seconds: int

    @property
    def github_webhook_secret_bytes(self) -> bytes:
        return (self.github_webhook_secret or "").encode()

    @classmethod
    def from_env(cls) -> "RelayConfig":
        default_whitelist_path = os.path.join(
            os.path.dirname(__file__),
            "whitelist.yaml",
        )
        return cls(
            github_app_id=_env("GITHUB_APP_ID", "2847493"),
            github_webhook_secret=_env("GITHUB_WEBHOOK_SECRET", "openEuler12#$"),
            github_app_private_key_path=_env(
                "GITHUB_APP_PRIVATE_KEY_PATH",
                "/opt/ci-gateway/pytorch-federated-ci-cosdt.2026-02-11.private-key.pem",
            ),
            whitelist_path=_env("WHITELIST_PATH", default_whitelist_path),
            upstream_repo=_env("UPSTREAM_REPO", "cosdt/Upstream"),
            clickhouse_url=_env("CLICKHOUSE_URL", "http://localhost:8123"),
            clickhouse_user=_env("CLICKHOUSE_USER", "admin"),
            clickhouse_password=_env("CLICKHOUSE_PASSWORD", "admin123"),
            clickhouse_database=_env("CLICKHOUSE_DATABASE", "default"),
            oot_whitelist_repo=_env("OOT_WHITELIST_REPO", "pytorch/test-infra"),
            # Path is repo-root relative for the GitHub contents API.
            oot_whitelist_file=_env(
                "OOT_WHITELIST_FILE",
                "aws/lambda/cross_repo_ci_relay/whitelist.yaml",
            ),
            oot_allowlist_ttl_seconds=_env_int("OOT_ALLOWLIST_TTL_SECONDS", 300),
        )


_CONFIG: Optional[RelayConfig] = None


def get_config() -> RelayConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = RelayConfig.from_env()
    return _CONFIG


# Backward-compatible module-level aliases (prefer using get_config()).
CONFIG = get_config()

GITHUB_APP_ID = CONFIG.github_app_id
GITHUB_WEBHOOK_SECRET = CONFIG.github_webhook_secret
GITHUB_WEBHOOK_SECRET_BYTES = CONFIG.github_webhook_secret_bytes
GITHUB_APP_PRIVATE_KEY_PATH = CONFIG.github_app_private_key_path

WHITELIST_PATH = CONFIG.whitelist_path
UPSTREAM_REPO = CONFIG.upstream_repo

CLICKHOUSE_URL = CONFIG.clickhouse_url
CLICKHOUSE_USER = CONFIG.clickhouse_user
CLICKHOUSE_PASSWORD = CONFIG.clickhouse_password
CLICKHOUSE_DATABASE = CONFIG.clickhouse_database

OOT_WHITELIST_REPO = CONFIG.oot_whitelist_repo
OOT_WHITELIST_FILE = CONFIG.oot_whitelist_file
OOT_ALLOWLIST_TTL_SECONDS = CONFIG.oot_allowlist_ttl_seconds
