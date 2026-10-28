"""Preview and explicitly requeue unread trace investigations; never invokes a model."""

import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from tracer.services.trace_investigation import (
    InvestigationControlError,
    requeue_unread_investigations,
)


class Command(BaseCommand):
    help = "Preview trace investigations that failed or were cancelled. --apply queues them again."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=uuid.UUID, required=True)
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            result = requeue_unread_investigations(
                project_id=options["project_id"],
                apply=options["apply"],
                limit=options["limit"],
            )
        except (InvestigationControlError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, sort_keys=True))
