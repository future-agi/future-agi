"""Cloud-only contract: DEMO_COLLECTOR_PATH=/absolute/demo-collect.py python -I -B <this file>.

The override is mandatory. The selected collector is copied into an isolated
synthetic catalogue, never invoked in a live kit. No third-party dependencies.
Parent orchestration supplies original for RED and staged candidate for FIX.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


HEAD = "1910278e57f5ffeb39017a58563d5d510612d0c6"
JOB = "synthetic-collector-contract"
A, B, C, EXTRA = ("SYN-E2E-001", "SYN-E2E-002", "SYN-E2E-003", "SYN-E2E-999")
SPEC = "synthetic/collector.spec.ts"
CANARY = "RAW_DIAGNOSTIC_CANARY_MUST_NOT_BE_RETAINED"


def case(fid, statuses=("passed",), *, annotation=None, file=SPEC):
    annotations = [{"type": "flow", "description": json.dumps({"id": fid})}]
    if annotation is not None:
        annotations.append({"type": "skip", "description": annotation})
    results = [{"status": status, "duration": 1, "attachments": []} for status in statuses]
    return {"id": "synthetic-" + fid, "title": fid + " synthetic contract", "file": file,
            "tests": [{"annotations": annotations, "results": results}]}


class CollectorContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        override = os.environ.get("DEMO_COLLECTOR_PATH")
        if not override:
            raise RuntimeError("Set DEMO_COLLECTOR_PATH to the explicit original or staged demo-collect.py; cloud only")
        cls.source = Path(override)
        if not cls.source.is_absolute() or cls.source.name != "demo-collect.py" or not cls.source.is_file():
            raise RuntimeError("DEMO_COLLECTOR_PATH must be an absolute existing demo-collect.py path")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="demo-collector-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.collector = self.root / "demo-collect.py"
        shutil.copyfile(self.source, self.collector)
        flows = [{"n": index, "id": fid, "title": "Synthetic contract", "spec": SPEC,
                  "area": "synthetic", "shard": "synthetic"}
                 for index, fid in enumerate((A, B, C), 1)]
        (self.root / "DEMO-FLOWS-tip2.json").write_text(json.dumps({"flows": flows}), encoding="utf-8")

    def collect(self, specs=(), selected=(A,), *, manifest=True, manifest_override=None,
                report_mode="normal", env=None):
        report = self.root / "report.json"
        if report_mode == "normal":
            report.write_text(json.dumps({"suites": [{"specs": list(specs)}]}), encoding="utf-8")
        elif report_mode == "invalid":
            report.write_text(CANARY, encoding="utf-8")
        elif report_mode == "nonobject":
            report.write_text("[]", encoding="utf-8")
        out = self.root / "out"
        bounded_env = {"DEMO_IDS": " ".join(selected), "E2E_GATEWAY_PROFILE": "stock"}
        if manifest:
            selection = self.root / "selection.json"
            payload = manifest_override if manifest_override is not None else {
                "head": HEAD, "job": JOB,
                "selected": [{"id": fid, "specSelector": SPEC} for fid in selected],
            }
            selection.write_text(json.dumps(payload), encoding="utf-8")
            bounded_env["DEMO_SELECTION_FILE"] = str(selection)
        bounded_env.update(env or {})
        proc = subprocess.run([sys.executable, "-I", "-B", str(self.collector), str(report),
                               "synthetic", "synthetic", HEAD, "synthetic", JOB, str(out)],
                              cwd=self.root, env=bounded_env, capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, "Collector must produce structural reconciliation, not crash")
        row_file = out / "rows.jsonl"
        summary_file = out / "reconciliation.json"
        self.assertTrue(row_file.is_file(), "rows.jsonl is required")
        self.assertTrue(summary_file.is_file(), "reconciliation.json is required")
        rows = [json.loads(line) for line in row_file.read_text(encoding="utf-8").splitlines()]
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        retained = row_file.read_text(encoding="utf-8") + summary_file.read_text(encoding="utf-8") + proc.stdout + proc.stderr
        self.assertNotIn(CANARY, retained)
        for row in rows:
            self.assertEqual(row["head"], HEAD)
            self.assertEqual(row["job"], JOB)
            self.assertEqual(row["specSelector"], SPEC)
            self.assertEqual(row["admissionDisposition"], "UNKNOWN")
            self.assertIn(row["status"], ("PASS", "FAIL", "SKIP", None))
            self.assertIn("reasonCode", row)
            self.assertIn("evidenceSource", row)
            self.assertIn("selectionDisposition", row)
        self.assertEqual(summary["head"], HEAD)
        self.assertEqual(summary["job"], JOB)
        self.assertEqual(len(rows), len({row["id"] for row in rows}), "One row per selected ID")
        return {row["id"]: row for row in rows}, summary

    def test_pass_fail_and_observed_skip_keep_counts_and_bindings(self):
        rows, summary = self.collect([case(A), case(B, ("failed",)), case(C, ("skipped",), annotation=CANARY)], (A, B, C))
        self.assertEqual([rows[fid]["status"] for fid in (A, B, C)], ["PASS", "FAIL", "SKIP"])
        self.assertEqual(rows[C]["reasonCode"], "OBSERVED_SKIP_REASON_WITHHELD")
        self.assertEqual(rows[C]["evidenceSource"], "PLAYWRIGHT_JSON")
        self.assertEqual(summary["resultCounts"], {"passed": 1, "failed": 1, "skipped": 1})
        self.assertEqual(summary["coverage"], "COMPLETE")
        self.assertEqual(rows[A]["selectionDisposition"], "FROZEN_MANIFEST")

    def test_observed_skip_without_annotation_is_unavailable_not_profile(self):
        rows, _ = self.collect([case(A, ("skipped",))])
        self.assertEqual(rows[A]["status"], "SKIP")
        self.assertEqual(rows[A]["reasonCode"], "OBSERVED_SKIP_REASON_UNAVAILABLE")
        self.assertEqual(rows[A]["resultCounts"], {"skipped": 1})

    def test_selected_missing_id_is_null_incomplete_never_skip(self):
        rows, summary = self.collect([case(A)], (A, B))
        self.assertIsNone(rows[B]["status"])
        self.assertEqual(rows[B]["verdict"], "INCOMPLETE")
        self.assertEqual(rows[B]["reasonCode"], "SELECTED_ID_MISSING")
        self.assertEqual(summary["missingIds"], [B])
        self.assertEqual(summary["coverage"], "INCOMPLETE")
        self.assertNotIn("profile", rows[B].get("reason", ""))

    def test_no_results_does_not_use_expected_or_summary_test_status(self):
        spec = case(A, ())
        spec["tests"][0].update(status="expected", expectedStatus="skipped")
        rows, summary = self.collect([spec])
        self.assertIsNone(rows[A]["status"])
        self.assertEqual(rows[A]["reasonCode"], "RESULT_UNAVAILABLE")
        self.assertEqual(rows[A]["noResultCases"], 1)
        self.assertEqual(summary["incompleteIds"], [A])

    def test_missing_report_is_explicit_gap(self):
        rows, summary = self.collect(report_mode="missing")
        self.assertIsNone(rows[A]["status"])
        self.assertEqual(rows[A]["reasonCode"], "REPORT_UNAVAILABLE")
        self.assertIn("REPORT_UNAVAILABLE", summary["issues"])
        self.assertEqual(summary["coverage"], "INCOMPLETE")

    def test_malformed_report_is_explicit_gap_without_raw_text(self):
        rows, summary = self.collect(report_mode="invalid")
        self.assertIsNone(rows[A]["status"])
        self.assertIn("REPORT_UNAVAILABLE", summary["issues"])

    def test_nonobject_report_is_explicit_gap(self):
        rows, summary = self.collect(report_mode="nonobject")
        self.assertIsNone(rows[A]["status"])
        self.assertIn("REPORT_UNAVAILABLE", summary["issues"])

    def test_duplicate_selected_ids_are_discrepancy_not_duplicate_rows(self):
        rows, summary = self.collect([case(A)], (A, A))
        self.assertEqual(list(rows), [A])
        self.assertEqual(summary["duplicateSelectedIds"], [A])
        self.assertEqual(summary["coverage"], "INCOMPLETE")
        self.assertNotEqual(rows[A]["selectionDisposition"], "FROZEN_MANIFEST")

    def test_duplicate_cases_preserve_failure_before_last_pass(self):
        rows, summary = self.collect([case(A, ("failed",)), case(A)])
        self.assertEqual(rows[A]["status"], "FAIL")
        self.assertEqual(rows[A]["resultCounts"], {"failed": 1, "passed": 1})
        self.assertEqual(rows[A]["caseCount"], 2)
        self.assertEqual(summary["duplicateOutcomeIds"], [A])
        self.assertEqual(summary["coverage"], "INCOMPLETE")

    def test_duplicate_cases_keep_skip_and_pass_counts(self):
        rows, summary = self.collect([case(A, ("skipped",)), case(A)])
        self.assertEqual(rows[A]["resultCounts"], {"skipped": 1, "passed": 1})
        self.assertEqual(summary["resultCounts"], {"skipped": 1, "passed": 1})
        self.assertEqual(summary["duplicateOutcomeIds"], [A])

    def test_retry_fail_then_pass_does_not_erase_failure(self):
        rows, summary = self.collect([case(A, ("failed", "passed"))])
        self.assertEqual(rows[A]["status"], "FAIL")
        self.assertEqual(rows[A]["resultCounts"], {"failed": 1, "passed": 1})
        self.assertEqual(summary["multipleAttemptIds"], [A])

    def test_extra_id_outside_catalogue_is_reported_and_counted(self):
        rows, summary = self.collect([case(A), case(EXTRA, ("failed",))])
        self.assertEqual(list(rows), [A])
        self.assertEqual(summary["extraResultIds"], [EXTRA])
        self.assertEqual(summary["resultCounts"], {"passed": 1, "failed": 1})
        self.assertEqual(summary["coverage"], "INCOMPLETE")

    def test_manifest_identity_mismatch_is_not_frozen_attestation(self):
        payload = {"head": "0" * 40, "job": JOB + "-other", "selected": [{"id": A, "specSelector": SPEC}]}
        rows, summary = self.collect([case(A)], manifest_override=payload)
        self.assertIn("SELECTION_MANIFEST_INVALID", summary["issues"])
        self.assertEqual(summary["coverage"], "INCOMPLETE")
        self.assertNotEqual(rows[A]["selectionDisposition"], "FROZEN_MANIFEST")

    def test_manifest_unsafe_selector_is_withheld_and_invalid(self):
        payload = {"head": HEAD, "job": JOB, "selected": [{"id": A, "specSelector": "../" + CANARY}]}
        _, summary = self.collect([case(A)], manifest_override=payload)
        self.assertIn("SELECTION_MANIFEST_INVALID", summary["issues"])

    def test_manifest_unsafe_id_is_withheld_and_invalid(self):
        payload = {"head": HEAD, "job": JOB, "selected": [{"id": CANARY, "specSelector": SPEC}]}
        _, summary = self.collect([case(A)], manifest_override=payload)
        self.assertIn("SELECTION_MANIFEST_INVALID", summary["issues"])

    def test_legacy_selection_without_manifest_is_visible_but_unattested(self):
        rows, summary = self.collect([case(A)], manifest=False)
        self.assertEqual(rows[A]["status"], "PASS")
        self.assertEqual(rows[A]["selectionDisposition"], "UNATTESTED")
        self.assertIn("SELECTION_WITNESS_UNAVAILABLE", summary["issues"])
        self.assertEqual(summary["coverage"], "INCOMPLETE")

    def test_gateway_profile_does_not_become_admission_witness(self):
        rows, _ = self.collect([case(A)], env={"E2E_GATEWAY_PROFILE": "full"})
        self.assertEqual(rows[A]["admissionDisposition"], "UNKNOWN")

    def test_raw_error_cause_and_skip_annotation_are_not_retained(self):
        failed = case(A, ("failed",))
        failed["tests"][0]["results"][0].update(error={"message": CANARY, "cause": {"message": CANARY}},
                                               errors=[{"message": CANARY}])
        rows, _ = self.collect([failed, case(B, ("skipped",), annotation=CANARY)], (A, B))
        self.assertEqual(rows[A]["status"], "FAIL")
        self.assertEqual(rows[B]["status"], "SKIP")
        self.assertNotIn("failLine", rows[A])

    def test_unknown_result_status_is_not_a_pass_or_invented_failure(self):
        rows, summary = self.collect([case(A, (CANARY,))])
        self.assertIsNone(rows[A]["status"])
        self.assertEqual(rows[A]["resultCounts"], {"unknown": 1})
        self.assertEqual(summary["coverage"], "INCOMPLETE")

    def test_selected_manifest_inventory_overrides_legacy_ids(self):
        payload = {"head": HEAD, "job": JOB, "selected": [{"id": B, "specSelector": SPEC}]}
        rows, summary = self.collect([case(B)], manifest_override=payload)
        self.assertEqual(list(rows), [B])
        self.assertEqual(rows[B]["status"], "PASS")
        self.assertEqual(summary["selectedIds"], [B])

    def test_fixture_video_copy_is_preserved_for_executed_result(self):
        video = self.root / "synthetic-video.webm"
        video.write_bytes(b"synthetic media bytes; not a real recording")
        spec = case(A)
        spec["tests"][0]["results"][0]["attachments"] = [{"name": "video", "path": str(video)}]
        rows, _ = self.collect([spec])
        copied = self.root / "out" / "videos" / rows[A]["video"]
        self.assertEqual(copied.read_bytes(), video.read_bytes())
        self.assertEqual(rows[A]["videoSource"], "fixture")

    def test_unobserved_selected_row_never_gets_video(self):
        rows, _ = self.collect(selected=(A, B))
        self.assertTrue(all(not row.get("video") for row in rows.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
