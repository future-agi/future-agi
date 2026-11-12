"""Preview grouping recovery; changes require explicit reviewed IDs and --apply."""

import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from tracer.models.trace_grouping import TraceGroupingScope
from tracer.services.grouping.control import GroupingControlError
from tracer.services.grouping.recovery import requeue_failed_work


class Command(BaseCommand):
    help = "Preview failed grouping recovery. --apply grants bounded retries without resetting history or spend."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=uuid.UUID, required=True)
        parser.add_argument("--work-id", type=uuid.UUID, action="append")
        parser.add_argument("--expected-registry-revision", type=int)
        parser.add_argument("--retry-attempts", type=int, default=1)
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            result = requeue_failed_work(
                project_id=options["project_id"],
                apply=options["apply"],
                expected_registry_revision=options["expected_registry_revision"],
                work_ids=options["work_id"],
                retry_attempts=options["retry_attempts"],
                limit=options["limit"],
            )
        except (
            ValueError,
            GroupingControlError,
            TraceGroupingScope.DoesNotExist,
        ) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(result, sort_keys=True))
