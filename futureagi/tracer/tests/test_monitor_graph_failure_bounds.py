from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.db import DatabaseError

from tfc.utils.general_methods import GeneralMethods
from tracer.models.monitor import MonitorMetricTypeChoices
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.utils import monitor_graphs
from tracer.views import monitor as monitor_view


class ShrinkingDeadline:
    def __init__(self, remaining_ms=8_000):
        self.remaining = remaining_ms
        self.calls = []

    def remaining_ms(self, cap_ms=None, *, floor_ms=25):
        self.remaining -= 100
        if self.remaining < floor_ms:
            raise ReadDeadlineExceeded("deadline exhausted")
        value = self.remaining if cap_ms is None else min(cap_ms, self.remaining)
        self.calls.append((cap_ms, floor_ms, value))
        return value


def _monitor(**overrides):
    values = {
        "id": "monitor-1",
        "project_id": "project-1",
        "project": SimpleNamespace(id="project-1"),
        "metric_type": MonitorMetricTypeChoices.COUNT_OF_ERRORS,
        "metric": None,
        "filters": {},
        "threshold_metric_value": None,
        "alert_frequency": 1,
        "auto_threshold_time_window": 60,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _builder():
    builder = Mock()
    builder.build_time_series_query.return_value = ("SELECT bounded", {})
    return builder


def _unwrap(function):
    while hasattr(function, "__wrapped__"):
        function = function.__wrapped__
    return function


def test_static_graph_uses_the_remaining_capped_clickhouse_budget():
    deadline = ShrinkingDeadline()
    analytics = Mock()
    analytics.execute_ch_query.return_value = SimpleNamespace(data=[])

    with (
        patch.object(monitor_graphs, "AnalyticsQueryService", return_value=analytics),
        patch.object(
            monitor_graphs,
            "_build_monitor_graph_ch_builder",
            return_value=_builder(),
        ),
    ):
        assert (
            monitor_graphs.get_static_metric_graph_data(_monitor(), deadline=deadline)
            == []
        )

    timeout_ms = analytics.execute_ch_query.call_args.kwargs["timeout_ms"]
    assert 0 < timeout_ms <= monitor_graphs.MONITOR_GRAPH_CH_TIMEOUT_CAP_MS


@pytest.mark.parametrize(
    "reader",
    [
        monitor_graphs.get_static_metric_graph_data,
        monitor_graphs.get_percentage_change_metric_graph_data,
    ],
)
def test_clickhouse_timeout_is_typed_unavailable_without_postgres_fallback(reader):
    analytics = Mock()
    analytics.execute_ch_query.side_effect = ReadDeadlineExceeded("CH timed out")

    with (
        patch.object(monitor_graphs, "AnalyticsQueryService", return_value=analytics),
        patch.object(
            monitor_graphs,
            "_build_monitor_graph_ch_builder",
            return_value=_builder(),
        ),
    ):
        with pytest.raises(monitor_graphs.MonitorGraphUnavailable):
            reader(_monitor(), deadline=ShrinkingDeadline())


def test_non_timeout_clickhouse_failure_fails_loud_without_stale_pg_fallback():
    analytics = Mock()
    analytics.execute_ch_query.side_effect = RuntimeError("temporary CH failure")

    with (
        patch.object(monitor_graphs, "AnalyticsQueryService", return_value=analytics),
        patch.object(
            monitor_graphs,
            "_build_monitor_graph_ch_builder",
            return_value=_builder(),
        ),
    ):
        with pytest.raises(RuntimeError, match="temporary CH failure"):
            monitor_graphs.get_static_metric_graph_data(
                _monitor(), deadline=ShrinkingDeadline()
            )


def test_expired_wall_after_clickhouse_failure_is_typed_unavailable():
    analytics = Mock()
    analytics.execute_ch_query.side_effect = RuntimeError("CH disconnected")

    with (
        patch.object(monitor_graphs, "AnalyticsQueryService", return_value=analytics),
        patch.object(
            monitor_graphs,
            "_build_monitor_graph_ch_builder",
            return_value=_builder(),
        ),
    ):
        with pytest.raises(monitor_graphs.MonitorGraphUnavailable):
            monitor_graphs.get_static_metric_graph_data(
                _monitor(), deadline=ShrinkingDeadline(remaining_ms=150)
            )


def test_postgres_failures_are_unavailable_instead_of_success_empty():
    fake_connection = SimpleNamespace(vendor="sqlite")

    with patch.object(monitor_graphs, "connection", fake_connection):
        with pytest.raises(monitor_graphs.MonitorGraphUnavailable):
            with monitor_graphs.monitor_graph_postgres_budget(ShrinkingDeadline()):
                raise DatabaseError("statement timeout")


def test_postgres_statement_timeout_shrinks_between_queries():
    deadline = ShrinkingDeadline(remaining_ms=2_600)
    raw_cursor = Mock()
    context = {"cursor": SimpleNamespace(cursor=raw_cursor)}
    execute = Mock(return_value="row")

    for _ in range(2):
        assert (
            monitor_graphs._execute_monitor_graph_pg_query_with_deadline(
                deadline,
                2_500,
                execute,
                "SELECT 1",
                (),
                False,
                context,
            )
            == "row"
        )

    first_timeout = int(raw_cursor.execute.call_args_list[0].args[1][0])
    second_timeout = int(raw_cursor.execute.call_args_list[1].args[1][0])
    assert 0 < second_timeout < first_timeout <= 2_500


def test_saved_graph_action_returns_typed_503_with_the_request_deadline():
    deadline = object()
    graph_reader = Mock(
        side_effect=monitor_graphs.MonitorGraphUnavailable("read timed out")
    )
    view = SimpleNamespace(
        _gm=GeneralMethods(),
        get_object=Mock(return_value=_monitor()),
    )
    request = SimpleNamespace(query_params={})

    with (
        patch.object(
            monitor_view, "start_monitor_graph_deadline", return_value=deadline
        ),
        patch.object(
            monitor_view, "monitor_graph_postgres_budget", return_value=nullcontext()
        ),
        patch.object(monitor_view, "get_graph_data", graph_reader),
    ):
        response = _unwrap(monitor_view.UserAlertMonitorView.graph_data)(view, request)

    assert response.status_code == 503
    assert response.data["code"] == "monitor_graph_unavailable"
    assert graph_reader.call_args.kwargs["deadline"] is deadline


def test_preview_graph_action_returns_the_same_typed_503():
    deadline = object()
    graph_reader = Mock(
        side_effect=monitor_graphs.MonitorGraphUnavailable("read timed out")
    )
    serializer = SimpleNamespace(
        fields={"name": SimpleNamespace(required=True)},
        validated_data={"metric_type": MonitorMetricTypeChoices.COUNT_OF_ERRORS},
        errors={},
        is_valid=Mock(return_value=True),
    )
    view = SimpleNamespace(
        _gm=GeneralMethods(),
        get_serializer=Mock(return_value=serializer),
    )
    request = SimpleNamespace(
        data={},
        query_params={},
        organization=SimpleNamespace(id="org-1"),
        workspace=None,
        user=SimpleNamespace(organization=SimpleNamespace(id="org-1")),
    )

    class FakeMonitor:
        DoesNotExist = monitor_view.UserAlertMonitor.DoesNotExist

        def __init__(self, **values):
            self.__dict__.update(values)

    with (
        patch.object(
            monitor_view, "start_monitor_graph_deadline", return_value=deadline
        ),
        patch.object(
            monitor_view, "monitor_graph_postgres_budget", return_value=nullcontext()
        ),
        patch.object(monitor_view, "UserAlertMonitor", FakeMonitor),
        patch.object(monitor_view, "get_graph_data", graph_reader),
    ):
        response = _unwrap(monitor_view.UserAlertMonitorView.preview_graph)(
            view, request
        )

    assert response.status_code == 503
    assert response.data["code"] == "monitor_graph_unavailable"
    assert graph_reader.call_args.kwargs["deadline"] is deadline
