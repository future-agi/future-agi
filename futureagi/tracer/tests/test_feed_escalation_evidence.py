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


def test_prepare_uses_original_completion_time_once_per_trace(
    legacy_issue, monkeypatch
):
    issue, scan = legacy_issue
    for _ in range(3):
        add_scan_finding(issue, scan, None)
    calls = []

    def source(**kwargs):
        calls.append(kwargs)
        return T - timedelta(hours=2), "trace_end"

    monkeypatch.setattr(
        "tracer.services.feed_escalation_evidence.capture_occurrence", source
    )
    result = prepare_issue_evidence(issue.pk, limit=100, now=T)
    assert result["prepared"] == 3
    assert len(calls) == 1
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
        "tracer.services.feed_escalation_evidence.capture_occurrence",
        lambda **kwargs: (None, ""),
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
        "tracer.services.feed_escalation_evidence.capture_occurrence",
        lambda **kwargs: (T, "trace_end"),
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
        "tracer.services.feed_escalation_evidence.capture_occurrence",
        lambda **kwargs: (T, "trace_end"),
    )
    prepare_issue_evidence(issue.pk, now=T)
    rows[-1].refresh_from_db()
    assert rows[-1].occurred_at == T - timedelta(hours=2)
