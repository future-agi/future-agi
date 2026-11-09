"""Retire purpose-minted gateway keys, with admin API readback and L1b's journal."""

import json

from django.core.management.base import BaseCommand, CommandError

from agentcc.services.retirement_733 import load_adapter, retire_selected_keys


class Command(BaseCommand):
    help = "Retire 733 purpose-labelled gateway keys while L1b legacy issuance is OFF."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        report = retire_selected_keys(load_adapter(), dry_run=options["dry_run"])
        self.stdout.write(json.dumps(report, sort_keys=True))
        if report["pending"]:
            # Rows and manifest have committed already. Retrying is safe, and
            # the negative bulk sync also repairs failed gateway revokes.
            raise CommandError("gateway revocation pending")
