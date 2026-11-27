"""
Store a sealed environment release and a suite proved against it.

Build and seal both with ALK first (the suite with a PostgreSQL proof), for example:
    python -m fi.alk.environments.appointment_booking.build --out DIR --postgres-dsn DSN

Usage:
    python manage.py register_harness_release --environment DIR/environment --suite DIR/suite
    python manage.py register_harness_release ... --organization <organization_id>

Without --organization the release is a system release every organization may compose.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Organization
from simulate.services.harness_releases import register_release
from simulate.services.hosted_harness import HostedHarnessError


class Command(BaseCommand):
    help = "Register a sealed environment release and suite release from ALK."

    def add_arguments(self, parser):
        parser.add_argument("--environment", required=True, type=Path)
        parser.add_argument("--suite", required=True, type=Path)
        parser.add_argument("--organization")

    def handle(self, *args, **options):
        organization = (
            Organization.objects.get(id=options["organization"])
            if options["organization"]
            else None
        )
        try:
            environment, suite = register_release(
                options["environment"], options["suite"], organization=organization
            )
        except HostedHarnessError as exc:
            raise CommandError(f"{exc.code}: {exc.message}") from exc
        self.stdout.write(
            json.dumps(
                {
                    "environment": {
                        "id": str(environment.id),
                        "digest": environment.digest,
                    },
                    "suite": {"id": str(suite.id), "digest": suite.digest},
                }
            )
        )
