from dataclasses import dataclass
import os

from dotenv import find_dotenv, load_dotenv


@dataclass(frozen=True)
class RelayConfig:
    github_app_id: str
    github_webhook_secret: str
    github_app_private_key: str
    secret_store_arn: str
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
    def from_env(cls, secrets=None) -> "RelayConfig":
        load_dotenv(find_dotenv(usecwd=False), override=False)
        github_webhook_secret = (
            getattr(secrets, "github_webhook_secret", "") if secrets else ""
        )
        github_app_private_key = (
            getattr(secrets, "github_app_private_key", "") if secrets else ""
        )
        clickhouse_password = (
            getattr(secrets, "clickhouse_password", "") if secrets else ""
        )
        redis_url = getattr(secrets, "redis_url", "") if secrets else ""
        return cls(
            github_app_id=os.getenv("GITHUB_APP_ID"),
            github_webhook_secret=github_webhook_secret,
            github_app_private_key=github_app_private_key,
            secret_store_arn=os.getenv("SECRET_STORE_ARN", ""),
            whitelist_path=os.getenv("WHITELIST_PATH"),
            upstream_repo=os.getenv("UPSTREAM_REPO"),
            clickhouse_url=os.getenv("CLICKHOUSE_URL"),
            clickhouse_user=os.getenv("CLICKHOUSE_USER"),
            clickhouse_password=clickhouse_password,
            clickhouse_database=os.getenv("CLICKHOUSE_DATABASE"),
            redis_url=redis_url,
            whitelist_ttl_seconds=int(os.getenv("WHITELIST_TTL_SECONDS", 1200)),
        )
