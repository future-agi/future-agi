"""The record of what each shard collected, the comparison, and their workflow wiring."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / "futureagi/.ci"
WORKFLOW = (ROOT / ".github/workflows/backend-ci.yml").read_text(encoding="utf-8")


def load(name):
    spec = importlib.util.spec_from_file_location(name, CI / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load("check_collected_ids")
try:
    plugin = load("collected_ids")
except ImportError:  # the plan job runs these tests before pytest is installed
    plugin = None


class CheckTest(unittest.TestCase):
    def check(self, expected, *records):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for number, lines in enumerate(records):
                path = pathlib.Path(directory) / f"record-{number}.txt"
                path.write_text(
                    "".join(line + "\n" for line in lines), encoding="utf-8"
                )
                paths.append(str(path))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = checker.main(["check", str(expected), *paths])
        return code, out.getvalue()

    def test_equal_records_pass(self):
        record = ["aa a.py::one", "bb a.py::two"]
        code, out = self.check(3, record, record, record)
        self.assertEqual(code, 0)
        self.assertIn("3 shards collected the same 2 tests in the same order", out)

    def test_equal_records_are_compared_by_digest_and_only_one_is_loaded(self):
        # 40 shards each upload a record of about 10 MB.
        record = [f"{n:02x} a.py::t{n}" for n in range(50)]
        loaded = []
        real = checker.read
        checker.read = lambda path: loaded.append(path) or real(path)
        try:
            code, out = self.check(4, record, record, record, record)
            self.assertEqual(code, 0)
            self.assertIn("4 shards collected the same 50 tests in the same order", out)
            self.assertEqual(len(loaded), 1)
            # Records that differ are all loaded, to name the difference.
            loaded.clear()
            code, out = self.check(3, record, record, record[:-1] + ["zz a.py::other"])
            self.assertEqual(code, 1)
            self.assertEqual(len(loaded), 3)
            self.assertIn("in 1 of 3 shards: a.py::other", out)
        finally:
            checker.read = real

    def test_a_difference_after_the_first_digest_block_fails(self):
        # The digest reads 1 MiB at a time; the only difference sits in the
        # second block of an otherwise equal record.
        record = [f"{n:08x} a.py::test_case_{n:06d}" for n in range(40000)]
        self.assertGreater(sum(len(line) + 1 for line in record), 1 << 20)
        late = list(record)
        late[-1] = late[-1][:9] + "a.py::test_other"
        code, out = self.check(3, record, record, late)
        self.assertEqual(code, 1)
        self.assertIn("in 1 of 3 shards: a.py::test_other", out)

    def test_the_same_tests_in_another_order_fail(self):
        # pytest-split breaks ties between equal times by position.
        code, out = self.check(
            2, ["aa a.py::one", "bb a.py::two"], ["bb a.py::two", "aa a.py::one"]
        )
        self.assertEqual(code, 1)
        self.assertIn("position 1: a.py::one", out)
        self.assertIn("in a different order", out)
        self.assertIn("re-run all jobs", out)

    def test_an_id_that_differs_between_shards_fails_and_is_named(self):
        code, out = self.check(
            2,
            ["aa a.py::one", "c1 a.py::case[5d96895e]"],
            ["aa a.py::one", "c2 a.py::case[caec3f69]"],
        )
        self.assertEqual(code, 1)
        self.assertIn("in 1 of 2 shards: a.py::case[5d96895e]", out)
        self.assertIn("in 1 of 2 shards: a.py::case[caec3f69]", out)
        self.assertNotIn("a.py::one", out)
        self.assertIn("::error title=Shards collected different tests::2 test ids", out)

    def test_a_missing_record_fails(self):
        record = ["aa a.py::one"]
        code, out = self.check(3, record, record)
        self.assertEqual(code, 1)
        self.assertIn("found 2 records for 3 shards", out)

    def test_one_shard_alone_proves_nothing(self):
        self.assertEqual(self.check(1, ["aa a.py::one"])[0], 1)

    def test_records_of_nothing_fail(self):
        self.assertEqual(self.check(2, [], [])[0], 1)
        # One shard with nothing is enough.
        code, out = self.check(2, ["aa a.py::one"], [])
        self.assertEqual(code, 1)
        self.assertIn("collected no test at all", out)

    def test_an_id_every_shard_holds_twice_is_equal(self):
        record = ["aa a.py::one", "aa a.py::one"]
        self.assertEqual(self.check(2, record, record)[0], 0)
        self.assertEqual(self.check(2, record, record[:1])[0], 1)


@unittest.skipUnless(plugin, "the plugin needs pytest")
class RecordTest(unittest.TestCase):
    def test_the_record_keeps_the_collection_order_and_tells_long_ids_apart(self):
        long = "a.py::case[" + "x" * 5000
        ids = [long + "1]", "b.py::two", long + "2]"]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ids.txt"
            plugin.record(iter(ids), path)
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            [line.partition(" ")[2] for line in lines],
            [i[: plugin.SHOWN_ID_LENGTH] for i in ids],
        )
        self.assertEqual(len(set(lines)), 3)
        self.assertTrue(all(len(line) <= 25 + plugin.SHOWN_ID_LENGTH for line in lines))

    def test_a_collection_of_nothing_fails_the_check(self):
        # What the plugin really writes for no test, through the real checker.
        with tempfile.TemporaryDirectory() as directory:
            paths = [str(pathlib.Path(directory) / f"ids-{n}.txt") for n in (1, 2)]
            for path in paths:
                plugin.record(iter([]), path)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(checker.main(["check", "2", *paths]), 1)


class TerminalTest(unittest.TestCase):
    def test_each_planned_id_needs_a_complete_protocol_and_permitted_outcome(self):
        expected = [["a", "test_a", "pass"]]
        phases = [["setup", "passed"], ["call", "passed"], ["teardown", "passed"]]
        row = ["a", "test_a", phases]
        checker.check_terminals(expected, [row])
        checker.check_terminals(
            [["a", "test_a", "skip"]],
            [["a", "test_a", [["setup", "declared-skip"], ["teardown", "passed"]]]],
        )
        for rows in (
            [],
            [row, row],
            [["b", "other", phases]],
            [["a", "test_a", phases[:-1]]],
            [["a", "test_a", [["setup", "declared-skip"], ["teardown", "passed"]]]],
            [["a", "test_a", [["setup", "passed"], ["teardown", "runtime-skip"]]]],
            [["a", "test_a", [["setup", "runtime-skip"], ["teardown", "failed"]]]],
        ):
            with self.assertRaises(ValueError):
                checker.check_terminals(expected, rows)

    def test_runtime_skip_reporting_never_waives_parity(self):
        expected = [["a", "test_a", "pass"]]
        row = [
            "a",
            "test_a",
            [
                ["setup", "passed"],
                ["call", "runtime-skip", "missing service"],
                ["teardown", "passed"],
            ],
        ]
        for inventory, rows in (
            (expected, [row, row]),
            (expected + [["b", "test_b", "pass"]], [row]),
            (expected, [["other", *row[1:]]]),
        ):
            with self.assertRaises(ValueError):
                checker.check_terminals(inventory, rows)
        with tempfile.TemporaryDirectory() as temp:
            for enforce in (True, False):
                with self.subTest(enforce=enforce):
                    out = io.StringIO()
                    report = pathlib.Path(temp) / "skips.json"
                    summary = pathlib.Path(temp) / f"summary-{enforce}"
                    checker.check_terminals(expected, [row])
                    skips = [
                        dict(suite="main", group=3, **skip)
                        for skip in checker.runtime_skips([row])
                    ]
                    with (
                        contextlib.redirect_stdout(out),
                        (
                            self.assertRaisesRegex(ValueError, "1 eligible")
                            if enforce
                            else contextlib.nullcontext()
                        ),
                    ):
                        checker.report_runtime_skips(
                            skips,
                            enforce=enforce,
                            partial=False,
                            report_path=report,
                            summary_path=summary,
                        )
                    self.assertIn("::warning", out.getvalue())
                    self.assertIn("test_a: call: missing service", out.getvalue())
                    self.assertIn("| main | 3 | 1 |", summary.read_text())
                    self.assertEqual(
                        json.loads(report.read_text()),
                        {
                            "enforced": enforce,
                            "partial": False,
                            "tests": [
                                {
                                    "suite": "main",
                                    "group": 3,
                                    "id": "a",
                                    "test": "test_a",
                                    "skips": [
                                        {"phase": "call", "reason": "missing service"}
                                    ],
                                }
                            ],
                            "allowed": [],
                        },
                    )


class RuntimeSkipRulesTest(unittest.TestCase):
    """runtime-skips.json: the expected backend run-time skips, as rules."""

    MEASURED = json.loads((CI / "fixtures/runtime-skips-measured.json").read_text())

    def rows(self, pairs, runs="internal"):
        # The measured run was an internal one (the private package checked out).
        out = []
        for number, (path, reason) in enumerate(pairs):
            test = f"{path}::test_{number}"
            out.append(
                {
                    "suite": "main",
                    "group": 1,
                    "runs": runs,
                    "id": f"id-{number}",
                    "test": test,
                    "inventory_test": test,
                    "skips": [{"phase": "setup", "reason": reason}],
                }
            )
        return out

    def measured(self, needle):
        return next(
            p for p in self.MEASURED["pairs"] if needle in p["file"] + p["reason"]
        )

    def report(self, rows, rules=None):
        with tempfile.TemporaryDirectory() as temp:
            out = io.StringIO()
            report = pathlib.Path(temp) / "skips.json"
            summary = pathlib.Path(temp) / "summary.md"
            with contextlib.redirect_stdout(out):
                try:
                    checker.report_runtime_skips(
                        rows,
                        enforce=True,
                        partial=False,
                        report_path=report,
                        summary_path=summary,
                        rules=rules,
                    )
                    error = None
                except ValueError as exc:
                    error = str(exc)
            return error, json.loads(report.read_text()), out.getvalue()

    def measured_rows(self):
        return self.rows(
            [(pair["file"], pair["reason"]) for pair in self.MEASURED["pairs"]]
        )

    def test_enforced_the_measured_skips_all_pass(self):
        # Every (file, reason) pair of a real Backend CI run on 22 Nov.
        self.assertEqual(self.MEASURED["total"], 1811)
        error, report, _ = self.report(self.measured_rows())
        self.assertIsNone(error)
        self.assertEqual(report["tests"], [])
        self.assertEqual(len(report["allowed"]), len(self.MEASURED["pairs"]))

    def test_the_two_opt_in_rules_cover_what_was_measured(self):
        rules = checker.read_runtime_skip_rules()
        counts = [0] * len(rules)
        for pair, row in zip(self.MEASURED["pairs"], self.measured_rows(), strict=True):
            counts[checker.allowing_rule(row, rules)] += pair["tests"]
        self.assertEqual(counts[:2], [1462, 327])
        self.assertEqual(sum(counts[2:]), 22)
        # The measured run was internal, so the fork-only rule is not used there.
        internal = [
            count
            for count, rule in zip(counts, rules, strict=True)
            if rule.get("runs") != "oss"
        ]
        self.assertTrue(all(internal), "a rule matches nothing that was measured")

    def test_a_new_skip_in_a_file_no_rule_covers_fails(self):
        rows = self.measured_rows() + self.rows(
            [
                (
                    "model_hub/tests/test_new_feature.py",
                    "Skipped: could not import 'chdb': No module named 'chdb'",
                )
            ]
        )
        error, report, out = self.report(rows)
        self.assertEqual(error, "1 eligible tests reported runtime skips")
        self.assertEqual(len(report["tests"]), 1)
        self.assertIn("model_hub/tests/test_new_feature.py", out)

    def test_a_new_reason_in_a_covered_file_fails(self):
        # The file is covered by a rule, but not this reason: a rule must not
        # swallow an unrelated skip.
        rows = self.measured_rows() + self.rows(
            [
                (
                    "tracer/tests/test_relational_filter_native.py",
                    "Skipped: database is not reachable",
                )
            ]
        )
        error, report, _ = self.report(rows)
        self.assertEqual(error, "1 eligible tests reported runtime skips")
        self.assertEqual(
            report["tests"][0]["test"].split("::")[0],
            "tracer/tests/test_relational_filter_native.py",
        )

    def test_every_skip_of_a_test_must_be_covered(self):
        row = self.rows(
            [
                (
                    "tracer/tests/test_relational_filter_native.py",
                    "Skipped: could not import 'chdb': x",
                )
            ]
        )[0]
        row["skips"].append({"phase": "call", "reason": "Skipped: something else"})
        self.assertIsNone(checker.allowing_rule(row, checker.read_runtime_skip_rules()))

    def test_only_the_measured_model_type_400_is_allowed(self):
        rules = checker.read_runtime_skip_rules()
        pair = self.measured("huggingface")
        row = self.rows([(pair["file"], pair["reason"])])[0]
        self.assertIsNotNone(checker.allowing_rule(row, rules))
        for other in (
            pair["reason"].replace("model_type", "huggingface_dataset_split"),
            "Skipped: API returned 400: {'status': False, 'type': 'validation_error'}",
        ):
            row = self.rows([(pair["file"], other)])[0]
            self.assertIsNone(checker.allowing_rule(row, rules), other)

    def test_a_missing_module_rule_names_the_exact_module(self):
        rules = checker.read_runtime_skip_rules()
        pair = self.measured("dunning")
        for reason in (
            pair["reason"].replace(
                "No module named 'ee.cloud.billing.dunning'", "No module named 'stripe'"
            ),
            "Skipped: could not import 'ee.cloud.billing.invoice': No module named 'ee.cloud.billing.invoice'",
        ):
            self.assertIsNone(
                checker.allowing_rule(self.rows([(pair["file"], reason)])[0], rules),
                reason,
            )
        harness = self.measured("fi.alk")
        other = harness["reason"].replace(
            "No module named 'fi'", "No module named 'yaml'"
        )
        self.assertIsNone(
            checker.allowing_rule(self.rows([(harness["file"], other)])[0], rules)
        )

    def test_a_fork_skip_is_by_design_and_an_internal_one_needs_its_ticket(self):
        rules = checker.read_runtime_skip_rules()
        pair = self.measured("dunning")
        fork = checker.allowing_rule(
            self.rows([(pair["file"], pair["reason"])], runs="oss")[0], rules
        )
        internal = checker.allowing_rule(
            self.rows([(pair["file"], pair["reason"])], runs="internal")[0], rules
        )
        self.assertEqual(rules[fork].get("runs"), "oss")
        self.assertNotIn("issue", rules[fork])
        self.assertEqual(rules[internal].get("runs"), "internal")
        self.assertEqual(rules[internal]["issue"], "TH-10618")
        # Without the ticketed internal rule, an internal run is not waived.
        fork_only = [rule for rule in rules if rule.get("runs") != "internal"]
        self.assertIsNone(
            checker.allowing_rule(
                self.rows([(pair["file"], pair["reason"])], runs="internal")[0],
                fork_only,
            )
        )

    def test_a_skip_is_allowed_only_for_the_path_the_inventory_recorded(self):
        rules = checker.read_runtime_skip_rules()
        pair = self.measured("test_relational_filter_native")
        row = self.rows([(pair["file"], pair["reason"])])[0]
        row.pop("inventory_test")
        self.assertIsNone(checker.allowing_rule(row, rules))
        # The terminal row's own label does not decide: the inventory's path does.
        row["test"] = pair["file"] + "::test_x"
        checker.bind_to_inventory(
            [row],
            {"main": [[row["id"], "model_hub/tests/test_other.py::test_x", "pass"]]},
        )
        self.assertIsNone(checker.allowing_rule(row, rules))
        checker.bind_to_inventory(
            [row], {"main": [[row["id"], pair["file"] + "::test_x", "pass"]]}
        )
        self.assertIsNotNone(checker.allowing_rule(row, rules))

    def test_usage_is_counted_per_rule_and_annotations_are_escaped(self):
        rules = checker.read_runtime_skip_rules(
            self.rule_file(
                [
                    {
                        "files": "a/tests/*.py",
                        "reason": "x",
                        "why": "Optional by design: same text",
                    },
                    {
                        "files": "b/tests/*.py",
                        "reason": "x",
                        "why": "Optional by design: same text",
                    },
                    {
                        "files": "c/tests/*.py",
                        "reason": "x",
                        "why": "Optional by design: 100%\nsecond line",
                    },
                ]
            )
        )
        error, report, out = self.report(
            self.rows([("a/tests/test_a.py", "x")]), rules=rules
        )
        self.assertIsNone(error)
        stale = [line for line in out.splitlines() if "rule matched nothing" in line]
        self.assertEqual(len(stale), 2)
        self.assertIn("b/tests/*.py", stale[0])
        self.assertIn("100%25%0Asecond line", stale[1])

    def test_a_rule_for_another_kind_of_run_is_not_called_stale(self):
        rules = checker.read_runtime_skip_rules(
            self.rule_file(
                [
                    {"files": "a/tests/*.py", "reason": "x", "why": "by design"},
                    {
                        "files": "b/tests/*.py",
                        "reason": "x",
                        "why": "by design",
                        "runs": "oss",
                    },
                ]
            )
        )
        rows = self.rows([("a/tests/test_a.py", "x")])
        for runs, warned in (
            (("internal",), False),
            (("oss",), True),
            (("internal", "oss"), True),
        ):
            with tempfile.TemporaryDirectory() as temp:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    checker.report_runtime_skips(
                        [dict(row, runs=runs[0]) for row in rows],
                        enforce=True,
                        partial=False,
                        report_path=pathlib.Path(temp) / "r.json",
                        summary_path=pathlib.Path(temp) / "s.md",
                        rules=rules,
                        runs=runs,
                    )
            self.assertEqual("b/tests/*.py" in out.getvalue(), warned, runs)

    def test_the_summary_shows_allowed_and_unexpected_counts(self):
        rows = self.measured_rows() + self.rows(
            [("model_hub/tests/test_new.py", "Skipped: new")]
        )
        with tempfile.TemporaryDirectory() as temp:
            summary = pathlib.Path(temp) / "summary.md"
            with contextlib.redirect_stdout(io.StringIO()):
                checker.report_runtime_skips(
                    rows,
                    enforce=False,
                    partial=False,
                    report_path=pathlib.Path(temp) / "r.json",
                    summary_path=summary,
                )
            text = summary.read_text()
        allowed = len(self.MEASURED["pairs"])
        self.assertIn(
            f"Backend runtime skips: {allowed + 1} ({allowed} allowed, 1 unexpected)",
            text,
        )

    def rule_file(self, rules):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = pathlib.Path(directory.name) / "rules.json"
        path.write_text(json.dumps(rules))
        return path

    def test_a_rule_that_matches_nothing_is_named(self):
        error, _, out = self.report([], rules=checker.read_runtime_skip_rules())
        self.assertIsNone(error)
        self.assertIn(
            "::warning title=Backend run-time skip rule matched nothing::", out
        )

    def test_the_rule_file_is_strict(self):
        good = {"files": "a/tests/*.py", "reason": "x", "why": "Optional by design: y"}
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "rules.json"
            for bad in (
                "{",
                "{}",
                json.dumps([{**good, "why": " "}]),
                json.dumps([{"reason": "x", "why": "by design"}]),
                json.dumps([{**good, "why": "flaky"}]),
                json.dumps([{**good, "reason": "("}]),
            ):
                path.write_text(bad)
                with self.assertRaises((ValueError, re.error), msg=bad):
                    checker.read_runtime_skip_rules(path)
            for bad in (
                json.dumps([{**good, "why": "flaky", "issue": "  "}]),
                json.dumps([{**good, "issue": 7}]),
                json.dumps([{**good, "runs": "both"}]),
            ):
                path.write_text(bad)
                with self.assertRaises(ValueError, msg=bad):
                    checker.read_runtime_skip_rules(path)
            path.write_text(
                json.dumps([good, {**good, "why": "flaky", "issue": "TH-1"}])
            )
            self.assertEqual(len(checker.read_runtime_skip_rules(path)), 2)


class ProducerIdentityTest(unittest.TestCase):
    def test_partial_rerun_needs_the_latest_success_for_each_leg(self):
        matrix = json.dumps(
            {
                "include": [
                    {"suite": "main", "name": "pytest", "group": n} for n in (1, 2)
                ]
            }
        )
        temp = self.enterContext(tempfile.TemporaryDirectory())
        env = {
            "RUNNER_TEMP": temp,
            "GITHUB_STEP_SUMMARY": str(pathlib.Path(temp) / "summary"),
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_RUN_ID": "100",
            "GITHUB_REPOSITORY": "fixture/repo",
            "GITHUB_SHA": "c" * 40,
            "SHARDS": matrix,
        }
        common = {
            "sha": env["GITHUB_SHA"],
            "run": "100",
            "suite": "main",
            "ee": "oss",
            "db": "schema",
            "durations": "0" * 64,
            "temporal": "1" * 64,
            "matrix": hashlib.sha256(matrix.encode()).hexdigest(),
        }
        jobs = [
            {
                "name": f"pytest shard {group}",
                "run_attempt": attempt,
                "conclusion": "success",
            }
            for group, attempt in ((1, 1), (2, 1), (2, 2))
        ]
        artifacts = [
            {"name": f"collected-ids-main-{n}", "id": n, "digest": "sha256:fixture"}
            for n in (1, 2)
        ]
        receipts = [dict(common, group=n, attempt=n) for n in (1, 2)]
        rows = [
            [
                str(n),
                f"test_{n}",
                [["setup", "passed"], ["call", "passed"], ["teardown", "passed"]],
            ]
            for n in (1, 2)
        ]

        def artifact(a, repo):
            n = a["id"]
            return (
                receipts[n - 1],
                [rows[n - 1]],
                "same-order",
                [r[:2] + ["pass"] for r in rows] if n == 1 else None,
            )

        with (
            patch.dict(os.environ, env),
            patch.object(
                checker,
                "pages",
                side_effect=lambda _, field: jobs if field == "jobs" else artifacts,
            ),
            patch.object(checker, "verify_artifact", side_effect=artifact),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            checker.verify_run()
            with patch.dict(os.environ, GITHUB_EVENT_NAME="merge_group"):
                with self.assertRaisesRegex(ValueError, "missing producer"):
                    checker.verify_run()
                for job in jobs:
                    job["name"] = "backend / " + job["name"]
                checker.verify_run()
                for job in jobs:
                    job["name"] = job["name"].removeprefix("backend / ")
            for field, invalid in (
                ("attempt", 1),
                ("sha", "stale"),
                ("ee", "f" * 40),
                ("temporal", ""),
                ("matrix", "other"),
            ):
                with (
                    self.subTest(field=field),
                    patch.dict(receipts[1], {field: invalid}),
                    self.assertRaises(ValueError),
                ):
                    checker.verify_run()
            # Failure in the first leg must not discard or misattribute the
            # other producer's authenticated evidence, even in enforced mode.
            rows[1][2][1] = ["call", "runtime-skip", "missing service"]
            report = pathlib.Path(temp) / "backend-runtime-skips.json"
            for cause, change in (
                (
                    "unsuccessful producer for main/1",
                    patch.dict(jobs[0], conclusion="failure"),
                ),
                (
                    "unsuccessful producer for main/2",
                    patch.dict(jobs[-1], conclusion="failure"),
                ),
                ("shards disagree", patch.dict(receipts[0], ee="f" * 40)),
            ):
                with (
                    self.subTest(cause=cause),
                    change,
                    patch.dict(os.environ, BACKEND_ENFORCE_RUNTIME_SKIPS="true"),
                    contextlib.redirect_stdout(io.StringIO()) as output,
                ):
                    with self.assertRaisesRegex(ValueError, cause):
                        checker.verify_run()
                    evidence = json.loads(report.read_text())
                    self.assertTrue(evidence["partial"])
                    self.assertEqual(
                        [(s["id"], s["group"]) for s in evidence["tests"]], [("2", 2)]
                    )
                    self.assertIn(
                        "(partial evidence)::main/2 test_2: call: missing service",
                        output.getvalue(),
                    )
                    self.assertIn(
                        "Partial evidence only",
                        pathlib.Path(env["GITHUB_STEP_SUMMARY"]).read_text(),
                    )
            artifacts.pop()
            with self.assertRaises(ValueError):
                checker.verify_run()

    def test_failed_inventory_cannot_qualify_even_when_it_wrote_json(self):
        for status in (None, b"1\n", b"0\n"):
            with self.subTest(status=status):
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, "w") as archive:
                    archive.writestr("receipt.json", '{"group":1,"suite":"main"}')
                    archive.writestr("terminal.jsonl", "[]\n")
                    archive.writestr("collected-ids.txt", "some-id\n")
                    archive.writestr("eligible.json", "[]")
                    if status is not None:
                        archive.writestr("inventory-exit.txt", status)
                raw = stream.getvalue()
                artifact = {
                    "id": 1,
                    "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                }
                with patch.object(checker.subprocess, "check_output", return_value=raw):
                    if status == b"0\n":
                        self.assertEqual(
                            checker.verify_artifact(artifact, "repo")[3], []
                        )
                    else:
                        with self.assertRaises((ValueError, KeyError)):
                            checker.verify_artifact(artifact, "repo")

    def test_raw_archive_digest_is_mandatory(self):
        with (
            patch.object(
                checker.subprocess, "check_output", return_value=b"wrong bytes"
            ),
            self.assertRaisesRegex(ValueError, "digest"),
        ):
            checker.verify_artifact({"id": 1, "digest": "sha256:other"}, "fixture/repo")


@unittest.skipUnless(plugin, "run with the shard pytest installation")
class PluginProtocolTest(unittest.TestCase):
    def pytest_run(self, root, *args, env=None, check=True):
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "collected_ids", *args],
            cwd=root,
            env={
                **os.environ,
                "PYTHONPATH": str(CI),
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTEST_ADDOPTS": "",
                **(env or {}),
            },
            check=check,
            capture_output=True,
            text=True,
        )

    def test_runtime_skip_differs_from_a_declared_skip_in_real_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "test_outcome.py").write_text(
                textwrap.dedent("""\
                    import pytest
                    import unittest

                    @pytest.mark.skip(reason="declared")
                    def test_declared(): pass

                    def test_runtime(): pytest.skip("missing service")

                    @pytest.fixture
                    def engine():
                        # Pytest reports fixture skips at the item location too.
                        pytest.importorskip("module_that_is_missing_service")

                    def test_fixture_runtime(engine): pass

                    @pytest.mark.skipif(False, reason="never")
                    def test_false_condition(): pass

                    class Cases(unittest.TestCase):
                        def test_ran(self):
                            for value in (1, 2):
                                with self.subTest(value=value):
                                    self.assertGreater(value, 0)

                        def test_subskip(self):
                            with self.subTest():
                                self.skipTest("missing service")
                    """)
            )
            inventory = root / "expected.json"
            receipt = root / "terminal.jsonl"
            # Simulate a self-test running inside the main shard. None of its
            # pytest subprocesses may write to any inherited receipt path.
            outer = root / "outer-receipt"
            outer.write_text("outer")
            inherited = dict.fromkeys(
                ("COLLECTED_IDS_FILE", "TERMINAL_IDS_FILE", "ELIGIBLE_IDS_FILE"),
                str(outer),
            )
            self.pytest_run(
                root, "--collect-only", f"--ci-eligible-ids={inventory}", env=inherited
            )
            self.pytest_run(root, f"--ci-terminal-ids={receipt}", env=inherited)
            self.assertEqual(outer.read_text(), "outer")
            rows = [json.loads(line) for line in receipt.read_text().splitlines()]
            expected = json.loads(inventory.read_text())
            self.assertEqual(len(expected), 6)
            checker.check_terminals(expected, rows)
            skips = checker.runtime_skips(rows)
            self.assertEqual(
                {row["test"].split("::")[-1] for row in skips},
                {"test_runtime", "test_fixture_runtime", "test_subskip"},
            )
            for row in skips:
                self.assertIn("missing", str(row["skips"]))

    def test_only_the_excluded_examples_may_take_their_declared_collection_skip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "test_other.py").write_text("def test_other(): pass")
            # Use only the import-time guard, never the live LLM examples.
            example = root / "ee/experiments/src/agent/test_flow.py"
            example.parent.mkdir(parents=True)
            example.write_text(
                "import pytest\n"
                "pytestmark = [pytest.mark.external, pytest.mark.live_llm]\n"
                "pytest.skip('Experimental live LLM example; set RUN_EXPERIMENT_EXAMPLES=1 to run.', allow_module_level=True)\n"
            )
            inventory = root / "expected.json"

            def collect(markers):
                inventory.unlink(missing_ok=True)
                return self.pytest_run(
                    root,
                    "--collect-only",
                    "-m",
                    markers,
                    f"--ci-eligible-ids={inventory}",
                    env={"RUN_EXPERIMENT_EXAMPLES": "0"},
                    check=False,
                )

            result = collect("not live_llm")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                [r[1] for r in json.loads(inventory.read_text())],
                ["test_other.py::test_other"],
            )

            def rejected(markers):
                result = collect(markers)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(
                    "unexpected collection skip:", result.stdout + result.stderr
                )
                self.assertFalse(inventory.exists())

            rejected("live_llm")
            shutil.copyfile(example, root / "test_unrelated.py")
            rejected("not live_llm")
            (root / "test_unrelated.py").unlink()
            # Same path and opt-out reason, but -m now selects the module.
            example.write_text(
                example.read_text().replace(", pytest.mark.live_llm", "")
            )
            rejected("not live_llm")
            example.write_text(
                "import pytest\npytest.skip('missing service', allow_module_level=True)\n"
            )
            rejected("not live_llm")


class WorkflowTest(unittest.TestCase):
    def test_every_shard_records_its_collection(self):
        shard = WORKFLOW.split("      - name: Run shard\n", 1)[1].split(
            "  saml-multiworker:\n", 1
        )[0]
        self.assertIn("          PYTHONPATH: .ci\n", shard)
        self.assertIn(
            '--ci-collected-ids="$RUNNER_TEMP/shard-receipt/collected-ids.txt"', shard
        )
        self.assertIn(
            '--ci-terminal-ids="$RUNNER_TEMP/shard-receipt/terminal.jsonl"', shard
        )
        inventory = WORKFLOW.split(
            "      - name: Collect the eligible inventory without sharding\n", 1
        )[1].split("      - name:", 1)[0]
        self.assertIn(
            '--ci-eligible-ids="$RUNNER_TEMP/shard-receipt/eligible.json"', inventory
        )
        self.assertIn(" -p collected_ids ", shard)
        self.assertIn(
            "          name: collected-ids-${{ matrix.suite }}-${{ matrix.group }}\n",
            shard,
        )

    def test_the_comparison_runs_whenever_the_shards_ran_and_gates_the_merge(self):
        job = WORKFLOW.split("  collection:\n", 1)[1].split("  backend-tests:\n", 1)[0]
        self.assertIn("    needs: [changes, test]\n", job)
        # Never skipped while the shards ran: the gate passes a skipped job.
        self.assertIn(
            "    if: ${{ !cancelled() && needs.test.result != 'skipped' }}\n", job
        )
        self.assertIn('"$RUNNER_TEMP"/collected/*/collected-ids.txt', job)
        strict = job.split("  terminal-parity:\n", 1)[1]
        self.assertIn("check_collected_ids.py --terminal", strict)
        self.assertIn(
            "BACKEND_ENFORCE_RUNTIME_SKIPS: ${{ vars.BACKEND_ENFORCE_RUNTIME_SKIPS }}",
            strict,
        )
        self.assertIn("name: backend-runtime-skips", strict)
        self.assertIn(
            "if-no-files-found: ${{ steps.qualification.outcome == 'success' && 'error' || 'ignore' }}",
            strict,
        )
        self.assertIn("SHARDS: ${{ needs.changes.outputs.shards }}", job)
        gate = WORKFLOW.split("  backend-tests:\n", 1)[1]
        self.assertIn("        collection,\n", gate.split("    if:", 1)[0])

    @unittest.skipUnless(shutil.which("jq"), "needs jq")
    def test_the_gate_requires_the_check_whenever_the_shards_ran(self):
        gate = WORKFLOW.split("  backend-tests:\n", 1)[1]
        script = textwrap.dedent(gate.split("        run: |\n", 1)[1])
        jobs = (
            "plan",
            "test",
            "collection",
            "terminal-parity",
            "saml-multiworker",
            "migrated-database",
            "agentic-eval",
            "slim-smoke",
        )
        for selected in (True, False):
            value = str(selected).lower()
            wanted = "success" if selected else "skipped"
            needs = {job: {"result": wanted} for job in jobs}
            needs.update(
                {
                    "changes": {
                        "result": "success",
                        "outputs": {
                            "backend": value,
                            "metrics": value,
                            "executor_base": "false",
                            "update_safety": "true",
                        },
                    },
                    "policy-self-test": {"result": "success"},
                    "update-safety": {"result": "success"},
                    "code-executor-base-pin": {"result": "skipped"},
                }
            )

            def passed(needs=needs):
                return (
                    subprocess.run(
                        ["bash", "-ec", script],
                        env={**os.environ, "NEEDS": json.dumps(needs)},
                        capture_output=True,
                    ).returncode
                    == 0
                )

            self.assertTrue(passed())
            for job in list(needs):
                previous = needs[job]["result"]
                for wrong in {"skipped", "cancelled", "success", "failure"} - {
                    previous
                }:
                    needs[job]["result"] = wrong
                    self.assertFalse(passed(), (job, wrong))
                needs[job]["result"] = previous
            for flag in needs["changes"]["outputs"]:
                previous = needs["changes"]["outputs"][flag]
                needs["changes"]["outputs"][flag] = ""
                self.assertFalse(passed(), flag)
                needs["changes"]["outputs"][flag] = previous


if __name__ == "__main__":
    unittest.main()
