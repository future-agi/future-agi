"""
Store a sealed environment release and a suite proved against it.

Build and seal both with ALK first (the suite with a PostgreSQL proof), for example:
    python -m fi.alk.environments.appointment_booking.build --out DIR --postgres-dsn DSN

Usage:
    python manage.py register_harness_release --environment DIR/environment --suite DIR/suite
    python manage.py register_harness_release ... --organization <organization_id>

Without --organization the release is a system release every organization may compose.

Publish a completed design (environment-releases.md §7.9) as a prebuilt environment, a system
release every organization sees:
    python manage.py register_harness_release --from-design <design job id>
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Organization
from simulate.models import HostedHarnessJob
from simulate.services.harness_designs import publish_design
from simulate.services.harness_releases import register_release
from simulate.services.hosted_harness import HostedHarnessError


class Command(BaseCommand):
    help = "Register a sealed environment release and suite release from ALK."

    def add_arguments(self, parser):
        parser.add_argument("--environment", type=Path)
        parser.add_argument("--suite", type=Path)
        parser.add_argument("--organization")
        parser.add_argument("--from-design")

    def handle(self, *args, **options):
        if options["from_design"]:
            if options["environment"] or options["suite"] or options["organization"]:
                raise CommandError("--from-design takes no other release options")
        elif not (options["environment"] and options["suite"]):
            raise CommandError("give --environment and --suite, or --from-design")
        organization = (
            Organization.objects.get(id=options["organization"])
            if options["organization"]
            else None
        )
        try:
            if options["from_design"]:
                environment, suite = publish_design(
                    HostedHarnessJob.no_workspace_objects.get(id=options["from_design"])
                )
            else:
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
