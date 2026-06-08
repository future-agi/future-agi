import json
from datetime import datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from accounts.services.onboarding.activation_fact_lifecycle import (
    receipt_backed_lifecycle_cohort_report,
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


def _parse_group_by(value):
    if not value:
        return ("campaign_key", "primary_cohort_key")
    return tuple(item.strip() for item in value.split(",") if item.strip())


class Command(BaseCommand):
    help = "Report receipt-backed onboarding lifecycle cohort summaries."

    def add_arguments(self, parser):
        parser.add_argument("--since")
        parser.add_argument("--until")
        parser.add_argument("--campaign-key")
        parser.add_argument("--workspace-id")
        parser.add_argument("--organization-id")
        parser.add_argument("--group-by", default="campaign_key,primary_cohort_key")
        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            dest="output_format",
        )

    def handle(self, *args, **options):
        try:
            result = receipt_backed_lifecycle_cohort_report(
                since=_parse_boundary(options.get("since")),
                until=_parse_boundary(options.get("until"), end_of_day=True),
                campaign_key=options.get("campaign_key"),
                workspace_id=options.get("workspace_id"),
                organization_id=options.get("organization_id"),
                group_by=_parse_group_by(options.get("group_by")),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        payload = result.to_payload()
        if options["output_format"] == "json":
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return

        self.stdout.write("receipt-backed onboarding lifecycle cohorts")
        self.stdout.write(f"since={payload['since']}")
        self.stdout.write(f"until={payload['until']}")
        self.stdout.write(f"group_by={payload['group_by']}")
        self.stdout.write(f"row_count={len(payload['rows'])}")
        for row in payload["rows"]:
            self.stdout.write(json.dumps(row, sort_keys=True))
