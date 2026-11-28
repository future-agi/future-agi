"""Read budgets for the issue header counts and overview comparison cards."""

from unittest.mock import patch
from uuid import uuid4

import pytest

from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)
from tracer.queries import feed


@pytest.fixture
def issue(observe_project):
    return TraceErrorGroup.objects.create(
        project=observe_project,
        cluster_id="eval-example",
        source=ClusterSource.EVAL,
        error_type="eval",
        issue_group="Example eval",
    )


def test_detail_reads_members_once_for_both_counts(issue, django_assert_num_queries):
    trace, session, turn = (str(uuid4()) for _ in range(3))
    ErrorClusterTraces.objects.create(cluster=issue, trace_id=trace)
    ErrorClusterTraces.objects.create(cluster=issue, trace_session_id=session)
    project = str(issue.project_id)
    with (
        patch.object(feed, "get_reader") as get_reader,
        patch.object(feed, "_fetch_trends_batch", return_value={}),
        patch.object(feed, "_fetch_latest_trace_id_batch", return_value={}),
        patch.object(feed, "is_voice_project", return_value=False),
    ):
        reader = get_reader.return_value.__enter__.return_value
        reader.session_trace_ids.return_value = [turn]
        reader.per_trace_root_span_start_times.return_value = {}
        reader.distinct_end_users_by_trace_ids.return_value = {
            trace: {"u1"},
            turn: {"u1", "u2"},
        }
        reader.trace_session_ids_by_trace_ids.return_value = {trace: session}
        # One project-scoped issue read and one shared membership projection.
        with django_assert_num_queries(2):
            detail = feed.get_cluster_detail(issue.cluster_id, [project])

    assert detail is not None
    assert detail.row.users_affected == 2
    assert detail.row.sessions == 1
    reader.trace_session_ids_by_trace_ids.assert_called_once_with([trace], [project])


def test_overview_reads_each_cohort_once_for_both_metrics(issue):
    failing = [str(uuid4()) for _ in range(6)]
    passing = [str(uuid4()) for _ in range(6)]
    for tid in failing:
        ErrorClusterTraces.objects.create(cluster=issue, trace_id=tid)
    project = str(issue.project_id)
    samples = dict.fromkeys(failing, (2000, 80, 20))
    samples.update(dict.fromkeys(passing, (500, 10, 10)))

    def totals(ids, projects):
        assert projects == [project]
        return {tid: samples[tid] for tid in ids}

    with (
        patch.object(feed, "_passing_baseline_trace_ids", return_value=passing),
        patch.object(feed, "_root_input_texts", return_value={}),
        patch.object(feed, "_is_omega_group", wraps=feed._is_omega_group) as group_mode,
        patch.object(feed, "get_reader") as get_reader,
    ):
        reader = get_reader.return_value.__enter__.return_value
        reader.roots_by_trace_ids.return_value = []
        reader.totals_by_trace_ids.side_effect = totals
        overview = feed.get_overview(issue.cluster_id, [project])

    assert overview is not None
    assert overview.representative_total == len(failing)
    assert [(card.title, card.value) for card in overview.pattern_summary.insights] == [
        ("Token usage", "~100"),
        ("Latency shift", "~2.0s"),
    ]
    calls = reader.totals_by_trace_ids.call_args_list
    assert [set(call.args[0]) for call in calls] == [set(failing), set(passing)]
    group_mode.assert_called_once_with(issue.cluster_id, project)
