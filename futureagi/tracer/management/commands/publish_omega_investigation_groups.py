import json

from django.core.management.base import BaseCommand, CommandError

from tracer.services.trace_investigation_grouping import (
    publish_pending_omega_investigation_groups,
)


class Command(BaseCommand):
    help = (
        "Publish a bounded page of durable Omega reports into Error Feed. "
        "Intended for an external periodic scheduler."
    )

    def add_arguments(self, parser):
        parser.add_argument("--report-limit", type=int, default=25)

    def handle(self, *args, **options):
        try:
            summary = publish_pending_omega_investigation_groups(
                report_limit=options["report_limit"]
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(summary, sort_keys=True))
