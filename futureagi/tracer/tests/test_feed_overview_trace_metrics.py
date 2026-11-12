"""Overview reports what was recorded: trace cost and affected users per day.

The Overview list priced every trace at a fixed per-token rate because its
payload carried no cost, and its chart hard-coded zero users for every day.
"""

from contextlib import nullcontext
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)
from tracer.queries import feed
from tracer.serializers.feed import OverviewResponseSerializer

pytestmark = pytest.mark.django_db


@pytest.fixture
def eval_issue(project, custom_eval_config):
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="E-OVERVIEW-METRICS-1",
        error_type="eval",
        issue_group=custom_eval_config.name,
        source=ClusterSource.EVAL,
        eval_target_type=EvalTargetType.TRACE,
        eval_config=custom_eval_config,
    )
    trace_id = str(uuid4())
    failing = EvalLogger.objects.create(
        trace_id=trace_id,
        observation_span_id=f"span_{uuid4().hex[:16]}",
        custom_eval_config=custom_eval_config,
        target_type=EvalTargetType.TRACE,
        output_float=0.4,
        eval_explanation="the agent repeated the question",
        eval_task_id=f"et-{uuid4()}",
    )
    ErrorClusterTraces.objects.create(
        cluster=cluster, trace_id=trace_id, eval_logger=failing
    )
    return cluster, trace_id


def _overview(cluster, trace_id, *, totals, costs):
    trace = feed._CHTraceShim(trace_id, None)
    with (
        patch.object(feed, "_fetch_events_over_time", return_value=[]),
        patch.object(
            feed, "_fetch_pattern_summary", return_value=feed.PatternSummary()
        ),
        patch.object(feed, "_fetch_success_trace_pass_reel", return_value=[]),
        patch.object(feed, "_cluster_highlight_terms", return_value=[]),
        patch.object(feed, "_resolve_member_traces", return_value={trace_id: trace}),
        patch.object(feed, "_get_root_spans_batch", return_value={}),
        patch.object(feed, "_get_trace_totals_batch", return_value=totals),
        patch.object(feed, "_get_investigation_reports_batch", return_value={}),
        patch.object(feed, "_get_trace_costs_batch", side_effect=costs) as cost_read,
    ):
        overview = feed.get_overview(cluster.cluster_id, [str(cluster.project_id)])
    summary = OverviewResponseSerializer(overview).data["representative_traces"][0][
        "summary"
    ]
    return summary, cost_read


def test_overview_trace_carries_its_recorded_cost(eval_issue):
    cluster, trace_id = eval_issue

    summary, cost_read = _overview(
        cluster,
        trace_id,
        totals={trace_id: (150, 1000, 500)},
        costs=lambda ids, _project: dict.fromkeys(ids, 0.12),
    )

    # The Traces tab's recorded cost, not 1000/500 tokens at $3/$15 per million.
    assert summary.get("cost") == pytest.approx(0.12)
    assert summary["input_tokens"] == 1000
    cost_read.assert_called_once_with([trace_id], str(cluster.project_id))


def test_overview_trace_without_spans_has_unknown_cost(eval_issue):
    cluster, trace_id = eval_issue

    summary, cost_read = _overview(
        cluster, trace_id, totals={}, costs=lambda ids, _project: {}
    )

    assert summary["cost"] is None
    cost_read.assert_called_once_with([], str(cluster.project_id))


@pytest.fixture
def scanner_issue(project):
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="S-OVERVIEW-USERS-1",
        error_type="scanner",
        issue_group="Tool Failures",
        source=ClusterSource.SCANNER,
    )
    trace_ids = [str(uuid4()), str(uuid4())]
    for trace_id in trace_ids:
        ErrorClusterTraces.objects.create(cluster=cluster, trace_id=trace_id)
    return cluster, trace_ids


@pytest.fixture
def end_users():
    reader = MagicMock()
    with patch.object(feed, "get_reader", return_value=nullcontext(reader)):
        yield reader


def test_overview_chart_counts_distinct_users_per_day(scanner_issue, end_users):
    cluster, (first, second) = scanner_issue
    project_id = str(cluster.project_id)
    end_users.distinct_end_users_by_trace_ids.return_value = {
        first: ["user-a", "user-b"],
        second: ["user-b"],
    }

    points = feed._fetch_events_over_time(cluster.cluster_id, project_id=project_id)

    assert [(p.errors, p.users) for p in points] == [(2, 2)]
    ids, projects = end_users.distinct_end_users_by_trace_ids.call_args.args
    assert sorted(ids) == sorted([first, second])
    assert projects == [project_id]


def test_trends_chart_keeps_the_same_daily_users(scanner_issue, end_users):
    cluster, (first, second) = scanner_issue
    end_users.distinct_end_users_by_trace_ids.return_value = {
        first: ["user-a", "user-b"],
        second: ["user-b"],
    }

    points = feed._fetch_events_over_time_with_passing(
        cluster.cluster_id, str(cluster.project_id), 14
    )

    assert [(p.errors, p.users) for p in points] == [(2, 2)]
