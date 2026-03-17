import logging
import threading
from urllib.parse import urlparse

import clickhouse_connect

logger = logging.getLogger(__name__)

_OOT_CI_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS oot_ci_results (
    recorded_at           DateTime DEFAULT now(),
    device                String,
    upstream_repo         String,
    commit_sha            String,
    workflow_name         String,
    conclusion            String,
    status                String,
    run_url               String,
    upstream_check_run_id Int64 DEFAULT 0
) ENGINE = MergeTree()
ORDER BY (upstream_repo, commit_sha, device)
""".strip()

# Migration statement for tables created before upstream_check_run_id was added.
_ADD_CHECKRUN_ID_COLUMN_DDL = (
    "ALTER TABLE oot_ci_results "
    "ADD COLUMN IF NOT EXISTS upstream_check_run_id Int64 DEFAULT 0"
)


class CHCliFactory:
    """Class-level ClickHouse client cache. Call setup_client() once at cold start."""

    _lock = threading.Lock()
    _table_ensured = False
    _client = None

    @classmethod
    def setup_client(
        cls,
        url: str,
        username: str,
        password: str,
        database: str = "default",
    ) -> None:
        parsed = urlparse(url)
        cls._host = parsed.hostname or "localhost"
        cls._port = parsed.port or 8123
        cls._secure = parsed.scheme in ("https", "clickhouses")
        cls._username = username
        cls._password = password
        cls._database = database
        cls._client = None
        cls._table_ensured = False
        logger.debug(
            "CHCliFactory configured host=%s port=%s database=%s",
            cls._host, cls._port, cls._database,
        )

    @classmethod
    def _get_client(cls) -> clickhouse_connect.driver.Client:
        if cls._client is None:
            for attr in ("_host", "_port", "_username", "_password", "_database"):
                if not hasattr(cls, attr):
                    raise RuntimeError(
                        "ClickHouse client not configured. Call CHCliFactory.setup_client() first."
                    )
            cls._client = clickhouse_connect.get_client(
                host=cls._host,
                port=cls._port,
                username=cls._username,
                password=cls._password,
                database=cls._database,
                secure=cls._secure,
            )
            logger.debug("ClickHouse client created host=%s", cls._host)
        return cls._client

    @classmethod
    def ensure_table(cls) -> None:
        """Create oot_ci_results if it does not exist and apply any pending migrations."""
        if cls._table_ensured:
            return
        with cls._lock:
            if cls._table_ensured:
                return
            client = cls._get_client()
            client.command(_OOT_CI_RESULTS_DDL)
            client.command(_ADD_CHECKRUN_ID_COLUMN_DDL)
            cls._table_ensured = True
            logger.info("ClickHouse table oot_ci_results ensured")

    @classmethod
    def write_ci_result(
        cls,
        *,
        device: str,
        upstream_repo: str,
        commit_sha: str,
        workflow_name: str,
        status: str,
        conclusion: str,
        run_url: str,
        upstream_check_run_id: int = 0,
    ) -> None:
        cls._get_client().insert(
            "oot_ci_results",
            [[device, upstream_repo, commit_sha, workflow_name, conclusion, status, run_url, upstream_check_run_id]],
            column_names=["device", "upstream_repo", "commit_sha", "workflow_name",
                          "conclusion", "status", "run_url", "upstream_check_run_id"],
        )

    @classmethod
    def get_upstream_check_run_id(
        cls,
        upstream_repo: str,
        commit_sha: str,
        device: str,
        workflow_name: str,
    ) -> int:
        """Return the upstream check run ID stored for the in_progress row, or 0 if not found.

        Called by the result handler when processing a completed (Call 2) report so it
        can update the existing in_progress check run rather than creating a duplicate.
        """
        result = cls._get_client().query(
            "SELECT upstream_check_run_id FROM oot_ci_results "
            "WHERE upstream_repo = {upstream_repo:String} "
            "AND commit_sha = {commit_sha:String} "
            "AND device = {device:String} "
            "AND workflow_name = {workflow_name:String} "
            "AND status = 'in_progress' "
            "AND upstream_check_run_id > 0 "
            "ORDER BY recorded_at DESC LIMIT 1",
            parameters={
                "upstream_repo": upstream_repo,
                "commit_sha": commit_sha,
                "device": device,
                "workflow_name": workflow_name,
            },
        )
        rows = result.result_rows
        if rows:
            return int(rows[0][0])
        return 0

    @classmethod
    def query_workflows_by_sha_device(
        cls,
        upstream_repo: str,
        commit_sha: str,
        device: str,
    ) -> list[dict]:
        """Return the latest row per workflow_name for a given (upstream_repo, commit_sha, device).

        Used by the webhook labeled event handler to create/update check runs for
        workflows that were already reported before the ciflow/oot/<device> label
        was added to the PR.

        Only the most recent row per workflow is returned so that an in_progress row
        is not acted on when a completed row for the same workflow already exists.
        """
        result = cls._get_client().query(
            "SELECT workflow_name, status, conclusion, run_url, upstream_check_run_id "
            "FROM oot_ci_results "
            "WHERE upstream_repo = {upstream_repo:String} "
            "AND commit_sha = {commit_sha:String} "
            "AND device = {device:String} "
            "ORDER BY recorded_at ASC",
            parameters={
                "upstream_repo": upstream_repo,
                "commit_sha": commit_sha,
                "device": device,
            },
        )
        # Deduplicate by workflow_name, keeping the last (most recent) row.
        # Iterating in ASC order means later entries overwrite earlier ones.
        latest: dict[str, dict] = {}
        for row in result.result_rows:
            latest[row[0]] = {
                "workflow_name": row[0],
                "status": row[1],
                "conclusion": row[2],
                "run_url": row[3],
                "upstream_check_run_id": int(row[4]),
            }
        return list(latest.values())
