from django.core.management.base import BaseCommand, CommandError

from accounts.services.onboarding.activation_exporter import (
    run_onboarding_activation_export,
)


class Command(BaseCommand):
    help = "Evaluate paid-cloud onboarding activation facts into an export outbox."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--user-id")
        parser.add_argument("--workspace-id")
        parser.add_argument("--source", default="activation_export")
        parser.add_argument("--write", action="store_true")

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit must be greater than zero.")

        result = run_onboarding_activation_export(
            limit=options["limit"],
            user_id=options.get("user_id"),
            workspace_id=options.get("workspace_id"),
            source=options["source"],
            write=options["write"],
        )
        payload = result.to_payload()
        self.stdout.write(f"run_id={payload['run_id']}")
        self.stdout.write(f"evaluated={payload['evaluated']}")
        self.stdout.write(f"written={payload['written']}")
        self.stdout.write(f"status_counts={payload['status_counts']}")
        self.stdout.write(f"suppression_counts={payload['suppression_counts']}")
        if payload["errors"]:
            self.stdout.write(f"errors={payload['errors']}")
