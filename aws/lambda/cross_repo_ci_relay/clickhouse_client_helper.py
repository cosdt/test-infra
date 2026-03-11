import threading
from urllib.parse import urlparse

import clickhouse_connect


class CHCliFactory:
    """Thread-safe ClickHouse client singleton, one client per thread."""

    _lock = threading.Lock()

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
        return self._data["client"]
