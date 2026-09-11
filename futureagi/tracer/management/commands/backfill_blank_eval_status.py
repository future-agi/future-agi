"""
Backfill ``status`` on legacy blank-status EvalLogger rows.

The legacy eval writer (`tracer.utils.eval`) persisted results without setting
``status`` (the column only exists since migration 0084), so rows it created
before the eval-task engine rollout carry ``status = ''`` even though they
hold a real, billed result. This command:

  1. Stamps ``completed`` on blank rows that verifiably succeeded (no error,
     no skip reason, and at least one populated output column).
  2. Re-runs the baseline errored/skipped correction pass from migration 0087.
     This is a full-table sweep (not scoped to blank rows) that flips
     ``error = true`` rows to ``errored`` and skip-reason rows to ``skipped``.
     It is idempotent and skips rows already on the target status, but
     operators should be aware it touches more than just blank rows.

Blank rows with no output at all are intentionally left untouched: their
outcome is unknown and mislabeling them would corrupt the metric this fixes.

Usage:
    python manage.py backfill_blank_eval_status --dry-run
    python manage.py backfill_blank_eval_status
    python manage.py backfill_blank_eval_status --batch-size 1000
"""

from django.core.management.base import BaseCommand

from tracer.services.eval_tasks.backfill import (
    _backfill_status,
    backfill_blank_completed_status,
)


class Command(BaseCommand):
    help = (
        "Stamp 'completed' on legacy blank-status EvalLogger rows that hold a "
        "successful result, then re-run the errored/skipped correction pass "
        "(full-table, idempotent)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many rows would change without writing.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5000,
            help="Rows per UPDATE batch (each batch commits independently).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        completed = backfill_blank_completed_status(
            batch_size=batch_size, dry_run=dry_run
        )
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"dry_run=True would_stamp_completed={completed} "
                    f"(errored/skipped pass skipped in dry-run)"
                )
            )
            return

        errored_or_skipped = _backfill_status(batch_size)
        self.stdout.write(
            self.style.SUCCESS(
                f"stamped_completed={completed} "
                f"errored_or_skipped_corrected={errored_or_skipped}"
            )
        )
