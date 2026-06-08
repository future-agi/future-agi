from django.core.management.base import BaseCommand, CommandError

from accounts.services.onboarding.activation_fact_lifecycle import (
    import_activation_fact_lifecycle_evaluations,
)


class Command(BaseCommand):
    help = "Import accepted onboarding activation fact receipts into lifecycle evaluations."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--campaign-key")
        parser.add_argument("--user-id")
        parser.add_argument("--workspace-id")
        parser.add_argument("--no-write", action="store_true")

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit must be greater than zero.")

        result = import_activation_fact_lifecycle_evaluations(
            limit=options["limit"],
            campaign_key=options.get("campaign_key"),
            user_id=options.get("user_id"),
            workspace_id=options.get("workspace_id"),
            write=not options["no_write"],
        )
        payload = result.to_payload()
        self.stdout.write(f"run_id={payload['run_id']}")
        self.stdout.write(f"evaluated={payload['evaluated']}")
        self.stdout.write(f"imported={payload['imported']}")
        self.stdout.write(f"status_counts={payload['status_counts']}")
        self.stdout.write(f"campaign_counts={payload['campaign_counts']}")
        self.stdout.write(f"skip_counts={payload['skip_counts']}")
        if payload["errors"]:
            self.stdout.write(f"errors={payload['errors']}")
