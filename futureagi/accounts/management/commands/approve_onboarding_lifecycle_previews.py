from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from accounts.services.onboarding.lifecycle_preview_approval import (
    write_lifecycle_preview_approval_record,
)


class Command(BaseCommand):
    help = "Write a reviewer approval record for generated lifecycle previews."

    def add_arguments(self, parser):
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--approved-by", required=True)
        parser.add_argument("--approved-at")
        parser.add_argument("--campaign-key", action="append")
        parser.add_argument("--note", default="")
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        approved_at = None
        if options.get("approved_at"):
            approved_at = parse_datetime(options["approved_at"])
            if approved_at is None:
                raise CommandError("--approved-at must be an ISO datetime.")
        try:
            result = write_lifecycle_preview_approval_record(
                manifest_path=options["manifest"],
                output_path=options["output"],
                approved_by=options["approved_by"],
                approved_at=approved_at,
                campaign_keys=options.get("campaign_key"),
                note=options["note"],
                force=options["force"],
            )
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        payload = result.to_payload()
        self.stdout.write(f"output_path={payload['output_path']}")
        self.stdout.write(f"manifest_sha256={payload['manifest_sha256']}")
        self.stdout.write(f"approval_record_sha256={payload['approval_record_sha256']}")
        self.stdout.write(f"approved_by={payload['approved_by']}")
        self.stdout.write(f"approved_at={payload['approved_at']}")
        self.stdout.write(f"campaign_keys={payload['campaign_keys']}")
