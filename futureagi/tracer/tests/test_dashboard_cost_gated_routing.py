"""Dashboard trace reads choose their lane before the statement runs.

A widget too heavy for the interactive wall used to prove it by spending the
whole wall on a statement that could not finish, and only then handing the
identical SQL to the exact worker. These tests pin the replacement: the lane
is decided from metadata, and every way the prediction can be unavailable
leaves the request on the path it takes today.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from tracer.services.clickhouse.dashboard_read_density import (
    density_scope_key,
    observe_completed_read,
)
from tracer.services.clickhouse.query_service import QueryResult
from tracer.views.dashboard import (
    _DASHBOARD_TRACE_READ_SETTINGS,
    DashboardWidgetViewSet,
)

_ESTIMATE_PREFIX = "EXPLAIN ESTIMATE "


def _attribute_filtered_query(project_id):
    """A widget whose exact read discovers candidate identities first."""

    end = datetime(2026, 9, 1, tzinfo=UTC)
    return {
        "project_ids": [str(project_id)],
        "granularity": "day",
        "time_range": {
            "custom_start": (end - timedelta(days=30)).isoformat(),
            "custom_end": end.isoformat(),
        },
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "source": "traces",
                "aggregation": "avg",
            }
        ],
        "filters": [
            {
                "column_id": "final_status",
                "source": "traces",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "settled",
                },
            }
        ],
        "breakdowns": [],
    }


class _StubAnalytics:
    """Records every statement the view actually sends to ClickHouse."""

    def __init__(self, *, estimated_rows=0, probe_error=None, read_bytes=1_000):
        self.estimated_rows = estimated_rows
        self.probe_error = probe_error
        self.read_bytes = read_bytes
        self.statements = []

    def execute_ch_query(self, sql, params=None, timeout_ms=None, settings=None):
        self.statements.append(
            SimpleNamespace(
                sql=sql, params=params, timeout_ms=timeout_ms, settings=settings
            )
        )
        if sql.startswith(_ESTIMATE_PREFIX):
            if self.probe_error is not None:
                raise self.probe_error
            return QueryResult(
                data=[{"parts": 3, "rows": self.estimated_rows}],
                row_count=1,
                backend_used="clickhouse",
                query_time_ms=0.4,
                columns=["parts", "rows"],
                read_bytes=0,
            )
        return QueryResult(
            data=[],
            row_count=0,
            backend_used="clickhouse",
            query_time_ms=500.0,
            columns=[],
            read_bytes=self.read_bytes,
        )

    @property
    def probes(self):
        return [s for s in self.statements if s.sql.startswith(_ESTIMATE_PREFIX)]

    @property
    def reads(self):
        return [s for s in self.statements if not s.sql.startswith(_ESTIMATE_PREFIX)]


def _cold_dashboard_cache_miss(_namespace, _identity, **kwargs):
    payload = dict(kwargs["pending_payload"])
    payload.update(query_refreshing=False, query_refresh_failed=False)
    return payload


def _run_widget_query(analytics, query, workspace, *, exact_worker=False):
    scheduled = MagicMock(return_value={"query_status": "pending"})
    with (
        patch(
            "tracer.views.dashboard._materialize_dashboard_query_scope",
            side_effect=lambda config, *_args, **_kwargs: config,
        ),
        patch(
            "tracer.views.dashboard._read_dashboard_rollup_fast_path",
            return_value=None,
        ),
        patch(
            "tracer.views.dashboard.read_or_schedule_exact_snapshot",
            side_effect=_cold_dashboard_cache_miss,
        ),
        patch(
            "tracer.views.dashboard._read_public_dashboard_query",
            scheduled,
        ),
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService",
            side_effect=lambda **_kwargs: analytics,
        ),
        patch(
            "tracer.views.dashboard._project_queryset_for_dashboard_scope",
            return_value=MagicMock(
                filter=MagicMock(return_value=MagicMock(count=lambda: 1))
            ),
        ),
        patch(
            "tracer.views.dashboard.Project.objects.filter",
            return_value=MagicMock(values_list=MagicMock(return_value=[])),
        ),
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            query,
            workspace,
            _exact_worker=exact_worker,
        )
    return response, scheduled


@pytest.fixture(autouse=True)
def _clean_density_cache(settings):
    settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def workspace():
    return SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())


def _seed_dense_scope(project_id):
    """Teach the scope that its rows are expensive, from completed reads."""

    scope_key = density_scope_key([str(project_id)])
    observe_completed_read(
        scope_key,
        1_000,
        SimpleNamespace(read_bytes=130_000_000, query_time_ms=100.0),
    )
    return scope_key


def test_predicted_heavy_widget_never_runs_its_statement_in_the_foreground(workspace):
    project_id = uuid.uuid4()
    _seed_dense_scope(project_id)
    # 700,000 estimated rows x 130 KB/row / 1.3 MB/ms is far beyond any wall.
    analytics = _StubAnalytics(estimated_rows=700_000)

    _response, scheduled = _run_widget_query(
        analytics, _attribute_filtered_query(project_id), workspace
    )

    assert len(analytics.probes) == 1
    assert analytics.reads == []
    assert scheduled.call_count == 1
    assert scheduled.call_args.kwargs["refresh"] is True


def test_cold_scope_runs_byte_identical_to_the_unrouted_statement(workspace):
    project_id = uuid.uuid4()
    query = _attribute_filtered_query(project_id)

    unrouted = _StubAnalytics(estimated_rows=700_000)
    with patch(
        "tracer.views.dashboard.probe_candidate_estimates",
        return_value={},
    ):
        _run_widget_query(unrouted, query, workspace)

    routed = _StubAnalytics(estimated_rows=700_000)
    _run_widget_query(routed, query, workspace)

    assert len(routed.probes) == 1
    assert [(s.sql, s.params, s.settings) for s in routed.reads] == [
        (s.sql, s.params, s.settings) for s in unrouted.reads
    ]
    assert routed.reads
    assert routed.reads[0].settings == _DASHBOARD_TRACE_READ_SETTINGS


def test_unavailable_probe_keeps_the_inline_path(workspace):
    project_id = uuid.uuid4()
    _seed_dense_scope(project_id)
    analytics = _StubAnalytics(probe_error=RuntimeError("estimate unavailable"))

    _response, scheduled = _run_widget_query(
        analytics, _attribute_filtered_query(project_id), workspace
    )

    assert len(analytics.probes) == 1
    assert len(analytics.reads) == 1
    assert scheduled.call_count == 0


def test_background_worker_never_probes(workspace):
    project_id = uuid.uuid4()
    _seed_dense_scope(project_id)
    analytics = _StubAnalytics(estimated_rows=700_000)

    _run_widget_query(
        analytics,
        _attribute_filtered_query(project_id),
        workspace,
        exact_worker=True,
    )

    assert analytics.probes == []
    assert len(analytics.reads) == 1


def test_completed_inline_read_teaches_the_scope_its_density(workspace):
    project_id = uuid.uuid4()
    analytics = _StubAnalytics(estimated_rows=1_000, read_bytes=2_000_000)

    _run_widget_query(analytics, _attribute_filtered_query(project_id), workspace)

    from tracer.services.clickhouse.dashboard_read_density import read_density_record

    record = read_density_record(density_scope_key([str(project_id)]))
    assert record is not None
    assert record.bytes_per_estimated_row == pytest.approx(2_000.0)
    assert record.bytes_per_ms == pytest.approx(4_000.0)
