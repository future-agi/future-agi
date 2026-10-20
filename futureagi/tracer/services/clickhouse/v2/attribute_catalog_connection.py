"""SELECT-only SQL and query bounds shared by observed catalog readers.

These helpers accept an explicit database and table allowlist; they neither own
credentials nor retain the retired snapshot/activation connection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tracer.services.clickhouse.server_readonly import ensure_read_statement

_DATABASE_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
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
        "SETTINGS",
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
    allowed_tables: frozenset[str],
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
