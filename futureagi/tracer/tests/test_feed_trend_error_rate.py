"""W10-ERROR-FEED-L05: eval error rates count affected traces, not eval rows."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest

from tracer.models.observation_span import EvalTargetType
from tracer.models.trace_error_analysis import ClusterSource
from tracer.queries import feed


@pytest.fixture
def trend_inputs(monkeypatch):
    """Exercise the KPI builder and denominator queries without PG or CH."""
    cluster = SimpleNamespace(
        source=ClusterSource.EVAL,
        eval_target_type=EvalTargetType.SPAN,
        eval_config_id=None,
    )
    clusters = Mock()
    clusters.filter.return_value.first.return_value = cluster
    members = Mock()
    omega = Mock(return_value=False)
    reader_context = MagicMock()
    reader = reader_context.__enter__.return_value
    reports = Mock()
    reports.filter.return_value = reports
    report_model = Mock()
    report_model.objects.filter.return_value = reports
    now = datetime(2026, 1, 29, tzinfo=UTC)

    monkeypatch.setattr(feed, "_cluster_qs_for_access", Mock(return_value=clusters))
    monkeypatch.setattr(feed, "_is_omega_group", omega)
    monkeypatch.setattr(feed, "_member_ids_in_window", members)
    monkeypatch.setattr(feed, "_session_rep_trace_map", Mock(return_value={}))
    monkeypatch.setattr(feed, "get_reader", Mock(return_value=reader_context))
    monkeypatch.setattr(feed, "TraceInvestigationReport", report_model)
    monkeypatch.setattr(feed.timezone, "now", Mock(return_value=now))
    monkeypatch.setattr(feed, "_avg_eval_score", Mock(return_value=0.4))
    monkeypatch.setattr(feed, "_users_affected_in_window", Mock(return_value=7))
    return SimpleNamespace(
        cluster=cluster,
        members=members,
        omega=omega,
        reader=reader,
        reports=reports,
        now=now,
    )


@pytest.mark.parametrize("target_type", [EvalTargetType.SPAN, EvalTargetType.TRACE])
@pytest.mark.parametrize(
    "current,previous,expected_value,expected_delta",
    [
        pytest.param((201, 4, 201), (101, 3, 202), "100%", 50.0, id="all-affected"),
        pytest.param(
            (101, 4, 404), (101, 6, 202), "25%", -25.0, id="partial-not-clamped"
        ),
        pytest.param((0, 1, 0), (101, 3, 202), "0%", -50.0, id="empty-current"),
        pytest.param((101, 4, 404), (0, 1, 0), "25%", 25.0, id="empty-previous"),
        pytest.param((0, 1, 0), (0, 1, 0), "0%", 0.0, id="both-empty"),
    ],
)
def test_eval_error_rate_counts_distinct_traces_in_each_window(
    trend_inputs, target_type, current, previous, expected_value, expected_delta
):
    trend_inputs.cluster.eval_target_type = target_type
    # The same trace can occur in both windows. Deduplication is window-local.
    cur_members = [f"trace-{i}" for i in range(current[0])] * current[1]
    prev_members = [f"trace-{i}" for i in range(previous[0])] * previous[1]
    trend_inputs.members.side_effect = [(cur_members, []), (prev_members, [])]
    trend_inputs.reader.count_with_filters.side_effect = [current[2], previous[2]]

    metrics = feed._fetch_trend_metrics("eval-issue", "project-1", days=14)

    error_rate, score, users = metrics
    assert error_rate.label == "Error rate"
    assert error_rate.value == expected_value
    assert error_rate.delta == expected_delta
    assert error_rate.unit == "%"
    assert score.value == "0.40"
    assert users.value == "7"
    # Only the rate counts distinct traces; raw memberships remain intact.
    assert len(cur_members) == current[0] * current[1]
    assert len(prev_members) == previous[0] * previous[1]
    cur_start = trend_inputs.now - timedelta(days=14)
    assert trend_inputs.reader.count_with_filters.call_args_list == [
        call(project_id="project-1", created_at_gte=cur_start, roots_only=True),
        call(
            project_id="project-1",
            created_at_half_open_range=(cur_start - timedelta(days=14), cur_start),
            roots_only=True,
        ),
    ]


@pytest.mark.parametrize(
    "omega,expected_value,expected_delta",
    [
        pytest.param(False, "40%", 10.0, id="legacy-scanner"),
        pytest.param(True, "10%", 0.0, id="omega"),
    ],
)
def test_non_eval_error_rate_keeps_existing_units(
    trend_inputs, omega, expected_value, expected_delta
):
    trend_inputs.cluster.source = ClusterSource.SCANNER
    trend_inputs.omega.return_value = omega
    trend_inputs.members.side_effect = [(["trace-1"] * 4, []), (["trace-1"] * 3, [])]
    trend_inputs.reports.count.side_effect = [10, 10]

    metrics = feed._fetch_trend_metrics("scanner-issue", "project-1", days=14)

    assert metrics[0].value == expected_value
    assert metrics[0].delta == expected_delta
    trend_inputs.reader.count_with_filters.assert_not_called()
