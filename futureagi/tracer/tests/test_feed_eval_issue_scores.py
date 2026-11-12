"""Eval issues are scored by their own eval; nothing measured reads as no data, not 0.

An eval issue groups failures of one eval config at one unit (trace or span).
Scoring it from span-level evals gave a trace-target eval issue no score, which
the Traces and Trends tabs then showed as 0.00.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)
from tracer.queries import feed

pytestmark = pytest.mark.django_db


def _eval(cfg, span, score, *, target_type=EvalTargetType.TRACE, **fields):
    # Trace-target rows carry the root span too; EvalLogger.save() enforces it.
    fields.setdefault("eval_explanation", "the agent repeated the question")
    return EvalLogger.objects.create(
        trace=span.trace,
        observation_span=span,
        custom_eval_config=cfg,
        target_type=target_type,
        output_float=score,
        # Distinct per row: two live evals on one (task, unit, config) collide
        # on the live work-item unique index.
        eval_task_id=f"et-{EvalLogger.objects.count() + 1}",
        **fields,
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


def test_overview_scores_the_issue_by_its_own_eval(trace_eval_issue, no_clickhouse):
    with (
        patch.object(feed, "_fetch_events_over_time", return_value=[]),
        patch.object(
            feed, "_fetch_pattern_summary", return_value=feed.PatternSummary()
        ),
        patch.object(feed, "_fetch_success_trace_pass_reel", return_value=[]),
        patch.object(feed, "_cluster_highlight_terms", return_value=[]),
    ):
        overview = feed.get_overview(
            trace_eval_issue.cluster_id, [str(trace_eval_issue.project_id)]
        )

    assert [t.summary.eval_score for t in overview.representative_traces] == [
        pytest.approx(0.4)
    ]


def test_score_chart_reads_the_same_evals_as_the_score_card(
    trace_eval_issue, no_clickhouse
):
    trends = feed._fetch_score_trends(
        trace_eval_issue, 14, project_id=str(trace_eval_issue.project_id)
    )

    # The 1.0 span eval on the same trace is not this issue's eval.
    assert [t.current for t in trends] == [pytest.approx(0.4)]


@pytest.mark.parametrize("days", [1, 14, 30])
def test_score_chart_has_no_data_when_member_has_only_old_evals(
    trace_eval_issue, no_clickhouse, days
):
    EvalLogger.objects.filter(
        target_type=EvalTargetType.TRACE,
        custom_eval_config=trace_eval_issue.eval_config,
    ).update(created_at=timezone.now() - timedelta(days=60))

    trends = feed._fetch_score_trends(
        trace_eval_issue, days, project_id=str(trace_eval_issue.project_id)
    )

    # Joining recently must not bring historical scores into the requested window.
    assert trends == []


@pytest.mark.parametrize("days", [3, 14])
def test_score_chart_excludes_old_results_without_changing_eval_scope(
    trace_eval_issue, observation_span, no_clickhouse, days
):
    now = timezone.now()
    cfg = trace_eval_issue.eval_config
    EvalLogger.objects.filter(
        target_type=EvalTargetType.TRACE, custom_eval_config=cfg
    ).update(created_at=now - timedelta(days=2))
    historical = _eval(cfg, observation_span, 0.1)
    EvalLogger.objects.filter(pk=historical.pk).update(
        created_at=now - timedelta(days=60)
    )
    other_cfg = CustomEvalConfig.objects.create(
        name="Unrelated eval",
        project=trace_eval_issue.project,
        eval_template=cfg.eval_template,
        config=cfg.config,
        mapping=cfg.mapping,
        filters={},
    )
    unrelated = _eval(other_cfg, observation_span, 0.9)
    EvalLogger.objects.filter(pk=unrelated.pk).update(
        created_at=now - timedelta(days=1)
    )

    with patch.object(feed.timezone, "now", return_value=now):
        trends = feed._fetch_score_trends(
            trace_eval_issue, days, project_id=str(trace_eval_issue.project_id)
        )

    assert [trend.label for trend in trends] == [cfg.name]
    assert trends[0].sparkline == [pytest.approx(0.4)]
    assert trends[0].current == pytest.approx(0.4)
    # Preserve the existing fallback when one half has no samples.
    assert trends[0].prev == pytest.approx(0.4)


def test_score_chart_includes_cutoff_and_splits_at_midpoint(
    trace_eval_issue, observation_span, no_clickhouse
):
    now = timezone.now()
    cfg = trace_eval_issue.eval_config
    EvalLogger.objects.filter(
        target_type=EvalTargetType.TRACE, custom_eval_config=cfg
    ).update(created_at=now - timedelta(days=2))
    for created_at, score in [
        (now - timedelta(days=14, microseconds=1), 0.1),
        (now - timedelta(days=14), 0.2),
        (now - timedelta(days=7), 0.6),
    ]:
        result = _eval(cfg, observation_span, score)
        EvalLogger.objects.filter(pk=result.pk).update(created_at=created_at)

    with patch.object(feed.timezone, "now", return_value=now):
        trends = feed._fetch_score_trends(
            trace_eval_issue, 14, project_id=str(trace_eval_issue.project_id)
        )

    assert len(trends) == 1
    assert trends[0].sparkline == pytest.approx([0.2, 0.6, 0.4])
    assert trends[0].prev == pytest.approx(0.2)
    assert trends[0].current == pytest.approx(0.5)


def test_score_chart_excludes_deleted_results_inside_window(
    trace_eval_issue, observation_span, no_clickhouse
):
    now = timezone.now()
    cfg = trace_eval_issue.eval_config
    EvalLogger.objects.filter(
        target_type=EvalTargetType.TRACE, custom_eval_config=cfg
    ).update(created_at=now - timedelta(days=2))
    deleted_result = _eval(cfg, observation_span, 0.1)
    EvalLogger.objects.filter(pk=deleted_result.pk).update(
        created_at=now - timedelta(days=1), deleted=True
    )

    with patch.object(feed.timezone, "now", return_value=now):
        trends = feed._fetch_score_trends(
            trace_eval_issue, 14, project_id=str(trace_eval_issue.project_id)
        )

    assert len(trends) == 1
    assert trends[0].sparkline == [pytest.approx(0.4)]
    assert trends[0].prev == pytest.approx(0.4)
    assert trends[0].current == pytest.approx(0.4)


@pytest.fixture
def choice_eval_config(project, organization, workspace):
    """A choices evaluator whose labels map to scores."""
    template = EvalTemplate.objects.create(
        name="Choice Template",
        description="",
        organization=organization,
        workspace=workspace,
        config={"output": "choices"},
        choice_scores={"Pass": 1, "Partial": 0.5, "Fail": 0},
    )
    return CustomEvalConfig.objects.create(
        name="Choice eval",
        project=project,
        eval_template=template,
        config={},
        mapping={},
        filters={},
    )


def _issue_scored_by_text(cfg, span, output_str, **fields):
    """An eval issue whose one member's eval result is stored only as text."""
    cluster = TraceErrorGroup.objects.create(
        project=cfg.project,
        cluster_id="E-CHOICE-1",
        error_type="eval",
        issue_group=cfg.name,
        source=ClusterSource.EVAL,
        eval_target_type=EvalTargetType.TRACE,
        eval_config=cfg,
    )
    member = _eval(cfg, span, None, output_str=output_str, **fields)
    ErrorClusterTraces.objects.create(
        cluster=cluster, trace=span.trace, eval_logger=member
    )
    return cluster


@pytest.mark.parametrize(
    "output_str, choice, expected",
    [
        pytest.param('{"score": 0, "choice": "Fail"}', "Fail", 0.0, id="fail-json"),
        pytest.param('{"score": 1, "choice": "Pass"}', "Pass", 1.0, id="pass-json"),
        pytest.param(
            "{'score': 0.5, 'choice': 'Partial'}",
            "Partial",
            0.5,
            id="partial-python-repr",
        ),
    ],
)
def test_traces_tab_reads_the_score_stored_with_a_choice(
    choice_eval_config, observation_span, no_clickhouse, output_str, choice, expected
):
    issue = _issue_scored_by_text(
        choice_eval_config, observation_span, output_str, output_str_list=[choice]
    )

    tab = feed.get_traces_tab(issue.cluster_id, [str(issue.project_id)])

    assert tab.aggregates.avg_score == pytest.approx(expected)
    assert [row.score for row in tab.traces] == [pytest.approx(expected)]


@pytest.mark.parametrize(
    "output_str",
    [
        pytest.param("Fail", id="plain-label"),
        pytest.param("the score: 5 was low", id="free-text"),
        pytest.param("judge said {'score': 5}", id="dict-after-text"),
    ],
)
def test_text_without_a_leading_score_dict_reads_as_no_data(
    custom_eval_config, observation_span, no_clickhouse, output_str
):
    # No choice map on this evaluator: a bare label has no score to read.
    issue = _issue_scored_by_text(custom_eval_config, observation_span, output_str)

    tab = feed.get_traces_tab(issue.cluster_id, [str(issue.project_id)])

    assert tab.aggregates.avg_score is None
    assert [row.score for row in tab.traces] == [None]


def test_judge_ranks_a_choice_eval_by_its_score(
    choice_eval_config, custom_eval_config, observation_span
):
    _eval(custom_eval_config, observation_span, 0.4, eval_explanation="scored 0.4")
    _eval(
        choice_eval_config,
        observation_span,
        None,
        output_str='{"score": 0, "choice": "Fail"}',
        output_str_list=["Fail"],
        eval_explanation="chose Fail",
    )
    trace_id = str(observation_span.trace.id)

    judges = feed._trace_judges_batch([trace_id])

    # The lowest score explains the failure, so the 0 choice outranks 0.4.
    reason, score = judges[trace_id]
    assert reason == "chose Fail"
    assert score == pytest.approx(0.0)


def test_sidebar_shows_the_choice_of_a_choice_eval(
    choice_eval_config, observation_span
):
    _eval(
        choice_eval_config,
        observation_span,
        None,
        output_str='{"score": 0, "choice": "Fail"}',
        output_str_list=["Fail"],
    )

    [evaluation] = feed._fetch_sidebar_evaluations([str(observation_span.trace.id)])

    assert evaluation.label == "Choice eval"
    assert evaluation.value == "Fail"


@pytest.mark.parametrize(
    "output_str",
    [
        pytest.param("Fail", id="plain-label"),
        pytest.param("the score: 5 was low", id="free-text"),
        pytest.param('{"reason": "too short"}', id="dict-without-choice"),
    ],
)
def test_sidebar_keeps_text_that_names_no_choice(
    custom_eval_config, observation_span, output_str
):
    _eval(custom_eval_config, observation_span, None, output_str=output_str)

    [evaluation] = feed._fetch_sidebar_evaluations([str(observation_span.trace.id)])

    assert evaluation.value == output_str
