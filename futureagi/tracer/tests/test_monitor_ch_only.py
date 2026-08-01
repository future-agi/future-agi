from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tracer.models.monitor import (
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
)
from tracer.utils import monitor as monitor_utils
from tracer.utils import monitor_graphs

pytestmark = pytest.mark.unit


def _monitor(metric_type=MonitorMetricTypeChoices.COUNT_OF_ERRORS):
    return SimpleNamespace(
        id="monitor-id",
        project_id="11111111-1111-1111-1111-111111111111",
        metric_type=metric_type,
        metric=None,
        filters={},
        threshold_metric_value=None,
        alert_frequency=5,
        auto_threshold_time_window=15,
        threshold_operator=ComparisonOperatorChoices.GREATER_THAN,
        warning_threshold_value=10,
        critical_threshold_value=20,
    )


class _Builder:
    def build_metric_value_query(self, metric_type, start_time, end_time):
        return "SELECT 1 AS value", {}

    def build_historical_stats_query(self, metric_type, start_time, end_time):
        return "SELECT 1 AS mean, 0 AS stddev", {}

    def build_time_series_query(
        self, metric_type, start_time, end_time, frequency_seconds
    ):
        return "SELECT now() AS timestamp, 1 AS value", {}


class _Analytics:
    def __init__(self, data=None, error=None):
        self.data = data or []
        self.error = error
        self.calls = []

    def execute_ch_query(self, query, params, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data)


def _assert_bounded(call):
    assert call["timeout_ms"] == 750
    settings = call["settings"]
    assert settings["max_threads"] == 2
    assert settings["max_memory_usage"] == 268_435_456
    assert settings["max_bytes_to_read"] == 1_073_741_824
    assert settings["max_result_rows"] == 2000
    assert settings["timeout_overflow_mode"] == "throw"
    assert settings["read_overflow_mode"] == "throw"
    assert settings["result_overflow_mode"] == "throw"


def _install_fakes(monkeypatch, module, analytics):
    monkeypatch.setattr(module, "AnalyticsQueryService", lambda: analytics)
    builder_name = (
        "_build_monitor_ch_builder"
        if module is monitor_utils
        else "_build_monitor_graph_ch_builder"
    )
    monkeypatch.setattr(module, builder_name, lambda monitor: _Builder())

    class _NoPostgresTelemetry:
        class objects:
            @staticmethod
            def filter(*args, **kwargs):
                raise AssertionError("PostgreSQL telemetry fallback was used")

    monkeypatch.setattr(module, "ObservationSpan", _NoPostgresTelemetry, raising=False)
    monkeypatch.setattr(module, "EvalLogger", _NoPostgresTelemetry, raising=False)


def test_monitor_metric_value_is_bounded_and_has_no_pg_fallback(monkeypatch):
    analytics = _Analytics(error=TimeoutError("read budget exhausted"))
    _install_fakes(monkeypatch, monitor_utils, analytics)

    value = monitor_utils._get_metric_value(
        _monitor(),
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert value is None
    _assert_bounded(analytics.calls[0])


def test_monitor_historical_aggregate_uses_bounded_ch_series(monkeypatch):
    analytics = _Analytics(
        data=[
            {"timestamp": "2026-07-01T00:00:00Z", "value": 2},
            {"timestamp": "2026-07-01T00:05:00Z", "value": 4},
        ]
    )
    _install_fakes(monkeypatch, monitor_utils, analytics)

    stats = monitor_utils._get_historical_stats(
        _monitor(),
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert stats == (3, pytest.approx(2**0.5))
    _assert_bounded(analytics.calls[0])


def test_monitor_historical_failure_has_no_pg_fallback(monkeypatch):
    analytics = _Analytics(error=TimeoutError("read budget exhausted"))
    _install_fakes(monkeypatch, monitor_utils, analytics)

    stats = monitor_utils._get_historical_stats(
        _monitor(MonitorMetricTypeChoices.SPAN_RESPONSE_TIME),
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert stats == (None, None)
    _assert_bounded(analytics.calls[0])


def test_static_monitor_graph_is_bounded_and_reports_read_budget_failure(monkeypatch):
    analytics = _Analytics(error=TimeoutError("read budget exhausted"))
    _install_fakes(monkeypatch, monitor_graphs, analytics)

    data = monitor_graphs.get_static_metric_graph_data(
        _monitor(),
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert data == {
        "graph_data": [],
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "read_budget_exceeded",
    }
    _assert_bounded(analytics.calls[0])


def test_percentage_monitor_graph_is_bounded_and_reports_read_budget_failure(
    monkeypatch,
):
    analytics = _Analytics(error=TimeoutError("read budget exhausted"))
    _install_fakes(monkeypatch, monitor_graphs, analytics)

    data = monitor_graphs.get_percentage_change_metric_graph_data(
        _monitor(),
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert data == {
        "graph_data": [],
        "alert_bar_data": [],
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "read_budget_exceeded",
    }
    _assert_bounded(analytics.calls[0])


def test_static_monitor_graph_non_budget_failure_is_safe_and_distinct_from_no_data(
    monkeypatch,
):
    raw_error = "Code: 999. DB::Exception: sensitive internal detail"
    analytics = _Analytics(error=RuntimeError(raw_error))
    _install_fakes(monkeypatch, monitor_graphs, analytics)

    data = monitor_graphs.get_static_metric_graph_data(
        _monitor(),
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert data == {
        "graph_data": [],
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "query_failed",
    }
    assert raw_error not in str(data)


def test_static_monitor_graph_true_no_data_keeps_legacy_empty_list(monkeypatch):
    analytics = _Analytics(data=[])
    _install_fakes(monkeypatch, monitor_graphs, analytics)

    data = monitor_graphs.get_static_metric_graph_data(
        _monitor(),
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert data == []


def test_percentage_monitor_graph_true_no_data_keeps_legacy_shape(monkeypatch):
    analytics = _Analytics(data=[])
    _install_fakes(monkeypatch, monitor_graphs, analytics)

    data = monitor_graphs.get_percentage_change_metric_graph_data(
        _monitor(),
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert data == {"graph_data": [], "alert_bar_data": []}


def test_legacy_monitor_without_project_never_queries_clickhouse(monkeypatch):
    analytics = _Analytics()
    monkeypatch.setattr(monitor_utils, "AnalyticsQueryService", lambda: analytics)
    monitor = _monitor()
    monitor.project_id = None

    value = monitor_utils._get_metric_value(
        monitor,
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert value is None
    assert analytics.calls == []


def test_legacy_monitor_graph_without_project_never_queries_clickhouse(monkeypatch):
    analytics = _Analytics()
    monkeypatch.setattr(monitor_graphs, "AnalyticsQueryService", lambda: analytics)
    monitor = _monitor()
    monitor.project_id = None

    data = monitor_graphs.get_static_metric_graph_data(
        monitor,
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert data == {
        "graph_data": [],
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "query_failed",
    }
    assert analytics.calls == []
