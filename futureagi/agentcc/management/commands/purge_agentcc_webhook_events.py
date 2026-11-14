from django.core.management.base import BaseCommand, CommandError

from agentcc.services.webhook_hygiene import BATCH_SIZE, purge_webhook_events


class Command(BaseCommand):
    help = (
        "Delete gateway webhook events that are no longer needed: delivered "
        "history, finished (dead-letter, failed, cancelled, expired, dropped) "
        "events, and every event of a deleted webhook. Pending events of a "
        "live webhook are never deleted. Safe on a live install: it deletes "
        "in small batches, one short transaction each, and takes no table lock."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--delivered-older-than-days",
            type=int,
            help="Delete delivered events older than this many days.",
        )
        parser.add_argument(
            "--terminal-older-than-days",
            type=int,
            help=(
                "Delete dead-letter, failed, cancelled, expired and dropped "
                "events older than this many days."
            ),
        )
        parser.add_argument(
            "--deleted-webhooks",
            action="store_true",
            help="Delete every event of a deleted webhook.",
        )
        parser.add_argument(
            "--organization", help="Only this organization's events (UUID)."
        )
        parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
        parser.add_argument("--sleep-seconds", type=float, default=0.1)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count what would be deleted; delete nothing.",
        )

    def handle(self, *args, **options):
        delivered_days = options["delivered_older_than_days"]
        terminal_days = options["terminal_older_than_days"]
        deleted_webhooks = options["deleted_webhooks"]
        if delivered_days is None and terminal_days is None and not deleted_webhooks:
            raise CommandError(
                "Choose what to delete: --delivered-older-than-days, "
                "--terminal-older-than-days and/or --deleted-webhooks."
            )
        for name, days in (
            ("--delivered-older-than-days", delivered_days),
            ("--terminal-older-than-days", terminal_days),
        ):
            if days is not None and days < 0:
                raise CommandError(f"{name} must be 0 or more.")
        if options["batch_size"] < 1:
            raise CommandError("--batch-size must be at least 1.")
        if options["sleep_seconds"] < 0:
            raise CommandError("--sleep-seconds must be 0 or more.")

        dry_run = options["dry_run"]
        counts = purge_webhook_events(
            delivered_older_than_days=delivered_days,
            terminal_older_than_days=terminal_days,
            deleted_webhooks=deleted_webhooks,
            organization_id=options["organization"],
            batch_size=options["batch_size"],
            sleep_seconds=options["sleep_seconds"],
            dry_run=dry_run,
        )
        parts = []
        if delivered_days is not None:
            parts.append(
                f"{counts['delivered']} delivered older than {delivered_days} days"
            )
        if deleted_webhooks:
            parts.append(f"{counts['deleted_webhooks']} of deleted webhooks")
        if terminal_days is not None:
            parts.append(
                f"{counts['terminal']} terminal older than {terminal_days} days"
            )
        verb = "Would delete" if dry_run else "Deleted"
        self.stdout.write(
            f"{verb} {sum(counts.values())} webhook events: {', '.join(parts)}."
        )
