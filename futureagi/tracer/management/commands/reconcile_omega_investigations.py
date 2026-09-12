import json

from django.core.management.base import BaseCommand, CommandError

from tracer.services.trace_investigation_reconciliation import (
    reconcile_omega_investigations,
)


class Command(BaseCommand):
    help = (
        "Reconcile completed ClickHouse root spans into durable Omega "
        "investigation jobs. Intended for an external periodic scheduler."
    )

    def add_arguments(self, parser):
        parser.add_argument("--project-limit", type=int)
        parser.add_argument("--page-size", type=int)
        parser.add_argument("--max-pages-per-project", type=int)
        parser.add_argument("--lookback-seconds", type=int)
        parser.add_argument("--grace-seconds", type=int)

    def handle(self, *args, **options):
        try:
            summary = reconcile_omega_investigations(
                project_limit=options["project_limit"],
                page_size=options["page_size"],
                max_pages_per_project=options["max_pages_per_project"],
                lookback_seconds=options["lookback_seconds"],
                grace_seconds=options["grace_seconds"],
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(summary, sort_keys=True))
