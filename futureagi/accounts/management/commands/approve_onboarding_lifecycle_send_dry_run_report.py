from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from accounts.services.onboarding.lifecycle_send_reports import (
    write_lifecycle_send_dry_run_report_review_record,
)


class Command(BaseCommand):
    help = "Write a reviewer record for an onboarding lifecycle send dry-run report."

    def add_arguments(self, parser):
        parser.add_argument("--report", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--reviewed-by", required=True)
        parser.add_argument("--reviewed-at")
        parser.add_argument("--note", default="")
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        reviewed_at = None
        if options.get("reviewed_at"):
            reviewed_at = parse_datetime(options["reviewed_at"])
            if reviewed_at is None:
                raise CommandError("--reviewed-at must be an ISO datetime.")
        try:
            result = write_lifecycle_send_dry_run_report_review_record(
                report_path=options["report"],
                output_path=options["output"],
                reviewed_by=options["reviewed_by"],
                reviewed_at=reviewed_at,
                note=options["note"],
                force=options["force"],
            )
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        payload = result.to_payload()
        self.stdout.write(f"output_path={payload['output_path']}")
        self.stdout.write(f"report_sha256={payload['report_sha256']}")
        self.stdout.write(f"review_record_sha256={payload['review_record_sha256']}")
        self.stdout.write(f"reviewed_by={payload['reviewed_by']}")
        self.stdout.write(f"reviewed_at={payload['reviewed_at']}")
        self.stdout.write(f"candidate_count={payload['candidate_count']}")
