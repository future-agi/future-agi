"""The fresh-migrate input recorder: repo-relative paths only, no contents, never fails."""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "futureagi/.ci/migrate_inputs.py"
SPEC = importlib.util.spec_from_file_location("migrate_inputs_under_test", PLUGIN)
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)  # CI_MIGRATE_INPUTS unset: no hook in this process


class RecorderTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="migrate-inputs-")
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(os.path.realpath(tmp.name)) / "futureagi"
        for path in (
            "tracer/migrations/0001_initial.py",
            "tracer/fixtures/seed.json",
            "tracer/__pycache__/fields.cpython-312.pyc",
            ".venv/lib/django/db.py",
            "ee/cloud/billing/migrations/0001_initial.py",
        ):
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text("SECRET-CONTENT")
        self.recorder = plugin.Recorder(root=str(self.root))

    def opened(self, path):
        self.recorder.hook("open", (str(self.root / path), "r", 0))

    def test_paths_are_repo_relative_with_sources_for_bytecode(self):
        self.opened("tracer/fixtures/seed.json")
        self.opened("tracer/__pycache__/fields.cpython-312.pyc")
        # The temp file Python writes bytecode to, and pytest's rewritten copy.
        self.recorder.hook(
            "open",
            (
                str(
                    self.root
                    / "tracer/__pycache__/fields.cpython-312.pyc.140264605312480"
                ),
                "wb",
                0,
            ),
        )
        self.recorder.hook(
            "open",
            (
                str(
                    self.root
                    / "tracer/__pycache__/conftest.cpython-312-pytest-9.0.2.pyc.6888"
                ),
                "wb",
                0,
            ),
        )
        self.recorder.hook(
            "open", (str(self.root / ".pytest_cache/v/cache/nodeids"), "r", 0)
        )
        self.recorder.hook(
            "open",
            (os.fsencode(self.root / "tracer/migrations/0001_initial.py"), "r", 0),
        )
        self.recorder.hook("os.listdir", (str(self.root / "tracer/migrations"),))
        self.recorder.hook("os.scandir", (self.root / "tracer",))
        setup = self.recorder.report()["setup"]
        self.assertEqual(
            setup["opened"],
            [
                "tracer/conftest.py",
                "tracer/fields.py",
                "tracer/fixtures/seed.json",
                "tracer/migrations/0001_initial.py",
            ],
        )
        self.assertEqual(setup["listed"], ["tracer", "tracer/migrations"])

    def test_the_virtualenv_outside_paths_fds_and_other_events_are_not_recorded(self):
        self.opened(".venv/lib/django/db.py")
        self.recorder.hook("open", ("/etc/hosts", "r", 0))
        self.recorder.hook("open", (3, "r", 0))
        self.recorder.hook("open", (None, None, 0))
        self.recorder.hook("os.listdir", (None,))
        self.recorder.hook("compile", (b"x = 1", str(self.root / "tracer/x.py")))
        report = self.recorder.report()
        self.assertEqual(report["setup"]["opened"], [])
        self.assertEqual(report["setup"]["listed"], [])
        self.assertEqual(report["cost"]["audit_events"], 6)
        self.assertEqual(report["cost"]["hook_errors"], 0)

    def test_an_unreadable_path_is_counted_not_raised(self):
        self.recorder.hook("open", (object(), "r", 0))
        self.assertEqual(self.recorder.report()["cost"]["hook_errors"], 1)

    def test_the_private_overlay_is_counted_never_listed(self):
        self.opened("ee/cloud/billing/migrations/0001_initial.py")
        self.recorder.hook(
            "os.listdir", (str(self.root / "ee/cloud/billing/migrations"),)
        )
        report = self.recorder.report()
        self.assertEqual(report["setup"]["opened"], [])
        self.assertEqual(report["setup"]["listed"], [])
        self.assertEqual(
            report["setup"]["private_overlay_counts"], {"files": 1, "listed": 1}
        )
        self.assertNotIn("ee/cloud", json.dumps(report))

    def test_imported_modules_come_from_their_files_and_tests_are_separate(self):
        early = types.ModuleType("recorder_probe_early")
        early.__file__ = str(self.root / "tracer/migrations/0001_initial.py")
        late = types.ModuleType("recorder_probe_late")
        late.__file__ = str(self.root / "tracer/__pycache__/fields.cpython-312.pyc")
        sys.modules[early.__name__] = early
        self.addCleanup(sys.modules.pop, early.__name__, None)
        self.recorder.end_setup()
        sys.modules[late.__name__] = late
        self.addCleanup(sys.modules.pop, late.__name__, None)
        self.opened("tracer/fixtures/seed.json")  # after setup: the test's own read
        report = self.recorder.report()
        self.assertIn("tracer/migrations/0001_initial.py", report["setup"]["imported"])
        self.assertNotIn("tracer/fields.py", report["setup"]["imported"])
        self.assertEqual(report["setup"]["opened"], [])
        self.assertEqual(report["test_only_counts"], {"imported": 1, "opened": 1})

    def test_no_content_reaches_the_report(self):
        self.opened("tracer/fixtures/seed.json")
        report = json.dumps(self.recorder.report())
        self.assertNotIn("SECRET-CONTENT", report)
        self.assertEqual(
            set(json.loads(report)),
            {"schema", "root", "setup", "test_only_counts", "cost"},
        )

    def test_the_summary_counts_by_top_level_directory(self):
        self.opened("tracer/fixtures/seed.json")
        self.opened("tracer/migrations/0001_initial.py")
        text = plugin.summary(self.recorder.report())
        self.assertIn(
            "0 modules imported, 2 other files opened, 0 directories listed", text
        )
        self.assertIn("by top-level directory: tracer 2", text)


class LiveHookTest(unittest.TestCase):
    """The real audit hook, in its own process (a hook cannot be removed)."""

    def run_plugin(self, target):
        tmp = tempfile.TemporaryDirectory(prefix="migrate-inputs-live-")
        self.addCleanup(tmp.cleanup)
        summary = pathlib.Path(tmp.name) / "summary.md"
        script = textwrap.dedent(
            f"""
            import importlib.util, os
            spec = importlib.util.spec_from_file_location("migrate_inputs", {str(PLUGIN)!r})
            plugin = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(plugin)
            with open(os.path.join(plugin.ROOT, "pytest.ini"), encoding="utf-8") as handle:
                handle.read(1)
            os.listdir(os.path.join(plugin.ROOT, ".ci"))
            plugin.pytest_runtest_call(None)
            plugin.pytest_sessionfinish(None, 0)
            """
        )
        target = target or pathlib.Path(tmp.name) / "out"
        proc = subprocess.run(
            [sys.executable, "-I", "-c", script],
            env={
                **os.environ,
                "CI_MIGRATE_INPUTS": str(target),
                "GITHUB_STEP_SUMMARY": str(summary),
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc, pathlib.Path(target), summary

    def test_the_installed_hook_reports_what_the_process_touched(self):
        proc, target, summary = self.run_plugin(None)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("::warning", proc.stdout)
        report = json.loads((target / "migrate-inputs.json").read_text())
        self.assertIn("pytest.ini", report["setup"]["opened"])
        self.assertIn(".ci", report["setup"]["listed"])
        self.assertGreater(report["cost"]["audit_events"], 0)
        self.assertIn("recorder cost:", summary.read_text())

    def test_an_unwritable_report_only_warns(self):
        proc, _, _ = self.run_plugin(PLUGIN / "cannot-be-a-directory")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(
            "::warning title=Migrate inputs::could not write the report: ", proc.stdout
        )


class WiringTest(unittest.TestCase):
    def test_only_the_first_fresh_migrate_is_observed_and_the_key_is_untouched(self):
        actions = ROOT / ".github/actions/backend-test-packages/database"
        producer = (actions / "producer.sh").read_text()
        self.assertEqual(producer.count("-p migrate_inputs"), 1)
        first = producer.index("--create-db")
        self.assertIn(
            'CI_PRESEED_DIGEST="$fresh/preseed" CI_MIGRATE_INPUTS="$RUNNER_TEMP/migrate-inputs" PYTHONPATH=.ci \\\n'
            "  uv run pytest -p migrate_inputs .ci/migrated_db.py --create-db -q\n",
            producer,
        )
        self.assertLess(producer.index("-p migrate_inputs"), first)
        for name in ("key.sh", "verify-record.sh"):
            self.assertNotIn("migrate_inputs", (actions / name).read_text())
        self.assertNotIn(
            "migrate_inputs", (ROOT / "futureagi/.ci/db_dump.py").read_text()
        )

    def test_the_report_is_uploaded_and_can_never_fail_the_job(self):
        workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        job = workflow.split("\n  migrated-database:\n", 1)[1].split(
            "\n  agentic-eval:", 1
        )[0]
        step = job.split(
            "      - name: Upload the files the fresh migrate touched (observation only)\n",
            1,
        )[1].split("\n      - ", 1)[0]
        self.assertIn("name: migrate-inputs\n", step)
        self.assertIn("path: ${{ runner.temp }}/migrate-inputs/\n", step)
        self.assertIn("if-no-files-found: ignore\n", step)
        self.assertEqual(workflow.count("name: migrate-inputs\n"), 1)


if __name__ == "__main__":
    unittest.main()
