"""Fail-closed, development-only installer for the property catalog schema.

This module intentionally does not use the general schema runner. It verifies
the pinned bytes and six executable CREATE statements from the clean pre-release
025-027 migrations, then creates those six tables in an empty isolated DEV
database. It never upgrades, alters, or drops an existing table.

The caller supplies a tiny client adapter so importing or testing this module
does not require Django or a ClickHouse driver.  ``database=`` is execution
context: implementations must not rewrite the SQL passed to ``command``.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tracer.services.clickhouse.v2.property_catalog.database import (
    configured_production_property_catalog_database,
)

DEVELOPMENT_SENTINEL = "PROPERTY_CATALOG_DEV_ONLY"
_TARGET_DATABASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED_TARGET_DATABASES = frozenset(
    {
        "default",
        "futureagi",
        "information_schema",
        "system",
    }
)


class CatalogDevSchemaError(RuntimeError):
    """Raised before, during, or after a deployment that cannot be proven safe."""


class CatalogDevClickHouseClient(Protocol):
    """Minimum ClickHouse surface needed by :func:`apply_catalog_dev_schema`.

    ``database`` selects the database context for an otherwise unchanged SQL
    statement.  Snapshot and server metadata queries use the default context;
    the six catalog DDL statements use the new target database context.
    """

    def query_rows(
        self, sql: str, *, database: str | None = None
    ) -> Sequence[Sequence[object]]: ...

    def command(self, sql: str, *, database: str | None = None) -> None: ...


class ClickHouseHttpClient:
    """Small HTTP adapter restricted to a loopback/SSH-forwarded endpoint."""

    _MAX_RESPONSE_BYTES = 64 * 1024 * 1024

    def __init__(
        self,
        endpoint: str,
        *,
        username: str = "default",
        password: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"}:
            raise CatalogDevSchemaError("ClickHouse endpoint must use http or https")
        if parsed.username is not None or parsed.password is not None:
            raise CatalogDevSchemaError(
                "credentials must not be embedded in the ClickHouse endpoint"
            )
        if parsed.query or parsed.fragment:
            raise CatalogDevSchemaError(
                "ClickHouse endpoint must not contain a query or fragment"
            )
        if parsed.path not in {"", "/"}:
            raise CatalogDevSchemaError(
                "ClickHouse endpoint must not contain a non-root path"
            )
        if not _is_loopback_host(parsed.hostname):
            raise CatalogDevSchemaError(
                "catalog dev HTTP client accepts only a loopback endpoint; "
                "use an SSH forward for a development server"
            )
        if not username or any(character in username for character in "\r\n"):
            raise CatalogDevSchemaError("invalid ClickHouse username")
        if any(character in password for character in "\r\n"):
            raise CatalogDevSchemaError("invalid ClickHouse password")
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise CatalogDevSchemaError(
                "ClickHouse HTTP timeout must be greater than zero and at most 300s"
            )

        self._endpoint = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path or "/", "", "")
        )
        self._username = username
        self._password = password
        self._timeout_seconds = timeout_seconds

    def query_rows(
        self, sql: str, *, database: str | None = None
    ) -> Sequence[Sequence[object]]:
        response = self._post(sql.rstrip(";\n") + "\nFORMAT JSONCompact", database)
        try:
            document = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CatalogDevSchemaError(
                "ClickHouse returned invalid JSONCompact data"
            ) from exc
        if not isinstance(document, dict) or not isinstance(document.get("data"), list):
            raise CatalogDevSchemaError(
                "ClickHouse JSONCompact response is missing a data array"
            )
        rows = document["data"]
        if not all(isinstance(row, list) for row in rows):
            raise CatalogDevSchemaError(
                "ClickHouse JSONCompact response contains a non-array row"
            )
        return rows

    def command(self, sql: str, *, database: str | None = None) -> None:
        self._post(sql, database)

    def _post(self, sql: str, database: str | None) -> bytes:
        query = urllib.parse.urlencode({"database": database}) if database else ""
        endpoint = urllib.parse.urlunsplit(
            (*urllib.parse.urlsplit(self._endpoint)[:3], query, "")
        )
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "X-ClickHouse-User": self._username,
        }
        if self._password:
            headers["X-ClickHouse-Key"] = self._password
        request = urllib.request.Request(
            endpoint,
            data=sql.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - loopback-only URL is validated
                request, timeout=self._timeout_seconds
            ) as response:
                body = response.read(self._MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace").strip()
            raise CatalogDevSchemaError(
                f"ClickHouse HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise CatalogDevSchemaError(
                f"ClickHouse HTTP request failed: {exc.reason}"
            ) from exc
        if len(body) > self._MAX_RESPONSE_BYTES:
            raise CatalogDevSchemaError("ClickHouse HTTP response exceeded 64 MiB")
        return body


@dataclass(frozen=True, order=True)
class TableSnapshot:
    database: str
    name: str
    engine: str
    create_table_query: str

    def as_dict(self) -> dict[str, str]:
        return {
            "database": self.database,
            "name": self.name,
            "engine": self.engine,
            "create_table_query": self.create_table_query,
        }


@dataclass(frozen=True)
class _PinnedMigration:
    filename: str
    file_sha256: str
    statement_sha256s: tuple[str, ...]


@dataclass(frozen=True)
class _CatalogTable:
    migration: str
    name: str
    engine: str


@dataclass(frozen=True)
class _LoadedStatement:
    migration: str
    ordinal: int
    sha256: str
    sql: str
    table: str
    engine: str


_SCHEMA_DIR = Path(__file__).with_name("schema")

# File and statement hashes deliberately make edits to any migration a hard
# stop.  Updating these values is an explicit review event, not schema discovery.
_PINNED_MIGRATIONS = (
    _PinnedMigration(
        filename="025_property_catalog_data.sql",
        file_sha256="2cc25d270f34a654b46855dd23f1362e854242cda93a465ddab6f3810bab3437",
        statement_sha256s=(
            "bf6b23322baeb159704fe2d2320262e18ad838d37a4ebf76d424217bf710e482",
            "9f7a24591b1c8f4563af2c18ca13331c13ae8bd2314af5ed81b67724989ef55d",
        ),
    ),
    _PinnedMigration(
        filename="026_property_catalog_state.sql",
        file_sha256="5b54ce0ccff8c5ee4a2bb8f391be142933740f86d73d5a0b14af866feb96d7e6",
        statement_sha256s=(
            "567c700a7df0767722500dcba840418693b96246658f634347d51fdadd142727",
            "5dde9acefbb8243828285b7e156d437fd561555ba5a2d9bdf45bb14baf77177d",
        ),
    ),
    _PinnedMigration(
        filename="027_property_catalog_delivery.sql",
        file_sha256="f3591e491d6a0a0f733b6aada56f02c0956b8f2524dded2b459211e40f8b85d2",
        statement_sha256s=(
            "92e85ee19c3b08179fe29cd2d58aeb5e33c2977a6cd8b0f696577e6deb83461a",
            "43629fc7baa3d0c3541e55d7e2c72a340cd547462abfee7fe14ba1f52cd56a59",
        ),
    ),
)

_EXPECTED_TABLES = (
    _CatalogTable(
        "025_property_catalog_data.sql",
        "property_definition_catalog",
        "MergeTree",
    ),
    _CatalogTable(
        "025_property_catalog_data.sql",
        "span_attribute_value_catalog",
        "AggregatingMergeTree",
    ),
    _CatalogTable(
        "026_property_catalog_state.sql",
        "property_catalog_checkpoints",
        "ReplacingMergeTree",
    ),
    _CatalogTable(
        "026_property_catalog_state.sql",
        "property_catalog_activations",
        "ReplacingMergeTree",
    ),
    _CatalogTable(
        "027_property_catalog_delivery.sql",
        "property_catalog_deliveries",
        "MergeTree",
    ),
    _CatalogTable(
        "027_property_catalog_delivery.sql",
        "property_catalog_source_streams",
        "ReplacingMergeTree",
    ),
)

_VERSION_SQL = "SELECT version()"
_TABLE_SNAPSHOT_SQL = """\
SELECT database, name, engine, create_table_query
FROM system.tables
ORDER BY database, name
"""
_CREATE_TABLE_RE = re.compile(
    r"\ACREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_ENGINE_RE = re.compile(r"\bENGINE\s*=\s*([A-Za-z][A-Za-z0-9]*)", re.IGNORECASE)
_FORBIDDEN_SQL = (
    ("ALTER", re.compile(r"\bALTER\b", re.IGNORECASE)),
    ("DROP", re.compile(r"\bDROP\b", re.IGNORECASE)),
    ("INSERT", re.compile(r"\bINSERT\b", re.IGNORECASE)),
    (
        "materialized view",
        re.compile(r"\bMATERIALIZED\s+VIEW\b", re.IGNORECASE),
    ),
    (
        "FROM spans",
        re.compile(
            r"\bFROM\s+(?:(?:`[^`]+`|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)\s*\.\s*)?"
            r"(?:`spans`|\"spans\"|spans)(?![A-Za-z0-9_])",
            re.IGNORECASE,
        ),
    ),
)


def _is_loopback_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _split_executable_statements(source: str) -> list[str]:
    """Return executable statements while preserving every executable byte.

    The three pinned migrations contain only whole-line ``--`` comments. Reject
    inline comments so a future edit cannot change how this deliberately small
    parser interprets SQL.
    """

    executable_lines: list[str] = []
    for line_number, line in enumerate(source.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("--"):
            continue
        if "--" in line:
            raise CatalogDevSchemaError(
                f"inline SQL comment is not allowed at line {line_number}"
            )
        executable_lines.append(line)

    executable = "\n".join(executable_lines)
    parts = executable.split(";\n")
    statements: list[str] = []
    for part in parts:
        statement = part.strip()
        if not statement:
            continue
        if not statement.endswith(";"):
            statement += ";"
        if statement.count(";") != 1:
            raise CatalogDevSchemaError(
                "pinned catalog SQL must contain one terminal semicolon per statement"
            )
        statements.append(statement)
    return statements


def _load_pinned_statements() -> tuple[_LoadedStatement, ...]:
    loaded_sql: list[tuple[str, int, str, str]] = []
    for migration in _PINNED_MIGRATIONS:
        path = _SCHEMA_DIR / migration.filename
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CatalogDevSchemaError(
                f"cannot read pinned migration {migration.filename}: {exc}"
            ) from exc

        actual_file_sha256 = _sha256(raw)
        if actual_file_sha256 != migration.file_sha256:
            raise CatalogDevSchemaError(
                f"pinned migration drift for {migration.filename}: "
                f"expected {migration.file_sha256}, got {actual_file_sha256}"
            )
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CatalogDevSchemaError(
                f"pinned migration {migration.filename} is not UTF-8"
            ) from exc

        statements = _split_executable_statements(source)
        if len(statements) != len(migration.statement_sha256s):
            raise CatalogDevSchemaError(
                f"{migration.filename} must contain exactly "
                f"{len(migration.statement_sha256s)} pinned statements"
            )
        for ordinal, (statement, expected_sha256) in enumerate(
            zip(statements, migration.statement_sha256s, strict=True), 1
        ):
            actual_sha256 = _sha256(statement.encode("utf-8"))
            if actual_sha256 != expected_sha256:
                raise CatalogDevSchemaError(
                    f"pinned statement drift in {migration.filename}#{ordinal}: "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )
            loaded_sql.append((migration.filename, ordinal, actual_sha256, statement))

    if len(loaded_sql) != len(_EXPECTED_TABLES):
        raise CatalogDevSchemaError(
            "catalog harness must load exactly six CREATE statements"
        )

    loaded: list[_LoadedStatement] = []
    for raw_statement, expected in zip(loaded_sql, _EXPECTED_TABLES, strict=True):
        migration, ordinal, statement_sha256, statement = raw_statement
        if migration != expected.migration:
            raise CatalogDevSchemaError("pinned catalog migration order changed")

        create_matches = _CREATE_TABLE_RE.findall(statement)
        if (
            len(create_matches) != 1
            or len(re.findall(r"\bCREATE\b", statement, re.IGNORECASE)) != 1
        ):
            raise CatalogDevSchemaError(
                f"{migration}#{ordinal} is not exactly one CREATE TABLE IF NOT EXISTS"
            )
        table = create_matches[0]
        engine_matches = _ENGINE_RE.findall(statement)
        if len(engine_matches) != 1:
            raise CatalogDevSchemaError(
                f"{migration}#{ordinal} must declare exactly one table engine"
            )
        engine = engine_matches[0]
        if table != expected.name or engine != expected.engine:
            raise CatalogDevSchemaError(
                f"unexpected catalog table contract in {migration}#{ordinal}: "
                f"{table}/{engine}"
            )
        for label, pattern in _FORBIDDEN_SQL:
            if pattern.search(statement):
                raise CatalogDevSchemaError(
                    f"forbidden {label} SQL in {migration}#{ordinal}"
                )
        loaded.append(
            _LoadedStatement(
                migration=migration,
                ordinal=ordinal,
                sha256=statement_sha256,
                sql=statement,
                table=table,
                engine=engine,
            )
        )
    return tuple(loaded)


def _text(value: object, *, field: str) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CatalogDevSchemaError(f"{field} is not UTF-8") from exc
    if isinstance(value, str):
        return value
    raise CatalogDevSchemaError(f"{field} must be returned as text")


def _server_version(client: CatalogDevClickHouseClient) -> str:
    rows = list(client.query_rows(_VERSION_SQL))
    if len(rows) != 1 or len(rows[0]) != 1:
        raise CatalogDevSchemaError("SELECT version() must return exactly one value")
    version = _text(rows[0][0], field="ClickHouse version")
    if re.fullmatch(r"25\.3(?:\.[0-9]+){0,2}", version) is None:
        raise CatalogDevSchemaError(
            f"ClickHouse 25.3 is required; server reported {version!r}"
        )
    return version


def _snapshot_tables(
    client: CatalogDevClickHouseClient,
) -> tuple[TableSnapshot, ...]:
    snapshots: list[TableSnapshot] = []
    for row_number, row in enumerate(client.query_rows(_TABLE_SNAPSHOT_SQL), 1):
        if len(row) != 4:
            raise CatalogDevSchemaError(
                f"system.tables snapshot row {row_number} must have four fields"
            )
        snapshots.append(
            TableSnapshot(
                database=_text(row[0], field="system.tables.database"),
                name=_text(row[1], field="system.tables.name"),
                engine=_text(row[2], field="system.tables.engine"),
                create_table_query=_text(
                    row[3], field="system.tables.create_table_query"
                ),
            )
        )
    snapshots.sort()
    identities = [(table.database, table.name) for table in snapshots]
    if len(identities) != len(set(identities)):
        raise CatalogDevSchemaError("system.tables returned duplicate table identities")
    return tuple(snapshots)


def _database_exists(client: CatalogDevClickHouseClient, target_database: str) -> bool:
    sql = (
        "SELECT name FROM system.databases "
        f"WHERE name = '{target_database}' ORDER BY name"
    )
    rows = list(client.query_rows(sql))
    if len(rows) > 1:
        raise CatalogDevSchemaError("system.databases returned duplicate databases")
    if not rows:
        return False
    if len(rows[0]) != 1:
        raise CatalogDevSchemaError("system.databases query must return one field")
    returned_name = _text(rows[0][0], field="system.databases.name")
    if returned_name != target_database:
        raise CatalogDevSchemaError(
            "system.databases returned a database other than the requested target"
        )
    return True


def _validate_target_database(target_database: str) -> None:
    if (
        not isinstance(target_database, str)
        or len(target_database) > 128
        or _TARGET_DATABASE_RE.fullmatch(target_database) is None
        or target_database in _RESERVED_TARGET_DATABASES
        or target_database == configured_production_property_catalog_database()
    ):
        raise CatalogDevSchemaError(
            "target database must be a safe lowercase ClickHouse identifier "
            "isolated from production and source databases"
        )


def _snapshot_digest(tables: Sequence[TableSnapshot]) -> str:
    encoded = json.dumps(
        [table.as_dict() for table in tables],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(encoded)


def _validate_exact_catalog_tables(tables: Sequence[TableSnapshot]) -> None:
    actual_engines = {table.name: table.engine for table in tables}
    expected_engines = {table.name: table.engine for table in _EXPECTED_TABLES}
    if len(tables) != len(_EXPECTED_TABLES) or actual_engines != expected_engines:
        raise CatalogDevSchemaError(
            "target database does not contain exactly the six expected catalog "
            f"tables/engines: expected {expected_engines}, got {actual_engines}"
        )


def _normalized_create_query(value: str) -> str:
    return " ".join(value.replace("`", "").replace('"', "").lower().split())


def _create_query_tokens(value: str) -> tuple[str, ...]:
    """Tokenize one CREATE query without weakening its schema semantics.

    ClickHouse's ``system.tables.create_table_query`` removes ``IF NOT EXISTS``,
    qualifies the table with its database, quotes identifiers, and reformats
    whitespace.  Those presentation changes must not make a pinned schema look
    different.  Column order/types, Enum labels, indexes, engines, keys, and
    settings remain exact tokens and therefore cannot drift unnoticed.
    """

    tokens: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace():
            index += 1
            continue
        if character == "'":
            start = index
            index += 1
            while index < len(value):
                if value[index] == "\\":
                    index += 2
                    continue
                if value[index] == "'":
                    if index + 1 < len(value) and value[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise CatalogDevSchemaError(
                    "pinned schema comparison found an unterminated string literal"
                )
            tokens.append(f"string:{value[start:index]}")
            continue
        if character in {"`", '"'}:
            quote = character
            index += 1
            identifier: list[str] = []
            while index < len(value):
                if value[index] == quote:
                    if index + 1 < len(value) and value[index + 1] == quote:
                        identifier.append(quote)
                        index += 2
                        continue
                    index += 1
                    break
                identifier.append(value[index])
                index += 1
            else:
                raise CatalogDevSchemaError(
                    "pinned schema comparison found an unterminated quoted identifier"
                )
            identifier_text = "".join(identifier)
            if re.fullmatch(r"[a-z_][a-z0-9_]*", identifier_text):
                tokens.append(f"word:{identifier_text}")
            else:
                # Preserve non-canonical spelling so a case-changed or otherwise
                # different identifier cannot compare equal to a pinned name.
                tokens.append(f"quoted_identifier:{identifier_text}")
            continue
        if character.isalpha() or character == "_":
            start = index
            index += 1
            while index < len(value) and (
                value[index].isalnum() or value[index] == "_"
            ):
                index += 1
            tokens.append(f"word:{value[start:index].lower()}")
            continue
        if character.isdigit():
            start = index
            index += 1
            while index < len(value) and value[index].isdigit():
                index += 1
            tokens.append(f"number:{value[start:index]}")
            continue
        if character == ";":
            tokens.append("punct:;")
            index += 1
            continue
        if character in "(),.=%+-*/":
            tokens.append(f"punct:{character}")
            index += 1
            continue
        raise CatalogDevSchemaError(
            f"pinned schema comparison found unsupported SQL character {character!r}"
        )
    if tokens and tokens[-1] == "punct:;":
        tokens.pop()
    if "punct:;" in tokens:
        raise CatalogDevSchemaError(
            "pinned schema comparison accepts exactly one CREATE statement"
        )
    return tuple(tokens)


def _canonical_create_query(value: str, *, expected_table: str) -> tuple[str, ...]:
    tokens = list(_create_query_tokens(value))
    if tokens[:2] != ["word:create", "word:table"]:
        raise CatalogDevSchemaError(
            f"target {expected_table} does not expose one CREATE TABLE query"
        )
    position = 2
    if tokens[position : position + 3] == [
        "word:if",
        "word:not",
        "word:exists",
    ]:
        position += 3
    if position >= len(tokens):
        raise CatalogDevSchemaError(
            f"target {expected_table} CREATE query is missing its table name"
        )
    table_token = tokens[position]
    position += 1
    if position < len(tokens) and tokens[position] == "punct:.":
        # The first identifier was the database. Database identity is already
        # proved independently by the system.tables snapshot.
        position += 1
        if position >= len(tokens):
            raise CatalogDevSchemaError(
                f"target {expected_table} CREATE query has an invalid qualifier"
            )
        table_token = tokens[position]
        position += 1
    expected_token = f"word:{expected_table}"
    if table_token != expected_token:
        raise CatalogDevSchemaError(
            f"target {expected_table} CREATE query names another table"
        )
    return ("word:create", "word:table", expected_token, *tokens[position:])


def _validate_pinned_create_queries(
    tables: Sequence[TableSnapshot],
    statements: Sequence[_LoadedStatement],
) -> str:
    expected = {
        statement.table: _canonical_create_query(
            statement.sql, expected_table=statement.table
        )
        for statement in statements
    }
    actual = {
        table.name: _canonical_create_query(
            table.create_table_query, expected_table=table.name
        )
        for table in tables
    }
    if actual != expected:
        drifted = sorted(
            name
            for name in set(expected) | set(actual)
            if expected.get(name) != actual.get(name)
        )
        raise CatalogDevSchemaError(
            "target catalog CREATE definitions differ from the six pinned statements: "
            + ", ".join(drifted)
        )
    encoded = json.dumps(
        [[name, list(expected[name])] for name in sorted(expected)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(encoded)


def _validate_unified_property_schema(tables: Sequence[TableSnapshot]) -> None:
    by_name = {
        table.name: _normalized_create_query(table.create_table_query)
        for table in tables
    }
    definition_query = by_name.get("property_definition_catalog", "")
    required_definition_fragments = (
        "organization_id uuid",
        "workspace_id uuid",
        "catalog_revision uint64",
        "binding_id fixedstring(64)",
        "source_version uint64",
        "property_id string",
        "sort_name_folded string",
        "search_text_folded string",
        "is_deleted uint8",
        "state_sha256 fixedstring(64)",
        "engine = mergetree",
    )
    if any(
        fragment not in definition_query for fragment in required_definition_fragments
    ):
        raise CatalogDevSchemaError(
            "target property_definition_catalog does not expose the pinned "
            "versioned definition contract"
        )

    value_query = by_name.get("span_attribute_value_catalog", "")
    required_value_fragments = (
        "organization_id uuid",
        "workspace_id uuid",
        "project_id uuid",
        "source_kind enum8",
        "'custom_attribute' = 1",
        "'system_attribute' = 2",
        "value_fingerprint fixedstring(64)",
        "engine = aggregatingmergetree",
    )
    if any(fragment not in value_query for fragment in required_value_fragments):
        raise CatalogDevSchemaError(
            "target span_attribute_value_catalog does not expose the pinned "
            "tenant-scoped value contract"
        )

    for table_name in (
        "property_catalog_checkpoints",
        "property_catalog_activations",
        "property_catalog_deliveries",
        "property_catalog_source_streams",
    ):
        query = by_name.get(table_name, "")
        if (
            "organization_id uuid" not in query
            or "workspace_id uuid" not in query
            or "catalog_epoch uint16" not in query
            or "catalog_revision uint64" not in query
            or "projection_version uint16" not in query
        ):
            raise CatalogDevSchemaError(
                f"target table {table_name} does not expose the pinned tenant/revision contract"
            )


def apply_catalog_dev_schema(
    client: CatalogDevClickHouseClient,
    *,
    target_database: str,
    development_sentinel: str,
) -> str:
    """Create one clean, isolated DEV property catalog.

    All local validation and the ClickHouse version gate run before the first
    command. The target database may be absent or empty; every pre-existing
    target table is rejected before any DDL so this function cannot reinterpret
    or upgrade an older schema.
    """

    if development_sentinel != DEVELOPMENT_SENTINEL:
        raise CatalogDevSchemaError(
            f"explicit development sentinel {DEVELOPMENT_SENTINEL!r} is required"
        )
    _validate_target_database(target_database)
    statements = _load_pinned_statements()
    version = _server_version(client)

    before = _snapshot_tables(client)
    target_before = tuple(
        table for table in before if table.database == target_database
    )
    if target_before:
        names = ", ".join(table.name for table in target_before)
        raise CatalogDevSchemaError(
            f"target database must be empty before deployment; found: {names}"
        )

    database_existed = _database_exists(client, target_database)
    if not database_existed:
        client.command(f"CREATE DATABASE IF NOT EXISTS {target_database}")

    for statement in statements:
        client.command(statement.sql, database=target_database)

    after = _snapshot_tables(client)
    unrelated_before = tuple(
        table for table in before if table.database != target_database
    )
    unrelated_after = tuple(
        table for table in after if table.database != target_database
    )
    if unrelated_after != unrelated_before:
        raise CatalogDevSchemaError(
            "a pre-existing table changed while applying the catalog schema"
        )

    target_after = tuple(table for table in after if table.database == target_database)
    _validate_exact_catalog_tables(target_after)
    pinned_schema_sha256 = _validate_pinned_create_queries(target_after, statements)
    _validate_unified_property_schema(target_after)

    evidence = {
        "clickhouse_version": version,
        "database_created": not database_existed,
        "development_only": True,
        "pinned_migrations": [
            {
                "file_sha256": migration.file_sha256,
                "filename": migration.filename,
                "statement_count": len(migration.statement_sha256s),
            }
            for migration in _PINNED_MIGRATIONS
        ],
        "pre_existing_tables": [table.as_dict() for table in before],
        "pre_existing_tables_sha256": _snapshot_digest(before),
        "pre_existing_tables_unchanged": True,
        "post_existing_tables_excluding_target": [
            table.as_dict() for table in unrelated_after
        ],
        "post_existing_tables_excluding_target_sha256": _snapshot_digest(
            unrelated_after
        ),
        "statements_applied": [
            {
                "engine": statement.engine,
                "migration": statement.migration,
                "ordinal": statement.ordinal,
                "sha256": statement.sha256,
                "table": statement.table,
            }
            for statement in statements
        ],
        "target_database": target_database,
        "pinned_create_schema_sha256": pinned_schema_sha256,
        "target_tables": [table.as_dict() for table in target_after],
        "validated_target_table_count": 6,
    }
    return json.dumps(evidence, indent=2, sort_keys=True) + "\n"


def verify_catalog_dev_schema(
    client: CatalogDevClickHouseClient,
    *,
    target_database: str,
) -> str:
    """Read-only proof that the target already is the exact pinned six tables."""

    return verify_catalog_schema(
        client,
        target_database=target_database,
        deployment="dev",
    )


def verify_catalog_schema(
    client: CatalogDevClickHouseClient,
    *,
    target_database: str,
    deployment: str,
) -> str:
    """Read-only proof of the pinned schema in an admitted catalog namespace.

    Production is deliberately verify-only. Schema creation remains confined
    to :func:`apply_catalog_dev_schema` and an explicitly selected DEV database.
    """

    if deployment == "dev":
        _validate_target_database(target_database)
    elif deployment == "prod":
        if target_database != configured_production_property_catalog_database():
            raise CatalogDevSchemaError(
                "production catalog database does not match the configured "
                "production database"
            )
    else:
        raise CatalogDevSchemaError("catalog schema deployment must be dev or prod")
    statements = _load_pinned_statements()
    version = _server_version(client)
    snapshot = _snapshot_tables(client)
    target = tuple(table for table in snapshot if table.database == target_database)
    if not target:
        raise CatalogDevSchemaError(
            "target database does not contain the pinned catalog schema"
        )
    _validate_exact_catalog_tables(target)
    pinned_schema_sha256 = _validate_pinned_create_queries(target, statements)
    _validate_unified_property_schema(target)
    evidence = {
        "clickhouse_version": version,
        "deployment": deployment,
        "development_only": deployment == "dev",
        "schema_action": "verified_existing",
        "statements": [
            {
                "migration": statement.migration,
                "ordinal": statement.ordinal,
                "sha256": statement.sha256,
                "table": statement.table,
            }
            for statement in statements
        ],
        "target_database": target_database,
        "pinned_create_schema_sha256": pinned_schema_sha256,
        "target_tables": [table.as_dict() for table in target],
        "target_tables_sha256": _snapshot_digest(target),
        "validated_target_table_count": 6,
        "write_count": 0,
    }
    return json.dumps(evidence, indent=2, sort_keys=True) + "\n"


def ensure_catalog_dev_schema(
    client: CatalogDevClickHouseClient,
    *,
    target_database: str,
    development_sentinel: str,
) -> str:
    """Create an empty target once, otherwise prove the exact schema read-only.

    A partial, extra, or drifted target never enters the DDL path.  This makes
    explicit initial rollout replayable without treating the installer as an
    upgrader, and keeps scheduled ticks on :func:`verify_catalog_dev_schema`.
    """

    if development_sentinel != DEVELOPMENT_SENTINEL:
        raise CatalogDevSchemaError(
            f"explicit development sentinel {DEVELOPMENT_SENTINEL!r} is required"
        )
    _validate_target_database(target_database)
    _load_pinned_statements()
    _server_version(client)
    before = _snapshot_tables(client)
    target_before = tuple(
        table for table in before if table.database == target_database
    )
    if target_before:
        return verify_catalog_dev_schema(client, target_database=target_database)
    return apply_catalog_dev_schema(
        client,
        target_database=target_database,
        development_sentinel=development_sentinel,
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply only the pinned property-catalog tables to a loopback-forwarded "
            "ClickHouse 25.3 development server and print JSON evidence."
        )
    )
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:19001",
        help="loopback ClickHouse HTTP endpoint (use an SSH forward)",
    )
    parser.add_argument("--target-database", required=True)
    parser.add_argument(
        "--development-sentinel",
        required=True,
        help=f"must equal {DEVELOPMENT_SENTINEL!r}",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("PROPERTY_CATALOG_CLICKHOUSE_USER", "default"),
    )
    parser.add_argument(
        "--password-env",
        default="PROPERTY_CATALOG_CLICKHOUSE_PASSWORD",
        help="environment variable containing the ClickHouse password",
    )
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_argument_parser().parse_args(argv)
    try:
        client = ClickHouseHttpClient(
            args.endpoint,
            username=args.username,
            password=os.environ.get(args.password_env, ""),
            timeout_seconds=args.timeout_seconds,
        )
        evidence = apply_catalog_dev_schema(
            client,
            target_database=args.target_database,
            development_sentinel=args.development_sentinel,
        )
    except CatalogDevSchemaError as exc:
        sys.stderr.write(json.dumps({"error": str(exc), "ok": False}) + "\n")
        return 2
    sys.stdout.write(evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
