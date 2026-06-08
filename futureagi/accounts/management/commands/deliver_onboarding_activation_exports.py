from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from accounts.services.onboarding.activation_export_delivery import (
    run_onboarding_activation_export_delivery,
)


class Command(BaseCommand):
    help = "Deliver ready paid-cloud onboarding activation export rows."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--write", action="store_true")
        parser.add_argument("--retry-failed", action="store_true")
        parser.add_argument("--endpoint-url")
        parser.add_argument("--shared-secret")
        parser.add_argument("--timeout-seconds", type=int)

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit must be greater than zero.")

        try:
            result = run_onboarding_activation_export_delivery(
                limit=options["limit"],
                dry_run=not options["write"],
                retry_failed=options["retry_failed"],
                endpoint_url=options.get("endpoint_url"),
                shared_secret=options.get("shared_secret"),
                timeout_seconds=options.get("timeout_seconds"),
            )
        except (ImproperlyConfigured, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        payload = result.to_payload()
        self.stdout.write(f"run_id={payload['run_id']}")
        self.stdout.write(f"evaluated={payload['evaluated']}")
        self.stdout.write(f"delivered={payload['delivered']}")
        self.stdout.write(f"failed={payload['failed']}")
        self.stdout.write(f"skipped={payload['skipped']}")
        self.stdout.write(f"dry_run={payload['dry_run']}")
        self.stdout.write(f"status_counts={payload['status_counts']}")
        if payload["errors"]:
            self.stdout.write(f"errors={payload['errors']}")
