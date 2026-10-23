"""Actual commit/rollback and concurrent manual ownership fencing."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from django.db import close_old_connections, transaction

from tracer.services import feed_escalation
from tracer.tests.test_feed_escalation_inputs import add_scan_finding
from tracer.tests.test_feed_escalation_inputs import legacy_issue as _legacy_issue
from tracer.tests.test_feed_escalation_policy import T

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
legacy_issue = _legacy_issue


@pytest.fixture(autouse=True)
def enabled_rollout(settings):
    settings.ERROR_FEED_ESCALATION_MODE = "enabled"
    settings.ERROR_FEED_ESCALATION_WORKSPACE_IDS = ("*",)
    settings.ERROR_FEED_ESCALATION_SOURCES = ("scanner", "eval")


def test_publication_wakes_only_after_commit_and_rollback_discards_pending(
    legacy_issue, monkeypatch
):
    issue, _scan = legacy_issue
    wakes = []
    monkeypatch.setattr(feed_escalation, "_wake_issue", lambda key: wakes.append(key))
    with transaction.atomic():
        feed_escalation.request_evaluation(issue.pk, now=T)
        assert wakes == []
    assert wakes == [str(issue.pk)]
    issue.refresh_from_db()
    assert issue.escalation_state["pending_input_revision"] == 1
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            feed_escalation.request_evaluation(issue.pk, now=T)
            raise RuntimeError("publication rolled back")
    issue.refresh_from_db()
    assert issue.escalation_state["pending_input_revision"] == 1
    assert wakes == [str(issue.pk)]


def test_concurrent_user_selection_wins_over_inflight_evaluation(
    legacy_issue, user, monkeypatch
):
    from tracer.queries.feed import update_cluster
    from tracer.types.feed_types import FeedUpdatePayload

    issue, scan = legacy_issue
    for _ in range(20):
        add_scan_finding(issue, scan, T - timedelta(minutes=20))
    for minute in (0, 5, 10):
        feed_escalation.evaluate_issue(issue.pk, now=T + timedelta(minutes=minute + 10))
    entered, resume = Event(), Event()
    original = feed_escalation.count_occurrences

    def count_then_wait(*args, **kwargs):
        counts = original(*args, **kwargs)
        entered.set()
        assert resume.wait(10), "manual-edit thread did not resume evaluation"
        return counts

    monkeypatch.setattr(feed_escalation, "count_occurrences", count_then_wait)
    monkeypatch.setattr(
        "tracer.queries.feed.get_cluster_detail", lambda *args: "detail"
    )

    def run_evaluator():
        close_old_connections()
        try:
            return feed_escalation.evaluate_issue(
                issue.pk, now=T + timedelta(minutes=25)
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(run_evaluator)
        try:
            assert entered.wait(10), "evaluator never reached the count boundary"
            update_cluster(
                issue.cluster_id,
                [str(issue.project_id)],
                FeedUpdatePayload(status="for_review", actor_id=user.id),
            )
        finally:
            resume.set()
        assert worker.result(timeout=10) == "skipped"
    issue.refresh_from_db()
    assert issue.status == "for_review"
    assert issue.status_origin == "manual"
    assert issue.status_revision == 1
    assert issue.status_changed_by_id == user.id


def test_changed_membership_evidence_cannot_publish_old_calculation(
    legacy_issue, monkeypatch
):
    issue, scan = legacy_issue
    for _ in range(20):
        add_scan_finding(issue, scan, T - timedelta(minutes=20))
    monkeypatch.setattr(feed_escalation, "_wake_issue", lambda key: None)
    original = feed_escalation.count_occurrences

    def changed_after_query(*args, **kwargs):
        counts = original(*args, **kwargs)
        feed_escalation.request_evaluation(issue.pk, now=T)
        return counts

    monkeypatch.setattr(feed_escalation, "count_occurrences", changed_after_query)
    assert (
        feed_escalation.evaluate_issue(issue.pk, now=T + timedelta(minutes=10))
        == "stale"
    )
    issue.refresh_from_db()
    assert issue.status == "for_review"
    assert "progress" not in issue.escalation_state
    assert issue.escalation_state["pending_input_revision"] == 1


def test_competing_first_settings_saves_have_one_winner(workspace, user, monkeypatch):
    from tracer.models.feed_escalation import FeedEscalationPolicy
    from tracer.services.feed_escalation_settings import PolicyConflict, update_policy

    FeedEscalationPolicy.no_workspace_objects.filter(workspace=workspace).delete()
    monkeypatch.setattr(
        "tracer.services.feed_escalation_settings._wake_workspace", lambda key: None
    )
    ready = Barrier(2)

    def save(minimum):
        close_old_connections()
        try:
            ready.wait(timeout=10)
            try:
                policy = update_policy(
                    workspace=workspace,
                    actor=user,
                    expected_revision=0,
                    changes={"min_findings": minimum},
                )
                return "saved", policy.min_findings
            except PolicyConflict:
                return "conflict", minimum
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, (25, 30)))
    assert sorted(result for result, _value in outcomes) == ["conflict", "saved"]
    row = FeedEscalationPolicy.no_workspace_objects.get(workspace=workspace)
    assert row.revision == 1
    assert row.min_findings == next(
        value for result, value in outcomes if result == "saved"
    )
    assert row.updated_by_id == user.pk


def test_many_attachments_coalesce_to_one_after_commit_wake(legacy_issue, monkeypatch):
    issue, _scan = legacy_issue
    wakes = []
    monkeypatch.setattr(feed_escalation, "_wake_issue", lambda key: wakes.append(key))
    with transaction.atomic():
        for _ in range(20):
            feed_escalation.request_evaluation(issue.pk, now=T)
        assert wakes == []
    assert wakes == [str(issue.pk)]
    issue.refresh_from_db()
    assert issue.escalation_state["pending_input_revision"] == 20
