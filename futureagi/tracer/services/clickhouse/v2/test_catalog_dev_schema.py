from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock


def _load_subject():
    module_path = Path(__file__).with_name("catalog_dev_schema.py")
    spec = importlib.util.spec_from_file_location(
        "catalog_dev_schema_subject", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load catalog_dev_schema.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


catalog_dev_schema = _load_subject()


class FakeClickHouseClient:
    def __init__(
        self,
        *,
        version: str = "25.3.8.23",
        databases: set[str] | None = None,
        tables: Sequence[tuple[str, str, str, str]] = (),
        mutate_unrelated_on_second_snapshot: bool = False,
    ) -> None:
        self.version = version
        self.databases = set(databases or {"default"})
        self.tables = list(tables)
        self.mutate_unrelated_on_second_snapshot = mutate_unrelated_on_second_snapshot
        self.commands: list[tuple[str, str | None]] = []
        self.snapshot_reads = 0

    def query_rows(
        self, sql: str, *, database: str | None = None
    ) -> Sequence[Sequence[object]]:
        if database is not None:
            raise AssertionError("metadata queries must not be target-scoped")
        normalized = " ".join(sql.split())
        if normalized == "SELECT version()":
            return [(self.version,)]
        if "FROM system.tables" in normalized:
            self.snapshot_reads += 1
            if self.snapshot_reads == 2 and self.mutate_unrelated_on_second_snapshot:
                self.tables = [
                    (
                        db,
                        name,
                        engine,
                        query + " /* concurrent drift */"
                        if (db, name) == ("default", "spans")
                        else query,
                    )
                    for db, name, engine, query in self.tables
                ]
            return sorted(self.tables)
        if "FROM system.databases" in normalized:
            match = re.search(r"WHERE name = '([a-z0-9_]+)'", normalized)
            if match is None:
                raise AssertionError(f"unexpected database query: {sql}")
            name = match.group(1)
            return [(name,)] if name in self.databases else []
        raise AssertionError(f"unexpected query: {sql}")

    def command(self, sql: str, *, database: str | None = None) -> None:
        self.commands.append((sql, database))
        database_match = re.fullmatch(
            r"CREATE DATABASE IF NOT EXISTS ([a-z0-9_]+)", sql
        )
        if database_match is not None:
            if database is not None:
                raise AssertionError("CREATE DATABASE must use the admin context")
            self.databases.add(database_match.group(1))
            return

        if database is None or database not in self.databases:
            raise AssertionError("table DDL must use an existing target context")
        table_match = re.match(
            r"CREATE TABLE IF NOT EXISTS ([A-Za-z_][A-Za-z0-9_]*)\b", sql
        )
        engine_match = re.search(r"\bENGINE\s*=\s*([A-Za-z][A-Za-z0-9]*)", sql)
        if table_match is None or engine_match is None:
            raise AssertionError(f"unexpected command: {sql}")
        self.tables.append((database, table_match.group(1), engine_match.group(1), sql))


class CatalogDevSchemaTests(unittest.TestCase):
    _existing_table = (
        "default",
        "spans",
        "MergeTree",
        "CREATE TABLE default.spans (id UUID) ENGINE = MergeTree ORDER BY id",
    )
    _target = "property_catalog_dev_unit_20260813"

    def _server_create_query(self, table: str, sql: str) -> str:
        return re.sub(
            rf"\ACREATE TABLE IF NOT EXISTS {re.escape(table)}\b",
            f"CREATE TABLE `{self._target}`.`{table}`",
            sql.rstrip(";"),
        )

    def test_applies_only_six_pinned_creates_and_emits_evidence(self) -> None:
        client = FakeClickHouseClient(tables=[self._existing_table])

        evidence_json = catalog_dev_schema.apply_catalog_dev_schema(
            client,
            target_database=self._target,
            development_sentinel=catalog_dev_schema.DEVELOPMENT_SENTINEL,
        )
        evidence = json.loads(evidence_json)

        self.assertEqual(len(client.commands), 7)
        self.assertEqual(
            client.commands[0],
            (f"CREATE DATABASE IF NOT EXISTS {self._target}", None),
        )
        expected_sql = [
            statement.sql for statement in catalog_dev_schema._load_pinned_statements()
        ]
        self.assertEqual([sql for sql, _database in client.commands[1:]], expected_sql)
        self.assertEqual(
            [database for _sql, database in client.commands[1:]],
            [self._target] * 6,
        )
        self.assertTrue(
            all(sql.startswith("CREATE TABLE IF NOT EXISTS ") for sql in expected_sql)
        )
        executable = "\n".join(expected_sql)
        self.assertNotRegex(executable, r"(?i)\b(?:ALTER|DROP|INSERT)\b")
        self.assertNotRegex(executable, r"(?i)\bMATERIALIZED\s+VIEW\b")
        self.assertNotRegex(executable, r"(?i)\bFROM\s+spans\b")
        self.assertNotIn("schema_versions", executable)

        self.assertEqual(evidence["clickhouse_version"], "25.3.8.23")
        self.assertTrue(evidence["database_created"])
        self.assertTrue(evidence["pre_existing_tables_unchanged"])
        self.assertEqual(evidence["validated_target_table_count"], 6)
        self.assertEqual(
            evidence["pre_existing_tables"],
            [
                {
                    "create_table_query": self._existing_table[3],
                    "database": "default",
                    "engine": "MergeTree",
                    "name": "spans",
                }
            ],
        )
        self.assertEqual(
            evidence["pre_existing_tables_sha256"],
            evidence["post_existing_tables_excluding_target_sha256"],
        )
        self.assertEqual(len(evidence["target_tables"]), 6)
        self.assertEqual(len(evidence["statements_applied"]), 6)
        self.assertEqual(
            [item["table"] for item in evidence["statements_applied"]],
            [
                "property_definition_catalog",
                "span_attribute_value_catalog",
                "property_catalog_checkpoints",
                "property_catalog_activations",
                "property_catalog_deliveries",
                "property_catalog_source_streams",
            ],
        )

    def test_existing_empty_dev_database_skips_database_ddl(self) -> None:
        client = FakeClickHouseClient(
            databases={"default", self._target}, tables=[self._existing_table]
        )

        evidence = json.loads(
            catalog_dev_schema.apply_catalog_dev_schema(
                client,
                target_database=self._target,
                development_sentinel=catalog_dev_schema.DEVELOPMENT_SENTINEL,
            )
        )

        self.assertEqual(len(client.commands), 6)
        self.assertTrue(
            all(database == self._target for _, database in client.commands)
        )
        self.assertFalse(evidence["database_created"])

    def test_local_gates_fail_before_any_command(self) -> None:
        cases = (
            {
                "target_database": self._target,
                "development_sentinel": "",
            },
            {
                "target_database": "default",
                "development_sentinel": catalog_dev_schema.DEVELOPMENT_SENTINEL,
            },
            {
                "target_database": "property_catalog_dev_bad-name",
                "development_sentinel": catalog_dev_schema.DEVELOPMENT_SENTINEL,
            },
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                client = FakeClickHouseClient(tables=[self._existing_table])
                with self.assertRaises(catalog_dev_schema.CatalogDevSchemaError):
                    catalog_dev_schema.apply_catalog_dev_schema(client, **kwargs)
                self.assertEqual(client.commands, [])

    def test_safe_legacy_target_name_is_admitted(self) -> None:
        catalog_dev_schema._validate_target_database("legacy_catalog_snapshot")

    def test_wrong_clickhouse_minor_fails_before_any_command(self) -> None:
        for version in ("24.10.5.1", "25.2.9.1", "25.30.1.1", "26.3.1.1"):
            with self.subTest(version=version):
                client = FakeClickHouseClient(
                    version=version, tables=[self._existing_table]
                )
                with self.assertRaisesRegex(
                    catalog_dev_schema.CatalogDevSchemaError,
                    "ClickHouse 25.3 is required",
                ):
                    catalog_dev_schema.apply_catalog_dev_schema(
                        client,
                        target_database=self._target,
                        development_sentinel=catalog_dev_schema.DEVELOPMENT_SENTINEL,
                    )
                self.assertEqual(client.commands, [])
                self.assertEqual(client.snapshot_reads, 0)

    def test_any_pre_existing_target_table_fails_before_any_command(self) -> None:
        client = FakeClickHouseClient(
            databases={"default", self._target},
            tables=[
                self._existing_table,
                (
                    self._target,
                    "property_definition_catalog",
                    "MergeTree",
                    "pre-existing target DDL",
                ),
            ],
        )

        with self.assertRaisesRegex(
            catalog_dev_schema.CatalogDevSchemaError,
            "target database must be empty",
        ):
            catalog_dev_schema.apply_catalog_dev_schema(
                client,
                target_database=self._target,
                development_sentinel=catalog_dev_schema.DEVELOPMENT_SENTINEL,
            )
        self.assertEqual(client.commands, [])

    def test_existing_six_table_catalog_is_never_reinterpreted_or_upgraded(
        self,
    ) -> None:
        create_statements = list(catalog_dev_schema._load_pinned_statements())
        target_tables = [
            (self._target, statement.table, statement.engine, statement.sql)
            for statement in create_statements
        ]
        client = FakeClickHouseClient(
            databases={"default", self._target},
            tables=[self._existing_table, *target_tables],
        )

        with self.assertRaisesRegex(
            catalog_dev_schema.CatalogDevSchemaError,
            "target database must be empty",
        ):
            catalog_dev_schema.apply_catalog_dev_schema(
                client,
                target_database=self._target,
                development_sentinel=catalog_dev_schema.DEVELOPMENT_SENTINEL,
            )
        self.assertEqual(client.commands, [])

    def test_verify_existing_exact_catalog_is_read_only(self) -> None:
        target_tables = [
            (
                self._target,
                statement.table,
                statement.engine,
                self._server_create_query(statement.table, statement.sql),
            )
            for statement in catalog_dev_schema._load_pinned_statements()
        ]
        client = FakeClickHouseClient(
            databases={"default", self._target},
            tables=[self._existing_table, *target_tables],
        )

        evidence = json.loads(
            catalog_dev_schema.verify_catalog_dev_schema(
                client,
                target_database=self._target,
            )
        )

        self.assertEqual(client.commands, [])
        self.assertEqual(evidence["schema_action"], "verified_existing")
        self.assertEqual(evidence["validated_target_table_count"], 6)
        self.assertEqual(evidence["write_count"], 0)
        self.assertRegex(evidence["pinned_create_schema_sha256"], r"\A[0-9a-f]{64}\Z")

    def test_verify_rejects_any_full_create_definition_drift(self) -> None:
        statements = list(catalog_dev_schema._load_pinned_statements())
        drift_cases = (
            (
                "extra column",
                "    organization_id       UUID,",
                "    organization_id       UUID,\n    unauthorized_column String,",
            ),
            (
                "sorting key",
                "    state_sha256",
                "    property_id",
            ),
            (
                "enum label",
                "'system_attribute' = 1",
                "'renamed_system_attribute' = 1",
            ),
            (
                "index setting",
                "SETTINGS index_granularity = 8192",
                "SETTINGS index_granularity = 4096",
            ),
        )
        for label, old, new in drift_cases:
            with self.subTest(label=label):
                target_tables = []
                for statement in statements:
                    query = self._server_create_query(statement.table, statement.sql)
                    if statement.table == "property_definition_catalog":
                        self.assertIn(old, query)
                        query = query.replace(old, new, 1)
                    target_tables.append(
                        (self._target, statement.table, statement.engine, query)
                    )
                client = FakeClickHouseClient(
                    databases={"default", self._target},
                    tables=[self._existing_table, *target_tables],
                )

                with self.assertRaisesRegex(
                    catalog_dev_schema.CatalogDevSchemaError,
                    "CREATE definitions differ",
                ):
                    catalog_dev_schema.verify_catalog_dev_schema(
                        client,
                        target_database=self._target,
                    )
                self.assertEqual(client.commands, [])

    def test_ensure_replays_exact_schema_without_ddl(self) -> None:
        target_tables = [
            (self._target, statement.table, statement.engine, statement.sql)
            for statement in catalog_dev_schema._load_pinned_statements()
        ]
        client = FakeClickHouseClient(
            databases={"default", self._target},
            tables=[self._existing_table, *target_tables],
        )

        evidence = json.loads(
            catalog_dev_schema.ensure_catalog_dev_schema(
                client,
                target_database=self._target,
                development_sentinel=catalog_dev_schema.DEVELOPMENT_SENTINEL,
            )
        )

        self.assertEqual(client.commands, [])
        self.assertEqual(evidence["schema_action"], "verified_existing")

    def test_ensure_rejects_partial_target_without_ddl(self) -> None:
        statement = catalog_dev_schema._load_pinned_statements()[0]
        client = FakeClickHouseClient(
            databases={"default", self._target},
            tables=[
                self._existing_table,
                (self._target, statement.table, statement.engine, statement.sql),
            ],
        )

        with self.assertRaisesRegex(
            catalog_dev_schema.CatalogDevSchemaError,
            "exactly the six",
        ):
            catalog_dev_schema.ensure_catalog_dev_schema(
                client,
                target_database=self._target,
                development_sentinel=catalog_dev_schema.DEVELOPMENT_SENTINEL,
            )

        self.assertEqual(client.commands, [])

    def test_unrelated_table_drift_fails_post_apply_proof(self) -> None:
        client = FakeClickHouseClient(
            tables=[self._existing_table],
            mutate_unrelated_on_second_snapshot=True,
        )

        with self.assertRaisesRegex(
            catalog_dev_schema.CatalogDevSchemaError,
            "pre-existing table changed",
        ):
            catalog_dev_schema.apply_catalog_dev_schema(
                client,
                target_database=self._target,
                development_sentinel=catalog_dev_schema.DEVELOPMENT_SENTINEL,
            )
        self.assertEqual(len(client.commands), 7)

    def test_stdlib_http_adapter_is_loopback_only_and_preserves_command_sql(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            catalog_dev_schema.CatalogDevSchemaError, "loopback endpoint"
        ):
            catalog_dev_schema.ClickHouseHttpClient(
                "https://clickhouse.production.example:8443"
            )

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _limit: int) -> bytes:
                return b'{"data":[["25.3.8.23"]]}'

        client = catalog_dev_schema.ClickHouseHttpClient(
            "http://127.0.0.1:19001", password="test-only"
        )
        with mock.patch.object(
            catalog_dev_schema.urllib.request,
            "urlopen",
            return_value=FakeResponse(),
        ) as urlopen:
            self.assertEqual(client.query_rows("SELECT version()"), [["25.3.8.23"]])
            pinned_sql = catalog_dev_schema._load_pinned_statements()[0].sql
            client.command(pinned_sql, database=self._target)

        query_request = urlopen.call_args_list[0].args[0]
        self.assertEqual(
            query_request.data.decode("utf-8"),
            "SELECT version()\nFORMAT JSONCompact",
        )
        ddl_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(ddl_request.data.decode("utf-8"), pinned_sql)
        self.assertEqual(
            dict(
                catalog_dev_schema.urllib.parse.parse_qsl(
                    catalog_dev_schema.urllib.parse.urlsplit(ddl_request.full_url).query
                )
            ),
            {"database": self._target},
        )


if __name__ == "__main__":
    unittest.main()
