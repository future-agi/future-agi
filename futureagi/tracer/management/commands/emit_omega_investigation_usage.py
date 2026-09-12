import json

from django.core.management.base import BaseCommand, CommandError

from tracer.services.trace_investigation_billing import (
    deliver_trace_investigation_usage,
)


class Command(BaseCommand):
    help = (
        "Backfill and drain a bounded page of durable Omega usage receipts. "
        "Emission remains default-off until cloud consumer dedup is verified."
    )

    def add_arguments(self, parser):
        parser.add_argument("--receipt-limit", type=int, default=100)

    def handle(self, *args, **options):
        try:
            summary = deliver_trace_investigation_usage(
                receipt_limit=options["receipt_limit"]
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(summary, sort_keys=True))
