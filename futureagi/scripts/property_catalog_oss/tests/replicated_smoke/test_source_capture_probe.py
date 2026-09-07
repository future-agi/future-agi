"""Offline gate safety/fixture fidelity; these tests are NOT native ATTACH proof."""

import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import harness
import source_capture_probe as probe
import sqlparse
from harness import GIB, MEMORY, read_file, save_new, xml_files
from schema_probe import configure_libraries
from test_harness import manifest


class SourceCaptureFixtureTests(unittest.TestCase):
    def test_exact_canonical_ddl_changes_only_header_and_replication_arguments(self):
        m = manifest()
        original = sqlparse.split(read_file(probe.SOURCE_FILE).decode())[0]
        sql = probe.canonical_source_create(m)
        source, _ = probe.names(m)
        restored = sql.replace(
            f"CREATE TABLE `{source}`.`spans`", "CREATE TABLE IF NOT EXISTS spans"
        )
        restored = restored.replace(
            f"ENGINE = ReplicatedReplacingMergeTree('/clickhouse/capture-probe/{m['run_id']}/spans', '{{replica}}', _version, is_deleted)",
            "ENGINE = ReplacingMergeTree(_version, is_deleted)",
        )
        self.assertEqual(restored, original)
        self.assertEqual(len(sqlparse.split(sql)), 1)
        self.assertIn("proj_root_spans", sql)
        self.assertIn("proj_metrics_hourly", sql)
        self.assertIn("INDEX idx_id", sql)
        self.assertIn("storage_policy", sql)
        self.assertNotIn("ON CLUSTER", sql)

    def test_real_product_schema_accepts_full_fixture_ddl_and_keeps_indices(self):
        configure_libraries()
        from tracer.services.clickhouse.v2.property_catalog.source_capture_schema import (
            qualified_capture_schema,
        )

        source, catalog = probe.names(manifest())
        result = qualified_capture_schema(
            probe.canonical_source_create(manifest()),
            source_database=source,
            source_table="spans",
            target_database=catalog + "_source_capture",
            target_table="spans_fixture",
            target_uuid="c504cb1f-630c-491e-97c3-df9db7fdcb05",
        )
        self.assertIn("ENGINE = MergeTree", result.create_sql)
        for required in (
            "INDEX idx_id",
            "proj_root_spans",
            "proj_metrics_hourly",
            "storage_policy",
            "index_granularity_bytes",
        ):
            self.assertIn(required, result.create_sql)
        self.assertNotIn("ReplicatedReplacingMergeTree", result.create_sql)
        self.assertNotIn("TO VOLUME", result.create_sql)
        result.verify_capture(result.create_sql)

    def test_existing_budget_and_canonical_local_policy(self):
        self.assertEqual(sum(MEMORY.values()), int(6.5 * GIB))
        for node in ("replica1", "replica2"):
            root = ET.fromstring(xml_files(manifest())[node + ".xml"])
            storage = root.find("storage_configuration")
            self.assertEqual(storage.findtext("disks/cold_local/type"), "local")
            self.assertEqual(
                storage.findtext("disks/cold_local/path"), "/var/lib/clickhouse/cold/"
            )
            self.assertEqual(
                [d.text for d in storage.findall("policies/tiered/volumes/*/disk")],
                ["default", "cold_local"],
            )

    def test_bootstrap_uses_exact_checked_in_migrations_before_capture(self):
        source, _ = probe.names(manifest())
        for path, sql in zip(
            probe.MIGRATIONS, probe.source_migrations(manifest()), strict=True
        ):
            original = sqlparse.split(
                sqlparse.format(read_file(path).decode(), strip_comments=True)
            )[0]
            self.assertEqual(
                sql.replace(
                    f"ALTER TABLE `{source}`.`spans`\n", "ALTER TABLE spans\n", 1
                ),
                original,
            )
        self.assertIn(
            "MODIFY COLUMN attributes_extra String",
            probe.source_migrations(manifest())[0],
        )

    def test_grants_separate_readonly_source_and_exact_target_writer(self):
        source, catalog = probe.names(manifest())
        statements = probe.grants(manifest())
        writer = [s for s in statements if s.endswith(" TO " + probe.WRITER)]
        self.assertEqual(
            writer,
            [
                f"GRANT SELECT ON `{source}`.spans TO {probe.WRITER}",
                f"GRANT SELECT, INSERT, CREATE TABLE, ALTER DELETE, ALTER TTL, DROP TABLE ON `{catalog}_source_capture`.* TO {probe.WRITER}",
            ],
        )
        reader = [s for s in statements if s.endswith(" TO " + probe.READER)]
        self.assertTrue(all(s.startswith("GRANT SELECT") for s in reader))
        self.assertTrue(
            any(
                "readonly=1" in s and s.startswith("CREATE USER " + probe.READER)
                for s in statements
            )
        )
        self.assertFalse(
            any("system.*" in s or "GRANT OPTION" in s for s in statements)
        )
        self.assertFalse(any("CREATE DATABASE" in s for s in statements))

    def test_fixture_insert_is_one_block_with_old_latest_and_tombstone(self):
        _, sql = probe.fixture_insert(manifest(), datetime(2026, 9, 6, tzinfo=UTC))
        self.assertEqual(len(sqlparse.split(sql)), 1)
        self.assertEqual(sql.count("map('capture_gate'"), probe.PHYSICAL_ROWS)
        self.assertIn("'old'), 1, 0)", sql)
        self.assertIn("'new'), 2, 0)", sql)
        self.assertIn("'removed'), 2, 1)", sql)
        self.assertIn("SETTINGS optimize_on_insert=0 VALUES", sql)
        self.assertNotIn(
            "optimize_on_insert", probe.canonical_source_create(manifest())
        )

    def test_foreign_or_injected_namespace_rejected_before_rendering(self):
        for key, value in (
            ("run_id", "x'; DROP DATABASE default; --"),
            ("project", "foreign"),
        ):
            m = manifest()
            m[key] = value
            with self.assertRaises(ValueError):
                probe.canonical_source_create(m)
            with self.assertRaises(ValueError):
                probe.grants(m)

    def test_plan_never_contacts_docker_or_a_database(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(harness, "RUNS", Path(tmp) / "runs"),
            patch.object(harness, "reserve_ports", return_value=manifest()["ports"]),
            patch.object(probe, "Docker", side_effect=AssertionError("no Docker")),
            patch("subprocess.run", side_effect=AssertionError("no subprocess")),
        ):
            directory, m = probe.prepare()
            self.assertEqual(harness.load(directory), m)
            prepared = json.loads(read_file(directory / "capture-plan.json"))
            self.assertEqual(prepared["sources"], probe.sources())
            self.assertEqual(
                read_file(directory / "capture-source.sql").decode(),
                probe.canonical_source_create(m),
            )
            self.assertFalse((directory / "start-intent.json").exists())

    def test_source_identity_parts_and_rows_all_participate_in_unchanged_check(self):
        before = {
            "replica1": {
                "table": [{"table_uuid": "a", "create_table_query": "ddl"}],
                "parts": [{"checksum": "a"}],
                "rows": [{"_version": 1}],
            }
        }
        probe.assert_source_unchanged(before, copy.deepcopy(before))
        for field in ("table", "parts", "rows"):
            after = copy.deepcopy(before)
            after["replica1"][field] = []
            with self.assertRaisesRegex(RuntimeError, "changed source"):
                probe.assert_source_unchanged(before, after)


class ReadinessEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.m = manifest()
        columns = [
            {
                "name": "_peerdb_is_deleted",
                "type": "UInt8",
                "default_kind": "ALIAS",
                "default_expression": "is_deleted",
            },
            {
                "name": "attributes_extra",
                "type": "String",
                "default_kind": "DEFAULT",
                "default_expression": "'{}'",
            },
        ]
        self.witness = {
            node: {
                "table": [
                    {
                        "engine": probe.SOURCE_ENGINE,
                        "create_table_query": "exact fixture DDL",
                    }
                ],
                "parts": [{"rows": 5, "checksum": "exact-part"}],
                "rows": [{"row": i} for i in range(5)],
                "columns": copy.deepcopy(columns),
            }
            for node in probe.NODES
        }
        self.membership = {
            node: [
                {
                    "zookeeper_path": f"/clickhouse/capture-probe/{self.m['run_id']}/spans",
                    "replica_name": node,
                    "total_replicas": 2,
                    "active_replicas": 2,
                }
            ]
            for node in probe.NODES
        }

        def sql(node, query, *, write=False):
            self.assertFalse(write)
            self.assertTrue(query.startswith("SELECT "))
            for table, key in (
                ("system.tables", "table"),
                ("system.parts", "parts"),
                ("system.columns", "columns"),
            ):
                if "FROM " + table in query:
                    return copy.deepcopy(self.witness[node][key])
            if "FROM system.replicas" in query:
                return copy.deepcopy(self.membership[node])
            if ".spans ORDER BY" in query:
                return copy.deepcopy(self.witness[node]["rows"])
            raise AssertionError("unexpected readiness query")

        self.runner = Mock()
        self.runner.sql.side_effect = sql

    def test_success_retains_actual_bounded_witness_and_membership(self):
        result = probe.wait_replicated_source(
            self.runner, self.m, directory=self.directory, timeout=0
        )
        self.assertEqual(result, (self.witness, self.membership))
        record = json.loads(read_file(self.directory / "capture-readiness.json"))
        self.assertEqual(record["status"], "ready")
        self.assertEqual(record["witness"], self.witness)
        self.assertEqual(record["membership"], self.membership)
        self.assertTrue((self.directory / "capture-readiness-first.json").exists())

    def test_collapsed_rows_stay_rejected_and_evidence_precedes_exception(self):
        for node in probe.NODES:
            self.witness[node]["rows"] = self.witness[node]["rows"][:3]
            self.witness[node]["parts"][0]["rows"] = 3
        with self.assertRaisesRegex(RuntimeError, "replica1:row_count"):
            probe.wait_replicated_source(
                self.runner, self.m, directory=self.directory, timeout=0
            )
        raw = read_file(self.directory / "capture-readiness.json")
        record = json.loads(raw)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["attempts"], 1)
        self.assertEqual(record["witness"], self.witness)
        self.assertEqual(record["membership"], self.membership)
        self.assertFalse(record["checks"]["replica1"]["part_row_count"])
        self.assertTrue(record["checks"]["replica1"]["migrated_columns"])
        self.assertNotIn(self.m["password"].encode(), raw)
        self.assertNotIn(self.m["token"].encode(), raw)
        self.assertNotIn(b"Authorization", raw)
        self.assertEqual(self.runner.sql.call_count, 10)

    def test_schema_and_membership_mismatches_are_named_not_guessed(self):
        self.witness["replica2"]["columns"][1]["type"] = "JSON"
        self.membership["replica2"][0]["active_replicas"] = 1
        with self.assertRaisesRegex(
            RuntimeError, "replica2:membership.*replica2:migrated_columns"
        ):
            probe.wait_replicated_source(
                self.runner, self.m, directory=self.directory, timeout=0
            )
        record = json.loads(read_file(self.directory / "capture-readiness.json"))
        self.assertEqual(record["witness"]["replica2"]["columns"][1]["type"], "JSON")
        self.assertFalse(record["checks"]["replica2"]["membership"])
        self.assertTrue(record["checks"]["replica2"]["row_count"])

    def test_read_error_retains_last_complete_witness_without_retrying(self):
        original = self.runner.sql.side_effect

        def fail_membership(node, query, *, write=False):
            if "FROM system.replicas" in query:
                raise TimeoutError("bounded source metadata read failed")
            return original(node, query, write=write)

        self.runner.sql.side_effect = fail_membership
        with self.assertRaises(TimeoutError):
            probe.wait_replicated_source(self.runner, self.m, directory=self.directory)
        record = json.loads(read_file(self.directory / "capture-readiness.json"))
        self.assertEqual(record["witness"], self.witness)
        self.assertEqual(record["error_type"], "TimeoutError")
        self.assertEqual(record["attempts"], 1)
        self.assertEqual(self.runner.sql.call_count, 9)


class ExecutionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.actual_exercise = probe.exercise
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.m = manifest()
        self.fingerprints = {"product.py": "a" * 64}
        ddl = probe.canonical_source_create(self.m).encode()
        save_new(self.directory / "capture-source.sql", ddl)
        save_new(
            self.directory / "capture-plan.json",
            {
                "run_id": self.m["run_id"],
                "sources": self.fingerprints,
                "ddl_sha256": probe.digest(ddl),
                "scope": probe.SCOPE,
            },
        )
        self.docker = Mock()
        self.docker.resources.return_value = [("container", "owned")]
        self.docker.inspect.return_value = None
        for name, replacement in (
            ("load", Mock(return_value=self.m)),
            ("sources", Mock(return_value=self.fingerprints)),
            ("Docker", Mock(return_value=self.docker)),
            ("Runner", Mock()),
            ("wait_for_keeper", Mock()),
            ("exercise", Mock(return_value={"fixture": "passed"})),
        ):
            patcher = patch.object(probe, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("builtins.print")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_wrong_confirmation_blocks_before_docker(self):
        with self.assertRaises(ValueError):
            probe.execute(self.directory, "wrong")
        probe.Docker.assert_not_called()

    def test_changed_product_or_ddl_blocks_before_docker(self):
        probe.sources.return_value = {"product.py": "b" * 64}
        with self.assertRaises(ValueError):
            probe.execute(self.directory, self.m["run_id"])
        probe.Docker.assert_not_called()

    def test_changed_saved_ddl_blocks_before_docker(self):
        with patch.object(
            probe, "canonical_source_create", return_value="different DDL"
        ):
            with self.assertRaises(ValueError):
                probe.execute(self.directory, self.m["run_id"])
        probe.Docker.assert_not_called()

    def test_preexisting_resources_are_neither_started_nor_cleaned(self):
        self.docker.inspect.return_value = {"owned": True}
        with self.assertRaisesRegex(RuntimeError, "preexisting"):
            probe.execute(self.directory, self.m["run_id"])
        self.docker.up.assert_not_called()
        self.docker.cleanup.assert_not_called()

    def test_unknown_write_never_retried_and_owned_fixture_cleaned(self):
        probe.exercise.side_effect = TimeoutError("lost DDL response")
        with self.assertRaises(TimeoutError):
            probe.execute(self.directory, self.m["run_id"])
        probe.exercise.assert_called_once()
        self.docker.cleanup.assert_called_once()
        self.assertEqual(
            json.loads(read_file(self.directory / "capture-result.json"))["status"],
            "failed",
        )
        probe.Docker.reset_mock()
        with self.assertRaises(FileExistsError):
            probe.execute(self.directory, self.m["run_id"])
        probe.Docker.assert_not_called()

    def test_success_is_explicitly_not_serving_or_production_admission(self):
        probe.execute(self.directory, self.m["run_id"])
        result = json.loads(read_file(self.directory / "capture-result.json"))
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["serving_tested"])
        self.assertFalse(result["production_admitted"])
        self.assertTrue(result["owned_resources_absent"])
        self.docker.cleanup.assert_called_once()

    def test_cleanup_failure_is_not_reported_as_clean(self):
        self.docker.cleanup.side_effect = RuntimeError("resource still present")
        with self.assertRaisesRegex(RuntimeError, "resource still present"):
            probe.execute(self.directory, self.m["run_id"])
        result = json.loads(read_file(self.directory / "capture-result.json"))
        self.assertNotIn("owned_resources_absent", result)
        self.assertEqual(result["status"], "failed")

    def test_setup_failure_is_exclusive_and_never_replays_ddl(self):
        runner = Mock()
        runner.sql.side_effect = TimeoutError("unknown first CREATE")
        with self.assertRaises(TimeoutError):
            probe.exercise.side_effect = None
            # Invoke the actual function while keeping all native IO behind the
            # runner's first CREATE; no live driver is constructed on this path.
            self.actual_exercise(self.directory, self.m, runner)
        runner.sql.assert_called_once()
        with self.assertRaises(FileExistsError):
            self.actual_exercise(self.directory, self.m, runner)
        runner.sql.assert_called_once()


class ScannerGateTests(unittest.TestCase):
    def make_reader(self, *, values=None, mismatch=False, conflict=0):
        configure_libraries()
        from tracer.services.clickhouse.v2.property_catalog.span_source import (
            SpanAuditAccumulator,
        )

        values = probe.EXPECTED_VALUES if values is None else values
        accumulator = SpanAuditAccumulator()
        hashes = tuple(f"{i + 1:064x}" for i in range(len(values)))
        for h in hashes:
            accumulator.add(h)
        rows = tuple(
            SimpleNamespace(
                cursor=SimpleNamespace(span_id=key),
                attrs_string={"capture_gate": value},
                gap_reasons=(),
            )
            for key, value in values.items()
        )
        reader = Mock()
        reader.read_page.return_value = SimpleNamespace(
            spans=rows, observation_sha256s=hashes, terminal=True, next_cursor=None
        )
        reader.audit.return_value = SimpleNamespace(
            count=accumulator.proof.count,
            digest="bad" if mismatch else accumulator.proof.digest,
            state_conflict_count=conflict,
        )
        return reader

    def test_scanner_and_independent_audit_are_both_used(self):
        reader = self.make_reader()
        result = probe.scanner_proof(
            reader, project="fixture", since=datetime(2026, 9, 6, tzinfo=UTC)
        )
        self.assertEqual(result["values"], {"live": "new", "stable": "kept"})
        reader.read_page.assert_called_once()
        reader.audit.assert_called_once_with(reader.freeze.return_value)

    def test_old_value_tombstone_audit_mismatch_and_conflict_cannot_pass(self):
        for options in (
            {"values": {"live": "old", "stable": "kept"}},
            {"values": {"live": "new", "stable": "kept", "dead": "removed"}},
            {"mismatch": True},
            {"conflict": 1},
        ):
            with (
                self.subTest(options=options),
                self.assertRaisesRegex(RuntimeError, "disagrees"),
            ):
                probe.scanner_proof(
                    self.make_reader(**options),
                    project="fixture",
                    since=datetime(2026, 9, 6, tzinfo=UTC),
                )


if __name__ == "__main__":
    unittest.main()
