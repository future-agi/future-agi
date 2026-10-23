"""Durable issue evaluator shared by publication wakes and the five-minute sweep."""

import copy
from datetime import timedelta
from time import monotonic

import structlog
from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import BigIntegerField, F, Q, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce
from django.utils import timezone

from tfc.temporal.drop_in import temporal_activity
from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.models.trace_grouping import TraceGroupingIssueState, TraceGroupingScope
from tracer.services.feed_escalation_inputs import CountUnavailable, count_occurrences
from tracer.services.feed_escalation_policy import (
    ALGORITHM_VERSION,
    CADENCE,
    evaluate,
    settled_boundary,
)
from tracer.services.feed_escalation_settings import get_policy_snapshot, lock_policy

logger = structlog.get_logger(__name__)


def _eligible():
    mode = settings.ERROR_FEED_ESCALATION_MODE
    qs = (
        TraceErrorGroup.no_workspace_objects.filter(
            target_type="error_feed",
            status_origin="system",
            source__in=settings.ERROR_FEED_ESCALATION_SOURCES,
            status__in=["for_review", "escalating"],
            project__workspace__isnull=False,
            project__deleted=False,
            project__workspace__deleted=False,
            project__workspace__is_active=True,
        )
        .exclude(issue_state__dirty=True)
        .exclude(issue_state__retired=True)
    )
    workspaces = settings.ERROR_FEED_ESCALATION_WORKSPACE_IDS
    if mode not in {"shadow", "enabled"}:
        return qs.none()
    if "*" not in workspaces and (mode == "enabled" or workspaces):
        qs = qs.filter(project__workspace_id__in=workspaces)
    return qs


def _input_revision(cluster):
    return cluster.escalation_state.get("pending_input_revision", 0)


def request_evaluation(cluster_id, *, structural=False, now=None):
    """Persist pending work inside the publication transaction, wake after commit."""
    now = now or timezone.now()
    with transaction.atomic():
        cluster = (
            TraceErrorGroup.no_workspace_objects.select_for_update()
            .filter(
                pk=cluster_id,
                target_type="error_feed",
                status_origin="system",
            )
            .first()
        )
        if cluster is None:
            return
        state = dict(cluster.escalation_state)
        already_pending = state.get("pending_input_revision", 0) != state.get(
            "evaluated_input_revision", 0
        )
        state["pending_input_revision"] = state.get("pending_input_revision", 0) + 1
        if structural:
            state["membership_reset_revision"] = (
                state.get("membership_reset_revision", 0) + 1
            )
        cluster.escalation_state = state
        cluster.escalation_next_evaluation_at = now
        cluster.save(
            update_fields=[
                "escalation_state",
                "escalation_next_evaluation_at",
                "updated_at",
            ]
        )
        if not already_pending:
            transaction.on_commit(lambda: _wake_issue(str(cluster_id)))


def _wake_issue(cluster_id):
    try:
        evaluate_feed_issue.delay(cluster_id)
    except Exception:
        logger.exception("feed_escalation_wake_failed", cluster_id=cluster_id)


def _locked_issue(cluster_id, workspace_id):
    """Policy is already locked; follow scope → grouping state → issue order."""
    reference = (
        TraceGroupingIssueState.no_workspace_objects.filter(cluster_id=cluster_id)
        .values("scope_id")
        .first()
    )
    grouping = None
    if reference:
        scope = TraceGroupingScope.no_workspace_objects.select_for_update().get(
            pk=reference["scope_id"]
        )
        grouping = TraceGroupingIssueState.no_workspace_objects.select_for_update().get(
            cluster_id=cluster_id,
            scope=scope,
        )
        if grouping.dirty or grouping.retired:
            return None, grouping
    cluster = (
        _eligible()
        .select_for_update(of=("self",))
        .select_related("project")
        .filter(
            pk=cluster_id,
            project__workspace_id=workspace_id,
        )
        .first()
    )
    return cluster, grouping


def evaluate_issue(cluster_id, *, now=None):
    now = now or timezone.now()
    boundary = settled_boundary(now)
    snapshot = _eligible().select_related("project").filter(pk=cluster_id).first()
    if snapshot is None:
        return "skipped"
    workspace_id = snapshot.project.workspace_id
    policy, policy_saved_at = get_policy_snapshot(workspace_id)
    if (
        policy.revision > 0
        and policy_saved_at is not None
        and snapshot.escalation_state.get("evaluated_policy_revision")
        != policy.revision
        and boundary <= settled_boundary(policy_saved_at)
    ):
        with transaction.atomic():
            locked_policy = lock_policy(workspace_id)
            cluster, _grouping = _locked_issue(cluster_id, workspace_id)
            if (
                cluster is None
                or locked_policy.revision != policy.revision
                or cluster.status_revision != snapshot.status_revision
                or cluster.escalation_state != snapshot.escalation_state
            ):
                return "stale"
            cluster.escalation_state = {
                **cluster.escalation_state,
                "attempted_policy_revision": policy.revision,
            }
            cluster.escalation_next_evaluation_at = (
                settled_boundary(policy_saved_at) + CADENCE + timedelta(minutes=10)
            )
            cluster.save(
                update_fields=[
                    "escalation_state",
                    "escalation_next_evaluation_at",
                    "updated_at",
                ]
            )
        return "skipped"
    if snapshot.escalation_state.get("evaluation_error") == "CountUnavailable":
        from tracer.services.feed_escalation_evidence import prepare_issue_evidence

        repaired = prepare_issue_evidence(snapshot.pk, now=now)
        if repaired["prepared"]:
            # Preparation bumped the durable input revision; read a new snapshot.
            return "stale"
    input_revision = _input_revision(snapshot)
    grouping_revision = (
        TraceGroupingIssueState.no_workspace_objects.filter(cluster_id=cluster_id)
        .values_list("membership_revision", flat=True)
        .first()
    )
    saved = copy.deepcopy(snapshot.escalation_state)
    mode = settings.ERROR_FEED_ESCALATION_MODE
    structural = saved.get("membership_reset_revision", 0) != saved.get(
        "evaluated_membership_reset_revision", 0
    )
    progress = dict(saved.get("progress", {}))
    status = (
        saved.get("shadow_status", snapshot.status)
        if mode == "shadow"
        else snapshot.status
    )
    live_baseline = saved.get("live_frozen_baseline")
    if snapshot.status == "escalating" and live_baseline is None:
        live_baseline = progress.get("frozen_baseline")
    if saved.get("evaluation_mode") and saved["evaluation_mode"] != mode:
        status = snapshot.status
        progress.update(
            entry_checks=0,
            recovery_checks=0,
            frozen_baseline=live_baseline if status == "escalating" else None,
        )
    if structural:
        # Membership invalidation ends only system-owned episodes. It never
        # changes F6 protection, and new volume requires fresh confirmation.
        status, progress = "for_review", {}
    error = None
    try:
        counts = count_occurrences(snapshot, boundary, now=now)
        decision = evaluate(
            status=status,
            origin=snapshot.status_origin,
            state=progress,
            counts=counts,
            policy=policy,
            boundary=boundary,
            input_revision=input_revision,
        )
    except (CountUnavailable, ValueError, DatabaseError) as exc:
        error = type(exc).__name__
        logger.warning(
            "feed_escalation_calculation_failed",
            cluster_id=str(cluster_id),
            error=error,
        )

    with transaction.atomic():
        locked_policy = lock_policy(workspace_id)
        cluster, grouping = _locked_issue(cluster_id, workspace_id)
        if cluster is None:
            return "skipped"
        if (
            locked_policy.revision != policy.revision
            or cluster.status_revision != snapshot.status_revision
            or _input_revision(cluster) != input_revision
            or cluster.escalation_state != saved
            or (grouping.membership_revision if grouping else None) != grouping_revision
        ):
            # The original pending request remains durable. Never apply stale evidence.
            return "stale"
        from tracer.services.feed_alerts.events import (
            issue_snapshot,
            record_issue_event,
        )

        alert_before = issue_snapshot(cluster)
        state = dict(cluster.escalation_state)
        if error:
            old_progress = dict(state.get("progress", {}))
            old_progress["entry_checks"] = old_progress["recovery_checks"] = 0
            if status == "for_review":
                old_progress["frozen_baseline"] = None
            state["progress"] = old_progress
            state["evaluation_error"] = error
            state["attempted_policy_revision"] = policy.revision
            # Last successful reason/timestamp remains visible.
            cluster.escalation_next_evaluation_at = now + CADENCE
        else:
            last = progress.get("last_boundary")
            if last and boundary.isoformat() < last:
                return "stale"
            state.update(
                {
                    "progress": decision.state,
                    "algorithm_version": ALGORITHM_VERSION,
                    "evaluation_mode": mode,
                    "evaluated_policy_revision": policy.revision,
                    "evaluated_input_revision": input_revision,
                    "evaluated_membership_reset_revision": state.get(
                        "membership_reset_revision", 0
                    ),
                    "evaluation_error": None,
                    "attempted_policy_revision": policy.revision,
                    "reason": {
                        "current_count": counts.current,
                        "affected_trace_count": counts.affected_traces,
                        "historical_count": counts.historical,
                        "baseline": float(decision.baseline),
                        "baseline_exact": str(decision.baseline),
                        "entry_threshold": float(decision.entry_threshold),
                        "recovery_threshold": float(decision.recovery_threshold),
                        "unit": counts.unit,
                        "window_start": (boundary - timedelta(hours=1)).isoformat(),
                        "window_end": boundary.isoformat(),
                        "evaluated_at": now.isoformat(),
                        "algorithm_version": ALGORITHM_VERSION,
                        "policy_revision": policy.revision,
                        "input_revision": input_revision,
                        "effective_settings": policy.values(),
                        "entry_checks": decision.state.get("entry_checks", 0),
                        "recovery_checks": decision.state.get("recovery_checks", 0),
                        "transition_reason": "membership_changed"
                        if structural
                        else (
                            "confirmed_growth"
                            if decision.status == "escalating" and decision.transitioned
                            else "confirmed_recovery"
                            if decision.transitioned
                            else "evaluated"
                        ),
                    },
                }
            )
            if mode == "shadow":
                state["shadow_status"] = decision.status
                state["live_frozen_baseline"] = live_baseline
            else:
                state.pop("shadow_status", None)
                state["live_frozen_baseline"] = (
                    decision.state.get("frozen_baseline")
                    if decision.status == "escalating"
                    else None
                )
            if mode == "enabled" and decision.status != cluster.status:
                cluster.status = decision.status
                cluster.status_revision += 1
                cluster.status_changed_at = now
                cluster.status_changed_by = None
            pending_candidate = decision.state.get("entry_checks", 0) > 0
            next_boundary = boundary + CADENCE + timedelta(minutes=10)
            cluster.escalation_next_evaluation_at = (
                next_boundary
                if (decision.status == "escalating" or pending_candidate)
                else None
            )
            if counts.next_occurrence_at is not None:
                eligible_at = (
                    settled_boundary(counts.next_occurrence_at + timedelta(minutes=10))
                    + CADENCE
                    + timedelta(minutes=10)
                )
                cluster.escalation_next_evaluation_at = min(
                    cluster.escalation_next_evaluation_at or eligible_at,
                    eligible_at,
                )
        cluster.escalation_state = state
        cluster.save(
            update_fields=[
                "status",
                "status_revision",
                "status_changed_at",
                "status_changed_by",
                "escalation_state",
                "escalation_next_evaluation_at",
                "updated_at",
            ]
        )
        if cluster.status != alert_before["status"]:
            record_issue_event(
                cluster=cluster,
                before=alert_before,
                source_key=f"escalation:{cluster.pk}:{cluster.status_revision}",
            )
        if not error and (decision.transitioned or structural):
            logger.info(
                "feed_escalation_decision",
                cluster_id=str(cluster.pk),
                workspace_id=str(workspace_id),
                mode=mode,
                previous_status=status,
                decision_status=decision.status,
                boundary=boundary.isoformat(),
                policy_revision=policy.revision,
                current_count=counts.current,
                baseline_exact=str(decision.baseline),
                transition_reason=state["reason"]["transition_reason"],
            )
    return "error" if error else "evaluated"


def _due_issues(now, workspace_id=None):
    qs = _eligible().annotate(
        current_policy_revision=Coalesce(
            F("project__workspace__feed_escalation_policy__revision"),
            Value(0),
            output_field=BigIntegerField(),
        ),
        evaluated_policy_revision=Cast(
            KeyTextTransform("evaluated_policy_revision", "escalation_state"),
            BigIntegerField(),
        ),
        attempted_policy_revision=Cast(
            KeyTextTransform("attempted_policy_revision", "escalation_state"),
            BigIntegerField(),
        ),
    )
    # Failed inputs wait for their retry time. A newly saved policy still wakes
    # them immediately; unchanged policy mismatch must not create a tight loop.
    policy_request = (
        Q(evaluated_policy_revision__isnull=True)
        | ~Q(evaluated_policy_revision=F("current_policy_revision"))
    ) & (
        Q(attempted_policy_revision__isnull=True)
        | ~Q(attempted_policy_revision=F("current_policy_revision"))
    )
    qs = qs.filter(
        Q(escalation_next_evaluation_at__lte=now)
        | Q(status="escalating", escalation_next_evaluation_at__isnull=True)
        | policy_request
    )
    if workspace_id:
        qs = qs.filter(project__workspace_id=workspace_id)
    return qs


def sweep_issues(*, now=None, workspace_id=None, limit=100, exclude_ids=()):
    """Bounded, oldest-due selection; policy mismatch survives a lost wake."""
    now = now or timezone.now()
    if not 1 <= limit <= 500:
        raise ValueError("Sweep limit must be between 1 and 500")
    qs = _due_issues(now, workspace_id).exclude(pk__in=exclude_ids)
    ids = list(
        qs.order_by("escalation_next_evaluation_at", "id").values_list("id", flat=True)[
            :limit
        ]
    )
    result = {"evaluated": 0, "error": 0, "stale": 0, "skipped": 0}
    for cluster_id in ids:
        outcome = evaluate_issue(cluster_id, now=now)
        result[outcome] += 1
    return result


@temporal_activity(time_limit=300, queue="tasks_s")
def evaluate_feed_issue(cluster_id: str):
    return evaluate_issue(cluster_id)


@temporal_activity(time_limit=300, queue="tasks_s")
def sweep_feed_escalations(workspace_id: str | None = None):
    # Drain batches within one settled boundary instead of delaying every 501st
    # issue by five minutes. Leave time to enqueue a continuation before timeout.
    now, started = timezone.now(), monotonic()
    attempted = set()
    result = {"evaluated": 0, "error": 0, "stale": 0, "skipped": 0}
    while monotonic() - started < 240:
        ids = list(
            _due_issues(now, workspace_id)
            .exclude(pk__in=attempted)
            .order_by("escalation_next_evaluation_at", "id")
            .values_list("id", flat=True)[:500]
        )
        if not ids:
            return result
        for cluster_id in ids:
            result[evaluate_issue(cluster_id, now=now)] += 1
            attempted.add(cluster_id)
            if monotonic() - started >= 240:
                break
    if _due_issues(now, workspace_id).exclude(pk__in=attempted).exists():
        sweep_feed_escalations.delay(workspace_id=workspace_id)
    return result
