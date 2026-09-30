"""Rebuild environments' scenario rows and scenarios output from their current snapshots."""

import json

from django.core.management.base import BaseCommand, CommandError

from simulate.models import HostedHarnessJob
from simulate.services.scenario_changes import resync_suite


class Command(BaseCommand):
    help = (
        "Rebuild each completed environment's scenario rows and scenarios output from the "
        "snapshot it points at. Prints one JSON line per environment."
    )

    def add_arguments(self, parser):
        parser.add_argument("environment_ids", nargs="*")
        parser.add_argument(
            "--all", action="store_true", help="every completed environment"
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="report, change nothing"
        )

    def handle(self, *args, **options):
        ids = options["environment_ids"]
        if not ids and not options["all"]:
            raise CommandError("name environment ids, or pass --all")
        environments = HostedHarnessJob.no_workspace_objects.filter(
            environment__isnull=True,
            deleted=False,
            state=HostedHarnessJob.State.COMPLETED,
        ).order_by("created_at")
        if ids:
            environments = environments.filter(id__in=ids)
        for environment in environments.iterator():
            try:
                report = resync_suite(environment, dry_run=options["dry_run"])
            except (
                Exception
            ) as exc:  # noqa: BLE001 - one environment must not stop the rest
                report = {
                    "environment": str(environment.id),
                    "outcome": "failed",
                    "why": type(exc).__name__,
                }
            self.stdout.write(json.dumps(report, sort_keys=True))
