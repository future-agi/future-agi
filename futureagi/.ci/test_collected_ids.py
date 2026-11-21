"""The record of what each shard collected, the comparison, and their workflow wiring."""

import contextlib
import importlib.util
import io
import pathlib
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
        self.assertIn("3 shards collected the same 2 tests", out)

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

    def test_an_id_every_shard_holds_twice_is_equal(self):
        record = ["aa a.py::one", "aa a.py::one"]
        self.assertEqual(self.check(2, record, record)[0], 0)
        self.assertEqual(self.check(2, record, record[:1])[0], 1)


@unittest.skipUnless(plugin, "the plugin needs pytest")
class RecordTest(unittest.TestCase):
    def test_the_record_is_sorted_and_tells_long_ids_apart(self):
        long = "a.py::case[" + "x" * 5000
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ids.txt"
            plugin.record(iter([long + "1]", "b.py::two", long + "2]"]), path)
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, sorted(lines))
        self.assertEqual(len(set(lines)), 3)
        self.assertTrue(all(len(line) <= 25 + plugin.SHOWN_ID_LENGTH for line in lines))


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

    def test_the_record_is_not_an_input_of_the_database_dump_key(self):
        # conftest.py and pytest.ini are key inputs; the plugin is loaded with -p instead.
        dump = load("db_dump")
        self.assertFalse(any("collected_ids" in spec for spec in dump.INPUTS))


if __name__ == "__main__":
    unittest.main()
