"""
Register the ready environments the pinned list names, as system releases.

The list is simulate/ready_environments.json, which an agent-learning-kit release updates by pull
request. The bootstrap runs this after `migrate` and `seed_system_evals` on every install and
upgrade; an environment already registered is skipped without a download.

Usage:
    python manage.py seed_ready_environments
    python manage.py seed_ready_environments --list PATH   # another list, e.g. a mirror's
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from simulate.services import ready_environments
from simulate.services.ready_environments import PINNED, ReadyEnvironmentError


class Command(BaseCommand):
    help = "Register the pinned ready environments as system releases."

    def add_arguments(self, parser):
        parser.add_argument("--list", type=Path, default=PINNED)

    def handle(self, *args, **options):
        try:
            seeded = ready_environments.seed(options["list"])
        except ReadyEnvironmentError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"ready environments: {len(seeded.registered)} registered, "
            f"{len(seeded.present)} already present, {len(seeded.failed)} failed"
        )
        for name, reason in seeded.failed.items():
            self.stderr.write(f"  {name}: {reason}")
        if seeded.failed:
            raise CommandError(
                f"{len(seeded.failed)} ready environment(s) were not registered"
            )
