"""Negative tests for the proof checker, without a product stack."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parent
MUTATIONS = json.loads((TOOLS / "mutations.json").read_text())
FLOWS = ["EVAL-E2E-006", "EVAL-E2E-007", "EVAL-E2E-008", "EVAL-E2E-009",
         "EVAL-E2E-010", "OBS-E2E-052", "OBS-E2E-053"]


def report_for(run):
    targets = {flow: mutation for mutation in MUTATIONS["mutations"]
               if mutation["run"] == run for flow in mutation["flow"].split("+")}
    specs = []
    for flow in FLOWS:
        result = {"status": "passed", "duration": 1}
        test = {"status": "expected", "expectedStatus": "passed", "results": [result]}
        if flow in targets:
            mutation = targets[flow]
            test["status"] = "unexpected"
            loc = {"file": f"/runner/e2e/{mutation['file']}",
                   "line": mutation["fail_line"], "column": 4}
            frame = f"    at {loc['file']}:{loc['line']}:4"
            body = "\n".join(mutation["expect_msg"])
            result.update(status="failed", errorLocation=loc,
                          error={"message": body, "stack": f"Error: {body}\n{frame}", "location": loc},
                          errors=[{"message": f"{body}\n\n> {loc['line']} | {mutation['replace']}\n\n{frame}",
                                   "location": loc}])
        specs.append({"title": flow + ": proof target", "tests": [test]})
    return {"suites": [{"specs": specs}], "errors": []}


class CheckerTests(unittest.TestCase):
    def check(self, report, run):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "report.json", Path(temp) / "verdict.json"
            source.write_text(json.dumps(report))
            checked = subprocess.run([
                sys.executable, "-I", str(TOOLS / "check.py"),
                str(TOOLS / "mutations.json"), str(source), run, str(output),
            ], capture_output=True, text=True)
            self.assertTrue(output.exists(), checked.stderr)
            return checked.returncode, json.loads(output.read_text())["ok"]

    def test_complete_green_is_accepted(self):
        self.assertEqual(self.check(report_for("G0"), "G0"), (0, True))

    def test_exact_expected_mutations_are_accepted(self):
        self.assertEqual(self.check(report_for("P1"), "P1"), (0, True))

    def test_a_global_error_is_not_a_green_run(self):
        report = report_for("G0")
        report["errors"] = [{"message": "worker teardown failed"}]
        self.assertEqual(self.check(report, "G0"), (1, False))

    def test_a_global_error_is_not_a_mutation_proof(self):
        report = report_for("P1")
        report["errors"] = [{"message": "worker crashed"}]
        self.assertEqual(self.check(report, "P1"), (1, False))

    def test_a_missing_control_is_rejected(self):
        report = report_for("P2")
        report["suites"][0]["specs"].pop(1)
        self.assertEqual(self.check(report, "P2"), (1, False))

    def test_a_failure_at_another_assertion_is_rejected(self):
        report = report_for("P1")
        result = report["suites"][0]["specs"][0]["tests"][0]["results"][0]
        result["errorLocation"]["line"] = 199
        result["error"]["stack"] = result["error"]["stack"].replace(":200:4", ":199:4")
        self.assertEqual(self.check(report, "P1"), (1, False))

    def test_a_different_error_at_the_same_line_is_rejected(self):
        report = report_for("P1")
        error = report["suites"][0]["specs"][0]["tests"][0]["results"][0]["error"]
        error["message"] = "Connection refused"
        self.assertEqual(self.check(report, "P1"), (1, False))

    def test_code_frame_text_is_not_the_failure_message(self):
        report = report_for("P1")
        result = report["suites"][0]["specs"][3]["tests"][0]["results"][0]
        result["error"]["message"] = "Error: Timeout 10000ms exceeded."
        self.assertEqual(self.check(report, "P1"), (1, False))

    def test_skipped_xfailed_and_retried_tests_are_rejected(self):
        for kind in ("skipped", "xfail", "retry"):
            with self.subTest(kind=kind):
                report = report_for("G0")
                test = report["suites"][0]["specs"][0]["tests"][0]
                if kind == "skipped":
                    test["status"] = "skipped"
                elif kind == "xfail":
                    test["expectedStatus"] = "failed"
                else:
                    test["results"].append(copy.deepcopy(test["results"][0]))
                self.assertEqual(self.check(report, "G0"), (1, False))


if __name__ == "__main__":
    unittest.main()
