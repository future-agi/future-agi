"""
Write a harness-generated environment into the repository as a system template.

Run against the environment the harness authored from simulate/harness_templates/<slug>/agent,
then commit the written files; every deployment picks them up with seed_environment_templates.

Usage:
    python manage.py export_environment_template --job <environment_job_id> --slug <slug> \
        [--domain "Financial services"]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from simulate.models import HostedHarnessJob
from simulate.services.harness_templates import export_template
from simulate.services.hosted_harness import HostedHarnessError


class Command(BaseCommand):
    help = "Export a generated environment as a committed system environment template."

    def add_arguments(self, parser):
        parser.add_argument("--job", required=True, help="Generated environment job id.")
        parser.add_argument("--slug", required=True, help="Template folder name.")
        parser.add_argument("--domain", default=None, help="Business area shown in the library.")

    def handle(self, *args, **options):
        job = HostedHarnessJob.no_workspace_objects.filter(id=options["job"]).first()
        if job is None:
            raise CommandError(f"environment not found: {options['job']}")
        try:
            release = export_template(job, slug=options["slug"], domain=options["domain"])
        except HostedHarnessError as exc:
            raise CommandError(f"{exc.code}: {exc.message}") from exc
        self.stdout.write(
            f"exported {release.slug}: {release.manifest['scenario_count']} scenarios "
            f"-> {release.root}"
        )
