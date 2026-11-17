"""bin/update apply runs the harness versions backfill once after a manual update (deploy/update/apply.py).

On the fake Installation of test_update_apply.py (imported, not changed). Each
result state is worded as ./bin/install words it, and none changes the
apply's result, its receipt or the journal. A staged run, a dry run and a
scheduled (guarded or automatic) run never start it. The README names the
same one-shot.
"""

from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import test_update_apply as fixtures  # noqa: E402  (puts the checkout on sys.path)
from deploy.update.apply import Updater  # noqa: E402
from deploy.update.install import CommandResult, InstallError  # noqa: E402

README = fixtures.ROOT / "deploy" / "update" / "README.md"
# The operator one-shot: the two variables the startup guard authorizes, nothing else.
OPERATOR = ("exec", "-T", "-e", "SERVICE_TYPE=bootstrap", "-e", "STARTUP_DB_MUTATION_MODE=operator")
COMMAND = ("python", "manage.py", "harness_versions_backfill", "--auto")
RERUN = ("Run it again with: docker compose exec -T -e SERVICE_TYPE=bootstrap"
         " -e STARTUP_DB_MUTATION_MODE=operator app python manage.py harness_versions_backfill --auto")
LINE = ("Harness versions backfill: state={} total=4 processed={} succeeded={} failed={} skipped=0 "
        "busy_organizations=0\n")
# The one-shot's other output: stdout detail, an earlier heartbeat, stderr.
UNPRINTED = ("STUB-DETAIL", "STUB-STDERR", "state=running")


class BackfillAfterApplyTests(fixtures.UpdaterCase):
    def answer_backfill(self, returncode=0, stdout="", raises=None):
        """The fake answers the one-shot; it records what the receipts and journal were when it ran."""

        original = self.inst.compose_run
        self.seen_at_backfill = []

        def compose_run(*args, check=True, stream=False, timeout=None):
            if args[:1] != ("exec",):
                return original(*args, check=check, stream=stream, timeout=timeout)
            self.log.append(("compose", tuple(args), stream))
            self.seen_at_backfill.append(([p.read_bytes() for p in self.state.receipts()], self.journal()))
            if raises is not None:
                raise raises
            return CommandResult(self.inst.compose_args(*args), returncode, stdout, "stderr STUB-STDERR\n")

        self.inst.compose_run = compose_run

    def backfill_calls(self):
        return [call for call in self.compose_calls() if call[:1] == ("exec",)]

    def assert_reported(self, returncode, stdout, words, *, backfilled=False):
        """One apply on the fresh world: one one-shot in `app`, these words after the success line, and the
        update as it was (a succeeded receipt, no journal, the receipt untouched after the one-shot ran)."""

        self.answer_backfill(returncode, stdout)
        path = self.apply()
        self.assertEqual(self.backfill_calls(), [OPERATOR + ("app",) + COMMAND])
        self.assertEqual(Path(path), self.state.receipts()[-1])
        self.assertEqual(self.last_receipt()["status"], "succeeded")
        (receipts, journal), = self.seen_at_backfill
        self.assertIsNone(journal)
        self.assertEqual(receipts, [p.read_bytes() for p in self.state.receipts()])
        self.assertIsNone(self.journal())
        out = self.out.getvalue()
        self.assertLess(out.index("Updated to v1.1.0. Receipt: "), out.index(words[0]))
        for word in words:
            self.assertIn(word, out)
        if not backfilled:
            self.assertNotIn("Harness versions backfilled", out)
        for unprinted in UNPRINTED:
            self.assertNotIn(unprinted, out)

    def test_complete_reports_the_environments_of_the_last_result_line(self):
        self.assert_reported(0, "detail STUB-DETAIL\n" + LINE.format("running", 1, 1, 0)
                             + LINE.format("complete", 4, 4, 0),
                             ["Harness versions backfilled (4 environments)"], backfilled=True)

    def test_off_says_versioning_is_off(self):
        self.assert_reported(0, LINE.format("off", 0, 0, 0),
                             ["Harness versioning is off (HARNESS_VERSIONING_ENABLED=false); no backfill"])

    def test_already_running_says_so(self):
        self.assert_reported(0, LINE.format("already_running", 0, 0, 0),
                             ["A backfill is already running; check versioning status for progress."])

    def test_no_result_line_warns_with_the_command_to_run_again(self):
        self.assert_reported(0, "detail STUB-DETAIL\n", ["printed no result", RERUN])

    def test_a_failed_backfill_warns_and_the_update_stands(self):
        self.assert_reported(1, "detail STUB-DETAIL\n" + LINE.format("failed", 4, 3, 1),
                             ["failed (exit 1, state=failed total=4 processed=4 succeeded=3 failed=1 skipped=0 "
                              "busy_organizations=0)", "one-shot", "docker compose logs app", RERUN])

    def test_a_backfill_that_cannot_start_warns_and_the_update_stands(self):
        self.answer_backfill(raises=InstallError("cannot run docker: [Errno 2] No such file or directory"))
        path = self.apply()
        self.assertEqual(Path(path), self.state.receipts()[-1])
        self.assertEqual(self.last_receipt()["status"], "succeeded")
        self.assertIsNone(self.journal())
        self.assertIn("the harness versions backfill could not run", self.out.getvalue())
        self.assertIn(RERUN, self.out.getvalue())
        # Only the error's type is named: its text could carry a command line.
        self.assertIn("(InstallError)", self.out.getvalue())
        self.assertNotIn("No such file or directory", self.out.getvalue())

    def test_distributed_runs_it_in_the_backend_service(self):
        self.build("distributed")
        self.answer_backfill(0, LINE.format("complete", 4, 4, 0))
        self.apply()
        self.assertEqual(self.backfill_calls(), [OPERATOR + ("backend",) + COMMAND])
        self.assertIn("Harness versions backfilled (4 environments)", self.out.getvalue())

    def test_production_runs_it_in_the_backend_service_with_its_compose_files(self):
        self.build("production")
        self.answer_backfill(1, "")
        self.apply()
        self.assertEqual(self.backfill_calls(), [OPERATOR + ("backend",) + COMMAND])
        compose = ("docker compose --env-file deploy/.env.production -f docker-compose.distributed.yml"
                   " -f deploy/docker-compose.production.yml")
        out = self.out.getvalue()
        self.assertIn(f"{compose} logs backend", out)
        self.assertIn(f"Run it again with: {compose} exec -T -e SERVICE_TYPE=bootstrap", out)

    def test_stage_and_dry_run_never_start_it(self):
        self.answer_backfill(0, LINE.format("complete", 4, 4, 0))
        self.apply(dry_run=True)
        fixtures.staged_by(self)
        self.assertEqual(self.backfill_calls(), [])
        self.assertNotIn("Harness versions", self.out.getvalue())

    def test_a_guarded_scheduled_run_never_starts_it(self):
        # Any run under an automation guard is unattended, even one that takes the manual apply path.
        self.answer_backfill(0, LINE.format("complete", 4, 4, 0))
        guard = SimpleNamespace(automatic=None, validate=lambda *args, **kwargs: None)
        updater = Updater(self.inst, self.updater().settings, self.state, out=self.out, readiness=self.ready,
                          hook_runner=self.hook, automation_guard=guard,
                          guard_context=lambda engine: SimpleNamespace(receipt_metadata={}))
        self.assertIsNone(updater._harness_versions_backfill())
        self.assertEqual(self.backfill_calls(), [])
        self.assertEqual(self.out.getvalue(), "")

    def test_a_scheduled_automatic_apply_never_starts_it(self):
        # Routing only: a scheduled run returns from its own scope before the manual path's success
        # line. Its scope and the private automation lock are covered by the automation tests.
        self.answer_backfill(0, LINE.format("complete", 4, 4, 0))
        guard = SimpleNamespace(automatic=object(), validate=lambda *args, **kwargs: None)
        updater = Updater(self.inst, self.updater().settings, self.state, out=self.out, readiness=self.ready,
                          hook_runner=self.hook, automation_guard=guard,
                          guard_context=lambda engine: SimpleNamespace(receipt_metadata={}))
        finished = self.root / "automatic-receipt.json"

        @contextmanager
        def lock(**kwargs):
            yield

        with mock.patch.object(self.state, "lock", lock), \
                mock.patch.object(Updater, "_apply_automatic_scope_locked", return_value=finished) as automatic:
            plan = self.plan()
            self.assertEqual(updater.apply(lambda history: plan, {"version": plan["target_version"]}), finished)
        automatic.assert_called_once()
        self.assertEqual(self.backfill_calls(), [])
        self.assertNotIn("Harness versions", self.out.getvalue())

    def test_the_readme_names_the_one_shot_to_run_by_hand(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("not_started", text)
        self.assertIn("docker compose exec -T -e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator app "
                      "python manage.py harness_versions_backfill --auto", text)
        self.assertIn("`backend` (Distributed)", text)


if __name__ == "__main__":
    unittest.main()
