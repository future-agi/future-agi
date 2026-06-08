from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from accounts.services.onboarding.lifecycle_send_evidence import (
    write_lifecycle_send_evidence_report,
)


class Command(BaseCommand):
    help = "Write a reviewable evidence report for lifecycle email sends."

    def add_arguments(self, parser):
        parser.add_argument("--send-log-id", action="append", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--now")
        parser.add_argument("--require-launch-packet", action="store_true")
        parser.add_argument("--require-provider-accepted", action="store_true")
        parser.add_argument("--require-email-delivery", action="store_true")
        parser.add_argument("--require-click", action="store_true")
        parser.add_argument("--require-completion", action="store_true")
        parser.add_argument("--require-unsubscribe", action="store_true")
        parser.add_argument("--require-snooze", action="store_true")
        parser.add_argument("--require-frequency-cap", action="store_true")
        parser.add_argument("--require-completion-suppression", action="store_true")
        parser.add_argument("--require-receipt-backed", action="store_true")

    def handle(self, *args, **options):
        now = None
        if options.get("now"):
            now = parse_datetime(options["now"])
            if now is None:
                raise CommandError("--now must be an ISO datetime.")
        try:
            result = write_lifecycle_send_evidence_report(
                output_path=options["output"],
                force=options["force"],
                send_log_ids=options["send_log_id"],
                generated_at=now,
                launch_packet=options["require_launch_packet"],
                provider_accepted=options["require_provider_accepted"],
                email_delivery=options["require_email_delivery"],
                click=options["require_click"],
                completion=options["require_completion"],
                unsubscribe=options["require_unsubscribe"],
                snooze=options["require_snooze"],
                frequency_cap=options["require_frequency_cap"],
                completion_suppression=options["require_completion_suppression"],
                receipt_backed=options["require_receipt_backed"],
            )
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        payload = result.to_payload()
        self.stdout.write(f"output_path={payload['output_path']}")
        self.stdout.write(f"report_sha256={payload['report_sha256']}")
        self.stdout.write(f"status={payload['status']}")
        self.stdout.write(f"send_log_count={payload['send_log_count']}")
        self.stdout.write(f"missing_requirements={payload['missing_requirements']}")
        if payload["missing_requirements"]:
            missing = ", ".join(payload["missing_requirements"])
            raise CommandError(
                f"Required lifecycle send evidence is missing: {missing}."
            )
