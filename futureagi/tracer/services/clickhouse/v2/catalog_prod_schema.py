"""CREATE-only production schema planner for the TH-7247 property catalog.

This module is deliberately independent from the DEV installer.  It reads the
three canonical, pinned catalog migrations as immutable input and renders a
production-only, fully-qualified, Keeper-replicated schema.  It never discovers
or rewrites arbitrary migrations and it has no import-time network behaviour.

The write surface is intentionally tiny: :func:`install_catalog_prod_schema`
can issue exactly one ``CREATE DATABASE IF NOT EXISTS`` followed by the seven
``CREATE TABLE IF NOT EXISTS`` statements in the isolated target database.  It
has no API capable of altering, deleting, renaming, attaching, detaching, or
backfilling any table.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    ReplicatedRewriteError,
    rewrite_for_replicated,
)

TARGET_DATABASE_PATTERN = r"th7247_catalog_prod_[a-z0-9_]+"
REQUIRED_SHARDS = 1
REQUIRED_REPLICAS = 3
MANIFEST_VERSION = "th7247-property-catalog-prod-schema/v2"

_TARGET_DATABASE_RE = re.compile(rf"\A{TARGET_DATABASE_PATTERN}\Z", re.ASCII)
_CLUSTER_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]*\Z", re.ASCII)
_KEEPER_PREFIX_RE = re.compile(r"\A/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z", re.ASCII)
_CANONICAL_CREATE_RE = re.compile(
    r"\ACREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"
    r"(?P<table>[A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_ENGINE_RE = re.compile(
    r"\bENGINE\s*=\s*(?P<engine>[A-Za-z][A-Za-z0-9]*)", re.IGNORECASE
)
_ON_CLUSTER_RE = re.compile(
    r"\bON\s+CLUSTER\s+(?P<cluster>'[^']+'|[A-Za-z0-9_.-]+)", re.IGNORECASE
)
_FORBIDDEN_SQL = (
    ("ALTER", re.compile(r"\bALTER\b", re.IGNORECASE)),
    ("DROP", re.compile(r"\bDROP\b", re.IGNORECASE)),
    ("TRUNCATE", re.compile(r"\bTRUNCATE\b", re.IGNORECASE)),
    ("RENAME", re.compile(r"\bRENAME\b", re.IGNORECASE)),
    ("ATTACH", re.compile(r"\bATTACH\b", re.IGNORECASE)),
    ("DETACH", re.compile(r"\bDETACH\b", re.IGNORECASE)),
    ("DELETE", re.compile(r"\bDELETE\b", re.IGNORECASE)),
    ("UPDATE", re.compile(r"\bUPDATE\b", re.IGNORECASE)),
    ("INSERT", re.compile(r"\bINSERT\b", re.IGNORECASE)),
    (
        "materialized view",
        re.compile(r"\bMATERIALIZED\s+VIEW\b", re.IGNORECASE),
    ),
)

_SCHEMA_DIR = Path(__file__).with_name("schema")
_ACTIVATION_CONTROL_TABLE = "property_catalog_activation_control_events"
_ACTIVATION_CONTROL_SOURCE = """\
CREATE TABLE IF NOT EXISTS property_catalog_activation_control_events
(
    organization_id          UUID,
    workspace_id             UUID,
    catalog_epoch            UInt16,
    projection_version       UInt16,
    control_sequence         UInt64,
    request_id               UUID,
    action                   Enum8('activate' = 1, 'disable' = 2, 'rollback' = 3),
    target_catalog_revision  UInt64,
    target_build_token       UUID,
    target_activation_sha256 FixedString(64),
    previous_control_sha256  FixedString(64),
    control_sha256           FixedString(64),
    controlled_at            DateTime64(6, 'UTC')
)
ENGINE = MergeTree
ORDER BY
(
    organization_id,
    workspace_id,
    control_sequence,
    request_id
)
SETTINGS index_granularity = 8192;"""


class CatalogProdSchemaError(RuntimeError):
    """Raised before a production schema plan can perform an unsafe action."""


class CatalogProdClickHouseClient(Protocol):
    """Minimal, injectable ClickHouse surface used by the planner.

    Metadata reads are parameterized.  Commands receive already validated,
    fully-qualified CREATE-only SQL and must execute them unchanged.
    """

    def query_rows(
        self,
        sql: str,
        *,
        parameters: Mapping[str, object] | None = None,
    ) -> Sequence[Sequence[object]]: ...

    def command(self, sql: str) -> None: ...


@dataclass(frozen=True)
class _PinnedMigration:
    filename: str
    file_sha256: str
    statement_sha256s: tuple[str, ...]


@dataclass(frozen=True)
class _TableSpec:
    migration: str
    name: str
    canonical_engine: str
    replicated_engine: str


@dataclass(frozen=True)
class _CanonicalStatement:
    migration: str
    ordinal: int
    table: str
    engine: str
    source_sha256: str
    sql: str


@dataclass(frozen=True)
class RenderedTable:
    """One fully-qualified table statement in the immutable manifest."""

    migration: str
    ordinal: int
    table: str
    engine: str
    keeper_path: str
    source_sha256: str
    rendered_sha256: str
    sql: str

    def as_dict(self) -> dict[str, object]:
        return {
            "engine": self.engine,
            "keeper_path": self.keeper_path,
            "migration": self.migration,
            "ordinal": self.ordinal,
            "rendered_sha256": self.rendered_sha256,
            "source_sha256": self.source_sha256,
            "sql": self.sql,
            "table": self.table,
        }


@dataclass(frozen=True)
class CatalogProdSchemaManifest:
    """Deterministic dry-run artifact; rendering does not contact ClickHouse."""

    target_database: str
    cluster: str
    keeper_path_prefix: str
    database_sql: str
    database_sql_sha256: str
    tables: tuple[RenderedTable, ...]
    source_migrations: tuple[tuple[str, str], ...]
    manifest_sha256: str

    @property
    def statements(self) -> tuple[str, ...]:
        return (self.database_sql, *(table.sql for table in self.tables))

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed_operations": [
                "CREATE DATABASE IF NOT EXISTS",
                "CREATE TABLE IF NOT EXISTS",
            ],
            "cluster": self.cluster,
            "database_statement": {
                "sha256": self.database_sql_sha256,
                "sql": self.database_sql,
            },
            "forbidden_operations": [
                "ALTER",
                "DROP",
                "TRUNCATE",
                "RENAME",
                "ATTACH",
                "DETACH",
                "DELETE",
                "UPDATE",
                "INSERT",
                "MATERIALIZED VIEW",
            ],
            "keeper_path_prefix": self.keeper_path_prefix,
            "manifest_sha256": self.manifest_sha256,
            "manifest_version": MANIFEST_VERSION,
            "required_topology": {
                "replicas_per_shard": REQUIRED_REPLICAS,
                "shards": REQUIRED_SHARDS,
            },
            "source_migrations": [
                {"filename": filename, "sha256": sha256}
                for filename, sha256 in self.source_migrations
            ],
            "statement_count": len(self.statements),
            "tables": [table.as_dict() for table in self.tables],
            "target_database": self.target_database,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, order=True)
class ReplicaTable:
    host: str
    database: str
    name: str
    engine: str
    create_table_query: str

    def as_dict(self) -> dict[str, str]:
        return {
            "create_table_query": self.create_table_query,
            "database": self.database,
            "engine": self.engine,
            "host": self.host,
            "name": self.name,
        }


@dataclass(frozen=True)
class _ClusterProof:
    configured_hosts: tuple[str, ...]
    reachable_hosts: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "configured_hosts": list(self.configured_hosts),
            "reachable_hosts": list(self.reachable_hosts),
            "replicas_per_shard": REQUIRED_REPLICAS,
            "shards": REQUIRED_SHARDS,
        }


@dataclass(frozen=True)
class _ObservedSchema:
    state: str
    database_hosts: tuple[str, ...]
    tables: tuple[ReplicaTable, ...]
    schema_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "database_hosts": list(self.database_hosts),
            "schema_sha256": self.schema_sha256,
            "state": self.state,
            "table_rows": [table.as_dict() for table in self.tables],
        }


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

_TABLE_SPECS = (
    _TableSpec(
        "025_property_catalog_data.sql",
        "property_definition_catalog",
        "MergeTree",
        "ReplicatedMergeTree",
    ),
    _TableSpec(
        "025_property_catalog_data.sql",
        "span_attribute_value_catalog",
        "AggregatingMergeTree",
        "ReplicatedAggregatingMergeTree",
    ),
    _TableSpec(
        "026_property_catalog_state.sql",
        "property_catalog_checkpoints",
        "ReplacingMergeTree",
        "ReplicatedReplacingMergeTree",
    ),
    _TableSpec(
        "026_property_catalog_state.sql",
        "property_catalog_activations",
        "ReplacingMergeTree",
        "ReplicatedReplacingMergeTree",
    ),
    _TableSpec(
        "027_property_catalog_delivery.sql",
        "property_catalog_deliveries",
        "MergeTree",
        "ReplicatedMergeTree",
    ),
    _TableSpec(
        "027_property_catalog_delivery.sql",
        "property_catalog_source_streams",
        "ReplacingMergeTree",
        "ReplicatedReplacingMergeTree",
    ),
)

_ACTIVATION_CONTROL_SPEC = _TableSpec(
    "generated:property-catalog-activation-control/v1",
    _ACTIVATION_CONTROL_TABLE,
    "MergeTree",
    "ReplicatedMergeTree",
)

_TOPOLOGY_SQL = """\
SELECT shard_num, replica_num, host_name
FROM system.clusters
WHERE cluster = %(cluster)s
ORDER BY shard_num, replica_num, host_name
"""
_REACHABILITY_SQL = """\
SELECT hostName()
FROM clusterAllReplicas(%(cluster)s, system.one)
ORDER BY hostName()
"""
_DATABASES_SQL = """\
SELECT hostName(), name
FROM clusterAllReplicas(%(cluster)s, system.databases)
WHERE name = %(database)s
ORDER BY hostName(), name
"""
_TABLES_SQL = """\
SELECT hostName(), database, name, engine, create_table_query
FROM clusterAllReplicas(%(cluster)s, system.tables)
WHERE database = %(database)s
ORDER BY hostName(), database, name
"""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _manifest_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _text(value: object, *, field: str) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CatalogProdSchemaError(f"{field} is not UTF-8") from exc
    if not isinstance(value, str) or not value:
        raise CatalogProdSchemaError(f"{field} must be non-empty text")
    return value


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise CatalogProdSchemaError(f"{field} must be an integer")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise CatalogProdSchemaError(f"{field} must be an integer") from exc
    return parsed


def _validate_options(
    *, target_database: str, cluster: str, keeper_path_prefix: str
) -> None:
    if _TARGET_DATABASE_RE.fullmatch(target_database) is None:
        raise CatalogProdSchemaError(
            f"target database must exactly match {TARGET_DATABASE_PATTERN!r}"
        )
    if _CLUSTER_RE.fullmatch(cluster) is None:
        raise CatalogProdSchemaError(
            "cluster must be explicit and contain only letters, digits, '.', '_', "
            "and '-'"
        )
    if _KEEPER_PREFIX_RE.fullmatch(keeper_path_prefix) is None:
        raise CatalogProdSchemaError(
            "Keeper path prefix must be an absolute path with literal safe "
            "segments, no macros, and no trailing slash"
        )


def _split_executable_statements(source: str) -> list[str]:
    executable_lines: list[str] = []
    for line_number, line in enumerate(source.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("--"):
            continue
        if "--" in line:
            raise CatalogProdSchemaError(
                f"inline SQL comment is not allowed at line {line_number}"
            )
        executable_lines.append(line)

    statements: list[str] = []
    for part in "\n".join(executable_lines).split(";\n"):
        statement = part.strip()
        if not statement:
            continue
        if not statement.endswith(";"):
            statement += ";"
        if statement.count(";") != 1:
            raise CatalogProdSchemaError(
                "canonical catalog SQL must contain one terminal semicolon per "
                "statement"
            )
        statements.append(statement)
    return statements


def _load_canonical_statements() -> tuple[_CanonicalStatement, ...]:
    raw_statements: list[tuple[str, int, str, str]] = []
    for migration in _PINNED_MIGRATIONS:
        path = _SCHEMA_DIR / migration.filename
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CatalogProdSchemaError(
                f"cannot read canonical migration {migration.filename}: {exc}"
            ) from exc
        actual_file_sha256 = _sha256_bytes(raw)
        if actual_file_sha256 != migration.file_sha256:
            raise CatalogProdSchemaError(
                f"canonical migration drift for {migration.filename}: expected "
                f"{migration.file_sha256}, got {actual_file_sha256}"
            )
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CatalogProdSchemaError(
                f"canonical migration {migration.filename} is not UTF-8"
            ) from exc
        statements = _split_executable_statements(source)
        if len(statements) != len(migration.statement_sha256s):
            raise CatalogProdSchemaError(
                f"{migration.filename} must contain exactly "
                f"{len(migration.statement_sha256s)} pinned statements"
            )
        for ordinal, (statement, expected_sha256) in enumerate(
            zip(statements, migration.statement_sha256s, strict=True), 1
        ):
            actual_sha256 = _sha256_text(statement)
            if actual_sha256 != expected_sha256:
                raise CatalogProdSchemaError(
                    f"canonical statement drift in {migration.filename}#{ordinal}: "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )
            raw_statements.append(
                (migration.filename, ordinal, actual_sha256, statement)
            )

    if len(raw_statements) != len(_TABLE_SPECS):
        raise CatalogProdSchemaError(
            "production renderer must load exactly six canonical CREATE statements"
        )

    loaded: list[_CanonicalStatement] = []
    for raw_statement, spec in zip(raw_statements, _TABLE_SPECS, strict=True):
        migration, ordinal, source_sha256, statement = raw_statement
        match = _CANONICAL_CREATE_RE.match(statement)
        engines = _ENGINE_RE.findall(statement)
        if (
            migration != spec.migration
            or match is None
            or match.group("table") != spec.name
            or engines != [spec.canonical_engine]
            or len(re.findall(r"\bCREATE\b", statement, re.IGNORECASE)) != 1
        ):
            raise CatalogProdSchemaError(
                f"unexpected canonical table contract in {migration}#{ordinal}"
            )
        _reject_forbidden_sql(statement, label=f"{migration}#{ordinal}")
        loaded.append(
            _CanonicalStatement(
                migration=migration,
                ordinal=ordinal,
                table=spec.name,
                engine=spec.canonical_engine,
                source_sha256=source_sha256,
                sql=statement,
            )
        )
    return tuple(loaded)


def _activation_control_statement() -> _CanonicalStatement:
    """Return the production-only ledger source without editing migrations."""

    match = _CANONICAL_CREATE_RE.match(_ACTIVATION_CONTROL_SOURCE)
    engines = _ENGINE_RE.findall(_ACTIVATION_CONTROL_SOURCE)
    if (
        match is None
        or match.group("table") != _ACTIVATION_CONTROL_TABLE
        or engines != ["MergeTree"]
        or _ACTIVATION_CONTROL_SOURCE.count(";") != 1
    ):
        raise CatalogProdSchemaError("activation-control source contract drift")
    _reject_forbidden_sql(
        _ACTIVATION_CONTROL_SOURCE,
        label="generated activation-control ledger",
    )
    return _CanonicalStatement(
        migration=_ACTIVATION_CONTROL_SPEC.migration,
        ordinal=1,
        table=_ACTIVATION_CONTROL_TABLE,
        engine="MergeTree",
        source_sha256=_sha256_text(_ACTIVATION_CONTROL_SOURCE),
        sql=_ACTIVATION_CONTROL_SOURCE,
    )


def _reject_forbidden_sql(sql: str, *, label: str) -> None:
    for operation, pattern in _FORBIDDEN_SQL:
        if pattern.search(sql):
            raise CatalogProdSchemaError(f"forbidden {operation} SQL in {label}")


def _render_table(
    statement: _CanonicalStatement,
    spec: _TableSpec,
    *,
    target_database: str,
    cluster: str,
    keeper_path_prefix: str,
) -> RenderedTable:
    qualified_prefix = f"CREATE TABLE IF NOT EXISTS {target_database}.{statement.table}"
    qualified, replacements = _CANONICAL_CREATE_RE.subn(
        qualified_prefix, statement.sql, count=1
    )
    if replacements != 1:
        raise CatalogProdSchemaError(
            f"could not qualify canonical table {statement.table}"
        )

    table_keeper_prefix = f"{keeper_path_prefix}/{target_database}"
    try:
        rendered = rewrite_for_replicated(
            qualified,
            table_name=statement.table,
            cluster=cluster,
            zk_prefix=table_keeper_prefix,
        )
    except ReplicatedRewriteError as exc:
        raise CatalogProdSchemaError(
            f"cannot render replicated table {statement.table}: {exc}"
        ) from exc

    keeper_path = f"{table_keeper_prefix}/{{shard}}/{statement.table}"
    _validate_rendered_table_sql(
        rendered,
        target_database=target_database,
        table=statement.table,
        cluster=cluster,
        replicated_engine=spec.replicated_engine,
        keeper_path=keeper_path,
    )
    return RenderedTable(
        migration=statement.migration,
        ordinal=statement.ordinal,
        table=statement.table,
        engine=spec.replicated_engine,
        keeper_path=keeper_path,
        source_sha256=statement.source_sha256,
        rendered_sha256=_sha256_text(rendered),
        sql=rendered,
    )


def _validate_rendered_table_sql(
    sql: str,
    *,
    target_database: str,
    table: str,
    cluster: str,
    replicated_engine: str,
    keeper_path: str,
) -> None:
    expected_prefix = f"CREATE TABLE IF NOT EXISTS {target_database}.{table}"
    if not sql.startswith(expected_prefix):
        raise CatalogProdSchemaError(
            f"rendered table {table} is not fully qualified in the isolated database"
        )
    if sql.count(";") != 1 or not sql.endswith(";"):
        raise CatalogProdSchemaError(
            f"rendered table {table} must contain exactly one CREATE statement"
        )
    if len(re.findall(r"\bCREATE\b", sql, re.IGNORECASE)) != 1:
        raise CatalogProdSchemaError(
            f"rendered table {table} must contain exactly one CREATE statement"
        )
    _reject_forbidden_sql(sql, label=f"rendered table {table}")

    cluster_matches = _ON_CLUSTER_RE.findall(sql)
    if cluster_matches != [f"'{cluster}'"]:
        raise CatalogProdSchemaError(
            f"rendered table {table} must declare the exact ON CLUSTER target once"
        )
    engines = _ENGINE_RE.findall(sql)
    if engines != [replicated_engine]:
        raise CatalogProdSchemaError(
            f"rendered table {table} must use {replicated_engine}"
        )
    expected_engine_prefix = (
        f"ENGINE = {replicated_engine}('{keeper_path}', '{{replica}}'"
    )
    if expected_engine_prefix not in sql:
        raise CatalogProdSchemaError(
            f"rendered table {table} has an unexpected Keeper path or replica macro"
        )


def render_catalog_prod_schema(
    *, target_database: str, cluster: str, keeper_path_prefix: str
) -> CatalogProdSchemaManifest:
    """Render the deterministic eight-statement CREATE-only manifest."""

    _validate_options(
        target_database=target_database,
        cluster=cluster,
        keeper_path_prefix=keeper_path_prefix,
    )
    canonical = _load_canonical_statements()
    canonical_tables = tuple(
        _render_table(
            statement,
            spec,
            target_database=target_database,
            cluster=cluster,
            keeper_path_prefix=keeper_path_prefix,
        )
        for statement, spec in zip(canonical, _TABLE_SPECS, strict=True)
    )
    control_table = _render_table(
        _activation_control_statement(),
        _ACTIVATION_CONTROL_SPEC,
        target_database=target_database,
        cluster=cluster,
        keeper_path_prefix=keeper_path_prefix,
    )
    tables = (*canonical_tables, control_table)
    database_sql = (
        f"CREATE DATABASE IF NOT EXISTS {target_database} ON CLUSTER '{cluster}';"
    )
    _validate_database_sql(
        database_sql, target_database=target_database, cluster=cluster
    )
    source_migrations = tuple(
        (migration.filename, migration.file_sha256) for migration in _PINNED_MIGRATIONS
    )
    digest_payload: dict[str, object] = {
        "cluster": cluster,
        "database_sql": database_sql,
        "keeper_path_prefix": keeper_path_prefix,
        "manifest_version": MANIFEST_VERSION,
        "required_replicas": REQUIRED_REPLICAS,
        "required_shards": REQUIRED_SHARDS,
        "source_migrations": source_migrations,
        "tables": [table.as_dict() for table in tables],
        "target_database": target_database,
    }
    return CatalogProdSchemaManifest(
        target_database=target_database,
        cluster=cluster,
        keeper_path_prefix=keeper_path_prefix,
        database_sql=database_sql,
        database_sql_sha256=_sha256_text(database_sql),
        tables=tables,
        source_migrations=source_migrations,
        manifest_sha256=_manifest_digest(digest_payload),
    )


def _validate_database_sql(sql: str, *, target_database: str, cluster: str) -> None:
    expected = (
        f"CREATE DATABASE IF NOT EXISTS {target_database} ON CLUSTER '{cluster}';"
    )
    if sql != expected:
        raise CatalogProdSchemaError("database SQL is not the exact CREATE-only form")
    _reject_forbidden_sql(sql, label="database statement")


def _query_rows(
    client: CatalogProdClickHouseClient,
    sql: str,
    *,
    parameters: Mapping[str, object],
) -> list[Sequence[object]]:
    rows = client.query_rows(sql, parameters=parameters)
    if isinstance(rows, (str, bytes)):
        raise CatalogProdSchemaError("ClickHouse metadata query returned invalid rows")
    return list(rows)


def _prove_cluster(
    client: CatalogProdClickHouseClient, *, cluster: str
) -> _ClusterProof:
    rows = _query_rows(client, _TOPOLOGY_SQL, parameters={"cluster": cluster})
    parsed: list[tuple[int, int, str]] = []
    for row_number, row in enumerate(rows, 1):
        if len(row) != 3:
            raise CatalogProdSchemaError(
                f"system.clusters row {row_number} must have three fields"
            )
        parsed.append(
            (
                _integer(row[0], field="system.clusters.shard_num"),
                _integer(row[1], field="system.clusters.replica_num"),
                _text(row[2], field="system.clusters.host_name"),
            )
        )
    expected_replica_numbers = set(range(1, REQUIRED_REPLICAS + 1))
    if (
        len(parsed) != REQUIRED_REPLICAS
        or {shard for shard, _replica, _host in parsed} != {1}
        or {replica for _shard, replica, _host in parsed} != expected_replica_numbers
        or len({host for _shard, _replica, host in parsed}) != REQUIRED_REPLICAS
    ):
        raise CatalogProdSchemaError(
            "production catalog requires exactly one shard with three distinct "
            "replicas numbered 1, 2, and 3"
        )

    reachability_rows = _query_rows(
        client, _REACHABILITY_SQL, parameters={"cluster": cluster}
    )
    reachable: list[str] = []
    for row_number, row in enumerate(reachability_rows, 1):
        if len(row) != 1:
            raise CatalogProdSchemaError(
                f"cluster reachability row {row_number} must have one field"
            )
        reachable.append(_text(row[0], field="clusterAllReplicas.hostName"))
    if len(reachable) != REQUIRED_REPLICAS or len(set(reachable)) != REQUIRED_REPLICAS:
        raise CatalogProdSchemaError(
            "clusterAllReplicas must reach exactly three distinct replicas"
        )
    return _ClusterProof(
        configured_hosts=tuple(sorted(host for _s, _r, host in parsed)),
        reachable_hosts=tuple(sorted(reachable)),
    )


def _schema_digest(tables: Sequence[ReplicaTable]) -> str:
    return _manifest_digest({"tables": [table.as_dict() for table in sorted(tables)]})


def _snapshot_target(
    client: CatalogProdClickHouseClient,
    manifest: CatalogProdSchemaManifest,
    cluster_proof: _ClusterProof,
) -> _ObservedSchema:
    parameters = {
        "cluster": manifest.cluster,
        "database": manifest.target_database,
    }
    database_rows = _query_rows(client, _DATABASES_SQL, parameters=parameters)
    database_hosts: list[str] = []
    for row_number, row in enumerate(database_rows, 1):
        if len(row) != 2:
            raise CatalogProdSchemaError(
                f"system.databases row {row_number} must have two fields"
            )
        host = _text(row[0], field="system.databases.hostName")
        database = _text(row[1], field="system.databases.name")
        if database != manifest.target_database:
            raise CatalogProdSchemaError(
                "system.databases returned a database outside the isolated target"
            )
        database_hosts.append(host)
    if len(database_hosts) != len(set(database_hosts)):
        raise CatalogProdSchemaError("system.databases returned duplicate replicas")

    reachable = set(cluster_proof.reachable_hosts)
    present = set(database_hosts)
    if present not in (set(), reachable):
        raise CatalogProdSchemaError(
            "isolated target database exists on only a subset of replicas"
        )

    table_rows = _query_rows(client, _TABLES_SQL, parameters=parameters)
    tables: list[ReplicaTable] = []
    for row_number, row in enumerate(table_rows, 1):
        if len(row) != 5:
            raise CatalogProdSchemaError(
                f"system.tables row {row_number} must have five fields"
            )
        table = ReplicaTable(
            host=_text(row[0], field="system.tables.hostName"),
            database=_text(row[1], field="system.tables.database"),
            name=_text(row[2], field="system.tables.name"),
            engine=_text(row[3], field="system.tables.engine"),
            create_table_query=_text(row[4], field="system.tables.create_table_query"),
        )
        if table.database != manifest.target_database or table.host not in reachable:
            raise CatalogProdSchemaError(
                "system.tables returned a row outside the isolated target replicas"
            )
        tables.append(table)
    tables.sort()
    identities = [(table.host, table.name) for table in tables]
    if len(identities) != len(set(identities)):
        raise CatalogProdSchemaError(
            "system.tables returned duplicate table identities"
        )
    if not present:
        if tables:
            raise CatalogProdSchemaError(
                "target tables exist even though the target database is absent"
            )
        state = "absent"
    elif not tables:
        state = "empty"
    else:
        _validate_exact_replica_schema(
            tables,
            manifest=manifest,
            expected_hosts=cluster_proof.reachable_hosts,
        )
        state = "exact"
    return _ObservedSchema(
        state=state,
        database_hosts=tuple(sorted(database_hosts)),
        tables=tuple(tables),
        schema_sha256=_schema_digest(tables),
    )


def _validate_exact_replica_schema(
    tables: Sequence[ReplicaTable],
    *,
    manifest: CatalogProdSchemaManifest,
    expected_hosts: Sequence[str],
) -> None:
    expected = {table.table: table for table in manifest.tables}
    by_host: dict[str, dict[str, ReplicaTable]] = {host: {} for host in expected_hosts}
    for table in tables:
        host_tables = by_host.get(table.host)
        if host_tables is None:
            raise CatalogProdSchemaError(
                f"unexpected replica {table.host!r} returned a target table"
            )
        host_tables[table.name] = table

    expected_names = set(expected)
    for host, host_tables in sorted(by_host.items()):
        if set(host_tables) != expected_names:
            raise CatalogProdSchemaError(
                f"target database on {host} must contain exactly the seven expected "
                f"tables; got {sorted(host_tables)}"
            )
        for table_name, actual in sorted(host_tables.items()):
            expected_table = expected[table_name]
            if actual.engine != expected_table.engine:
                raise CatalogProdSchemaError(
                    f"target {host}/{table_name} has engine {actual.engine!r}; "
                    f"expected {expected_table.engine!r}"
                )
            actual_tokens = _canonical_create_tokens(
                actual.create_table_query,
                target_database=manifest.target_database,
                expected_table=table_name,
                cluster=manifest.cluster,
            )
            expected_tokens = _canonical_create_tokens(
                expected_table.sql,
                target_database=manifest.target_database,
                expected_table=table_name,
                cluster=manifest.cluster,
            )
            if actual_tokens != expected_tokens:
                raise CatalogProdSchemaError(
                    f"target {host}/{table_name} CREATE definition differs from "
                    "the pinned production manifest"
                )


def _create_query_tokens(value: str) -> tuple[str, ...]:
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
                raise CatalogProdSchemaError(
                    "schema comparison found an unterminated string literal"
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
                raise CatalogProdSchemaError(
                    "schema comparison found an unterminated quoted identifier"
                )
            identifier_text = "".join(identifier)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier_text):
                tokens.append(f"word:{identifier_text.lower()}")
            else:
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
        raise CatalogProdSchemaError(
            f"schema comparison found unsupported SQL character {character!r}"
        )
    if tokens and tokens[-1] == "punct:;":
        tokens.pop()
    if "punct:;" in tokens:
        raise CatalogProdSchemaError(
            "schema comparison accepts exactly one CREATE statement"
        )
    return tuple(tokens)


def _canonical_create_tokens(
    value: str,
    *,
    target_database: str,
    expected_table: str,
    cluster: str,
) -> tuple[str, ...]:
    tokens = list(_create_query_tokens(value))
    if tokens[:2] != ["word:create", "word:table"]:
        raise CatalogProdSchemaError(
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
        raise CatalogProdSchemaError(
            f"target {expected_table} CREATE query is missing its table name"
        )

    first_name = tokens[position]
    position += 1
    if position < len(tokens) and tokens[position] == "punct:.":
        if first_name != f"word:{target_database}":
            raise CatalogProdSchemaError(
                f"target {expected_table} CREATE query names another database"
            )
        position += 1
        if position >= len(tokens):
            raise CatalogProdSchemaError(
                f"target {expected_table} CREATE query has an invalid qualifier"
            )
        table_token = tokens[position]
        position += 1
    else:
        table_token = first_name
    if table_token != f"word:{expected_table}":
        raise CatalogProdSchemaError(
            f"target {expected_table} CREATE query names another table"
        )

    while position < len(tokens):
        if tokens[position : position + 2] == ["word:on", "word:cluster"]:
            if position + 2 >= len(tokens):
                raise CatalogProdSchemaError("ON CLUSTER is missing its cluster")
            cluster_token = tokens[position + 2]
            if cluster_token not in {
                f"string:'{cluster}'",
                f"word:{cluster.lower()}",
            }:
                raise CatalogProdSchemaError(
                    f"target {expected_table} CREATE query names another cluster"
                )
            position += 3
            continue
        if tokens[position] == "word:uuid":
            if position + 1 >= len(tokens) or not tokens[position + 1].startswith(
                "string:"
            ):
                raise CatalogProdSchemaError("UUID clause is missing its value")
            position += 2
            continue
        break
    return (
        "word:create",
        "word:table",
        f"word:{expected_table}",
        *tokens[position:],
    )


def _evidence(
    *,
    mode: str,
    action: str,
    manifest: CatalogProdSchemaManifest,
    topology: _ClusterProof,
    before: _ObservedSchema,
    after: _ObservedSchema,
    write_count: int,
) -> str:
    payload = {
        "action": action,
        "after": after.as_dict(),
        "before": before.as_dict(),
        "manifest": manifest.as_dict(),
        "mode": mode,
        "topology": topology.as_dict(),
        "write_count": write_count,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def dry_run_catalog_prod_schema(
    client: CatalogProdClickHouseClient,
    *,
    target_database: str,
    cluster: str,
    keeper_path_prefix: str,
) -> str:
    """Return a manifest and read-only preflight proof; issue no commands."""

    manifest = render_catalog_prod_schema(
        target_database=target_database,
        cluster=cluster,
        keeper_path_prefix=keeper_path_prefix,
    )
    topology = _prove_cluster(client, cluster=cluster)
    observed = _snapshot_target(client, manifest, topology)
    action = "verified_existing" if observed.state == "exact" else "would_create"
    return _evidence(
        mode="dry_run",
        action=action,
        manifest=manifest,
        topology=topology,
        before=observed,
        after=observed,
        write_count=0,
    )


def verify_catalog_prod_schema(
    client: CatalogProdClickHouseClient,
    *,
    target_database: str,
    cluster: str,
    keeper_path_prefix: str,
) -> str:
    """Read-only proof of 1x3 topology and exact seven-table definitions."""

    manifest = render_catalog_prod_schema(
        target_database=target_database,
        cluster=cluster,
        keeper_path_prefix=keeper_path_prefix,
    )
    topology = _prove_cluster(client, cluster=cluster)
    observed = _snapshot_target(client, manifest, topology)
    if observed.state != "exact":
        raise CatalogProdSchemaError(
            "verification requires the exact seven-table production catalog on all "
            "three replicas"
        )
    return _evidence(
        mode="verify",
        action="verified_existing",
        manifest=manifest,
        topology=topology,
        before=observed,
        after=observed,
        write_count=0,
    )


def install_catalog_prod_schema(
    client: CatalogProdClickHouseClient,
    *,
    target_database: str,
    cluster: str,
    keeper_path_prefix: str,
) -> str:
    """Install only into an absent/empty isolated database, then verify 1x3.

    A non-empty target is accepted only when every replica already exposes the
    exact manifest.  In that idempotent case this function performs no writes.
    """

    manifest = render_catalog_prod_schema(
        target_database=target_database,
        cluster=cluster,
        keeper_path_prefix=keeper_path_prefix,
    )
    topology = _prove_cluster(client, cluster=cluster)
    before = _snapshot_target(client, manifest, topology)
    if before.state == "exact":
        return _evidence(
            mode="install",
            action="verified_existing",
            manifest=manifest,
            topology=topology,
            before=before,
            after=before,
            write_count=0,
        )
    if before.state not in {"absent", "empty"}:
        raise CatalogProdSchemaError(
            f"unsafe target state before CREATE-only install: {before.state}"
        )

    for statement in manifest.statements:
        # Defense in depth: every outgoing command is revalidated immediately
        # before the client receives it.
        if statement == manifest.database_sql:
            _validate_database_sql(
                statement,
                target_database=manifest.target_database,
                cluster=manifest.cluster,
            )
        else:
            rendered = next(
                (table for table in manifest.tables if table.sql == statement), None
            )
            if rendered is None:
                raise CatalogProdSchemaError(
                    "manifest contains a statement outside the seven-table allowlist"
                )
            _validate_rendered_table_sql(
                statement,
                target_database=manifest.target_database,
                table=rendered.table,
                cluster=manifest.cluster,
                replicated_engine=rendered.engine,
                keeper_path=rendered.keeper_path,
            )
        client.command(statement)

    topology_after = _prove_cluster(client, cluster=cluster)
    after = _snapshot_target(client, manifest, topology_after)
    if after.state != "exact":
        raise CatalogProdSchemaError(
            "CREATE-only install completed but exact three-replica verification failed"
        )
    return _evidence(
        mode="install",
        action="created",
        manifest=manifest,
        topology=topology_after,
        before=before,
        after=after,
        write_count=len(manifest.statements),
    )


__all__ = [
    "CatalogProdClickHouseClient",
    "CatalogProdSchemaError",
    "CatalogProdSchemaManifest",
    "MANIFEST_VERSION",
    "REQUIRED_REPLICAS",
    "REQUIRED_SHARDS",
    "RenderedTable",
    "TARGET_DATABASE_PATTERN",
    "dry_run_catalog_prod_schema",
    "install_catalog_prod_schema",
    "render_catalog_prod_schema",
    "verify_catalog_prod_schema",
]
