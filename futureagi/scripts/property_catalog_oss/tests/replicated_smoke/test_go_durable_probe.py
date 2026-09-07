"""Offline only: exact privileges, admission ordering, one-shot/cleanup guards."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import go_durable_probe as probe
from harness import read_file, save_new
from schema_probe import schema_bundle
from test_harness import manifest


class GoDurableFixtureTests(unittest.TestCase):
    def test_grants_match_actual_seven_table_schema_and_exact_three_insert_tables(self):
        m = manifest()
        bundle = schema_bundle(m)
        for family in ("standalone", "replicated"):
            statements = probe.grant_statements(m, family)
            tables = {t["name"] for t in bundle["databases"][family]["tables"]}
            self.assertEqual(tables, set(probe.PROOF_TABLES))
            self.assertEqual(len(probe.INSERT_TABLES), 3)
            writer = "smoke_go_writer_" + family
            reader = "smoke_go_proof_" + family
            inserts = [s for s in statements if s.startswith("GRANT INSERT ")]
            selects = [s for s in statements if s.startswith("GRANT SELECT ")]
            self.assertEqual(len(inserts), 3)
            self.assertEqual(len(selects), 7 + len(probe.SYSTEM_TABLES))
            self.assertTrue(all(s.endswith(" TO " + writer) for s in inserts))
            self.assertTrue(all(s.endswith(" TO " + reader) for s in selects))
            self.assertTrue(all("*" not in s and "ROLE" not in s and "smoke_admin" not in s for s in statements))
            self.assertTrue(all("WITH GRANT OPTION" not in s and "CREATE OR REPLACE" not in s for s in statements))
            self.assertNotIn(m["password"], "\n".join(statements))
            self.assertIn("async_insert=1, wait_for_async_insert=0", statements[0])
            self.assertIn("log_queries=1, log_query_settings=1", statements[0])
            self.assertIn("readonly=2", statements[1])
        self.assertEqual(bundle["production_required_replicas"], 3)

    def test_grants_reject_foreign_manifest_or_family(self):
        for m, family in ((manifest(), "production"), ({**manifest(), "project": "th7247-native-igazce"}, "replicated")):
            with self.assertRaises(ValueError):
                probe.grant_statements(m, family)

    def test_subprocess_environment_excludes_ambient_infrastructure_credentials(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://foreign", "CH_PASSWORD": "secret", "DJANGO_SETTINGS_MODULE": "tfc.settings.prod"}):
            self.assertTrue(set(probe.process_env()) <= {"HOME", "PATH", "LANG", "LC_ALL"})

    def test_confirmation_rejected_before_any_docker_call_or_execution_intent(self):
        with patch.object(probe, "load", return_value=manifest()), patch.object(probe, "Docker") as docker:
            with self.assertRaisesRegex(ValueError, "confirmed run id"):
                probe.execute(Path("/unused"), "foreign")
            docker.assert_not_called()

    def test_cleanup_runs_on_bootstrap_failure_and_preserves_exact_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            docker = Mock()
            docker.owned.return_value = []
            runner = Mock()
            runner.bootstrap.side_effect = RuntimeError("actual bootstrap failure")
            with patch.object(probe, "validate_execution", return_value=(manifest(), directory / "binary", {})), \
                 patch.object(probe, "Docker", return_value=docker), \
                 patch.object(probe, "wait_for_keeper", return_value=[]), \
                 patch.object(probe, "Runner", return_value=runner):
                with self.assertRaisesRegex(RuntimeError, "actual bootstrap failure"):
                    probe.execute(directory, manifest()["run_id"])
            docker.up.assert_called_once_with(manifest()["run_id"])
            docker.cleanup.assert_called_once_with()
            self.assertIn(b"actual bootstrap failure", read_file(directory / "go-live-result.json"))

    def test_cleanup_error_never_becomes_passing_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            docker = Mock()
            docker.owned.return_value = []
            docker.up.side_effect = RuntimeError("headroom denied")
            docker.cleanup.side_effect = RuntimeError("ownership changed")
            with patch.object(probe, "validate_execution", return_value=(manifest(), directory / "binary", {})), \
                 patch.object(probe, "Docker", return_value=docker):
                with self.assertRaisesRegex(RuntimeError, "ownership changed"):
                    probe.execute(directory, manifest()["run_id"])
            raw = read_file(directory / "go-live-result.json")
            self.assertIn(b"headroom denied", raw)
            self.assertIn(b"cleanup_error", raw)
            self.assertIn(b'"status":"failed"', raw)

    def test_keeper_wait_retries_only_reads_before_bootstrap(self):
        runner = Mock()
        runner.verify_owned.return_value = []
        runner.sql.side_effect = [ValueError("connection refused"), [{"entries": 0}], [{"entries": 1}]]
        with patch.object(probe.time, "sleep") as sleep:
            self.assertEqual(probe.wait_for_keeper(runner), [])
        sleep.assert_called_once_with(0.5)
        self.assertEqual(runner.sql.call_count, 3)
        self.assertTrue(all(call.args[1].startswith("SELECT ") and not call.kwargs.get("write") for call in runner.sql.call_args_list))
        runner.bootstrap.assert_not_called()

    def test_keeper_wait_timeout_does_not_issue_ddl(self):
        runner = Mock()
        runner.sql.side_effect = ValueError("Keeper unavailable")
        with self.assertRaisesRegex(ValueError, "Keeper unavailable"):
            probe.wait_for_keeper(runner, timeout=0)
        runner.bootstrap.assert_not_called()

    def test_live_intent_is_exclusive_before_docker_connect(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            save_new(directory / "go-plan.json", {"run_id": manifest()["run_id"], "source_sha256": {}, "binary_sha256": probe.digest(b"binary")})
            save_new(directory / "go-durable-probe.test", b"binary")
            with patch.object(probe, "load", return_value=manifest()), patch.object(probe, "go_sources", return_value={}):
                probe.validate_execution(directory, manifest()["run_id"])
                with self.assertRaises(FileExistsError):
                    probe.validate_execution(directory, manifest()["run_id"])


if __name__ == "__main__":
    unittest.main()
