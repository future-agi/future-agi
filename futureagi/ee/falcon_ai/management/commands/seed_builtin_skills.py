"""Seed builtin Falcon skills from YAML definitions.

The skill data lives in ``falcon_ai/builtin_skills/*.yaml`` and is loaded via
``falcon_ai.builtin_skills_loader.seed_builtin_skills``. This command exists
for manual runs and CI checks — the same function is invoked automatically
after every ``migrate`` via a post_migrate signal.
"""

from django.core.management.base import BaseCommand

from ee.falcon_ai.builtin_skills_loader import seed_builtin_skills


class Command(BaseCommand):
    help = "Seed built-in Falcon AI skills from YAML into the DB (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Retained for backwards compatibility. Seeding is already "
                "idempotent — YAML changes propagate on every run."
            ),
        )

    def handle(self, *args, **options):
        created, updated = seed_builtin_skills()
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created} created, {updated} updated."
            )
        )
