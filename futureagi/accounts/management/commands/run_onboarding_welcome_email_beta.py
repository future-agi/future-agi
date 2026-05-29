from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from accounts.services.onboarding.lifecycle_sender import (
    send_limited_onboarding_lifecycle_batch,
)

WELCOME_CAMPAIGN_GROUP = "welcome"
MAX_BETA_LIMIT = 100


class Command(BaseCommand):
    help = (
        "Run the tightly scoped welcome-email onboarding beta. Defaults to "
        "dry-run; pass --send to use the existing allowlisted send pipeline."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument("--cohort", choices=["internal", "beta"], default="beta")
        parser.add_argument("--user-id")
        parser.add_argument("--workspace-id")
        parser.add_argument("--send", action="store_true")
        parser.add_argument("--now")

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1:
            raise CommandError("--limit must be greater than zero.")
        if limit > MAX_BETA_LIMIT:
            raise CommandError(f"--limit must be {MAX_BETA_LIMIT} or lower.")

        now = None
        if options.get("now"):
            now = parse_datetime(options["now"])
            if now is None:
                raise CommandError("--now must be an ISO datetime.")

        dry_run = not options["send"]
        result = send_limited_onboarding_lifecycle_batch(
            cohort=options["cohort"],
            limit=limit,
            campaign_group=WELCOME_CAMPAIGN_GROUP,
            workspace_id=options.get("workspace_id"),
            user_id=options.get("user_id"),
            dry_run=dry_run,
            now=now,
        )
        payload = result.to_payload()
        self.stdout.write(f"mode={'send' if options['send'] else 'dry_run'}")
        self.stdout.write(f"campaign_group={WELCOME_CAMPAIGN_GROUP}")
        self.stdout.write(f"cohort={options['cohort']}")
        self.stdout.write(f"run_id={payload['run_id']}")
        self.stdout.write(f"evaluated={payload['evaluated']}")
        self.stdout.write(f"sent={payload['sent']}")
        self.stdout.write(f"suppressed={payload['suppressed']}")
        self.stdout.write(f"failed={payload['failed']}")
        self.stdout.write(f"skipped={payload['skipped']}")
        self.stdout.write(f"status_counts={payload['status_counts']}")
        self.stdout.write(f"suppression_counts={payload['suppression_counts']}")
