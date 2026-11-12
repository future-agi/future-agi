"""Overview evidence must explain the issue's evaluator, not a lower-scoring peer."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)
from tracer.queries import feed
from tracer.views.feed.linear_issue_view import _build_issue_description

pytestmark = pytest.mark.django_db

OWN_REASON = "Evaluator A: missing required disclosure"
OTHER_REASON = "Evaluator B: invalid JSON response"


def _eval(issue, config, score, reason, *, target_type=None, **kwargs):
    target_type = target_type or issue.cluster.eval_target_type
    anchors = (
        {"trace_session_id": issue.session_id}
        if target_type == EvalTargetType.SESSION
        else {"trace_id": issue.trace_id, "observation_span_id": issue.span_id}
    )
    return EvalLogger.objects.create(
        **anchors,
        custom_eval_config=config,
        target_type=target_type,
        output_float=score,
        eval_explanation=reason,
        eval_task_id=f"et-{uuid4()}",
        **kwargs,
    )


@pytest.fixture(
    params=[EvalTargetType.TRACE, EvalTargetType.SPAN, EvalTargetType.SESSION],
    ids=["trace", "span", "session"],
)
def issue(request, project, custom_eval_config):
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="E-JUDGE-1",
        error_type="eval",
        issue_group=custom_eval_config.name,
        source=ClusterSource.EVAL,
        eval_target_type=request.param,
        eval_config=custom_eval_config,
    )
    other_config = CustomEvalConfig.objects.create(
        project=project,
        name="Evaluator B",
        eval_template=custom_eval_config.eval_template,
        config={"threshold": 0.8},
        mapping={"input": "input", "output": "output"},
        filters={},
    )
    # Eval and membership FKs support CH-only identities, so no PG trace/span
    # or external ClickHouse fixture is needed for these query regressions.
    issue = SimpleNamespace(
        cluster=cluster,
        other_config=other_config,
        trace_id=str(uuid4()),
        span_id=f"span_{uuid4().hex[:16]}",
        session_id=str(uuid4()),
    )
    issue.own_eval = _eval(issue, custom_eval_config, 0.4, OWN_REASON)
    member = (
        {"trace_session_id": issue.session_id}
        if request.param == EvalTargetType.SESSION
        else {"trace_id": issue.trace_id}
    )
    ErrorClusterTraces.objects.create(
        cluster=cluster, eval_logger=issue.own_eval, **member
    )
    return issue


def _overview_trace(issue):
    trace = feed._CHTraceShim(issue.trace_id, None)
    with (
        patch.object(feed, "_fetch_events_over_time", return_value=[]),
        patch.object(
            feed, "_fetch_pattern_summary", return_value=feed.PatternSummary()
        ),
        patch.object(feed, "_fetch_success_trace_pass_reel", return_value=[]),
        patch.object(feed, "_cluster_highlight_terms", return_value=[]),
        patch.object(
            feed,
            "_session_rep_trace_map",
            return_value={issue.session_id: issue.trace_id},
        ),
        patch.object(
            feed, "_resolve_member_traces", return_value={issue.trace_id: trace}
        ),
        patch.object(feed, "_get_root_spans_batch", return_value={}),
        patch.object(feed, "_get_trace_totals_batch", return_value={}),
        patch.object(feed, "_get_investigation_reports_batch", return_value={}),
    ):
        overview = feed.get_overview(
            issue.cluster.cluster_id, [str(issue.cluster.project_id)]
        )
    assert len(overview.representative_traces) == 1
    return overview.representative_traces[0]


def test_overview_reason_and_score_belong_to_the_issue_evaluator(issue):
    _eval(issue, issue.other_config, 0.1, OTHER_REASON)

    trace = _overview_trace(issue)

    assert trace.summary.eval_score == pytest.approx(0.4)
    assert trace.evidence.score == pytest.approx(0.4)
    assert trace.evidence.judge_reason == OWN_REASON


def test_overview_ignores_other_target_types_of_the_same_evaluator(issue):
    other_target = (
        EvalTargetType.TRACE
        if issue.cluster.eval_target_type == EvalTargetType.SPAN
        else EvalTargetType.SPAN
    )
    _eval(
        issue,
        issue.cluster.eval_config,
        0.05,
        "A different evaluation target failed",
        target_type=other_target,
    )

    trace = _overview_trace(issue)

    assert trace.summary.eval_score == pytest.approx(0.4)
    assert trace.evidence.score == pytest.approx(0.4)
    assert trace.evidence.judge_reason == OWN_REASON


@pytest.mark.parametrize("explanation", [None, ""])
def test_overview_does_not_borrow_a_reason_when_the_issue_has_none(issue, explanation):
    EvalLogger.objects.filter(pk=issue.own_eval.pk).update(eval_explanation=explanation)
    _eval(issue, issue.other_config, 0.1, OTHER_REASON)
    if issue.cluster.eval_target_type == EvalTargetType.SESSION:
        # With no session explanation, do not fall back to an unrelated eval
        # on the session's representative trace (either reason or score).
        _eval(
            issue,
            issue.cluster.eval_config,
            0.05,
            "The representative span failed",
            target_type=EvalTargetType.SPAN,
        )

    trace = _overview_trace(issue)

    assert trace.evidence.judge_reason is None
    if issue.cluster.eval_target_type == EvalTargetType.SESSION:
        assert trace.evidence.score is None
    else:
        assert trace.evidence.score == pytest.approx(0.4)


def test_overview_keeps_lowest_live_explained_result_within_the_issue(issue):
    _eval(issue, issue.other_config, 0.1, OTHER_REASON)
    _eval(issue, issue.cluster.eval_config, 0.2, "A's lower-scoring failure")
    _eval(issue, issue.cluster.eval_config, 0.0, "Deleted result", deleted=True)
    _eval(issue, issue.cluster.eval_config, 0.0, "")
    _eval(issue, issue.cluster.eval_config, 0.0, None)
    _eval(issue, issue.cluster.eval_config, None, "Unscored explanation")

    trace = _overview_trace(issue)

    assert trace.evidence.score == pytest.approx(0.2)
    assert trace.evidence.judge_reason == "A's lower-scoring failure"


def test_overview_preserves_boolean_eval_scores(issue):
    EvalLogger.objects.filter(pk=issue.own_eval.pk).update(
        output_float=None, output_bool=False
    )

    trace = _overview_trace(issue)

    assert trace.evidence.score == pytest.approx(0.0)
    assert trace.evidence.judge_reason == OWN_REASON


@pytest.mark.parametrize(
    "issue",
    [EvalTargetType.TRACE, EvalTargetType.SPAN],
    indirect=True,
    ids=["trace", "span"],
)
def test_scanner_overview_keeps_trace_wide_judge_selection(issue):
    issue.cluster.source = ClusterSource.SCANNER
    issue.cluster.eval_config = None
    issue.cluster.eval_target_type = None
    issue.cluster.save(update_fields=["source", "eval_config", "eval_target_type"])
    _eval(
        issue,
        issue.other_config,
        0.1,
        OTHER_REASON,
        target_type=EvalTargetType.TRACE,
    )

    trace = _overview_trace(issue)

    assert trace.evidence.score == pytest.approx(0.1)
    assert trace.evidence.judge_reason == OTHER_REASON


OTHER_TARGET_REASON = "Evaluator A at the trace: the whole reply failed"


@pytest.fixture
def untargeted_issue(project, custom_eval_config):
    """An eval issue from before the target was recorded, failed by a span eval."""
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="E-JUDGE-2",
        error_type="eval",
        issue_group=custom_eval_config.name,
        source=ClusterSource.EVAL,
        eval_target_type=None,
        eval_config=custom_eval_config,
    )
    other_config = CustomEvalConfig.objects.create(
        project=project,
        name="Evaluator B",
        eval_template=custom_eval_config.eval_template,
        config={"threshold": 0.8},
        mapping={"input": "input", "output": "output"},
        filters={},
    )
    issue = SimpleNamespace(
        cluster=cluster,
        other_config=other_config,
        trace_id=str(uuid4()),
        span_id=f"span_{uuid4().hex[:16]}",
        session_id=str(uuid4()),
    )
    issue.own_eval = _eval(
        issue, custom_eval_config, 0.2, OWN_REASON, target_type=EvalTargetType.SPAN
    )
    ErrorClusterTraces.objects.create(
        cluster=cluster, eval_logger=issue.own_eval, trace_id=issue.trace_id
    )
    return issue


def test_overview_scores_and_explains_an_issue_without_a_recorded_target(
    untargeted_issue,
):
    trace = _overview_trace(untargeted_issue)

    assert trace.summary.eval_score == pytest.approx(0.2)
    assert trace.evidence.score == pytest.approx(0.2)
    assert trace.evidence.judge_reason == OWN_REASON


def test_overview_of_an_issue_without_a_recorded_target_quotes_only_its_evaluator(
    untargeted_issue,
):
    _eval(
        untargeted_issue,
        untargeted_issue.other_config,
        0.1,
        OTHER_REASON,
        target_type=EvalTargetType.SPAN,
    )

    trace = _overview_trace(untargeted_issue)

    assert trace.summary.eval_score == pytest.approx(0.2)
    assert trace.evidence.score == pytest.approx(0.2)
    assert trace.evidence.judge_reason == OWN_REASON


def test_ticket_for_an_issue_without_a_recorded_target_quotes_its_evaluator(
    untargeted_issue,
):
    _eval(
        untargeted_issue,
        untargeted_issue.other_config,
        0.1,
        OTHER_REASON,
        target_type=EvalTargetType.SPAN,
    )

    body = _build_issue_description(
        untargeted_issue.cluster, trace_id=untargeted_issue.trace_id
    )

    assert "## Evaluator reasoning — sampled trace (0.20/1.00)" in body
    assert OWN_REASON in body
    assert OTHER_REASON not in body


@pytest.mark.parametrize("issue", [EvalTargetType.SPAN], indirect=True, ids=["span"])
def test_span_issue_reads_only_its_own_target_on_overview_and_ticket(issue):
    _eval(
        issue,
        issue.cluster.eval_config,
        0.05,
        OTHER_TARGET_REASON,
        target_type=EvalTargetType.TRACE,
    )

    trace = _overview_trace(issue)
    body = _build_issue_description(issue.cluster, trace_id=issue.trace_id)

    assert trace.summary.eval_score == pytest.approx(0.4)
    assert trace.evidence.judge_reason == OWN_REASON
    assert "## Evaluator reasoning — sampled trace (0.40/1.00)" in body
    assert OTHER_TARGET_REASON not in body
