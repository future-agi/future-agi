from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from accounts.services.onboarding.lifecycle_launch_reviews import (
    write_lifecycle_launch_review,
)


class Command(BaseCommand):
    help = "Write a final review manifest for an onboarding lifecycle launch."

    def add_arguments(self, parser):
        parser.add_argument("--approval-manifest", required=True)
        parser.add_argument("--approval-record", required=True)
        parser.add_argument("--dry-run-report", required=True)
        parser.add_argument("--dry-run-report-review-record", required=True)
        parser.add_argument("--launch-packet", required=True)
        parser.add_argument("--send-evidence-report", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--now")

    def handle(self, *args, **options):
        now = None
        if options.get("now"):
            now = parse_datetime(options["now"])
            if now is None:
                raise CommandError("--now must be an ISO datetime.")
        try:
            result = write_lifecycle_launch_review(
                output_path=options["output"],
                force=options["force"],
                approval_manifest_path=options["approval_manifest"],
                approval_record_path=options["approval_record"],
                dry_run_report_path=options["dry_run_report"],
                dry_run_report_review_record_path=(
                    options["dry_run_report_review_record"]
                ),
                launch_packet_path=options["launch_packet"],
                send_evidence_report_path=options["send_evidence_report"],
                generated_at=now,
            )
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        payload = result.to_payload()
        self.stdout.write(f"output_path={payload['output_path']}")
        self.stdout.write(f"review_sha256={payload['review_sha256']}")
        self.stdout.write(f"status={payload['status']}")
        self.stdout.write(f"missing_checks={payload['missing_checks']}")
        if payload["missing_checks"]:
            missing = ", ".join(payload["missing_checks"])
            raise CommandError(f"Lifecycle launch review is incomplete: {missing}.")
