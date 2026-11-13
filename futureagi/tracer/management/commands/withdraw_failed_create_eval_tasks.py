"""One-off rollout step; never run as part of application startup.

Withdraw the continuous tasks that failed creates left live before create
withdrew its own row. Run it with ``--apply`` while the stranded sweep is off
(``EVAL_TASK_SWEEP_MAX_TASKS=0``), then turn the sweep back on: otherwise the
sweep activates those rows from their creation time. Prints counts and
organization ids only, never a task, project or organization name.

Like the other operator commands it runs only in a one-shot operator job
(``SERVICE_TYPE=bootstrap`` with ``STARTUP_DB_MUTATION_MODE=operator``; see
``model_hub.apps``), with the backend's Temporal settings, because every
candidate is checked against Temporal before it is withdrawn.
"""

from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tracer.selectors.eval_tasks.failed_creates import FAILED_CREATE_PREDICATE
from tracer.services.eval_tasks.withdrawal import TALLIES, withdraw_failed_creates


def _line(label, tally):
    return f"{label}: " + " ".join(f"{key}={tally.get(key, 0)}" for key in TALLIES)


class Command(BaseCommand):
    help = (
        "Report, and with --apply withdraw, continuous eval tasks left PENDING "
        "and never started by a create that failed. Dry run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Withdraw the would_withdraw rows. Without it nothing is written.",
        )
        parser.add_argument(
            "--min-age-minutes",
            type=int,
            default=60,
            help=(
                "Only rows created at least this long ago, so a create still "
                "starting its workflow is never withdrawn (default 60)."
            ),
        )

    def handle(self, *args, **options):
        min_age = options["min_age_minutes"]
        if min_age < 1:
            raise CommandError("--min-age-minutes must be at least 1")
        created_before = timezone.now() - timedelta(minutes=min_age)
        tallies = withdraw_failed_creates(
            created_before=created_before, apply=options["apply"]
        )

        self.stdout.write(f"Predicate: {FAILED_CREATE_PREDICATE}")
        self.stdout.write(
            f"created_before: {created_before.isoformat()} (min age {min_age} minutes)"
        )
        total = Counter()
        for organization_id in sorted(tallies):
            tally = tallies[organization_id]
            self.stdout.write(_line(f"organization {organization_id}", tally))
            total.update(tally)
        self.stdout.write(_line("total", total))
        if options["apply"]:
            self.stdout.write(
                f"Applied: withdrew {total['withdrawn']} task(s). Running it "
                "again matches none of them."
            )
        else:
            self.stdout.write(
                "Dry run: nothing was written. Re-run with --apply to withdraw "
                "the would_withdraw rows."
            )
