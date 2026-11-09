"""Operator-only composed retirement, with explicit forward/reverse previews."""

import json

from django.core.management.base import BaseCommand, CommandError

from accounts.services.retirement_733 import run_retirement


class Command(BaseCommand):
    help = "Retire legacy credentials with a non-secret reversible state journal."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--accept-open-batch-loss",
            action="store_true",
            help="Acknowledge explicit failure of open legacy batches at cutover.",
        )
        parser.add_argument(
            "--reverse",
            action="store_true",
            help="Restore unchanged journal rows; requires an operator-approved rollback.",
        )

    def handle(self, *args, **options):
        result = run_retirement(
            dry_run=options["dry_run"],
            reverse=options["reverse"],
            accept_open_batch_loss=options["accept_open_batch_loss"],
        )
        # Counts and operational obligations only, never identifiers or material.
        self.stdout.write(json.dumps(result, sort_keys=True))
        if result.get("gateway_pending") or result.get("config_pending"):
            raise CommandError("Retirement propagation is pending; retry the command")
