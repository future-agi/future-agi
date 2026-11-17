"""Give hosted harness environments built before versioning their versions and pins.

Run by hand after the versioning code is deployed; safe to run again. Check first, then run:

    python manage.py harness_versions_backfill --check
    python manage.py harness_versions_backfill
    python manage.py harness_versions_backfill --job <environment_id> [--job ...]

``--check`` is a read-only dry pass: database reads run in rolled-back, read-only
transactions and storage is only read, so it may run in any backend pod. It exits with
status 1 when any environment has an error finding.

A run backfills one environment at a time and needs a pod allowed to write (a one-shot
operator job in hosted environments). An environment that fails is reported and skipped;
the command exits with status 1 if any failed, was changed while it was read, has an
archive that cannot be split, or was named with ``--job`` but cannot be backfilled.

Outcomes ``appended`` and ``default_moved`` move the environment's default to the
versions of its current archive: the head's default tracks its archive, the one pin the
backfill replaces. ``skipped_unreadable`` pins only the job's agent; its versions and runs
wait until the archive is fixed.

``--json`` prints the result as JSON on stdout, where application logs may also appear;
``--output FILE`` writes the same JSON to FILE alone, for a clean machine-readable copy.
FILE must not exist yet, and is created before any work starts.

``--auto`` is the separate post-bootstrap one-shot. It prints state and counts
only, with a timer heartbeat during archive work. Exit 0 can mean complete,
explicitly off (no writes), or already_running (another attempt owns the work).
Only state=complete means this attempt finished its work. Partial failures exit
nonzero; the installer warns without making application services depend on it.
It uses the same operator startup authorization as a manual write command.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from dataclasses import asdict
from typing import IO, Any

from django.core.management.base import BaseCommand, CommandError

from simulate.services.harness_versioning.backfill import FAILED_OUTCOMES
from simulate.services.harness_versioning.backfill_check import check_all
from simulate.services.harness_versioning.upgrade_runner import run_managed


class Command(BaseCommand):
    help = (
        "Record versions, the default agent and run pins for hosted harness environments "
        "built before versioning. --check reports what it would do without writing."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--auto",
            action="store_true",
            help="Independent upgrade one-shot: state/counts only; respect explicit off.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Read-only dry pass: report what the backfill would do and any errors.",
        )
        parser.add_argument(
            "--job",
            action="append",
            default=[],
            help="Limit to these environment job ids (repeatable).",
        )
        parser.add_argument(
            "--json", action="store_true", help="Print the result as JSON."
        )
        parser.add_argument(
            "--output", help="Write the result as JSON to this file only."
        )

    def handle(self, *args, **options):
        if options.get("auto"):
            if any(options.get(name) for name in ("check", "job", "json", "output")):
                raise CommandError(
                    "--auto cannot be combined with --check, --job, --json or --output"
                )
            self._auto()
            return
        job_ids = None
        if options["job"]:
            try:
                job_ids = [uuid.UUID(value) for value in options["job"]]
            except ValueError as exc:
                raise CommandError(f"--job takes environment job ids: {exc}") from exc
        output = self._open_output(options.get("output"))
        try:
            if options["check"]:
                self._check(job_ids, options, output)
            else:
                self._run(job_ids, options, output)
        finally:
            if output is not None:
                output.close()

    @staticmethod
    def _open_output(path: str | None) -> IO[str] | None:
        """Create the output file up front, so a bad path fails before any work."""
        if not path:
            return None
        try:
            return open(path, "x", encoding="utf-8")
        except OSError as exc:
            raise CommandError(f"--output cannot be created: {exc}") from exc

    def _emit(self, result: Any, options: dict, output: IO[str] | None) -> None:
        text = json.dumps(result, indent=2, sort_keys=True, default=str)
        if output is not None:
            output.write(text)
        if options.get("json"):
            self.stdout.write(text)

    def _check(self, job_ids, options, output) -> None:
        result = check_all(job_ids=job_ids)
        self._emit(result, options, output)
        if not options.get("json"):
            for one in result["environments"]:
                codes = ", ".join(
                    f"{finding['severity']}:{finding['code']}"
                    for finding in one["findings"]
                )
                self.stdout.write(
                    f"{one['short_id']}  {one['outcome']:<26} "
                    f"{one['layout'] or '-':<9} {codes or 'ok'}"
                )
            totals = result["totals"]
            self.stdout.write(
                self.style.SUCCESS(
                    f"Backfill check: {totals['environments']} environments, "
                    f"outcomes {totals['outcomes']}, layouts {totals['layouts']}, "
                    f"{totals['errors']} errors, {totals['warnings']} warnings."
                )
            )
        if result["totals"]["errors"]:
            raise CommandError(
                f"{result['totals']['errors']} error finding(s); the backfill would "
                "fail or record wrong data for these environments"
            )

    def _auto(self) -> None:
        def progress(counts):
            self.stdout.write(
                "Harness versions backfill: "
                + " ".join(f"{name}={value}" for name, value in counts.items())
            )
            self.stdout.flush()

        # This is a dedicated operator process, never a request/background task
        # inside the app. Dependency logs can include object keys or diagnostics;
        # only our explicit count lines are emitted during automatic work.
        previous_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            try:
                report = run_managed(automatic=True, on_progress=progress)
            except Exception:
                raise CommandError(
                    "Automatic backfill could not finish; retry the operator command"
                ) from None
        finally:
            logging.disable(previous_disable)
        if report.failed:
            raise CommandError(
                f"{report.failed} environment(s) need attention; fix and run it again"
            )

    def _run(self, job_ids, options, output) -> None:
        results = run_managed(job_ids).results
        outcomes = Counter(result.outcome for result in results)
        failed = [result for result in results if result.outcome in FAILED_OUTCOMES]
        self._emit(
            {
                "outcomes": dict(outcomes),
                "environments": [asdict(result) for result in results],
            },
            options,
            output,
        )
        for result in failed:
            self.stderr.write(
                f"{result.job_id}: {result.outcome}"
                + (f" ({'; '.join(result.notes)})" if result.notes else "")
            )
        if not options.get("json"):
            for result in results:
                if result.notes and result.outcome not in FAILED_OUTCOMES:
                    self.stdout.write(
                        f"{result.job_id[:8]}  {result.outcome}: "
                        f"{', '.join(result.notes)}"
                    )
            self.stdout.write(
                self.style.SUCCESS(
                    "Harness versions backfill: "
                    + (
                        ", ".join(
                            f"{name} {count}"
                            for name, count in sorted(outcomes.items())
                        )
                        or "nothing to backfill"
                    )
                )
            )
        if failed:
            raise CommandError(
                f"{len(failed)} environment(s) need attention; fix and run it again"
            )
