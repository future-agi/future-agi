from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest import mock

import pytest

from tracer.services.clickhouse.v2 import catalog_prod_schema


class FakeProdClickHouseClient:
    def __init__(
        self,
        *,
        topology_rows: Sequence[Sequence[object]] | None = None,
        reachable_hosts: Sequence[str] = ("replica-1", "replica-2", "replica-3"),
        database_hosts: Sequence[str] = (),
        tables: Sequence[Sequence[object]] = (),
        apply_commands: bool = True,
    ) -> None:
        self.topology_rows = list(
            topology_rows
            if topology_rows is not None
            else (
                (1, 1, "configured-replica-1"),
                (1, 2, "configured-replica-2"),
                (1, 3, "configured-replica-3"),
            )
        )
        self.reachable_hosts = list(reachable_hosts)
        self.database_hosts = set(database_hosts)
        self.tables = [tuple(row) for row in tables]
        self.apply_commands = apply_commands
        self.commands: list[str] = []
        self.queries: list[tuple[str, Mapping[str, object]]] = []

    def query_rows(
        self,
        sql: str,
        *,
        parameters: Mapping[str, object] | None = None,
    ) -> Sequence[Sequence[object]]:
        supplied = dict(parameters or {})
        self.queries.append((sql, supplied))
        normalized = " ".join(sql.split())
        if "FROM system.clusters" in normalized:
            return self.topology_rows
        if "system.one" in normalized:
            return [(host,) for host in self.reachable_hosts]
        if "system.databases" in normalized:
            database = supplied["database"]
            return [(host, database) for host in sorted(self.database_hosts)]
        if "system.tables" in normalized:
            return sorted(self.tables)
        raise AssertionError(f"unexpected query: {sql}")

    def command(self, sql: str) -> None:
        self.commands.append(sql)
        if not self.apply_commands:
            return
        database_match = re.fullmatch(
            r"CREATE DATABASE IF NOT EXISTS (?P<database>[a-z0-9_]+) "
            r"ON CLUSTER '(?P<cluster>[A-Za-z0-9_.-]+)';",
            sql,
        )
        if database_match is not None:
            self.database_hosts = set(self.reachable_hosts)
            return

        table_match = re.match(
            r"CREATE TABLE IF NOT EXISTS "
            r"(?P<database>[a-z0-9_]+)\.(?P<table>[A-Za-z_][A-Za-z0-9_]*) "
            r"ON CLUSTER '(?P<cluster>[A-Za-z0-9_.-]+)'",
            sql,
        )
        engine_match = re.search(
            r"\bENGINE\s*=\s*(?P<engine>Replicated(?:[A-Za-z]+)?MergeTree)", sql
        )
        if table_match is None or engine_match is None:
            raise AssertionError(f"unexpected command: {sql}")
        database = table_match.group("database")
        table = table_match.group("table")
        cluster = table_match.group("cluster")
        server_query = re.sub(
            rf"\ACREATE TABLE IF NOT EXISTS {re.escape(database)}\."
            rf"{re.escape(table)} ON CLUSTER '{re.escape(cluster)}'",
            f"CREATE TABLE `{database}`.`{table}`",
            sql.rstrip(";"),
        )
        for host in self.reachable_hosts:
            self.tables.append(
                (host, database, table, engine_match.group("engine"), server_query)
            )


TARGET = "th7247_catalog_prod_unit"
CLUSTER = "us_prod"
KEEPER_PREFIX = "/clickhouse/tables/th7247"


def _manifest() -> catalog_prod_schema.CatalogProdSchemaManifest:
    return catalog_prod_schema.render_catalog_prod_schema(
        target_database=TARGET,
        cluster=CLUSTER,
        keeper_path_prefix=KEEPER_PREFIX,
    )


def _server_create_query(
    table: catalog_prod_schema.RenderedTable,
    *,
    add_uuid: bool = False,
    database: str = TARGET,
) -> str:
    replacement = f"CREATE TABLE `{database}`.`{table.table}`"
    if add_uuid:
        replacement += " UUID '11111111-1111-1111-1111-111111111111'"
    return re.sub(
        rf"\ACREATE TABLE IF NOT EXISTS {re.escape(TARGET)}\."
        rf"{re.escape(table.table)} ON CLUSTER '{re.escape(CLUSTER)}'",
        replacement,
        table.sql.rstrip(";"),
    )


def _exact_tables(
    manifest: catalog_prod_schema.CatalogProdSchemaManifest,
    *,
    add_uuid: bool = False,
) -> list[tuple[str, str, str, str, str]]:
    return [
        (
            host,
            manifest.target_database,
            table.table,
            table.engine,
            _server_create_query(table, add_uuid=add_uuid),
        )
        for host in ("replica-1", "replica-2", "replica-3")
        for table in manifest.tables
    ]


def test_render_is_exactly_pinned_create_only_replicated_schema() -> None:
    manifest = _manifest()

    assert manifest.manifest_sha256 == (
        "71667a959c24bd05a6375b21896d116d3c26d790c017e603d0fee83a64b5d7e3"
    )
    assert manifest.database_sql_sha256 == (
        "4f695a1763f59960cd08c89ff04a4e33d5f33ecfc152952215c73f8baca2df54"
    )
    assert tuple(table.rendered_sha256 for table in manifest.tables) == (
        "75ae06a60f7872d2cb76e85b4862989fb2e715cfb3bf5fcfc82d3cec641876ec",
        "14b5797d9e768bb0ce65ebb44038c8d7d4c633107b7aa7d15d96f0be5a118e4f",
        "eff3d002b587af69b35cbe6cdd0d454ff94b45c7413298cf1c7d5945afabc12b",
        "9fe77eed2ceb161c4df9a9d8198fbbda3c1156b5b159efa3fa368fccc59a47b9",
        "751e4bc5e497f52215d049f196e64f3a92d82de16d71bce19e78dce3c514aa15",
        "d7e4c89a57f41f191bd722e80dfc9ccddb78efc2466339c90f3301538893c6ec",
        "749f3aa6de566e067b90ef0952544efefe8f7c799bd3b1811d7f55b1b35827f7",
    )
    assert len(manifest.statements) == 8
    assert manifest.database_sql == (
        "CREATE DATABASE IF NOT EXISTS th7247_catalog_prod_unit ON CLUSTER 'us_prod';"
    )
    assert tuple(table.engine for table in manifest.tables) == (
        "ReplicatedMergeTree",
        "ReplicatedAggregatingMergeTree",
        "ReplicatedReplacingMergeTree",
        "ReplicatedReplacingMergeTree",
        "ReplicatedMergeTree",
        "ReplicatedReplacingMergeTree",
        "ReplicatedMergeTree",
    )

    executable = "\n".join(manifest.statements)
    assert (
        re.search(
            r"(?i)\b(?:ALTER|DROP|TRUNCATE|RENAME|ATTACH|DETACH|DELETE|UPDATE|INSERT)\b",
            executable,
        )
        is None
    )
    assert re.search(r"(?i)\bMATERIALIZED\s+VIEW\b", executable) is None
    assert executable.count("CREATE DATABASE IF NOT EXISTS") == 1
    assert executable.count("CREATE TABLE IF NOT EXISTS") == 7
    assert executable.count("ON CLUSTER 'us_prod'") == 8
    assert executable.count("'{replica}'") == 7
    control_sql = next(
        table.sql
        for table in manifest.tables
        if table.table == "property_catalog_activation_control_events"
    )
    assert "ReplicatedMergeTree(" in control_sql
    assert "ReplacingMergeTree" not in control_sql
    assert "control_sequence" in control_sql
    assert "previous_control_sha256" in control_sql
    assert all(
        table.sql.startswith(
            f"CREATE TABLE IF NOT EXISTS {TARGET}.{table.table} ON CLUSTER '{CLUSTER}'"
        )
        for table in manifest.tables
    )
    assert all(
        table.keeper_path == f"{KEEPER_PREFIX}/{TARGET}/{{shard}}/{table.table}"
        for table in manifest.tables
    )


@pytest.mark.parametrize(
    "target",
    (
        "th7247_catalog_prod_",
        "th7247_catalog_prod_UPPER",
        "th7247_catalog_prod_bad-name",
        "th7247_catalog_dev_unit",
        "default",
        "th7247_catalog_prod_unit' OR 1=1",
    ),
)
def test_target_database_must_match_exact_prod_regex(target: str) -> None:
    with pytest.raises(
        catalog_prod_schema.CatalogProdSchemaError,
        match="target database must exactly match",
    ):
        catalog_prod_schema.render_catalog_prod_schema(
            target_database=target,
            cluster=CLUSTER,
            keeper_path_prefix=KEEPER_PREFIX,
        )


@pytest.mark.parametrize(
    ("cluster", "keeper_prefix"),
    (
        ("", KEEPER_PREFIX),
        ("prod cluster", KEEPER_PREFIX),
        ("prod'cluster", KEEPER_PREFIX),
        (CLUSTER, "clickhouse/tables/th7247"),
        (CLUSTER, "/clickhouse/{shard}/th7247"),
        (CLUSTER, "/clickhouse/tables/th7247/"),
        (CLUSTER, "/clickhouse//th7247"),
    ),
)
def test_cluster_and_keeper_path_are_explicit_safe_literals(
    cluster: str, keeper_prefix: str
) -> None:
    with pytest.raises(catalog_prod_schema.CatalogProdSchemaError):
        catalog_prod_schema.render_catalog_prod_schema(
            target_database=TARGET,
            cluster=cluster,
            keeper_path_prefix=keeper_prefix,
        )


def test_canonical_migration_drift_fails_render(tmp_path: Path) -> None:
    for migration in catalog_prod_schema._PINNED_MIGRATIONS:
        original = catalog_prod_schema._SCHEMA_DIR / migration.filename
        content = original.read_text(encoding="utf-8")
        if migration.filename == "025_property_catalog_data.sql":
            content += "\n-- unauthorized drift\n"
        (tmp_path / migration.filename).write_text(content, encoding="utf-8")

    with (
        mock.patch.object(catalog_prod_schema, "_SCHEMA_DIR", tmp_path),
        pytest.raises(
            catalog_prod_schema.CatalogProdSchemaError,
            match="canonical migration drift",
        ),
    ):
        _manifest()


def test_dry_run_is_read_only_and_includes_topology_and_manifest() -> None:
    client = FakeProdClickHouseClient()

    result = json.loads(
        catalog_prod_schema.dry_run_catalog_prod_schema(
            client,
            target_database=TARGET,
            cluster=CLUSTER,
            keeper_path_prefix=KEEPER_PREFIX,
        )
    )

    assert client.commands == []
    assert result["mode"] == "dry_run"
    assert result["action"] == "would_create"
    assert result["write_count"] == 0
    assert result["before"]["state"] == "absent"
    assert result["manifest"]["statement_count"] == 8
    assert result["manifest"]["required_topology"] == {
        "replicas_per_shard": 3,
        "shards": 1,
    }
    assert result["topology"]["reachable_hosts"] == [
        "replica-1",
        "replica-2",
        "replica-3",
    ]
    assert len(client.queries) == 4
    assert all(query[1]["cluster"] == CLUSTER for query in client.queries)
    assert all(TARGET not in sql for sql, _parameters in client.queries), (
        "target values must remain parameterized in metadata reads"
    )


@pytest.mark.parametrize(
    ("topology_rows", "reachable_hosts"),
    (
        (((1, 1, "one"), (1, 2, "two")), ("one", "two", "three")),
        (
            ((1, 1, "one"), (2, 1, "two"), (2, 2, "three")),
            ("one", "two", "three"),
        ),
        (
            ((1, 1, "one"), (1, 2, "two"), (1, 3, "three")),
            ("one", "two"),
        ),
    ),
)
def test_dry_run_rejects_any_topology_other_than_one_by_three(
    topology_rows: Sequence[Sequence[object]], reachable_hosts: Sequence[str]
) -> None:
    client = FakeProdClickHouseClient(
        topology_rows=topology_rows,
        reachable_hosts=reachable_hosts,
    )

    with pytest.raises(catalog_prod_schema.CatalogProdSchemaError):
        catalog_prod_schema.dry_run_catalog_prod_schema(
            client,
            target_database=TARGET,
            cluster=CLUSTER,
            keeper_path_prefix=KEEPER_PREFIX,
        )

    assert client.commands == []


def test_database_present_on_only_some_replicas_fails_without_ddl() -> None:
    client = FakeProdClickHouseClient(database_hosts=("replica-1", "replica-2"))

    with pytest.raises(
        catalog_prod_schema.CatalogProdSchemaError,
        match="only a subset of replicas",
    ):
        catalog_prod_schema.install_catalog_prod_schema(
            client,
            target_database=TARGET,
            cluster=CLUSTER,
            keeper_path_prefix=KEEPER_PREFIX,
        )

    assert client.commands == []


@pytest.mark.parametrize("drift", ("partial", "extra", "engine", "definition"))
def test_nonempty_target_must_match_exact_schema_before_any_ddl(drift: str) -> None:
    manifest = _manifest()
    tables = _exact_tables(manifest)
    if drift == "partial":
        tables = [
            row
            for row in tables
            if not (row[0] == "replica-3" and row[2] == manifest.tables[-1].table)
        ]
    elif drift == "extra":
        tables.append(
            (
                "replica-1",
                TARGET,
                "unauthorized_table",
                "ReplicatedMergeTree",
                "CREATE TABLE th7247_catalog_prod_unit.unauthorized_table "
                "(id UInt8) ENGINE = ReplicatedMergeTree('/bad', '{replica}') "
                "ORDER BY id",
            )
        )
    elif drift == "engine":
        first = tables[0]
        tables[0] = (*first[:3], "MergeTree", *first[4:])
    else:
        first = tables[0]
        tables[0] = (
            *first[:4],
            first[4].replace("index_granularity = 8192", "index_granularity = 4096"),
        )

    client = FakeProdClickHouseClient(
        database_hosts=("replica-1", "replica-2", "replica-3"),
        tables=tables,
    )

    with pytest.raises(catalog_prod_schema.CatalogProdSchemaError):
        catalog_prod_schema.install_catalog_prod_schema(
            client,
            target_database=TARGET,
            cluster=CLUSTER,
            keeper_path_prefix=KEEPER_PREFIX,
        )

    assert client.commands == []


def test_exact_existing_schema_is_verified_read_only_on_all_replicas() -> None:
    manifest = _manifest()
    client = FakeProdClickHouseClient(
        database_hosts=("replica-1", "replica-2", "replica-3"),
        tables=_exact_tables(manifest, add_uuid=True),
    )

    verify_result = json.loads(
        catalog_prod_schema.verify_catalog_prod_schema(
            client,
            target_database=TARGET,
            cluster=CLUSTER,
            keeper_path_prefix=KEEPER_PREFIX,
        )
    )
    install_result = json.loads(
        catalog_prod_schema.install_catalog_prod_schema(
            client,
            target_database=TARGET,
            cluster=CLUSTER,
            keeper_path_prefix=KEEPER_PREFIX,
        )
    )

    assert client.commands == []
    assert verify_result["action"] == "verified_existing"
    assert verify_result["write_count"] == 0
    assert install_result["action"] == "verified_existing"
    assert install_result["write_count"] == 0
    assert len(verify_result["after"]["table_rows"]) == 21


@pytest.mark.parametrize("initial_state", ("absent", "empty"))
def test_install_executes_only_eight_allowlisted_creates_and_verifies(
    initial_state: str,
) -> None:
    database_hosts: Sequence[str] = ()
    if initial_state == "empty":
        database_hosts = ("replica-1", "replica-2", "replica-3")
    client = FakeProdClickHouseClient(database_hosts=database_hosts)

    result = json.loads(
        catalog_prod_schema.install_catalog_prod_schema(
            client,
            target_database=TARGET,
            cluster=CLUSTER,
            keeper_path_prefix=KEEPER_PREFIX,
        )
    )

    assert len(client.commands) == 8
    assert client.commands[0].startswith("CREATE DATABASE IF NOT EXISTS ")
    assert all(
        statement.startswith("CREATE TABLE IF NOT EXISTS ")
        for statement in client.commands[1:]
    )
    assert all(f" {TARGET}." in statement for statement in client.commands[1:])
    assert all(f"ON CLUSTER '{CLUSTER}'" in statement for statement in client.commands)
    executable = "\n".join(client.commands)
    assert (
        re.search(
            r"(?i)\b(?:ALTER|DROP|TRUNCATE|RENAME|ATTACH|DETACH|DELETE|UPDATE|INSERT)\b",
            executable,
        )
        is None
    )
    assert result["action"] == "created"
    assert result["write_count"] == 8
    assert result["before"]["state"] == initial_state
    assert result["after"]["state"] == "exact"
    assert len(result["after"]["table_rows"]) == 21


def test_install_fails_closed_when_post_create_replica_proof_is_not_exact() -> None:
    client = FakeProdClickHouseClient(apply_commands=False)

    with pytest.raises(
        catalog_prod_schema.CatalogProdSchemaError,
        match="exact three-replica verification failed",
    ):
        catalog_prod_schema.install_catalog_prod_schema(
            client,
            target_database=TARGET,
            cluster=CLUSTER,
            keeper_path_prefix=KEEPER_PREFIX,
        )

    assert len(client.commands) == 8


def test_verify_is_read_only_and_rejects_absent_or_empty_target() -> None:
    for database_hosts in ((), ("replica-1", "replica-2", "replica-3")):
        client = FakeProdClickHouseClient(database_hosts=database_hosts)
        with pytest.raises(
            catalog_prod_schema.CatalogProdSchemaError,
            match="verification requires the exact seven-table",
        ):
            catalog_prod_schema.verify_catalog_prod_schema(
                client,
                target_database=TARGET,
                cluster=CLUSTER,
                keeper_path_prefix=KEEPER_PREFIX,
            )
        assert client.commands == []


def test_schema_comparison_rejects_cross_database_definition() -> None:
    manifest = _manifest()
    tables = _exact_tables(manifest)
    first = tables[0]
    tables[0] = (
        *first[:4],
        _server_create_query(manifest.tables[0], database="th7247_catalog_prod_other"),
    )
    client = FakeProdClickHouseClient(
        database_hosts=("replica-1", "replica-2", "replica-3"),
        tables=tables,
    )

    with pytest.raises(
        catalog_prod_schema.CatalogProdSchemaError,
        match="names another database",
    ):
        catalog_prod_schema.verify_catalog_prod_schema(
            client,
            target_database=TARGET,
            cluster=CLUSTER,
            keeper_path_prefix=KEEPER_PREFIX,
        )

    assert client.commands == []
