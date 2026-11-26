"""Negative controls for the proof boundary, separate from the frozen behavior tests."""

import copy
import os
import sys
import tempfile
import unittest
from functools import cache
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import case
import observe
import run

ROOT, TIP, HEAD, MERGE, TREE = (c * 40 for c in "abcde")
BINDING = "1" * 64


@cache
def pinned_body(arm):
    with tempfile.TemporaryDirectory() as tmp:
        _, body = observe.materialize(Path(tmp), arm, {})
    return body


def acceptance(arm="broken"):
    role = "acceptance"
    test_id = case.full_id(role)
    subject = case.SUBJECT[arm]
    c = dict(event="pull_request", base=ROOT, head=HEAD, tested_commit=MERGE, event_sha=MERGE,
             tested_tree=TREE, event_tree=TREE, parents=[TIP, HEAD], merge_base=ROOT, ancestor_rc=0,
             original_sha256=subject["bootstrap"], executed_sha256=subject["executed"],
             returncode=1, stderr_marker=[13, 1], plan=None)
    terminal = dict(id=test_id, status="failure", exception="AssertionError", oracle_frames=[[case.METHOD[role], 327]])
    calls = [c]
    if arm == "reference":
        c.update(returncode=0, stderr_marker=None, plan=dict(version=1, mode="qualified", event="pull_request",
                 policy_base=TIP, diff_base=ROOT, diff_head=HEAD, tested_commit=MERGE, tested_tree=TREE,
                 policy_sha256=case.POLICY, path_count=1))
        calls.extend([dict(c, base=TIP), dict(c, head=TIP, returncode=1, stderr_marker=[18, 1], plan=None),
                      dict(c, base="f" * 40, ancestor_rc=1, returncode=1, stderr_marker=[19, 1], plan=None)])
        terminal = dict(id=test_id, status="success")
    for call in calls:
        marker = call["stderr_marker"]
        stderr = b"" if marker is None else f"PILOT_ERR {marker[0]} {marker[1]}\n".encode()
        call["stderr_sha256"] = case.digest(stderr)
    receipt = dict(arm=arm, role=role, binding=BINDING, source=copy.deepcopy(dict(subject, oracle=case.ORACLE, policy=case.POLICY)),
                   bodies=dict(bootstrap=pinned_body(arm), executed=observe.PREFIX + pinned_body(arm)),
                   selected=[test_id], started=[test_id], stopped=[test_id], terminal=[terminal], calls=calls)
    return dict(receipt=receipt, timed_out=False, returncode=int(arm == "broken"), stderr_sha256=case.digest(b""))


class Grading(unittest.TestCase):
    def grade(self, raw, arm="broken", role="acceptance"):
        # Re-encode the mutated payload so a bad transport digest cannot mask a grader bug.
        raw["stdout_sha256"] = case.digest(case.encode(raw["receipt"]))
        return run.grade(arm, role, raw, BINDING)["outcome"]

    def test_specific_red_and_reference_matrix(self):
        self.assertEqual(self.grade(acceptance()), "expected-red")
        self.assertEqual(self.grade(acceptance("reference"), "reference"), "pass")
        self.assertEqual(self.grade(acceptance(), "reference"), "invalid")
        self.assertEqual(self.grade(acceptance(), role="preservation"), "invalid")

    def test_zero_missing_duplicate_wrong_ids(self):
        for field in ("selected", "started", "stopped", "terminal"):
            for count in (0, 2):
                with self.subTest(field=field, count=count):
                    raw = acceptance()
                    raw["receipt"][field] *= count
                    self.assertEqual(self.grade(raw), "invalid")
        for field in ("selected", "started", "stopped"):
            raw = acceptance()
            raw["receipt"][field] = [case.full_id("preservation")]
            self.assertEqual(self.grade(raw), "invalid")

    def test_unexpected_success_skips_setup_and_teardown(self):
        for arm in case.SUBJECT:
            for status in ("skip", "error", "expectedFailure", "unexpectedSuccess"):
                with self.subTest(arm=arm, status=status):
                    raw = acceptance(arm)
                    raw["receipt"]["terminal"][0]["status"] = status
                    self.assertEqual(self.grade(raw, arm), "invalid")
        raw = acceptance()
        raw["returncode"] = 0
        raw["receipt"]["terminal"] = [dict(id=case.full_id("acceptance"), status="success")]
        self.assertEqual(self.grade(raw), "invalid")
        for phase in ("setUp", "tearDown"):
            raw = acceptance()
            raw["receipt"]["terminal"][0]["oracle_frames"] = [[phase, 327]]
            self.assertEqual(self.grade(raw), "invalid")
        raw = acceptance("reference")
        raw["receipt"]["terminal"].append(dict(status="error", exception="RuntimeError", id=case.full_id("acceptance")))
        self.assertEqual(self.grade(raw, "reference"), "invalid")

    def test_generic_red_wrong_command_or_unrelated_assertion(self):
        for marker in (None, "other", [12, 1], [13, 127], [19, 1]):
            raw = acceptance()
            raw["receipt"]["calls"][0]["stderr_marker"] = marker
            self.assertEqual(self.grade(raw), "invalid")
        for key, value in (("id", case.full_id("preservation")), ("exception", "FileNotFoundError"),
                           ("oracle_frames", [[case.METHOD["acceptance"], 331]])):
            raw = acceptance()
            raw["receipt"]["terminal"][0][key] = value
            self.assertEqual(self.grade(raw), "invalid")

    def test_missing_stale_or_timed_out_receipt(self):
        for field, value in (("receipt", None), ("returncode", -9), ("timed_out", True)):
            self.assertEqual(self.grade(dict(acceptance(), **{field: value})), "invalid")
        raw = acceptance()
        raw["receipt"]["binding"] = "another attempt"
        self.assertEqual(self.grade(raw), "invalid")

    def test_source_pins_and_actual_executed_bytes(self):
        for field in ("oracle", "policy", "bootstrap", "executed", "commit", "tree", "workflows"):
            raw = acceptance()
            raw["receipt"]["source"][field] = None
            self.assertEqual(self.grade(raw), "invalid")
        for field in ("original_sha256", "executed_sha256"):
            raw = acceptance()
            raw["receipt"]["calls"][0][field] = "0" * 64
            self.assertEqual(self.grade(raw), "invalid")

    def test_recorded_script_bytes_are_pinned(self):
        for key in ("bootstrap", "executed"):
            raw = acceptance()
            raw["receipt"]["bodies"][key] += " "
            self.assertEqual(self.grade(raw), "invalid")

    def test_private_outer_requires_exact_clean_git_identity(self):
        env = dict(BASE_OID=ROOT, HEAD_OID=HEAD, HEAD_TREE=TREE)
        values = [f"{HEAD} {TREE} {TIP}".encode(), b"false\n", b"", ROOT.encode()]
        with patch.dict(os.environ, env, clear=True), patch.object(run, "git", side_effect=values):
            record = run.private_outer()
        self.assertEqual(set(record), {"event", "base", "tested_commit", "tested_tree", "parents", "execution_id"})
        self.assertEqual(record["event"], "local/private-preflight")
        for index, bad in enumerate((f"{TIP} {TREE} {ROOT}".encode(), b"true", b" M file", TIP.encode())):
            versions = values.copy()
            versions[index] = bad
            with self.subTest(index=index), patch.dict(os.environ, env, clear=True), patch.object(run, "git", side_effect=versions), self.assertRaises(case.Invalid):
                run.private_outer()
        with patch.dict(os.environ, dict(env, GITHUB_ACTIONS="true"), clear=True), self.assertRaises(case.Invalid):
            run.private_outer()

    def test_mismatched_git_blob_rejected_before_oracle_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, field in (("test_ci_scope.py", "ORACLE"), ("ci_scope.py", "POLICY")):
                with self.subTest(file=name), patch.object(case, field, "0" * 64):
                    with self.assertRaisesRegex(case.Invalid, "source digest: " + name):
                        observe.materialize(Path(tmp) / field, "broken", {})
            workflows = ["0" * 64, *case.SUBJECT["broken"]["workflows"][1:]]
            with patch.dict(case.SUBJECT["broken"], workflows=workflows):
                with self.assertRaisesRegex(case.Invalid, "source digest: backend-ci.yml"):
                    observe.materialize(Path(tmp) / "workflow", "broken", {})

    def test_wrong_command_even_with_consistent_diagnostic_digest(self):
        raw = acceptance()
        raw["receipt"]["calls"][0].update(stderr_marker=[2, 1], stderr_sha256=case.digest(b"PILOT_ERR 2 1\n"))
        self.assertEqual(self.grade(raw), "invalid")

    def test_not_the_stale_scenario(self):
        for field, value in (("ancestor_rc", 1), ("merge_base", MERGE), ("base", TIP),
                             ("parents", [HEAD, TIP]), ("event_sha", HEAD), ("tested_tree", ROOT)):
            raw = acceptance()
            raw["receipt"]["calls"][0][field] = value
            self.assertEqual(self.grade(raw), "invalid")

    def test_green_process_with_wrong_plan_or_wrong_negative_site(self):
        for field in ("policy_base", "diff_base", "diff_head", "tested_commit", "tested_tree", "policy_sha256", "path_count"):
            raw = acceptance("reference")
            raw["receipt"]["calls"][0]["plan"][field] = None
            self.assertEqual(self.grade(raw, "reference"), "invalid")
        raw = acceptance("reference")
        raw["receipt"]["calls"][3]["stderr_marker"] = [18, 1]
        self.assertEqual(self.grade(raw, "reference"), "invalid")

    def test_outer_merge_uses_first_parent_not_advertised_base(self):
        outer = dict(event="pull_request", pr=17, ref="refs/pull/17/merge", advertised_base=ROOT,
                     effective_base=TIP, head=HEAD, parents=[TIP, HEAD], event_sha=MERGE,
                     tested_commit=MERGE, tested_tree=TREE, ancestor_rc=0, run_id="21", run_attempt="2")
        case.check_outer(outer)
        for key, value in (("effective_base", ROOT), ("head", TIP), ("parents", [TIP]), ("ancestor_rc", 1),
                           ("event_sha", HEAD), ("tested_tree", ""), ("run_attempt", "0")):
            with self.subTest(key=key), self.assertRaises(case.Invalid):
                case.check_outer(dict(outer, **{key: value}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
