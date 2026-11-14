"""Remove expired dataset comparison objects from object storage, once.

The scheduled cleanup (``delete_unused_compare_folder``) already does this on
every run, within its time limit. Run this once when rolling out the private
compare bucket, so that comparison objects an earlier release left under
``compare/`` in the upload bucket are removed without waiting for the
scheduled runs. It exits with an error when anything expired is left (it
stopped at its time limit or a storage call failed); run it again then.

Usage:
    python manage.py sweep_compare_objects
    python manage.py sweep_compare_objects --max-seconds 7200
"""

import time

from django.core.management.base import BaseCommand, CommandError

from model_hub.utils.compare_storage import sweep_remote_compare_objects

DEFAULT_MAX_SECONDS = 60 * 60


class Command(BaseCommand):
    help = (
        "Remove expired dataset comparison objects under compare/ in the "
        "compare bucket and the upload bucket."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-seconds",
            type=int,
            default=DEFAULT_MAX_SECONDS,
            help=(
                "Stop starting deletions after this many seconds, shared between "
                f"the two buckets (default {DEFAULT_MAX_SECONDS})."
            ),
        )

    def handle(self, *args, **options):
        max_seconds = options["max_seconds"]
        if max_seconds <= 0:
            raise CommandError("--max-seconds must be positive.")
        deadline = time.monotonic() + max_seconds
        if not sweep_remote_compare_objects(deadline=deadline):
            raise CommandError(
                "Some expired comparison objects were not removed: the sweep "
                "stopped at --max-seconds or hit a storage error (see the log). "
                "Run the command again."
            )
        self.stdout.write(self.style.SUCCESS("Expired comparison objects removed."))
