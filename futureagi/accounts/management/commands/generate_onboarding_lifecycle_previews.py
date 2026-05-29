from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from accounts.services.onboarding.lifecycle_preview_snapshots import (
    write_lifecycle_preview_snapshots,
)


class Command(BaseCommand):
    help = "Generate no-send onboarding lifecycle email preview snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", required=True)
        parser.add_argument("--campaign-key")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--now")

    def handle(self, *args, **options):
        now = None
        if options.get("now"):
            now = parse_datetime(options["now"])
            if now is None:
                raise CommandError("--now must be an ISO datetime.")
        try:
            result = write_lifecycle_preview_snapshots(
                output_dir=options["output_dir"],
                campaign_key=options.get("campaign_key"),
                force=options["force"],
                now=now,
            )
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        payload = result.to_payload()
        self.stdout.write(f"output_dir={payload['output_dir']}")
        self.stdout.write(f"count={payload['count']}")
        self.stdout.write(f"campaign_keys={payload['campaign_keys']}")
        self.stdout.write(f"files={payload['files']}")
