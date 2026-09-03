"""Retired span-only catalog backfill entrypoint.

This command intentionally imports no catalog service or database client. It
is retained only so stale automation fails with an actionable replacement
instead of silently targeting the retired six-table schema.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

REPLACEMENT_COMMAND = "ch25_property_catalog_dev_rollout"
RETIRED_MESSAGE = (
    "ch25_backfill_attribute_catalog is retired and performs zero I/O; use "
    f"{REPLACEMENT_COMMAND} for the unified property catalog"
)


class Command(BaseCommand):
    help = RETIRED_MESSAGE
    requires_system_checks: list[str] = []
    requires_migrations_checks = False

    def add_arguments(self, parser: CommandParser) -> None:
        # Parse the retired interface only so existing jobs reach the explicit
        # replacement error. No value is inspected or used.
        for option in (
            "--environment",
            "--cloud-deployment",
            "--dev-identity",
            "--ack",
            "--project-id",
            "--since",
            "--until",
            "--epoch",
            "--source-database",
            "--target-database",
            "--page-rows",
            "--max-windows",
            "--max-runtime-seconds",
            "--max-source-attribute-entries",
            "--max-source-attribute-bytes",
            "--worker-id",
        ):
            parser.add_argument(option)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        raise CommandError(RETIRED_MESSAGE)


__all__ = ["Command", "REPLACEMENT_COMMAND", "RETIRED_MESSAGE"]
