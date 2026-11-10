"""Bounded event-time preparation, including retry after source lookup failure."""

from datetime import timedelta

import pytest

from tracer.models.trace_scan import TraceScanIssue
from tracer.services.feed_escalation_evidence import prepare_issue_evidence
from tracer.tests.test_feed_escalation_inputs import add_scan_finding
from tracer.tests.test_feed_escalation_inputs import legacy_issue as _legacy_issue
from tracer.tests.test_feed_escalation_policy import T

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
legacy_issue = _legacy_issue


def _traces_at(at, provenance="trace_end", calls=None):
    """Stand-in for the batched root-span read."""

    def source(project_id, trace_ids):
        if calls is not None:
            calls.append(set(trace_ids))
        return {str(t): (at, provenance) for t in trace_ids}

    return source


def test_prepare_uses_original_completion_time_once_per_trace(
    legacy_issue, monkeypatch
):
    issue, scan = legacy_issue
    for _ in range(3):
        add_scan_finding(issue, scan, None)
    calls = []
    monkeypatch.setattr(
        "tracer.services.feed_escalation_evidence.capture_trace_occurrences",
        _traces_at(T - timedelta(hours=2), calls=calls),
    )
    result = prepare_issue_evidence(issue.pk, limit=100, now=T)
    assert result["prepared"] == 3
    assert calls == [{str(scan.trace_id)}]
    assert (
        list(
            TraceScanIssue.no_workspace_objects.filter(cluster=issue).values_list(
                "occurred_at", flat=True
            )
        )
        == [T - timedelta(hours=2)] * 3
    )
    issue.refresh_from_db()
    assert issue.status == "for_review"
    assert issue.escalation_state["pending_input_revision"] == 1


def test_unavailable_source_time_remains_null_and_manual_status_is_preserved(
    legacy_issue, monkeypatch
):
    issue, scan = legacy_issue
    finding = add_scan_finding(issue, scan, None)
    issue.status_origin = "manual"
    issue.status = "escalating"
    issue.save(update_fields=["status", "status_origin"])
    monkeypatch.setattr(
        "tracer.services.feed_escalation_evidence.capture_trace_occurrences",
        _traces_at(None, ""),
    )
    result = prepare_issue_evidence(issue.pk, now=T)
    finding.refresh_from_db()
    issue.refresh_from_db()
    assert result["unresolved"] == 1
    assert finding.occurred_at is None
    assert issue.status == "escalating" and issue.status_origin == "manual"
    assert issue.escalation_state == {}


def test_preparation_is_bounded_idempotent_and_dry_run_does_not_write(
    legacy_issue, monkeypatch
):
    issue, scan = legacy_issue
    for _ in range(3):
        add_scan_finding(issue, scan, None)
    monkeypatch.setattr(
        "tracer.services.feed_escalation_evidence.capture_trace_occurrences",
        _traces_at(T),
    )
    preview = prepare_issue_evidence(issue.pk, limit=2, dry_run=True, now=T)
    assert preview["prepared"] == 2
    assert (
        TraceScanIssue.no_workspace_objects.filter(
            cluster=issue, occurred_at__isnull=True
        ).count()
        == 3
    )
    prepare_issue_evidence(issue.pk, limit=2, now=T)
    assert (
        TraceScanIssue.no_workspace_objects.filter(
            cluster=issue, occurred_at__isnull=True
        ).count()
        == 1
    )
    prepare_issue_evidence(issue.pk, limit=2, now=T)
    assert prepare_issue_evidence(issue.pk, now=T)["prepared"] == 0


def test_eval_retry_preparation_reuses_immutable_occurrence_time(
    custom_eval_config, monkeypatch
):
    import uuid

    from tracer.models.observation_span import EvalLogger
    from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup

    issue = TraceErrorGroup.no_workspace_objects.create(
        project=custom_eval_config.project,
        cluster_id="PREPARE-EVAL",
        source="eval",
        eval_config=custom_eval_config,
        eval_target_type="trace",
        error_type="quality",
    )
    trace_id = uuid.uuid4()
    rows = []
    for index, at in enumerate((T - timedelta(hours=2), None)):
        log = EvalLogger.no_workspace_objects.create(
            custom_eval_config=custom_eval_config,
            target_type="trace",
            trace_id=trace_id,
            observation_span_id="root-source-span",
            output_bool=False,
            config_hash="a" * 64,
            eval_task_id=f"retry-{index}",
        )
        rows.append(
            ErrorClusterTraces.no_workspace_objects.create(
                cluster=issue,
                eval_logger=log,
                trace_id=trace_id,
                occurred_at=at,
                occurrence_time_source="trace_end" if at else "",
            )
        )
    monkeypatch.setattr(
        "tracer.services.feed_escalation_evidence.capture_trace_occurrences",
        _traces_at(T),
    )
    prepare_issue_evidence(issue.pk, now=T)
    rows[-1].refresh_from_db()
    assert rows[-1].occurred_at == T - timedelta(hours=2)


def test_all_traces_in_a_batch_share_one_root_span_read(observe_project, monkeypatch):
    import uuid

    from tracer.models.trace_error_analysis import TraceErrorGroup
    from tracer.models.trace_scan import TraceScanResult

    issue = TraceErrorGroup.no_workspace_objects.create(
        project=observe_project, cluster_id="S-BATCH", error_type="tool"
    )
    scans = [
        TraceScanResult.no_workspace_objects.create(
            project=observe_project,
            trace_id=uuid.uuid4(),
            status="completed",
            has_issues=True,
        )
        for _ in range(5)
    ]
    for scan in scans:
        add_scan_finding(issue, scan, None)
    reads = []

    class Reader:
        def list_root_spans_by_trace_ids(self, trace_ids, **kwargs):
            reads.append(set(trace_ids))
            # One trace has no root span yet: only that row stays unresolved.
            return {
                t: {"trace_id": t, "end_time": T - timedelta(hours=1)}
                for t in trace_ids
                if t != str(scans[0].trace_id)
            }

    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: Reader())
    result = prepare_issue_evidence(issue.pk, now=T)
    assert reads == [{str(scan.trace_id) for scan in scans}]
    assert result == {"prepared": 4, "unresolved": 1}


def test_rows_recorded_before_the_evaluation_range_are_not_repaired(
    legacy_issue, monkeypatch
):
    from tracer.services.feed_escalation_inputs import evidence_since

    issue, scan = legacy_issue
    old = add_scan_finding(issue, scan, None)
    recent = add_scan_finding(issue, scan, None)
    since = evidence_since(T)
    TraceScanIssue.no_workspace_objects.filter(pk=old.pk).update(
        created_at=since - timedelta(seconds=1)
    )
    TraceScanIssue.no_workspace_objects.filter(pk=recent.pk).update(created_at=since)
    calls = []
    monkeypatch.setattr(
        "tracer.services.feed_escalation_evidence.capture_trace_occurrences",
        _traces_at(T - timedelta(minutes=30), calls=calls),
    )
    assert prepare_issue_evidence(issue.pk, now=T, since=since) == {
        "prepared": 1,
        "unresolved": 0,
    }
    old.refresh_from_db()
    recent.refresh_from_db()
    assert old.occurred_at is None
    assert recent.occurred_at == T - timedelta(minutes=30)


def test_stuck_issue_with_only_old_unresolvable_history_evaluates_without_lookups(
    legacy_issue, monkeypatch
):
    from tracer.services.feed_escalation import evaluate_issue
    from tracer.services.feed_escalation_inputs import evidence_since

    issue, scan = legacy_issue
    issue.status, issue.status_origin = "for_review", "system"
    issue.escalation_state = {"evaluation_error": "CountUnavailable"}
    issue.save(update_fields=["status", "status_origin", "escalation_state"])
    now = T + timedelta(minutes=10)
    old = add_scan_finding(issue, scan, None)
    TraceScanIssue.no_workspace_objects.filter(pk=old.pk).update(
        created_at=evidence_since(T) - timedelta(days=30)
    )
    add_scan_finding(issue, scan, T - timedelta(minutes=20))
    calls = []
    monkeypatch.setattr(
        "tracer.services.feed_escalation_evidence.capture_trace_occurrences",
        _traces_at(None, "", calls=calls),
    )
    assert evaluate_issue(issue.pk, now=now) == "evaluated"
    issue.refresh_from_db()
    assert calls == []
    assert issue.escalation_state["evaluation_error"] is None
    assert issue.escalation_state["reason"]["current_count"] == 1
