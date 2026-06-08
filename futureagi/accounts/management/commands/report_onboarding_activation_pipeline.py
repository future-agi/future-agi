import json
from datetime import datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from accounts.services.onboarding.activation_pipeline_report import (
    activation_pipeline_report,
)


def _parse_boundary(value, *, end_of_day=False):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        parsed_date = parse_date(value)
        if parsed_date:
            parsed_time = time.max if end_of_day else time.min
            parsed = datetime.combine(parsed_date, parsed_time)
    if parsed is None:
        raise CommandError(f"Invalid date or datetime: {value}")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


class Command(BaseCommand):
    help = "Report onboarding activation pipeline operational health."

    def add_arguments(self, parser):
        parser.add_argument("--since")
        parser.add_argument("--until")
        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            dest="output_format",
        )
        parser.add_argument("--fail-on-risk", action="store_true")

    def handle(self, *args, **options):
        result = activation_pipeline_report(
            since=_parse_boundary(options.get("since")),
            until=_parse_boundary(options.get("until"), end_of_day=True),
        )
        payload = result.to_payload()

        if options["output_format"] == "json":
            self.stdout.write(json.dumps(payload, sort_keys=True))
        else:
            self.stdout.write("onboarding activation pipeline report")
            self.stdout.write(f"schema_version={payload['schema_version']}")
            self.stdout.write(f"status={payload['status']}")
            self.stdout.write(f"since={payload['since']}")
            self.stdout.write(f"until={payload['until']}")
            self.stdout.write(
                f"lifecycle_evaluations={payload['lifecycle_evaluations']}"
            )
            self.stdout.write(f"activation_exports={payload['activation_exports']}")
            self.stdout.write(f"activation_receipts={payload['activation_receipts']}")
            self.stdout.write(f"lifecycle_sends={payload['lifecycle_sends']}")
            self.stdout.write(
                f"notification_deliveries={payload['notification_deliveries']}"
            )
            self.stdout.write(f"risks={payload['risks']}")

        if options["fail_on_risk"] and payload["status"] != "healthy":
            raise CommandError("Onboarding activation pipeline needs attention.")
