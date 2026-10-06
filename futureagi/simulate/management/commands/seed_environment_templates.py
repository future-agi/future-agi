"""
Seed the system environment templates committed under simulate/harness_templates.

Every deployment runs this, like seed_system_evals, so all of them serve the same templates.
Idempotent: a template whose committed files have not changed is left alone.

Usage:
    python manage.py seed_environment_templates
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from simulate.services.harness_templates import seed_templates


class Command(BaseCommand):
    help = "Seed the committed system environment templates into this deployment."

    def handle(self, *args, **options):
        outcome = seed_templates()
        for state in ("created", "updated", "unchanged"):
            self.stdout.write(f"{state}: {', '.join(outcome[state]) or '-'}")
