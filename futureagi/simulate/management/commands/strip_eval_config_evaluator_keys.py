"""
Strip the evaluator settings grading leaked into simulate eval attachments, or put back
the copies that the strip kept.

Usage:
    python manage.py strip_eval_config_evaluator_keys [--dry-run]
    python manage.py strip_eval_config_evaluator_keys --restore <eval_config_id> ... [--keys k1 k2]
    python manage.py strip_eval_config_evaluator_keys --restore-all [--keys k1 k2]
"""

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
        "honoured keys the strip copied (run it only after the last strip), and a "
        "restored row is never stripped again."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count, write nothing.")
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
            help=f"Narrow the keys a restore brings back (of: {', '.join(sorted(RESTORABLE_KEYS))}).",
        )

    def handle(self, *args, **options):
        restoring = options["restore"] or options["restore_all"]
        if options["restore"] and options["restore_all"]:
            raise CommandError("Use --restore or --restore-all, not both.")
        if restoring:
            if options["dry_run"]:
                raise CommandError("--dry-run applies to the strip, not a restore.")
            keys = options["keys"] or RESTORABLE_KEYS
            report = restore(
                SimulateEvalConfig,
                SimulateEvalConfigRemovedKeys,
                ids=options["restore"] if options["restore"] else None,
                keys=keys,
            )
            self.stdout.write(
                f"restored={len(report['restored'])} "
                f"missing={len(report['missing'])} raced={len(report['raced'])}"
            )
            for label in ("missing", "raced"):
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
