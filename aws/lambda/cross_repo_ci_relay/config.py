from dataclasses import dataclass
import os

from dotenv import find_dotenv, load_dotenv


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
    # Redis (whitelist cache)
    # ---------------------------------------------------------------------
    redis_url: str
    whitelist_ttl_seconds: int

    @property
    def github_webhook_secret_bytes(self) -> bytes:
        return (self.github_webhook_secret or "").encode()

    @classmethod
    def from_env(cls) -> "RelayConfig":
        # Do not depend on process cwd (uvicorn reload/app-dir can change it).
        # Default find_dotenv behavior searches relative to this file.
        load_dotenv(find_dotenv(usecwd=False), override=False)
        return cls(
            github_app_id=os.getenv("GITHUB_APP_ID"),
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET"),
            github_app_private_key_path=os.getenv("GITHUB_APP_PRIVATE_KEY_PATH"),
            whitelist_path=os.getenv("WHITELIST_PATH"),
            upstream_repo=os.getenv("UPSTREAM_REPO"),
            clickhouse_url=os.getenv("CLICKHOUSE_URL"),
            clickhouse_user=os.getenv("CLICKHOUSE_USER"),
            clickhouse_password=os.getenv("CLICKHOUSE_PASSWORD"),
            clickhouse_database=os.getenv("CLICKHOUSE_DATABASE"),
            redis_url=os.getenv("REDIS_URL", ""),
            whitelist_ttl_seconds=int(os.getenv("WHITELIST_TTL_SECONDS", 1200)),
        )

    @classmethod
    def from_event(cls, event: dict) -> "RelayConfig":
        """For testing: construct config from a GitHub event payload."""
        return cls(
            github_app_id=event.get("github_app_id", ""),
            github_webhook_secret=event.get("github_webhook_secret", ""),
            github_app_private_key_path=event.get("github_app_private_key_path", ""),
            whitelist_path=event.get("whitelist_path", ""),
            upstream_repo=event.get("upstream_repo", ""),
            clickhouse_url=event.get("clickhouse_url", ""),
            clickhouse_user=event.get("clickhouse_user", ""),
            clickhouse_password=event.get("clickhouse_password", ""),
            clickhouse_database=event.get("clickhouse_database", ""),
            redis_url=event.get("redis_url", ""),
            whitelist_ttl_seconds=int(event.get("whitelist_ttl_seconds", 1200)),
        )

    @classmethod
    def default_env(cls) -> "RelayConfig":
        return cls(
            github_app_id="",
            github_webhook_secret="",
            github_app_private_key_path="",
            whitelist_path="",
            upstream_repo="",
            clickhouse_url="",
            clickhouse_user="",
            clickhouse_password="",
            clickhouse_database="",
            redis_url="",
            whitelist_ttl_seconds=1200,
        )
