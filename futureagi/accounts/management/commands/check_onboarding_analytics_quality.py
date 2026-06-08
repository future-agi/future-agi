import json
from datetime import datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from accounts.services.onboarding.analytics_quality import (
    check_onboarding_analytics_quality,
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
    help = "Check onboarding launch analytics quality from durable product data."

    def add_arguments(self, parser):
        parser.add_argument("--since")
        parser.add_argument("--until")
        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            dest="output_format",
        )
        parser.add_argument("--fail-on-error", action="store_true")

    def handle(self, *args, **options):
        result = check_onboarding_analytics_quality(
            since=_parse_boundary(options.get("since")),
            until=_parse_boundary(options.get("until"), end_of_day=True),
        )
        payload = result.to_payload()

        if options["output_format"] == "json":
            self.stdout.write(json.dumps(payload, sort_keys=True))
        else:
            self.stdout.write("onboarding analytics quality")
            for key, value in payload.items():
                self.stdout.write(f"{key}={value}")

        if options["fail_on_error"] and payload["status"] != "pass":
            raise CommandError("Onboarding analytics quality check failed.")
