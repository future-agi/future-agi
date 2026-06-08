from django.core.management.base import BaseCommand, CommandError

from accounts.services.onboarding.lifecycle_jobs import (
    run_onboarding_lifecycle_dry_run,
)


class Command(BaseCommand):
    help = "Evaluate onboarding lifecycle campaigns without sending email."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--user-id")
        parser.add_argument("--workspace-id")
        parser.add_argument("--campaign-key")
        parser.add_argument("--source", default="lifecycle_dry_run")
        parser.add_argument("--no-write", action="store_true")
        parser.add_argument("--dry-run-only", action="store_true")

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit must be greater than zero.")

        result = run_onboarding_lifecycle_dry_run(
            limit=options["limit"],
            user_id=options.get("user_id"),
            workspace_id=options.get("workspace_id"),
            campaign_key=options.get("campaign_key"),
            source=options["source"],
            write=not options["no_write"],
        )
        payload = result.to_payload()
        self.stdout.write(f"run_id={payload['run_id']}")
        self.stdout.write(f"evaluated={payload['evaluated']}")
        self.stdout.write(f"written={payload['written']}")
        self.stdout.write(f"status_counts={payload['status_counts']}")
        self.stdout.write(f"campaign_counts={payload['campaign_counts']}")
        self.stdout.write(f"suppression_counts={payload['suppression_counts']}")
        if payload["errors"]:
            self.stdout.write(f"errors={payload['errors']}")
