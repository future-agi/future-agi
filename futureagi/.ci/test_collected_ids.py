"""The record of what each shard collected, the comparison, and their workflow wiring."""

import contextlib
import importlib.util
import io
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

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


class WorkflowTest(unittest.TestCase):
    def test_every_shard_records_its_collection(self):
        shard = WORKFLOW.split("      - name: Run shard\n", 1)[1].split(
            "  saml-multiworker:\n", 1
        )[0]
        self.assertIn("          PYTHONPATH: .ci\n", shard)
        self.assertIn(
            "          COLLECTED_IDS_FILE: ${{ runner.temp }}/collected-ids.txt\n",
            shard,
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
        self.assertIn(
            'check_collected_ids.py "$(jq \'.include | length\' <<<"$SHARDS")"', job
        )
        gate = WORKFLOW.split("  backend-tests:\n", 1)[1]
        self.assertIn("        collection,\n", gate.split("    if: always()", 1)[0])

    @unittest.skipUnless(shutil.which("jq"), "needs jq")
    def test_the_gate_requires_the_check_whenever_the_shards_ran(self):
        # The general filter passes a skipped job; this one must not, for the collection check.
        gate = WORKFLOW.split("  backend-tests:\n", 1)[1]
        start = gate.index('          echo "$NEEDS" | jq \'if .test.result')
        end = gate.index("end'\n", start) + len("end'\n")
        script = "\n".join(line[10:] for line in gate[start:end].splitlines())

        def run(test, collection):
            needs = {"test": {"result": test}, "collection": {"result": collection}}
            return subprocess.run(
                ["bash", "-ec", script],
                env={
                    "NEEDS": json.dumps(needs),
                    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                },
                capture_output=True,
                text=True,
                check=False,
            ).returncode

        self.assertEqual(run("success", "success"), 0)
        self.assertEqual(run("skipped", "skipped"), 0)
        for test, collection in (
            ("success", "skipped"),
            ("success", "failure"),
            ("failure", "skipped"),
            ("failure", "cancelled"),
            ("success", "cancelled"),
        ):
            self.assertNotEqual(run(test, collection), 0, (test, collection))
        # A failed shard with a passed check is the general filter's business, not this one's.
        self.assertEqual(run("failure", "success"), 0)


if __name__ == "__main__":
    unittest.main()
