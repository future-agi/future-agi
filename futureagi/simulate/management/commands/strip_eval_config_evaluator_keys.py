"""
Strip the evaluator-written keys from every simulate eval attachment's nested config.

Usage:
    python manage.py strip_eval_config_evaluator_keys
"""

from django.core.management.base import BaseCommand

from simulate.models import SimulateEvalConfig
from simulate.services.eval_config_repair import strip_evaluator_keys


class Command(BaseCommand):
    help = (
        "Run once per region after every backend, Temporal-worker and simulation-runner "
        "pod is on the new version, and again about a day later: strips the "
        "evaluator-written keys that old pods, or edit screens opened before the "
        "release, wrote back into simulate eval attachments after migration 0098 ran."
    )

    def handle(self, *args, **options):
        scanned, changed = strip_evaluator_keys(SimulateEvalConfig)
        self.stdout.write(f"scanned={scanned} changed={changed}")
