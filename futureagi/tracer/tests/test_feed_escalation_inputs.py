"""Event-time, current-evidence, deduplication and tenant boundaries."""

import uuid
from datetime import timedelta

import pytest

from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.models.trace_grouping import TraceGroupingIssueState, TraceGroupingScope
from tracer.models.trace_investigation import TraceInvestigationFinding
from tracer.models.trace_scan import TraceScanIssue, TraceScanResult
from tracer.services.feed_escalation_inputs import CountUnavailable, count_occurrences
from tracer.tests.test_feed_escalation_policy import T
from tracer.tests.test_grouping_snapshot import _saved_report

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture
def legacy_issue(observe_project):
    cluster = TraceErrorGroup.no_workspace_objects.create(
        project=observe_project,
        cluster_id="S-EVENT-TIME",
        error_type="tool",
    )
    scan = TraceScanResult.no_workspace_objects.create(
        project=observe_project,
        trace_id=uuid.uuid4(),
        status="completed",
        has_issues=True,
    )
    return cluster, scan


def add_scan_finding(cluster, scan, at, *, deleted=False):
    return TraceScanIssue.no_workspace_objects.create(
        scan_result=scan,
        cluster=cluster,
        category="tool",
        group="Tool Failures",
        fix_layer="Tools",
        brief="Distinct failure",
        occurred_at=at,
        occurrence_time_source="trace_end",
        deleted=deleted,
    )


def test_two_findings_on_one_trace_count_as_two_findings(legacy_issue):
    cluster, scan = legacy_issue
    for _ in range(2):
        add_scan_finding(cluster, scan, T - timedelta(minutes=20))
    result = count_occurrences(cluster, T)
    assert result.current == 2
    assert result.historical == 0
    assert result.affected_traces == 1
    assert result.unit == "findings"


def test_half_open_windows_and_deleted_findings(legacy_issue):
    cluster, scan = legacy_issue
    for at in (
        T - timedelta(hours=25),
        T - timedelta(hours=1),
        T,
        T + timedelta(minutes=1),
    ):
        add_scan_finding(cluster, scan, at)
    add_scan_finding(cluster, scan, T - timedelta(minutes=20), deleted=True)
    result = count_occurrences(cluster, T)
    assert result.current == 1
    assert result.historical == 1


def test_missing_timestamp_is_an_error_not_zero(legacy_issue):
    cluster, scan = legacy_issue
    add_scan_finding(cluster, scan, None)
    with pytest.raises(CountUnavailable):
        count_occurrences(cluster, T)


def test_empty_windows_are_zero_without_source_health_checks(legacy_issue):
    cluster, _scan = legacy_issue
    result = count_occurrences(cluster, T)
    assert result.current == result.historical == result.affected_traces == 0


def test_wrong_project_scan_is_not_evidence(legacy_issue, project):
    cluster, _scan = legacy_issue
    foreign_scan = TraceScanResult.no_workspace_objects.create(
        project=project,
        trace_id=uuid.uuid4(),
        status="completed",
        has_issues=True,
    )
    add_scan_finding(cluster, foreign_scan, T - timedelta(minutes=20))
    assert count_occurrences(cluster, T).current == 0


def test_omega_counts_distinct_current_canonical_findings(observe_project):
    report = _saved_report(observe_project)
    report.grouping_status = "completed"
    report.save(update_fields=["grouping_status"])
    cluster = TraceErrorGroup.no_workspace_objects.create(
        project=observe_project,
        cluster_id="S-OMEGA-TIME",
        error_type="finding",
    )
    scope = TraceGroupingScope.no_workspace_objects.create(
        project=observe_project,
        organization_id=observe_project.organization_id,
        workspace_id=observe_project.workspace_id,
    )
    state = TraceGroupingIssueState.no_workspace_objects.create(
        scope=scope, cluster=cluster
    )
    first = report.findings.get(finding_id="finding-1")
    first.cluster = cluster
    first.save(update_fields=["cluster"])
    second = TraceInvestigationFinding.no_workspace_objects.create(
        id=uuid.uuid4(),
        report=report,
        finding_id="another-finding",
        ordinal=99,
        statement="Another defect",
        cluster=cluster,
    )
    for finding in (first, second):
        ErrorClusterTraces.no_workspace_objects.create(
            cluster=cluster,
            finding=finding,
            trace_id=report.trace_id,
            occurred_at=T - timedelta(minutes=20),
            occurrence_time_source="omega_root_end",
        )
    assert count_occurrences(cluster, T).current == 2
    assert count_occurrences(cluster, T).affected_traces == 1
    # A malformed duplicate join must not inflate the finding count.
    ErrorClusterTraces.no_workspace_objects.create(
        cluster=cluster,
        finding=first,
        trace_id=uuid.uuid4(),
        occurred_at=T - timedelta(minutes=20),
    )
    assert count_occurrences(cluster, T).current == 2
    report.is_current = False
    report.save(update_fields=["is_current"])
    assert count_occurrences(cluster, T).current == 0
    report.is_current = True
    report.save(update_fields=["is_current"])
    state.retired = True
    state.save(update_fields=["retired"])
    assert count_occurrences(cluster, T).current == 0


@pytest.mark.parametrize("target", ["span", "trace", "session"])
def test_eval_retries_deduplicate_configuration_version_and_target(
    custom_eval_config, target, monkeypatch
):
    from types import SimpleNamespace

    from tracer.models.observation_span import EvalLogger

    cluster = TraceErrorGroup.no_workspace_objects.create(
        project=custom_eval_config.project,
        cluster_id=f"E-COUNT-{target}",
        error_type="quality",
        source="eval",
        eval_target_type=target,
        eval_config=custom_eval_config,
    )
    trace_id, span_id, session_id = uuid.uuid4(), "source-span", uuid.uuid4()
    shape = (
        {"trace_session_id": session_id}
        if target == "session"
        else {"trace_id": trace_id, "observation_span_id": span_id}
    )
    for version in ("a", "a", "b"):
        log = EvalLogger.no_workspace_objects.create(
            custom_eval_config=custom_eval_config,
            target_type=target,
            **shape,
            output_bool=False,
            eval_explanation="Independent failure",
            config_hash=version * 64,
            eval_task_id=str(uuid.uuid4()),
        )
        ErrorClusterTraces.no_workspace_objects.create(
            cluster=cluster,
            eval_logger=log,
            trace_id=None if target == "session" else trace_id,
            occurred_at=T - timedelta(minutes=20),
            occurrence_time_source="target_end",
        )
    reader = SimpleNamespace(
        aggregate_by_session_ids=lambda ids, **kwargs: {
            str(session_id): {"traces_count": 3}
        }
    )
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    result = count_occurrences(cluster, T)
    assert result.unit == "failed_evaluations"
    assert result.current == 2
    assert result.affected_traces == (3 if target == "session" else 1)


def test_a_later_passing_eval_supersedes_failed_occurrence(custom_eval_config):
    from tracer.models.observation_span import EvalLogger

    cluster = TraceErrorGroup.no_workspace_objects.create(
        project=custom_eval_config.project,
        cluster_id="E-RECOVERED",
        error_type="quality",
        source="eval",
        eval_target_type="span",
        eval_config=custom_eval_config,
    )
    shape = {"trace_id": uuid.uuid4(), "observation_span_id": "fixed-span"}
    log = EvalLogger.no_workspace_objects.create(
        custom_eval_config=custom_eval_config,
        target_type="span",
        **shape,
        output_bool=False,
        eval_explanation="Failed",
        config_hash="a" * 64,
        eval_task_id="first",
    )
    ErrorClusterTraces.no_workspace_objects.create(
        cluster=cluster,
        eval_logger=log,
        trace_id=shape["trace_id"],
        occurred_at=T - timedelta(minutes=20),
        occurrence_time_source="span_end",
    )
    EvalLogger.no_workspace_objects.create(
        custom_eval_config=custom_eval_config,
        target_type="span",
        **shape,
        output_bool=True,
        eval_explanation="Passed",
        config_hash="a" * 64,
        eval_task_id="second",
    )
    assert count_occurrences(cluster, T).current == 0
