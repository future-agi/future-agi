"""Preview or explicitly activate behavior grouping for one simulation registry."""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from tracer.constants.grouping_versions import (
    GROUPING_POLICY_VERSION,
    SAMPLED_GROUPING_POLICY_VERSION,
    SIMULATION_FEATURE_POLICY_VERSION,
    SIMULATION_GROUPING_POLICY_VERSION,
)
from tracer.models.trace_grouping import (
    GroupingFeatureState,
    GroupingWorkState,
    TraceGroupingAttempt,
    TraceGroupingCall,
    TraceGroupingFeatureJob,
    TraceGroupingScope,
    TraceGroupingWork,
)
from tracer.models.trace_investigation import (
    InvestigationWorkload,
    TraceInvestigationFinding,
    TraceInvestigationGroupingStatus,
    TraceInvestigationReport,
)
from tracer.queries.grouping import GROUPABLE_RECOVERY
from tracer.services.grouping.control import _eligible_project
from tracer.services.grouping_features import enqueue_grouping_features


class Command(BaseCommand):
    help = "Preview simulation behavior grouping. --apply rebuilds features and requeues unassigned findings without rerunning investigation."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=uuid.UUID, required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--expected-registry-revision", type=int)

    def handle(self, *args, **options):
        with transaction.atomic():
            scope = (
                # Feature claims and completion take this lock before job rows.
                TraceGroupingScope.no_workspace_objects.select_for_update(no_key=True)
                .filter(project_id=options["project_id"])
                .first()
            )
            if scope is None:
                raise CommandError("Project has no grouping scope")
            reports = TraceInvestigationReport.no_workspace_objects.filter(
                project_id=scope.project_id,
                is_current=True,
                source="omega",
                execution_status="completed",
            )
            if (
                not reports.exists()
                or reports.exclude(
                    workload_type=InvestigationWorkload.SIMULATION_TEST_EXECUTION
                ).exists()
            ):
                raise CommandError(
                    "Activation requires an exclusively simulation registry"
                )
            pending = TraceInvestigationFinding.no_workspace_objects.filter(
                report__in=reports,
                recovery__in=GROUPABLE_RECOVERY,
                cluster__isnull=True,
            ).count()
            self.stdout.write(
                f"Project {scope.project_id}: {scope.policy_version} -> {SIMULATION_GROUPING_POLICY_VERSION}; "
                f"registry revision {scope.registry_revision}; {reports.count()} reports; {pending} unassigned findings"
            )
            if not options["apply"]:
                self.stdout.write(
                    "Preview only. Applying preserves memberships, receipts and spend; new feature jobs precede grouping-only rechecks."
                )
                return
            if options["expected_registry_revision"] != scope.registry_revision:
                raise CommandError("Registry revision changed; preview again")
            if scope.policy_version == SIMULATION_GROUPING_POLICY_VERSION:
                self.stdout.write("Already enabled")
                return
            if scope.policy_version not in (
                GROUPING_POLICY_VERSION,
                SAMPLED_GROUPING_POLICY_VERSION,
            ):
                raise CommandError("Unsupported source grouping policy")
            if not _eligible_project(scope.project_id, simulation=True):
                raise CommandError(
                    "Enable simulation grouping and configure its budgets first"
                )
            now = timezone.now()
            if (
                TraceGroupingAttempt.no_workspace_objects.filter(
                    work__scope=scope,
                    state="claimed",
                    lease_expires_at__gte=now,
                ).exists()
                or TraceGroupingCall.no_workspace_objects.filter(
                    scope=scope,
                    status__in=["reserved", "unknown"],
                ).exists()
                or TraceGroupingFeatureJob.no_workspace_objects.filter(
                    report__project_id=scope.project_id,
                    state="running",
                    lease_expires_at__gte=now,
                ).exists()
            ):
                raise CommandError(
                    "Drain active grouping/feature attempts and resolve outstanding provider usage first"
                )
            # Preserve old feature receipts and the paid-call ledger. The new
            # occurrence population gets a distinct feature-job identity.
            scope.policy_version = SIMULATION_GROUPING_POLICY_VERSION
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
            TraceGroupingFeatureJob.no_workspace_objects.filter(
                report__in=reports
            ).exclude(policy_version=SIMULATION_FEATURE_POLICY_VERSION).update(
                state=GroupingFeatureState.SUPERSEDED, updated_at=now
            )
            TraceGroupingWork.no_workspace_objects.filter(
                scope=scope, report__in=reports
            ).update(
                state=GroupingWorkState.SUPERSEDED,
                updated_at=now,
            )
            jobs = 0
            for report in reports.order_by("id"):
                job = enqueue_grouping_features(report=report)
                report.grouping_status = (
                    TraceInvestigationGroupingStatus.PENDING
                    if job
                    else TraceInvestigationGroupingStatus.NOT_REQUIRED
                )
                report.save(update_fields=["grouping_status", "updated_at"])
                jobs += int(job is not None)
            self.stdout.write(
                f"Enabled; queued {jobs} feature jobs. Existing memberships and spend are preserved. No investigation was rerun."
            )
