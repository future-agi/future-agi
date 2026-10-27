"""Eval issues are scored by their own eval; nothing measured reads as no data, not 0.

An eval issue groups failures of one eval config at one unit (trace or span).
Scoring it from span-level evals gave a trace-target eval issue no score, which
the Traces and Trends tabs then showed as 0.00.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)
from tracer.queries import feed

pytestmark = pytest.mark.django_db


def _eval(cfg, span, score, *, target_type=EvalTargetType.TRACE):
    # Trace-target rows carry the root span too; EvalLogger.save() enforces it.
    return EvalLogger.objects.create(
        trace=span.trace,
        observation_span=span,
        custom_eval_config=cfg,
        target_type=target_type,
        output_float=score,
        eval_explanation="the agent repeated the question",
        # Distinct per row: two live evals on one (task, unit, config) collide
        # on the live work-item unique index.
        eval_task_id=f"et-{EvalLogger.objects.count() + 1}",
    )


def _members_without_ch(trace_ids, *_args, **_kwargs):
    return {str(t): feed._CHTraceShim(str(t), None) for t in trace_ids}


@pytest.fixture
def no_clickhouse():
    """Stub the CH-backed reads; the eval scores under test live in PG."""
    with (
        patch.object(feed, "_resolve_member_traces", side_effect=_members_without_ch),
        patch.object(feed, "_get_trace_totals_batch", return_value={}),
        patch.object(feed, "_get_root_spans_batch", return_value={}),
        patch.object(feed, "_get_investigation_reports_batch", return_value={}),
        patch.object(feed, "_session_judges_batch", return_value={}),
        patch.object(feed, "_users_affected_in_window", return_value=0),
        patch.object(feed, "_project_scope_total", return_value=10),
        patch.object(feed, "_fetch_latest_trace_id_batch", return_value={}),
    ):
        yield


@pytest.fixture
def trace_eval_issue(project, trace, observation_span, custom_eval_config):
    """A trace-target eval issue whose trace ALSO carries a span eval at 1.0.

    The span eval is the trap: a span-only read scores this issue 1.0.
    """
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="E-SCORE-1",
        error_type="eval",
        issue_group=custom_eval_config.name,
        source=ClusterSource.EVAL,
        eval_target_type=EvalTargetType.TRACE,
        eval_config=custom_eval_config,
    )
    failing = _eval(custom_eval_config, observation_span, 0.4)
    _eval(custom_eval_config, observation_span, 1.0, target_type=EvalTargetType.SPAN)
    ErrorClusterTraces.objects.create(cluster=cluster, trace=trace, eval_logger=failing)
    return cluster


def test_traces_tab_scores_trace_eval_issue_with_its_own_eval(
    trace_eval_issue, no_clickhouse
):
    project_id = str(trace_eval_issue.project_id)

    tab = feed.get_traces_tab(trace_eval_issue.cluster_id, [project_id])

    assert tab.aggregates.avg_score == pytest.approx(0.4)
    assert [row.score for row in tab.traces] == [pytest.approx(0.4)]
    # No call or conversation was recorded: turns are absent, not zero.
    assert tab.aggregates.avg_turns is None


def test_scanner_issue_keeps_the_span_average(
    project, trace, observation_span, custom_eval_config, no_clickhouse
):
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="S-SCORE-1",
        error_type="scanner",
        issue_group="Tool Failures",
        source=ClusterSource.SCANNER,
    )
    _eval(custom_eval_config, observation_span, 0.4)
    _eval(custom_eval_config, observation_span, 1.0, target_type=EvalTargetType.SPAN)
    ErrorClusterTraces.objects.create(cluster=cluster, trace=trace)

    tab = feed.get_traces_tab(cluster.cluster_id, [str(project.id)])

    assert tab.aggregates.avg_score == pytest.approx(1.0)


def test_issue_with_no_scored_eval_reports_none_not_zero(project, trace, no_clickhouse):
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="S-SCORE-2",
        error_type="scanner",
        issue_group="Tool Failures",
        source=ClusterSource.SCANNER,
    )
    ErrorClusterTraces.objects.create(cluster=cluster, trace=trace)

    tab = feed.get_traces_tab(cluster.cluster_id, [str(project.id)])

    assert tab.aggregates.avg_score is None
    assert tab.aggregates.avg_turns is None


def test_trend_score_uses_the_issue_eval_inside_the_window(
    trace_eval_issue, no_clickhouse
):
    metrics = feed._fetch_trend_metrics(
        trace_eval_issue.cluster_id, str(trace_eval_issue.project_id), days=14
    )

    score = next(m for m in metrics if m.label == "Avg eval score")
    assert score.value == "0.40"


def test_trend_score_is_no_data_when_the_issue_was_quiet_all_window(
    trace_eval_issue, no_clickhouse
):
    quiet_until = timezone.now() + timedelta(days=30)
    with patch.object(feed.timezone, "now", return_value=quiet_until):
        metrics = feed._fetch_trend_metrics(
            trace_eval_issue.cluster_id, str(trace_eval_issue.project_id), days=14
        )

    score = next(m for m in metrics if m.label == "Avg eval score")
    assert score.value == feed._NO_DATA
    assert score.delta == 0.0


def test_sidebar_eval_score_uses_the_issue_eval(trace_eval_issue, no_clickhouse):
    trace_ids, _sessions = feed._cluster_member_ids(
        trace_eval_issue.cluster_id, str(trace_eval_issue.project_id)
    )

    meta = feed._fetch_sidebar_ai_metadata(trace_eval_issue, trace_ids)

    assert meta.eval_score == pytest.approx(0.4)
