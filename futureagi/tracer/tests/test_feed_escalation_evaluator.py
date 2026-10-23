"""Persistence, silence recovery, retries and explicit manual status intent."""

from datetime import timedelta

import pytest

from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.services.feed_escalation import (
    evaluate_issue,
    request_evaluation,
    sweep_issues,
)
from tracer.services.feed_escalation_settings import update_policy
from tracer.tests.test_feed_alerts import slack_connection as _slack_connection
from tracer.tests.test_feed_escalation_inputs import add_scan_finding
from tracer.tests.test_feed_escalation_inputs import legacy_issue as _legacy_issue
from tracer.tests.test_feed_escalation_policy import T
from tracer.types.feed_types import FeedUpdatePayload

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
legacy_issue = _legacy_issue
slack_connection = _slack_connection


@pytest.fixture(autouse=True)
def enabled_rollout(settings):
    settings.ERROR_FEED_ESCALATION_MODE = "enabled"
    settings.ERROR_FEED_ESCALATION_WORKSPACE_IDS = ("*",)
    settings.ERROR_FEED_ESCALATION_SOURCES = ("scanner", "eval")


def tick(cluster, minute=0):
    result = evaluate_issue(cluster.pk, now=T + timedelta(minutes=minute + 10))
    cluster.refresh_from_db()
    return result


def test_new_issue_starts_for_review_with_system_ownership(legacy_issue):
    cluster, _scan = legacy_issue
    assert cluster.status == "for_review"
    assert cluster.status_origin == "system"


def test_real_findings_confirm_escalation_and_recover_without_arrivals(legacy_issue):
    cluster, scan = legacy_issue
    for _ in range(20):
        add_scan_finding(cluster, scan, T - timedelta(minutes=20))
    request_evaluation(cluster.pk, now=T + timedelta(minutes=10))
    for minute in (0, 5, 10):
        tick(cluster, minute)
        assert cluster.status == "for_review"
    tick(cluster, 15)
    assert cluster.status == "escalating"
    assert cluster.status_revision == 1
    assert cluster.escalation_state["reason"]["current_count"] == 20
    tick(cluster, 15)
    assert cluster.status_revision == 1
    for minute in (20, 25, 30, 35, 40, 45, 50, 55):
        tick(cluster, minute)
    assert cluster.status == "for_review"
    assert cluster.status_revision == 2
    assert cluster.escalation_state["reason"]["current_count"] == 0


@pytest.mark.parametrize(
    "status", ["for_review", "escalating", "acknowledged", "resolved"]
)
def test_manual_selection_is_preserved_during_growth_and_quiet(legacy_issue, status):
    cluster, scan = legacy_issue
    cluster.status = status
    cluster.status_origin = "manual"
    cluster.save(update_fields=["status", "status_origin"])
    for _ in range(40):
        add_scan_finding(cluster, scan, T - timedelta(minutes=20))
    for minute in (0, 5, 10, 15, 80):
        tick(cluster, minute)
    assert cluster.status == status
    assert cluster.status_revision == 0


def test_same_value_manual_patch_records_ownership(legacy_issue, user):
    from tracer.queries.feed import _set_cluster_update_fields

    cluster, _scan = legacy_issue
    fields = _set_cluster_update_fields(
        cluster,
        FeedUpdatePayload(status="for_review", actor_id=user.id),
        changed_only=True,
    )
    cluster.save(update_fields=fields)
    cluster.refresh_from_db()
    assert cluster.status_origin == "manual"
    assert cluster.status_changed_by_id == user.id
    assert cluster.status_revision == 1


def test_settings_save_does_not_change_status_and_sweep_finds_mismatch(
    legacy_issue, workspace, user, monkeypatch
):
    cluster, scan = legacy_issue
    for _ in range(20):
        add_scan_finding(cluster, scan, T - timedelta(minutes=20))
    tick(cluster)
    monkeypatch.setattr("django.utils.timezone.now", lambda: T + timedelta(minutes=11))
    policy = update_policy(
        workspace=workspace,
        actor=user,
        expected_revision=0,
        changes={"min_findings": 30},
    )
    cluster.refresh_from_db()
    assert cluster.status == "for_review"
    result = sweep_issues(now=T + timedelta(minutes=15), workspace_id=workspace.id)
    cluster.refresh_from_db()
    assert result["evaluated"] == 1
    assert cluster.escalation_state["evaluated_policy_revision"] == policy.revision
    assert cluster.escalation_state["progress"]["entry_checks"] == 0


def test_settings_save_waits_for_next_settled_boundary(
    legacy_issue, workspace, user, monkeypatch
):
    cluster, scan = legacy_issue
    for _ in range(20):
        add_scan_finding(cluster, scan, T - timedelta(minutes=20))
    tick(cluster)
    monkeypatch.setattr("django.utils.timezone.now", lambda: T + timedelta(minutes=11))
    policy = update_policy(
        workspace=workspace, actor=user, expected_revision=0, changes={"entry_delta": 9}
    )
    assert evaluate_issue(cluster.pk, now=T + timedelta(minutes=12)) == "skipped"
    cluster.refresh_from_db()
    assert cluster.status == "for_review"
    assert cluster.escalation_state["evaluated_policy_revision"] == 0
    assert cluster.escalation_next_evaluation_at == T + timedelta(minutes=15)
    assert sweep_issues(now=T + timedelta(minutes=13))["evaluated"] == 0
    assert evaluate_issue(cluster.pk, now=T + timedelta(minutes=15)) == "evaluated"
    cluster.refresh_from_db()
    assert cluster.escalation_state["evaluated_policy_revision"] == policy.revision
    assert cluster.escalation_state["progress"]["entry_checks"] == 1


def test_failed_query_preserves_status_and_resets_candidate(legacy_issue, monkeypatch):
    from tracer.services.feed_escalation_inputs import CountUnavailable

    cluster, scan = legacy_issue
    for _ in range(20):
        add_scan_finding(cluster, scan, T - timedelta(minutes=20))
    tick(cluster)

    def unavailable(*args, **kwargs):
        raise CountUnavailable("missing occurrence timestamp")

    monkeypatch.setattr(
        "tracer.services.feed_escalation.count_occurrences", unavailable
    )
    result = tick(cluster, 5)
    assert result == "error"
    assert cluster.status == "for_review"
    assert cluster.escalation_state["progress"]["entry_checks"] == 0
    assert cluster.escalation_state["reason"]["current_count"] == 20


def test_structural_change_requires_fresh_confirmation(legacy_issue):
    cluster, scan = legacy_issue
    for _ in range(20):
        add_scan_finding(cluster, scan, T - timedelta(minutes=20))
    for minute in (0, 5, 10, 15):
        tick(cluster, minute)
    assert cluster.status == "escalating"
    request_evaluation(cluster.pk, structural=True, now=T + timedelta(minutes=30))
    tick(cluster, 20)
    assert cluster.status == "for_review"
    assert (
        cluster.escalation_state["reason"]["transition_reason"] == "membership_changed"
    )


def test_simulation_is_not_evaluated(legacy_issue):
    from simulate.models.run_test import RunTest
    from simulate.models.test_execution import TestExecution

    cluster, _scan = legacy_issue
    run = RunTest.objects.create(
        name="Simulation",
        organization=cluster.project.organization,
        workspace=cluster.project.workspace,
    )
    cluster.test_execution = TestExecution.objects.create(run_test=run)
    cluster.target_type = "simulation"
    cluster.save(update_fields=["target_type", "test_execution"])
    assert tick(cluster) == "skipped"
    assert cluster.escalation_state == {}


def test_shadow_mode_records_decision_without_changing_status(legacy_issue, settings):
    settings.ERROR_FEED_ESCALATION_MODE = "shadow"
    cluster, scan = legacy_issue
    for _ in range(20):
        add_scan_finding(cluster, scan, T - timedelta(minutes=20))
    for minute in (0, 5, 10, 15):
        tick(cluster, minute)
    assert cluster.status == "for_review"
    assert cluster.status_revision == 0
    assert cluster.escalation_state["shadow_status"] == "escalating"


def test_disable_and_workspace_gate_leave_issues_untouched(legacy_issue, settings):
    cluster, _scan = legacy_issue
    settings.ERROR_FEED_ESCALATION_MODE = "disabled"
    assert tick(cluster) == "skipped"
    assert cluster.escalation_state == {}
    settings.ERROR_FEED_ESCALATION_MODE = "enabled"
    settings.ERROR_FEED_ESCALATION_WORKSPACE_IDS = (
        "99999999-9999-4999-8999-999999999999",
    )
    assert tick(cluster) == "skipped"
    assert cluster.escalation_state == {}


def test_enabling_after_shadow_requires_fresh_confirmation(legacy_issue, settings):
    settings.ERROR_FEED_ESCALATION_MODE = "shadow"
    cluster, scan = legacy_issue
    for _ in range(20):
        add_scan_finding(cluster, scan, T - timedelta(minutes=20))
    for minute in (0, 5, 10, 15):
        tick(cluster, minute)
    settings.ERROR_FEED_ESCALATION_MODE = "enabled"
    tick(cluster, 20)
    assert cluster.status == "for_review"
    assert cluster.escalation_state["progress"]["entry_checks"] == 1


def test_unsettled_findings_are_scheduled_without_requiring_another_attachment(
    legacy_issue,
):
    cluster, scan = legacy_issue
    for _ in range(20):
        add_scan_finding(cluster, scan, T + timedelta(minutes=1))
    request_evaluation(cluster.pk, now=T + timedelta(minutes=10))
    tick(cluster)
    assert cluster.status == "for_review"
    assert cluster.escalation_next_evaluation_at == T + timedelta(minutes=15)
    for minute in (15, 20, 25, 30):
        sweep_issues(
            now=T + timedelta(minutes=minute), workspace_id=cluster.project.workspace_id
        )
    cluster.refresh_from_db()
    assert cluster.status == "escalating"
    assert cluster.status_revision == 1


def test_scheduled_sweep_drains_more_than_one_batch(legacy_issue, monkeypatch):
    from tracer.services.feed_escalation import sweep_feed_escalations

    cluster, _scan = legacy_issue
    siblings = [
        TraceErrorGroup(
            project=cluster.project,
            source="scanner",
            target_type="error_feed",
            cluster_id=f"drain-{index}",
            status="for_review",
            status_origin="system",
        )
        for index in range(501)
    ]
    TraceErrorGroup.no_workspace_objects.bulk_create(siblings)
    visited = []

    def evaluate_and_mark(key, **kwargs):
        visited.append(key)
        TraceErrorGroup.no_workspace_objects.filter(pk=key).update(
            escalation_state={"evaluated_policy_revision": 0},
        )
        return "evaluated"

    monkeypatch.setattr(
        "tracer.services.feed_escalation.evaluate_issue", evaluate_and_mark
    )
    result = sweep_feed_escalations._original_func(
        workspace_id=str(cluster.project.workspace_id)
    )
    assert result["evaluated"] == 502
    assert len(set(visited)) == 502


def test_legacy_merge_requests_fresh_confirmation_and_keeps_occurrence_times(
    legacy_issue,
):
    from tracer.queries.scan_clustering import _merge_pg

    cluster, scan = legacy_issue
    cluster.status = "escalating"
    cluster.save(update_fields=["status"])
    absorbed = TraceErrorGroup.no_workspace_objects.create(
        project=cluster.project,
        cluster_id="S-MERGED",
        error_type="tool",
    )
    for _ in range(20):
        add_scan_finding(absorbed, scan, T - timedelta(minutes=20))
    assert _merge_pg(
        cluster.cluster_id, absorbed.cluster_id, str(cluster.project_id)
    ) == (
        cluster.cluster_id,
        absorbed.cluster_id,
    )
    cluster.refresh_from_db()
    assert cluster.escalation_state["membership_reset_revision"] == 1
    tick(cluster)
    assert cluster.status == "for_review"
    assert cluster.escalation_state["reason"]["current_count"] == 20
    assert (
        cluster.escalation_state["reason"]["transition_reason"] == "membership_changed"
    )


def test_legacy_merge_does_not_discard_manual_for_review_selection(legacy_issue):
    from tracer.queries.scan_clustering import _merge_pg

    cluster, _scan = legacy_issue
    manually_reviewed = TraceErrorGroup.no_workspace_objects.create(
        project=cluster.project,
        cluster_id="S-MANUAL-REVIEW",
        error_type="tool",
        status="for_review",
        status_origin="manual",
    )
    kept, _absorbed = _merge_pg(
        cluster.cluster_id, manually_reviewed.cluster_id, str(cluster.project_id)
    )
    assert kept == manually_reviewed.cluster_id
    manually_reviewed.refresh_from_db()
    assert manually_reviewed.status_origin == "manual"
    assert manually_reviewed.status == "for_review"
    assert manually_reviewed.escalation_state == {}


def test_live_transition_records_alert_event_once(
    legacy_issue, organization, workspace, user, slack_connection, observe_project
):
    from tracer.models.feed_alert import ErrorFeedIssueEvent, FeedAlertTrigger
    from tracer.services.feed_alerts.events import _matches
    from tracer.tests.test_feed_alerts import _rule

    cluster, scan = legacy_issue
    rule = _rule(
        organization,
        workspace,
        user,
        slack_connection,
        observe_project,
        trigger_type=FeedAlertTrigger.ESCALATING,
    )
    for _ in range(20):
        add_scan_finding(cluster, scan, T - timedelta(minutes=20))
    for minute in (0, 5, 10, 15, 15):
        tick(cluster, minute)
    events = ErrorFeedIssueEvent.no_workspace_objects.filter(cluster=cluster)
    assert events.count() == 1
    event = events.get()
    assert event.before["status"] == "for_review"
    assert event.after["status"] == "escalating"
    assert event.source_key == f"escalation:{cluster.pk}:1"
    assert _matches(rule, event)
