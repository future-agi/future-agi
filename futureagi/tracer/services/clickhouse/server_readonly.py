"""Transport guards for a ClickHouse profile locked at ``readonly=1``.

Such profiles reject client-side setting changes.  Query builders still emit
trusted, performance-only ``SETTINGS`` clauses for the ordinary application
role, so the isolated SOS/read-replica lane removes those clauses at the final
transport boundary and sends no connection/query settings.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

_ASSIGNMENT_AFTER_SETTINGS = re.compile(r"\s+[A-Za-z_][A-Za-z0-9_]*\s*=")
_BLOCKED_HTTP_METHODS = frozenset(
    {
        "command",
        "insert",
        "insert_df",
        "insert_df_arrow",
        "insert_arrow",
        "raw_insert",
    }
)


def _top_level_tokens(sql: str) -> Iterator[tuple[str, int, int]]:
    """Yield SQL identifier tokens outside strings/comments/subqueries."""

    index = 0
    depth = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        following = sql[index + 1] if index + 1 < length else ""

        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            while index < length:
                if sql[index] == "\\":
                    index += 2
                    continue
                if sql[index] == quote:
                    if index + 1 < length and sql[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if char == "-" and following == "-":
            newline = sql.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if char == "/" and following == "*":
            end = sql.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if char == "#":
            newline = sql.find("\n", index + 1)
            index = length if newline < 0 else newline + 1
            continue
        if char == "(":
            depth += 1
            index += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            index += 1
            continue
        if depth == 0 and char == ";":
            yield ";", index, index + 1
            index += 1
            continue
        if depth == 0 and (char.isalpha() or char == "_"):
            end = index + 1
            while end < length and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            yield sql[index:end].upper(), index, end
            index = end
            continue
        index += 1


def without_query_settings(sql: str) -> str:
    """Remove one top-level query ``SETTINGS`` clause, preserving ``FORMAT``.

    Only a token followed by a setting assignment is considered.  Nested
    ``SETTINGS`` text, quoted literals, comments, and a selected column named
    ``settings`` remain untouched.
    """

    tokens = list(_top_level_tokens(sql))
    settings_index: int | None = None
    settings_end: int | None = None
    for token, start, end in tokens:
        if token == "SETTINGS" and _ASSIGNMENT_AFTER_SETTINGS.match(sql, end):
            settings_index = start
            settings_end = end
    if settings_index is None or settings_end is None:
        return sql

    format_index = next(
        (
            start
            for token, start, _ in tokens
            if token == "FORMAT" and start > settings_end
        ),
        None,
    )
    prefix = sql[:settings_index].rstrip()
    if format_index is not None:
        return f"{prefix}\n{sql[format_index:].lstrip()}"
    return prefix + (";" if sql.rstrip().endswith(";") else "")


_READ_STATEMENTS = frozenset(
    {"SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE", "DESC", "EXISTS"}
)
_MUTATION_STATEMENTS = frozenset(
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


def ensure_read_statement(sql: str) -> None:
    """Allow one top-level read statement and reject mutation disguises."""

    tokens = [token for token, _, _ in _top_level_tokens(sql)]
    semicolons = [index for index, token in enumerate(tokens) if token == ";"]
    if len(semicolons) > 1 or (semicolons and semicolons[0] != len(tokens) - 1):
        raise RuntimeError(
            "Only read statements are allowed for the server-enforced "
            "read-only ClickHouse client."
        )
    if semicolons:
        tokens.pop()

    first_token = tokens[0] if tokens else ""
    if first_token not in _READ_STATEMENTS:
        raise RuntimeError(
            "Only read statements are allowed for the server-enforced "
            "read-only ClickHouse client."
        )

    if first_token == "WITH":
        body_token = next(
            (
                token
                for token in tokens[1:]
                if token == "SELECT" or token in _MUTATION_STATEMENTS
            ),
            "",
        )
        if body_token != "SELECT":
            raise RuntimeError(
                "Only read statements are allowed for the server-enforced "
                "read-only ClickHouse client."
            )
    elif first_token == "EXPLAIN":
        target = next(
            (
                (index, token)
                for index, token in enumerate(tokens[1:], start=1)
                if token in (_READ_STATEMENTS - {"EXPLAIN"})
                or token in _MUTATION_STATEMENTS
            ),
            None,
        )
        if target is None or target[1] not in _READ_STATEMENTS:
            raise RuntimeError(
                "Only read statements are allowed for the server-enforced "
                "read-only ClickHouse client."
            )
        if target[1] == "WITH":
            body_token = next(
                (
                    token
                    for token in tokens[target[0] + 1 :]
                    if token == "SELECT" or token in _MUTATION_STATEMENTS
                ),
                "",
            )
            if body_token != "SELECT":
                raise RuntimeError(
                    "Only read statements are allowed for the server-enforced "
                    "read-only ClickHouse client."
                )


class _NativeBlockStream:
    """Compatibility adapter over the core client's managed read stream."""

    def __init__(
        self,
        client: Any,
        query: str,
        parameters: dict[str, Any],
        *,
        block_size: int = 8192,
    ):
        self._stream = client.execute_read_block_stream(
            query,
            parameters,
            block_size=block_size,
        )

    def __enter__(self):
        return self._stream.__enter__()

    def __exit__(self, exc_type, *_exc) -> None:
        self._stream.__exit__(exc_type, *_exc)


class ServerEnforcedReadOnlyNativeClient:
    """Minimal clickhouse-connect-shaped reader over settings-free native TCP.

    clickhouse-connect adds HTTP query parameters even when callers pass
    ``settings=None``.  A server profile locked at ``readonly=1`` can reject
    those parameters, so this lane deliberately avoids the HTTP transport.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str,
    ):
        # Lazy import avoids a module cycle: ClickHouseClient imports this
        # module for its final SQL guard.
        from tracer.services.clickhouse.client import ClickHouseClient

        self._client = ClickHouseClient(
            host=host,
            port=port,
            user=username,
            password=password,
            database=database,
            server_enforced_readonly=True,
        )

    def query(
        self,
        query: str,
        *,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> SimpleNamespace:
        del settings
        query = without_query_settings(query)
        ensure_read_statement(query)
        rows, _, _ = self._client.execute_read(
            query,
            parameters or {},
            settings=None,
        )
        return SimpleNamespace(result_rows=rows)

    def query_row_block_stream(
        self,
        query: str,
        *,
        parameters: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> _NativeBlockStream:
        del settings
        return _NativeBlockStream(self._client, query, parameters or {})

    def close(self) -> None:
        self._client.close()

    def __getattr__(self, name: str):
        if name in _BLOCKED_HTTP_METHODS:
            raise RuntimeError(
                "ClickHouse mutation methods are disabled for the "
                "server-enforced read-only client."
            )
        return getattr(self._client, name)


__all__ = [
    "ServerEnforcedReadOnlyNativeClient",
    "ensure_read_statement",
    "without_query_settings",
]
