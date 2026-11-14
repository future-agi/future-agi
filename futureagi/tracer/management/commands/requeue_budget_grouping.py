"""Preview and explicitly requeue budget-blocked work; never invokes a model."""

import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from tracer.models.trace_grouping import TraceGroupingScope
from tracer.services.grouping.budget_recovery import requeue_budget_work
from tracer.services.grouping.control import GroupingControlError


class Command(BaseCommand):
    help = "Preview budget recovery. --apply requires reviewed registry revision; spend is never reset."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=uuid.UUID, required=True)
        parser.add_argument("--expected-registry-revision", type=int)
        parser.add_argument("--min-reservation-usd", default="0.01")
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            result = requeue_budget_work(
                project_id=options["project_id"],
                apply=options["apply"],
                expected_registry_revision=options["expected_registry_revision"],
                min_reservation_usd=options["min_reservation_usd"],
                limit=options["limit"],
            )
        except (
            GroupingControlError,
            ValueError,
            TraceGroupingScope.DoesNotExist,
        ) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, sort_keys=True))
