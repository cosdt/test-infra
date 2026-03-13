from dataclasses import dataclass
import os

from dotenv import find_dotenv, load_dotenv


@dataclass(frozen=True)
class RelayConfig:
    github_app_id: str
    github_webhook_secret: str
    github_app_private_key_path: str
    whitelist_path: str
    upstream_repo: str
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_database: str
    redis_url: str
    whitelist_ttl_seconds: int

    @property
    def github_webhook_secret_bytes(self) -> bytes:
        return (self.github_webhook_secret or "").encode()

    @classmethod
    def from_env(cls) -> "RelayConfig":
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
