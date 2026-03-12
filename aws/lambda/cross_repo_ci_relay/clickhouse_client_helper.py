import logging
import threading
from urllib.parse import urlparse

import clickhouse_connect

logger = logging.getLogger(__name__)

_OOT_CI_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS oot_ci_results (
    recorded_at   DateTime DEFAULT now(),
    device        String,
    upstream_repo String,
    commit_sha    String,
    workflow_name String,
    conclusion    String,
    status        String,
    run_url       String
) ENGINE = MergeTree()
ORDER BY (upstream_repo, commit_sha, device)
""".strip()


class CHCliFactory:
    """Thread-safe ClickHouse client singleton, one client per thread."""

    _lock = threading.Lock()
    _table_ensured = False

    @classmethod
    def setup_client(
        cls,
        url: str,
        username: str,
        password: str,
        database: str = "default",
    ) -> None:
        """Configure connection parameters. Must be called once before first use."""
        parsed = urlparse(url)
        cls._host = parsed.hostname or "localhost"
        cls._port = parsed.port or 8123
        cls._secure = parsed.scheme in ("https", "clickhouses")
        cls._username = username
        cls._password = password
        cls._database = database
        cls._table_ensured = False  # reset so DDL re-runs if reconfigured
        logger.debug(
            "CHCliFactory configured host=%s port=%s database=%s secure=%s",
            cls._host, cls._port, cls._database, cls._secure,
        )

    def __new__(cls):
        if not hasattr(cls, "_instance"):
            with cls._lock:
                if not hasattr(cls, "_instance"):
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        tlocal = threading.local()
        if not hasattr(tlocal, "_CHCliFactory_data"):
            tlocal._CHCliFactory_data = {}
        self._data = tlocal._CHCliFactory_data

    @property
    def client(self) -> clickhouse_connect.driver.Client:
        if "client" not in self._data:
            for attr in ("_host", "_port", "_username", "_password", "_database"):
                if not hasattr(self.__class__, attr):
                    raise RuntimeError(
                        "ClickHouse client not configured. Call CHCliFactory.setup_client() first."
                    )
            self._data["client"] = clickhouse_connect.get_client(
                host=self.__class__._host,
                port=self.__class__._port,
                username=self.__class__._username,
                password=self.__class__._password,
                database=self.__class__._database,
                secure=self.__class__._secure,
            )
            logger.debug("ClickHouse client created for thread")
        return self._data["client"]

    def ensure_table(self) -> None:
        """Create oot_ci_results if it does not exist (idempotent, runs once per process)."""
        if self.__class__._table_ensured:
            return
        with self.__class__._lock:
            if self.__class__._table_ensured:
                return
            self.client.command(_OOT_CI_RESULTS_DDL)
            self.__class__._table_ensured = True
            logger.info("ClickHouse table oot_ci_results ensured")

    def write_ci_result(
        self,
        *,
        device: str,
        upstream_repo: str,
        commit_sha: str,
        workflow_name: str,
        status: str,
        conclusion: str,
        run_url: str,
    ) -> None:
        """Insert one result row into oot_ci_results."""
        logger.debug(
            "CH write device=%s upstream_repo=%s commit_sha=%.12s workflow=%s conclusion=%s status=%s",
            device, upstream_repo, commit_sha, workflow_name, conclusion, status,
        )
        self.client.insert(
            "oot_ci_results",
            [[device, upstream_repo, commit_sha, workflow_name, conclusion, status, run_url]],
            column_names=["device", "upstream_repo", "commit_sha", "workflow_name",
                          "conclusion", "status", "run_url"],
        )
