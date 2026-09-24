"""Dedicated read-only ClickHouse connection for observed-span indexes.

This boundary deliberately uses a separate catalog identity/database and a
hard physical-table allowlist.  It cannot read application fact tables and it
cannot execute mutations. Current native metadata never needs this connection.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from django.conf import settings as django_settings

from tracer.services.clickhouse.application_read_policy import (
    application_read_context,
)
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.v2.attribute_catalog_connection import (
    AttributeCatalogQueryPage,
    _bounded_query_settings,
    _validate_catalog_query,
)
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS

PROPERTY_CATALOG_READ_MAX_WALL_MS = RUNTIME_LIMITS.query_wall_ms
PROPERTY_CATALOG_READ_POOL_SIZE = RUNTIME_LIMITS.read_pool_size
PROPERTY_CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS = (
    RUNTIME_LIMITS.read_transport_timeout_seconds
)

PROPERTY_CATALOG_TABLES = frozenset(
    {"observed_attribute_keys", "observed_attribute_values"}
)


def validate_property_catalog_database(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None
    ):
        raise ValueError("invalid catalog database")
    return value


@dataclass(frozen=True, slots=True)
class PropertyCatalogConnectionConfig:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)

    @classmethod
    def from_settings(cls, source: Any = django_settings):
        config = cls(
            host=getattr(source, "PROPERTY_CATALOG_CH_HOST", None),
            port=getattr(source, "PROPERTY_CATALOG_CH_PORT", None),
            database=getattr(source, "PROPERTY_CATALOG_DATABASE", None),
            user=getattr(source, "PROPERTY_CATALOG_CH_USER", None),
            password=getattr(source, "PROPERTY_CATALOG_CH_PASSWORD", ""),
        )
        config.validate()
        return config

    def validate(self, *, source_users=frozenset()):
        validate_property_catalog_database(self.database)
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("catalog host is required")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("invalid catalog port")
        if not isinstance(self.user, str) or not self.user or self.user in source_users:
            raise ValueError("dedicated catalog read user is required")
        if not isinstance(self.password, str):
            raise ValueError("invalid catalog password")


_client: ClickHouseClient | None = None
_client_config: PropertyCatalogConnectionConfig | None = None
_client_lock = threading.Lock()


def get_property_catalog_read_client(
    config: PropertyCatalogConnectionConfig | None = None,
) -> ClickHouseClient:
    global _client, _client_config
    config = config or PropertyCatalogConnectionConfig.from_settings()
    with _client_lock:
        if _client is not None and _client_config != config:
            _client.close()
            _client = None
            _client_config = None
        if _client is None:
            _client = ClickHouseClient(
                host=config.host,
                port=config.port,
                user=config.user,
                password=config.password,
                database=config.database,
                server_enforced_readonly=True,
                connect_timeout=PROPERTY_CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS,
                send_timeout=PROPERTY_CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS,
                receive_timeout=PROPERTY_CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS,
                pool_size=PROPERTY_CATALOG_READ_POOL_SIZE,
                allow_query_settings_with_server_readonly=True,
            )
            _client_config = config
        return _client


def reset_property_catalog_read_client() -> None:
    global _client, _client_config
    with _client_lock:
        if _client is not None:
            _client.close()
        _client = None
        _client_config = None


class PropertyCatalogReadExecutor:
    """Allowlisted catalog reads with one bounded public/maintenance policy."""

    def __init__(
        self,
        *,
        config: PropertyCatalogConnectionConfig | None = None,
        client_factory: Callable[
            [PropertyCatalogConnectionConfig], ClickHouseClient
        ] = get_property_catalog_read_client,
        clock: Callable[[], float] = monotonic,
        max_wall_ms: int = PROPERTY_CATALOG_READ_MAX_WALL_MS,
    ) -> None:
        if type(max_wall_ms) is not int or max_wall_ms < 1:
            raise ValueError("property catalog max_wall_ms must be a positive integer")
        self._config = config or PropertyCatalogConnectionConfig.from_settings()
        self._client_factory = client_factory
        self._clock = clock
        self._max_wall_ms = min(max_wall_ms, PROPERTY_CATALOG_READ_MAX_WALL_MS)
        self._deadline = clock() + self._max_wall_ms / 1_000
        self._client: ClickHouseClient | None = None

    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> AttributeCatalogQueryPage:
        _validate_catalog_query(
            query,
            database=self._config.database,
            allowed_tables=PROPERTY_CATALOG_TABLES,
        )
        remaining_ms = int((self._deadline - self._clock()) * 1_000)
        if remaining_ms < 1:
            raise TimeoutError("property catalog read deadline exhausted")
        bounded_timeout_ms = min(
            max(int(timeout_ms), 1),
            remaining_ms,
            self._max_wall_ms,
        )
        catalog_settings = {
            **RUNTIME_LIMITS.clickhouse_read_settings,
            "max_result_rows": RUNTIME_LIMITS.max_page_size + 1,
        }
        query_settings = _bounded_query_settings(
            {**catalog_settings, **settings}, timeout_ms=bounded_timeout_ms
        )
        for key in (
            "max_bytes_to_read",
            "max_memory_usage",
            "max_result_rows",
            "max_result_bytes",
        ):
            value = query_settings[key]
            if type(value) is not int or value < 1:
                raise ValueError(f"catalog {key} must be a positive integer")
            # Row limits belong to each query (including non-page type probes).
            # Byte and memory limits may only tighten the catalog ceilings.
            if key != "max_result_rows":
                query_settings[key] = min(value, catalog_settings[key])
        if self._client is None:
            self._client = self._client_factory(self._config)
        started_at = self._clock()
        bounded_timeout_ms = min(
            bounded_timeout_ms, int((self._deadline - started_at) * 1_000)
        )
        if bounded_timeout_ms < 1:
            raise TimeoutError("property catalog read deadline exhausted")
        query_settings["max_execution_time"] = bounded_timeout_ms / 1_000
        try:
            progress_execute = getattr(
                type(self._client), "execute_read_with_progress", None
            )
            # Catalog bounds also apply inside an outer application request.
            with application_read_context(False):
                if callable(progress_execute):
                    rows, columns, _, read_rows, read_bytes = progress_execute(
                        self._client,
                        query,
                        params,
                        timeout_ms=bounded_timeout_ms,
                        settings=query_settings,
                    )
                else:
                    rows, columns, _ = self._client.execute_read(
                        query,
                        params,
                        timeout_ms=bounded_timeout_ms,
                        settings=query_settings,
                    )
                    read_rows = None
                    read_bytes = None
        except Exception:
            if self._client_factory is get_property_catalog_read_client:
                reset_property_catalog_read_client()
            self._client = None
            raise
        names = [
            column[0] if isinstance(column, tuple) else column for column in columns
        ]
        return AttributeCatalogQueryPage(
            data=[dict(zip(names, row, strict=False)) for row in rows],
            query_time_ms=round((self._clock() - started_at) * 1_000, 2),
            read_rows=read_rows,
            read_bytes=read_bytes,
        )

    def close(self) -> None:
        self._client = None


__all__ = [
    "PROPERTY_CATALOG_READ_MAX_WALL_MS",
    "PROPERTY_CATALOG_TABLES",
    "PropertyCatalogConnectionConfig",
    "PropertyCatalogReadExecutor",
    "get_property_catalog_read_client",
    "reset_property_catalog_read_client",
    "validate_property_catalog_database",
]
