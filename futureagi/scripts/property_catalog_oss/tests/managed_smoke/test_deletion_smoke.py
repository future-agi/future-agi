"""Offline diagnostic-control tests; these are not deletion E2E evidence."""

import itertools
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deletion_smoke import diagnose_candidate_repair, verify


class DeletionDiagnosticTests(unittest.TestCase):
    def fixture(self):
        return {
            "project_id": "00000000-0000-4000-8000-000000000001",
            "since": "2026-09-06T00:00:00+00:00",
        }

    def test_unknown_probe_values_are_not_requested_before_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            run = SimpleNamespace(
                directory=Path(directory),
                manifest={"run_id": "0123456789abcdef", "ports": {"otlp": 45678}},
            )
            session = Mock()
            session.__enter__ = Mock(return_value=session)
            session.__exit__ = Mock(return_value=False)
            session.post.return_value.status_code = 200
            session.post.return_value.json.return_value = {}
            discovered = False
            probe_reads = 0

            def get(endpoint, params):
                nonlocal discovered, probe_reads
                if endpoint == "metrics":
                    if params["search"] == "otlp_late":
                        metrics = [] if probe_reads else [{"name": "otlp_late"}]
                    else:
                        probe_reads += 1
                        discovered = probe_reads > 1
                        metrics = (
                            [{"name": "deletion_repair_probe"}] if discovered else []
                        )
                    return {"metrics": metrics, "catalog_revision": 22}, 0
                if params["property_id"] == "custom_attribute:deletion_repair_probe":
                    self.assertTrue(
                        discovered, "the real API rejects unknown definitions"
                    )
                    value = "trigger"
                else:
                    self.assertEqual(params["property_id"], "custom_attribute:plan")
                    value = "Pro"
                return {"values": [{"value": value}]}, 0

            with (
                patch("ingestion_smoke.collector_state"),
                patch("requests.Session", return_value=session),
                patch("deletion_smoke.time.sleep"),
                patch("deletion_smoke.time.monotonic", side_effect=itertools.count()),
            ):
                result = diagnose_candidate_repair(
                    run,
                    self.fixture(),
                    SimpleNamespace(api_key="local", secret_key="local"),
                    get,
                )
            self.assertEqual(result["status"], "passed")
            self.assertTrue(result["diagnostic_only"])
            self.assertTrue(result["does_not_satisfy_notification_loss_gate"])
            self.assertTrue(result["unrelated_history_preserved"])
            session.post.assert_called_once()
            self.assertFalse(session.trust_env)
            self.assertFalse(session.post.call_args.kwargs["allow_redirects"])
            self.assertEqual(result["observations"][0]["probe_values"], None)

    def test_pending_source_repair_cannot_mask_the_quiet_deletion_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Mock()
            run.directory = Path(directory)
            run.manifest = {"application": True, "run_id": "0123456789abcdef"}
            run.owned.return_value = ["owned-synthetic-container"]
            candidate_dir = run.directory / "application-spool/candidate-receipts"
            source_dir = run.directory / "application-runtime/source-repairs"
            candidate_dir.mkdir(parents=True)
            source_dir.mkdir(parents=True)
            raw = json.dumps({"generation": 1, "candidate_id": "unit-test"})
            path = candidate_dir / "repair-synthetic.json"
            path.write_text(raw)
            Path(str(path) + ".ack").write_text(raw)
            source = source_dir / "repair-synthetic.json"
            source.write_text('{"generation":1}')
            run.execute.return_value = SimpleNamespace(
                stdout=json.dumps({"versions": 1, "deleted": 0, "value": "updated"})
            )
            with patch("deletion_smoke.time.monotonic", side_effect=[0, 100]):
                with self.assertRaisesRegex(
                    RuntimeError, "earlier repairs are still pending"
                ):
                    verify(run, self.fixture(), object(), Mock())
            self.assertEqual(run.execute.call_count, 1)  # source SELECT only
            self.assertFalse(Path(str(source) + ".ack").exists())

    def test_successful_diagnostic_never_overrides_primary_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Mock()
            run.directory = Path(directory)
            run.manifest = {"application": True, "run_id": "0123456789abcdef"}
            run.owned.return_value = ["owned-synthetic-container"]
            receipts = run.directory / "application-spool/candidate-receipts"
            receipts.mkdir(parents=True)
            request = receipts / "repair-synthetic.json"
            raw = json.dumps({"generation": 1, "candidate_id": "unit-test"})
            request.write_text(raw)
            Path(str(request) + ".ack").write_text(raw)
            run.execute.side_effect = [
                SimpleNamespace(
                    stdout=json.dumps({"versions": 1, "deleted": 0, "value": "updated"})
                ),
                SimpleNamespace(
                    stdout="is_deleted\n_version\nupdated_at\nid\ntrace_id\nproject_id\n"
                ),
                SimpleNamespace(stdout=""),
                SimpleNamespace(
                    stdout=json.dumps({"versions": 2, "deleted": 1, "value": "updated"})
                ),
            ]
            with (
                patch("deletion_smoke.time.monotonic", side_effect=[0, 1, 2, 100, 101]),
                patch(
                    "deletion_smoke.diagnose_candidate_repair",
                    return_value={"status": "passed"},
                ) as diagnostic,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "not reflected without new candidate"
                ):
                    verify(run, self.fixture(), object(), Mock())
            diagnostic.assert_called_once()
            result = json.loads((run.directory / "catalog-deletion.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["no_new_candidate_repair"])
            writes = [
                call.args[1][-1]
                for call in run.execute.call_args_list
                if call.args[1][-1].startswith("INSERT")
            ]
            self.assertEqual(len(writes), 1)
            self.assertIn(
                "WHERE project_id=toUUID('" + self.fixture()["project_id"] + "')",
                writes[0],
            )
            self.assertIn("AND name='otlp_late'", writes[0])
            self.assertIn("ORDER BY _version DESC LIMIT 1", writes[0])
            self.assertEqual(request.read_text(), raw)
