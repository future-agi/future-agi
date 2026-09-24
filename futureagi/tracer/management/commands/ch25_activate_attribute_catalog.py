"""Retired span-only catalog activation entrypoint.

The old activation path addressed the deleted six-table snapshot catalog.
Keeping this zero-I/O command stub makes stale operator jobs fail closed and
points them at the unified rollout command.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

REPLACEMENT_COMMAND = "ch25_property_catalog_dev_rollout"
RETIRED_MESSAGE = (
    "ch25_activate_attribute_catalog is retired and performs zero I/O; use "
    f"{REPLACEMENT_COMMAND} for the unified property catalog"
)


class Command(BaseCommand):
    help = RETIRED_MESSAGE
    requires_system_checks: list[str] = []
    requires_migrations_checks = False

    def add_arguments(self, parser: CommandParser) -> None:
        for option in (
            "--environment",
            "--ack",
            "--project-id",
            "--since",
            "--until",
            "--epoch",
            "--target-database",
            "--supersession-ack",
        ):
            parser.add_argument(option)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--allow-projection-supersession", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        raise CommandError(RETIRED_MESSAGE)


__all__ = ["Command", "REPLACEMENT_COMMAND", "RETIRED_MESSAGE"]
