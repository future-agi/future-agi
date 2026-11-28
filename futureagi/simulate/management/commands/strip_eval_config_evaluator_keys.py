"""
Strip the evaluator settings grading leaked into simulate eval attachments, or put back
the copies that the strip kept.

Usage:
    python manage.py strip_eval_config_evaluator_keys [--dry-run]
    python manage.py strip_eval_config_evaluator_keys --restore <eval_config_id> ... [--keys k1 k2]
    python manage.py strip_eval_config_evaluator_keys --restore-all [--keys k1 k2]
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from simulate.models import SimulateEvalConfig, SimulateEvalConfigRemovedKeys
from simulate.services.eval_config_repair import (
    RESTORABLE_KEYS,
    restore,
    strip_rows,
)


class Command(BaseCommand):
    help = (
        "Migration 0114 strips the evaluator-written keys from simulate eval "
        "attachments. Run this once per region after every backend, Temporal-worker "
        "and simulation-runner pod is on the new version, and again about a day later, "
        "to strip what old pods or edit screens opened before the release wrote back "
        "after 0114 ran. --dry-run only counts. --restore / --restore-all put back the "
        "honoured keys the strip copied. A restored row keeps the keys its restore "
        "handled on later strips; api_key and the leak's other keys still go. Run a "
        "restore only after the last clean-up run, and never at the same time as one "
        "(or as another restore). Prefer --restore <id> over --restore-all, which also "
        "brings back stale grading text. A row that shows the leak again is reported "
        "as leaked and left alone: strip it first."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Count, write nothing."
        )
        parser.add_argument(
            "--restore", nargs="+", metavar="EVAL_CONFIG_ID", help="Restore these rows."
        )
        parser.add_argument(
            "--restore-all", action="store_true", help="Restore every unrestored copy."
        )
        parser.add_argument(
            "--keys",
            nargs="+",
            metavar="KEY",
            choices=sorted(RESTORABLE_KEYS),
            help="Narrow the keys a restore brings back (default: all of them).",
        )

    def handle(self, *args, **options):
        restoring = options["restore"] or options["restore_all"]
        if options["restore"] and options["restore_all"]:
            raise CommandError("Use --restore or --restore-all, not both.")
        if restoring:
            if options["dry_run"]:
                raise CommandError("--dry-run applies to the strip, not a restore.")
            keys = options["keys"] or RESTORABLE_KEYS
            ids = None
            if options["restore"]:
                ids = []
                for value in options["restore"]:
                    try:
                        ids.append(uuid.UUID(value))
                    except ValueError as exc:
                        raise CommandError(f"Not an eval config id: {value}") from exc
            report = restore(
                SimulateEvalConfig,
                SimulateEvalConfigRemovedKeys,
                ids=ids,
                keys=keys,
            )
            labels = (
                "restored",
                "missing",
                "raced",
                "skipped",
                "leaked",
                "nothing_to_apply",
                "closed_without_applying",
                "none",
            )
            counts = [f"{label}={len(report[label])}" for label in labels]
            counts.append(
                f"applied_keys={sum(len(k) for k in report['applied'].values())}"
            )
            counts.append(
                f"present_keys={sum(len(k) for k in report['present'].values())}"
            )
            self.stdout.write(" ".join(counts))
            for label in labels[1:]:
                for eval_config_id in report[label]:
                    self.stdout.write(f"{label}: {eval_config_id}")
            return
        if options["keys"]:
            raise CommandError("--keys applies to a restore only.")
        scanned, changed = strip_rows(
            SimulateEvalConfig,
            SimulateEvalConfigRemovedKeys,
            dry_run=options["dry_run"],
        )
        self.stdout.write(f"scanned={scanned} changed={changed}")
