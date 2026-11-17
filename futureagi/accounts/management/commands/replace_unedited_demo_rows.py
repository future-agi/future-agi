"""Operator command: replace earlier seeded demo rows that nobody has changed."""

import uuid

from django.core.management.base import BaseCommand, CommandError

from accounts.services.demo_row_repair import repair_demo_rows


class Command(BaseCommand):
    help = (
        "Rewrites the rows of each organization's seeded demo dataset that still "
        "hold exactly what an earlier build seeded, with the rows the seed writes "
        "now. Rows anyone changed or added are left as they are, and so is every "
        "other dataset. One transaction per organization; an organization that "
        "fails is rolled back and counted, the others go on, and the command then "
        "exits with an error. Running it again changes nothing. Prints six counts "
        "only; with --dry-run it changes nothing and locks no row."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the counts; change nothing and lock no row.",
        )
        parser.add_argument(
            "--organization",
            metavar="UUID",
            help="Repair only this organization's demo dataset.",
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
        dry_run = options["dry_run"]
        totals = repair_demo_rows(apply=not dry_run, organization_id=organization_id)
        replaced = "rows that would be replaced" if dry_run else "rows replaced"
        for line in (
            f"organizations touched: {totals.organizations}",
            f"{replaced}: {totals.replaced}",
            f"rows left because edited: {totals.left_edited}",
            f"rows left because added after the seed: {totals.left_added}",
            "rows left because the dataset's seeded columns changed: "
            f"{totals.left_columns_changed}",
            f"organizations failed: {totals.failed}",
        ):
            self.stdout.write(line)
        if totals.failed:
            kinds = ", ".join(
                f"{name} x{count}" for name, count in sorted(totals.failures.items())
            )
            raise CommandError(
                f"{totals.failed} organization(s) failed ({kinds}). Each one was left "
                "unchanged and is not in the counts; the other organizations are "
                "done. Run the command again to retry.",
                returncode=1,
            )
