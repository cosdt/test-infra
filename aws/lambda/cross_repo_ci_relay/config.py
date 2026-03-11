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


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if val is None or val == "":
        hint = "Create a .env file (see .env.example) or set it in the environment."
        if not _HAS_DOTENV:
            hint += " Note: python-dotenv is not available, so .env will NOT be loaded automatically. Install python-dotenv or export env vars manually."
        else:
            hint += " If you are using a .env file, ensure you start uvicorn from the directory that contains .env (or that find_dotenv can locate it)."
        raise RuntimeError(f"Missing required env var: {name}. {hint}")
    return val


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return int(default)
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Invalid int env var {name}={raw!r}") from e


def _require_env_int(name: str) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        hint = "Create a .env file (see .env.example) or set it in the environment."
        if not _HAS_DOTENV:
            hint += " Note: python-dotenv is not available, so .env will NOT be loaded automatically. Install python-dotenv or export env vars manually."
        else:
            hint += " If you are using a .env file, ensure you start uvicorn from the directory that contains .env (or that find_dotenv can locate it)."
        raise RuntimeError(f"Missing required env var: {name}. {hint}")
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

    @property
    def github_webhook_secret_bytes(self) -> bytes:
        return (self.github_webhook_secret or "").encode()

    @classmethod
    def from_env(cls) -> "RelayConfig":
        return cls(
            github_app_id=_require_env("GITHUB_APP_ID"),
            github_webhook_secret=_require_env("GITHUB_WEBHOOK_SECRET"),
            github_app_private_key_path=_require_env("GITHUB_APP_PRIVATE_KEY_PATH"),
            whitelist_path=_require_env("WHITELIST_PATH"),
            upstream_repo=_require_env("UPSTREAM_REPO"),
            clickhouse_url=_require_env("CLICKHOUSE_URL"),
            clickhouse_user=_require_env("CLICKHOUSE_USER"),
            clickhouse_password=_require_env("CLICKHOUSE_PASSWORD"),
            clickhouse_database=_require_env("CLICKHOUSE_DATABASE"),
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
