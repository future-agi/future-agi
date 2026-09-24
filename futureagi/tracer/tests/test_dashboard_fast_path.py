from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings

from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.views import dashboard as dashboard_view
from tracer.views.dashboard import (
    _DASHBOARD_INTERACTIVE_TIMEOUT_MS,
    _DASHBOARD_ROLLUP_READ_SETTINGS,
    _read_dashboard_rollup_fast_path,
    _read_public_dashboard_query,
)


class _RollupAnalytics:
    def __init__(self, *, malformed=False):
        self.calls = []
        self.malformed = malformed

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, dict(params), timeout_ms, dict(settings)))
        aliases = [
            part.split()[0].rstrip(",")
            for part in query.split(" AS ")[2:]
            if part.startswith("metric_")
        ]
        if self.malformed:
            return SimpleNamespace(
                data=[{"time_bucket": datetime.now(UTC)}], columns=[]
            )
        row = {"time_bucket": params["start_date"]}
        row.update({alias: index + 1 for index, alias in enumerate(aliases)})
        return SimpleNamespace(data=[row], columns=["time_bucket", *aliases])


def _query_config(*, preset="30D", granularity="day", metrics=None, **overrides):
    config = {
        "project_ids": ["00000000-0000-0000-0000-000000000001"],
        "time_range": {"preset": preset},
        "granularity": granularity,
        "metrics": metrics
        or [
            {
                "id": "tokens",
                "name": "tokens",
                "type": "system_metric",
                "source": "traces",
                "aggregation": "sum",
                "filters": [],
            }
        ],
        "filters": [],
        "breakdowns": [],
    }
    config.update(overrides)
    return config


@pytest.mark.unit
@pytest.mark.parametrize(
    ("preset", "granularity"),
    [
        ("today", "hour"),
        ("7D", "day"),
        ("30D", "day"),
        ("3M", "day"),
        ("6M", "week"),
        ("12M", "month"),
    ],
)
def test_w1_w6_simple_metrics_use_bounded_rollup(monkeypatch, preset, granularity):
    analytics = _RollupAnalytics()
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: analytics)

    result = _read_dashboard_rollup_fast_path(
        _query_config(preset=preset, granularity=granularity)
    )

    assert result["query_status"] == "complete"
    assert result["query_exact"] is False
    assert result["query_provenance"] == "materialized_rollup"
    assert len(analytics.calls) == 1
    query, params, timeout_ms, settings = analytics.calls[0]
    assert "FROM spans_hourly_rollup" in query
    assert "FROM spans\n" not in query
    assert params["start_date"] < params["end_date"]
    assert 0 < timeout_ms <= _DASHBOARD_INTERACTIVE_TIMEOUT_MS
    assert settings == _DASHBOARD_ROLLUP_READ_SETTINGS
    assert "max_rows_to_read" not in settings
    assert (
        settings["max_memory_usage"]
        == django_settings.DASHBOARD_TRACE_READ_MAX_MEMORY_BYTES
    )
    assert (
        settings["max_bytes_to_read"] == django_settings.DASHBOARD_TRACE_READ_MAX_BYTES
    )


@pytest.mark.unit
def test_span_and_trace_rollups_share_one_deadline(monkeypatch):
    analytics = _RollupAnalytics()
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: analytics)

    class _Deadline:
        def __init__(self):
            self.values = iter([9_000, 8_000, 7_000, 6_000])

        def remaining_ms(self, _cap=None, *, floor_ms=25):
            assert floor_ms > 0
            return next(self.values)

    deadline = _Deadline()
    monkeypatch.setattr(
        dashboard_view.ReadDeadline,
        "start",
        staticmethod(lambda total_ms: deadline),
    )
    metrics = [
        {
            "id": "tokens",
            "name": "tokens",
            "type": "system_metric",
            "source": "traces",
            "aggregation": "sum",
            "filters": [],
        },
        {
            "id": "trace_count",
            "name": "trace_count",
            "type": "system_metric",
            "source": "traces",
            "aggregation": "count_distinct",
            "filters": [],
        },
    ]

    result = _read_dashboard_rollup_fast_path(_query_config(metrics=metrics))

    assert result["query_status"] == "complete"
    assert len(analytics.calls) == 2
    assert [call[2] for call in analytics.calls] == [8_000, 7_000]
    assert {"spans_hourly_rollup", "trace_count_rollup"} == {
        "spans_hourly_rollup"
        if "spans_hourly_rollup" in call[0]
        else "trace_count_rollup"
        for call in analytics.calls
    }


@pytest.mark.unit
def test_rollup_failure_never_returns_plausible_zero(monkeypatch):
    analytics = _RollupAnalytics(malformed=True)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: analytics)

    result = _read_dashboard_rollup_fast_path(_query_config())

    assert result["query_status"] == "degraded"
    assert result["query_error_code"] == "malformed_result"
    assert result["metrics"][0]["series"] == []


@pytest.mark.unit
def test_rollup_timeout_is_typed_degraded(monkeypatch):
    analytics = _RollupAnalytics()
    analytics.execute_ch_query = lambda *args, **kwargs: (_ for _ in ()).throw(
        ReadDeadlineExceeded("private timeout")
    )
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: analytics)

    result = _read_dashboard_rollup_fast_path(_query_config())

    assert result["query_status"] == "degraded"
    assert result["query_error_code"] == "read_budget_exceeded"


@pytest.mark.unit
def test_rollup_fails_closed_when_query_settings_are_locked(monkeypatch):
    analytics = _RollupAnalytics()
    analytics.supports_per_query_read_settings = False
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: analytics)

    result = _read_dashboard_rollup_fast_path(_query_config())

    assert analytics.calls == []
    assert result["query_status"] == "degraded"
    assert result["query_error_code"] == "read_settings_unavailable"
    assert result["metrics"][0]["series"] == []


@pytest.mark.unit
def test_filtered_cold_miss_keeps_exact_refresh_pending(monkeypatch):
    monkeypatch.setattr(
        dashboard_view,
        "read_or_schedule_exact_snapshot",
        lambda *args, **kwargs: kwargs["pending_payload"],
    )
    monkeypatch.setattr(
        dashboard_view,
        "V2AnalyticsQueryService",
        lambda: pytest.fail("unsupported cold shape must not scan raw spans"),
    )

    result = _read_public_dashboard_query(
        _query_config(filters=[{"metric_name": "status"}]),
        cache_identity={"workspace_id": "workspace", "query_config": {}},
        refresh=False,
    )

    assert result["query_status"] == "pending"
    assert result["query_refreshing"] is True
    assert result["metrics"] == []


@pytest.mark.unit
def test_exhausted_direct_read_dispatches_one_background_refresh(monkeypatch):
    class _ExpiredDeadline:
        def remaining_ms(self, *_args, **_kwargs):
            raise ReadDeadlineExceeded("deadline")

    calls = []

    def _schedule(*args, **kwargs):
        calls.append((args, kwargs))
        return kwargs["pending_payload"]

    monkeypatch.setattr(
        dashboard_view,
        "read_or_schedule_exact_snapshot",
        _schedule,
    )

    result = _read_public_dashboard_query(
        _query_config(filters=[{"metric_name": "status"}]),
        cache_identity={"workspace_id": "workspace", "query_config": {}},
        refresh=False,
        deadline=_ExpiredDeadline(),
        try_rollup=False,
    )

    assert result["query_status"] == "pending"
    assert len(calls) == 1
    assert calls[0][0][0] == "dashboard-query"
    assert calls[0][1]["refresh"] is False
