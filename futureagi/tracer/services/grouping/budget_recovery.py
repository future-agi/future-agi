"""Explicit budget recovery. Ledger totals and per-work call limits never reset."""

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from accounts.models import Organization
from tracer.constants.grouping_versions import SAMPLED_GROUPING_POLICY_VERSION
from tracer.models.trace_grouping import (
    GroupingAttemptState,
    GroupingWorkState,
    TraceGroupingAttempt,
    TraceGroupingCall,
    TraceGroupingFindingState,
    TraceGroupingScope,
    TraceGroupingWork,
)
from tracer.queries.grouping import (
    canonical_grouping_source_digest,
    export_grouping_snapshot,
)
from tracer.services.grouping.accounting import _money
from tracer.services.grouping.control import (
    MAX_ATTEMPTS,
    GroupingConflict,
    _live_report,
)


def requeue_budget_work(
    *,
    project_id,
    expected_registry_revision=None,
    apply=False,
    min_reservation_usd="0.01",
    limit=20,
):
    amount = _money(min_reservation_usd)
    if amount <= 0 or not 1 <= limit <= 100:
        raise ValueError("positive minimum reservation and limit 1-100 required")
    with transaction.atomic():
        # Use the same organization -> scope lock order as reserve_call.
        org_id = TraceGroupingScope.no_workspace_objects.get(
            project_id=project_id
        ).organization_id
        Organization.objects.select_for_update().get(pk=org_id)
        scope = TraceGroupingScope.no_workspace_objects.select_for_update().get(
            project_id=project_id
        )
        if scope.policy_version != SAMPLED_GROUPING_POLICY_VERSION:
            raise GroupingConflict("Budget recovery requires sampled grouping policy")
        if apply and expected_registry_revision != scope.registry_revision:
            raise GroupingConflict("Registry revision changed; preview again")
        if apply and (
            TraceGroupingAttempt.no_workspace_objects.filter(
                work__scope=scope,
                state=GroupingAttemptState.CLAIMED,
                lease_expires_at__gte=timezone.now(),
            ).exists()
            or TraceGroupingCall.no_workspace_objects.filter(
                scope=scope, status="reserved"
            ).exists()
        ):
            raise GroupingConflict(
                "Drain active attempts and outstanding reservations first"
            )
        totals = TraceGroupingScope.no_workspace_objects.filter(
            organization_id=org_id
        ).aggregate(spent=Sum("spent_usd"), reserved=Sum("reserved_usd"))
        tenant_committed = (totals["spent"] or Decimal(0)) + (
            totals["reserved"] or Decimal(0)
        )
        results = []
        works = (
            TraceGroupingWork.no_workspace_objects.select_for_update(of=("self",))
            .filter(scope=scope, state=GroupingWorkState.WAITING_BUDGET)
            .select_related("report__job", "feature_job")
            .order_by("id")[:limit]
        )
        for work in works:
            states = list(
                TraceGroupingFindingState.no_workspace_objects.filter(
                    scope=scope,
                    finding__report=work.report,
                    finding__cluster__isnull=True,
                    disposition="waiting_budget",
                    reason__startswith="budget_exhausted:",
                )
            )
            reason = None
            if (
                not states
                or not _live_report(work.report)
                or work.feature_job.state != "ready"
            ):
                reason = "report_no_longer_eligible"
            elif work.attempt_number >= MAX_ATTEMPTS:
                reason = "attempt_limit"
            elif any(
                item.source_digest
                != canonical_grouping_source_digest(
                    export_grouping_snapshot(report=work.report)
                )
                for item in states
            ):
                reason = "source_changed"
            budget_work_id = work.budget_work_id or work.id
            calls = TraceGroupingCall.no_workspace_objects.filter(
                Q(work_id=budget_work_id) | Q(work__budget_work_id=budget_work_id)
            )
            committed = sum(
                (call.cost_usd if call.cost_usd is not None else call.max_cost_usd)
                for call in calls
            )
            if reason is None and getattr(
                settings, "ERROR_FEED_GROUPING_BUDGET_ENFORCED", True
            ):
                for name, used in (
                    ("PROJECT", scope.spent_usd + scope.reserved_usd),
                    ("WORK", committed),
                    ("TENANT", tenant_committed),
                ):
                    budget = _money(
                        getattr(settings, f"ERROR_FEED_GROUPING_{name}_BUDGET_USD", "0")
                    )
                    if budget <= 0 or used + amount > budget:
                        reason = f"{name.lower()}_budget_unavailable"
                        break
            if reason is None and calls.count() >= 100:
                reason = "work_call_limit"
            if reason is None and apply:
                work.state = GroupingWorkState.PENDING
                work.not_before = timezone.now()
                work.save(update_fields=["state", "not_before", "updated_at"])
                # Preserve all paid receipts/spend; the fresh claim excludes assigned findings.
            results.append(
                {
                    "work_id": str(work.id),
                    "waiting_findings": len(states),
                    "eligible": reason is None,
                    "blocked_reason": reason,
                    "requeued": reason is None and apply,
                }
            )
        return {
            "registry_revision": scope.registry_revision,
            "apply": apply,
            "minimum_reservation_usd": str(amount),
            "works": results,
        }
