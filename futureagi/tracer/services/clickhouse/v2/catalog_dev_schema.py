"""Fail-closed, development-only installer for the TH-7247 catalog schema.

This module intentionally does not use the general schema runner.  It reads
only migrations 025 and 026, verifies their pinned bytes and executable
statements, and sends those six statements unchanged to an explicitly scoped
development database.

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

DEVELOPMENT_SENTINEL = "TH7247_CATALOG_DEV_ONLY"
TARGET_DATABASE_PREFIX = "th7247_catalog_dev_"


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

# File and statement hashes deliberately make edits to either migration a hard
# stop.  Updating these values is an explicit review event, not schema discovery.
_PINNED_MIGRATIONS = (
    _PinnedMigration(
        filename="025_span_attribute_catalog.sql",
        file_sha256="69ff1ca253aac2f1e790ac1e506413eabcbec902431f85e5a8f8225d8b3f70e2",
        statement_sha256s=(
            "915ef9ce7a3bc78c0cfe21247cc45eb57192c0d30dccca574c89aac24b91e797",
            "cc6f693b258add4fe3ff2cba5da8025164b3478fe5c01f7c8e8e9413e42e8eeb",
            "2ff92f36cbb747d92b057e410352ef848e3bfba91e2b37a669d92bedcc329fd6",
            "72047aea95c024f9dbc1f17b266a4fae3f7cf0f1bc9815f150363b73cc27c42d",
        ),
    ),
    _PinnedMigration(
        filename="026_span_attribute_catalog_delivery.sql",
        file_sha256="b676ae8d9393b01ab7bd0bdff657c0cc3cde7ef7e5c8a6b642962cc539ac2d2d",
        statement_sha256s=(
            "3ef7ac3adcbafcd0adb5691bb1602f1f47340534285582c9151abde08dbc78be",
            "1be0cec66e1bee10970b450957170ee6ebffe80483e4a4cf219217ac16b110ae",
        ),
    ),
)

_EXPECTED_TABLES = (
    _CatalogTable(
        "025_span_attribute_catalog.sql",
        "span_attribute_key_catalog",
        "AggregatingMergeTree",
    ),
    _CatalogTable(
        "025_span_attribute_catalog.sql",
        "span_attribute_value_catalog",
        "AggregatingMergeTree",
    ),
    _CatalogTable(
        "025_span_attribute_catalog.sql",
        "span_attribute_catalog_checkpoints",
        "ReplacingMergeTree",
    ),
    _CatalogTable(
        "025_span_attribute_catalog.sql",
        "span_attribute_catalog_activations",
        "ReplacingMergeTree",
    ),
    _CatalogTable(
        "026_span_attribute_catalog_delivery.sql",
        "span_attribute_catalog_deliveries",
        "ReplacingMergeTree",
    ),
    _CatalogTable(
        "026_span_attribute_catalog_delivery.sql",
        "span_attribute_catalog_source_streams",
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

    The two pinned migrations contain only whole-line ``--`` comments.  Reject
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
        raise CatalogDevSchemaError("catalog harness must load exactly six statements")

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
    suffix_pattern = re.escape(TARGET_DATABASE_PREFIX) + r"[a-z0-9][a-z0-9_]*"
    if (
        len(target_database) > 128
        or re.fullmatch(suffix_pattern, target_database) is None
    ):
        raise CatalogDevSchemaError(
            f"target database must start {TARGET_DATABASE_PREFIX!r} and contain "
            "only lowercase letters, digits, and underscores after a non-empty suffix"
        )


def _snapshot_digest(tables: Sequence[TableSnapshot]) -> str:
    encoded = json.dumps(
        [table.as_dict() for table in tables],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(encoded)


def apply_catalog_dev_schema(
    client: CatalogDevClickHouseClient,
    *,
    target_database: str,
    development_sentinel: str,
) -> str:
    """Apply the six pinned catalog tables and return deterministic JSON evidence.

    All local validation and the ClickHouse version gate run before the first
    command.  The target may be absent or an existing empty database; any
    pre-existing target table is rejected so every successful result proves
    that exactly six new tables were created by this invocation.
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
    actual_engines = {table.name: table.engine for table in target_after}
    expected_engines = {table.name: table.engine for table in _EXPECTED_TABLES}
    if len(target_after) != 6 or actual_engines != expected_engines:
        raise CatalogDevSchemaError(
            "target database does not contain exactly the six expected catalog "
            f"tables/engines: expected {expected_engines}, got {actual_engines}"
        )

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
        "target_tables": [table.as_dict() for table in target_after],
        "validated_target_table_count": 6,
    }
    return json.dumps(evidence, indent=2, sort_keys=True) + "\n"


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply only the pinned TH-7247 catalog tables to a loopback-forwarded "
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
        default=os.environ.get("TH7247_CLICKHOUSE_USER", "default"),
    )
    parser.add_argument(
        "--password-env",
        default="TH7247_CLICKHOUSE_PASSWORD",
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
