"""Dedicated fail-closed ClickHouse boundary for public catalog reads.

This module deliberately does not import ``get_v2_config`` or the CH25 source
query client.  Every credential and the database are supplied by the explicit
``SPAN_ATTRIBUTE_CATALOG_CH_*`` settings.  The pooled native client is locked
to the server-enforced read-only path and every executor instance owns one
shared two-second wall across all qualification and candidate queries.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from django.conf import settings as django_settings

from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.server_readonly import ensure_read_statement

CATALOG_READ_MAX_WALL_MS = 2_000
CATALOG_READ_POOL_SIZE = 4
CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS = 2.0

_DATABASE_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
_ISOLATED_DATABASE_DENYLIST = frozenset(
    {"default", "system", "information_schema", "futureagi"}
)
_CATALOG_TABLES = frozenset(
    {
        "span_attribute_catalog_activations",
        "span_attribute_catalog_source_streams",
        "span_attribute_catalog_checkpoints",
        "span_attribute_key_catalog",
        "span_attribute_value_catalog",
    }
)
_MUTATION_KEYWORDS = frozenset(
    {
        "ALTER",
        "ATTACH",
        "BACKUP",
        "CREATE",
        "DELETE",
        "DETACH",
        "DROP",
        "GRANT",
        "INSERT",
        "KILL",
        "OPTIMIZE",
        "RENAME",
        "RESTORE",
        "REVOKE",
        "SET",
        "SYSTEM",
        "TRUNCATE",
        "UPDATE",
        "USE",
    }
)
_FROM_TERMINATORS = frozenset(
    {
        "ARRAY",
        "FINAL",
        "PREWHERE",
        "WHERE",
        "GROUP",
        "HAVING",
        "WINDOW",
        "QUALIFY",
        "ORDER",
        "LIMIT",
        "OFFSET",
        "UNION",
        "EXCEPT",
        "INTERSECT",
        "SETTINGS",
        "FORMAT",
    }
)
_ALLOWED_SETTING_KEYS = frozenset(
    {
        "max_threads",
        "max_concurrent_queries_for_user",
        "max_bytes_to_read",
        "read_overflow_mode",
        "max_memory_usage",
        "max_bytes_before_external_group_by",
        "max_bytes_before_external_sort",
        "max_result_rows",
        "max_result_bytes",
        "result_overflow_mode",
        "timeout_overflow_mode",
        "max_execution_time",
        "readonly",
    }
)


@dataclass(frozen=True, slots=True)
class AttributeCatalogConnectionConfig:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)

    @classmethod
    def from_settings(cls, source: Any = django_settings):
        config = cls(
            host=getattr(source, "SPAN_ATTRIBUTE_CATALOG_CH_HOST", None),
            port=getattr(source, "SPAN_ATTRIBUTE_CATALOG_CH_PORT", None),
            database=getattr(source, "SPAN_ATTRIBUTE_CATALOG_CH_DATABASE", None),
            user=getattr(source, "SPAN_ATTRIBUTE_CATALOG_CH_USER", None),
            password=getattr(source, "SPAN_ATTRIBUTE_CATALOG_CH_PASSWORD", None),
        )
        config.validate(
            qualifier_database=getattr(source, "SPAN_ATTRIBUTE_CATALOG_DATABASE", None),
            source_users={
                str(
                    (getattr(source, "CLICKHOUSE_V2", {}) or {}).get("CH25_USER") or ""
                ).strip(),
                str(
                    (getattr(source, "CLICKHOUSE", {}) or {}).get("CH_USERNAME") or ""
                ).strip(),
            }
            - {""},
        )
        return config

    def validate(self, *, qualifier_database: Any, source_users: set[str]) -> None:
        if (
            not isinstance(self.host, str)
            or not self.host.strip()
            or type(self.port) is not int
            or not 1 <= self.port <= 65_535
            or not isinstance(self.user, str)
            or not self.user.strip()
            or not isinstance(self.password, str)
            or not self.password
        ):
            raise ValueError(
                "complete dedicated attribute catalog ClickHouse settings are required"
            )
        if (
            not isinstance(self.database, str)
            or not _DATABASE_RE.fullmatch(self.database)
            or len(self.database.encode("utf-8")) > 128
            or "dev" not in self.database.lower()
            or self.database.lower() in _ISOLATED_DATABASE_DENYLIST
        ):
            raise ValueError(
                "attribute catalog database must be an isolated development identifier"
            )
        if qualifier_database != self.database:
            raise ValueError(
                "attribute catalog qualifier and connection databases must match"
            )
        if self.user in source_users:
            raise ValueError(
                "attribute catalog reads require a dedicated identity distinct "
                "from source application users"
            )


@dataclass(frozen=True, slots=True)
class AttributeCatalogQueryPage:
    data: list[dict[str, Any]]
    query_time_ms: float
    read_rows: int | None = None
    read_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class _SqlToken:
    text: str
    depth: int

    @property
    def upper(self) -> str:
        return self.text.upper()


def _sql_tokens(sql: str) -> tuple[_SqlToken, ...]:
    """Tokenize identifiers/punctuation while excluding literals/comments."""

    tokens: list[_SqlToken] = []
    index = 0
    depth = 0
    while index < len(sql):
        char = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if char.isspace():
            index += 1
            continue
        if char == "-" and following == "-":
            newline = sql.find("\n", index + 2)
            index = len(sql) if newline < 0 else newline + 1
            continue
        if char == "/" and following == "*":
            end = sql.find("*/", index + 2)
            if end < 0:
                raise ValueError("unterminated catalog SQL comment")
            index = end + 2
            continue
        if char == "#":
            newline = sql.find("\n", index + 1)
            index = len(sql) if newline < 0 else newline + 1
            continue
        if char == "'":
            quote = char
            index += 1
            while index < len(sql):
                if sql[index] == "\\":
                    index += 2
                    continue
                if sql[index] == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise ValueError("unterminated catalog SQL literal")
            continue
        if char in {'"', "`"}:
            quote = char
            index += 1
            identifier: list[str] = []
            while index < len(sql):
                if sql[index] == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        identifier.append(quote)
                        index += 2
                        continue
                    index += 1
                    break
                identifier.append(sql[index])
                index += 1
            else:
                raise ValueError("unterminated catalog SQL identifier")
            tokens.append(_SqlToken("".join(identifier), depth))
            continue
        if char == "(":
            tokens.append(_SqlToken(char, depth))
            depth += 1
            index += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            tokens.append(_SqlToken(char, depth))
            index += 1
            continue
        if char in {".", ",", ";"}:
            tokens.append(_SqlToken(char, depth))
            index += 1
            continue
        if char.isalpha() or char == "_":
            end = index + 1
            while end < len(sql) and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            tokens.append(_SqlToken(sql[index:end], depth))
            index = end
            continue
        index += 1
    return tuple(tokens)


def _is_identifier(token: _SqlToken) -> bool:
    return bool(_DATABASE_RE.fullmatch(token.text))


def _validate_catalog_query(
    query: str,
    *,
    database: str,
    allowed_tables: frozenset[str] = _CATALOG_TABLES,
) -> None:
    if not isinstance(query, str):
        raise TypeError("catalog query must be SQL text")
    ensure_read_statement(query)
    tokens = _sql_tokens(query)
    words = [token for token in tokens if _is_identifier(token)]
    if not words or words[0].upper not in {"SELECT", "WITH"}:
        raise RuntimeError("catalog queries must start with SELECT or WITH")
    if any(token.upper in _MUTATION_KEYWORDS for token in words):
        raise RuntimeError("catalog queries cannot contain mutation statements")

    cte_names = {
        tokens[index].text
        for index in range(len(tokens) - 2)
        if _is_identifier(tokens[index])
        and tokens[index + 1].upper == "AS"
        and tokens[index + 2].text == "("
    }
    physical_tables = 0

    def validate_reference(index: int) -> int:
        nonlocal physical_tables
        if index >= len(tokens) or tokens[index].text == "(":
            return index
        if not _is_identifier(tokens[index]):
            raise RuntimeError("catalog query contains an unsupported table source")
        first = tokens[index].text
        end = index + 1
        if end < len(tokens) and tokens[end].text == ".":
            if end + 1 >= len(tokens) or not _is_identifier(tokens[end + 1]):
                raise RuntimeError("catalog query contains an invalid table source")
            table = tokens[end + 1].text
            end += 2
            if first != database or table not in allowed_tables:
                raise RuntimeError("catalog query may read only catalog tables")
            physical_tables += 1
        elif first not in cte_names:
            raise RuntimeError(
                "catalog physical tables must use the dedicated database qualifier"
            )
        if end < len(tokens) and tokens[end].text == "(":
            raise RuntimeError("catalog query table functions are not allowed")
        return end

    for index, token in enumerate(tokens):
        if token.upper not in {"FROM", "JOIN"}:
            continue
        next_index = index + 1
        if next_index < len(tokens) and tokens[next_index].upper == "GLOBAL":
            next_index += 1
        validate_reference(next_index)

        # Reject legacy comma joins too. At the FROM clause's own nesting level,
        # a comma starts another physical/CTE source until the next SQL clause.
        if token.upper != "FROM":
            continue
        scan = next_index
        while scan < len(tokens):
            candidate = tokens[scan]
            if candidate.depth < token.depth:
                break
            if candidate.depth == token.depth and candidate.upper in _FROM_TERMINATORS:
                break
            if candidate.depth == token.depth and candidate.text == ",":
                validate_reference(scan + 1)
            scan += 1

    if physical_tables < 1:
        raise RuntimeError("catalog query must read a dedicated catalog table")


def _bounded_query_settings(
    requested: dict[str, Any], *, timeout_ms: int
) -> dict[str, Any]:
    unknown = set(requested) - _ALLOWED_SETTING_KEYS
    if unknown:
        raise ValueError("unsupported attribute catalog query setting")
    bounded = dict(requested)
    bounded["readonly"] = 1
    bounded["max_execution_time"] = timeout_ms / 1_000
    bounded["read_overflow_mode"] = "throw"
    bounded["result_overflow_mode"] = "throw"
    bounded["timeout_overflow_mode"] = "throw"
    return bounded


_client: ClickHouseClient | None = None
_client_config: AttributeCatalogConnectionConfig | None = None
_client_lock = threading.Lock()


def get_attribute_catalog_read_client(
    config: AttributeCatalogConnectionConfig | None = None,
) -> ClickHouseClient:
    """Return the dedicated pooled client without consulting source settings."""

    global _client, _client_config
    config = config or AttributeCatalogConnectionConfig.from_settings()
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
                connect_timeout=CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS,
                send_timeout=CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS,
                receive_timeout=CATALOG_READ_TRANSPORT_TIMEOUT_SECONDS,
                pool_size=CATALOG_READ_POOL_SIZE,
            )
            _client_config = config
        return _client


def reset_attribute_catalog_read_client() -> None:
    """Close the isolated pool; intended for shutdown and config-aware tests."""

    global _client, _client_config
    with _client_lock:
        if _client is not None:
            _client.close()
        _client = None
        _client_config = None


class AttributeCatalogReadExecutor:
    """Execute allowlisted catalog reads inside one shared two-second wall."""

    def __init__(
        self,
        *,
        config: AttributeCatalogConnectionConfig | None = None,
        client_factory: Callable[
            [AttributeCatalogConnectionConfig], ClickHouseClient
        ] = get_attribute_catalog_read_client,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._config = config or AttributeCatalogConnectionConfig.from_settings()
        self._client_factory = client_factory
        self._clock = clock
        self._deadline = clock() + CATALOG_READ_MAX_WALL_MS / 1_000
        self._client: ClickHouseClient | None = None

    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> AttributeCatalogQueryPage:
        _validate_catalog_query(query, database=self._config.database)
        remaining_ms = int((self._deadline - self._clock()) * 1_000)
        if remaining_ms < 1:
            raise TimeoutError("attribute catalog read deadline exhausted")
        bounded_timeout_ms = min(
            max(int(timeout_ms), 1),
            remaining_ms,
            CATALOG_READ_MAX_WALL_MS,
        )
        query_settings = _bounded_query_settings(
            settings, timeout_ms=bounded_timeout_ms
        )
        if self._client is None:
            self._client = self._client_factory(self._config)
        started_at = self._clock()
        try:
            progress_execute = getattr(
                type(self._client), "execute_read_with_progress", None
            )
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
            # A deadline/error may leave a native socket unusable. The global
            # provider will construct a fresh isolated pool on the next read.
            if self._client_factory is get_attribute_catalog_read_client:
                reset_attribute_catalog_read_client()
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
        # The default client is a process-wide dedicated pool and is intentionally
        # retained. Injected per-request clients remain owned by their factory.
        self._client = None


__all__ = [
    "AttributeCatalogConnectionConfig",
    "AttributeCatalogQueryPage",
    "AttributeCatalogReadExecutor",
    "CATALOG_READ_MAX_WALL_MS",
    "get_attribute_catalog_read_client",
    "reset_attribute_catalog_read_client",
]
