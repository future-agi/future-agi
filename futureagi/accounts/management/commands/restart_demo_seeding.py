"""Operator command: start the demo seeds that stopped after a lasting failure."""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.demo_seeding import restart_demo_seeding


class Command(BaseCommand):
    help = (
        "Restarts the demo data seeds that stopped after a lasting failure, for "
        "every organization or only the one given with --organization. The "
        "recovery schedule runs them again on its next pass; with --dry-run the "
        "command only prints how many it would restart."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--organization",
            metavar="UUID",
            help="Restart only this organization's demo seed.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print how many seeds would restart and change nothing.",
        )

    def handle(self, *args, **options):
        organization_id = None
        if options["organization"] is not None:
            try:
                organization_id = uuid.UUID(str(options["organization"]))
            except ValueError:
                raise CommandError(
                    "The --organization value is not an organization id (a UUID).",
                    returncode=2,
                ) from None
        with transaction.atomic():
            restarted = restart_demo_seeding(organization_id)
            if options["dry_run"]:
                transaction.set_rollback(True)
        noun = "request" if restarted == 1 else "requests"
        if options["dry_run"]:
            self.stdout.write(
                f"Dry run: {restarted} stopped demo seed {noun} would restart; "
                "nothing changed."
            )
        else:
            self.stdout.write(f"Restarted {restarted} stopped demo seed {noun}.")
