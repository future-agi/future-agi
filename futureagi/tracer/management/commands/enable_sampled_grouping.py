"""Explicit project rollout; never run as part of application startup."""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from tracer.constants.grouping_versions import SAMPLED_GROUPING_POLICY_VERSION
from tracer.models.trace_grouping import (
    TraceGroupingAttempt,
    TraceGroupingCall,
    TraceGroupingScope,
)


class Command(BaseCommand):
    help = "Preview a project's sampled grouping activation. --apply requires the reviewed registry revision."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=uuid.UUID, required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--expected-registry-revision", type=int)

    def handle(self, *args, **options):
        with transaction.atomic():
            scope = (
                TraceGroupingScope.no_workspace_objects.select_for_update()
                .filter(project_id=options["project_id"])
                .first()
            )
            if scope is None:
                raise CommandError("Project has no grouping scope")
            self.stdout.write(
                f"Project {scope.project_id}: {scope.policy_version} -> "
                f"{SAMPLED_GROUPING_POLICY_VERSION}; registry revision {scope.registry_revision}"
            )
            if not options["apply"]:
                self.stdout.write(
                    "Preview only. Existing receipts, failed work and Feed membership are unchanged."
                )
                return
            if options["expected_registry_revision"] != scope.registry_revision:
                raise CommandError("Registry revision changed; preview again")
            if scope.policy_version == SAMPLED_GROUPING_POLICY_VERSION:
                self.stdout.write("Already enabled")
                return
            if scope.policy_version != "f6-minilm/v1":
                raise CommandError("Unsupported source grouping policy")
            if (
                TraceGroupingAttempt.no_workspace_objects.filter(
                    work__scope=scope,
                    state="claimed",
                    lease_expires_at__gte=timezone.now(),
                ).exists()
                or TraceGroupingCall.no_workspace_objects.filter(
                    scope=scope, status="reserved"
                ).exists()
            ):
                raise CommandError(
                    "Drain active attempts and settle outstanding reservations first"
                )
            # Fence old snapshots without rewriting historical algorithm receipts.
            scope.policy_version = SAMPLED_GROUPING_POLICY_VERSION
            scope.registry_revision += 1
            scope.lease_fence += 1
            scope.save(
                update_fields=[
                    "policy_version",
                    "registry_revision",
                    "lease_fence",
                    "updated_at",
                ]
            )
            self.stdout.write(
                "Enabled. No failed work was requeued and no reconciliation was run."
            )
