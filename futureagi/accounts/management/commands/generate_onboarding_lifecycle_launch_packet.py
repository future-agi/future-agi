from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from accounts.services.onboarding.lifecycle_launch_packets import (
    write_lifecycle_launch_packet,
)


class Command(BaseCommand):
    help = "Write a reviewable launch packet for an onboarding lifecycle send."

    def add_arguments(self, parser):
        parser.add_argument("--approval-manifest", required=True)
        parser.add_argument("--approval-record", required=True)
        parser.add_argument("--dry-run-report", required=True)
        parser.add_argument("--dry-run-report-review-record", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--require-sendable-candidate", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--now")

    def handle(self, *args, **options):
        now = None
        if options.get("now"):
            now = parse_datetime(options["now"])
            if now is None:
                raise CommandError("--now must be an ISO datetime.")
        try:
            result = write_lifecycle_launch_packet(
                output_path=options["output"],
                force=options["force"],
                approval_manifest_path=options["approval_manifest"],
                approval_record_path=options["approval_record"],
                dry_run_report_path=options["dry_run_report"],
                dry_run_report_review_record_path=(
                    options["dry_run_report_review_record"]
                ),
                generated_at=now,
                require_sendable_candidate=options["require_sendable_candidate"],
            )
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        payload = result.to_payload()
        self.stdout.write(f"output_path={payload['output_path']}")
        self.stdout.write(f"packet_sha256={payload['packet_sha256']}")
        self.stdout.write(f"status={payload['status']}")
        self.stdout.write(f"command_name={payload['command_name']}")
        self.stdout.write(f"evaluated={payload['evaluated']}")
        self.stdout.write(
            f"sendable_candidate_count={payload['sendable_candidate_count']}"
        )
