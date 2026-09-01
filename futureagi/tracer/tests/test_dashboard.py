"""
Tests for the Custom Dashboards feature.

Covers:
- Dashboard CRUD API
- DashboardWidget CRUD API
- DashboardQueryBuilder (all metric types, time ranges, filters, breakdowns)
- Serializer validation
- Metrics discovery endpoint
- Query execution (mocked ClickHouse)
"""

import hashlib
import inspect
import json
import uuid
from datetime import UTC, datetime, timedelta
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
from urllib.parse import urlencode

import pytest
from clickhouse_driver.errors import NetworkError, ServerException
from django.conf import settings
from django.core import signing

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.develop_dataset import Dataset
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.models.project import Project
from tracer.serializers.dashboard import (
    DashboardCreateUpdateSerializer,
    DashboardFilterValuesResponseSerializer,
    DashboardQueryApiResponseSerializer,
    DashboardQuerySerializer,
    DashboardQuerySeriesSerializer,
    DashboardWidgetSerializer,
)
from tracer.services.clickhouse.attribute_cursor_state import (
    ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS,
    AttributeCursorSeenState,
    load_attribute_cursor_seen_state,
    persist_attribute_cursor_seen_state,
)
from tracer.services.clickhouse.attribute_reads import (
    ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS,
    AttributeReadMetadata,
    AttributeValueCursorPageRead,
    AttributeValueRead,
    AttributeValueRow,
    attribute_value_cursor_digest,
)
from tracer.services.clickhouse.filter_value_reads import (
    FILTER_VALUE_CURSOR_INITIAL_SEGMENT,
    _value_digest,
)
from tracer.services.clickhouse.list_cursor import (
    CURSOR_SALT,
    decode_list_cursor,
    encode_list_cursor,
)
from tracer.services.clickhouse.query_builders.dashboard import (
    AGGREGATIONS,
    FILTER_OPERATORS,
    GRANULARITY_TO_CH,
    PRESET_RANGES,
    SYSTEM_METRICS,
    DashboardQueryBuilder,
    InvalidMetricCombinationError,
    _coerce_filter_value,
    _generate_time_buckets,
    _prefix_spans_columns,
)
from tracer.services.clickhouse.query_builders.dashboard_base import (
    DashboardQueryBuilderBase,
)
from tracer.services.clickhouse.query_builders.dataset_dashboard import (
    DatasetQueryBuilder,
)
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.clickhouse.server_readonly import without_query_settings
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.services.exact_aggregation_cache import snapshot_cache_key
from tracer.utils.graphs_optimized import EvalGraphReadError
from tracer.views.dashboard import (
    _DASHBOARD_EXACT_QUERY_TIMEOUT_MS,
    _DASHBOARD_TRACE_READ_SETTINGS,
    DashboardExactReadError,
    DashboardReadQuerySerializer,
    DashboardViewSet,
    DashboardWidgetViewSet,
    _fetch_exact_dashboard_rows,
    _materialize_dashboard_query_scope,
    _normalize_dashboard_query_filters,
)


def _attribute_value_read(
    values=(), *, attribute_type="string", complete=True, error_code=None
):
    now = datetime(2026, 8, 1, tzinfo=UTC)
    return AttributeValueRead(
        tuple(AttributeValueRow(value, attribute_type, 1) for value in values),
        AttributeReadMetadata(
            query_complete=complete,
            query_status="complete" if complete else "degraded",
            query_error_code=error_code,
            query_window_start=now - timedelta(days=365),
            query_window_end=now,
            query_count=5,
        ),
    )


def _attribute_value_cursor_page(
    values,
    *,
    has_more,
    browse_status=None,
    next_before_identity=None,
    next_resume_identity=None,
    next_resume_member_offset=0,
    seen_value_digests=(),
    next_segment_start=None,
):
    now = datetime(2026, 8, 1, tzinfo=UTC)
    return AttributeValueCursorPageRead(
        tuple(AttributeValueRow(value, "string", 1) for value in values),
        AttributeReadMetadata(
            query_complete=True,
            query_status="complete",
            query_error_code=None,
            query_window_start=now - timedelta(days=365),
            query_window_end=now,
            query_count=2,
        ),
        has_more=has_more,
        next_segment_end=now,
        next_before_identity=next_before_identity,
        next_resume_identity=next_resume_identity,
        next_resume_member_offset=next_resume_member_offset,
        seen_value_digests=seen_value_digests,
        browse_status=browse_status or ("continuation" if has_more else "exhausted"),
        next_segment_start=next_segment_start,
    )


class _DashboardFullWindowAnalytics:
    def __init__(self, *, failure=None):
        self.failure = failure
        self.calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        copied_params = dict(params)
        self.calls.append((query, copied_params, timeout_ms, dict(settings)))
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(
            data=[{"time_bucket": params["start_date"], "value": 1}],
            columns=["time_bucket", "value"],
        )


class _ProductionShapedDashboardAnalytics:
    """Model the observed 114.4M-row exact dashboard scan."""

    rows_to_read = 114_400_000

    def __init__(self):
        self.calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params, timeout_ms, settings))
        assert "max_rows_to_read" not in settings
        return SimpleNamespace(
            data=[
                {
                    "time_bucket": params["start_date"],
                    "value": 1,
                    "eval_attempts": 1,
                }
            ],
            columns=["time_bucket", "value", "eval_attempts"],
        )


def _recording_dashboard_builder(configs):
    class RecordingDashboardBuilder:
        def __init__(self, config):
            self.config = config
            self.metrics = config["metrics"]
            configs.append(config)

        def parse_time_range(self):
            time_range = self.config["time_range"]
            if time_range.get("custom_start") and time_range.get("custom_end"):
                start = time_range["custom_start"]
                end = time_range["custom_end"]
                return (
                    start
                    if isinstance(start, datetime)
                    else datetime.fromisoformat(start),
                    end if isinstance(end, datetime) else datetime.fromisoformat(end),
                )
            return (
                datetime(2026, 7, 1, tzinfo=UTC),
                datetime(2026, 8, 1, tzinfo=UTC),
            )

        def build_metric_query(self, metric):
            window = self.config["time_range"]
            start = window["custom_start"]
            end = window["custom_end"]
            return (
                f"SELECT exact {metric['id']} metric",
                {
                    "start_date": (
                        start
                        if isinstance(start, datetime)
                        else datetime.fromisoformat(start)
                    ),
                    "end_date": (
                        end
                        if isinstance(end, datetime)
                        else datetime.fromisoformat(end)
                    ),
                },
            )

        @staticmethod
        def metric_info(metric):
            return dict(metric)

        def format_results(self, metric_results, **_kwargs):
            return {
                "metrics": [metric_info for metric_info, _rows in metric_results],
                "time_range": self.config["time_range"],
                "granularity": self.config["granularity"],
            }

    return RecordingDashboardBuilder


def _dashboard_full_window_params(*, days):
    return {
        "start_date": datetime(2026, 8, 1, 0, 0),
        "end_date": datetime(2026, 8, 1, 0, 0) + timedelta(days=days),
        "filter_identity": ("final_status", "Rechazado", 0.8, True),
    }


@pytest.mark.unit
def test_dashboard_exact_rows_use_one_full_window_statement_unchanged():
    analytics = _DashboardFullWindowAnalytics()
    settings = dict(_DASHBOARD_TRACE_READ_SETTINGS)
    params = _dashboard_full_window_params(days=365)

    rows = _fetch_exact_dashboard_rows(
        analytics=analytics,
        sql="SELECT exact dashboard metric",
        params=params,
        timeout_ms=_DASHBOARD_EXACT_QUERY_TIMEOUT_MS,
        settings=settings,
    )

    assert rows == [{"time_bucket": params["start_date"], "value": 1}]
    assert len(analytics.calls) == 1
    query, observed_params, timeout_ms, observed_settings = analytics.calls[0]
    assert query == "SELECT exact dashboard metric"
    assert observed_params == params
    assert timeout_ms == _DASHBOARD_EXACT_QUERY_TIMEOUT_MS
    assert observed_settings == settings
    assert "additional_table_filters" not in observed_settings
    assert "snapshot_version_ceiling" not in observed_params


@pytest.mark.unit
def test_dashboard_30d_114m_row_read_has_no_application_row_ceiling():
    analytics = _ProductionShapedDashboardAnalytics()
    params = _dashboard_full_window_params(days=30)
    sql = (
        "WITH window_global_latest_evals AS ("
        "SELECT * FROM usage_apicalllog "
        "WHERE created_at >= %(start_date)s AND created_at < %(end_date)s"
        ") SELECT time_bucket, avg(score) FROM window_global_latest_evals"
    )

    rows = _fetch_exact_dashboard_rows(
        analytics=analytics,
        sql=sql,
        params=params,
        timeout_ms=_DASHBOARD_EXACT_QUERY_TIMEOUT_MS,
        settings=_DASHBOARD_TRACE_READ_SETTINGS,
    )

    assert rows == [
        {
            "time_bucket": params["start_date"],
            "value": 1,
            "eval_attempts": 1,
        }
    ]
    assert len(analytics.calls) == 1
    observed_sql, observed_params, observed_timeout, observed_settings = (
        analytics.calls[0]
    )
    # Identity assertions make query/parameter cloning-and-rewriting fail this
    # test even when a rewritten mapping happens to compare equal.
    assert observed_sql is sql
    assert observed_params is params
    assert observed_settings is _DASHBOARD_TRACE_READ_SETTINGS
    assert observed_timeout == _DASHBOARD_EXACT_QUERY_TIMEOUT_MS
    assert "max_rows_to_read" not in observed_settings
    assert params == _dashboard_full_window_params(days=30)


@pytest.mark.unit
def test_dashboard_full_window_budget_failure_is_not_retried_or_partitioned():
    analytics = _DashboardFullWindowAnalytics(
        failure=ServerException("private read budget detail", code=159)
    )
    published = []
    params = _dashboard_full_window_params(days=30)

    with pytest.raises(ServerException):
        rows = _fetch_exact_dashboard_rows(
            analytics=analytics,
            sql="SELECT exact dashboard metric",
            params=params,
            timeout_ms=_DASHBOARD_EXACT_QUERY_TIMEOUT_MS,
            settings=_DASHBOARD_TRACE_READ_SETTINGS,
        )
        published.extend(rows)

    assert published == []
    assert len(analytics.calls) == 1
    assert analytics.calls[0][1] == params
    assert analytics.calls[0][2] == _DASHBOARD_EXACT_QUERY_TIMEOUT_MS


@pytest.mark.unit
def test_dashboard_full_window_read_keeps_all_independent_finite_limits():
    read_settings = _DASHBOARD_TRACE_READ_SETTINGS

    assert read_settings == {
        "max_threads": settings.DASHBOARD_TRACE_READ_MAX_THREADS,
        "max_bytes_to_read": settings.DASHBOARD_TRACE_READ_MAX_BYTES,
        "max_memory_usage": settings.DASHBOARD_TRACE_READ_MAX_MEMORY_BYTES,
        "read_overflow_mode": "throw",
        "max_result_rows": settings.DASHBOARD_TRACE_READ_MAX_RESULT_ROWS,
        "max_result_bytes": settings.DASHBOARD_TRACE_READ_MAX_RESULT_BYTES,
        "result_overflow_mode": "throw",
        "timeout_overflow_mode": "throw",
    }


@pytest.mark.unit
def test_dashboard_worker_has_one_deadline_for_every_exact_source():
    source = inspect.getsource(DashboardWidgetViewSet._execute_ch_query_config)

    assert (
        _DASHBOARD_EXACT_QUERY_TIMEOUT_MS
        == settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    assert source.count("ReadDeadline.start(") == 1
    assert source.count("timeout_ms=read_deadline.remaining_ms(") == 3
    assert source.count("read_deadline.remaining_ms(floor_ms=1)") == 2
    assert "query_timeout =" not in source


@pytest.mark.unit
def test_dashboard_worker_runs_each_metric_once_without_snapshot_ceiling_metadata():
    start = datetime(2025, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    project_id = "00000000-0000-0000-0000-000000000010"
    eval_id = "00000000-0000-0000-0000-000000000040"
    workspace = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000020",
        organization_id="00000000-0000-0000-0000-000000000030",
    )
    query_config = {
        "project_ids": [project_id],
        "granularity": "day",
        "time_range": {
            "custom_start": start.isoformat(),
            "custom_end": end.isoformat(),
        },
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
                "source": "traces",
            },
            {
                "id": "traffic",
                "name": "traffic",
                "type": "system_metric",
                "aggregation": "count",
                "source": "traces",
            },
            {
                "id": eval_id,
                "name": "quality",
                "type": "eval_metric",
                "aggregation": "avg",
                "output_type": "SCORE",
                "source": "all",
            },
            {
                "id": "cost_breakdown.stt",
                "name": "cost_breakdown.stt",
                "type": "custom_attribute",
                "aggregation": "avg",
                "attribute_key": "cost_breakdown.stt",
                "attribute_type": "number",
                # Simulation custom attributes are stored on trace spans and
                # must enter the exact trace compiler, not the simulation one.
                "source": "simulation",
            },
        ],
        "filters": [],
        "breakdowns": [],
    }

    builder_configs = []

    class FakeTraceBuilder:
        def __init__(self, config):
            self.config = config
            self.metrics = config["metrics"]
            builder_configs.append(config)

        def build_metric_query(self, metric):
            window = self.config["time_range"]
            return (
                f"SELECT {metric['id']} FROM spans FINAL",
                {
                    "start_date": datetime.fromisoformat(window["custom_start"]),
                    "end_date": datetime.fromisoformat(window["custom_end"]),
                },
            )

        @staticmethod
        def metric_info(metric):
            return dict(metric)

        def format_results(self, metric_results, **_kwargs):
            return {
                "metrics": [metric_info for metric_info, _rows in metric_results],
                "time_range": self.config["time_range"],
                "granularity": self.config["granularity"],
            }

    analytics = _DashboardFullWindowAnalytics()
    deadline_lock = Lock()

    class TrackingDeadline:
        def __init__(self):
            self.next_timeout_ms = _DASHBOARD_EXACT_QUERY_TIMEOUT_MS
            self.statement_timeouts = []
            self.publication_fences = 0

        def remaining_ms(self, cap_ms=None, *, floor_ms=25):
            with deadline_lock:
                if cap_ms is None:
                    assert floor_ms == 1
                    self.publication_fences += 1
                    return 1_000
                timeout_ms = min(cap_ms, self.next_timeout_ms)
                self.next_timeout_ms -= 1_000
                self.statement_timeouts.append(timeout_ms)
                return timeout_ms

    deadline = TrackingDeadline()
    project_queryset = MagicMock()
    project_queryset.filter.return_value = project_queryset
    project_queryset.count.return_value = 1
    project_queryset.values_list.return_value = []

    with (
        patch(
            "tracer.views.dashboard._materialize_dashboard_query_scope",
            side_effect=lambda config, *_args, **_kwargs: config,
        ),
        patch(
            "tracer.views.dashboard._project_queryset_for_dashboard_scope",
            return_value=project_queryset,
        ),
        patch(
            "tracer.views.dashboard.Project.objects.filter",
            return_value=project_queryset,
        ),
        patch(
            "tracer.views.dashboard.DashboardQueryBuilderV2",
            FakeTraceBuilder,
        ),
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService",
            return_value=analytics,
        ),
        patch(
            "tracer.views.dashboard.ReadDeadline.start",
            return_value=deadline,
        ) as deadline_start,
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            query_config,
            workspace,
            _exact_worker=True,
            cache_identity_override={
                "workspace_id": workspace.id,
                "query_config": query_config,
            },
        )

    assert len(analytics.calls) == 4
    deadline_start.assert_called_once_with(_DASHBOARD_EXACT_QUERY_TIMEOUT_MS)
    expected_timeouts = [
        _DASHBOARD_EXACT_QUERY_TIMEOUT_MS - (index * 1_000) for index in range(4)
    ]
    assert sorted(deadline.statement_timeouts, reverse=True) == expected_timeouts
    assert deadline.publication_fences == 2
    assert (
        sorted([call[2] for call in analytics.calls], reverse=True) == expected_timeouts
    )
    assert {call[0] for call in analytics.calls} == {
        "SELECT latency FROM spans FINAL",
        "SELECT traffic FROM spans FINAL",
        f"SELECT {eval_id} FROM spans FINAL",
        "SELECT cost_breakdown.stt FROM spans FINAL",
    }
    assert all(
        call_params["start_date"] == start
        and call_params["end_date"] == end
        and "snapshot_version_ceiling" not in call_params
        and "additional_table_filters" not in call_settings
        for _query, call_params, _timeout, call_settings in analytics.calls
    )
    assert builder_configs
    worker_config = builder_configs[0]
    assert worker_config["workspace_id"] == workspace.id
    assert worker_config["organization_id"] == workspace.organization_id
    assert {metric["source"] for metric in worker_config["metrics"]} <= {
        "traces",
        "all",
    }
    routed_custom_metric = next(
        metric
        for metric in worker_config["metrics"]
        if metric["id"] == "cost_breakdown.stt"
    )
    assert routed_custom_metric["source"] == "traces"
    result = response.data["result"]
    assert result["query_complete"] is True
    assert result["query_sampled"] is False
    assert "query_snapshot_version_ceiling" not in result
    assert "query_snapshot_capture_count" not in result
    assert "query_snapshot_relation_count" not in result


@pytest.mark.unit
def test_dashboard_public_fallback_executes_directly_without_scheduling_worker():
    project_id = "00000000-0000-0000-0000-000000000010"
    workspace = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000020",
        organization_id="00000000-0000-0000-0000-000000000030",
    )
    query_config = {
        "project_ids": [project_id],
        "granularity": "day",
        "time_range": {
            "custom_start": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
            "custom_end": datetime(2026, 8, 1, tzinfo=UTC).isoformat(),
        },
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
                "source": "traces",
            }
        ],
        "filters": [],
        "breakdowns": [],
    }
    builder_configs = []
    analytics = _DashboardFullWindowAnalytics()
    project_queryset = MagicMock()
    project_queryset.filter.return_value = project_queryset
    project_queryset.count.return_value = 1
    project_queryset.values_list.return_value = []

    with (
        patch(
            "tracer.views.dashboard._materialize_dashboard_query_scope",
            side_effect=lambda config, *_args, **_kwargs: config,
        ),
        patch(
            "tracer.views.dashboard._bind_dashboard_annotation_completeness",
            side_effect=lambda config, *_args, **_kwargs: config,
        ),
        patch(
            "tracer.views.dashboard._read_dashboard_rollup_fast_path",
            return_value=None,
        ) as rollup,
        patch(
            "tracer.views.dashboard._project_queryset_for_dashboard_scope",
            return_value=project_queryset,
        ),
        patch(
            "tracer.views.dashboard.Project.objects.filter",
            return_value=project_queryset,
        ),
        patch(
            "tracer.views.dashboard.DashboardQueryBuilderV2",
            _recording_dashboard_builder(builder_configs),
        ),
        patch(
            "tracer.views.dashboard.DatasetQueryBuilder",
            _recording_dashboard_builder(builder_configs),
        ),
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService",
            return_value=analytics,
        ),
        patch("tracer.views.dashboard.read_or_schedule_exact_snapshot") as scheduler,
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            query_config,
            workspace,
            refresh=True,
        )

    assert response.status_code == 200
    assert len(analytics.calls) == 1
    assert builder_configs
    assert response.data["result"]["query_complete"] is True
    assert response.data["result"]["query_provenance"] == "exact_snapshot"
    rollup.assert_called_once()
    scheduler.assert_not_called()


@pytest.mark.unit
def test_dashboard_worker_does_not_return_payload_after_formatting_crosses_deadline():
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    project_id = "00000000-0000-0000-0000-000000000010"
    workspace = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000020",
        organization_id="00000000-0000-0000-0000-000000000030",
    )
    query_config = {
        "project_ids": [project_id],
        "granularity": "day",
        "time_range": {
            "custom_start": start.isoformat(),
            "custom_end": end.isoformat(),
        },
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
                "source": "traces",
            }
        ],
        "filters": [],
        "breakdowns": [],
    }
    builder_configs = []
    BaseBuilder = _recording_dashboard_builder(builder_configs)
    formatting = {"complete": False}

    class FormattingBuilder(BaseBuilder):
        def format_results(self, metric_results, **kwargs):
            formatted = super().format_results(metric_results, **kwargs)
            formatting["complete"] = True
            return formatted

    class ExpiresAfterFormattingDeadline:
        def __init__(self):
            self.fences = 0

        def remaining_ms(self, cap_ms=None, *, floor_ms=25):
            if cap_ms is not None:
                return min(cap_ms, _DASHBOARD_EXACT_QUERY_TIMEOUT_MS)
            assert floor_ms == 1
            self.fences += 1
            if self.fences == 1:
                return 1
            raise ReadDeadlineExceeded("deadline")

    analytics = _DashboardFullWindowAnalytics()
    project_queryset = MagicMock()
    project_queryset.filter.return_value = project_queryset
    project_queryset.count.return_value = 1
    project_queryset.values_list.return_value = []

    with (
        patch(
            "tracer.views.dashboard._materialize_dashboard_query_scope",
            side_effect=lambda config, *_args, **_kwargs: config,
        ),
        patch(
            "tracer.views.dashboard._project_queryset_for_dashboard_scope",
            return_value=project_queryset,
        ),
        patch(
            "tracer.views.dashboard.Project.objects.filter",
            return_value=project_queryset,
        ),
        patch("tracer.views.dashboard.DashboardQueryBuilderV2", FormattingBuilder),
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService",
            return_value=analytics,
        ),
        patch(
            "tracer.views.dashboard.ReadDeadline.start",
            return_value=ExpiresAfterFormattingDeadline(),
        ),
    ):
        with pytest.raises(
            DashboardExactReadError,
            match="dashboard exact read deadline exceeded",
        ):
            DashboardWidgetViewSet()._execute_ch_query_config(
                query_config,
                workspace,
                _exact_worker=True,
                cache_identity_override={
                    "workspace_id": workspace.id,
                    "query_config": query_config,
                },
            )

    assert formatting["complete"] is True
    assert len(analytics.calls) == 1


@pytest.mark.django_db
def test_dashboard_worker_accepts_legacy_null_project_in_default_workspace_scope(
    organization,
    workspace,
):
    workspace_project = Project.no_workspace_objects.create(
        name="Workspace project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )
    legacy_project = Project.no_workspace_objects.create(
        name="Legacy null project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )
    Project.no_workspace_objects.filter(id=legacy_project.id).update(workspace=None)
    legacy_project.refresh_from_db()
    assert legacy_project.workspace_id is None
    query_config = {
        "project_ids": [str(workspace_project.id), str(legacy_project.id)],
        "granularity": "day",
        "time_range": {"preset": "30D"},
        "metrics": [
            {
                "id": "project",
                "name": "project",
                "type": "system_metric",
                "source": "traces",
                "aggregation": "avg",
            }
        ],
        "filters": [],
        "breakdowns": [],
    }
    builder_configs = []

    class FakeTraceBuilder:
        def __init__(self, config):
            self.config = config
            self.metrics = config["metrics"]
            builder_configs.append(config)

        def build_metric_query(self, metric):
            window = self.config["time_range"]
            return (
                "SELECT exact project metric FROM spans FINAL",
                {
                    "start_date": datetime.fromisoformat(window["custom_start"]),
                    "end_date": datetime.fromisoformat(window["custom_end"]),
                },
            )

        @staticmethod
        def metric_info(metric):
            return dict(metric)

        def format_results(self, metric_results, **_kwargs):
            return {
                "metrics": [metric_info for metric_info, _rows in metric_results],
                "time_range": self.config["time_range"],
                "granularity": self.config["granularity"],
            }

    analytics = _DashboardFullWindowAnalytics()
    with (
        patch(
            "tracer.views.dashboard.DashboardQueryBuilderV2",
            FakeTraceBuilder,
        ),
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService",
            return_value=analytics,
        ),
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            query_config,
            workspace,
            _exact_worker=True,
            cache_identity_override={
                "workspace_id": str(workspace.id),
                "query_config": query_config,
            },
        )

    assert response.status_code == 200
    assert len(analytics.calls) == 1
    assert builder_configs
    assert set(builder_configs[0]["project_ids"]) == {
        str(workspace_project.id),
        str(legacy_project.id),
    }
    assert response.data["result"]["query_complete"] is True


@pytest.mark.django_db
def test_dashboard_worker_keeps_frozen_empty_project_scope_after_project_added(
    organization,
    workspace,
):
    query_config = {
        "project_ids": [],
        "granularity": "day",
        "time_range": {"preset": "30D"},
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "source": "traces",
                "aggregation": "avg",
            }
        ],
        "filters": [],
        "breakdowns": [],
    }
    cache_identity = {
        "workspace_id": str(workspace.id),
        "query_config": json.loads(json.dumps(query_config)),
    }

    # This project did not exist when the API materialized the concrete empty
    # cache identity above. Worker replay must not reinterpret [] as "all".
    Project.no_workspace_objects.create(
        name="Added after cache identity",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )

    builder_configs = []
    analytics = _DashboardFullWindowAnalytics()
    with (
        patch(
            "tracer.views.dashboard.DashboardQueryBuilderV2",
            _recording_dashboard_builder(builder_configs),
        ),
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService",
            return_value=analytics,
        ),
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            query_config,
            workspace,
            _exact_worker=True,
            cache_identity_override=cache_identity,
        )

    assert response.status_code == 200
    assert analytics.calls == []
    assert builder_configs
    assert all(config["project_ids"] == [] for config in builder_configs)
    metric = response.data["result"]["metrics"][0]
    assert metric["query_complete"] is True
    assert metric["query_status"] == "complete"


@pytest.mark.django_db
def test_dashboard_dataset_worker_replays_internal_concrete_scope(
    organization,
    workspace,
    user,
):
    dataset = Dataset.no_workspace_objects.create(
        name="Worker dataset",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    query_config = {
        "project_ids": [],
        "dataset_ids": [str(dataset.id)],
        "granularity": "day",
        "time_range": {"preset": "30D"},
        "metrics": [
            {
                "id": "row_count",
                "name": "row_count",
                "type": "system_metric",
                "source": "datasets",
                "aggregation": "count",
            }
        ],
        "filters": [],
        "breakdowns": [],
    }
    builder_configs = []
    analytics = _DashboardFullWindowAnalytics()

    with (
        patch(
            "tracer.views.dashboard.DatasetQueryBuilder",
            _recording_dashboard_builder(builder_configs),
        ),
        patch("tracer.views.dashboard.get_clickhouse_client", return_value=object()),
        patch(
            "tracer.views.dashboard.AnalyticsQueryService",
            return_value=analytics,
        ),
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            query_config,
            workspace,
            _exact_worker=True,
            cache_identity_override={
                "workspace_id": str(workspace.id),
                "query_config": query_config,
            },
        )

    assert response.status_code == 200
    assert len(analytics.calls) == 1
    assert any(
        config.get("dataset_ids") == [str(dataset.id)] for config in builder_configs
    )
    assert response.data["result"]["query_complete"] is True

    # dataset_ids remains internal cache state; the public contract stays
    # strict and rejects clients that try to submit it directly.
    public_response = DashboardWidgetViewSet()._execute_ch_query_config(
        query_config,
        workspace,
    )
    assert public_response.status_code == 400


@pytest.mark.django_db
def test_dashboard_dataset_worker_keeps_frozen_empty_scope_after_dataset_added(
    organization,
    workspace,
    user,
):
    query_config = {
        "project_ids": [],
        "dataset_ids": [],
        "granularity": "day",
        "time_range": {"preset": "30D"},
        "metrics": [
            {
                "id": "row_count",
                "name": "row_count",
                "type": "system_metric",
                "source": "datasets",
                "aggregation": "count",
            }
        ],
        "filters": [],
        "breakdowns": [],
    }
    cache_identity = {
        "workspace_id": str(workspace.id),
        "query_config": json.loads(json.dumps(query_config)),
    }
    Dataset.no_workspace_objects.create(
        name="Added after cache identity",
        organization=organization,
        workspace=workspace,
        user=user,
    )

    builder_configs = []
    analytics = _DashboardFullWindowAnalytics()
    with (
        patch(
            "tracer.views.dashboard.DatasetQueryBuilder",
            _recording_dashboard_builder(builder_configs),
        ),
        patch("tracer.views.dashboard.get_clickhouse_client", return_value=object()),
        patch(
            "tracer.views.dashboard.AnalyticsQueryService",
            return_value=analytics,
        ),
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            query_config,
            workspace,
            _exact_worker=True,
            cache_identity_override=cache_identity,
        )

    assert response.status_code == 200
    assert analytics.calls == []
    assert builder_configs
    assert all(config["dataset_ids"] == [] for config in builder_configs)
    metric = response.data["result"]["metrics"][0]
    assert metric["query_complete"] is True
    assert metric["query_status"] == "complete"


@pytest.mark.django_db
def test_dashboard_dataset_worker_includes_authorized_legacy_null_dataset(
    organization,
    workspace,
    user,
):
    legacy_dataset = Dataset.no_workspace_objects.create(
        name="Legacy null dataset",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    Dataset.no_workspace_objects.filter(id=legacy_dataset.id).update(workspace=None)
    legacy_dataset.refresh_from_db()
    assert legacy_dataset.workspace_id is None

    query_config = {
        "project_ids": [],
        "dataset_ids": [str(legacy_dataset.id)],
        "granularity": "day",
        "time_range": {"preset": "30D"},
        "metrics": [
            {
                "id": "row_count",
                "name": "row_count",
                "type": "system_metric",
                "source": "datasets",
                "aggregation": "count",
            }
        ],
        "filters": [],
        "breakdowns": [],
    }
    builder_configs = []
    built_queries = []

    class RecordingRealDatasetBuilder(DatasetQueryBuilder):
        def __init__(self, config):
            super().__init__(config)
            builder_configs.append(config)

        def build_metric_query(self, metric):
            sql, params = super().build_metric_query(metric)
            built_queries.append((sql, params))
            return sql, params

        def format_results(self, metric_results, **_kwargs):
            return {
                "metrics": [metric_info for metric_info, _rows in metric_results],
                "time_range": self.config["time_range"],
                "granularity": self.config["granularity"],
            }

    analytics = _DashboardFullWindowAnalytics()
    with (
        patch(
            "tracer.views.dashboard.DatasetQueryBuilder",
            RecordingRealDatasetBuilder,
        ),
        patch("tracer.views.dashboard.get_clickhouse_client", return_value=object()),
        patch(
            "tracer.views.dashboard.AnalyticsQueryService",
            return_value=analytics,
        ),
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            query_config,
            workspace,
            _exact_worker=True,
            cache_identity_override={
                "workspace_id": str(workspace.id),
                "query_config": query_config,
            },
        )

    assert response.status_code == 200
    assert len(analytics.calls) == 1
    assert built_queries
    sql, params = built_queries[0]
    assert "c.dataset_id IN %(dataset_ids)s" in sql
    assert "workspace_id = toUUID(%(workspace_id)s)" not in sql
    assert params["dataset_ids"] == [str(legacy_dataset.id)]
    assert any(config.get("workspace_id") == "" for config in builder_configs)


def test_join_alias_prefixing_never_rewrites_quoted_customer_data():
    clause = (
        "project_id IN %(project_ids)s "
        "AND start_time >= %(start_date)s "
        "AND created_at >= %(start_date)s - INTERVAL 1 DAY "
        "AND parent_span_id = '' "
        "AND marker = 'project_id / start_time / created_at / parent_span_id' "
        "AND escaped = 'it''s _peerdb_is_deleted'"
    )

    prefixed = _prefix_spans_columns(clause)

    assert "s.project_id IN %(project_ids)s" in prefixed
    assert "s.start_time >= %(start_date)s" in prefixed
    assert "s.created_at >= %(start_date)s - INTERVAL 1 DAY" in prefixed
    assert "s.parent_span_id = ''" in prefixed
    assert "'project_id / start_time / created_at / parent_span_id'" in prefixed
    assert "'it''s _peerdb_is_deleted'" in prefixed


def _get_metrics_with_annotation_labels(auth_client, project_id, label_ids):
    """Force a fresh catalog and model the authoritative label-source read."""

    source = MagicMock()
    source.label_ids_for_project.return_value = [str(value) for value in label_ids]
    source_class = MagicMock(return_value=source)
    with (
        patch(
            "tracer.services.dashboard_metrics_catalog.cache.get",
            return_value=None,
        ),
        patch("tracer.services.dashboard_metrics_catalog.cache.set"),
        patch(
            "tracer.services.dashboard_metrics_catalog.AnnotationLabelScoresProjectPG",
            source_class,
        ) as direct_source_class,
        patch(
            "tracer.services.dashboard_metrics_catalog."
            "V2AnalyticsQueryService.get_span_attribute_keys_ch_for_projects",
            return_value=[],
        ),
    ):
        response = auth_client.get(
            f"/tracer/dashboard/metrics/?project_ids={project_id}"
        )
    direct_source_class.assert_called_once_with()
    source.label_ids_for_project.assert_called_once_with(str(project_id))
    return response


@pytest.fixture
def isolated_eval_usage_analytics():
    """Real CH25 executor with a unique, test-owned eval usage table."""

    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.v2 import get_v2_config

    config = get_v2_config()
    client = ClickHouseClient(
        host=config["host"],
        port=config["tcp_port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
    )
    table = f"_test_dashboard_eval_usage_{uuid.uuid4().hex[:12]}"
    try:
        client.execute(
            f"""
            CREATE TABLE {table} (
                id Int64,
                organization_id UUID,
                workspace_id Nullable(UUID),
                status LowCardinality(String),
                config String DEFAULT '{{}}',
                eval_score Float64 MATERIALIZED
                    JSONExtractFloat(JSONExtractString(config), 'output', 'output'),
                eval_output_str String MATERIALIZED
                    JSONExtractString(JSONExtractString(config), 'output', 'output'),
                eval_trace_id String MATERIALIZED
                    JSONExtractString(JSONExtractString(config), 'trace_id'),
                eval_dataset_id String MATERIALIZED
                    JSONExtractString(JSONExtractString(config), 'dataset_id'),
                source LowCardinality(String),
                source_id String,
                deleted UInt8,
                created_at DateTime64(6, 'UTC'),
                _peerdb_is_deleted UInt8,
                _peerdb_version Int64
            ) ENGINE = ReplacingMergeTree(_peerdb_version)
            ORDER BY (organization_id, source_id, created_at, id)
            """
        )
    except Exception:
        client.close()
        raise

    delegate = AnalyticsQueryService()
    delegate._ch_client = client

    class IsolatedEvalUsageAnalytics:
        def execute_ch_query(self, query, params=None, timeout_ms=10000, settings=None):
            assert "usage_apicalllog" in query
            return delegate.execute_ch_query(
                query.replace("usage_apicalllog", table),
                params,
                timeout_ms=timeout_ms,
                settings=settings,
            )

    try:
        yield IsolatedEvalUsageAnalytics()
    finally:
        client.execute(f"DROP TABLE IF EXISTS {table}")
        client.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dashboard(db, workspace, user):
    return Dashboard.objects.create(
        workspace=workspace,
        name="Test Dashboard",
        description="A test dashboard",
        created_by=user,
        updated_by=user,
    )


@pytest.fixture
def dashboard_widget(db, dashboard, user):
    return DashboardWidget.objects.create(
        dashboard=dashboard,
        name="Latency Chart",
        position=0,
        width=6,
        height=4,
        query_config={
            "project_ids": [str(uuid.uuid4())],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        },
        chart_config={"chart_type": "line"},
        created_by=user,
    )


@pytest.fixture
def sample_query_config():
    return {
        "project_ids": [str(uuid.uuid4())],
        "allow_sampled": True,
        "granularity": "day",
        "time_range": {"preset": "7D"},
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ],
        "filters": [],
        "breakdowns": [],
    }


# ===========================================================================
# Dashboard CRUD API
# ===========================================================================


class TestDashboardCRUD:
    @pytest.mark.django_db
    def test_create_dashboard(self, auth_client, workspace):
        response = auth_client.post(
            "/tracer/dashboard/",
            {"name": "My Dashboard", "description": "Test description"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "My Dashboard"
        assert data["description"] == "Test description"
        assert data["id"] is not None

    @pytest.mark.django_db
    def test_create_dashboard_empty_name_rejected(self, auth_client):
        response = auth_client.post(
            "/tracer/dashboard/",
            {"name": "", "description": "No name"},
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_list_dashboards(self, auth_client, dashboard):
        response = auth_client.get("/tracer/dashboard/")
        assert response.status_code == 200
        data = response.json()["result"]
        assert len(data) >= 1
        names = [d["name"] for d in data]
        assert "Test Dashboard" in names

    @pytest.mark.django_db
    def test_retrieve_dashboard(self, auth_client, dashboard, dashboard_widget):
        response = auth_client.get(f"/tracer/dashboard/{dashboard.id}/")
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Test Dashboard"
        assert "widgets" in data
        assert len(data["widgets"]) == 1

    @pytest.mark.django_db
    def test_update_dashboard(self, auth_client, dashboard):
        response = auth_client.put(
            f"/tracer/dashboard/{dashboard.id}/",
            {"name": "Updated Dashboard", "description": "Updated desc"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Updated Dashboard"

    @pytest.mark.django_db
    def test_partial_update_dashboard(self, auth_client, dashboard):
        response = auth_client.patch(
            f"/tracer/dashboard/{dashboard.id}/",
            {"name": "Patched Name"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Patched Name"
        assert data["description"] == "A test dashboard"

    @pytest.mark.django_db
    def test_delete_dashboard(self, auth_client, dashboard, dashboard_widget):
        response = auth_client.delete(f"/tracer/dashboard/{dashboard.id}/")
        assert response.status_code == 200
        dashboard.refresh_from_db()
        dashboard_widget.refresh_from_db()
        assert dashboard.deleted is True
        assert dashboard.deleted_at is not None
        assert dashboard_widget.deleted is True
        assert dashboard_widget.deleted_at is not None

    @pytest.mark.django_db
    def test_deleted_dashboard_not_in_list(self, auth_client, dashboard):
        dashboard.deleted = True
        dashboard.save()
        response = auth_client.get("/tracer/dashboard/")
        assert response.status_code == 200
        data = response.json()["result"]
        ids = [d["id"] for d in data]
        assert str(dashboard.id) not in ids

    @pytest.mark.django_db
    def test_list_dashboard_has_widget_count(
        self, auth_client, dashboard, dashboard_widget
    ):
        response = auth_client.get("/tracer/dashboard/")
        assert response.status_code == 200
        data = response.json()["result"]
        d = next(item for item in data if item["id"] == str(dashboard.id))
        assert d["widget_count"] == 1


# ===========================================================================
# DashboardWidget CRUD API
# ===========================================================================


class TestDashboardWidgetCRUD:
    @pytest.mark.django_db
    def test_create_widget(self, auth_client, dashboard):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/",
            {
                "name": "New Widget",
                "position": 0,
                "width": 12,
                "height": 6,
                "query_config": {"metrics": [], "project_ids": []},
                "chart_config": {},
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "New Widget"
        assert data["width"] == 12

    @pytest.mark.django_db
    def test_create_widget_default_name(self, auth_client, dashboard):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/",
            {
                "position": 0,
                "query_config": {},
                "chart_config": {},
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Untitled"

    @pytest.mark.django_db
    def test_create_widget_invalid_width(self, auth_client, dashboard):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/",
            {
                "name": "Too Wide",
                "position": 0,
                "width": 15,
                "query_config": {},
                "chart_config": {},
            },
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_update_widget(self, auth_client, dashboard, dashboard_widget):
        response = auth_client.patch(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/",
            {"name": "Updated Widget"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Updated Widget"

    @pytest.mark.django_db
    def test_put_widget_replaces_fields(
        self, auth_client, dashboard, dashboard_widget, sample_query_config
    ):
        response = auth_client.put(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/",
            {
                "name": "Fully Updated Widget",
                "description": "Full update description",
                "position": 2,
                "width": 7,
                "height": 6,
                "query_config": sample_query_config,
                "chart_config": {"chart_type": "bar", "show_legend": False},
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Fully Updated Widget"
        assert data["description"] == "Full update description"
        assert data["position"] == 2
        assert data["width"] == 7
        assert data["height"] == 6
        assert data["query_config"]["metrics"][0]["name"] == "latency"
        assert data["chart_config"]["chart_type"] == "bar"

        dashboard_widget.refresh_from_db()
        assert dashboard_widget.name == "Fully Updated Widget"
        assert dashboard_widget.query_config["metrics"][0]["name"] == "latency"
        assert dashboard_widget.chart_config["chart_type"] == "bar"

    @pytest.mark.django_db
    def test_put_widget_wrong_dashboard_not_found(
        self, auth_client, dashboard, dashboard_widget, user
    ):
        other_dashboard = Dashboard.objects.create(
            workspace=dashboard.workspace,
            name="Other Dashboard",
            description="Other",
            created_by=user,
            updated_by=user,
        )
        response = auth_client.put(
            f"/tracer/dashboard/{other_dashboard.id}/widgets/{dashboard_widget.id}/",
            {
                "name": "Should Not Mutate",
                "position": 1,
                "width": 6,
                "height": 4,
                "query_config": {},
                "chart_config": {},
            },
            format="json",
        )
        assert response.status_code == 404
        dashboard_widget.refresh_from_db()
        assert dashboard_widget.name == "Latency Chart"

    @pytest.mark.django_db
    def test_put_widget_under_deleted_dashboard_not_found(
        self, auth_client, dashboard, dashboard_widget
    ):
        dashboard.deleted = True
        dashboard.save(update_fields=["deleted"])

        response = auth_client.put(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/",
            {
                "name": "Should Not Mutate",
                "position": 1,
                "width": 6,
                "height": 4,
                "query_config": {},
                "chart_config": {},
            },
            format="json",
        )
        assert response.status_code == 404
        dashboard_widget.refresh_from_db()
        assert dashboard_widget.name == "Latency Chart"

    @pytest.mark.django_db
    def test_delete_widget(self, auth_client, dashboard, dashboard_widget):
        response = auth_client.delete(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/"
        )
        assert response.status_code == 200
        dashboard_widget.refresh_from_db()
        assert dashboard_widget.deleted is True

    @pytest.mark.django_db
    def test_create_widget_for_nonexistent_dashboard(self, auth_client):
        fake_id = uuid.uuid4()
        response = auth_client.post(
            f"/tracer/dashboard/{fake_id}/widgets/",
            {"name": "Orphan", "query_config": {}, "chart_config": {}},
            format="json",
        )
        assert response.status_code == 404


class TestWidgetReadEndpoints:
    """GET /dashboard/<pk>/widgets/ (list) and /widgets/<pk>/ (retrieve).

    The web app never calls these two reads (widgets load embedded in the
    dashboard-detail payload), but they are publicly reachable API, so they
    carry a basic happy-path + not-found + workspace-isolation contract.
    """

    @pytest.mark.django_db
    def test_list_returns_dashboard_widgets(
        self, auth_client, dashboard, dashboard_widget
    ):
        response = auth_client.get(f"/tracer/dashboard/{dashboard.id}/widgets/")
        assert response.status_code == 200
        assert str(dashboard_widget.id) in response.content.decode()

    @pytest.mark.django_db
    def test_list_empty_dashboard_returns_ok(self, auth_client, dashboard):
        response = auth_client.get(f"/tracer/dashboard/{dashboard.id}/widgets/")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 0
        assert body["results"] == []

    @pytest.mark.django_db
    def test_retrieve_returns_single_widget(
        self, auth_client, dashboard, dashboard_widget
    ):
        response = auth_client.get(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/"
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert str(dashboard_widget.id) in body
        assert dashboard_widget.name in body

    @pytest.mark.django_db
    def test_retrieve_nonexistent_widget_returns_404(self, auth_client, dashboard):
        response = auth_client.get(
            f"/tracer/dashboard/{dashboard.id}/widgets/{uuid.uuid4()}/"
        )
        assert response.status_code == 404

    @pytest.mark.django_db
    def test_reads_isolate_other_workspace_widgets(
        self, auth_client, organization, user
    ):
        other_ws = Workspace.objects.create(
            name="Other workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        other_dash = Dashboard.objects.create(
            workspace=other_ws,
            name="Other WS Dashboard",
            created_by=user,
            updated_by=user,
        )
        other_widget = DashboardWidget.objects.create(
            dashboard=other_dash,
            name="Secret Chart",
            position=0,
            width=6,
            height=4,
            query_config={},
            chart_config={},
            created_by=user,
        )
        # List must not leak another workspace's widgets.
        list_resp = auth_client.get(f"/tracer/dashboard/{other_dash.id}/widgets/")
        assert list_resp.status_code == 200
        assert str(other_widget.id) not in list_resp.content.decode()
        # The widget itself is not retrievable across the workspace boundary.
        detail_resp = auth_client.get(
            f"/tracer/dashboard/{other_dash.id}/widgets/{other_widget.id}/"
        )
        assert detail_resp.status_code == 404


# ===========================================================================
# Metrics Discovery Endpoint
# ===========================================================================


class TestMetricsEndpoint:
    def test_catalog_dataset_column_uses_exact_column_value_adapter(self):
        from tracer.views.dashboard import DashboardViewSet

        dataset_id = "11111111-1111-4111-8111-111111111111"
        column_id = "22222222-2222-4222-8222-222222222222"
        request = SimpleNamespace(
            workspace=SimpleNamespace(id="workspace-1"),
            validated_query_data={
                "property_id": f"dataset_column:{column_id}",
                "metric_name": column_id,
                "metric_type": "custom_column",
                "source": "datasets",
                "project_ids": [],
                "search": "",
            },
        )
        view = DashboardViewSet()
        sentinel = object()
        deadline = MagicMock()
        view._filter_values_dataset_column = MagicMock(return_value=sentinel)

        with (
            patch(
                "tracer.views.dashboard.ReadDeadline.start",
                return_value=deadline,
            ),
            patch(
                "tracer.views.dashboard._run_filter_value_pg_read",
                return_value=dataset_id,
            ),
        ):
            response = inspect.unwrap(DashboardViewSet.filter_values)(view, request)

        assert response is sentinel
        view._filter_values_dataset_column.assert_called_once_with(
            request,
            dataset_id=dataset_id,
            column_id=column_id,
            query_params=request.validated_query_data,
            deadline=deadline,
        )

    def test_dataset_native_values_use_remaining_wall_and_result_ceiling(self):
        from tracer.views.dashboard import (
            _FINITE_NATIVE_FILTER_VALUE_MAX_RESULT_BYTES,
            DashboardViewSet,
        )

        analytics = MagicMock()
        analytics.execute_ch_query.return_value = SimpleNamespace(
            data=[{"val": "dataset-a"}]
        )
        deadline = MagicMock()
        deadline.remaining_ms.return_value = 321
        request = SimpleNamespace(
            user=SimpleNamespace(pk="user-1"),
            organization=SimpleNamespace(pk="org-1"),
            workspace=SimpleNamespace(pk="workspace-1", id="workspace-1"),
            auth=None,
        )

        with (
            patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True),
            patch(
                "tracer.views.dashboard.AnalyticsQueryService",
                return_value=analytics,
            ),
        ):
            response = DashboardViewSet()._filter_values_dataset(
                request,
                "dataset",
                "system_metric",
                query_params={"page_size": 10, "search": ""},
                deadline=deadline,
            )

        assert response.status_code == 200
        execute_kwargs = analytics.execute_ch_query.call_args.kwargs
        assert execute_kwargs["timeout_ms"] == 321
        assert execute_kwargs["settings"] == {
            "max_result_rows": 5_001,
            "max_result_bytes": _FINITE_NATIVE_FILTER_VALUE_MAX_RESULT_BYTES,
            "result_overflow_mode": "throw",
        }

    def test_dataset_native_values_do_not_relabel_programming_errors_as_retryable(self):
        from tracer.views.dashboard import DashboardViewSet

        analytics = MagicMock()
        analytics.execute_ch_query.side_effect = RuntimeError("broken query builder")
        deadline = MagicMock()
        deadline.remaining_ms.return_value = 321
        request = SimpleNamespace(workspace=SimpleNamespace(id="workspace-1"))

        with (
            patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True),
            patch(
                "tracer.views.dashboard.AnalyticsQueryService",
                return_value=analytics,
            ),
        ):
            response = DashboardViewSet()._filter_values_dataset(
                request,
                "dataset",
                "system_metric",
                query_params={"page_size": 10, "search": ""},
                deadline=deadline,
            )

        assert response.status_code == 500
        assert response.data["code"] == "server_error"

    def test_native_value_vocabularies_use_signed_fixed_size_pages(self):
        from tracer.views.dashboard import DashboardViewSet

        request = SimpleNamespace(
            user=SimpleNamespace(pk="user-1"),
            organization=SimpleNamespace(pk="org-1"),
            workspace=SimpleNamespace(pk="workspace-1"),
            auth=None,
        )
        values = [
            {"value": "alpha", "label": "alpha"},
            {"value": "beta", "label": "beta"},
            {"value": "gamma", "label": "gamma"},
        ]
        view = DashboardViewSet()
        first = view._finite_native_filter_values_response(
            request,
            query_params={"page_size": 2, "search": ""},
            values=values,
            query={"source": "simulation", "metric_name": "status"},
        )
        first_payload = first.data["result"]

        assert first.status_code == 200
        assert first_payload["values"] == values[:2]
        assert first_payload["query_complete"] is True
        assert first_payload["has_more"] is True
        assert first_payload["next_cursor"]

        second = view._finite_native_filter_values_response(
            request,
            query_params={
                "page_size": 2,
                "search": "",
                "cursor": first_payload["next_cursor"],
            },
            values=values,
            query={"source": "simulation", "metric_name": "status"},
        )
        second_payload = second.data["result"]

        assert second.status_code == 200
        assert second_payload["values"] == values[2:]
        assert second_payload["has_more"] is False
        assert second_payload["next_cursor"] is None

    def test_native_value_cursor_rejects_vocabulary_drift(self):
        from tracer.views.dashboard import DashboardViewSet

        request = SimpleNamespace(
            user=SimpleNamespace(pk="user-1"),
            organization=SimpleNamespace(pk="org-1"),
            workspace=SimpleNamespace(pk="workspace-1"),
            auth=None,
        )
        view = DashboardViewSet()
        first = view._finite_native_filter_values_response(
            request,
            query_params={"page_size": 1, "search": ""},
            values=[
                {"value": "alpha", "label": "alpha"},
                {"value": "beta", "label": "beta"},
            ],
            query={"source": "datasets", "metric_name": "dataset"},
        )

        changed = view._finite_native_filter_values_response(
            request,
            query_params={
                "page_size": 1,
                "search": "",
                "cursor": first.data["result"]["next_cursor"],
            },
            values=[
                {"value": "alpha", "label": "alpha"},
                {"value": "changed", "label": "changed"},
            ],
            query={"source": "datasets", "metric_name": "dataset"},
        )

        assert changed.status_code == 400

    def test_native_value_vocabularies_refuse_oversized_success(self):
        from tracer.views.dashboard import (
            _LEGACY_NATIVE_FILTER_VALUE_MAX,
            DashboardViewSet,
        )

        request = SimpleNamespace()
        values = [
            {"value": str(index), "label": str(index)}
            for index in range(_LEGACY_NATIVE_FILTER_VALUE_MAX + 1)
        ]
        response = DashboardViewSet()._finite_native_filter_values_response(
            request,
            query_params={"search": ""},
            values=values,
            query={"source": "datasets", "metric_name": "dataset"},
        )

        assert response.status_code == 422
        assert response.data["code"] == "filter_value_inventory_too_broad"

    def test_dashboard_eval_config_registry_id_resolves_to_its_template(self):
        config_id = "11111111-1111-4111-8111-111111111111"
        template_id = "22222222-2222-4222-8222-222222222222"

        class _ConfigQuery:
            def __init__(self):
                self.filters = []

            def filter(self, **kwargs):
                self.filters.append(kwargs)
                return self

            def values_list(self, *_args, **_kwargs):
                return self

            def first(self):
                return template_id

        query = _ConfigQuery()
        builder = DashboardQueryBuilder(
            {
                "organization_id": "org-1",
                "workspace_id": "workspace-1",
                "project_ids": ["project-1"],
            }
        )
        with patch("tracer.models.custom_eval_config.CustomEvalConfig.objects", query):
            resolved = builder._resolve_eval_template_identity(
                {"property_id": f"eval_config:{config_id}"}, config_id
            )

        assert resolved == template_id
        assert any(filters.get("id") == config_id for filters in query.filters)
        assert any(
            filters.get("project_id__in") == ["project-1"] for filters in query.filters
        )

    def test_dashboard_eval_template_identity_accepts_only_same_org_or_global_system(self):
        template_id = "22222222-2222-4222-8222-222222222222"

        class _TemplateQuery:
            def __init__(self):
                self.filters = []

            def filter(self, *args, **kwargs):
                self.filters.append((args, kwargs))
                return self

            def values_list(self, *_args, **_kwargs):
                return self

            def first(self):
                return template_id

        query = _TemplateQuery()
        builder = DashboardQueryBuilder(
            {
                "organization_id": "org-1",
                "workspace_id": "workspace-1",
                "project_ids": [],
            }
        )
        with patch(
            "model_hub.models.evals_metric.EvalTemplate.no_workspace_objects",
            query,
        ):
            resolved = builder._resolve_eval_template_identity(
                {"property_id": f"eval_template:{template_id}"}, template_id
            )

        assert resolved == template_id
        assert query.filters[0] == (
            (),
            {"id": template_id, "deleted": False},
        )
        tenant_scope = repr(query.filters[1][0][0])
        assert "organization_id" in tenant_scope
        assert "org-1" in tenant_scope
        assert "organization_id__isnull" in tenant_scope
        assert "owner" in tenant_scope
        assert "system" in tenant_scope

    def test_metrics_catalog_contract_keeps_runtime_registry_adapter_fields(self):
        from tracer.serializers.dashboard import DashboardMetricCatalogItemSerializer

        serializer = DashboardMetricCatalogItemSerializer(
            data={
                "name": "config-id",
                "property_id": "eval_config:config-id",
                "property_kind": "eval_config",
                "category": "eval_metric",
                "source": "all",
                "eval_template_id": "22222222-2222-4222-8222-222222222222",
                "role": "metric",
            }
        )
        assert serializer.is_valid(), serializer.errors
        assert str(serializer.validated_data["eval_template_id"]) == (
            "22222222-2222-4222-8222-222222222222"
        )
        assert serializer.validated_data["role"] == "metric"

    def test_property_registry_identities_keep_system_and_customer_keys_distinct(self):
        from tracer.services.dashboard_metrics_catalog import (
            _annotate_property_registry_identity,
        )

        metrics = _annotate_property_registry_identity(
            [
                {
                    "name": "model",
                    "category": "system_metric",
                    "source": "traces",
                },
                {"name": "model", "category": "custom_attribute"},
                {"name": "template-id", "category": "eval_metric"},
                {
                    "name": "config-id",
                    "category": "eval_metric",
                    "_property_kind": "eval_config",
                },
                {"name": "label-id", "category": "annotation_metric"},
                {"name": "column-id", "category": "custom_column"},
            ]
        )

        assert [metric["property_id"] for metric in metrics] == [
            "system_attribute:traces:model",
            "custom_attribute:model",
            "eval_template:template-id",
            "eval_config:config-id",
            "annotation:label-id",
            "dataset_column:column-id",
        ]
        assert metrics[0]["property_id"] != metrics[1]["property_id"]

    def test_property_registry_id_routes_to_native_filter_value_adapter(self):
        from tracer.serializers.dashboard import DashboardFilterValuesQuerySerializer

        serializer = DashboardFilterValuesQuerySerializer(
            data={
                "property_id": "system_attribute:traces:model",
                "source": "traces",
                "page_size": 10,
            }
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["metric_name"] == "model"
        assert serializer.validated_data["metric_type"] == "system_metric"
        assert serializer.validated_data["source"] == "traces"

        voice_transport = DashboardFilterValuesQuerySerializer(
            data={
                "property_id": "system_attribute:voice_calls:call_status",
                "source": "traces",
            }
        )
        assert voice_transport.is_valid(), voice_transport.errors
        assert voice_transport.validated_data["metric_name"] == "call_status"

        wrong_source = DashboardFilterValuesQuerySerializer(
            data={
                "property_id": "system_attribute:traces:model",
                "source": "sessions",
            }
        )
        assert not wrong_source.is_valid()
        assert "property_id" in wrong_source.errors

        legacy = DashboardFilterValuesQuerySerializer(
            data={
                "metric_name": "model",
                "metric_type": "system_metric",
                "source": "sessions",
            }
        )
        assert legacy.is_valid(), legacy.errors

        legacy_registry_id = DashboardFilterValuesQuerySerializer(
            data={"property_id": "eval:legacy-id", "source": "traces"}
        )
        assert legacy_registry_id.is_valid(), legacy_registry_id.errors
        assert legacy_registry_id.validated_data["metric_type"] == "eval_metric"
        assert legacy_registry_id.validated_data["_property_kind"] == "eval"

        eval_config = DashboardFilterValuesQuerySerializer(
            data={"property_id": "eval_config:config-id", "source": "traces"}
        )
        assert eval_config.is_valid(), eval_config.errors
        assert eval_config.validated_data["_property_kind"] == "eval_config"

        eval_template = DashboardFilterValuesQuerySerializer(
            data={"property_id": "eval_template:template-id", "source": "traces"}
        )
        assert eval_template.is_valid(), eval_template.errors
        assert eval_template.validated_data["_property_kind"] == "eval_template"

        conflicting = DashboardFilterValuesQuerySerializer(
            data={
                "property_id": "custom_attribute:model",
                "metric_name": "other",
            }
        )
        assert not conflicting.is_valid()
        assert "metric_name" in conflicting.errors

    def test_eval_filter_values_never_guess_between_config_and_template_ids(self):
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.views.dashboard import DashboardViewSet

        metric_id = "11111111-1111-4111-8111-111111111111"
        project_scope = SimpleNamespace(
            mode="fixed",
            batched=False,
            project_ids=("project-1",),
            requested_project_ids=frozenset({"project-1"}),
        )
        eval_template = SimpleNamespace(
            config={"output": "PASS_FAIL"},
            choices=[],
        )
        config = SimpleNamespace(
            project_id="project-1",
            eval_template=eval_template,
        )

        class _EvalConfigQuery:
            def __init__(self, *, config_result=None, template_result=None):
                self.config_result = config_result
                self.template_result = template_result
                self.lookups = []
                self.current_lookup = {}

            def filter(self, *_args, **kwargs):
                self.lookups.append(kwargs)
                if "id" in kwargs or "eval_template_id" in kwargs:
                    self.current_lookup = kwargs
                return self

            def select_related(self, *_args):
                return self

            def first(self):
                if "id" in self.current_lookup:
                    return self.config_result
                if "eval_template_id" in self.current_lookup:
                    return self.template_result
                return None

        def run_request(property_kind, query, *, page_size=None):
            property_id = f"{property_kind}:{metric_id}"
            request_data = {
                "property_id": property_id,
                "_property_kind": property_kind,
                "metric_name": metric_id,
                "metric_type": "eval_metric",
                "source": "traces",
                "project_ids": ["project-1"],
                "search": "",
            }
            if page_size is not None:
                request_data["page_size"] = page_size
            request = SimpleNamespace(
                workspace=SimpleNamespace(id="workspace-1"),
                validated_query_data=request_data,
            )
            view = DashboardViewSet()
            with (
                patch(
                    "tracer.views.dashboard._prepare_filter_value_project_scope",
                    return_value=project_scope,
                ),
                patch(
                    "tracer.views.dashboard._run_filter_value_pg_read",
                    side_effect=lambda _deadline, reader: reader(),
                ),
                patch(
                    "tracer.views.dashboard.project_workspace_scope_q",
                    return_value=object(),
                ),
                patch.object(
                    CustomEvalConfig,
                    "no_workspace_objects",
                    query,
                ),
                patch(
                    "tracer.views.dashboard._finite_filter_value_cursor_page",
                    return_value={"values": [], "query_complete": True},
                ) as finite_page,
            ):
                inspect.unwrap(DashboardViewSet.filter_values)(view, request)
            return property_id, finite_page

        config_query = _EvalConfigQuery(config_result=None, template_result=config)
        run_request("eval_config", config_query)
        assert any("id" in lookup for lookup in config_query.lookups)
        assert not any("eval_template_id" in lookup for lookup in config_query.lookups)

        template_query = _EvalConfigQuery(config_result=config, template_result=config)
        property_id, finite_page = run_request(
            "eval_template",
            template_query,
            page_size=10,
        )
        assert not any("id" in lookup for lookup in template_query.lookups)
        assert any("eval_template_id" in lookup for lookup in template_query.lookups)
        assert finite_page.call_args.kwargs["query"]["property_id"] == property_id

    def test_property_registry_id_is_bound_to_persisted_filter_family(self):
        from rest_framework import serializers

        from tracer.serializers.filters import FilterItemField

        field = FilterItemField()
        valid = field.run_validation(
            {
                "column_id": "model",
                "property_id": "custom_attribute:model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "gpt-4.1",
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        )
        assert valid["property_id"] == "custom_attribute:model"

        with pytest.raises(serializers.ValidationError, match="col_type"):
            field.run_validation(
                {
                    "column_id": "model",
                    "property_id": "system_attribute:traces:model",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4.1",
                        "col_type": "SPAN_ATTRIBUTE",
                    },
                }
            )

        session_filter = field.run_validation(
            {
                "column_id": "session_id",
                "property_id": "system_attribute:sessions:session",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "session-123",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        )
        assert session_filter["property_id"] == "system_attribute:sessions:session"

        annotation_choice_filter = field.run_validation(
            {
                "column_id": "annotation-id**thumbs_up",
                "property_id": "annotation:annotation-id",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0,
                    "col_type": "ANNOTATION",
                },
            }
        )
        assert annotation_choice_filter["property_id"] == "annotation:annotation-id"

        eval_choice_filter = field.run_validation(
            {
                "column_id": "eval-config-id**positive",
                "property_id": "eval_config:eval-config-id",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0,
                    "col_type": "EVAL_METRIC",
                },
            }
        )
        assert eval_choice_filter["property_id"] == "eval_config:eval-config-id"

        with pytest.raises(serializers.ValidationError, match="column_id"):
            field.run_validation(
                {
                    "column_id": "other-eval-config-id**positive",
                    "property_id": "eval_config:eval-config-id",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 0,
                        "col_type": "EVAL_METRIC",
                    },
                }
            )

        with pytest.raises(serializers.ValidationError, match="column_id"):
            field.run_validation(
                {
                    "column_id": "other-annotation-id**thumbs_up",
                    "property_id": "annotation:annotation-id",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 0,
                        "col_type": "ANNOTATION",
                    },
                }
            )

        with pytest.raises(serializers.ValidationError, match="source"):
            field.run_validation(
                {
                    "column_id": "model",
                    "property_id": "system_attribute:traces:model",
                    "source": "sessions",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4.1",
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            )

    def test_exact_graph_registry_binding_requires_config_identity_and_source(self):
        from rest_framework import serializers

        from tracer.serializers.filters import ObserveGraphMetricConfigField
        from tracer.utils.property_registry import validate_property_graph_namespace

        field = ObserveGraphMetricConfigField()
        valid = field.run_validation(
            {
                "id": "config-id",
                "type": "EVAL",
                "property_id": "eval_config:config-id",
                "source": "traces",
            }
        )
        assert valid["property_id"] == "eval_config:config-id"

        span_metric = field.run_validation(
            {
                "id": "latency",
                "type": "SYSTEM_METRIC",
                "property_id": "system_attribute:spans:latency",
                "source": "traces",
            }
        )
        assert span_metric["source"] == "traces"
        validate_property_graph_namespace(
            span_metric["property_id"], expected_definition_source="spans"
        )
        with pytest.raises(ValueError, match="graph endpoint"):
            validate_property_graph_namespace(
                span_metric["property_id"], expected_definition_source="traces"
            )

        user_metric = field.run_validation(
            {
                "id": "latency",
                "type": "SYSTEM_METRIC",
                "property_id": "system_attribute:users:latency",
                "source": "sessions",
            }
        )
        assert user_metric["source"] == "sessions"
        with pytest.raises(ValueError, match="graph endpoint"):
            validate_property_graph_namespace(
                user_metric["property_id"], expected_definition_source="sessions"
            )

        for ambiguous_id in ("eval_template:config-id", "eval:config-id"):
            with pytest.raises(serializers.ValidationError, match="exact graph"):
                field.run_validation(
                    {
                        "id": "config-id",
                        "type": "EVAL",
                        "property_id": ambiguous_id,
                        "source": "traces",
                    }
                )

        with pytest.raises(serializers.ValidationError, match="provided together"):
            field.run_validation(
                {
                    "id": "latency",
                    "type": "SYSTEM_METRIC",
                    "property_id": "system_attribute:traces:latency",
                }
            )

        # Pre-registry clients remain supported only when neither identity
        # field is present.
        assert field.run_validation({"id": "config-id", "type": "EVAL"}) == {
            "id": "config-id",
            "type": "EVAL",
        }

    def test_property_registry_id_is_bound_to_saved_metric_family(self):
        from tracer.serializers.dashboard import (
            DashboardBreakdownSerializer,
            DashboardMetricSerializer,
        )

        valid_metric = DashboardMetricSerializer(
            data={
                "name": "model",
                "property_id": "system_attribute:traces:model",
                "type": "system_metric",
            }
        )
        assert valid_metric.is_valid(), valid_metric.errors

        template_metric = DashboardMetricSerializer(
            data={
                "name": "template-id",
                "property_id": "eval_template:template-id",
                "type": "eval_metric",
                "source": "all",
            }
        )
        assert template_metric.is_valid(), template_metric.errors

        config_metric = DashboardMetricSerializer(
            data={
                "name": "config-id",
                "property_id": "eval_config:config-id",
                "type": "eval_metric",
                "source": "traces",
            }
        )
        assert config_metric.is_valid(), config_metric.errors

        wrong_source = DashboardMetricSerializer(
            data={
                "name": "model",
                "property_id": "system_attribute:traces:model",
                "type": "system_metric",
                "source": "datasets",
            }
        )
        assert not wrong_source.is_valid()
        assert "property_id" in wrong_source.errors

        invalid_metric = DashboardMetricSerializer(
            data={
                "name": "model",
                "attribute_key": "model",
                "property_id": "custom_attribute:model",
                "type": "system_metric",
            }
        )
        assert not invalid_metric.is_valid()
        assert "property_id" in invalid_metric.errors

        annotation_breakdown = DashboardBreakdownSerializer(
            data={
                "name": "Quality",
                "label_id": "label-id",
                "property_id": "annotation:label-id",
                "type": "annotation_metric",
            }
        )
        assert annotation_breakdown.is_valid(), annotation_breakdown.errors

    def test_metrics_cache_separates_attribute_and_finite_catalogs(self):
        from tracer.services.dashboard_metrics_catalog import (
            get_cached_metrics_catalog,
        )

        workspace = SimpleNamespace(id="workspace-a")
        observed_keys = []

        def cached_empty_catalog(cache_key):
            observed_keys.append(cache_key)
            return []

        with patch(
            "tracer.services.dashboard_metrics_catalog.cache.get",
            side_effect=cached_empty_catalog,
        ):
            get_cached_metrics_catalog(workspace, include_custom_attributes=True)
            get_cached_metrics_catalog(workspace, include_custom_attributes=False)

        assert len(observed_keys) == 2
        assert observed_keys[0] != observed_keys[1]
        assert ":1::" in observed_keys[0]
        assert ":0::" in observed_keys[1]

    @pytest.mark.django_db
    def test_metrics_without_project_ids_returns_all(self, auth_client):
        """Unified metrics endpoint returns all metrics even without project_ids."""
        response = auth_client.get("/tracer/dashboard/metrics/")
        assert response.status_code == 200
        data = response.json()["result"]
        assert "metrics" in data

    @pytest.mark.django_db
    def test_metrics_endpoint_survives_cache_backend_outage(
        self, auth_client, observe_project
    ):
        """Prod django-redis has no ``IGNORE_EXCEPTIONS``, so a cache-backend
        outage used to re-raise into the view's ``except`` and 500 the
        metrics endpoint. The cache is best-effort — a get/set failure must
        fall through to ``build_metrics_catalog`` and return live results.
        """
        with (
            patch(
                "tracer.services.dashboard_metrics_catalog.cache.get",
                side_effect=RuntimeError("redis down"),
            ),
            patch(
                "tracer.services.dashboard_metrics_catalog.cache.set",
                side_effect=RuntimeError("redis down"),
            ),
            patch(
                "tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService"
            ) as analytics_cls,
        ):
            analytics_cls.return_value.get_span_attribute_keys_ch_for_projects.return_value = []
            response = auth_client.get(
                f"/tracer/dashboard/metrics/?project_ids={observe_project.id}"
            )
        assert response.status_code == 200
        metric_names = [m["name"] for m in response.json()["result"]["metrics"]]
        assert "latency" in metric_names

    @pytest.mark.django_db
    @patch("tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService")
    def test_metrics_returns_system_metrics(
        self, mock_analytics_cls, auth_client, observe_project
    ):
        mock_analytics_cls.return_value.get_span_attribute_keys_ch_for_projects.return_value = []
        response = auth_client.get(
            f"/tracer/dashboard/metrics/?project_ids={observe_project.id}"
        )
        assert response.status_code == 200
        data = response.json()["result"]
        # Unified API returns flat {"metrics": [...]} array
        assert "metrics" in data
        metric_names = [m["name"] for m in data["metrics"]]
        assert "latency" in metric_names
        assert "cost" in metric_names
        # latency_ms ("Duration") was removed from the catalog (duplicate of latency)
        assert "latency_ms" not in metric_names

    @pytest.mark.django_db
    @patch("tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService")
    def test_metrics_can_exclude_capped_attributes_without_losing_finite_catalog(
        self,
        mock_analytics_cls,
        auth_client,
        organization,
        workspace,
        observe_project,
        custom_eval_config,
        _annotation_label_factory,
    ):
        from model_hub.models.choices import (
            DataTypeChoices,
            SourceChoices,
            StatusType,
        )
        from model_hub.models.develop_dataset import Column, Dataset

        custom_eval_config.project = observe_project
        custom_eval_config.save(update_fields=["project"])
        annotation_label = _annotation_label_factory("Retained Annotation")
        dataset = Dataset.objects.create(
            name="Retained Dataset",
            organization=organization,
            workspace=workspace,
        )
        column = Column.objects.create(
            name="Retained Dataset Number",
            data_type=DataTypeChoices.FLOAT.value,
            source=SourceChoices.OTHERS.value,
            status=StatusType.RUNNING.value,
            dataset=dataset,
        )

        with (
            patch(
                "tracer.services.dashboard_metrics_catalog.cache.get",
                return_value=None,
            ),
            patch("tracer.services.dashboard_metrics_catalog.cache.set"),
            patch(
                "tracer.services.dashboard_metrics_catalog.Project.objects.filter"
            ) as project_filter,
        ):
            response = auth_client.get(
                "/tracer/dashboard/metrics/",
                {
                    "exclude_custom_attributes": "true",
                    "page": 1,
                    "page_size": 200,
                },
            )

        assert response.status_code == 200
        metrics = response.json()["result"]["metrics"]
        metric_names = {metric["name"] for metric in metrics}
        categories = {metric["category"] for metric in metrics}
        assert "latency" in metric_names
        assert str(custom_eval_config.eval_template_id) in metric_names
        assert str(annotation_label.id) in metric_names
        assert str(column.id) in metric_names
        assert {
            "system_metric",
            "eval_metric",
            "annotation_metric",
            "custom_column",
        } <= categories
        assert "custom_attribute" not in categories
        project_filter.assert_not_called()
        mock_analytics_cls.return_value.get_span_attribute_keys_ch_for_projects.assert_not_called()

    @pytest.mark.django_db
    def test_metrics_keeps_configured_evals_when_direct_usage_read_times_out(
        self,
        auth_client,
        observe_project,
        custom_eval_config,
    ):
        custom_eval_config.project = observe_project
        custom_eval_config.save(update_fields=["project"])
        analytics = MagicMock()
        analytics.get_span_attribute_keys_ch_for_projects.return_value = []
        analytics.get_eval_config_ids_for_candidates_ch.side_effect = RuntimeError(
            "private ClickHouse timeout detail"
        )

        with (
            patch(
                "tracer.services.dashboard_metrics_catalog.cache.get",
                return_value=None,
            ),
            patch("tracer.services.dashboard_metrics_catalog.cache.set"),
            patch(
                "tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService",
                return_value=analytics,
            ),
            patch(
                "tracer.services.dashboard_metrics_catalog.logger.warning"
            ) as warning,
        ):
            response = auth_client.get(
                f"/tracer/dashboard/metrics/?project_ids={observe_project.id}"
            )

        assert response.status_code == 200
        metric_names = {item["name"] for item in response.json()["result"]["metrics"]}
        assert str(custom_eval_config.eval_template_id) in metric_names
        assert "private ClickHouse timeout detail" not in str(response.json())
        assert any(
            call.args
            and call.args[0] == "dashboard_metrics_catalog_optimization_fallback"
            and call.kwargs.get("optimization") == "eval_usage"
            and call.kwargs.get("fallback") == "configured_eval_definitions"
            for call in warning.call_args_list
        )

    @pytest.mark.django_db
    def test_metrics_returns_agent_scoped_simulation_eval_metrics(
        self, auth_client, organization, workspace
    ):
        from model_hub.models.evals_metric import EvalTemplate
        from simulate.models import AgentDefinition
        from simulate.models.eval_config import SimulateEvalConfig
        from simulate.models.run_test import RunTest

        agent = AgentDefinition.objects.create(
            agent_name="Metrics Agent",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+1234567001",
            inbound=True,
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        run_test = RunTest.objects.create(
            name="Metrics Test",
            agent_definition=agent,
            organization=organization,
            workspace=workspace,
        )
        template = EvalTemplate.objects.create(
            name="Metrics Eval",
            organization=organization,
            workspace=workspace,
            config={"output": "pass_fail"},
        )
        eval_config = SimulateEvalConfig.objects.create(
            name="Metrics Eval Config",
            eval_template=template,
            run_test=run_test,
        )

        response = auth_client.get(
            f"/tracer/dashboard/metrics/?agent_definition_id={agent.id}"
        )

        assert response.status_code == 200
        metric = next(
            m
            for m in response.json()["result"]["metrics"]
            if m["name"] == str(eval_config.id)
        )
        assert metric["display_name"] == "Metrics Eval Config"
        assert metric["category"] == "eval_metric"
        assert metric["source"] == "simulation"
        assert metric["output_type"] == "PASS_FAIL"
        assert metric["choices"] == ["Passed", "Failed"]

    @pytest.mark.integration
    @pytest.mark.django_db
    def test_metrics_includes_span_backed_annotation_labels(
        self, auth_client, project, observation_span, user, organization, workspace
    ):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score

        label = AnnotationsLabels.objects.create(
            name="Quality",
            type=AnnotationTypeChoices.NUMERIC.value,
            organization=organization,
            workspace=workspace,
            project=project,
            settings={
                "min": 0,
                "max": 10,
                "step_size": 1,
                "display_type": "slider",
            },
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=observation_span,
            label=label,
            annotator=user,
            value={"value": 7},
            score_source="human",
            organization=organization,
            workspace=workspace,
        )

        response = _get_metrics_with_annotation_labels(
            auth_client,
            project.id,
            [label.id],
        )

        assert response.status_code == 200
        metric_names = [m["name"] for m in response.json()["result"]["metrics"]]
        assert str(label.id) in metric_names

    @pytest.mark.django_db
    def test_metrics_includes_configured_project_annotation_before_first_score(
        self,
        auth_client,
        project,
        organization,
        workspace,
    ):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels

        label = AnnotationsLabels.objects.create(
            name="Ready Before First Score",
            type=AnnotationTypeChoices.CATEGORICAL.value,
            organization=organization,
            workspace=workspace,
            project=project,
            settings={
                "options": [
                    {"value": "ready", "label": "Ready"},
                    {"value": "blocked", "label": "Blocked"},
                ],
                "strategy": None,
                "auto_annotate": False,
                "multi_choice": False,
                "rule_prompt": "",
            },
        )

        response = _get_metrics_with_annotation_labels(
            auth_client,
            project.id,
            [],
        )

        assert response.status_code == 200
        metric = next(
            entry
            for entry in response.json()["result"]["metrics"]
            if entry["name"] == str(label.id)
        )
        assert metric["category"] == "annotation_metric"
        assert metric["choice_options"] == [
            {"value": "ready", "label": "Ready"},
            {"value": "blocked", "label": "Blocked"},
        ]

    @pytest.mark.django_db
    @patch("tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService")
    def test_metrics_suppresses_customer_attribute_aliases_when_canonical_metric_exists(
        self,
        mock_analytics_cls,
        auth_client,
        observe_project,
    ):
        mock_analytics_cls.return_value.get_span_attribute_keys_ch_for_projects.return_value = [
            {"key": "call.bot_wpm", "type": "string"},
            {"key": "call.user_wpm", "type": "string"},
            {"key": "freeform.attr", "type": "string"},
        ]

        response = auth_client.get(
            f"/tracer/dashboard/metrics/?project_ids={observe_project.id}"
        )

        assert response.status_code == 200
        metrics = response.json()["result"]["metrics"]
        metric_names = [m["name"] for m in metrics]
        assert "bot_wpm" in metric_names
        assert "user_wpm" in metric_names
        assert "call.bot_wpm" not in metric_names
        assert "call.user_wpm" not in metric_names
        assert "freeform.attr" in metric_names

    @pytest.mark.django_db
    @patch("tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService")
    def test_metrics_exposes_agent_talk_percentage_for_simulator_project(
        self,
        mock_analytics_cls,
        auth_client,
        organization,
        workspace,
    ):
        from model_hub.models.ai_model import AIModel
        from tracer.models.project import Project, ProjectSourceChoices

        simulator_project = Project.objects.create(
            name="Voice Project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            source=ProjectSourceChoices.SIMULATOR.value,
        )
        mock_analytics_cls.return_value.get_span_attribute_keys_ch_for_projects.return_value = [
            {"key": "call.talk_ratio", "type": "number"},
            {"key": "freeform.attr", "type": "string"},
        ]

        response = auth_client.get(
            f"/tracer/dashboard/metrics/?project_ids={simulator_project.id}"
        )

        assert response.status_code == 200
        metrics = response.json()["result"]["metrics"]
        metric_names = [m["name"] for m in metrics]
        assert "agent_talk_percentage" in metric_names
        # Raw call.talk_ratio collapsed by _suppress_customer_attribute_metric_aliases
        # once the canonical agent_talk_percentage is published.
        assert "call.talk_ratio" not in metric_names
        assert "freeform.attr" in metric_names

        entry = next(m for m in metrics if m["name"] == "agent_talk_percentage")
        assert entry["category"] == "system_metric"
        assert entry["source"] == "traces"
        assert entry["type"] == "number"

    @pytest.mark.django_db
    @patch("tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService")
    def test_metrics_hides_agent_talk_percentage_for_non_simulator_project(
        self,
        mock_analytics_cls,
        auth_client,
        observe_project,
    ):
        # observe_project defaults to ProjectSourceChoices.PROTOTYPE.
        mock_analytics_cls.return_value.get_span_attribute_keys_ch_for_projects.return_value = [
            {"key": "call.talk_ratio", "type": "number"}
        ]

        response = auth_client.get(
            f"/tracer/dashboard/metrics/?project_ids={observe_project.id}"
        )

        assert response.status_code == 200
        metric_names = [m["name"] for m in response.json()["result"]["metrics"]]
        assert "agent_talk_percentage" not in metric_names

    @pytest.mark.django_db
    @patch("tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService")
    def test_metrics_hides_agent_talk_percentage_when_mixed_sources(
        self,
        mock_analytics_cls,
        auth_client,
        organization,
        workspace,
        observe_project,
    ):
        from model_hub.models.ai_model import AIModel
        from tracer.models.project import Project, ProjectSourceChoices

        simulator_project = Project.objects.create(
            name="Voice Project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            source=ProjectSourceChoices.SIMULATOR.value,
        )
        mock_analytics_cls.return_value.get_span_attribute_keys_ch_for_projects.return_value = []

        response = auth_client.get(
            "/tracer/dashboard/metrics/"
            f"?project_ids={simulator_project.id},{observe_project.id}"
        )

        assert response.status_code == 200
        metric_names = [m["name"] for m in response.json()["result"]["metrics"]]
        # Gate requires *every* queried project to be SIMULATOR — mixed scope
        # must hide the option so a non-voice project can't filter on it.
        assert "agent_talk_percentage" not in metric_names

    @pytest.mark.django_db
    def test_metrics_hides_agent_talk_percentage_without_explicit_project_ids(
        self, auth_client
    ):
        # Workspace-wide call (used by dashboard widget pickers) must not
        # expose the voice-only metric.
        response = auth_client.get("/tracer/dashboard/metrics/")
        assert response.status_code == 200
        metric_names = [m["name"] for m in response.json()["result"]["metrics"]]
        assert "agent_talk_percentage" not in metric_names

    @pytest.fixture
    def _annotation_label_factory(self, db, organization, workspace):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels

        def _make(name="Test Annotation Label"):
            return AnnotationsLabels.objects.create(
                name=name,
                type=AnnotationTypeChoices.NUMERIC.value,
                organization=organization,
                workspace=workspace,
                settings={
                    "min": 0,
                    "max": 10,
                    "step_size": 1,
                    "display_type": "slider",
                },
            )

        return _make

    @pytest.mark.django_db
    def test_metrics_returns_span_attached_annotation_label(
        self,
        auth_client,
        organization,
        observe_project,
        user,
        _annotation_label_factory,
    ):
        """Span-attached Score (trace=NULL) must surface its label in the metrics API."""
        from model_hub.models.score import Score
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.trace import Trace

        trace = Trace.objects.create(project=observe_project, name="Span-Anno Trace")
        span = ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:16]}",
            project=observe_project,
            trace=trace,
            name="Span With Annotation",
            observation_type="llm",
        )
        label = _annotation_label_factory(name="Span Attached Label")
        Score.objects.create(
            source_type="observation_span",
            observation_span=span,
            label=label,
            annotator=user,
            value={"value": 5.0},
            score_source="human",
            organization=organization,
        )

        response = _get_metrics_with_annotation_labels(
            auth_client,
            observe_project.id,
            [label.id],
        )
        assert response.status_code == 200
        metrics = response.json()["result"]["metrics"]
        annotation_ids = [
            m["name"] for m in metrics if m.get("category") == "annotation_metric"
        ]
        assert str(label.id) in annotation_ids, (
            "Span-attached annotation label was not returned — regression of TH-4914"
        )

    @pytest.mark.django_db
    def test_metrics_returns_trace_attached_annotation_label(
        self,
        auth_client,
        organization,
        observe_project,
        user,
        _annotation_label_factory,
    ):
        """Trace-attached Score path keeps working alongside the span branch."""
        from model_hub.models.score import Score
        from tracer.models.trace import Trace

        trace = Trace.objects.create(
            project=observe_project,
            name="Trace For Annotation",
        )
        label = _annotation_label_factory(name="Trace Attached Label")
        Score.objects.create(
            source_type="trace",
            trace=trace,
            label=label,
            annotator=user,
            value={"value": 7.0},
            score_source="human",
            organization=organization,
        )

        response = _get_metrics_with_annotation_labels(
            auth_client,
            observe_project.id,
            [label.id],
        )
        assert response.status_code == 200
        metrics = response.json()["result"]["metrics"]
        annotation_ids = [
            m["name"] for m in metrics if m.get("category") == "annotation_metric"
        ]
        assert str(label.id) in annotation_ids

    @pytest.mark.django_db
    @patch("tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService")
    def test_metrics_excludes_annotation_label_from_other_project(
        self,
        mock_analytics_cls,
        auth_client,
        organization,
        workspace,
        observe_project,
        user,
        _annotation_label_factory,
    ):
        """A label used only in a different project must not leak into this one."""
        mock_analytics_cls.return_value.get_span_attribute_keys_ch_for_projects.return_value = []
        from model_hub.models.ai_model import AIModel
        from model_hub.models.score import Score
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.project import Project
        from tracer.models.trace import Trace

        other_project = Project.objects.create(
            name="Other Project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        other_trace = Trace.objects.create(project=other_project, name="Other Trace")
        other_span = ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:16]}",
            project=other_project,
            trace=other_trace,
            name="Other Span",
            observation_type="llm",
        )
        label = _annotation_label_factory(name="Other Project Label")
        Score.objects.create(
            source_type="observation_span",
            observation_span=other_span,
            label=label,
            annotator=user,
            value={"value": 1.0},
            score_source="human",
            organization=organization,
        )

        response = auth_client.get(
            f"/tracer/dashboard/metrics/?project_ids={observe_project.id}"
        )
        assert response.status_code == 200
        annotation_ids = [
            m["name"]
            for m in response.json()["result"]["metrics"]
            if m.get("category") == "annotation_metric"
        ]
        assert str(label.id) not in annotation_ids

    # ------------------------------------------------------------------
    # /filter_values endpoint — name / span_name col_map coverage.
    # The handler whitelists allowed system metric column ids; "name"
    # and "span_name" were missing so the FE picker showed empty
    # suggestions for Trace Name / Span Name filters.
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("metric_name", ["name", "span_name", "service_name"])
    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_accepts_name_aliases(
        self,
        mock_analytics_cls,
        _mock_ch_enabled,
        metric_name,
        auth_client,
        observe_project,
    ):
        mock_result = MagicMock()
        mock_result.data = [{"val": "agent.handle_request"}, {"val": "chain.run"}]
        mock_analytics_cls.return_value.execute_ch_query.return_value = mock_result

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            f"?metric_name={metric_name}"
            "&metric_type=system_metric"
            f"&project_ids={observe_project.id}"
            "&source=traces"
        )
        assert response.status_code == 200
        values = response.json()["result"]["values"]
        labels = [v["label"] for v in values]
        assert labels == ["agent.handle_request", "chain.run"]

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_name_restricts_to_root_spans(
        self,
        mock_analytics_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        """`metric_name=name` (Trace Name) must scope to root spans.

        CH25 v2 spans write '' (not NULL) on the non-nullable parent_span_id
        for root spans, so the clause must match both forms or it returns 0 rows.
        """
        mock_result = MagicMock()
        mock_result.data = []
        mock_analytics_cls.return_value.execute_ch_query.return_value = mock_result

        auth_client.get(
            "/tracer/dashboard/filter_values/"
            f"?metric_name=name&metric_type=system_metric"
            f"&project_ids={observe_project.id}&source=traces"
        )

        sql_arg = mock_analytics_cls.return_value.execute_ch_query.call_args[0][0]
        assert (
            "(latest_parent_span_id IS NULL OR latest_parent_span_id = '')" in sql_arg
        )
        assert "argMax(tuple(parent_span_id), _version).1" in sql_arg

    @pytest.mark.parametrize("metric_name", ["span_name", "service_name"])
    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_span_name_does_not_restrict_to_root(
        self,
        mock_analytics_cls,
        _mock_ch_enabled,
        metric_name,
        auth_client,
        observe_project,
    ):
        """span_name / service_name should NOT add the root-span clause."""
        mock_result = MagicMock()
        mock_result.data = []
        mock_analytics_cls.return_value.execute_ch_query.return_value = mock_result

        auth_client.get(
            "/tracer/dashboard/filter_values/"
            f"?metric_name={metric_name}&metric_type=system_metric"
            f"&project_ids={observe_project.id}&source=traces"
        )

        sql_arg = mock_analytics_cls.return_value.execute_ch_query.call_args[0][0]
        assert "argMax(tuple(parent_span_id), _version).1" in sql_arg
        assert (
            "AND (latest_parent_span_id IS NULL OR latest_parent_span_id = '')"
            not in sql_arg
        )

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_service_name_uses_service_name_col(
        self,
        mock_analytics_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        """service_name must select the real `service_name` column (OTel
        service.name) — the same column _STRING_FILTER_COL filters on — not the
        span `name`/`trace_name`, else the picker offers unmatchable values."""
        mock_result = MagicMock()
        mock_result.data = []
        mock_analytics_cls.return_value.execute_ch_query.return_value = mock_result

        auth_client.get(
            "/tracer/dashboard/filter_values/"
            f"?metric_name=service_name&metric_type=system_metric"
            f"&project_ids={observe_project.id}&source=traces"
        )

        sql_arg = mock_analytics_cls.return_value.execute_ch_query.call_args[0][0]
        assert "argMax(tuple(service_name), _version).1 AS raw_value" in sql_arg
        assert "argMax(tuple(name), _version).1 AS raw_value" not in sql_arg
        assert "trace_name" not in sql_arg

    # ------------------------------------------------------------------
    # /filter_values — span-scan time bounds.
    # `spans` is PARTITION BY toDate(start_time); without a start_time bound
    # these DISTINCT scans read the project's whole history (measured: 19M
    # rows / 51 GiB on the largest tenant, tripping the endpoint timeout).
    # ------------------------------------------------------------------

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_custom_attribute_bounds_scan_by_default_lookback(
        self,
        mock_selector_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        mock_selector_cls.return_value.read_values.return_value = _attribute_value_read(
            ["checkout"]
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=prompt_slug&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 200
        assert response.json()["result"]["values"] == [
            {"value": "checkout", "type": "string", "label": "checkout"}
        ]
        mock_selector_cls.return_value.read_values.assert_called_once_with(
            [str(observe_project.id)],
            "prompt_slug",
            search="",
            max_values=500,
        )

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_custom_attribute_honors_search(
        self,
        mock_selector_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        """Search is passed as literal UTF-8 text with a tighter result cap."""
        mock_selector_cls.return_value.read_values.return_value = _attribute_value_read(
            ["agent_100%_done"]
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=prompt_slug&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
            "&search=100%25_d"  # url-encoded "100%_d" — % and _ are literals
        )

        assert response.status_code == 200
        mock_selector_cls.return_value.read_values.assert_called_once_with(
            [str(observe_project.id)],
            "prompt_slug",
            search="100%_d",
            max_values=20,
        )

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_search_of_ngram_size_scans_unbounded(
        self,
        mock_selector_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        """Long searches use the same bounded selector as every other search."""
        mock_selector_cls.return_value.read_values.return_value = _attribute_value_read(
            ["gpt-4o-mini"]
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=model_name&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
            "&search=gpt-"
        )

        assert response.status_code == 200
        mock_selector_cls.return_value.read_values.assert_called_once_with(
            [str(observe_project.id)],
            "model_name",
            search="gpt-",
            max_values=20,
        )

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_short_search_stays_windowed(
        self,
        mock_selector_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        mock_selector_cls.return_value.read_values.return_value = (
            _attribute_value_read()
        )

        auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=model_name&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
            "&search=gpt"
        )

        mock_selector_cls.return_value.read_values.assert_called_once_with(
            [str(observe_project.id)],
            "model_name",
            search="gpt",
            max_values=20,
        )

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_search_companion_lowercases_needle(
        self,
        mock_selector_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        """Case is preserved at the boundary; selector matching casefolds it."""
        mock_selector_cls.return_value.read_values.return_value = (
            _attribute_value_read()
        )

        auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=prompt_slug&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
            "&search=AgEnT"
        )

        assert (
            mock_selector_cls.return_value.read_values.call_args.kwargs["search"]
            == "AgEnT"
        )

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_span_scans_run_in_break_mode(
        self,
        mock_selector_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        """The endpoint uses the selector's throw-and-sanitize policy."""
        mock_selector_cls.return_value.read_values.return_value = _attribute_value_read(
            ["checkout"]
        )

        auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=prompt_slug&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
        )

        mock_selector_cls.return_value.read_values.assert_called_once()

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_empty_window_stays_a_single_bounded_call(
        self,
        mock_selector_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        mock_selector_cls.return_value.read_values.return_value = (
            _attribute_value_read()
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=prompt_slug&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 200
        assert response.json()["result"]["values"] == []
        mock_selector_cls.return_value.read_values.assert_called_once()

    @pytest.mark.parametrize(
        "values",
        [
            (),
            ("partial-value",),
        ],
        ids=["empty-budget", "partial-budget"],
    )
    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_custom_attribute_incomplete_read_is_sanitized_503(
        self,
        mock_selector_cls,
        values,
        auth_client,
        observe_project,
    ):
        mock_selector_cls.return_value.read_values.return_value = _attribute_value_read(
            values,
            complete=False,
            error_code="read_budget_exceeded",
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=prompt_slug&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 503
        payload = json.dumps(response.json())
        assert "temporarily unavailable" in payload
        assert response.json()["code"] == "service_unavailable"
        assert "partial-value" not in payload
        assert "read_budget_exceeded" not in payload

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_custom_attribute_empty_cap_is_labelled_sample(
        self,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        mock_selector_cls.return_value.read_values.return_value = _attribute_value_read(
            (),
            complete=False,
            error_code="sample_limit",
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=absent_heavy_key&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 200
        assert response.json()["result"] == {
            "values": [],
            "query_complete": False,
            "query_status": "sampled",
            "query_error_code": "sample_limit",
            "query_window_start": "2025-08-01T00:00:00Z",
            "query_window_end": "2026-08-01T00:00:00Z",
        }

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_custom_attribute_nonempty_cap_is_labelled_sample(
        self,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        mock_selector_cls.return_value.read_values.return_value = _attribute_value_read(
            ("verified-value",),
            complete=False,
            error_code="sample_limit",
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=prompt_slug&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 200
        payload = response.json()["result"]
        assert payload["values"] == [
            {
                "value": "verified-value",
                "type": "string",
                "label": "verified-value",
            }
        ]
        assert payload["query_complete"] is False
        assert payload["query_status"] == "sampled"
        assert payload["query_error_code"] == "sample_limit"

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_custom_attribute_cursor_pages_are_opaque_and_unique(
        self,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        now = datetime(2026, 8, 1, tzinfo=UTC)
        completed_digest = attribute_value_cursor_digest("string", "completed")
        failed_digest = attribute_value_cursor_digest("string", "failed")
        queued_digest = attribute_value_cursor_digest("string", "queued")
        first_before = (
            str(observe_project.id),
            "trace-first",
            "span-first",
            now - timedelta(minutes=1),
        )
        selector = mock_selector_cls.return_value
        retained_start = now - timedelta(days=400)
        selector.retained_window_start.return_value = retained_start
        selector.read_value_cursor_page.side_effect = [
            _attribute_value_cursor_page(
                ("completed", "failed"),
                has_more=True,
                next_before_identity=first_before,
                seen_value_digests=(completed_digest, failed_digest),
                next_segment_start=now - timedelta(minutes=5),
            ),
            _attribute_value_cursor_page(
                ("queued",),
                has_more=False,
                seen_value_digests=(
                    completed_digest,
                    failed_digest,
                    queued_digest,
                ),
            ),
        ]

        first = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {
                "metric_name": "call.status",
                "metric_type": "custom_attribute",
                "project_ids": str(observe_project.id),
                "source": "traces",
                "page_size": 2,
                "attribute_type": "string",
            },
        )

        assert first.status_code == 200
        first_payload = first.json()["result"]
        assert [value["value"] for value in first_payload["values"]] == [
            "completed",
            "failed",
        ]
        assert first_payload["has_more"] is True
        assert first_payload["browse_status"] == "continuation"
        assert isinstance(first_payload["next_cursor"], str)
        assert first_payload["attribute_type"] == "string"
        assert first_payload["query_complete"] is True
        assert first_payload["query_status"] == "complete"
        assert "query_error_code" not in first_payload
        selector_init = mock_selector_cls.call_args_list[0].kwargs
        assert set(selector_init) == {
            "typed_only",
            "json_attribute_mode",
            "wall_timeout_ms",
        }
        assert selector_init["typed_only"] is True
        assert selector_init["json_attribute_mode"] == "arrays"
        # Project authorization and cursor setup consume the same request-owned
        # wall. The selector therefore receives the positive *remaining*
        # budget rather than starting a new independent timeout.
        assert (
            0
            < selector_init["wall_timeout_ms"]
            <= ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS
        )
        first_kwargs = selector.read_value_cursor_page.call_args_list[0].kwargs
        assert first_kwargs["window_start"] == retained_start
        assert first_kwargs["continue_operation"] is True
        frozen_window_end = first_kwargs["window_end"]

        second = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {
                "metric_name": "call.status",
                "metric_type": "custom_attribute",
                "project_ids": str(observe_project.id),
                "source": "traces",
                "page_size": 2,
                "attribute_type": "string",
                "cursor": first_payload["next_cursor"],
            },
        )

        assert second.status_code == 200
        second_payload = second.json()["result"]
        assert [value["value"] for value in second_payload["values"]] == ["queued"]
        assert second_payload["has_more"] is False
        assert second_payload["browse_status"] == "exhausted"
        assert second_payload["next_cursor"] is None
        assert selector.read_value_cursor_page.call_count == 2
        second_kwargs = selector.read_value_cursor_page.call_args_list[1].kwargs
        assert second_kwargs["window_start"] == retained_start
        assert second_kwargs["continue_operation"] is False
        assert second_kwargs["window_end"] == frozen_window_end
        assert second_kwargs["before_identity"] == first_before
        assert second_kwargs["segment_start"] == now - timedelta(minutes=5)
        assert second_kwargs["attribute_type"] == "string"
        assert second_kwargs["seen_value_digests"] == (
            completed_digest,
            failed_digest,
        )

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_signed_cursor_roundtrips_after_tracking_prefix_is_full(
        self,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        now = datetime(2026, 8, 1, tzinfo=UTC)
        seen = tuple(
            attribute_value_cursor_digest("string", f"prior-{index}")
            for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS)
        )
        first_before = (
            str(observe_project.id),
            "trace-at-cap",
            "span-at-cap",
            now - timedelta(minutes=1),
        )
        second_before = (
            str(observe_project.id),
            "trace-after-cap",
            "span-after-cap",
            now - timedelta(minutes=2),
        )
        selector = mock_selector_cls.return_value
        selector.retained_window_start.return_value = now - timedelta(days=365)
        selector.read_value_cursor_page.side_effect = [
            _attribute_value_cursor_page(
                ("prior-4095",),
                has_more=True,
                next_before_identity=first_before,
                seen_value_digests=seen,
            ),
            _attribute_value_cursor_page(
                ("after-cap",),
                has_more=True,
                next_before_identity=second_before,
                seen_value_digests=seen,
            ),
            _attribute_value_cursor_page(
                (),
                has_more=False,
                seen_value_digests=seen,
            ),
        ]
        params = {
            "metric_name": "call.status",
            "metric_type": "custom_attribute",
            "project_ids": str(observe_project.id),
            "source": "traces",
            "page_size": 1,
            "attribute_type": "string",
        }

        first = auth_client.get("/tracer/dashboard/filter_values/", params)
        first_payload = first.json()["result"]
        second = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {**params, "cursor": first_payload["next_cursor"]},
        )
        second_payload = second.json()["result"]
        third = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {**params, "cursor": second_payload["next_cursor"]},
        )
        third_payload = third.json()["result"]

        assert first.status_code == second.status_code == third.status_code == 200
        assert second_payload["values"][0]["value"] == "after-cap"
        assert second_payload["browse_status"] == "continuation"
        assert second_payload["next_cursor"]
        assert second_payload["next_cursor"] != first_payload["next_cursor"]
        assert third_payload["values"] == []
        assert third_payload["browse_status"] == "exhausted"
        assert third_payload["next_cursor"] is None
        assert selector.read_value_cursor_page.call_count == 3
        assert (
            selector.read_value_cursor_page.call_args_list[1].kwargs[
                "seen_value_digests"
            ]
            == seen
        )
        assert (
            selector.read_value_cursor_page.call_args_list[1].kwargs["before_identity"]
            == first_before
        )
        assert (
            selector.read_value_cursor_page.call_args_list[2].kwargs[
                "seen_value_digests"
            ]
            == seen
        )
        assert (
            selector.read_value_cursor_page.call_args_list[2].kwargs["before_identity"]
            == second_before
        )

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_rejects_scan_slice_that_mismatches_order(
        self,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        now = datetime(2026, 8, 1, tzinfo=UTC)
        digest = attribute_value_cursor_digest("string", "Rechazado")
        selector = mock_selector_cls.return_value
        selector.retained_window_start.return_value = now - timedelta(days=365)
        selector.read_value_cursor_page.return_value = _attribute_value_cursor_page(
            ("Rechazado",),
            has_more=True,
            next_before_identity=(
                str(observe_project.id),
                "trace-rechazado",
                "span-rechazado",
                now - timedelta(minutes=1),
            ),
            seen_value_digests=(digest,),
            next_segment_start=now - timedelta(minutes=5),
        )
        params = {
            "metric_name": "final_status",
            "metric_type": "custom_attribute",
            "project_ids": str(observe_project.id),
            "source": "traces",
            "page_size": 10,
            "search": "Rechazado",
        }
        first = auth_client.get("/tracer/dashboard/filter_values/", params)
        assert first.status_code == 200
        token = first.json()["result"]["next_cursor"]
        payload = signing.loads(
            token,
            key=settings.SECRET_KEY,
            salt=CURSOR_SALT,
        )
        payload["scan_slice_end"] = {
            "$datetime": (now - timedelta(seconds=1)).isoformat()
        }
        malformed = signing.dumps(
            payload,
            key=settings.SECRET_KEY,
            salt=CURSOR_SALT,
            compress=True,
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {**params, "cursor": malformed},
        )

        assert response.status_code == 400
        assert response.json()["code"] == "invalid_cursor"
        assert selector.read_value_cursor_page.call_count == 1

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_exposes_truthful_browse_limit(
        self,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        selector = mock_selector_cls.return_value
        selector.retained_window_start.return_value = datetime(2025, 8, 1, tzinfo=UTC)
        selector.read_value_cursor_page.return_value = _attribute_value_cursor_page(
            (),
            has_more=False,
            browse_status="limit_reached",
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {
                "metric_name": "call.status",
                "metric_type": "custom_attribute",
                "project_ids": str(observe_project.id),
                "source": "traces",
                "page_size": 10,
            },
        )

        assert response.status_code == 200
        payload = response.json()["result"]
        assert payload["values"] == []
        assert payload["has_more"] is False
        assert payload["next_cursor"] is None
        assert payload["browse_status"] == "limit_reached"

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_cursor_is_bound_to_search_and_rejects_mismatch(
        self,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        now = datetime(2026, 8, 1, tzinfo=UTC)
        digest = attribute_value_cursor_digest("string", "completed")
        selector = mock_selector_cls.return_value
        selector.retained_window_start.return_value = now - timedelta(days=365)
        selector.read_value_cursor_page.return_value = _attribute_value_cursor_page(
            ("completed",),
            has_more=True,
            next_before_identity=(
                str(observe_project.id),
                "trace-first",
                "span-first",
                now - timedelta(minutes=1),
            ),
            seen_value_digests=(digest,),
        )
        base = {
            "metric_name": "call.status",
            "metric_type": "custom_attribute",
            "project_ids": str(observe_project.id),
            "source": "traces",
            "page_size": 10,
        }
        first = auth_client.get("/tracer/dashboard/filter_values/", base)
        token = first.json()["result"]["next_cursor"]

        mismatched = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {**base, "search": "failed", "cursor": token},
        )

        assert mismatched.status_code == 400
        assert mismatched.json()["code"] == "cursor_mismatch"
        assert selector.read_value_cursor_page.call_count == 1

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_rejects_malformed_cursor_without_clickhouse(
        self,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        response = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {
                "metric_name": "call.status",
                "metric_type": "custom_attribute",
                "project_ids": str(observe_project.id),
                "source": "traces",
                "page_size": 10,
                "cursor": "not-a-signed-cursor",
            },
        )

        assert response.status_code == 400
        assert response.json()["code"] == "invalid_cursor"
        mock_selector_cls.return_value.read_value_cursor_page.assert_not_called()

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_legacy_request_does_not_enter_cursor_mode(
        self,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        mock_selector_cls.return_value.read_values.return_value = _attribute_value_read(
            ("completed",)
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=call.status&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 200
        mock_selector_cls.return_value.read_values.assert_called_once()
        mock_selector_cls.return_value.read_value_cursor_page.assert_not_called()

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_custom_attribute_preserves_json_array_type(
        self,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        mock_selector_cls.return_value.read_values.return_value = _attribute_value_read(
            ("Rechazado",), attribute_type="array"
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=final_status&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 200
        assert response.json()["result"]["values"] == [
            {
                "value": "Rechazado",
                "type": "array",
                "label": "Rechazado",
            }
        ]

    def test_filter_values_response_serializer_accepts_legacy_options_without_type(
        self,
    ):
        serializer = DashboardFilterValuesResponseSerializer(
            data={
                "status": True,
                "result": {
                    "values": [{"value": "legacy", "label": "legacy"}],
                },
            }
        )

        assert serializer.is_valid(), serializer.errors

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_custom_attribute_query_defect_returns_sanitized_500(
        self,
        mock_selector_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        """Query defects are server failures, never an empty picker or raw SQL."""
        mock_selector_cls.return_value.read_values.side_effect = ServerException(
            "private ClickHouse query detail", code=47
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=prompt_slug&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 500
        payload = json.dumps(response.json())
        assert "could not be loaded" in payload
        assert response.json()["code"] == "server_error"
        assert "private ClickHouse" not in payload

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_custom_attribute_programming_error_returns_sanitized_500(
        self,
        mock_selector_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        """Programming defects are sanitized server errors, not bad requests."""
        mock_selector_cls.return_value.read_values.side_effect = RuntimeError(
            "private attribute compiler invariant"
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=prompt_slug&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 500
        payload = json.dumps(response.json())
        assert "could not be loaded" in payload
        assert response.json()["code"] == "server_error"
        assert "compiler invariant" not in payload

    @pytest.mark.parametrize("code", [241, 386], ids=["memory", "heterogeneous"])
    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_custom_attribute_resource_failure_is_sanitized_503(
        self,
        mock_selector_cls,
        _mock_ch_enabled,
        code,
        auth_client,
        observe_project,
    ):
        mock_selector_cls.return_value.read_values.side_effect = ServerException(
            "private attribute memory failure and SQL",
            code=code,
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=prompt_slug&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 503
        payload = json.dumps(response.json())
        assert "temporarily unavailable" in payload
        assert response.json()["code"] == "service_unavailable"
        assert "private attribute" not in payload

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_serializer_validation_remains_sanitized_400(
        self,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        response = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {
                "metric_name": "prompt_slug",
                "metric_type": "custom_attribute",
                "project_ids": str(observe_project.id),
                "source": "traces",
                "search": "x" * 513,
            },
        )

        assert response.status_code == 400
        assert "search" in json.dumps(response.json()).lower()
        assert "traceback" not in json.dumps(response.json()).lower()
        mock_selector_cls.assert_not_called()

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AttributeReadSelector")
    def test_filter_values_ignores_caller_supplied_window(
        self,
        mock_selector_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        """A caller-sent range does not override selector-owned windows."""
        mock_selector_cls.return_value.read_values.return_value = _attribute_value_read(
            ["checkout"]
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=prompt_slug&metric_type=custom_attribute"
            f"&project_ids={observe_project.id}&source=traces"
            "&start_time=2020-01-01T00:00:00Z&end_time=2030-01-01T00:00:00Z"
        )

        assert response.status_code == 200
        kwargs = mock_selector_cls.return_value.read_values.call_args.kwargs
        assert "window_start" not in kwargs
        assert "window_end" not in kwargs

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_system_metric_bounds_scan(
        self,
        mock_analytics_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        mock_result = MagicMock()
        mock_result.data = [{"val": "gpt-4o"}]
        mock_analytics_cls.return_value.execute_ch_query.return_value = mock_result

        auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=model&metric_type=system_metric"
            f"&project_ids={observe_project.id}&source=traces"
        )

        call = mock_analytics_cls.return_value.execute_ch_query.call_args
        sql_arg, params = call.args[:2]
        assert "start_time >= %(window_start)s" in sql_arg
        assert "start_time < %(window_end)s" in sql_arg
        assert params["window_end"] - params["window_start"] == timedelta(days=7)
        assert "argMax(is_deleted, _version) AS latest_is_deleted" in sql_arg
        assert "WHERE latest_is_deleted = 0" in sql_arg
        assert call.kwargs["settings"]["timeout_overflow_mode"] == "throw"
        assert call.kwargs["settings"]["read_overflow_mode"] == "throw"

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AttributeReadSelector")
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_system_metric_cursor_uses_retained_window_without_sampling(
        self,
        mock_analytics_cls,
        mock_selector_cls,
        auth_client,
        observe_project,
    ):
        retained_start = datetime(2024, 1, 1, tzinfo=UTC)
        mock_selector_cls.return_value.retained_window_start.return_value = (
            retained_start
        )
        mock_analytics_cls.return_value.execute_ch_query.return_value = MagicMock(
            data=[{"val": "gpt-4o"}, {"val": "gpt-5"}]
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {
                "metric_name": "model",
                "metric_type": "system_metric",
                "project_ids": str(observe_project.id),
                "source": "traces",
                "page_size": 1,
            },
        )

        assert response.status_code == 200
        payload = response.json()["result"]
        assert payload["values"] == [{"value": "gpt-4o", "label": "gpt-4o"}]
        assert payload["query_complete"] is True
        assert payload["query_status"] == "complete"
        assert payload["has_more"] is True
        assert payload["browse_status"] == "continuation"
        assert isinstance(payload["next_cursor"], str)
        assert payload["query_window_start"] == "2024-01-01T00:00:00+00:00"
        call = mock_analytics_cls.return_value.execute_ch_query.call_args
        assert (
            call.args[1]["window_end"] - call.args[1]["window_start"]
            == FILTER_VALUE_CURSOR_INITIAL_SEGMENT
        )
        assert "LIMIT %(result_limit)s" in call.args[0]

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_system_metric_roundtrips_radix_state_past_4096(
        self,
        mock_analytics_cls,
        auth_client,
        observe_project,
    ):
        scope = {"test_principal": "system-radix-roundtrip"}
        page_size = 1
        window_end = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        window_start = window_end - timedelta(minutes=5)
        cursor_query = {
            "metric_name": "status",
            "metric_type": "system_metric",
            "source": "traces",
            "project_ids": [str(observe_project.id)],
            "search": "",
        }
        state_binding = {
            "scope": scope,
            "query": cursor_query,
            "page_size": page_size,
            "window_start": window_start,
            "window_end": window_end,
        }
        legacy_digests = (
            _value_digest("completed"),
            *(
                hashlib.md5(  # noqa: S324 - deterministic non-security test digest
                    f"legacy-system-{index}".encode(),
                    usedforsecurity=False,
                ).hexdigest()
                for index in range(ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS)
            ),
        )
        seen_reference = persist_attribute_cursor_seen_state(
            AttributeCursorSeenState((), None),
            legacy_digests,
            resource="dashboard_system_filter_values",
            binding=state_binding,
            validate_digest=lambda value: (
                len(value) == 32 and all(char in "0123456789abcdef" for char in value)
            ),
        )
        cursor = encode_list_cursor(
            resource="dashboard_system_filter_values",
            scope=scope,
            query=cursor_query,
            page_size=page_size,
            window_start=window_start,
            window_end=window_end,
            order=(window_end, window_start, "", seen_reference),
            seen_rows=ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 1,
        )
        mock_analytics_cls.return_value.execute_ch_query.return_value = MagicMock(
            data=[{"val": "completed"}, {"val": "new-status"}]
        )

        with patch(
            "tracer.views.dashboard.cursor_scope_for_request",
            return_value=scope,
        ):
            response = auth_client.get(
                "/tracer/dashboard/filter_values/",
                {
                    "metric_name": "status",
                    "metric_type": "system_metric",
                    "project_ids": str(observe_project.id),
                    "source": "traces",
                    "page_size": page_size,
                    "cursor": cursor,
                },
            )

        assert response.status_code == 200
        payload = response.json()["result"]
        assert payload["values"] == [{"value": "new-status", "label": "new-status"}]
        assert payload["has_more"] is True
        continued = decode_list_cursor(
            payload["next_cursor"],
            resource="dashboard_system_filter_values",
            scope=scope,
            query=cursor_query,
            page_size=page_size,
        )
        assert continued.seen_rows == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 2
        continued_state = load_attribute_cursor_seen_state(
            continued.order[3],
            resource="dashboard_system_filter_values",
            binding=state_binding,
            validate_digest=lambda value: (
                len(value) == 32 and all(char in "0123456789abcdef" for char in value)
            ),
        )
        assert continued_state.seen_count == ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + 2
        assert continued_state.contains(_value_digest("completed")) is True
        assert continued_state.contains(_value_digest("new-status")) is True

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_end_user_cursor_reaches_values_after_first_page(
        self,
        mock_analytics_cls,
        auth_client,
        observe_project,
    ):
        analytics = mock_analytics_cls.return_value
        analytics.execute_ch_query.side_effect = [
            MagicMock(data=[{"val": "alice"}, {"val": "bob"}]),
            MagicMock(data=[{"val": "bob"}]),
        ]
        params = {
            "metric_name": "user_id",
            "metric_type": "system_metric",
            "project_ids": str(observe_project.id),
            "source": "traces",
            "page_size": 1,
        }

        first = auth_client.get("/tracer/dashboard/filter_values/", params)
        first_payload = first.json()["result"]
        second = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {**params, "cursor": first_payload["next_cursor"]},
        )

        assert first.status_code == second.status_code == 200
        assert first_payload["values"] == [{"value": "alice", "label": "alice"}]
        assert first_payload["has_more"] is True
        assert second.json()["result"]["values"] == [{"value": "bob", "label": "bob"}]
        assert second.json()["result"]["has_more"] is False
        first_sql = analytics.execute_ch_query.call_args_list[0].args[0]
        second_params = analytics.execute_ch_query.call_args_list[1].args[1]
        assert "FROM end_users" in first_sql
        assert "FINAL" not in first_sql
        assert second_params["value_after"] == "alice"

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=False)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_uses_direct_write_service_when_legacy_client_is_disabled(
        self,
        mock_analytics_cls,
        mock_legacy_enabled,
        auth_client,
        observe_project,
    ):
        mock_analytics_cls.return_value.execute_ch_query.return_value = MagicMock(
            data=[{"val": "gpt-4o"}]
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=model&metric_type=system_metric"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 200
        assert response.json()["result"]["values"] == [
            {"value": "gpt-4o", "label": "gpt-4o"}
        ]
        mock_analytics_cls.assert_called_once_with()
        mock_legacy_enabled.assert_not_called()

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_end_user_dimension_failure_is_sanitized_503(
        self,
        mock_analytics_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        mock_analytics_cls.return_value.execute_ch_query.side_effect = NetworkError(
            "private end-users ClickHouse host"
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=user_id&metric_type=system_metric"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 503
        payload = json.dumps(response.json())
        assert "temporarily unavailable" in payload
        assert response.json()["code"] == "service_unavailable"
        assert "private end-users" not in payload

    @pytest.mark.parametrize(
        "exc",
        [
            ServerException("private timeout query and stack", code=159),
            ServerException("private memory query and stack", code=241),
            ServerException("private byte-limit query and stack", code=307),
            ServerException("private heterogeneous query and stack", code=386),
            NetworkError("private dashboard ClickHouse host"),
        ],
        ids=["code-159", "code-241", "code-307", "code-386", "network"],
    )
    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_system_metric_unavailable_is_sanitized_503(
        self,
        mock_analytics_cls,
        _mock_ch_enabled,
        exc,
        auth_client,
        observe_project,
    ):
        mock_analytics_cls.return_value.execute_ch_query.side_effect = exc

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=model&metric_type=system_metric"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 503
        payload = json.dumps(response.json())
        assert "temporarily unavailable" in payload
        assert response.json()["code"] == "service_unavailable"
        assert "private" not in payload
        settings = mock_analytics_cls.return_value.execute_ch_query.call_args.kwargs[
            "settings"
        ]
        assert settings["timeout_overflow_mode"] == "throw"
        assert settings["read_overflow_mode"] == "throw"

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_system_metric_cardinality_cap_is_explicit_sample(
        self,
        mock_analytics_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        mock_analytics_cls.return_value.execute_ch_query.return_value = MagicMock(
            data=[{"val": f"model-{index:03d}"} for index in range(501)]
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=model&metric_type=system_metric"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 200
        payload = response.json()["result"]
        assert len(payload["values"]) == 500
        assert payload["query_complete"] is False
        assert payload["query_status"] == "sampled"
        assert payload["query_error_code"] == "sample_limit"

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_tag_uses_direct_write_tags_json(
        self,
        mock_analytics_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        mock_analytics_cls.return_value.execute_ch_query.return_value = MagicMock(
            data=[{"val": "production"}]
        )

        response = auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=tag&metric_type=system_metric"
            f"&project_ids={observe_project.id}&source=traces"
        )

        assert response.status_code == 200
        sql_arg = mock_analytics_cls.return_value.execute_ch_query.call_args.args[0]
        assert "argMax(tuple(tags), _version).1 AS raw_value" in sql_arg
        assert (
            "arrayJoin(JSONExtract(latest_spans.raw_value, 'Array(String)'))" in sql_arg
        )
        assert "trace_tags" not in sql_arg

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_filter_values_session_bounds_scan_on_aliased_column(
        self,
        mock_analytics_cls,
        _mock_ch_enabled,
        auth_client,
        observe_project,
    ):
        """Session values collapse latest state before the remap join."""
        mock_result = MagicMock()
        mock_result.data = [{"val": str(uuid.uuid4())}]
        mock_analytics_cls.return_value.execute_ch_query.return_value = mock_result

        auth_client.get(
            "/tracer/dashboard/filter_values/"
            "?metric_name=session&metric_type=system_metric"
            f"&project_ids={observe_project.id}&source=traces"
        )

        sql_arg = mock_analytics_cls.return_value.execute_ch_query.call_args[0][0]
        assert "start_time >= %(window_start)s" in sql_arg
        assert "argMax(tuple(trace_session_id), _version).1 AS raw_value" in sql_arg
        assert "trace_session_id_remap" in sql_arg


class TestChartsView:
    @pytest.mark.django_db
    def test_generated_chart_crud_routes_return_method_guards(self, auth_client):
        chart_id = uuid.uuid4()
        payload = {
            "project_id": str(uuid.uuid4()),
            "interval": "day",
            "property": "average",
            "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
        }
        calls = [
            auth_client.get("/tracer/charts/"),
            auth_client.post("/tracer/charts/", payload, format="json"),
            auth_client.get(f"/tracer/charts/{chart_id}/"),
            auth_client.put(f"/tracer/charts/{chart_id}/", payload, format="json"),
            auth_client.patch(
                f"/tracer/charts/{chart_id}/", {"property": "p95"}, format="json"
            ),
            auth_client.delete(f"/tracer/charts/{chart_id}/"),
        ]

        for response in calls:
            assert response.status_code == 405
            assert "fetch_graph" in response.json()["detail"]

    @pytest.mark.django_db
    @patch("tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService")
    def test_fetch_eval_graph_rejects_config_from_another_project_before_ch(
        self,
        mock_analytics_cls,
        auth_client,
        observe_project,
        custom_eval_config,
    ):
        """A raw choice-eval read must not rely on the logger for project scope."""

        template = custom_eval_config.eval_template
        template.config = {"output": "CHOICES"}
        template.choices = ["foreign-choice"]
        template.save(update_fields=["config", "choices"])

        query = urlencode(
            {
                "project_id": str(observe_project.id),
                "interval": "day",
                "property": "average",
                "req_data_config": json.dumps(
                    {"id": str(custom_eval_config.id), "type": "EVAL"}
                ),
            }
        )

        response = auth_client.get(f"/tracer/charts/fetch_graph/?{query}")

        assert response.status_code == 400
        assert "Evaluation config is not available for this project" in str(
            response.json()
        )
        mock_analytics_cls.assert_not_called()

    @pytest.mark.django_db
    @patch("tracer.views.charts.get_eval_graph_data")
    def test_fetch_eval_graph_does_not_fall_back_or_expose_ch_error(
        self,
        mock_eval_graph,
        auth_client,
        observe_project,
        custom_eval_config,
    ):
        custom_eval_config.project = observe_project
        custom_eval_config.save(update_fields=["project"])
        custom_eval_config.eval_template.config = {"output": "Pass/Fail"}
        custom_eval_config.eval_template.save(update_fields=["config"])
        mock_eval_graph.side_effect = EvalGraphReadError(
            "secret ClickHouse host and stack"
        )

        query = urlencode(
            {
                "project_id": str(observe_project.id),
                "interval": "day",
                "property": "average",
                "req_data_config": json.dumps(
                    {"id": str(custom_eval_config.id), "type": "EVAL"}
                ),
            }
        )

        with patch(
            "tracer.utils.graphs_optimized.ObservationSpan.objects.filter"
        ) as pg_filter:
            response = auth_client.get(f"/tracer/charts/fetch_graph/?{query}")

        assert response.status_code == 503
        payload = str(response.json())
        assert "temporarily unavailable" in payload
        assert "secret ClickHouse host" not in payload
        mock_eval_graph.assert_called_once()
        pg_filter.assert_not_called()

    @pytest.mark.django_db
    @patch("tracer.views.charts.get_system_metric_data")
    def test_fetch_graph_supports_single_system_metric(
        self, mock_system_metric_data, auth_client, observe_project
    ):
        mock_system_metric_data.return_value = {
            "metric_name": "latency",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

        query = urlencode(
            {
                "project_id": str(observe_project.id),
                "interval": "day",
                "property": "average",
                "req_data_config": json.dumps(
                    {"id": "latency", "type": "SYSTEM_METRIC"}
                ),
            }
        )

        response = auth_client.get(f"/tracer/charts/fetch_graph/?{query}")

        assert response.status_code == 200
        assert response.json()["result"]["metric_name"] == "latency"
        mock_system_metric_data.assert_called_once()
        assert mock_system_metric_data.call_args.kwargs["system_metric_filters"] == {
            "project_id": str(observe_project.id)
        }

    @pytest.mark.django_db
    @patch("tracer.views.charts.get_system_metric_data")
    def test_fetch_graph_rejects_sampled_payload_even_with_legacy_opt_in(
        self, mock_system_metric_data, auth_client, observe_project
    ):
        mock_system_metric_data.return_value = {
            "metric_name": "latency",
            "data": [{"timestamp": "2026-08-03T00:00:00Z", "value": 12}],
            "query_complete": False,
            "query_status": "sampled",
            "query_error_code": "sample_limit",
            "query_sampling_strategy": "time_stratified_latest_state",
            "query_sampling_strata": 8,
            "query_sampling_strata_completed": 8,
        }
        params = {
            "project_id": str(observe_project.id),
            "interval": "day",
            "property": "average",
            "req_data_config": json.dumps({"id": "latency", "type": "SYSTEM_METRIC"}),
        }

        legacy_response = auth_client.get(
            f"/tracer/charts/fetch_graph/?{urlencode(params)}"
        )
        opted_in_response = auth_client.get(
            f"/tracer/charts/fetch_graph/?{urlencode({**params, 'allow_sampled': 'true'})}"
        )

        assert legacy_response.status_code == 503
        assert opted_in_response.status_code == 503
        assert "temporarily unavailable" in str(opted_in_response.json())

    @pytest.mark.django_db
    @pytest.mark.django_db
    @patch("tracer.views.charts.get_system_metric_data")
    def test_fetch_graph_rejects_same_org_other_workspace_project(
        self, mock_system_metric_data, auth_client, organization, user
    ):
        other_workspace = Workspace.objects.create(
            name="Other workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        other_project = Project.objects.create(
            name="Other workspace observe project",
            organization=organization,
            workspace=other_workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            metadata={},
        )

        query = urlencode(
            {
                "project_id": str(other_project.id),
                "interval": "day",
                "property": "average",
                "req_data_config": json.dumps(
                    {"id": "latency", "type": "SYSTEM_METRIC"}
                ),
            }
        )

        response = auth_client.get(f"/tracer/charts/fetch_graph/?{query}")

        assert response.status_code == 400
        assert "Project does not exist" in str(response.json())
        mock_system_metric_data.assert_not_called()


# ===========================================================================
# DashboardQueryBuilder
# ===========================================================================


class TestDashboardQueryBuilder:
    def test_system_metric_query(self, sample_query_config):
        builder = DashboardQueryBuilder(sample_query_config)
        queries = builder.build_all_queries()
        assert len(queries) == 1
        sql, params, metric_info = queries[0]
        assert "latency_ms" in sql
        assert "avg" in sql.lower()
        assert "toStartOfDay" in sql
        assert params["project_ids"] == sample_query_config["project_ids"]

    def test_system_metric_query_prunes_partitions(self, sample_query_config):
        """Spans-based queries must bound created_at (the partition key) so a
        windowed query prunes old partitions instead of scanning all history."""
        builder = DashboardQueryBuilder(sample_query_config)
        sql, _, _ = builder.build_all_queries()[0]
        # partition-prune bound on the partition key is present
        assert "created_at >= %(start_date)s - INTERVAL 1 DAY" in sql
        # and the precise event-time window is still enforced (correctness)
        assert "start_time >= %(start_date)s" in sql
        assert "start_time < %(end_date)s" in sql

    def test_v2_root_latency_uses_ch25_start_time_partition_and_projection_shape(
        self, sample_query_config
    ):
        """CH25 must not retain the legacy created_at partition hint.

        The v2 table is partitioned by start_time and proj_root_spans does not
        project created_at. Keeping that redundant predicate forces this common
        root-latency metric back to the base table.
        """
        builder = DashboardQueryBuilderV2(sample_query_config)
        sql, _, _ = builder.build_all_queries()[0]

        assert "created_at >=" not in sql
        assert "FROM spans FINAL" in sql
        assert "FROM spans FINAL" in without_query_settings(sql)
        assert "start_time >= %(start_date)s" in sql
        assert "start_time < %(end_date)s" in sql
        assert "project_id IN %(project_ids)s" in sql
        assert "is_deleted = 0" in sql
        assert "(parent_span_id IS NULL OR parent_span_id = '')" in sql
        assert "avg(latency_ms) AS value" in sql
        assert "optimize_use_projections = 1" in sql
        assert "optimize_aggregation_in_order = 1" in sql

    def test_v2_raw_attribute_dashboard_paths_use_ch25_start_time_partition(
        self, sample_query_config, settings
    ):
        """Custom metrics and raw attribute breakdowns share the v2 bound."""
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False

        custom_metric_config = {
            **sample_query_config,
            "metrics": [
                {
                    "id": "final_status",
                    "name": "final_status",
                    "type": "custom_attribute",
                    "attribute_key": "final_status",
                    "attribute_type": "string",
                    "aggregation": "count_distinct",
                }
            ],
        }
        custom_sql, custom_params, custom_info = DashboardQueryBuilderV2(
            custom_metric_config
        ).build_all_queries()[0]

        breakdown_config = {
            **sample_query_config,
            "breakdowns": [
                {
                    "type": "custom_attribute",
                    "name": "final_status",
                    "source": "traces",
                    "attribute_type": "string",
                }
            ],
        }
        breakdown_sql, breakdown_params, breakdown_info = DashboardQueryBuilderV2(
            breakdown_config
        ).build_all_queries()[0]

        for sql, params, metric_info in (
            (custom_sql, custom_params, custom_info),
            (breakdown_sql, breakdown_params, breakdown_info),
        ):
            assert "created_at >=" not in sql
            assert "start_time >= %(start_date)s" in sql
            assert "start_time < %(end_date)s" in sql
            assert "project_id IN %(project_ids)s" in sql
            assert "is_deleted = 0" in sql
            assert "mapContains(attrs_string" in sql
            assert "_raw_attr_candidate_limit" not in sql
            assert not any(key.startswith("_raw_attr_slice_") for key in params)
            assert "query_status" not in metric_info

        assert "uniqExact(attrs_string" in custom_sql
        assert "parent_span_id" not in custom_sql
        assert "(parent_span_id IS NULL OR parent_span_id = '')" in breakdown_sql

    def test_obsolete_raw_attribute_sampling_is_absent_from_both_builders(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        custom_metric = {
            "id": "final_status",
            "name": "final_status",
            "type": "custom_attribute",
            "attribute_key": "final_status",
            "attribute_type": "string",
            "aggregation": "count_distinct",
        }

        v1_sql, _, v1_info = DashboardQueryBuilder(
            {**sample_query_config, "metrics": [custom_metric]}
        ).build_all_queries()[0]
        system_sql, _, system_info = DashboardQueryBuilderV2(
            sample_query_config
        ).build_all_queries()[0]

        assert "_raw_attr_candidate_limit" not in v1_sql
        assert "query_status" not in v1_info
        assert "_raw_attr_candidate_limit" not in system_sql
        assert "query_status" not in system_info

    def test_raw_attribute_exact_read_is_the_strict_default(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        config = {
            **sample_query_config,
            "allow_sampled": False,
            "metrics": [
                {
                    "id": "final_status",
                    "name": "final_status",
                    "type": "custom_attribute",
                    "attribute_key": "final_status",
                    "attribute_type": "string",
                    "aggregation": "count_distinct",
                }
            ],
        }

        builder = DashboardQueryBuilderV2(config)
        metric_info = builder.metric_info(config["metrics"][0])
        sql, params = builder.build_metric_query(config["metrics"][0])

        assert "query_complete" not in metric_info
        assert "query_status" not in metric_info
        assert "query_error_code" not in metric_info
        assert "FROM spans" in sql
        assert "mapContains(attrs_string, %(custom_metric_attr_key)s)" in sql
        assert params["custom_metric_attr_key"] == "final_status"
        assert "_raw_attr_candidate_limit" not in sql
        assert not any(key.startswith("_raw_attr_slice_") for key in params)

    def test_strict_raw_attribute_breakdown_and_filter_are_exact(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        canonical_filter = {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rechazado",
            },
        }
        internal_filter = {
            "metric_type": "custom_attribute",
            "metric_name": "final_status",
            "operator": "equal_to",
            "value": "Rechazado",
            "attribute_type": "string",
            "canonical_filter": canonical_filter,
        }
        config = {
            **sample_query_config,
            "allow_sampled": False,
            "filters": [internal_filter],
            "breakdowns": [
                {
                    "type": "custom_attribute",
                    "name": "country",
                    "source": "traces",
                    "attribute_type": "string",
                }
            ],
        }

        sql, params, metric_info = DashboardQueryBuilderV2(config).build_all_queries()[
            0
        ]

        assert "attrs_string[%(_custom_bd_key_0)s] AS breakdown_value" in sql
        assert "lowerUTF8(toString(attrs_string[%(latest_filter_key_0)s]))" in sql
        assert "mapContains(attrs_string, %(_custom_bd_key_0)s)" in sql
        assert params["_custom_bd_key_0"] == "country"
        assert "rechazado" in params.values()
        assert "_raw_attr_candidate_limit" not in sql
        assert not any(key.startswith("_raw_attr_slice_") for key in params)
        assert "query_status" not in metric_info

    def test_raw_attribute_exact_reads_cover_legacy_metric_and_scalar_filter_gaps(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        canonical_filter = {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rechazado",
            },
        }
        internal_filter = {
            "metric_type": "custom_attribute",
            "metric_name": "final_status",
            "operator": "equal_to",
            "value": "Rechazado",
            "attribute_type": "string",
            "canonical_filter": canonical_filter,
        }

        unknown_sql, unknown_params, unknown_info = DashboardQueryBuilderV2(
            {
                **sample_query_config,
                "metrics": [
                    {
                        "name": "legacy_numeric_attribute",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
            }
        ).build_all_queries()[0]
        filtered_sql, filtered_params, filtered_info = DashboardQueryBuilderV2(
            {**sample_query_config, "filters": [internal_filter]}
        ).build_all_queries()[0]

        for sql, params, info in (
            (unknown_sql, unknown_params, unknown_info),
            (filtered_sql, filtered_params, filtered_info),
        ):
            assert "_raw_attr_candidate_limit" not in sql
            assert not any(key.startswith("_raw_attr_slice_") for key in params)
            assert "query_status" not in info
        assert "latest_custom_metric_spans AS" in unknown_sql
        assert "FROM spans FINAL" not in unknown_sql
        assert "argMax(" in unknown_sql
        assert "tupleElement(latest_metric_state, 1) = 0" in unknown_sql
        assert "tupleElement(latest_metric_state, 3) = 1" in unknown_sql
        assert "dashboard_filter_candidate_identities AS" in filtered_sql
        assert "FROM spans FINAL" not in filtered_sql
        assert "LIMIT 1 BY" in filtered_sql
        assert "dashboard_replay_source._version DESC" in filtered_sql
        assert "tuple(" in filtered_sql
        assert "IN (" in filtered_sql
        assert "attrs_number" in unknown_sql
        assert "legacy_numeric_attribute" in unknown_params.values()
        assert "attrs_string" in filtered_sql
        assert "Rechazado" not in filtered_sql
        assert "rechazado" in filtered_params.values()
        assert any(
            key.startswith("dashboard_candidate_latest_filter_")
            for key in filtered_params
        )

    def test_numeric_custom_metric_seeds_with_the_typed_key_bloom_index(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        metric = {
            "id": "call.total_turns",
            "name": "call.total_turns",
            "type": "custom_attribute",
            "attribute_key": "call.total_turns",
            "attribute_type": "number",
            "aggregation": "avg",
        }

        sql, params, _metric_info = DashboardQueryBuilderV2(
            {**sample_query_config, "metrics": [metric]}
        ).build_all_queries()[0]
        candidate_sql, replay_and_live_sql = sql.split(
            "), latest_custom_metric_spans AS (", 1
        )

        assert "custom_metric_candidate_identities AS" in candidate_sql
        assert "indexHint(has(mapKeys(" in candidate_sql
        assert "custom_metric_candidate_source.attrs_number" in candidate_sql
        assert "%(custom_metric_attr_key)s" in candidate_sql
        assert "mapContains(" in candidate_sql
        assert "GROUP BY" in candidate_sql
        compact_candidate_sql = " ".join(candidate_sql.split())
        assert (
            "custom_metric_candidate_source.observation_type AS observation_type"
            in compact_candidate_sql
        )
        assert (
            "custom_metric_candidate_source.service_name AS service_name"
            in compact_candidate_sql
        )
        assert (
            "toStartOfHour( custom_metric_candidate_source.start_time ) "
            "AS identity_hour"
        ) in compact_candidate_sql
        assert "start_time AS start_time" not in compact_candidate_sql
        assert params["custom_metric_attr_key"] == "call.total_turns"
        # The hint is a candidate-seed optimization, never a mutable predicate
        # on the latest-version replay itself.
        assert "indexHint(" not in replay_and_live_sql

    def test_numeric_custom_metric_replays_key_removals_and_tombstones(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        metric = {
            "id": "call.total_turns",
            "name": "call.total_turns",
            "type": "custom_attribute",
            "attribute_key": "call.total_turns",
            "attribute_type": "number",
            "aggregation": "avg",
        }

        sql, _params, _metric_info = DashboardQueryBuilderV2(
            {**sample_query_config, "metrics": [metric]}
        ).build_all_queries()[0]
        _candidate_sql, replay_and_live_sql = sql.split(
            "), latest_custom_metric_spans AS (", 1
        )
        replay_sql, live_sql = replay_and_live_sql.split(
            "), live_custom_metric_spans AS (", 1
        )
        compact_replay_sql = " ".join(replay_sql.split())

        assert "INNER JOIN custom_metric_candidate_identities" in compact_replay_sql
        assert (
            "custom_metric_candidate.project_id = custom_metric_source.project_id"
        ) in compact_replay_sql
        assert (
            "custom_metric_candidate.observation_type "
            "= custom_metric_source.observation_type"
        ) in compact_replay_sql
        assert (
            "custom_metric_candidate.service_name = custom_metric_source.service_name"
        ) in compact_replay_sql
        assert (
            "custom_metric_candidate.identity_hour "
            "= toStartOfHour(custom_metric_source.start_time)"
        ) in compact_replay_sql
        assert (
            "custom_metric_candidate.trace_id = custom_metric_source.trace_id"
        ) in compact_replay_sql
        assert (
            "custom_metric_candidate.id = custom_metric_source.id"
        ) in compact_replay_sql
        assert "custom_metric_candidate.start_time" not in compact_replay_sql
        assert (
            ">= toStartOfHour(toDateTime64( %(start_date)s, 6, 'UTC' ))"
            in compact_replay_sql
        )
        assert (
            "< toStartOfHour(toDateTime64( %(end_date)s, 6, 'UTC' )) + INTERVAL 1 HOUR"
        ) in compact_replay_sql
        # clickhouse-driver serializes datetime values as quoted SQL literals;
        # date functions reject those literals unless the query restores type.
        assert "toStartOfHour(%(start_date)s)" not in compact_replay_sql
        assert "toStartOfHour(%(end_date)s)" not in compact_replay_sql
        assert replay_sql.count("mapContains(") == 1
        assert "custom_metric_source.is_deleted" in replay_sql
        assert "custom_metric_source._version" in replay_sql
        assert "indexHint(" not in replay_sql
        assert "tupleElement(latest_metric_state, 1) = 0" in live_sql
        assert "tupleElement(latest_metric_state, 3) = 1" in live_sql
        assert sql.index("argMax(") < sql.index(
            "tupleElement(latest_metric_state, 1) = 0"
        )

    def test_time_to_first_token_exact_read_uses_metric_key(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        metric = {
            "id": "time_to_first_token",
            "name": "time_to_first_token",
            "type": "system_metric",
            "aggregation": "avg",
        }

        sql, params, metric_info = DashboardQueryBuilderV2(
            {**sample_query_config, "metrics": [metric]}
        ).build_all_queries()[0]

        assert "FROM spans FINAL" in sql
        assert "attrs_number['gen_ai.server.time_to_first_token']" in sql
        assert not any(key.startswith("_raw_attr_") for key in params)
        assert "query_status" not in metric_info

    def test_canonical_boolean_array_and_map_filters_compile_together(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        canonical_filters = [
            {
                "column_id": "is_final",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
            {
                "column_id": "labels",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "array",
                    "filter_op": "contains",
                    "filter_value": ["vip", True, 2],
                },
            },
            {
                "column_id": "routing",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "map",
                    "filter_op": "equals",
                    "filter_value": {"tier": "gold", "enabled": True},
                },
            },
        ]
        config = _normalize_dashboard_query_filters(
            {**sample_query_config, "filters": canonical_filters}
        )

        sql, params, metric_info = DashboardQueryBuilderV2(config).build_all_queries()[
            0
        ]

        assert "mapContains(attrs_bool" in sql
        assert "JSONExtractArrayRaw(attributes_extra" in sql
        assert "JSONExtractRaw(attributes_extra" in sql
        assert "JSONLength(JSONExtractRaw(attributes_extra" in sql
        # The positive boolean equality is an exhaustive candidate witness.
        # Array/map predicates remain outside the replay source and are still
        # applied exactly after every candidate identity resolves to latest.
        assert "dashboard_filter_candidate_identities AS" in sql
        assert "FROM spans FINAL" not in sql
        assert "LIMIT 1 BY" in sql
        assert not any(key.startswith("_raw_attr_") for key in params)
        assert "query_status" not in metric_info
        assert "True" not in sql
        assert 1 in params.values()

    def test_legacy_is_not_set_filter_does_not_require_candidate_key_presence(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        legacy_filter = {
            "metric_type": "custom_attribute",
            "metric_name": "optional_status",
            "operator": "is_not_set",
            "value": None,
            "attribute_type": "string",
        }

        sql, params, metric_info = DashboardQueryBuilderV2(
            {**sample_query_config, "filters": [legacy_filter]}
        ).build_all_queries()[0]

        assert "attrs_string['optional_status'] = ''" in sql
        assert "_raw_attr_presence_key_0" not in params
        assert "FROM spans FINAL" in sql
        assert "query_status" not in metric_info

    def test_negative_canonical_attribute_filter_keeps_full_exact_source(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        config = _normalize_dashboard_query_filters(
            {
                **sample_query_config,
                "filters": [
                    {
                        "column_id": "conversation.transcript.0.message.content",
                        "filter_config": {
                            "col_type": "SPAN_ATTRIBUTE",
                            "filter_type": "text",
                            "filter_op": "not_equals",
                            "filter_value": "a long exact transcript value",
                        },
                    }
                ],
            }
        )

        sql, params, metric_info = DashboardQueryBuilderV2(
            config
        ).build_all_queries()[0]

        assert "dashboard_filter_candidate_identities AS" not in sql
        assert "FROM spans FINAL" in sql
        assert "a long exact transcript value" in params.values()
        assert "query_status" not in metric_info

    def test_positive_text_candidate_replays_latest_then_reapplies_exact_filter(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        key = "conversation.recording.mono.combined"
        value = "https://storage.example.test/a/very/long/recording.wav"
        config = _normalize_dashboard_query_filters(
            {
                **sample_query_config,
                "filters": [
                    {
                        "column_id": key,
                        "filter_config": {
                            "col_type": "SPAN_ATTRIBUTE",
                            "filter_type": "text",
                            "filter_op": "equals",
                            "filter_value": value,
                        },
                    }
                ],
            }
        )

        sql, params, metric_info = DashboardQueryBuilderV2(
            config
        ).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert "dashboard_filter_candidate_identities AS" in sql
        assert "FROM spans FINAL" not in sql
        assert "dashboard_replay_source._version DESC" in sql
        assert "LIMIT 1 BY" in sql
        assert (
            "tuple( dashboard_replay_source.project_id, "
            "dashboard_replay_source.observation_type, "
            "dashboard_replay_source.service_name, "
            "toStartOfHour(dashboard_replay_source.start_time), "
            "dashboard_replay_source.trace_id, dashboard_replay_source.id ) IN ("
            in compact_sql
        )
        # The candidate witness and outer exact predicate have separate
        # bindings, so candidate discovery cannot replace exact semantics.
        assert value in params.values()
        assert any(
            name.startswith("dashboard_candidate_latest_filter_")
            and bound_value == value
            for name, bound_value in params.items()
        )
        assert any(
            name.startswith("latest_filter_") and bound_value == value
            for name, bound_value in params.items()
        )
        assert "query_status" not in metric_info

    def test_legacy_boolean_filter_uses_boolean_map_in_exact_read(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        legacy_filter = {
            "metric_type": "custom_attribute",
            "metric_name": "is_final",
            "operator": "equal_to",
            "value": True,
            "attribute_type": "boolean",
        }

        sql, params, metric_info = DashboardQueryBuilderV2(
            {**sample_query_config, "filters": [legacy_filter]}
        ).build_all_queries()[0]

        assert "attrs_bool['is_final'] = %(f_0_val)s" in sql
        assert "_raw_attr_presence_key_0" not in params
        assert "attrs_string['is_final']" not in sql
        assert params["f_0_val"] is True
        assert "FROM spans FINAL" in sql
        assert "query_status" not in metric_info

    def test_long_minute_window_is_exact_without_candidate_truncation(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        custom_metric = {
            "id": "final_status",
            "name": "final_status",
            "type": "custom_attribute",
            "attribute_key": "final_status",
            "attribute_type": "string",
            "aggregation": "count_distinct",
        }
        config = {
            **sample_query_config,
            "granularity": "minute",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00Z",
                "custom_end": "2026-01-01T00:00:00Z",
            },
            "metrics": [custom_metric],
        }

        sql, params, metric_info = DashboardQueryBuilderV2(config).build_all_queries()[
            0
        ]

        assert "FROM spans FINAL" in sql
        assert "UNION ALL" not in sql
        assert "LIMIT %(_raw_attr_" not in sql
        assert not any(key.startswith("_raw_attr_") for key in params)
        assert params["start_date"] == datetime(2025, 1, 1, tzinfo=UTC)
        assert params["end_date"] == datetime(2026, 1, 1, tzinfo=UTC)
        assert "query_status" not in metric_info
        stripped = without_query_settings(sql)
        assert "SETTINGS" not in stripped
        assert "FROM spans FINAL" in stripped

    def test_raw_attribute_exact_source_keeps_latest_state_inside_id_remap(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        config = {
            **sample_query_config,
            "metrics": [
                {
                    "id": "final_status",
                    "name": "final_status",
                    "type": "custom_attribute",
                    "attribute_key": "final_status",
                    "attribute_type": "string",
                    "aggregation": "count_distinct",
                    "filters": [
                        {
                            "metric_type": "system_metric",
                            "metric_name": "session",
                            "operator": "equal_to",
                            "value": "00000000-0000-4000-8000-000000000002",
                        }
                    ],
                }
            ],
        }

        sql, params, metric_info = DashboardQueryBuilderV2(config).build_all_queries()[
            0
        ]

        assert "trace_session_id_remap" in sql
        assert "FROM spans AS sp FINAL" in sql
        assert "FROM spans AS sp FINAL" in without_query_settings(sql)
        assert "_raw_attr_candidate_limit" not in sql
        assert not any(key.startswith("_raw_attr_") for key in params)
        assert "query_status" not in metric_info

    def test_exact_raw_read_ignores_non_trace_filter_and_breakdown_sources(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        config = {
            **sample_query_config,
            "filters": [
                {
                    "metric_type": "custom_attribute",
                    "metric_name": "dataset_only_key",
                    "operator": "equal_to",
                    "value": "ignored",
                    "attribute_type": "string",
                    "source": "datasets",
                }
            ],
            "breakdowns": [
                {
                    "type": "custom_attribute",
                    "name": "simulation_only_key",
                    "attribute_type": "string",
                    "source": "simulation",
                }
            ],
        }

        sql, params, metric_info = DashboardQueryBuilderV2(config).build_all_queries()[
            0
        ]

        assert "_raw_attr_candidate_limit" not in params
        assert "dataset_only_key" not in sql
        assert "simulation_only_key" not in sql
        assert "query_status" not in metric_info

    def test_exact_builder_never_emits_obsolete_sampling_metadata(
        self, sample_query_config, settings
    ):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        config = {
            **sample_query_config,
            "metrics": [
                {
                    "id": "final_status",
                    "name": "final_status",
                    "type": "custom_attribute",
                    "attribute_key": "final_status",
                    "attribute_type": "string",
                    "aggregation": "count_distinct",
                }
            ],
        }
        builder = DashboardQueryBuilderV2(config)
        metric_info = builder.metric_info(config["metrics"][0])

        trace_payload = builder.format_results([(metric_info, [])])
        base_formatter = DashboardQueryBuilderBase(config)
        merged_metric = base_formatter._format_metric_result(
            metric_info,
            [],
            [],
            {},
        )

        for formatted_metric in (trace_payload["metrics"][0], merged_metric):
            assert "query_status" not in formatted_metric
            assert "query_sampled" not in formatted_metric
            assert "query_error_code" not in formatted_metric
            assert "query_sampling_strategy" not in formatted_metric
            assert "query_sampling_interval_seconds" not in formatted_metric
            assert "query_sample_limit" not in formatted_metric
            assert "query_sample_per_bucket" not in formatted_metric

        serializer = DashboardQueryApiResponseSerializer(
            data={"status": True, "result": trace_payload}
        )
        assert serializer.is_valid(), serializer.errors

    def test_breakdown_query_prunes_partitions(self):
        """A latency average broken down by a custom span attribute must emit
        the created_at partition-prune bound while preserving the start_time
        window."""
        config = {
            "project_ids": [str(uuid.uuid4())],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [
                {
                    "name": "final_status",
                    "type": "custom_attribute",
                    "source": "traces",
                    "display_name": "final_status",
                    "attribute_type": "string",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        sql, _, _ = builder.build_all_queries()[0]
        assert "created_at >= %(start_date)s - INTERVAL 1 DAY" in sql
        assert "start_time >= %(start_date)s" in sql
        # the breakdown is still applied (real call path intact)
        assert "breakdown_value" in sql

    def test_all_system_metrics(self):
        for metric_name in SYSTEM_METRICS:
            config = {
                "project_ids": ["proj1"],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": metric_name,
                        "name": metric_name,
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
            }
            builder = DashboardQueryBuilder(config)
            queries = builder.build_all_queries()
            assert len(queries) == 1

    def test_all_aggregations(self):
        for agg_name in AGGREGATIONS:
            config = {
                "project_ids": ["proj1"],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": agg_name,
                    }
                ],
            }
            builder = DashboardQueryBuilder(config)
            queries = builder.build_all_queries()
            assert len(queries) == 1

    def test_eval_metric_query(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "hour",
            "time_range": {"preset": "today"},
            "metrics": [
                {
                    "id": "e1",
                    "name": "accuracy",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "output_type": "SCORE",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1
        sql, params, _ = queries[0]
        assert "usage_apicalllog" in sql
        assert "eval_score" in sql

    def test_eval_metric_pass_fail(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e2",
                    "name": "pass_rate",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "output_type": "PASS_FAIL",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "eval_output_str" in sql
        assert "eval_score" in sql

    def test_eval_pass_fail_filter_uses_canonical_label_coercion(self):
        eval_id = str(uuid.uuid4())
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "pass-rate-filtered",
                    "name": "pass_rate",
                    "type": "eval_metric",
                    "config_id": eval_id,
                    "output_type": "PASS_FAIL",
                    "aggregation": "avg",
                    "filters": [
                        {
                            "metric_type": "eval_metric",
                            "metric_name": eval_id,
                            "operator": "str_contains",
                            "value": 0,
                            "output_type": "PASS_FAIL",
                        }
                    ],
                }
            ],
        }

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "'Passed', 'Failed'" in sql
        assert params["_evf_0_val"] == "%Failed%"

    @pytest.mark.parametrize(
        ("operator", "value", "expected"),
        [
            ("equal_to", "Passed", "Passed"),
            ("equal_to", "Failed", "Failed"),
            ("equal_to", True, "Passed"),
            ("equal_to", 0.0, "Failed"),
            ("contains", ["Passed", "Failed"], ["Passed", "Failed"]),
            ("contains", [1.0, 0.0], ["Passed", "Failed"]),
        ],
    )
    def test_eval_pass_fail_filter_accepts_public_and_legacy_values(
        self, operator, value, expected
    ):
        eval_id = str(uuid.uuid4())
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "pass-rate-filtered",
                    "name": "pass_rate",
                    "type": "eval_metric",
                    "config_id": eval_id,
                    "output_type": "PASS_FAIL",
                    "aggregation": "avg",
                    "filters": [
                        {
                            "metric_type": "eval_metric",
                            "metric_name": eval_id,
                            "operator": operator,
                            "value": value,
                            "output_type": "PASS_FAIL",
                        }
                    ],
                }
            ],
        }

        sql, params, _ = DashboardQueryBuilder(config).build_all_queries()[0]

        assert "if((e.eval_score >= 1.0" in sql
        assert "'Passed', 'Failed')" in sql
        assert params["_evf_0_val"] == expected

    def test_eval_pass_fail_canonical_filter_and_joined_paths_use_labels(self):
        eval_id = str(uuid.uuid4())
        joined_eval_id = str(uuid.uuid4())
        config = _normalize_dashboard_query_filters(
            {
                "project_ids": ["proj1"],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "pass-rate-filtered",
                        "name": "pass_rate",
                        "type": "eval_metric",
                        "config_id": eval_id,
                        "output_type": "PASS_FAIL",
                        "aggregation": "avg",
                        "filters": [
                            {
                                "column_id": joined_eval_id,
                                "output_type": "PASS_FAIL",
                                "filter_config": {
                                    "col_type": "EVAL_METRIC",
                                    "filter_type": "text",
                                    "filter_op": "in",
                                    "filter_value": ["Passed"],
                                },
                            }
                        ],
                    }
                ],
                "breakdowns": [
                    {
                        "name": eval_id,
                        "type": "eval_metric",
                        "config_id": eval_id,
                        "output_type": "PASS_FAIL",
                    },
                    {
                        "name": joined_eval_id,
                        "type": "eval_metric",
                        "config_id": joined_eval_id,
                        "output_type": "PASS_FAIL",
                    },
                ],
            }
        )

        sql, params, _ = DashboardQueryBuilder(config).build_all_queries()[0]

        assert "if((e.eval_score >= 1.0" in sql
        assert "if((ev_bd1.eval_score >= 1.0" in sql
        assert "if((ev_f0.eval_score >= 1.0" in sql
        assert "'Passed', 'Failed'" in sql
        assert params["_evf_0_val"] == ["Passed"]

    def test_eval_metric_compiles_typed_canonical_span_filters(self):
        canonical_filters = [
            {
                "column_id": "is_final",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
            {
                "column_id": "routing",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "map",
                    "filter_op": "contains",
                    "filter_value": {"tier": "gold"},
                },
            },
        ]
        config = _normalize_dashboard_query_filters(
            {
                "project_ids": ["proj1"],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "typed-eval-filter",
                        "name": "quality",
                        "type": "eval_metric",
                        "config_id": str(uuid.uuid4()),
                        "output_type": "SCORE",
                        "aggregation": "avg",
                    }
                ],
                "filters": canonical_filters,
            }
        )

        sql, params, metric_info = DashboardQueryBuilderV2(config).build_all_queries()[
            0
        ]

        assert "dashboard_filter_candidate_identities AS" in sql
        assert "FROM spans FINAL" not in sql
        assert ") AS s" in sql
        assert "dashboard_replay_source._version DESC" in sql
        assert "LIMIT 1 BY" in sql
        assert "usage_span_trace_candidates" in sql
        assert "s.project_id IN %(project_ids)s" in sql
        assert "s.trace_id IN (SELECT toString(trace_id) AS trace_id" in sql
        assert "mapContains(attrs_bool" in sql
        assert "JSONExtractRaw(attributes_extra" in sql
        assert "True" not in sql
        assert 1 in params.values()
        assert "query_status" not in metric_info

    def test_eval_metric_legacy_boolean_filter_uses_boolean_map(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "legacy-boolean-eval-filter",
                    "name": "quality",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "output_type": "SCORE",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "custom_attribute",
                    "metric_name": "is_final",
                    "operator": "equal_to",
                    "value": True,
                    "attribute_type": "boolean",
                }
            ],
        }

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "s.attrs_bool['is_final'] = %(_evf_0_val)s" in sql
        assert "s.attrs_string['is_final']" not in sql
        assert params["_evf_0_val"] is True

    def test_eval_metric_string_dimension_keeps_numeric_looking_value_as_string(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "numeric-looking-user-eval-filter",
                    "name": "quality",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "output_type": "SCORE",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "system_metric",
                    "metric_name": "user",
                    "operator": "equal_to",
                    "value": "123",
                }
            ],
        }

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "if(s.user_id = '', toString(s.end_user_id), s.user_id) =" in sql
        assert params["_evf_0_val"] == "123"
        assert isinstance(params["_evf_0_val"], str)

    def test_eval_metric_legacy_string_attribute_keeps_numeric_value_as_string(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "numeric-looking-attribute-eval-filter",
                    "name": "quality",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "output_type": "SCORE",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "custom_attribute",
                    "metric_name": "external_code",
                    "operator": "equal_to",
                    "value": "123",
                    "attribute_type": "string",
                }
            ],
        }

        _, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert params["_evf_0_val"] == "123"
        assert isinstance(params["_evf_0_val"], str)

    def test_eval_metric_sum_uses_output_string_fallback(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e2",
                    "name": "conversation_hallucination",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "aggregation": "sum",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "sum(if(e.eval_output_str = '', NULL" in sql
        assert "lower(e.eval_output_str) IN ('passed', 'pass', 'true', '1')" in sql
        assert "sum(e.eval_score)" not in sql

    def test_eval_metric_avg_keeps_structured_score_rows(self):
        """A structured output is not numeric text, so the numeric-detection
        branch must accept the nested score or every such row is NULLed out.
        """
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e2",
                    "name": "conversation_hallucination",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert (
            "JSONType(e.eval_output_str, 'score') IN ('Double', 'Int64', 'UInt64')"
            in sql
        )

    def test_pass_fail_paths_render_one_shared_predicate(self):
        """The time-series predicate is unchanged to the byte, and the breakdown
        label and the eval filter now render it instead of a 'Passed' literal.
        """
        eval_id = str(uuid.uuid4())
        metric = {
            "id": "e_pf",
            "name": "pass_fail_eval",
            "type": "eval_metric",
            "config_id": eval_id,
            "output_type": "PASS_FAIL",
            "aggregation": "pass_rate",
        }
        builder = DashboardQueryBuilder(
            {
                "project_ids": ["proj1"],
                "organization_id": str(uuid.uuid4()),
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [metric],
                "breakdowns": [metric],
            }
        )

        sql, _, _ = builder.build_all_queries()[0]
        breakdown_expr = builder._resolve_all_breakdowns({})[0]["expr"]
        filter_clauses, _ = builder._build_subquery_filters(
            [
                {
                    "metric_type": "eval_metric",
                    "metric_name": eval_id,
                    "output_type": "PASS_FAIL",
                    "operator": "equal_to",
                    "value": 1.0,
                }
            ],
            {},
            "f_",
        )

        assert (
            "(e.eval_score >= 1.0 OR lower(e.eval_output_str) IN "
            "('passed', 'pass', 'true', '1'))" in sql
        ), "the time-series pass predicate must render exactly as it did before"
        assert (
            "(ev0.eval_score >= 1.0 OR lower(ev0.eval_output_str) IN "
            "('passed', 'pass', 'true', '1'))" in breakdown_expr
        ), "the PASS_FAIL breakdown label must not read a structured row as Fail"
        assert (
            "(eval_score >= 1.0 OR lower(eval_output_str) IN "
            "('passed', 'pass', 'true', '1'))" in filter_clauses[0]
        ), "the PASS_FAIL eval filter must select what the widget labels Pass"

    def test_eval_metric_combines_project_and_dataset_breakdowns(self):
        config = {
            "project_ids": ["proj1"],
            "organization_id": str(uuid.uuid4()),
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e2",
                    "name": "conversation_hallucination",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "aggregation": "count",
                }
            ],
            "breakdowns": [
                {"name": "project", "type": "system_metric"},
                {"name": "dataset", "type": "system_metric"},
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "concat(" in sql
        assert "' / '" in sql
        assert " AS breakdown_value" in sql
        assert sql.count(" AS breakdown_value") == 1
        assert "e.eval_project_id" in sql
        assert "trace_dict" not in sql
        assert "e.eval_dataset_id" in sql

    def test_eval_metric_dedups_physical_rows_and_reruns_in_one_bounded_scan(
        self,
    ):
        """Collapse physical versions, then the latest trace attempt."""
        config = {
            "project_ids": ["proj1"],
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e_dedup",
                    "name": "conversation_hallucination",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "aggregation": "count",
                }
            ],
        }
        for builder_cls in (DashboardQueryBuilder, DashboardQueryBuilderV2):
            sql, _, _ = builder_cls(config).build_all_queries()[0]
            assert "usage_apicalllog AS usage_main_scan" in sql
            assert "usage_main_scan.*" not in sql
            assert "usage_main_scan.eval_trace_id" in sql
            assert "usage_main_scan.eval_score" in sql
            assert "usage_apicalllog AS e FINAL" not in sql
            assert "ORDER BY usage_main_scan._peerdb_version DESC" in sql
            assert "LIMIT 1 BY usage_main_scan.id" in sql
            assert "usage_main_latest._peerdb_is_deleted = 0" in sql
            assert "usage_main_latest.deleted = 0" in sql
            assert "LIMIT 1 BY if(" in sql
            assert "concat('row:', toString(usage_main_latest.id))" in sql
            assert "concat('trace:', usage_main_latest.eval_trace_id)" in sql
            assert "usage_main_scan.workspace_id = toUUID(%(workspace_id)s)" in sql

    def test_eval_metric_scopes_trace_attached_rows_to_selected_projects(self):
        """Workspace scope alone must not mix sibling-project trace evals.

        The simple metric must read the large usage slice exactly once. Trace
        ownership comes from the narrow project-key relation rather than a
        second pass over the same usage rows.
        """
        project_id = str(uuid.uuid4())
        config = {
            "project_ids": [project_id],
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e_project_scope",
                    "name": "conversation_hallucination",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "aggregation": "count",
                }
            ],
        }

        for builder_cls in (DashboardQueryBuilder, DashboardQueryBuilderV2):
            sql, params, _ = builder_cls(config).build_all_queries()[0]
            compact_sql = " ".join(sql.split())
            assert "trace_dict" not in compact_sql
            assert "FROM traces" in compact_sql
            assert (
                "PREWHERE trace_project_scan.project_id IN %(project_ids)s"
                in compact_sql
            )
            assert "AS bounded_trace_candidates" not in compact_sql
            assert "usage_trace_candidate" not in compact_sql
            assert compact_sql.count("FROM usage_apicalllog AS") == 1
            assert "GROUP BY trace_project_scan.id" in compact_sql
            assert (
                "uniqExact(trace_project_scan.project_id) AS project_identity_count"
                in compact_sql
            )
            assert "WHERE project_identity_count = 1" in compact_sql
            assert "toUUIDOrZero(usage_main_latest.eval_trace_id)" in compact_sql
            assert "IN %(project_ids)s" in compact_sql
            assert params["project_ids"] == [project_id]
            assert "usage_main_latest.eval_trace_id = ''" in sql

    def test_eval_metric_breakdown_buckets_all_eval_sources(self):
        config = {
            "project_ids": ["proj1"],
            "organization_id": str(uuid.uuid4()),
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e2",
                    "name": "conversation_hallucination",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "aggregation": "count",
                }
            ],
            "breakdowns": [
                {"name": "project", "type": "system_metric"},
                {"name": "dataset", "type": "system_metric"},
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        for source in (
            "feedback",
            "tracer_composite",
            "prompt_template",
            "simulate",
            "simulate_tool_evaluation",
            "voice_call",
            "text_call",
            "composite_eval",
            "composite_eval_adhoc",
            "composite_eval_dataset",
        ):
            assert f"e.source = '{source}'" in sql
        assert "'(simulation)'" in sql

    def test_eval_metric_project_breakdown_falls_through_to_source_bucket_labels(
        self,
    ):
        """When the project breakdown can't resolve a project (playground /
        dataset / SDK rows have no trace and can't feed ``trace_dict``), the
        fallback must dispatch on ``e.source`` and surface user-facing bucket
        labels — not lump everything under ``(no project)``.
        """
        config = {
            "project_ids": ["proj1"],
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e_src",
                    "name": "conversation_hallucination",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "aggregation": "count",
                }
            ],
            "breakdowns": [{"name": "project", "type": "system_metric"}],
        }
        for builder_cls in (DashboardQueryBuilder, DashboardQueryBuilderV2):
            sql, _, _ = builder_cls(config).build_all_queries()[0]
            # Direct-write trace resolution branch is still tried first.
            assert "e.eval_project_id" in sql
            assert "trace_dict" not in sql
            # Fallback dispatches on eval source with human-readable labels.
            assert "e.source = 'eval_playground'" in sql
            assert "'(playground)'" in sql
            assert "e.source = 'dataset_evaluation'" in sql
            assert "'(dataset)'" in sql
            assert "e.source = 'standalone_v2'" in sql
            assert "'(sdk)'" in sql
            # The excluded-self rule keeps 'tracer' out of the project fallback.
            assert "e.source = 'tracer'" not in sql

    def test_system_metric_sum_aggregation(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "cost",
                    "name": "cost",
                    "type": "system_metric",
                    "aggregation": "sum",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "sum(cost)" in sql

    def test_system_metric_median_aggregation(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "median",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "quantileExact(0.5)(latency_ms)" in sql

    def test_system_metric_count_distinct_aggregation(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "model",
                    "name": "model",
                    "type": "system_metric",
                    "aggregation": "count_distinct",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "uniqExact(model)" in sql

    def test_project_metric_count_uses_distinct_projects(self):
        config = {
            "project_ids": ["proj1", "proj2"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "project",
                    "name": "project",
                    "type": "system_metric",
                    "aggregation": "count",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "uniqExact(project_id)" in sql

    def test_user_count_forces_uniq_on_resolved_user_dict_regardless_of_agg(
        self,
    ):
        """Even when the user picks ``count`` (or ``sum``, ``avg``), an
        identity metric like ``user_count`` must run distinct-count on the
        resolved user id — not row count of the containing table.
        """
        for agg in ("count", "avg", "sum"):
            config = {
                "project_ids": ["proj1"],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "user_count",
                        "name": "user_count",
                        "type": "system_metric",
                        "aggregation": agg,
                    }
                ],
            }
            sql, _, _ = DashboardQueryBuilder(config).build_all_queries()[0]
            assert "uniqExact(" in sql
            assert "end_users_dict" in sql
            # row-count fallbacks should never win here
            assert "count(*)" not in sql

    def test_latency_metric_uses_root_spans_only(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "min",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "(parent_span_id IS NULL OR parent_span_id = '')" in sql

    def test_eval_metric_pass_rate_aggregation(self):
        config = {
            "project_ids": ["proj1"],
            "organization_id": str(uuid.uuid4()),
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e_pass_rate",
                    "name": "accuracy",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "output_type": "PASS_FAIL",
                    "aggregation": "pass_rate",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "countIf(" in sql
        assert "/ nullIf(count(), 0)" in sql

    def test_eval_metric_fail_count_aggregation(self):
        config = {
            "project_ids": ["proj1"],
            "organization_id": str(uuid.uuid4()),
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "e_fail_count",
                    "name": "accuracy",
                    "type": "eval_metric",
                    "config_id": str(uuid.uuid4()),
                    "output_type": "PASS_FAIL",
                    "aggregation": "fail_count",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "countIf(" in sql
        assert "AS value" in sql

    def test_annotation_metric_query(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "a1",
                    "name": "quality",
                    "type": "annotation_metric",
                    "label_id": str(uuid.uuid4()),
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "model_hub_score" in sql
        assert "JSONExtract(a.value, 'value', 'Nullable(Float64)')" in sql
        assert params["annotation_label_id"]

    def test_annotation_star_metric_uses_rating_value(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "a_star",
                    "name": "quality_star",
                    "type": "annotation_metric",
                    "label_id": str(uuid.uuid4()),
                    "aggregation": "avg",
                    "output_type": "star",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "model_hub_score" in sql
        assert "JSONExtract(a.value, 'rating', 'Nullable(Float64)')" in sql

    def test_custom_attribute_query(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "c1",
                    "name": "my_metric",
                    "type": "custom_attribute",
                    "attribute_key": "custom.score",
                    "attribute_type": "number",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "span_attr_num" in sql
        assert "span_attr_num[%(custom_metric_attr_key)s]" in sql
        assert "mapContains(span_attr_num, %(custom_metric_attr_key)s)" in sql
        assert params["custom_metric_attr_key"] == "custom.score"

    def test_v2_string_attribute_metric_uses_map_key_bloom_predicate(self):
        config = {
            "project_ids": ["00000000-0000-4000-8000-000000000001"],
            "allow_sampled": True,
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "final_status",
                    "name": "final_status",
                    "type": "custom_attribute",
                    "attribute_key": "final_status",
                    "attribute_type": "string",
                    "aggregation": "count_distinct",
                }
            ],
        }

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "uniqExact(attrs_string[%(custom_metric_attr_key)s])" in sql
        assert "mapContains(attrs_string, %(custom_metric_attr_key)s)" in sql
        assert params["custom_metric_attr_key"] == "final_status"
        assert "span_attr_str" not in sql

    @pytest.mark.parametrize(
        "attribute_key",
        [
            "span_attr_str",
            "span_attr_num",
            "span_attr_bool",
            "prefix.span_attr_str",
            "span_attr_num.suffix",
            "prefix.span_attr_bool.suffix",
        ],
    )
    def test_v2_custom_metric_alias_like_key_is_bound_as_data(self, attribute_key):
        config = {
            "project_ids": ["00000000-0000-4000-8000-000000000001"],
            "allow_sampled": True,
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": attribute_key,
                    "name": attribute_key,
                    "type": "custom_attribute",
                    "attribute_key": attribute_key,
                    "attribute_type": "string",
                    "aggregation": "count_distinct",
                }
            ],
        }

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "attrs_string[%(custom_metric_attr_key)s]" in sql
        assert "mapContains(attrs_string, %(custom_metric_attr_key)s)" in sql
        assert params["custom_metric_attr_key"] == attribute_key
        assert attribute_key not in sql

    def test_multiple_metrics(self, sample_query_config):
        sample_query_config["metrics"].append(
            {
                "id": "cost",
                "name": "cost",
                "type": "system_metric",
                "aggregation": "sum",
            }
        )
        builder = DashboardQueryBuilder(sample_query_config)
        queries = builder.build_all_queries()
        assert len(queries) == 2

    def test_breakdown_system(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [{"type": "system_metric", "name": "model"}],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "breakdown_value" in sql
        assert "model" in sql

    def test_breakdown_custom_attribute(self):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [
                {"type": "custom_attribute", "name": "env", "attribute_type": "string"}
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "span_attr_str" in sql
        assert "breakdown_value" in sql
        assert "mapContains(span_attr_str, %(_custom_bd_key_0)s)" in sql
        assert params["_custom_bd_key_0"] == "env"

    def test_v2_custom_attribute_breakdown_uses_map_key_bloom_predicate(self):
        config = {
            "project_ids": ["00000000-0000-4000-8000-000000000001"],
            "allow_sampled": True,
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [
                {
                    "type": "custom_attribute",
                    "name": "final_status",
                    "attribute_type": "string",
                }
            ],
        }

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "attrs_string[%(_custom_bd_key_0)s] AS breakdown_value" in sql
        assert "mapContains(attrs_string, %(_custom_bd_key_0)s)" in sql
        assert params["_custom_bd_key_0"] == "final_status"
        assert "span_attr_str" not in sql

    @pytest.mark.parametrize(
        "attribute_key",
        [
            "span_attr_str",
            "span_attr_num",
            "span_attr_bool",
            "prefix.span_attr_str",
            "span_attr_num.suffix",
            "prefix.span_attr_bool.suffix",
        ],
    )
    def test_annotation_join_custom_breakdown_alias_like_key_is_bound_as_data(
        self, attribute_key
    ):
        label_id = "00000000-0000-4000-8000-000000000077"
        config = {
            "project_ids": ["00000000-0000-4000-8000-000000000001"],
            "allow_sampled": True,
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [
                {
                    "type": "annotation_metric",
                    "name": label_id,
                    "label_id": label_id,
                    "output_type": "thumbs_up_down",
                },
                {
                    "type": "custom_attribute",
                    "name": attribute_key,
                    "attribute_type": "string",
                },
            ],
        }

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "LEFT JOIN model_hub_score AS ann0" in sql
        assert "attrs_string[%(_custom_bd_key_0)s]" in sql
        assert "mapContains(attrs_string, %(_custom_bd_key_0)s)" in sql
        assert params["_custom_bd_key_0"] == attribute_key
        assert attribute_key not in sql


class TestDashboardAttrRollupRouting:
    """Routing for the latency-avg × covered-attribute breakdown.

    Drives the real build_all_queries() call-path. [FIX] tests go RED if the
    routing branch is removed; [FALLBACK] tests prove the spans path is kept.

    The rollup is fail-closed behind three gates: v2 schema only
    (``_attr_rollup_available``), DASHBOARD_ATTR_ROLLUP_ENABLED, and the window
    starting at/after DASHBOARD_ATTR_ROLLUP_COVERED_SINCE. ``_v2``+``_enable``
    open all three so a [FALLBACK] test isolates the one condition it names.
    """

    # Far enough in the past that the 30D-preset window always starts after it.
    _COVERED_SINCE = datetime(2000, 1, 1, tzinfo=UTC)

    @staticmethod
    def _config(
        metric_name="latency",
        aggregation="avg",
        breakdowns=None,
        metric_filters=None,
        global_filters=None,
        granularity="day",
    ):
        metric = {
            "id": metric_name,
            "name": metric_name,
            "type": "system_metric",
            "aggregation": aggregation,
        }
        if metric_filters is not None:
            metric["filters"] = metric_filters
        return {
            "project_ids": [str(uuid.uuid4())],
            "allow_sampled": True,
            "granularity": granularity,
            "time_range": {"preset": "30D"},
            "metrics": [metric],
            "filters": global_filters or [],
            "breakdowns": breakdowns if breakdowns is not None else [],
        }

    @staticmethod
    def _bd(name):
        return {
            "type": "custom_attribute",
            "name": name,
            "source": "traces",
            "display_name": name,
            "attribute_type": "string",
        }

    @staticmethod
    def _v2(config):
        return DashboardQueryBuilderV2(config)

    def _enable(self, settings, covered_since=None):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = True
        settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = (
            self._COVERED_SINCE if covered_since is None else covered_since
        )

    def test_covered_breakdown_final_status_routes_to_rollup(self, settings):
        # [FIX] final_status → rollup. RED without the routing branch.
        self._enable(settings)
        config = self._config(breakdowns=[self._bd("final_status")])
        sql, params, metric_info = self._v2(config).build_all_queries()[0]
        # Targets the rollup, reads merged state, and does NOT scan the Map.
        assert "dashboard_attr_rollup" in sql
        assert "sumMerge(latency_sum)" in sql
        assert "countMerge(n)" in sql
        assert "span_attr_str" not in sql
        assert "FROM spans" not in sql
        # Output contract unchanged: time_bucket / breakdown_value / value.
        assert "time_bucket" in sql
        assert "breakdown_value" in sql
        # attr_key is passed as a param, filtered on in the rollup.
        assert params["attr_key"] == "final_status"
        assert "attr_key = %(attr_key)s" in sql
        assert "query_status" not in metric_info

    def test_covered_breakdown_country_routes_to_rollup(self, settings):
        # [FIX] country → rollup too.
        self._enable(settings)
        config = self._config(breakdowns=[self._bd("country")])
        sql, params, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" in sql
        assert "sumMerge(latency_sum) / countMerge(n)" in sql
        assert "span_attr_str" not in sql
        assert params["attr_key"] == "country"

    def test_v1_builder_never_routes_to_rollup(self, settings):
        # [FALLBACK] FIX 1 — base/v1 builder lacks the rollup table; even with
        # the flag on and the window covered it must emit the spans scan.
        self._enable(settings)
        config = self._config(breakdowns=[self._bd("final_status")])
        sql, _, _ = DashboardQueryBuilder(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql
        assert "FROM spans" in sql

    def test_flag_disabled_falls_back_to_spans(self, settings):
        # [FALLBACK] FIX 2 — flag off (fresh deploy) → spans path.
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
        settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = self._COVERED_SINCE
        config = self._config(breakdowns=[self._bd("final_status")])
        sql, _, metric_info = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql
        assert "FROM spans FINAL" in sql
        assert "query_status" not in metric_info

    def test_coverage_unset_falls_back_to_spans(self, settings):
        # [FALLBACK] FIX 2 — flag on but no coverage date set → spans path.
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = True
        settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = None
        config = self._config(breakdowns=[self._bd("final_status")])
        sql, _, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql
        assert "FROM spans" in sql

    def test_window_before_coverage_falls_back_to_spans(self, settings):
        # [FALLBACK] boundary (a) — a window starting before COVERED_SINCE is
        # not backfilled; route must fall back, never return a partial rollup.
        self._enable(settings, covered_since=datetime.now(UTC) + timedelta(days=1))
        config = self._config(breakdowns=[self._bd("final_status")])
        sql, _, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql
        assert "FROM spans" in sql

    def test_per_metric_filter_falls_back_to_spans(self, settings):
        # [FALLBACK] per-metric filter → spans path.
        self._enable(settings)
        config = self._config(
            breakdowns=[self._bd("final_status")],
            metric_filters=[
                {
                    "metric_type": "system_metric",
                    "metric_name": "status",
                    "operator": "equal_to",
                    "value": "OK",
                }
            ],
        )
        sql, _, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql
        assert "FROM spans" in sql

    def test_global_filter_falls_back_to_spans(self, settings):
        # [FALLBACK] a global filter present → spans path.
        self._enable(settings)
        config = self._config(
            breakdowns=[self._bd("final_status")],
            global_filters=[
                {
                    "metric_type": "custom_attribute",
                    "metric_name": "env",
                    "operator": "equal_to",
                    "value": "prod",
                    "attribute_type": "string",
                }
            ],
        )
        sql, _, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql
        assert "FROM spans" in sql

    def test_uncovered_attribute_falls_back_to_spans(self, settings):
        # [FALLBACK] an attribute outside the covered set (user_id) → spans path.
        self._enable(settings)
        config = self._config(breakdowns=[self._bd("user_id")])
        sql, _, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql
        assert "FROM spans" in sql

    def test_non_avg_aggregation_falls_back_to_spans(self, settings):
        # [FALLBACK] non-avg (p95) → spans path.
        self._enable(settings)
        config = self._config(aggregation="p95", breakdowns=[self._bd("final_status")])
        sql, _, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql
        assert "FROM spans" in sql

    def test_non_latency_metric_falls_back_to_spans(self, settings):
        # [FALLBACK] non-latency (cost) → spans path.
        self._enable(settings)
        config = self._config(metric_name="cost", breakdowns=[self._bd("final_status")])
        sql, _, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql
        assert "cost" in sql.lower()
        assert "breakdown_value" in sql

    def test_two_breakdowns_fall_back_to_spans(self, settings):
        # [FALLBACK] >1 breakdown → spans path.
        self._enable(settings)
        config = self._config(
            breakdowns=[self._bd("final_status"), self._bd("country")]
        )
        sql, _, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql

    def test_no_breakdown_latency_avg_falls_back_to_spans(self, settings):
        # [FALLBACK] plain latency avg with no breakdown → spans path unchanged.
        self._enable(settings)
        config = self._config(breakdowns=[])
        sql, _, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql
        assert "latency_ms" in sql

    def test_sub_hour_granularity_falls_back_to_spans(self, settings):
        # [FALLBACK] sub-hour granularity → spans path (rollup is hourly).
        self._enable(settings)
        config = self._config(
            breakdowns=[self._bd("final_status")], granularity="minute"
        )
        sql, _, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" not in sql
        assert "FROM spans" in sql

    def test_hour_granularity_routes_to_rollup(self, settings):
        # [FIX] hour granularity is covered (>= the rollup's hour resolution).
        self._enable(settings)
        config = self._config(breakdowns=[self._bd("final_status")], granularity="hour")
        sql, _, _ = self._v2(config).build_all_queries()[0]
        assert "dashboard_attr_rollup" in sql

    def test_rollup_params_carry_window_bounds(self, settings):
        # [FIX] rollup is window-bounded, never all-history.
        self._enable(settings)
        config = self._config(breakdowns=[self._bd("final_status")])
        sql, params, _ = self._v2(config).build_all_queries()[0]
        assert "hour >= %(start_date)s" in sql
        assert "hour < %(end_date)s" in sql
        assert "start_date" in params and "end_date" in params
        assert "project_id IN %(project_ids)s" in sql

    def test_rollup_window_snapped_to_hour(self, settings):
        # [FIX] FIX 3 — the rollup window is floored to whole hours so no
        # partial bucket is read.
        self._enable(settings)
        config = self._config(breakdowns=[self._bd("final_status")])
        _, params, _ = self._v2(config).build_all_queries()[0]
        for key in ("start_date", "end_date"):
            dt = params[key]
            assert dt.minute == 0 and dt.second == 0 and dt.microsecond == 0

    def test_weighted_mean_equals_raw_avg(self):
        # sumMerge/countMerge == flat avg of raw latencies; avg-of-avgs would not.
        hour_a = [100, 200, 300]
        hour_b = [1000]
        raw = hour_a + hour_b
        flat_avg = sum(raw) / len(raw)
        states = [(sum(hour_a), len(hour_a)), (sum(hour_b), len(hour_b))]
        weighted = sum(s for s, _ in states) / sum(c for _, c in states)
        assert weighted == pytest.approx(flat_avg)
        avg_of_avgs = ((sum(hour_a) / len(hour_a)) + (sum(hour_b) / len(hour_b))) / 2
        assert avg_of_avgs != pytest.approx(flat_avg)


class TestDashboardQueryBuilderTimeRanges:
    def test_preset_7d(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        start, end = builder.parse_time_range()
        assert (end - start).days <= 7

    def test_preset_today(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "hour",
            "time_range": {"preset": "today"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        start, end = builder.parse_time_range()
        assert start.hour == 0 and start.minute == 0

    def test_preset_yesterday(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "hour",
            "time_range": {"preset": "yesterday"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        start, end = builder.parse_time_range()
        assert start.date() == (datetime.utcnow() - timedelta(days=1)).date()

    def test_custom_time_range(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-31T23:59:59",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        start, end = builder.parse_time_range()
        assert start.year == 2025 and start.month == 1 and start.day == 1

    def test_all_granularities(self):
        for gran in ("minute", "hour", "day", "week", "month", "year"):
            config = {
                "project_ids": ["p1"],
                "granularity": gran,
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
            }
            builder = DashboardQueryBuilder(config)
            queries = builder.build_all_queries()
            assert len(queries) == 1


class TestDashboardQueryBuilderFilters:
    def test_global_system_filter(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "system_metric",
                    "metric_name": "cost",
                    "operator": "greater_than",
                    "value": 0.01,
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "cost" in sql
        assert any("val" in k for k in params)

    def test_custom_attr_key_injection_rejected(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "m",
                    "name": "injected",
                    "type": "custom_attribute",
                    "attribute_key": "key'] OR 1=1 --",
                    "attribute_type": "number",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        with pytest.raises(ValueError, match="Invalid attribute key"):
            builder.build_all_queries()

    def test_unknown_metric_type_raises(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {"id": "x", "name": "x", "type": "unknown_type", "aggregation": "avg"}
            ],
        }
        builder = DashboardQueryBuilder(config)
        with pytest.raises(ValueError, match="Unknown metric type"):
            builder.build_all_queries()


class TestDashboardQueryBuilderFormatResults:
    def test_format_empty_results(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-03T23:59:59",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        result = builder.format_results(
            [({"id": "latency", "name": "latency", "aggregation": "avg"}, [])]
        )
        assert "metrics" in result
        assert len(result["metrics"]) == 1
        series = result["metrics"][0]["series"]
        assert len(series) == 1
        assert series[0]["name"] == "total"
        # All buckets filled with null (Jan 1, 2, 3)
        assert len(series[0]["data"]) == 3
        assert all(d["value"] is None for d in series[0]["data"])

    def test_format_with_data(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-04T23:59:59",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        result = builder.format_results(
            [
                (
                    {"id": "latency", "name": "latency", "aggregation": "avg"},
                    [
                        {"time_bucket": datetime(2025, 1, 1), "value": 123.456789},
                        {"time_bucket": datetime(2025, 1, 2), "value": 200.1},
                    ],
                )
            ]
        )
        metrics = result["metrics"]
        assert len(metrics) == 1
        series = metrics[0]["series"]
        assert len(series) == 1
        assert series[0]["name"] == "total"
        # 4 day buckets (Jan 1-4), 2 with data + 2 filled with null
        assert len(series[0]["data"]) == 4
        non_null = [d for d in series[0]["data"] if d["value"] is not None]
        assert len(non_null) == 2
        assert metrics[0]["unit"] == "ms"

    def test_format_with_breakdown(self):
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-02T23:59:59",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [{"type": "system_metric", "name": "model"}],
        }
        builder = DashboardQueryBuilder(config)
        result = builder.format_results(
            [
                (
                    {"id": "latency", "name": "latency", "aggregation": "avg"},
                    [
                        {
                            "time_bucket": datetime(2025, 1, 1),
                            "value": 100.0,
                            "breakdown_value": "gpt-4",
                        },
                        {
                            "time_bucket": datetime(2025, 1, 1),
                            "value": 200.0,
                            "breakdown_value": "gpt-3.5",
                        },
                    ],
                )
            ]
        )
        series = result["metrics"][0]["series"]
        assert len(series) == 2
        series_names = [s["name"] for s in series]
        assert "gpt-4" in series_names
        assert "gpt-3.5" in series_names

    def test_format_results_resolves_unit_by_id_when_name_is_display_label(self):
        """``get_metric_info`` sets ``name`` from ``display_name``, so a widget
        with ``display_name: "Cost"`` used to look up ``METRIC_UNITS["Cost"] →
        ""`` and drop the ``$`` prefix. The fallback must land on
        ``METRIC_UNITS[id]`` so the unit still resolves.
        """
        config = {
            "project_ids": ["p1"],
            "granularity": "day",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-01T23:59:59",
            },
            "metrics": [
                {
                    "id": "cost",
                    "name": "cost",
                    "type": "system_metric",
                    "aggregation": "sum",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        result = builder.format_results(
            [
                (
                    # display_name-derived name; id is the canonical key.
                    {"id": "cost", "name": "Cost", "aggregation": "sum"},
                    [{"time_bucket": datetime(2025, 1, 1), "value": 15.63}],
                )
            ]
        )
        assert result["metrics"][0]["unit"] == "$"


# ===========================================================================
# Serializer Validation
# ===========================================================================


class TestSerializerValidation:
    def test_query_series_serializer_accepts_empty_breakdown_label(self):
        serializer = DashboardQuerySeriesSerializer(
            data={
                "name": "",
                "data": [{"timestamp": "2026-08-31T00:00:00Z", "value": 1.0}],
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data == {
            "name": "",
            "data": [{"timestamp": "2026-08-31T00:00:00Z", "value": 1.0}],
        }

    def test_widget_serializer_width_too_large(self):
        data = {
            "name": "Test",
            "position": 0,
            "width": 20,
            "height": 4,
            "query_config": {},
            "chart_config": {},
        }
        serializer = DashboardWidgetSerializer(data=data)
        assert not serializer.is_valid()
        assert "width" in serializer.errors

    def test_widget_serializer_width_zero(self):
        data = {
            "name": "Test",
            "position": 0,
            "width": 0,
            "height": 4,
            "query_config": {},
            "chart_config": {},
        }
        serializer = DashboardWidgetSerializer(data=data)
        assert not serializer.is_valid()
        assert "width" in serializer.errors

    def test_widget_serializer_height_zero(self):
        data = {
            "name": "Test",
            "position": 0,
            "width": 6,
            "height": 0,
            "query_config": {},
            "chart_config": {},
        }
        serializer = DashboardWidgetSerializer(data=data)
        assert not serializer.is_valid()
        assert "height" in serializer.errors

    def test_widget_serializer_valid(self):
        data = {
            "name": "Test",
            "position": 0,
            "width": 6,
            "height": 4,
            "query_config": {"metrics": []},
            "chart_config": {"chart_type": "line"},
        }
        serializer = DashboardWidgetSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_widget_serializer_query_config_must_be_dict(self):
        data = {
            "name": "Test",
            "position": 0,
            "width": 6,
            "height": 4,
            "query_config": "not a dict",
            "chart_config": {},
        }
        serializer = DashboardWidgetSerializer(data=data)
        assert not serializer.is_valid()
        assert "query_config" in serializer.errors

    def test_dashboard_create_serializer_strips_name(self):
        data = {"name": "  My Dashboard  ", "description": "test"}
        serializer = DashboardCreateUpdateSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["name"] == "My Dashboard"

    def test_dashboard_create_serializer_blank_name(self):
        data = {"name": "   ", "description": "test"}
        serializer = DashboardCreateUpdateSerializer(data=data)
        assert not serializer.is_valid()
        assert "name" in serializer.errors


# ===========================================================================
# Query Execution (mocked ClickHouse) via Dashboard query action
# ===========================================================================


class TestDashboardQueryExecution:
    def test_invalid_metric_combination_is_explicitly_degraded(self):
        builder = MagicMock()
        builder.metrics = [{"id": "unsupported", "name": "unsupported"}]
        builder.metric_info.return_value = {
            "id": "unsupported",
            "name": "unsupported",
        }
        builder.build_metric_query.side_effect = InvalidMetricCombinationError(
            "Unsupported filter combination"
        )

        results = DashboardViewSet._run_metric_queries(
            builder,
            "traces",
            MagicMock(),
        )

        metric_info, rows = results[0]
        assert rows == []
        assert metric_info["query_complete"] is False
        assert metric_info["query_status"] == "degraded"
        assert metric_info["query_error_code"] == "query_failed"
        assert metric_info["error"] == "Unsupported filter combination"

    def test_metric_read_budget_degrades_only_the_affected_metric(self):
        builder = MagicMock()
        builder.metrics = [
            {"id": "slow", "name": "slow"},
            {"id": "healthy", "name": "healthy"},
        ]
        builder.metric_info.side_effect = lambda metric: dict(metric)
        builder.build_metric_query.side_effect = lambda metric: (
            f"SELECT '{metric['id']}'",
            {},
        )

        def fetch_rows(sql, _params):
            if "slow" in sql:
                raise ReadDeadlineExceeded("deadline")
            return [{"time_bucket": "2026-08-01T00:00:00", "value": 1}]

        results = DashboardViewSet._run_metric_queries(
            builder,
            "traces",
            fetch_rows,
        )

        slow_info, slow_rows = results[0]
        healthy_info, healthy_rows = results[1]
        assert slow_rows == []
        assert slow_info["query_complete"] is False
        assert slow_info["query_status"] == "degraded"
        assert slow_info["query_error_code"] == "read_budget_exceeded"
        assert "deadline" not in slow_info["error"]
        assert healthy_info["id"] == "healthy"
        assert healthy_rows[0]["value"] == 1

    def test_metric_programming_error_still_fails_closed(self):
        builder = MagicMock()
        builder.metrics = [{"id": "broken", "name": "broken"}]
        builder.metric_info.return_value = {"id": "broken", "name": "broken"}
        builder.build_metric_query.return_value = ("SELECT broken", {})

        with pytest.raises(RuntimeError, match="compiler defect"):
            DashboardViewSet._run_metric_queries(
                builder,
                "traces",
                lambda *_args: (_ for _ in ()).throw(RuntimeError("compiler defect")),
            )

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_query_action_missing_project_ids_still_works(
        self, mock_analytics_cls, auth_client, observe_project
    ):
        """Query endpoint accepts requests without project_ids (unified picker)."""
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [{"time_bucket": "2025-01-01T00:00:00", "value": 123.45}]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                        "source": "traces",
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 200

    def test_query_action_simulation_query_defect_fails_closed(
        self,
    ):
        viewset = DashboardViewSet()
        mock_service = MagicMock()
        success_result = MagicMock()
        success_result.data = [
            {"time_bucket": "2025-01-01T00:00:00", "value": 1.0},
        ]
        mock_service.execute_ch_query.side_effect = [
            Exception("Code: 47 unknown column"),
            success_result,
        ]

        sim_config = {
            "workflow": "simulation",
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "duration",
                    "name": "duration",
                    "type": "system_metric",
                    "aggregation": "avg",
                    "source": "simulation",
                },
                {
                    "id": "success_rate",
                    "name": "success_rate",
                    "type": "system_metric",
                    "aggregation": "avg",
                    "source": "simulation",
                },
            ],
        }

        with pytest.raises(Exception, match="Code: 47 unknown column"):
            viewset._run_simulation_analytics_queries(
                mock_service,
                sim_config,
            )

        # Both independent metric reads may already be in flight, but the
        # endpoint must fail the combined response instead of publishing a
        # partial chart that hides the broken query.
        assert mock_service.execute_ch_query.call_count == 2

    @pytest.mark.integration
    @pytest.mark.django_db
    def test_query_action_eval_metric_runs_against_real_ch(
        self,
        observe_project,
        isolated_eval_usage_analytics,
    ):
        """Eval metrics read usage_apicalllog (a non-migrated legacy table).

        The v2 column rewrite must NOT rename `_peerdb_is_deleted` → `is_deleted`
        there; pre-fix this 500'd with "Identifier 'e.is_deleted' cannot be
        resolved". Hits real ClickHouse (no mock) so the SQL is actually parsed.
        """
        query_config = {
            "project_ids": [str(observe_project.id)],
            "granularity": "month",
            "time_range": {"preset": "6M"},
            "metrics": [
                {
                    "id": str(uuid.uuid4()),
                    "name": "conversation_hallucination",
                    "type": "eval_metric",
                    "source": "all",
                    "config_id": str(uuid.uuid4()),  # UUID → no DB lookup
                    "output_type": "SCORE",
                    "aggregation": "count",
                }
            ],
        }
        with (
            patch(
                "tracer.views.dashboard.V2AnalyticsQueryService",
                return_value=isolated_eval_usage_analytics,
            ),
        ):
            # Public dashboard polls intentionally return a non-chartable
            # pending envelope while the exact worker runs out of band. Drive
            # that worker path directly so this integration test still proves
            # the generated eval SQL parses on real CH25.
            response = DashboardWidgetViewSet()._execute_ch_query_config(
                query_config,
                observe_project.workspace,
                _exact_worker=True,
                cache_identity_override={
                    "workspace_id": str(observe_project.workspace_id),
                    "query_config": query_config,
                },
            )
        assert response.status_code == 200
        metrics = response.data["result"]["metrics"]
        assert len(metrics) == 1
        # Query parsed + executed cleanly; no per-widget error attached.
        assert "error" not in metrics[0]

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AnalyticsQueryService")
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    def test_filter_values_simulation_excludes_deleted_rows_and_handles_numeric_columns(
        self, _mock_enabled, mock_analytics_cls, auth_client
    ):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [{"val": 12.5}]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.get(
            "/tracer/dashboard/filter_values/?source=simulation&metric_name=duration&metric_type=system_metric"
        )

        assert response.status_code == 200
        sql = mock_service.execute_ch_query.call_args.args[0]
        assert "c.deleted = 0" in sql
        assert "c.duration_seconds IS NOT NULL" in sql
        assert "c.duration_seconds != ''" not in sql
        assert response.json()["result"]["values"] == [{"value": 12.5, "label": "12.5"}]

    @pytest.mark.django_db
    def test_filter_values_simulation_eval_metric_pages_configured_values(
        self,
        auth_client,
        organization,
        workspace,
    ):
        from model_hub.models.evals_metric import EvalTemplate
        from simulate.models import AgentDefinition
        from simulate.models.eval_config import SimulateEvalConfig
        from simulate.models.run_test import RunTest

        agent = AgentDefinition.objects.create(
            agent_name="Filter Value Agent",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+1234567002",
            inbound=True,
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        run_test = RunTest.objects.create(
            name="Filter Value Test",
            agent_definition=agent,
            organization=organization,
            workspace=workspace,
        )
        template = EvalTemplate.objects.create(
            name="Filter Value Eval",
            organization=organization,
            workspace=workspace,
            config={"output": "pass_fail"},
        )
        eval_config = SimulateEvalConfig.objects.create(
            name="Filter Value Eval Config",
            eval_template=template,
            run_test=run_test,
        )
        params = {
            "source": "simulation",
            "metric_name": str(eval_config.id),
            "metric_type": "eval_metric",
            "page_size": 1,
        }

        first = auth_client.get("/tracer/dashboard/filter_values/", params)

        assert first.status_code == 200
        first_result = first.json()["result"]
        assert first_result["values"] == [{"value": "Passed", "label": "Passed"}]
        assert first_result["has_more"] is True
        assert first_result["next_cursor"]

        second = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {**params, "cursor": first_result["next_cursor"]},
        )

        assert second.status_code == 200
        second_result = second.json()["result"]
        assert second_result["values"] == [{"value": "Failed", "label": "Failed"}]
        assert second_result["has_more"] is False
        assert second_result["next_cursor"] is None

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AnalyticsQueryService")
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    def test_filter_values_dataset_picker_keeps_active_dataset_scope(
        self, _mock_enabled, mock_analytics_cls, auth_client
    ):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = []
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.get(
            "/tracer/dashboard/filter_values/?source=datasets&metric_name=dataset&metric_type=system_metric"
        )

        assert response.status_code == 200
        sql = mock_service.execute_ch_query.call_args.args[0]
        assert "FROM model_hub_dataset FINAL" in sql
        assert "AND deleted = 0" in sql

    @pytest.mark.django_db
    @patch(
        "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields"
    )
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    def test_filter_values_session_uses_remap_survivor_values(
        self,
        _mock_enabled,
        mock_analytics_cls,
        mock_resolve_session_fields,
        auth_client,
        observe_project,
    ):
        survivor_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [{"val": survivor_id}]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service
        mock_resolve_session_fields.return_value = {
            survivor_id: {
                "external_session_id": None,
                "display_name": None,
            }
        }

        response = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {
                "source": "traces",
                "metric_name": "session",
                "metric_type": "system_metric",
                "project_ids": str(observe_project.id),
            },
        )

        assert response.status_code == 200
        assert response.json()["result"]["values"] == [
            {"value": survivor_id, "label": survivor_id}
        ]
        sql = mock_service.execute_ch_query.call_args.args[0]
        assert "FROM spans" in sql
        assert "trace_session_id_remap" in sql
        assert "filter_value_session_remap.survivor_id" in sql
        assert "latest_spans.raw_value" in sql
        assert "argMax(is_deleted, _version)" in sql

    @pytest.mark.django_db
    @patch(
        "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields"
    )
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    def test_filter_values_sessions_source_labels_session_ids(
        self,
        _mock_enabled,
        mock_analytics_cls,
        mock_resolve_session_fields,
        auth_client,
        observe_project,
    ):
        session_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [{"val": session_id}]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service
        mock_resolve_session_fields.return_value = {
            session_id: {
                "external_session_id": "session-alpha",
                "display_name": None,
            }
        }

        response = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {
                "source": "sessions",
                "metric_name": "session",
                "metric_type": "system_metric",
                "project_ids": str(observe_project.id),
            },
        )

        assert response.status_code == 200
        assert response.json()["result"]["values"] == [
            {"value": session_id, "label": "session-alpha"}
        ]

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    def test_filter_values_sessions_source_uses_span_backed_values(
        self, _mock_enabled, mock_analytics_cls, auth_client, observe_project
    ):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [{"val": "gpt-4o-mini"}]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {
                "source": "sessions",
                "metric_name": "model",
                "metric_type": "system_metric",
                "project_ids": str(observe_project.id),
            },
        )

        assert response.status_code == 200
        assert response.json()["result"]["values"] == [
            {"value": "gpt-4o-mini", "label": "gpt-4o-mini"}
        ]
        sql = mock_service.execute_ch_query.call_args.args[0]
        assert "argMax(tuple(model), _version).1 AS raw_value" in sql
        assert "WHERE latest_is_deleted = 0" in sql

    @pytest.mark.django_db
    @patch(
        "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields"
    )
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    def test_legacy_no_page_session_search_is_raw_and_reports_sample_cap(
        self,
        _mock_enabled,
        mock_analytics_cls,
        mock_resolve_session_fields,
        auth_client,
        observe_project,
    ):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [{"val": f"abc123-{index:02d}"} for index in range(21)]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service
        mock_resolve_session_fields.return_value = {}

        response = auth_client.get(
            "/tracer/dashboard/filter_values/",
            {
                "source": "traces",
                "metric_name": "session",
                "metric_type": "system_metric",
                "project_ids": str(observe_project.id),
                "search": "abc123",
            },
        )

        sql, params = mock_service.execute_ch_query.call_args.args[:2]
        assert "positionCaseInsensitiveUTF8" in sql
        assert params["filter_value_search"] == "abc123"
        assert params["result_limit"] == 21
        payload = response.json()["result"]
        assert len(payload["values"]) == 20
        assert payload["query_complete"] is False
        assert payload["query_status"] == "sampled"
        assert payload["query_error_code"] == "sample_limit"

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    def test_filter_values_session_no_search_uses_500_row_sentinel(
        self, _mock_enabled, mock_analytics_cls, auth_client, observe_project
    ):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = []
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        auth_client.get(
            "/tracer/dashboard/filter_values/",
            {
                "source": "traces",
                "metric_name": "session",
                "metric_type": "system_metric",
                "project_ids": str(observe_project.id),
            },
        )

        sql, params = mock_service.execute_ch_query.call_args.args[:2]
        assert "positionCaseInsensitiveUTF8" not in sql
        assert "filter_value_search" not in params
        assert params["result_limit"] == 501

    @pytest.mark.django_db
    def test_filter_values_annotation_annotator_returns_project_annotators(
        self, auth_client, project, user, organization, workspace
    ):
        from tracer.services.annotation_label_source import (
            AnnotationLabelScoresCH,
            AnnotationLabelScoresProjectPG,
        )

        with (
            patch.object(
                AnnotationLabelScoresProjectPG,
                "annotator_ids_for_projects",
                return_value=[str(user.id)],
            ),
            patch.object(
                AnnotationLabelScoresCH,
                "annotator_ids_for_projects",
                side_effect=AssertionError("legacy ClickHouse score source used"),
            ),
        ):
            response = auth_client.get(
                "/tracer/dashboard/filter_values/",
                {
                    "source": "traces",
                    "metric_name": "annotator",
                    "metric_type": "annotation_metric",
                    "project_ids": str(project.id),
                },
            )

        assert response.status_code == 200
        values = response.json()["result"]["values"]
        assert values == [
            {
                "value": str(user.id),
                "label": user.name,
                "name": user.name,
                "email": user.email,
                "description": user.email,
            }
        ]

    @pytest.mark.django_db
    def test_filter_values_annotation_categorical_uses_only_configured_values(
        self, auth_client, project, organization, workspace
    ):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels
        from tracer.services.annotation_label_source import (
            AnnotationLabelScoresProjectPG,
        )

        label = AnnotationsLabels.objects.create(
            name="Matrix",
            type=AnnotationTypeChoices.CATEGORICAL.value,
            organization=organization,
            workspace=workspace,
            project=project,
            settings={
                "options": [{"label": "accuracy"}, {"label": "coverage"}],
                "strategy": None,
                "auto_annotate": False,
                "multi_choice": True,
                "rule_prompt": "",
            },
        )

        with patch.object(
            AnnotationLabelScoresProjectPG,
            "categorical_values_for_label",
            side_effect=AssertionError("configured values must not scan Score history"),
            create=True,
        ):
            response = auth_client.get(
                "/tracer/dashboard/filter_values/",
                {
                    "source": "traces",
                    "metric_name": str(label.id),
                    "metric_type": "annotation_metric",
                    "project_ids": str(project.id),
                },
            )

        assert response.status_code == 200
        values = response.json()["result"]["values"]
        assert values == [
            {"value": "accuracy", "label": "accuracy"},
            {"value": "coverage", "label": "coverage"},
        ]

    @pytest.mark.django_db
    def test_filter_values_annotation_database_error_is_sanitized_503(
        self, auth_client, project, organization, workspace
    ):
        from tracer.services.annotation_label_source import (
            AnnotationLabelScoresProjectPG,
            AnnotationScoreReadUnavailable,
        )

        raw = "private database host and query diagnostics"
        with patch.object(
            AnnotationLabelScoresProjectPG,
            "annotator_ids_for_projects",
            side_effect=AnnotationScoreReadUnavailable(raw),
        ):
            response = auth_client.get(
                "/tracer/dashboard/filter_values/",
                {
                    "source": "traces",
                    "metric_name": "annotator",
                    "metric_type": "annotation_metric",
                    "project_ids": str(project.id),
                },
            )

        assert response.status_code == 503
        assert raw not in str(response.json())


class TestAnnotationLabelScoresCH:
    """Unit tests for the CH readers in AnnotationLabelScoresCH."""

    def _make_ch_client(self, captured: dict):
        mock_client = MagicMock()
        mock_client.execute_read.side_effect = lambda q, p, **kw: (
            captured.update({"query": q, "params": p, "kwargs": kw}) or ([], [], 0)
        )
        return mock_client

    def test_annotator_ids_empty_returns_empty_without_ch(self):
        from tracer.services.annotation_label_source import AnnotationLabelScoresCH

        with patch(
            "tracer.services.clickhouse.client.get_clickhouse_client"
        ) as mock_get:
            result = AnnotationLabelScoresCH().annotator_ids_for_projects([])
        mock_get.assert_not_called()
        assert result == []

    def test_annotator_ids_query_uses_ch_not_dropped_tables(self):
        from tracer.services.annotation_label_source import AnnotationLabelScoresCH

        captured: dict = {}
        mock_client = self._make_ch_client(captured)

        with patch(
            "tracer.services.clickhouse.client.get_clickhouse_client",
            return_value=mock_client,
        ):
            AnnotationLabelScoresCH().annotator_ids_for_projects(["proj-1"])

        sql = captured["query"]
        assert "FROM model_hub_score" in sql
        assert "FROM spans" in sql
        assert "project_id IN %(project_ids)s" in sql
        assert "tracer_observation_span" not in sql
        assert "tracer_trace" not in sql
        assert "tracer_trace_session" not in sql
        assert captured["kwargs"]["timeout_ms"] == (
            settings.CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS
        )


class TestDashboardTraceTimeoutSelection:
    def test_default_trace_timeout_uses_reviewed_analytics_budget(self):
        viewset = DashboardViewSet()
        timeout = viewset._get_trace_query_timeout_ms(
            {
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
                "breakdowns": [],
            }
        )
        assert timeout == _DASHBOARD_EXACT_QUERY_TIMEOUT_MS

    def test_project_breakdown_uses_longer_timeout(self):
        viewset = DashboardViewSet()
        timeout = viewset._get_trace_query_timeout_ms(
            {
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
                "breakdowns": [{"type": "system_metric", "name": "project"}],
            }
        )
        assert timeout == _DASHBOARD_EXACT_QUERY_TIMEOUT_MS

    def test_eval_metric_uses_longer_timeout(self):
        viewset = DashboardViewSet()
        timeout = viewset._get_trace_query_timeout_ms(
            {
                "metrics": [
                    {
                        "id": "eval1",
                        "name": "accuracy",
                        "type": "eval_metric",
                        "aggregation": "avg",
                    }
                ],
                "breakdowns": [],
            }
        )
        assert timeout == _DASHBOARD_EXACT_QUERY_TIMEOUT_MS


class TestDashboardMetricSourceNormalization:
    def test_simulation_custom_attribute_is_rerouted_to_traces(self):
        viewset = DashboardViewSet()
        normalized = viewset._normalize_metric_sources(
            [
                {
                    "id": "cost_breakdown.stt",
                    "type": "custom_attribute",
                    "source": "simulation",
                }
            ]
        )

        assert normalized[0]["source"] == "traces"

    def test_non_custom_simulation_metric_keeps_simulation_source(self):
        viewset = DashboardViewSet()
        normalized = viewset._normalize_metric_sources(
            [{"id": "stt_cost", "type": "system_metric", "source": "simulation"}]
        )

        assert normalized[0]["source"] == "simulation"


# ===========================================================================
# Widget Query Execution (mocked ClickHouse)
# ===========================================================================


class TestWidgetQueryExecution:
    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=False)
    def test_execute_query_clickhouse_disabled(
        self, mock_enabled, auth_client, dashboard, dashboard_widget
    ):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/query/"
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.get_clickhouse_client")
    def test_preview_query(
        self, mock_get_client, mock_enabled, auth_client, dashboard, observe_project
    ):
        mock_client = MagicMock()
        mock_client.execute_read.return_value = (
            [(datetime(2025, 1, 1), 50.0)],
            [("time_bucket", "DateTime"), ("value", "Float64")],
            3.0,
        )
        mock_get_client.return_value = mock_client

        with patch(
            "tracer.services.clickhouse.v2.query_service.get_v2_query_client",
            return_value=mock_client,
        ):
            response = auth_client.post(
                f"/tracer/dashboard/{dashboard.id}/widgets/preview/",
                {
                    "query_config": {
                        "project_ids": [str(observe_project.id)],
                        "granularity": "day",
                        "time_range": {"preset": "7D"},
                        "metrics": [
                            {
                                "id": "cost",
                                "name": "cost",
                                "type": "system_metric",
                                "aggregation": "sum",
                            }
                        ],
                    }
                },
                format="json",
            )
        assert response.status_code == 200

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    def test_preview_query_missing_config(self, mock_enabled, auth_client, dashboard):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/preview/",
            {"query_config": {}},
            format="json",
        )
        assert response.status_code == 400


# ===========================================================================
# Model tests
# ===========================================================================


class TestDashboardModel:
    @pytest.mark.django_db
    def test_dashboard_str(self, dashboard):
        assert str(dashboard) == "Test Dashboard"

    @pytest.mark.django_db
    def test_widget_str(self, dashboard_widget):
        assert "Test Dashboard" in str(dashboard_widget)
        assert "Latency Chart" in str(dashboard_widget)

    @pytest.mark.django_db
    def test_dashboard_soft_delete(self, dashboard):
        dashboard.delete()
        dashboard.refresh_from_db()
        assert dashboard.deleted is True
        assert dashboard.deleted_at is not None

    @pytest.mark.django_db
    def test_widget_cascade_visibility(self, dashboard, dashboard_widget):
        """Widgets should be filtered by deleted=False in queryset."""
        dashboard_widget.deleted = True
        dashboard_widget.save()
        active_widgets = DashboardWidget.objects.filter(
            dashboard=dashboard, deleted=False
        )
        assert active_widgets.count() == 0

    @pytest.mark.django_db
    def test_widget_default_values(self, dashboard, user):
        widget = DashboardWidget.objects.create(
            dashboard=dashboard,
            created_by=user,
        )
        assert widget.name == "Untitled"
        assert widget.width == 12
        assert widget.height == 4
        assert widget.position == 0
        assert widget.query_config == {}
        assert widget.chart_config == {}


# ===========================================================================
# Frontend Payload Simulation Tests
# ===========================================================================
# These tests simulate the exact payloads the React frontend sends
# to ensure the full round-trip works without errors.


class TestFrontendPayloadSimulation:
    """Test DashboardQueryBuilder with payloads matching what the frontend sends."""

    # --- System metrics (all aggregations) ---

    @pytest.mark.parametrize("metric_name", list(SYSTEM_METRICS.keys()))
    def test_all_system_metrics(self, metric_name):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": metric_name,
                    "name": metric_name,
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1
        sql, params, info = queries[0]
        assert "project_id IN" in sql
        assert "start_time >=" in sql
        assert info["type"] == "system_metric"

    @pytest.mark.parametrize("agg", list(AGGREGATIONS.keys()))
    def test_all_aggregations_with_latency(self, agg):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": agg,
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1

    # --- Eval metrics (frontend sends config_id as the UUID) ---

    def test_eval_metric_frontend_payload(self):
        eval_uuid = str(uuid.uuid4())
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": eval_uuid,
                    "name": "Coherence",
                    "type": "eval_metric",
                    "config_id": eval_uuid,
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1
        sql, params, _ = queries[0]
        assert "usage_apicalllog" in sql
        assert params["eval_template_id"] == eval_uuid

    # --- Annotation metrics ---

    def test_annotation_metric_frontend_payload(self):
        label_uuid = str(uuid.uuid4())
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": label_uuid,
                    "name": "Quality",
                    "type": "annotation_metric",
                    "label_id": label_uuid,
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1
        sql, params, _ = queries[0]
        assert "model_hub_score" in sql
        assert params["annotation_label_id"] == label_uuid

    # --- Custom attribute metrics ---

    def test_custom_attr_number_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "llm.token_count.prompt",
                    "name": "llm.token_count.prompt",
                    "type": "custom_attribute",
                    "attribute_key": "llm.token_count.prompt",
                    "attribute_type": "number",
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "span_attr_num" in sql
        assert "%(custom_metric_attr_key)s" in sql
        assert "llm.token_count.prompt" not in sql
        assert params["custom_metric_attr_key"] == "llm.token_count.prompt"

    def test_custom_attr_string_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "hour",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "llm.model",
                    "name": "llm.model",
                    "type": "custom_attribute",
                    "attribute_key": "llm.model",
                    "attribute_type": "string",
                    "aggregation": "count",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        # count() aggregation doesn't reference the column, just verify query builds
        assert "FROM spans" in sql
        assert "count()" in sql

    # --- Multiple metrics at once ---

    def test_mixed_metrics_frontend_payload(self):
        eval_uuid = str(uuid.uuid4())
        config = {
            "project_ids": ["proj-1", "proj-2"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                },
                {
                    "id": "cost",
                    "name": "cost",
                    "type": "system_metric",
                    "aggregation": "sum",
                },
                {
                    "id": eval_uuid,
                    "name": "Coherence",
                    "type": "eval_metric",
                    "config_id": eval_uuid,
                    "aggregation": "avg",
                },
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 3

    # --- Filters ---

    def test_system_filter_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "system_metric",
                    "metric_name": "cost",
                    "operator": "greater_than",
                    "value": "0.01",
                }
            ],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "cost" in sql
        assert params["f_0_val"] == 0.01

    def test_custom_attr_filter_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "custom_attribute",
                    "metric_name": "llm.model",
                    "operator": "contains",
                    "value": "gpt-4",
                    "attribute_type": "string",
                }
            ],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        assert "span_attr_str" in sql
        assert "llm.model" in sql

    def test_eval_filter_frontend_payload(self):
        eval_uuid = str(uuid.uuid4())
        workspace_uuid = str(uuid.uuid4())
        config = {
            "project_ids": ["proj-1"],
            "organization_id": str(uuid.uuid4()),
            "workspace_id": workspace_uuid,
            "granularity": "day",
            "time_range": {
                "custom_start": "2026-01-01T00:00:00Z",
                "custom_end": "2026-07-01T00:00:00Z",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "eval_metric",
                    "metric_name": eval_uuid,
                    "operator": "greater_than",
                    "value": "0.5",
                    "output_type": "SCORE",
                }
            ],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, params, _ = queries[0]
        compact_sql = " ".join(sql.split())

        assert "eval_score" in sql
        assert "trace_id IN" in sql
        assert "usage_apicalllog FINAL" not in sql
        assert "FROM usage_apicalllog AS usage_s_eval_filter_scan_0" in compact_sql
        assert (
            "PREWHERE usage_s_eval_filter_scan_0.workspace_id = "
            "toUUID(%(s_scope_id_0)s)"
        ) in compact_sql
        assert "usage_s_eval_filter_scan_0.created_at >= %(start_date)s" in compact_sql
        assert "usage_s_eval_filter_scan_0.created_at < %(end_date)s" in compact_sql
        assert (
            "ORDER BY usage_s_eval_filter_scan_0._peerdb_version DESC "
            "LIMIT 1 BY usage_s_eval_filter_scan_0.id"
        ) in compact_sql
        assert "usage_s_eval_filter_latest_0._peerdb_is_deleted = 0" in compact_sql
        assert "usage_s_eval_filter_latest_0.deleted = 0" in compact_sql
        assert "usage_s_eval_filter_latest_0.status = 'success'" in compact_sql
        assert params["s_scope_id_0"] == workspace_uuid
        assert params["start_date"] == datetime(2026, 1, 1, tzinfo=UTC)
        assert params["end_date"] == datetime(2026, 7, 1, tzinfo=UTC)

    def test_annotation_filter_bounds_final_before_json_value_filter(self):
        organization_uuid = str(uuid.uuid4())
        label_uuid = str(uuid.uuid4())
        config = {
            "project_ids": ["proj-1"],
            "organization_id": organization_uuid,
            "workspace_id": str(uuid.uuid4()),
            "granularity": "day",
            "time_range": {
                "custom_start": "2026-01-01T00:00:00Z",
                "custom_end": "2026-07-01T00:00:00Z",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [
                {
                    "metric_type": "annotation_metric",
                    "metric_name": label_uuid,
                    "operator": "greater_than",
                    "value": "0.5",
                }
            ],
            "breakdowns": [],
        }

        sql, params, _ = DashboardQueryBuilder(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert (
            "FROM model_hub_score AS annotation_s_filter_0 FINAL PREWHERE"
            in compact_sql
        )
        assert (
            "annotation_s_filter_0.label_id = toUUID(%(s_label_id_0)s)" in compact_sql
        )
        assert (
            "annotation_s_filter_0.organization_id = toUUID(%(s_ann_org_id_0)s)"
        ) in compact_sql
        assert "annotation_s_filter_0.created_at >= %(start_date)s" in compact_sql
        assert "annotation_s_filter_0.created_at < %(end_date)s" in compact_sql
        assert "annotation_s_filter_0._peerdb_is_deleted = 0" in compact_sql
        assert "annotation_s_filter_0.deleted = 0" in compact_sql
        assert "direct_annotation.trace_id IS NOT NULL" in compact_sql
        assert "observation_span_id AS observation_span_id" in compact_sql
        assert "SELECT DISTINCT annotation_membership.trace_id" in compact_sql
        assert params["s_ann_org_id_0"] == organization_uuid
        assert params["s_label_id_0"] == label_uuid
        assert params["start_date"] == datetime(2026, 1, 1, tzinfo=UTC)
        assert params["end_date"] == datetime(2026, 7, 1, tzinfo=UTC)

    # --- Breakdowns ---

    def test_breakdown_model_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [{"name": "model", "type": "system_metric"}],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "breakdown_value" in sql
        assert "model" in sql

    def test_breakdown_custom_attr_frontend_payload(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "breakdowns": [
                {
                    "name": "llm.model",
                    "type": "custom_attribute",
                    "attribute_type": "string",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        sql, _, _ = queries[0]
        assert "span_attr_str" in sql
        assert "breakdown_value" in sql

    # --- Time ranges ---

    @pytest.mark.parametrize(
        "preset", ["30m", "6h", "today", "yesterday", "7D", "30D", "3M", "6M", "12M"]
    )
    def test_all_time_presets(self, preset):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": preset},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1

    # --- Edge cases ---

    def test_empty_filters_and_breakdowns(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 1

    def test_five_metrics_max(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": name,
                    "name": name,
                    "type": "system_metric",
                    "aggregation": "avg",
                }
                for name in ["latency", "cost", "tokens", "error_rate", "input_tokens"]
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 5

    def test_format_results_full_roundtrip(self):
        config = {
            "project_ids": ["proj-1"],
            "granularity": "day",
            "time_range": {
                "custom_start": "2025-01-01T00:00:00",
                "custom_end": "2025-01-03T23:59:59",
            },
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                },
                {
                    "id": "cost",
                    "name": "cost",
                    "type": "system_metric",
                    "aggregation": "sum",
                },
            ],
        }
        builder = DashboardQueryBuilder(config)
        queries = builder.build_all_queries()
        assert len(queries) == 2

        # Simulate ClickHouse returning data
        mock_results = [
            (
                queries[0][2],  # metric_info
                [
                    {"time_bucket": datetime(2025, 1, 1), "value": 100.5},
                    {"time_bucket": datetime(2025, 1, 2), "value": 120.3},
                ],
            ),
            (
                queries[1][2],
                [
                    {"time_bucket": datetime(2025, 1, 1), "value": 0.05},
                    {"time_bucket": datetime(2025, 1, 2), "value": 0.08},
                ],
            ),
        ]
        result = builder.format_results(mock_results)

        assert "metrics" in result
        assert len(result["metrics"]) == 2
        assert result["granularity"] == "day"
        assert result["metrics"][0]["name"] == "latency"
        assert result["metrics"][0]["unit"] == "ms"
        assert result["metrics"][1]["name"] == "cost"
        assert result["metrics"][1]["unit"] == "$"
        # 3 day buckets (Jan 1-3), 2 with data + 1 filled with null
        data = result["metrics"][0]["series"][0]["data"]
        assert len(data) == 3
        non_null = [d for d in data if d["value"] is not None]
        assert len(non_null) == 2


# ===========================================================================
# Security and Edge Case Tests
# ===========================================================================


class TestQueryBuilderSecurity:
    """Security tests for DashboardQueryBuilder to prevent injection and misuse."""

    def test_unknown_metric_name_falls_back_to_custom_attribute(
        self, sample_query_config
    ):
        """Verify that passing an unknown metric_name falls back to custom attribute query."""
        sample_query_config["metrics"] = [
            {
                "name": "nonexistent_metric",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ]
        builder = DashboardQueryBuilder(sample_query_config)
        sql, params = builder.build_metric_query(sample_query_config["metrics"][0])
        # Falls back to custom attribute — queries span_attr_num map
        assert "span_attr_num" in sql or "span_attr_str" in sql

    def test_sql_injection_in_metric_name_blocked(self, sample_query_config):
        """Verify that a SQL injection attempt in metric_name is safely handled."""
        sample_query_config["metrics"] = [
            {
                "name": "1; DROP TABLE spans--",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ]
        builder = DashboardQueryBuilder(sample_query_config)
        # Falls back to custom attribute, which rejects unsafe attribute keys
        with pytest.raises(ValueError, match="Invalid attribute key"):
            builder.build_metric_query(sample_query_config["metrics"][0])

    def test_like_metacharacters_escaped(self):
        """Verify that _coerce_filter_value escapes % in LIKE patterns."""
        result = _coerce_filter_value("100%", "str_contains")
        assert result == "%100\\%%"

    def test_like_underscore_escaped(self):
        """Verify that _coerce_filter_value escapes underscore in LIKE patterns."""
        result = _coerce_filter_value("test_val", "str_contains")
        assert "\\_" in result
        assert result == "%test\\_val%"

    def test_filter_value_parameterized_not_interpolated(self, sample_query_config):
        """Verify filter values go through %(param)s placeholders, not f-string interpolation."""
        sample_query_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "latency",
                "operator": "greater_than",
                "value": 100,
            }
        ]
        builder = DashboardQueryBuilder(sample_query_config)
        sql, params = builder.build_metric_query(sample_query_config["metrics"][0])
        # The SQL should use %(f_0_val)s placeholder, not the raw value
        assert "%(f_0_val)s" in sql
        assert "f_0_val" in params

    def test_aggregation_fallback_uses_avg(self, sample_query_config):
        """Verify unknown aggregation falls back to avg safely."""
        sample_query_config["metrics"] = [
            {
                "name": "latency",
                "type": "system_metric",
                "aggregation": "unknown_agg_xyz",
            }
        ]
        builder = DashboardQueryBuilder(sample_query_config)
        sql, _ = builder.build_metric_query(sample_query_config["metrics"][0])
        # AGGREGATIONS.get("unknown_agg_xyz", "avg({col})") falls back to avg
        assert "avg(" in sql


class TestQueryBuilderEdgeCases:
    """Edge case tests for DashboardQueryBuilder."""

    def test_empty_metrics_list(self, sample_query_config):
        """Verify build_all_queries handles empty metrics gracefully."""
        sample_query_config["metrics"] = []
        builder = DashboardQueryBuilder(sample_query_config)
        results = builder.build_all_queries()
        assert results == []

    def test_single_metric_no_breakdown(self, sample_query_config):
        """Basic case with one metric, no filters, no breakdowns."""
        sample_query_config["filters"] = []
        sample_query_config["breakdowns"] = []
        builder = DashboardQueryBuilder(sample_query_config)
        results = builder.build_all_queries()
        assert len(results) == 1
        sql, params, info = results[0]
        assert "time_bucket" in sql
        assert "breakdown_value" not in sql
        assert info["name"] == "latency"

    def test_max_series_cap(self, sample_query_config):
        """Verify format_results caps at MAX_SERIES (100)."""
        sample_query_config["time_range"] = {
            "custom_start": "2025-01-01T00:00:00",
            "custom_end": "2025-01-02T00:00:00",
        }
        sample_query_config["breakdowns"] = [{"name": "model", "type": "system_metric"}]
        builder = DashboardQueryBuilder(sample_query_config)
        # Generate 150 breakdown values
        rows = [
            {
                "time_bucket": datetime(2025, 1, 1),
                "value": float(i),
                "breakdown_value": f"model-{i}",
            }
            for i in range(150)
        ]
        result = builder.format_results(
            [({"id": "latency", "name": "latency", "aggregation": "avg"}, rows)]
        )
        series = result["metrics"][0]["series"]
        assert len(series) <= 100

    def test_zero_total_in_pie_data(self, sample_query_config):
        """Verify no division by zero when all values are zero."""
        sample_query_config["time_range"] = {
            "custom_start": "2025-01-01T00:00:00",
            "custom_end": "2025-01-02T00:00:00",
        }
        builder = DashboardQueryBuilder(sample_query_config)
        rows = [
            {"time_bucket": datetime(2025, 1, 1), "value": 0},
        ]
        result = builder.format_results(
            [({"id": "latency", "name": "latency", "aggregation": "avg"}, rows)]
        )
        # Should complete without error
        assert result["metrics"][0]["series"][0]["data"][0]["value"] == 0

    def test_custom_date_range_parsing(self, sample_query_config):
        """Verify custom start/end dates are parsed correctly."""
        sample_query_config["time_range"] = {
            "custom_start": "2024-06-15T10:30:00",
            "custom_end": "2024-06-20T18:00:00",
        }
        builder = DashboardQueryBuilder(sample_query_config)
        start, end = builder.parse_time_range()
        assert start.year == 2024
        assert start.month == 6
        assert start.day == 15
        assert start.hour == 10
        assert end.day == 20
        assert end.hour == 18

    def test_minute_granularity_generates_correct_buckets(self):
        """Verify bucket count for 1-hour range with minute granularity."""
        start = datetime(2025, 1, 1, 0, 0, 0)
        end = datetime(2025, 1, 1, 1, 0, 0)
        buckets = _generate_time_buckets(start, end, "minute")
        # 0:00 through 1:00 inclusive = 61 buckets
        assert len(buckets) == 61

    def test_very_large_time_range_buckets(self):
        """Verify 12M with minute granularity produces output (potentially large but bounded)."""
        start = datetime(2024, 1, 1, 0, 0, 0)
        end = datetime(2024, 1, 2, 0, 0, 0)  # 1 day at minute granularity
        buckets = _generate_time_buckets(start, end, "minute")
        # 1440 minutes in a day + 1 for inclusive end
        assert len(buckets) == 1441

    def test_preset_ranges_all_valid(self):
        """Verify all PRESET_RANGES produce valid (start, end) tuples."""
        for preset_key in PRESET_RANGES:
            config = {
                "project_ids": ["test-project"],
                "granularity": "day",
                "time_range": {"preset": preset_key},
                "metrics": [],
            }
            builder = DashboardQueryBuilder(config)
            start, end = builder.parse_time_range()
            assert isinstance(start, datetime)
            assert isinstance(end, datetime)
            assert start <= end, f"Preset {preset_key}: start > end"

    def test_granularity_to_ch_mapping(self):
        """Verify all granularities map to valid ClickHouse functions."""
        expected_functions = {
            "minute": "toStartOfMinute",
            "hour": "toStartOfHour",
            "day": "toStartOfDay",
            "week": "toMonday",
            "month": "toStartOfMonth",
            "year": "toStartOfYear",
        }
        for gran, expected_fn in expected_functions.items():
            assert GRANULARITY_TO_CH[gran] == expected_fn


class TestDashboardQuerySerializer:
    """Tests for the DashboardQuerySerializer validation."""

    def test_valid_query_config_passes(self):
        """Verify a fully valid query config passes serializer validation."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [
                {"name": "latency", "type": "system_metric", "aggregation": "avg"}
            ],
        }
        serializer = DashboardQuerySerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["allow_sampled"] is False

        opted_in = DashboardQuerySerializer(data={**data, "allow_sampled": True})
        assert opted_in.is_valid(), opted_in.errors
        assert opted_in.validated_data["allow_sampled"] is True

    def test_numeric_custom_metric_infers_number_when_frontend_omits_type(self):
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "6M"},
            "granularity": "month",
            "metrics": [
                {
                    "name": "call.total_turns",
                    "type": "custom_attribute",
                    "aggregation": "avg",
                    "attribute_key": "call.total_turns",
                }
            ],
        }

        serializer = DashboardQuerySerializer(data=data)

        assert serializer.is_valid(), serializer.errors
        metric = serializer.validated_data["metrics"][0]
        assert metric["attribute_type"] == "number"
        sql, params = DashboardQueryBuilderV2(
            serializer.validated_data
        ).build_metric_query(metric)
        compact_sql = "".join(sql.split())
        assert "avg(metric_value)" in sql
        assert "latest_custom_metric_spans AS" in sql
        assert "FROM spans FINAL" not in sql
        assert "mapContains(" in sql
        assert (
            "custom_metric_source.attrs_number[%(custom_metric_attr_key)s]"
            in compact_sql
        )
        assert "tupleElement(latest_metric_state, 1) = 0" in sql
        assert "tupleElement(latest_metric_state, 3) = 1" in sql
        assert "GROUP BY\n                    custom_metric_source.project_id," in sql
        assert params["custom_metric_attr_key"] == "call.total_turns"

    @pytest.mark.parametrize("aggregation", ["min", "max"])
    def test_min_max_custom_metric_infer_numeric_map_when_type_is_omitted(
        self, aggregation
    ):
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "6M"},
            "granularity": "month",
            "metrics": [
                {
                    "name": "call.total_turns",
                    "type": "custom_attribute",
                    "aggregation": aggregation,
                    "attribute_key": "call.total_turns",
                }
            ],
        }

        serializer = DashboardQuerySerializer(data=data)

        assert serializer.is_valid(), serializer.errors
        metric = serializer.validated_data["metrics"][0]
        assert metric["attribute_type"] == "number"
        sql, params = DashboardQueryBuilderV2(
            serializer.validated_data
        ).build_metric_query(metric)
        assert f"{aggregation}(metric_value)" in sql
        assert "latest_custom_metric_spans AS" in sql
        assert "FROM spans FINAL" not in sql
        assert params["custom_metric_attr_key"] == "call.total_turns"

    def test_text_custom_metric_count_keeps_string_default_when_type_is_omitted(self):
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [
                {
                    "name": "final_status",
                    "type": "custom_attribute",
                    "aggregation": "count_distinct",
                    "attribute_key": "final_status",
                }
            ],
        }

        serializer = DashboardQuerySerializer(data=data)

        assert serializer.is_valid(), serializer.errors
        metric = serializer.validated_data["metrics"][0]
        assert metric["attribute_type"] == "string"
        sql, _params = DashboardQueryBuilderV2(
            serializer.validated_data
        ).build_metric_query(metric)
        assert "uniqExact(attrs_string[%(custom_metric_attr_key)s])" in sql

    def test_canonical_filters_with_source_metadata_pass(self):
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [
                {
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                    "filters": [
                        {
                            "column_id": "status",
                            "source": "traces",
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": "in",
                                "filter_value": ["OK"],
                                "col_type": "SYSTEM_METRIC",
                            },
                        }
                    ],
                }
            ],
            "filters": [
                {
                    "column_id": "latency",
                    "source": "traces",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 100,
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            ],
        }
        serializer = DashboardQuerySerializer(data=data)

        assert serializer.is_valid(), serializer.errors
        normalized = _normalize_dashboard_query_filters(serializer.validated_data)
        assert normalized["filters"][0] == {
            "metric_type": "system_metric",
            "metric_name": "latency",
            "operator": "greater_than",
            "value": 100,
            "source": "traces",
            "canonical_filter": {
                "column_id": "latency",
                "source": "traces",
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 100,
                    "col_type": "SYSTEM_METRIC",
                },
            },
        }
        assert normalized["metrics"][0]["filters"][0]["operator"] == "contains"

    def test_legacy_dashboard_filter_shape_fails_global_serializer(self):
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [
                {"name": "latency", "type": "system_metric", "aggregation": "avg"}
            ],
            "filters": [
                {
                    "metric_type": "system_metric",
                    "metric_name": "latency",
                    "operator": "greater_than",
                    "value": 100,
                }
            ],
        }
        serializer = DashboardQuerySerializer(data=data)

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_legacy_dashboard_filter_shape_fails_metric_serializer(self):
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [
                {
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                    "source": "traces",
                    "filters": [
                        {
                            "metric_type": "system_metric",
                            "metric_name": "latency",
                            "operator": "greater_than",
                            "value": 100,
                        }
                    ],
                }
            ],
        }
        serializer = DashboardQuerySerializer(data=data)

        assert not serializer.is_valid()
        assert "metrics" in serializer.errors

    def test_read_query_serializer_restores_legacy_metric_filters(self):
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [
                {
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                    "source": "traces",
                    "filters": [
                        {
                            "metric_name": "status",
                            "metric_type": "system_metric",
                            "operator": "equal_to",
                            "source": "traces",
                            "value": "OK",
                        }
                    ],
                }
            ],
        }

        serializer = DashboardReadQuerySerializer(data=data)

        assert serializer.is_valid(), serializer.errors
        restored = serializer.validated_data["metrics"][0]["filters"][0]
        assert restored["column_id"] == "status"
        assert restored["filter_config"] == {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "OK",
            "col_type": "SYSTEM_METRIC",
        }

    def test_metric_rejects_camel_case_display_name(self):
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [
                {
                    "name": "latency",
                    "displayName": "Latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                    "source": "traces",
                }
            ],
        }
        serializer = DashboardQuerySerializer(data=data)

        assert not serializer.is_valid()
        assert "metrics" in serializer.errors

    def test_breakdown_rejects_camel_case_output_type(self):
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [
                {
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                    "source": "traces",
                }
            ],
            "breakdowns": [
                {
                    "name": "quality",
                    "type": "eval_metric",
                    "source": "traces",
                    "outputType": "CHOICE",
                }
            ],
        }
        serializer = DashboardQuerySerializer(data=data)

        assert not serializer.is_valid()
        assert "breakdowns" in serializer.errors

    def test_missing_metrics_fails(self):
        """Verify missing metrics field fails validation."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
        }
        serializer = DashboardQuerySerializer(data=data)
        assert not serializer.is_valid()
        assert "metrics" in serializer.errors

    def test_empty_metrics_list_fails(self):
        """Verify empty metrics list fails validation (min_length=1)."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [],
        }
        serializer = DashboardQuerySerializer(data=data)
        assert not serializer.is_valid()
        assert "metrics" in serializer.errors

    def test_too_many_metrics_fails(self):
        """Verify >5 metrics fails validation (max_length=5)."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "day",
            "metrics": [
                {"name": f"m{i}", "type": "system_metric", "aggregation": "avg"}
                for i in range(6)
            ],
        }
        serializer = DashboardQuerySerializer(data=data)
        assert not serializer.is_valid()
        assert "metrics" in serializer.errors

    def test_invalid_granularity_fails(self):
        """Verify an invalid granularity value fails validation."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "time_range": {"preset": "7D"},
            "granularity": "microsecond",
            "metrics": [
                {"name": "latency", "type": "system_metric", "aggregation": "avg"}
            ],
        }
        serializer = DashboardQuerySerializer(data=data)
        assert not serializer.is_valid()
        assert "granularity" in serializer.errors

    def test_missing_time_range_uses_default(self):
        """Verify missing time_range fails validation (required=True)."""
        data = {
            "workflow": "observability",
            "project_ids": ["proj-1"],
            "granularity": "day",
            "metrics": [
                {"name": "latency", "type": "system_metric", "aggregation": "avg"}
            ],
        }
        serializer = DashboardQuerySerializer(data=data)
        assert not serializer.is_valid()
        assert "time_range" in serializer.errors


class TestFilterOperators:
    """Tests for FILTER_OPERATORS templates producing valid SQL patterns."""

    def test_all_filter_operators_produce_valid_sql(self):
        """Iterate FILTER_OPERATORS dict, verify each template produces valid SQL."""
        for op_name, template in FILTER_OPERATORS.items():
            # Templates with format placeholders need prefix and idx
            if "{prefix}" in template and "{idx}" in template:
                result = template.format(prefix="f_", idx=0)
            else:
                result = template
            # Should produce a non-empty string
            assert len(result) > 0, f"Operator {op_name} produced empty SQL"
            # Should not contain un-replaced format placeholders
            assert "{" not in result, (
                f"Operator {op_name} has unresolved placeholder: {result}"
            )

    def test_between_operator_requires_two_values(self, sample_query_config):
        """Verify between operator with non-list value is skipped."""
        sample_query_config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "latency",
                "operator": "between",
                "value": "single_value",  # Should be a list of 2
            }
        ]
        builder = DashboardQueryBuilder(sample_query_config)
        sql, params = builder.build_metric_query(sample_query_config["metrics"][0])
        # Should not have BETWEEN since value is not a list of 2
        assert "BETWEEN" not in sql

    def test_string_contains_case_insensitive(self):
        """Verify str_contains uses LIKE (case-insensitive matching via _coerce_filter_value)."""
        assert "LIKE" in FILTER_OPERATORS["str_contains"]

    def test_is_set_operator_generates_not_null(self):
        """Verify is_set produces != '' (NOT NULL equivalent for strings)."""
        assert FILTER_OPERATORS["is_set"] == "!= ''"

    def test_is_not_set_operator_generates_null(self):
        """Verify is_not_set produces = '' (NULL equivalent for strings)."""
        assert FILTER_OPERATORS["is_not_set"] == "= ''"


class TestDashboardQueryBuilderBase:
    """Tests for the DashboardQueryBuilderBase shared base class."""

    def test_base_class_build_metric_query_raises_not_implemented(self):
        """Verify that calling build_metric_query on the base class raises NotImplementedError."""
        config = {
            "granularity": "day",
            "metrics": [
                {"name": "test", "type": "system_metric", "aggregation": "avg"}
            ],
        }
        base = DashboardQueryBuilderBase(config)
        with pytest.raises(NotImplementedError):
            base.build_metric_query(config["metrics"][0])

    def test_base_class_build_all_queries_dispatches_to_subclass(self):
        """Verify build_all_queries calls build_metric_query for each metric."""

        class TestSubclass(DashboardQueryBuilderBase):
            def build_metric_query(self, metric):
                return f"SELECT 1 -- {metric['name']}", {"key": "val"}

            def parse_time_range(self):
                return datetime(2025, 1, 1), datetime(2025, 1, 2)

        config = {
            "granularity": "day",
            "metrics": [
                {"name": "metric_a", "type": "system_metric", "aggregation": "avg"},
                {"name": "metric_b", "type": "system_metric", "aggregation": "sum"},
            ],
        }
        builder = TestSubclass(config)
        results = builder.build_all_queries()
        assert len(results) == 2
        assert "metric_a" in results[0][0]
        assert "metric_b" in results[1][0]
        assert results[0][2]["name"] == "metric_a"
        assert results[1][2]["name"] == "metric_b"

    def test_format_metric_result_basic(self):
        """Verify _format_metric_result produces correct structure with basic data."""
        config = {
            "granularity": "day",
            "metrics": [],
            "breakdowns": [],
        }

        base = DashboardQueryBuilderBase(config)
        # Buckets must use UTC-aware ISO format to match _build_series_data output
        all_buckets = [
            datetime(2025, 1, 1, tzinfo=UTC).isoformat(),
            datetime(2025, 1, 2, tzinfo=UTC).isoformat(),
        ]
        metric_info = {"id": "latency", "name": "latency", "aggregation": "avg"}
        rows = [
            {"time_bucket": datetime(2025, 1, 1), "value": 42.5},
        ]
        result = base._format_metric_result(
            metric_info, rows, all_buckets, {"latency": "ms"}
        )
        assert result["name"] == "latency"
        assert result["unit"] == "ms"
        assert len(result["series"]) == 1
        assert result["series"][0]["name"] == "total"
        assert len(result["series"][0]["data"]) == 2
        assert result["series"][0]["data"][0]["value"] == 42.5

    def test_format_metric_result_with_name_map(self):
        """Verify _format_metric_result resolves breakdown values via name_map."""
        config = {
            "granularity": "day",
            "metrics": [],
            "breakdowns": [{"name": "project"}],
        }
        base = DashboardQueryBuilderBase(config)
        all_buckets = [datetime(2025, 1, 1).isoformat()]
        metric_info = {"id": "latency", "name": "latency", "aggregation": "avg"}
        rows = [
            {
                "time_bucket": datetime(2025, 1, 1),
                "value": 50.0,
                "breakdown_value": "uuid-123",
            },
        ]
        name_map = {"uuid-123": "My Project"}
        result = base._format_metric_result(
            metric_info,
            rows,
            all_buckets,
            {"latency": "ms"},
            name_map=name_map,
            name_map_breakdown="project",
        )
        series_names = [s["name"] for s in result["series"]]
        assert "My Project" in series_names

    def test_format_metric_result_uses_metric_id_for_unit_lookup(self):
        config = {
            "granularity": "day",
            "metrics": [],
            "breakdowns": [],
        }
        base = DashboardQueryBuilderBase(config)
        all_buckets = [datetime(2025, 1, 1).isoformat()]
        metric_info = {"id": "duration", "name": "Duration", "aggregation": "avg"}
        rows = [{"time_bucket": datetime(2025, 1, 1), "value": 42.5}]
        result = base._format_metric_result(
            metric_info, rows, all_buckets, {"duration": "s"}
        )
        assert result["name"] == "Duration"
        assert result["unit"] == "s"


# ===========================================================================
# v2 rewrite routing + invalid-combination handling
# ===========================================================================


def _single_metric_config(metric, breakdowns=None):
    return {
        "project_ids": ["11111111-1111-1111-1111-111111111111"],
        "organization_id": "22222222-2222-2222-2222-222222222222",
        "workspace_id": "33333333-3333-3333-3333-333333333333",
        "allow_sampled": True,
        "granularity": "day",
        "time_range": {"preset": "7D"},
        "metrics": [metric],
        "filters": [],
        "breakdowns": breakdowns or [],
    }


class TestDashboardV2RewriteRouting:
    def test_system_metric_rewritten_to_v2_columns(self):
        config = _single_metric_config(
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        )
        sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        assert "is_deleted" in sql
        assert "_peerdb_is_deleted" not in sql
        assert "use_skip_indexes_if_final = 0" in sql
        assert "use_skip_indexes_if_final = 1" not in sql

    def test_bounded_legacy_filter_subqueries_preserve_legacy_cdc_columns(self):
        eval_uuid = str(uuid.uuid4())
        annotation_uuid = str(uuid.uuid4())
        config = _single_metric_config(
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        )
        config["time_range"] = {
            "custom_start": "2026-01-01T00:00:00Z",
            "custom_end": "2026-07-01T00:00:00Z",
        }
        config["filters"] = [
            {
                "metric_type": "eval_metric",
                "metric_name": eval_uuid,
                "operator": "greater_than",
                "value": "0.5",
                "output_type": "SCORE",
            },
            {
                "metric_type": "annotation_metric",
                "metric_name": annotation_uuid,
                "operator": "greater_than",
                "value": "0.5",
            },
        ]

        sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert "usage_apicalllog FINAL" not in sql
        assert "usage_s_eval_filter_scan_0.created_at >= %(start_date)s" in compact_sql
        assert "usage_s_eval_filter_scan_0.created_at < %(end_date)s" in compact_sql
        assert "usage_s_eval_filter_scan_0._peerdb_version" in sql
        assert "usage_s_eval_filter_latest_0._peerdb_is_deleted = 0" in sql
        assert "usage_s_eval_filter_scan_0._version" not in sql
        assert "usage_s_eval_filter_latest_0.is_deleted" not in sql

        assert (
            "FROM model_hub_score AS annotation_s_filter_1 FINAL PREWHERE"
            in compact_sql
        )
        assert "annotation_s_filter_1.created_at >= %(start_date)s" in compact_sql
        assert "annotation_s_filter_1.created_at < %(end_date)s" in compact_sql
        assert "annotation_s_filter_1._peerdb_is_deleted = 0" in sql
        assert "annotation_s_filter_1.is_deleted" not in sql

        # The same rewrite must still target the CH25 spans source.
        assert "is_deleted = 0" in sql
        assert "use_skip_indexes_if_final = 0" in sql

    def test_settings_appended_exactly_once(self):
        config = _single_metric_config(
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        )
        sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        assert sql.count("use_skip_indexes_if_final") == 1
        assert sql.count("SETTINGS") == 1

    def test_annotation_breakdown_uses_ch25_spans_start_time_partition(self):
        config = _single_metric_config(
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            },
            breakdowns=[
                {
                    "name": "44444444-4444-4444-4444-444444444444",
                    "type": "annotation_metric",
                    "output_type": "text",
                }
            ],
        )

        sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "LEFT JOIN model_hub_score AS ann0" in sql
        assert "s.start_time >= %(start_date)s" in sql
        assert "s.start_time < %(end_date)s" in sql
        assert "s.created_at >=" not in sql
        assert " AND created_at >= %(start_date)s" not in sql
        assert "ann0._peerdb_is_deleted = 0" in sql
        assert "ann0.is_deleted = 0" not in sql

    def test_annotation_metric_uses_bounded_direct_latest_live_trace_scope(self):
        config = _single_metric_config(
            {
                "id": "44444444-4444-4444-4444-444444444444",
                "name": "quality",
                "type": "annotation_metric",
                "label_id": "44444444-4444-4444-4444-444444444444",
                "output_type": "text",
                "aggregation": "count",
            }
        )

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert "trace_dict" not in sql
        assert "FROM traces AS trace_project_scan" in compact_sql
        assert "INNER JOIN ( SELECT DISTINCT" in compact_sql
        assert "FROM model_hub_score AS annotation_trace_candidate" in compact_sql
        assert (
            "annotation_trace_candidate.organization_id = "
            "toUUID(%(annotation_organization_id)s)"
        ) in compact_sql
        assert (
            "annotation_trace_candidate.label_id = toUUID(%(annotation_label_id)s)"
        ) in compact_sql
        assert "annotation_trace_candidate.created_at >= %(start_date)s" in compact_sql
        assert "annotation_trace_candidate.created_at < %(end_date)s" in compact_sql
        assert (
            "PREWHERE trace_project_scan.project_id IN %(project_ids)s" in compact_sql
        )
        assert "argMax(" in compact_sql
        assert "trace_project_scan.is_deleted" in compact_sql
        assert "GROUP BY trace_project_scan.id" in compact_sql
        assert (
            "uniqExact(trace_project_scan.project_id) AS project_identity_count"
            in compact_sql
        )
        assert "WHERE project_identity_count = 1" in compact_sql
        assert "AND tupleElement(latest_state, 2) = 0" in compact_sql
        assert (
            "LEFT JOIN ( SELECT trace_id, tupleElement(latest_state, 1) AS project_id"
            in compact_sql
        )
        assert "ON annotation_trace_project.trace_id = a.trace_id" in compact_sql
        assert "annotation_trace_project.project_id IN %(project_ids)s" in compact_sql
        assert "FROM model_hub_score AS annotation_span_candidate" in compact_sql
        assert (
            "annotation_span_candidate.organization_id = "
            "toUUID(%(annotation_organization_id)s)"
        ) in compact_sql
        assert (
            "annotation_span_candidate.label_id = toUUID(%(annotation_label_id)s)"
        ) in compact_sql
        assert "annotation_span_candidate.created_at >= %(start_date)s" in compact_sql
        assert "annotation_span_candidate.created_at < %(end_date)s" in compact_sql
        assert (
            "PREWHERE annotation_span_scan.project_id IN %(project_ids)s" in compact_sql
        )
        assert "WHERE annotation_span_scan.id IN (" in compact_sql
        assert (
            "uniqExact( tuple( annotation_span_scan.project_id, "
            "annotation_span_scan.trace_id ) ) AS identity_count"
        ) in compact_sql
        assert "annotation_span_latest.identity_count = 1" in compact_sql
        assert "a.trace_id IN (" not in compact_sql
        assert params["annotation_organization_id"] == config["organization_id"]

    def test_annotation_metric_applies_global_and_metric_span_filters_bounded(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "column_id": "routing",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "map",
                        "filter_op": "contains",
                        "filter_value": {"tier": "gold"},
                    },
                },
                {
                    "column_id": "status",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "ERROR",
                    },
                },
            ],
        }
        config = _normalize_dashboard_query_filters(
            {
                **_single_metric_config(metric),
                "filters": [
                    {
                        "column_id": "is_final",
                        "filter_config": {
                            "col_type": "SPAN_ATTRIBUTE",
                            "filter_type": "boolean",
                            "filter_op": "equals",
                            "filter_value": True,
                        },
                    }
                ],
            }
        )

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert "AS annotation_subject_span" in compact_sql
        assert "AS annotation_subject_candidate" in compact_sql
        assert "dashboard_filter_candidate_identities AS" in compact_sql
        assert "SELECT DISTINCT s.trace_id FROM spans AS s FINAL" not in compact_sql
        assert "LEFT JOIN ( SELECT * FROM spans AS s FINAL" not in compact_sql
        assert ") AS s PREWHERE s.project_id IN %(project_ids)s" in compact_sql
        assert "dashboard_replay_source._version DESC" in compact_sql
        assert "LIMIT 1 BY" in compact_sql
        assert "PREWHERE s.project_id IN %(project_ids)s" in compact_sql
        assert "s.trace_id IN (" in compact_sql
        assert "(s.parent_span_id IS NULL OR s.parent_span_id = '')" in compact_sql
        assert "s.is_deleted = 0" in compact_sql
        assert "mapContains(s.attrs_bool" in compact_sql
        assert "JSONExtractRaw(s.attributes_extra" in compact_sql
        assert "s.status = %(_ann_span_filter_2_value)s" in compact_sql
        assert params["_ann_span_filter_2_value"] == "ERROR"
        assert params["latest_filter_key_0"] == "is_final"
        assert params["latest_filter_key_1"] == "routing"

    def test_annotation_system_string_filter_keeps_numeric_value_as_string(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "metric_type": "system_metric",
                    "metric_name": "status",
                    "operator": "equal_to",
                    "value": "123",
                }
            ],
        }

        sql, params, _ = DashboardQueryBuilderV2(
            _single_metric_config(metric)
        ).build_all_queries()[0]

        assert "s.status = %(_ann_span_filter_0_value)s" in sql
        assert params["_ann_span_filter_0_value"] == "123"
        assert isinstance(params["_ann_span_filter_0_value"], str)

    def test_annotation_metric_applies_bounded_eval_and_annotation_filters(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        eval_id = "55555555-5555-4555-8555-555555555555"
        other_label_id = "66666666-6666-4666-8666-666666666666"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "metric_type": "annotation_metric",
                    "metric_name": other_label_id,
                    "operator": "greater_than",
                    "value": 0.2,
                }
            ],
        }
        config = _single_metric_config(metric)
        config["filters"] = [
            {
                "metric_type": "eval_metric",
                "metric_name": eval_id,
                "operator": "greater_than",
                "value": 0.5,
                "output_type": "SCORE",
            }
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        subject_expr = (
            "if(a.trace_id IS NOT NULL, toString(a.trace_id), "
            "annotation_subject_span.trace_id) IN ("
        )
        assert compact_sql.count(subject_expr) == 2
        assert "FROM usage_apicalllog AS usage_ann_metric_eval_filter_scan_0" in sql
        assert "FROM model_hub_score AS annotation_ann_metric_filter_1 FINAL" in sql
        assert "created_at >= %(start_date)s" in sql
        assert "created_at < %(end_date)s" in sql
        assert params["ann_metric_eval_id_0"] == eval_id
        assert params["ann_metric_label_id_1"] == other_label_id

    def test_v1_annotation_system_filter_uses_string_safe_root_expression(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "metric_type": "system_metric",
                    "metric_name": "session",
                    "operator": "equal_to",
                    "value": "customer-session",
                }
            ],
        }
        config = _single_metric_config(metric)

        sql, params = DashboardQueryBuilder(config).build_metric_query(metric)
        compact_sql = " ".join(sql.split())

        assert "FROM spans AS s FINAL" in compact_sql
        assert "toString(s.trace_session_id) = %(_ann_span_filter_0_value)s" in (
            compact_sql
        )
        assert "(s.parent_span_id IS NULL OR s.parent_span_id = '')" in compact_sql
        assert params["_ann_span_filter_0_value"] == "customer-session"

    def test_v2_annotation_id_remap_filter_fails_closed(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "metric_type": "system_metric",
                    "metric_name": "session",
                    "operator": "equal_to",
                    "value": "customer-session",
                }
            ],
        }
        config = _single_metric_config(metric)

        with pytest.raises(
            InvalidMetricCombinationError,
            match="Resolved user/session filters are not supported",
        ):
            DashboardQueryBuilderV2(config).build_metric_query(metric)

    def test_v2_annotation_project_filter_uses_ui_source_and_string_expression(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        project_id = "11111111-1111-4111-8111-111111111111"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "metric_type": "system_metric",
                    "metric_name": "project",
                    "operator": "equal_to",
                    "value": project_id,
                    "source": "all",
                }
            ],
        }
        config = _single_metric_config(metric)

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert "FROM spans AS s FINAL" in compact_sql
        assert "toString(s.project_id) = %(_ann_span_filter_0_value)s" in compact_sql
        assert params["_ann_span_filter_0_value"] == project_id

    def test_annotation_eval_choice_filter_uses_string_output_and_ui_source(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        eval_id = "55555555-5555-4555-8555-555555555555"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
        }
        config = _single_metric_config(metric)
        config["filters"] = [
            {
                "metric_type": "eval_metric",
                "metric_name": eval_id,
                "operator": "equal_to",
                "value": "Approved",
                "output_type": "CHOICES",
                "source": "all",
            }
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "usage_ann_metric_eval_filter_latest_0.eval_output_str =" in sql
        assert params["ann_metric_0_val"] == "Approved"

    def test_annotation_categorical_membership_filter_uses_string_value(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "metric_type": "annotation_metric",
                    "metric_name": "66666666-6666-4666-8666-666666666666",
                    "operator": "equal_to",
                    "value": "Approved",
                    "output_type": "categorical",
                    "source": "both",
                }
            ],
        }
        config = _single_metric_config(metric)

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert (
            "notEmpty(JSONExtract(annotation_ann_metric_filter_0.value, 'selected', "
            "'Array(String)')) AND has(JSONExtract("
        ) in " ".join(sql.split())
        assert params["ann_metric_0_val"] == "Approved"

    def test_annotation_text_contains_filter_escapes_like_value(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "metric_type": "annotation_metric",
                    "metric_name": "66666666-6666-4666-8666-666666666666",
                    "operator": "str_contains",
                    "value": "50%_done",
                    "output_type": "text",
                    "source": "both",
                }
            ],
        }
        config = _single_metric_config(metric)

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert (
            "JSONExtract(annotation_ann_metric_filter_0.value, 'text', "
            "'Nullable(String)') LIKE %(ann_metric_0_val)s"
        ) in " ".join(sql.split())
        assert params["ann_metric_0_val"] == r"%50\%\_done%"

    def test_annotation_categorical_negative_requires_a_stored_selection(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "metric_type": "annotation_metric",
                    "metric_name": "66666666-6666-4666-8666-666666666666",
                    "operator": "not_equal_to",
                    "value": "Rejected",
                    "output_type": "categorical",
                    "source": "both",
                }
            ],
        }

        sql, _, _ = DashboardQueryBuilderV2(
            _single_metric_config(metric)
        ).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert "notEmpty(JSONExtract(" in compact_sql
        assert "AND NOT has(JSONExtract(" in compact_sql

    def test_annotation_categorical_contains_uses_array_overlap(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "metric_type": "annotation_metric",
                    "metric_name": "66666666-6666-4666-8666-666666666666",
                    "operator": "contains",
                    "value": ["Approved", "Escalated"],
                    "output_type": "categorical",
                    "source": "both",
                }
            ],
        }

        sql, params, _ = DashboardQueryBuilderV2(
            _single_metric_config(metric)
        ).build_all_queries()[0]

        assert "hasAny(JSONExtract(" in " ".join(sql.split())
        assert params["ann_metric_0_val"] == ["Approved", "Escalated"]

        metric["filters"][0].update({"operator": "str_contains", "value": "Approved"})
        exact_sql, exact_params, _ = DashboardQueryBuilderV2(
            _single_metric_config(metric)
        ).build_all_queries()[0]
        compact_exact_sql = " ".join(exact_sql.split())
        assert "notEmpty(JSONExtract(" in compact_exact_sql
        assert "AND has(JSONExtract(" in compact_exact_sql
        assert " LIKE " not in compact_exact_sql
        assert exact_params["ann_metric_0_val"] == "Approved"

    def test_annotation_thumbs_filter_normalizes_ui_tokens(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "metric_type": "annotation_metric",
                    "metric_name": "66666666-6666-4666-8666-666666666666",
                    "operator": "contains",
                    "value": ["Thumbs Up", "thumbs_down"],
                    "output_type": "thumbs_up_down",
                    "source": "both",
                }
            ],
        }

        sql, params, _ = DashboardQueryBuilderV2(
            _single_metric_config(metric)
        ).build_all_queries()[0]

        assert (
            "JSONExtract(annotation_ann_metric_filter_0.value, 'value', "
            "'Nullable(String)') IN %(ann_metric_0_val)s"
        ) in " ".join(sql.split())
        assert params["ann_metric_0_val"] == ["up", "down"]

    def test_annotation_membership_maps_observation_rows_without_fanout(self):
        label_id = "44444444-4444-4444-4444-444444444444"
        metric = {
            "id": label_id,
            "name": "quality",
            "type": "annotation_metric",
            "label_id": label_id,
            "output_type": "text",
            "aggregation": "count",
            "filters": [
                {
                    "metric_type": "annotation_metric",
                    "metric_name": "66666666-6666-4666-8666-666666666666",
                    "operator": "equal_to",
                    "value": "Approved",
                    "output_type": "categorical",
                    "source": "both",
                }
            ],
        }

        sql, _, _ = DashboardQueryBuilderV2(
            _single_metric_config(metric)
        ).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert "WITH annotation_ann_metric_candidates_0 AS" in compact_sql
        assert "observation_span_id AS observation_span_id" in compact_sql
        assert "UNION ALL" in compact_sql
        assert "SELECT DISTINCT annotation_membership.trace_id" in compact_sql
        assert "annotation_ann_metric_span_filter_latest_0.identity_count = 1" in (
            compact_sql
        )
        assert (
            "uniqExact(trace_project_scan.project_id) AS project_identity_count"
            in compact_sql
        )
        assert "WHERE project_identity_count = 1" in compact_sql

    def test_v1_annotation_metric_preserves_trace_dictionary_scope(self):
        config = _single_metric_config(
            {
                "id": "44444444-4444-4444-4444-444444444444",
                "name": "quality",
                "type": "annotation_metric",
                "label_id": "44444444-4444-4444-4444-444444444444",
                "output_type": "text",
                "aggregation": "count",
            }
        )

        sql, params, _ = DashboardQueryBuilder(config).build_all_queries()[0]

        assert "dictGet('trace_dict', 'project_id', a.trace_id)" in sql
        assert "FROM traces AS trace_project_scan" not in sql
        assert params["annotation_organization_id"] == config["organization_id"]

    def test_user_breakdown_reads_bounded_direct_end_users_not_dictionary(self):
        config = _single_metric_config(
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            },
            breakdowns=[{"name": "user", "type": "system_metric"}],
        )

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert "end_users_dict" not in sql
        assert "FROM end_users AS eu FINAL" in compact_sql
        assert "WHERE eu.project_id IN %(project_ids)s" in compact_sql
        assert "AND eu.is_deleted = 0" in compact_sql
        assert params["project_ids"] == config["project_ids"]

    def test_user_breakdown_resolves_exact_and_remapped_ids_without_fanout(self):
        config = _single_metric_config(
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            },
            breakdowns=[{"name": "user_id_type", "type": "system_metric"}],
        )

        sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        # The one request-scoped survivor map is reused for both span ids and
        # curated dimension rows; it never materializes a tenant-global window.
        assert compact_sql.count("end_user_id_remap FINAL") == 2
        assert "dashboard_candidate_end_user_ids AS" in compact_sql
        assert "FROM spans PREWHERE project_id IN %(project_ids)s" in compact_sql
        assert "OVER (PARTITION BY new_id)" not in compact_sql
        assert "AS eu_remap ON sp.end_user_id = eu_remap.any_id" in compact_sql
        assert (
            "AS eu_dimension_remap ON eu.end_user_id = eu_dimension_remap.any_id"
        ) in compact_sql
        assert "GROUP BY project_id, resolved_end_user_id" in compact_sql
        assert (
            "ON sp.project_id = eu_dimension.project_id AND "
            "if(eu_remap.survivor_id IS NULL OR "
            "eu_remap.survivor_id = toUUID(" in compact_sql
        )
        # Prefer an exact survivor row; fall back to the newest live member of
        # its many-to-one remap group.
        assert "tuple(eu.end_user_id = if(" in compact_sql
        assert "eu.version)) AS user_id" in compact_sql

    def test_user_dimension_tombstones_are_removed_after_latest_state(self):
        config = _single_metric_config(
            {
                "id": "user_count",
                "name": "user_count",
                "type": "system_metric",
                "aggregation": "count",
            }
        )

        sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert "end_users_dict" not in sql
        # FINAL must be applied before the live-row predicate: filtering before
        # version collapse could resurrect an older label after a tombstone.
        assert (
            "FROM end_users AS eu FINAL LEFT JOIN" in compact_sql
            and "WHERE eu.project_id IN %(project_ids)s AND eu.is_deleted = 0"
            in compact_sql
        )
        assert "uniqExact(if(" in compact_sql
        assert "eu_dimension.user_id" in compact_sql

    def test_user_filter_uses_direct_dimension_and_keeps_external_id_semantics(self):
        config = _single_metric_config(
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "user",
                "operator": "equal_to",
                "value": "customer@example.com",
            }
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "end_users_dict" not in sql
        assert "FROM end_users AS eu FINAL" in sql
        assert "if(user_id = '', toString(end_user_id), user_id)" in sql
        assert params["f_0_val"] == "customer@example.com"
        assert params["direct_user_filter_0_val"] == "customer@example.com"
        compact_sql = " ".join(sql.split())
        assert "(sp.project_id, sp.end_user_id) IN (" in compact_sql
        assert "FROM end_users AS filtered_eu FINAL" in compact_sql
        assert "filtered_eu.project_id IN %(project_ids)s" in compact_sql
        assert "filtered_eu.is_deleted = 0" in compact_sql
        assert (
            "GROUP BY project_id, resolved_end_user_id HAVING "
            "if(curated_user_id = ''" in compact_sql
        )
        assert "matched_user.project_id AS project_id" in compact_sql
        assert "AS user_filter_physical_map" in compact_sql
        assert "filtered_dimension_candidate.project_id IN %(project_ids)s" in (
            compact_sql
        )
        assert "OVER (PARTITION BY new_id)" not in compact_sql
        assert "PREWHERE sp.project_id IN %(project_ids)s" in compact_sql
        assert "sp.start_time >= %(start_date)s" in compact_sql
        assert "sp.start_time < %(end_date)s" in compact_sql
        assert "WHERE sp.is_deleted = 0" in compact_sql
        assert "trace_session_id_remap" not in compact_sql

    def test_session_only_query_does_not_read_end_user_remap(self):
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "session",
                "operator": "equal_to",
                "value": str(uuid.uuid4()),
            }
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "trace_session_id_remap" in sql
        assert "end_user_id_remap" not in sql
        assert "AS session_filter_physical_map" in sql
        assert "old_id IN %(direct_session_filter_uuids)s" in sql
        assert "new_id IN %(direct_session_filter_uuids)s" in sql
        assert "OVER (PARTITION BY new_id)" not in sql
        assert "sp.trace_session_id IN (" in sql
        assert "PREWHERE sp.project_id IN %(project_ids)s" in sql
        assert len(params["direct_session_filter_uuids"]) == 1

    def test_combined_user_and_session_query_reads_both_remaps(self):
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "user",
                "operator": "equal_to",
                "value": "customer@example.com",
            },
            {
                "metric_type": "system_metric",
                "metric_name": "session",
                "operator": "equal_to",
                "value": str(uuid.uuid4()),
            },
        ]

        sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "trace_session_id_remap" in sql
        assert "end_user_id_remap" in sql
        assert "session_filter_physical_map" in sql
        assert "user_filter_physical_map" in sql
        assert "OVER (PARTITION BY new_id)" not in sql

    def test_invalid_positive_session_filter_short_circuits_physical_scan(self):
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "session",
                "operator": "equal_to",
                "value": "not-a-uuid",
            }
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "AND (0)" in sql
        assert "direct_session_filter_uuids" not in params

    def test_large_session_candidate_set_keeps_exact_outer_plan(self):
        session_ids = [str(uuid.uuid4()) for _ in range(65)]
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "session",
                "operator": "contains",
                "value": session_ids,
            }
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "session_filter_physical_map" not in sql
        assert "direct_session_filter_uuids" not in params
        assert params["f_0_val"] == session_ids

    def test_user_dimension_identity_is_project_scoped(self):
        config = _single_metric_config(
            {
                "id": "user_count",
                "name": "user_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["project_ids"] = [str(uuid.uuid4()), str(uuid.uuid4())]

        sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert "eu.project_id AS project_id" in compact_sql
        assert "GROUP BY project_id, resolved_end_user_id" in compact_sql
        assert "ON sp.project_id = eu_dimension.project_id" in compact_sql

    def test_negative_user_filter_keeps_exact_enrichment_without_broad_prefilter(self):
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "user",
                "operator": "not_contains",
                "value": ["customer@example.com"],
            }
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "user_filter_physical_map" not in sql
        assert "direct_user_filter_0_val" not in params
        assert params["f_0_val"] == ["customer@example.com"]

    def test_multiple_positive_user_filters_share_a_finite_physical_candidate_set(self):
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "user",
                "operator": "contains",
                "value": ["customer@example.com", "second@example.com"],
            },
            {
                "metric_type": "system_metric",
                "metric_name": "user",
                "operator": "equal_to",
                "value": "customer@example.com",
            },
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert compact_sql.count("AS user_filter_physical_map") == 1
        assert "curated_user_id) IN %(direct_user_filter_0_val)s" in compact_sql
        assert "curated_user_id) = %(direct_user_filter_1_val)s" in compact_sql
        assert params["direct_user_filter_0_val"] == [
            "customer@example.com",
            "second@example.com",
        ]
        assert params["direct_user_filter_1_val"] == "customer@example.com"

    def test_numeric_looking_user_ids_remain_string_typed(self):
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "user",
                "operator": "contains",
                "value": ["123", 456],
            },
            {
                "metric_type": "system_metric",
                "metric_name": "user",
                "operator": "equal_to",
                "value": "123",
            },
        ]

        _, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert params["direct_user_filter_0_val"] == ["123", "456"]
        assert params["direct_user_filter_1_val"] == "123"
        assert params["f_0_val"] == ["123", "456"]
        assert params["f_1_val"] == "123"

    def test_string_contains_preserves_zero_value(self):
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "status",
                "operator": "str_contains",
                "value": 0,
            }
        ]

        _, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert params["f_0_val"] == "%0%"

    @pytest.mark.parametrize(
        "metric_name", ("trace_count", "span_count", "session_count", "user_count")
    )
    def test_identifier_count_filters_keep_numeric_value_as_string(self, metric_name):
        config = _single_metric_config(
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": metric_name,
                "operator": "equal_to",
                "value": "123",
            }
        ]

        _, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert params["f_0_val"] == "123"
        assert isinstance(params["f_0_val"], str)

    def test_uuid_user_filter_preserves_missing_dimension_fallback(self):
        fallback_user_id = str(uuid.uuid4())
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "user",
                "operator": "contains",
                "value": [fallback_user_id],
            }
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        compact_sql = " ".join(sql.split())

        assert "OR sp.end_user_id IN (" in compact_sql
        assert "AS fallback_user_physical_map" in compact_sql
        assert "WHERE survivor_id IN %(direct_user_fallback_uuids)s" in compact_sql
        assert "old_id IN %(direct_user_fallback_uuids)s" in compact_sql
        assert "OVER (PARTITION BY new_id)" not in compact_sql
        assert params["direct_user_fallback_uuids"] == (fallback_user_id,)
        assert params["direct_user_fallback_uuid_0"] == fallback_user_id

    def test_partial_user_filter_does_not_use_membership_prefilter(self):
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "user",
                "operator": "str_contains",
                "value": "customer",
            }
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "user_filter_physical_map" not in sql
        assert "direct_user_filter_0_val" not in params

    def test_untyped_user_filter_does_not_become_inner_only(self):
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_name": "user",
                "operator": "equal_to",
                "value": "customer@example.com",
            }
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "user_filter_physical_map" not in sql
        assert "direct_user_filter_0_val" not in params

    def test_large_uuid_user_set_disables_partial_prefilter(self):
        user_ids = [str(uuid.uuid4()) for _ in range(65)]
        config = _single_metric_config(
            {
                "id": "trace_count",
                "name": "trace_count",
                "type": "system_metric",
                "aggregation": "count_distinct",
            }
        )
        config["filters"] = [
            {
                "metric_type": "system_metric",
                "metric_name": "user",
                "operator": "contains",
                "value": user_ids,
            }
        ]

        sql, params, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]

        assert "user_filter_physical_map" not in sql
        assert "direct_user_filter_0_val" not in params
        assert params["f_0_val"] == user_ids

    def test_eval_metric_keeps_legacy_columns(self):
        config = _single_metric_config(
            {
                "id": str(uuid.uuid4()),
                "name": "hallucination",
                "type": "eval_metric",
                "config_id": str(uuid.uuid4()),  # UUID → no DB lookup
                "output_type": "SCORE",
                "aggregation": "count",
            }
        )
        sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        assert "usage_apicalllog" in sql
        assert "e._peerdb_is_deleted" in sql
        assert "e.is_deleted" not in sql
        assert "usage_main_scan._peerdb_version" in sql
        assert "usage_main_scan._version" not in sql
        assert "usage_main_latest._peerdb_is_deleted" in sql
        assert "usage_main_latest.is_deleted" not in sql

    def test_eval_metric_with_spans_breakdown_rewrites_spans_refs(self):
        config = _single_metric_config(
            {
                "id": str(uuid.uuid4()),
                "name": "hallucination",
                "type": "eval_metric",
                "config_id": str(uuid.uuid4()),
                "output_type": "SCORE",
                "aggregation": "count",
            },
            breakdowns=[{"name": "provider", "type": "system_metric"}],
        )
        sql, _, _ = DashboardQueryBuilderV2(config).build_all_queries()[0]
        # Spans JOIN present, its _peerdb_is_deleted rewritten to is_deleted
        assert "s.is_deleted" in sql or "is_deleted = 0" in sql
        assert "s._peerdb_is_deleted" not in sql
        # Legacy alias untouched
        assert "e._peerdb_is_deleted" in sql
        assert "e.is_deleted" not in sql


class TestInvalidMetricCombination:
    def test_cataloged_unknown_system_metric_fails_closed(self):
        config = _single_metric_config(
            {
                "id": "unknown_dimension",
                "name": "unknown_dimension",
                "property_id": "system_attribute:traces:unknown_dimension",
                "type": "system_metric",
                "aggregation": "count",
            }
        )

        with pytest.raises(
            InvalidMetricCombinationError,
            match="Unsupported cataloged system metric",
        ):
            DashboardQueryBuilder(config).build_all_queries()

    def test_cataloged_unknown_system_filter_fails_closed(self):
        config = _single_metric_config(
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
                "filters": [
                    {
                        "metric_type": "system_metric",
                        "metric_name": "unknown_dimension",
                        "property_id": "system_attribute:traces:unknown_dimension",
                        "operator": "equal_to",
                        "value": "value",
                    }
                ],
            }
        )

        with pytest.raises(
            InvalidMetricCombinationError,
            match="Unsupported cataloged system filter",
        ):
            DashboardQueryBuilder(config).build_all_queries()

    def test_cataloged_unknown_system_breakdown_fails_closed(self):
        config = _single_metric_config(
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            },
            breakdowns=[
                {
                    "name": "unknown_dimension",
                    "property_id": "system_attribute:traces:unknown_dimension",
                    "type": "system_metric",
                    "source": "traces",
                }
            ],
        )

        with pytest.raises(
            InvalidMetricCombinationError,
            match="Unsupported cataloged system breakdown",
        ):
            DashboardQueryBuilder(config).build_all_queries()

    @pytest.mark.parametrize(
        ("field", "value", "error_role"),
        [
            (
                "filters",
                [
                    {
                        "metric_type": "system_metric",
                        "metric_name": "unknown_dimension",
                        "property_id": "system_attribute:traces:unknown_dimension",
                        "operator": "equal_to",
                        "value": "value",
                    }
                ],
                "filter",
            ),
            (
                "breakdowns",
                [
                    {
                        "name": "unknown_dimension",
                        "property_id": "system_attribute:traces:unknown_dimension",
                        "type": "system_metric",
                        "source": "traces",
                    }
                ],
                "breakdown",
            ),
        ],
    )
    def test_eval_metric_rejects_unknown_cataloged_system_dimension(
        self,
        field,
        value,
        error_role,
    ):
        eval_id = "44444444-4444-4444-4444-444444444444"
        metric = {
            "id": eval_id,
            "name": "quality",
            "type": "eval_metric",
            "config_id": eval_id,
            "output_type": "SCORE",
            "aggregation": "count",
        }
        if field == "filters":
            metric["filters"] = value
            config = _single_metric_config(metric)
        else:
            config = _single_metric_config(metric, breakdowns=value)

        with pytest.raises(
            InvalidMetricCombinationError,
            match=f"Unsupported cataloged system {error_role}",
        ):
            DashboardQueryBuilder(config).build_all_queries()

    def test_avg_of_text_attribute_raises(self):
        config = _single_metric_config(
            {
                "id": "bot_wpm",
                "name": "bot_wpm",
                "type": "custom_attribute",
                "aggregation": "avg",
                "attribute_key": "bot_wpm",
                "attribute_type": "string",
            }
        )
        with pytest.raises(InvalidMetricCombinationError):
            DashboardQueryBuilder(config).build_all_queries()

    def test_count_of_text_attribute_is_allowed(self):
        config = _single_metric_config(
            {
                "id": "bot_wpm",
                "name": "bot_wpm",
                "type": "custom_attribute",
                "aggregation": "count_distinct",
                "attribute_key": "bot_wpm",
                "attribute_type": "string",
            }
        )
        sql, params, _ = DashboardQueryBuilder(config).build_all_queries()[0]
        assert "span_attr_str[%(custom_metric_attr_key)s]" in sql
        assert params["custom_metric_attr_key"] == "bot_wpm"

    @pytest.mark.parametrize("aggregation", ["min", "max"])
    def test_min_max_of_text_attribute_raise(self, aggregation):
        config = _single_metric_config(
            {
                "id": "final_status",
                "name": "final_status",
                "type": "custom_attribute",
                "aggregation": aggregation,
                "attribute_key": "final_status",
                "attribute_type": "string",
            }
        )

        with pytest.raises(InvalidMetricCombinationError):
            DashboardQueryBuilder(config).build_all_queries()

    def test_avg_of_numeric_attribute_is_allowed(self):
        config = _single_metric_config(
            {
                "id": "bot_wpm",
                "name": "bot_wpm",
                "type": "custom_attribute",
                "aggregation": "avg",
                "attribute_key": "bot_wpm",
                "attribute_type": "number",
            }
        )
        sql, params, _ = DashboardQueryBuilder(config).build_all_queries()[0]
        assert "avg(span_attr_num[%(custom_metric_attr_key)s])" in sql
        assert params["custom_metric_attr_key"] == "bot_wpm"

    def test_format_metric_result_surfaces_error(self):
        base = DashboardQueryBuilderBase(
            {"granularity": "day", "metrics": [], "breakdowns": []}
        )
        all_buckets = [datetime(2025, 1, 1).isoformat()]
        metric_info = {
            "id": "bot_wpm",
            "name": "bot_wpm",
            "aggregation": "avg",
            "error": "'avg' can't be applied to the text attribute 'bot_wpm'.",
        }
        result = base._format_metric_result(metric_info, [], all_buckets, {})
        assert result["error"].startswith("'avg' can't be applied")


class TestWidgetReorder:
    """POST /dashboard/<pk>/widgets/reorder/ — previously untested endpoint."""

    def _make_widget(self, dashboard, user, name, position):
        return DashboardWidget.objects.create(
            dashboard=dashboard,
            name=name,
            position=position,
            width=6,
            height=4,
            query_config={},
            chart_config={},
            created_by=user,
        )

    @pytest.mark.django_db
    def test_reorder_persists_new_positions(self, auth_client, dashboard, user):
        w0 = self._make_widget(dashboard, user, "A", 0)
        w1 = self._make_widget(dashboard, user, "B", 1)
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/reorder/",
            {"order": [str(w1.id), str(w0.id)]},
            format="json",
        )
        assert response.status_code == 200
        w0.refresh_from_db()
        w1.refresh_from_db()
        assert w1.position == 0
        assert w0.position == 1

    @pytest.mark.django_db
    def test_reorder_clamps_width_to_1_12(self, auth_client, dashboard, user):
        wide = self._make_widget(dashboard, user, "Wide", 0)
        narrow = self._make_widget(dashboard, user, "Narrow", 1)
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/reorder/",
            {
                "order": [
                    {"id": str(wide.id), "width": 99},
                    {"id": str(narrow.id), "width": 0},
                ]
            },
            format="json",
        )
        assert response.status_code == 200
        wide.refresh_from_db()
        narrow.refresh_from_db()
        assert wide.width == 12
        assert narrow.width == 1

    @pytest.mark.django_db
    def test_reorder_non_list_order_returns_400(self, auth_client, dashboard):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/reorder/",
            {"order": "not-a-list"},
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_reorder_ignores_foreign_widget_ids(self, auth_client, dashboard, user):
        w0 = self._make_widget(dashboard, user, "A", 0)
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/reorder/",
            {"order": [str(uuid.uuid4()), str(w0.id)]},
            format="json",
        )
        assert response.status_code == 200
        w0.refresh_from_db()
        # foreign id occupies index 0 and is skipped; own widget takes index 1
        assert w0.position == 1


class TestWidgetDuplicate:
    """POST /dashboard/<pk>/widgets/<pk>/duplicate/ — previously untested endpoint."""

    @pytest.mark.django_db
    def test_duplicate_copies_config_name_and_position(
        self, auth_client, dashboard, dashboard_widget
    ):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/duplicate/",
            {},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == f"{dashboard_widget.name} (Copy)"
        assert data["position"] == dashboard_widget.position + 1
        assert data["width"] == dashboard_widget.width
        assert data["query_config"] == dashboard_widget.query_config
        assert (
            DashboardWidget.objects.filter(dashboard=dashboard, deleted=False).count()
            == 2
        )


class TestDashboardWorkspaceIsolation:
    """A dashboard in another workspace must be invisible (404) to this workspace."""

    def _other_ws_dashboard(self, organization, user):
        other_ws = Workspace.objects.create(
            name="Other workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        return Dashboard.objects.create(
            workspace=other_ws,
            name="Other WS Dashboard",
            created_by=user,
            updated_by=user,
        )

    @pytest.mark.django_db
    def test_retrieve_other_workspace_dashboard_is_blocked(
        self, auth_client, organization, user
    ):
        other = self._other_ws_dashboard(organization, user)
        response = auth_client.get(f"/tracer/dashboard/{other.id}/")
        assert response.status_code == 400
        assert "Other WS Dashboard" not in response.content.decode()

    @pytest.mark.django_db
    def test_update_other_workspace_dashboard_is_blocked(
        self, auth_client, organization, user
    ):
        other = self._other_ws_dashboard(organization, user)
        response = auth_client.put(
            f"/tracer/dashboard/{other.id}/",
            {"name": "Hijacked"},
            format="json",
        )
        assert response.status_code == 400
        other.refresh_from_db()
        assert other.name == "Other WS Dashboard"

    @pytest.mark.django_db
    def test_delete_other_workspace_dashboard_is_blocked(
        self, auth_client, organization, user
    ):
        other = self._other_ws_dashboard(organization, user)
        response = auth_client.delete(f"/tracer/dashboard/{other.id}/")
        assert response.status_code == 400
        other.refresh_from_db()
        assert other.deleted is False


class TestWidgetConfigPersistence:
    """A saved widget must keep its query_config."""

    @pytest.mark.django_db
    def test_widget_update_persists_query_config(
        self, auth_client, dashboard, dashboard_widget
    ):
        new_config = {
            "project_ids": [str(uuid.uuid4())],
            "granularity": "hour",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "error_rate",
                    "name": "error_rate",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        }
        response = auth_client.put(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/",
            {
                "name": dashboard_widget.name,
                "position": dashboard_widget.position,
                "width": dashboard_widget.width,
                "height": dashboard_widget.height,
                "query_config": new_config,
                "chart_config": dashboard_widget.chart_config,
            },
            format="json",
        )
        assert response.status_code == 200
        dashboard_widget.refresh_from_db()
        assert dashboard_widget.query_config["granularity"] == "hour"
        assert dashboard_widget.query_config["metrics"][0]["name"] == "error_rate"


class TestDashboardTimeRangeValidation:
    def test_custom_start_without_custom_end_rejected(self):
        from tracer.serializers.dashboard import DashboardTimeRangeSerializer

        serializer = DashboardTimeRangeSerializer(
            data={"custom_start": "2026-01-01T00:00:00Z"}
        )
        assert not serializer.is_valid()

    def test_neither_preset_nor_custom_rejected(self):
        from tracer.serializers.dashboard import DashboardTimeRangeSerializer

        serializer = DashboardTimeRangeSerializer(data={})
        assert not serializer.is_valid()


class TestMetricsCatalogPagination:
    def test_manual_catalog_actions_disable_drf_pagination(self):
        from tracer.views.dashboard import DashboardViewSet

        assert DashboardViewSet.metrics.kwargs["pagination_class"] is None
        assert DashboardViewSet.filter_values.kwargs["pagination_class"] is None

    def test_response_contract_covers_legacy_and_paginated_shapes(self):
        from tracer.serializers.dashboard import (
            DashboardMetricsCatalogResultSerializer,
        )

        legacy = DashboardMetricsCatalogResultSerializer(data={"metrics": []})
        assert legacy.is_valid(), legacy.errors

        paginated = DashboardMetricsCatalogResultSerializer(
            data={
                "metrics": [],
                "total": 401,
                "page": 2,
                "page_size": 200,
                "has_more": True,
            }
        )
        assert paginated.is_valid(), paginated.errors
        assert paginated.validated_data["total"] == 401
        assert paginated.validated_data["has_more"] is True

    @pytest.mark.django_db
    def test_pagination_returns_page_metadata(self, auth_client):
        response = auth_client.get("/tracer/dashboard/metrics/?page=1&page_size=5")
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["page"] == 1
        assert result["page_size"] == 5
        assert "total" in result
        assert "has_more" in result
        assert len(result["metrics"]) <= 5

    @pytest.mark.django_db
    def test_later_page_preserves_exact_total_and_exhaustion(self, auth_client):
        catalog = [{"name": f"metric-{index}"} for index in range(5)]
        with patch(
            "tracer.views.dashboard.build_metrics_catalog_page",
            return_value=(catalog[4:], 5, False),
        ):
            response = auth_client.get("/tracer/dashboard/metrics/?page=3&page_size=2")

        assert response.status_code == 200
        result = response.json()["result"]
        assert result == {
            "metrics": [{"name": "metric-4"}],
            "total": 5,
            "page": 3,
            "page_size": 2,
            "has_more": False,
        }

    @pytest.mark.django_db
    def test_page_size_over_200_is_rejected(self, auth_client):
        response = auth_client.get("/tracer/dashboard/metrics/?page=1&page_size=999")
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_garbage_page_is_rejected(self, auth_client):
        response = auth_client.get("/tracer/dashboard/metrics/?page=abc&page_size=5")
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_unknown_query_parameter_is_rejected(self, auth_client):
        response = auth_client.get(
            "/tracer/dashboard/metrics/?page=1&page_size=5&surprise=true"
        )
        assert response.status_code == 400

    def test_query_serializer_enforces_positive_bounded_pages(self):
        from tracer.serializers.dashboard import (
            DashboardMetricsCatalogQuerySerializer,
        )

        assert not DashboardMetricsCatalogQuerySerializer(
            data={"page": 0, "page_size": 50}
        ).is_valid()
        assert not DashboardMetricsCatalogQuerySerializer(
            data={"page": 1, "page_size": 201}
        ).is_valid()
        valid = DashboardMetricsCatalogQuerySerializer(
            data={"page": 1, "page_size": 200}
        )
        assert valid.is_valid(), valid.errors
        unknown = DashboardMetricsCatalogQuerySerializer(data={"surprise": True})
        assert not unknown.is_valid()
        assert "surprise" in unknown.errors
        invalid_project = DashboardMetricsCatalogQuerySerializer(
            data={"project_ids": "not-a-uuid"}
        )
        assert not invalid_project.is_valid()
        assert "project_ids" in invalid_project.errors

    def test_page_family_walk_counts_all_but_reads_only_boundary_slices(self):
        from tracer.services.clickhouse.read_budget import ReadDeadline
        from tracer.services.dashboard_metrics_catalog import (
            _CatalogPageFamily,
            _paginate_catalog_families,
        )

        count_calls = []
        read_calls = []

        def family(name, rows):
            return _CatalogPageFamily(
                name=name,
                count_rows=lambda: count_calls.append(name) or len(rows),
                read_rows=lambda offset, limit: (
                    read_calls.append((name, offset, limit))
                    or rows[offset : offset + limit]
                ),
            )

        rows, total, has_more = _paginate_catalog_families(
            [
                family("system_metrics", [{"name": "s0"}, {"name": "s1"}]),
                family(
                    "eval_metrics",
                    [
                        {"name": "e0"},
                        {"name": "e1"},
                        {"name": "e2"},
                        {"name": "e3"},
                    ],
                ),
                family(
                    "annotation_metrics",
                    [{"name": "a0"}, {"name": "a1"}],
                ),
            ],
            page=2,
            page_size=4,
            deadline=ReadDeadline.start(8_500),
        )

        assert count_calls == [
            "system_metrics",
            "eval_metrics",
            "annotation_metrics",
        ]
        assert read_calls == [
            ("eval_metrics", 2, 2),
            ("annotation_metrics", 0, 2),
        ]
        assert [row["name"] for row in rows] == ["e2", "e3", "a0", "a1"]
        assert total == 8
        assert has_more is False

    def test_page_first_order_does_not_change_with_python_casefold_page_size(self):
        from tracer.services.clickhouse.read_budget import ReadDeadline
        from tracer.services.dashboard_metrics_catalog import build_metrics_catalog_page

        workspace = SimpleNamespace(id="workspace-order", organization=object())
        preordered = [
            {
                "name": "z",
                "display_name": "ss",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
            },
            {
                "name": "a",
                "display_name": "ß",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
            },
        ]

        def read_page(page, page_size):
            with (
                patch(
                    "tracer.services.dashboard_metrics_catalog._resolve_metrics_catalog_project_scope",
                    return_value=([], False),
                ),
                patch(
                    "tracer.services.dashboard_metrics_catalog.build_metrics_catalog",
                    return_value=[dict(row) for row in preordered],
                ),
                patch(
                    "tracer.services.dashboard_metrics_catalog._run_metrics_catalog_pg_snapshot",
                    side_effect=lambda _deadline, read: read(),
                ),
            ):
                rows, _total, _has_more = build_metrics_catalog_page(
                    workspace,
                    page=page,
                    page_size=page_size,
                    include_custom_attributes=False,
                    category="system_metric",
                    deadline=ReadDeadline.start(8_500),
                )
            return [row["display_name"] for row in rows]

        assert read_page(1, 2) == read_page(1, 1) + read_page(2, 1)
        assert read_page(1, 2) == ["ss", "ß"]

    def test_required_family_count_failure_reads_no_payload(self):
        from tracer.services.clickhouse.read_budget import ReadDeadline
        from tracer.services.dashboard_metrics_catalog import (
            MetricsCatalogUnavailable,
            _CatalogPageFamily,
            _paginate_catalog_families,
        )

        read_rows = MagicMock(return_value=[])
        families = [
            _CatalogPageFamily("system_metrics", lambda: 3, read_rows),
            _CatalogPageFamily(
                "annotation_metrics",
                lambda: (_ for _ in ()).throw(RuntimeError("private db error")),
                read_rows,
            ),
        ]

        with pytest.raises(MetricsCatalogUnavailable) as exc_info:
            _paginate_catalog_families(
                families,
                page=1,
                page_size=2,
                deadline=ReadDeadline.start(8_500),
            )

        assert exc_info.value.family == "annotation_metrics"
        read_rows.assert_not_called()

    def test_queryset_family_applies_slice_before_materializing(self):
        from tracer.services.clickhouse.read_budget import ReadDeadline
        from tracer.services.dashboard_metrics_catalog import (
            _queryset_catalog_family,
        )

        class FakeQuerySet:
            def __init__(self):
                self.values_fields = None
                self.slices = []

            def values(self, *fields):
                self.values_fields = fields
                return self

            def count(self):
                return 10_000

            def __getitem__(self, value):
                self.slices.append(value)
                return [{"id": index} for index in range(value.start, value.stop)]

        queryset = FakeQuerySet()
        deadline = ReadDeadline.start(8_500)
        with patch(
            "tracer.services.dashboard_metrics_catalog._run_metrics_catalog_pg_read",
            side_effect=lambda _deadline, _family, read: read(),
        ):
            family = _queryset_catalog_family(
                name="eval_metrics",
                queryset=queryset,
                fields=("id", "name"),
                convert=lambda row: {"name": str(row["id"])},
                deadline=deadline,
            )
            assert family.count_rows() == 10_000
            assert family.read_rows(4_000, 50)[0] == {"name": "4000"}

        assert queryset.values_fields == ("id", "name")
        assert queryset.slices == [slice(4_000, 4_050)]

    @pytest.mark.parametrize(
        (
            "family_category",
            "family_source",
            "family_sources",
            "category",
            "source",
            "expected",
        ),
        [
            ("eval_metric", "all", ("all",), "eval_metric", "all", True),
            ("eval_metric", "all", ("all",), "eval_metric", "traces", False),
            (
                "annotation_metric",
                "both",
                ("datasets", "traces"),
                "annotation_metric",
                "datasets",
                True,
            ),
            (
                "annotation_metric",
                "both",
                ("datasets", "traces"),
                "annotation_metric",
                "simulation",
                False,
            ),
            ("custom_column", "datasets", (), "", "datasets", True),
            ("custom_column", "datasets", (), "eval_metric", "datasets", False),
        ],
    )
    def test_category_and_source_are_applied_before_family_reads(
        self,
        family_category,
        family_source,
        family_sources,
        category,
        source,
        expected,
    ):
        from tracer.services.dashboard_metrics_catalog import (
            _catalog_family_requested,
        )

        assert (
            _catalog_family_requested(
                category=category,
                source=source,
                family_category=family_category,
                family_source=family_source,
                family_sources=family_sources,
            )
            is expected
        )

    def test_project_eval_search_query_is_correlated_and_page_ordered(self):
        from tracer.services.clickhouse.read_budget import ReadDeadline
        from tracer.services.dashboard_metrics_catalog import (
            _CatalogPageFamily,
            build_metrics_catalog_page,
        )

        captured_querysets = []

        def capture_family(*, name, queryset, **_kwargs):
            captured_querysets.append(queryset)
            return _CatalogPageFamily(name, lambda: 0, lambda _offset, _limit: [])

        project_id = "11111111-1111-4111-8111-111111111111"
        workspace = SimpleNamespace(
            id="22222222-2222-4222-8222-222222222222",
            organization="33333333-3333-4333-8333-333333333333",
        )
        with (
            patch(
                "tracer.services.dashboard_metrics_catalog._resolve_metrics_catalog_project_scope",
                return_value=([project_id], True),
            ),
            patch(
                "tracer.services.dashboard_metrics_catalog._queryset_catalog_family",
                side_effect=capture_family,
            ),
            patch(
                "tracer.services.dashboard_metrics_catalog._run_metrics_catalog_pg_snapshot",
                side_effect=lambda _deadline, read: read(),
            ),
        ):
            metrics, total, has_more = build_metrics_catalog_page(
                workspace,
                page=1,
                page_size=50,
                project_ids_param=project_id,
                include_custom_attributes=False,
                search="quality",
                category="eval_metric",
                source="all",
                deadline=ReadDeadline.start(8_500),
            )

        assert metrics == []
        assert total == 0
        assert has_more is False
        assert len(captured_querysets) == 1
        sql = str(captured_querysets[0].query).upper()
        assert "EXISTS" in sql
        assert "LOWER" in sql
        assert "QUALITY" in sql

    def test_bounded_view_routes_page_first_and_never_builds_legacy_catalog(self):
        from tracer.views.dashboard import DashboardViewSet

        request = SimpleNamespace(
            workspace=SimpleNamespace(id="workspace-page-first"),
            query_params={"page": "2", "page_size": "25", "search": "quality"},
            validated_query_data={
                "project_ids": [],
                "category": "",
                "source": "",
                "search": "quality",
                "page": 2,
                "page_size": 25,
                "per_eval_config": False,
                "exclude_custom_attributes": True,
            },
        )
        with (
            patch(
                "tracer.views.dashboard.build_metrics_catalog_page",
                return_value=([{"name": "quality"}], 26, False),
            ) as page_builder,
            patch(
                "tracer.views.dashboard.get_cached_metrics_catalog"
            ) as legacy_builder,
        ):
            response = inspect.unwrap(DashboardViewSet.metrics)(
                DashboardViewSet(), request
            )

        assert response.status_code == 200
        assert response.data["result"] == {
            "metrics": [{"name": "quality"}],
            "total": 26,
            "page": 2,
            "page_size": 25,
            "has_more": False,
        }
        page_builder.assert_called_once()
        assert page_builder.call_args.kwargs["search"] == "quality"
        assert page_builder.call_args.kwargs["include_custom_attributes"] is False
        legacy_builder.assert_not_called()

    def test_unpaged_compatibility_shape_is_deadline_bound_and_deprecated(self):
        from tracer.views.dashboard import DashboardViewSet

        request = SimpleNamespace(
            workspace=SimpleNamespace(id="workspace-legacy"),
            query_params={},
            validated_query_data={
                "project_ids": [],
                "category": "",
                "source": "",
                "search": "",
                "per_eval_config": False,
                "exclude_custom_attributes": True,
            },
        )
        with (
            patch("tracer.views.dashboard.build_metrics_catalog_page") as page_builder,
            patch(
                "tracer.views.dashboard.get_cached_metrics_catalog",
                return_value=[{"name": "legacy"}],
            ) as legacy_builder,
        ):
            response = inspect.unwrap(DashboardViewSet.metrics)(
                DashboardViewSet(), request
            )

        assert response.status_code == 200
        assert response.data["result"] == {"metrics": [{"name": "legacy"}]}
        assert response["Deprecation"] == "true"
        assert legacy_builder.call_args.kwargs["deadline"] is not None
        page_builder.assert_not_called()

    def test_requested_family_failure_is_sanitized_503(self):
        from tracer.services.dashboard_metrics_catalog import (
            MetricsCatalogUnavailable,
        )
        from tracer.views.dashboard import DashboardViewSet

        with patch(
            "tracer.views.dashboard.build_metrics_catalog_page",
            side_effect=MetricsCatalogUnavailable("annotation_metrics"),
        ):
            request = SimpleNamespace(
                workspace=SimpleNamespace(id="workspace-1"),
                query_params={
                    "category": "annotation_metric",
                    "page": "1",
                    "page_size": "50",
                },
                validated_query_data={
                    "project_ids": [],
                    "category": "annotation_metric",
                    "source": "",
                    "search": "",
                    "page": 1,
                    "page_size": 50,
                    "per_eval_config": False,
                    "exclude_custom_attributes": False,
                },
            )
            response = inspect.unwrap(DashboardViewSet.metrics)(
                DashboardViewSet(), request
            )

        assert response.status_code == 503
        payload = response.data
        assert payload["code"] == "service_unavailable"
        assert "annotation_metrics" not in str(payload)

    def test_incomplete_catalog_is_never_cached(self):
        from tracer.services.clickhouse.read_budget import ReadDeadline
        from tracer.services.dashboard_metrics_catalog import (
            METRICS_CATALOG_TIMEOUT_MS,
            MetricsCatalogUnavailable,
            get_cached_metrics_catalog,
        )

        workspace = SimpleNamespace(id="workspace-complete-only")
        with (
            patch(
                "tracer.services.dashboard_metrics_catalog.cache.get",
                return_value=None,
            ),
            patch("tracer.services.dashboard_metrics_catalog.cache.set") as cache_set,
            patch(
                "tracer.services.dashboard_metrics_catalog.build_metrics_catalog",
                side_effect=MetricsCatalogUnavailable("custom_columns"),
            ),
        ):
            with pytest.raises(MetricsCatalogUnavailable):
                get_cached_metrics_catalog(
                    workspace,
                    include_custom_attributes=False,
                    deadline=ReadDeadline.start(METRICS_CATALOG_TIMEOUT_MS),
                )

        cache_set.assert_not_called()

    def test_each_catalog_pg_statement_uses_the_shrinking_request_wall(self):
        from tracer.services.dashboard_metrics_catalog import (
            _execute_metrics_catalog_pg_query_with_deadline,
        )

        class Deadline:
            def __init__(self):
                self.remaining = iter((8_250, 8_100, 7_600, 7_450))
                self.calls = []

            def remaining_ms(self, *, floor_ms):
                self.calls.append(floor_ms)
                return next(self.remaining)

        class RawCursor:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params):
                self.calls.append((sql, params))

        deadline = Deadline()
        raw_cursor = RawCursor()
        executed = []

        def execute(sql, params, many, context):
            executed.append((sql, params, many, context))
            return sql

        context = {"cursor": SimpleNamespace(cursor=raw_cursor)}
        first = _execute_metrics_catalog_pg_query_with_deadline(
            deadline,
            execute,
            "SELECT first_family",
            (),
            False,
            context,
        )
        second = _execute_metrics_catalog_pg_query_with_deadline(
            deadline,
            execute,
            "SELECT second_family",
            (),
            False,
            context,
        )

        assert first == "SELECT first_family"
        assert second == "SELECT second_family"
        assert raw_cursor.calls == [
            ("SELECT set_config('statement_timeout', %s, true)", ("8250",)),
            ("SELECT set_config('statement_timeout', %s, true)", ("7600",)),
        ]
        assert [call[:3] for call in executed] == [
            ("SELECT first_family", (), False),
            ("SELECT second_family", (), False),
        ]
        assert deadline.calls == [1, 1, 1, 1]

    def test_catalog_counts_and_slices_share_a_read_only_repeatable_snapshot(self):
        from tracer.services.dashboard_metrics_catalog import (
            METRICS_CATALOG_TIMEOUT_MS,
            _run_metrics_catalog_pg_snapshot,
        )

        deadline = MagicMock()
        deadline.remaining_ms.return_value = 8_000
        fake_connection = MagicMock(vendor="postgresql", in_atomic_block=False)
        cursor = fake_connection.cursor.return_value.__enter__.return_value
        atomic = MagicMock()

        with (
            patch(
                "tracer.services.dashboard_metrics_catalog.connection",
                fake_connection,
            ),
            patch(
                "tracer.services.dashboard_metrics_catalog.transaction.atomic",
                return_value=atomic,
            ),
        ):
            result = _run_metrics_catalog_pg_snapshot(deadline, lambda: "page")

        assert result == "page"
        atomic.__enter__.assert_called_once_with()
        atomic.__exit__.assert_called_once()
        cursor.execute.assert_called_once_with(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )
        assert deadline.remaining_ms.call_args_list == [
            call(METRICS_CATALOG_TIMEOUT_MS),
            call(floor_ms=1),
            call(floor_ms=1),
        ]

    def test_stalled_remote_cache_is_skipped_without_spending_request_wall(self):
        import time

        from tracer.services.clickhouse.read_budget import ReadDeadline
        from tracer.services.dashboard_metrics_catalog import (
            get_cached_metrics_catalog,
        )

        remote_cache = MagicMock()
        remote_cache.get.side_effect = lambda *_args, **_kwargs: time.sleep(5)
        remote_cache.set.side_effect = lambda *_args, **_kwargs: time.sleep(5)
        metrics = [{"name": "complete"}]

        started = time.monotonic()
        with (
            patch(
                "tracer.services.dashboard_metrics_catalog.caches",
                {"default": remote_cache},
            ),
            patch(
                "tracer.services.dashboard_metrics_catalog.cache",
                remote_cache,
            ),
            patch(
                "tracer.services.dashboard_metrics_catalog.build_metrics_catalog",
                return_value=metrics,
            ),
        ):
            result = get_cached_metrics_catalog(
                SimpleNamespace(id="workspace-remote-cache"),
                deadline=ReadDeadline.start(100),
            )
        elapsed = time.monotonic() - started

        assert result == metrics
        assert elapsed < 0.5
        remote_cache.get.assert_not_called()
        remote_cache.set.assert_not_called()

    def test_static_scope_is_deterministic_and_skips_dynamic_readers(self):
        from tracer.services.clickhouse.read_budget import ReadDeadline
        from tracer.services.dashboard_metrics_catalog import (
            METRICS_CATALOG_TIMEOUT_MS,
            _metric_catalog_sort_key,
            build_metrics_catalog,
        )

        workspace = SimpleNamespace(
            id="workspace-static",
            organization=object(),
            organization_id="organization-static",
        )
        with (
            patch(
                "tracer.services.dashboard_metrics_catalog.Project.objects.filter"
            ) as project_filter,
            patch(
                "tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService"
            ) as analytics,
            patch(
                "tracer.services.dashboard_metrics_catalog.AnnotationLabelScoresProjectPG"
            ) as annotation_source,
        ):
            metrics = build_metrics_catalog(
                workspace,
                include_custom_attributes=False,
                category="system_metric",
                source="datasets",
                deadline=ReadDeadline.start(METRICS_CATALOG_TIMEOUT_MS),
            )

        assert metrics
        assert all(item["category"] == "system_metric" for item in metrics)
        assert all(item["source"] == "datasets" for item in metrics)
        assert metrics == sorted(metrics, key=_metric_catalog_sort_key)
        project_filter.assert_not_called()
        analytics.assert_not_called()
        annotation_source.assert_not_called()

    def test_attribute_future_uses_shared_deadline_and_fails_closed(self):
        from tracer.services.clickhouse.read_budget import ReadDeadline
        from tracer.services.dashboard_metrics_catalog import (
            METRICS_CATALOG_TIMEOUT_MS,
            MetricsCatalogUnavailable,
            build_metrics_catalog,
        )

        project_id = "11111111-1111-4111-8111-111111111111"
        analytics = MagicMock()
        analytics.get_span_attribute_keys_ch_for_projects.side_effect = RuntimeError(
            "private attribute failure"
        )
        with (
            patch(
                "tracer.services.dashboard_metrics_catalog._run_metrics_catalog_pg_read",
                return_value=[project_id],
            ),
            patch(
                "tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService",
                return_value=analytics,
            ),
        ):
            with pytest.raises(MetricsCatalogUnavailable) as exc_info:
                build_metrics_catalog(
                    SimpleNamespace(
                        id="workspace-worker",
                        organization=object(),
                        organization_id="organization-worker",
                    ),
                    project_ids_param=project_id,
                    category="custom_attribute",
                    source="traces",
                    deadline=ReadDeadline.start(METRICS_CATALOG_TIMEOUT_MS),
                )

        assert exc_info.value.family == "custom_attributes"
        kwargs = analytics.get_span_attribute_keys_ch_for_projects.call_args.kwargs
        assert 0 < kwargs["timeout_ms"] <= METRICS_CATALOG_TIMEOUT_MS

    @pytest.mark.django_db
    def test_scope_skips_unrequested_dynamic_families(
        self,
        auth_client,
    ):
        with (
            patch(
                "tracer.services.dashboard_metrics_catalog.cache.get",
                return_value=None,
            ),
            patch("tracer.services.dashboard_metrics_catalog.cache.set"),
            patch(
                "tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService"
            ) as analytics,
            patch(
                "tracer.services.dashboard_metrics_catalog.AnnotationLabelScoresProjectPG"
            ) as annotation_source,
        ):
            response = auth_client.get(
                "/tracer/dashboard/metrics/"
                "?category=system_metric&source=datasets&page=1&page_size=50"
            )

        assert response.status_code == 200
        metrics = response.json()["result"]["metrics"]
        assert metrics
        assert all(item["category"] == "system_metric" for item in metrics)
        assert all(item["source"] == "datasets" for item in metrics)
        analytics.assert_not_called()
        annotation_source.assert_not_called()

    @pytest.mark.django_db
    def test_custom_attribute_worker_failure_is_503_and_uses_shared_timeout(
        self,
        auth_client,
        observe_project,
    ):
        analytics = MagicMock()
        analytics.get_span_attribute_keys_ch_for_projects.side_effect = RuntimeError(
            "private attribute failure"
        )
        with (
            patch(
                "tracer.services.dashboard_metrics_catalog.cache.get",
                return_value=None,
            ),
            patch("tracer.services.dashboard_metrics_catalog.cache.set") as cache_set,
            patch(
                "tracer.services.dashboard_metrics_catalog.V2AnalyticsQueryService",
                return_value=analytics,
            ),
        ):
            response = auth_client.get(
                "/tracer/dashboard/metrics/",
                {
                    "project_ids": str(observe_project.id),
                    "category": "custom_attribute",
                    "page": 1,
                    "page_size": 50,
                },
            )

        assert response.status_code == 503
        assert "private attribute failure" not in str(response.json())
        cache_set.assert_not_called()
        kwargs = analytics.get_span_attribute_keys_ch_for_projects.call_args.kwargs
        assert 0 < kwargs["timeout_ms"] <= settings.INTERACTIVE_READ_DEFAULT_WALL_MS


class TestDashboardAuthRequired:
    @pytest.mark.django_db
    def test_unauthenticated_list_is_blocked(self, api_client):
        response = api_client.get("/tracer/dashboard/")
        assert response.status_code == 401

    @pytest.mark.django_db
    def test_unauthenticated_metrics_is_blocked(self, api_client):
        response = api_client.get("/tracer/dashboard/metrics/")
        assert response.status_code == 401


class TestAnnotationMetricAggregation:
    def _annotation_sql(self, output_type):
        config = {
            "project_ids": ["proj1"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "a1",
                    "name": "quality",
                    "type": "annotation_metric",
                    "label_id": str(uuid.uuid4()),
                    "aggregation": "avg",
                    "output_type": output_type,
                }
            ],
        }
        sql, _, _ = DashboardQueryBuilder(config).build_all_queries()[0]
        return sql

    def test_thumbs_up_down_uses_up_percentage(self):
        sql = self._annotation_sql("thumbs_up_down")
        assert "JSONExtract(a.value, 'value', 'Nullable(String)')" in sql
        assert "= 'up') * 100.0 /" in sql
        assert "greatest(countIf(" in sql

    def test_categorical_uses_count(self):
        sql = self._annotation_sql("categorical")
        assert "count() AS value" in sql

    def test_text_uses_count(self):
        sql = self._annotation_sql("text")
        assert "count() AS value" in sql


class TestDashboardQueryValidation:
    def test_implicit_all_scope_is_concrete_and_changes_with_workspace_membership(
        self,
    ):
        workspace = MagicMock()
        workspace.id = uuid.uuid4()
        first_project_id = uuid.uuid4()
        second_project_id = uuid.uuid4()
        first_dataset_id = uuid.uuid4()
        second_dataset_id = uuid.uuid4()

        def scoped_queryset(ids):
            queryset = MagicMock()
            queryset.values_list.return_value = ids
            return queryset

        trace_metric = {
            "id": "latency",
            "name": "latency",
            "type": "system_metric",
            "source": "traces",
            "aggregation": "avg",
        }
        dataset_metric = {
            "id": "row_count",
            "name": "row_count",
            "type": "system_metric",
            "source": "datasets",
            "aggregation": "count",
        }
        query_config = {
            "project_ids": [],
            "dataset_ids": [],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [trace_metric, dataset_metric],
        }

        with (
            patch(
                "tracer.views.dashboard.project_queryset_for_request",
                side_effect=[
                    scoped_queryset([first_project_id, second_project_id]),
                    scoped_queryset([first_project_id]),
                ],
            ),
            patch(
                "model_hub.utils.workspace_scope.scoped_dataset_queryset",
                side_effect=[
                    scoped_queryset([first_dataset_id, second_dataset_id]),
                    scoped_queryset([first_dataset_id]),
                ],
            ),
        ):
            first_scope = _materialize_dashboard_query_scope(
                query_config,
                workspace,
                trace_metrics=[trace_metric],
                dataset_metrics=[dataset_metric],
            )
            current_scope = _materialize_dashboard_query_scope(
                query_config,
                workspace,
                trace_metrics=[trace_metric],
                dataset_metrics=[dataset_metric],
            )

        first_key = snapshot_cache_key(
            "dashboard-query",
            {
                "workspace_id": str(workspace.id),
                "query_config": first_scope,
            },
        )
        current_key = snapshot_cache_key(
            "dashboard-query",
            {
                "workspace_id": str(workspace.id),
                "query_config": current_scope,
            },
        )
        assert first_scope["project_ids"] == sorted(
            [str(first_project_id), str(second_project_id)]
        )
        assert first_scope["dataset_ids"] == sorted(
            [str(first_dataset_id), str(second_dataset_id)]
        )
        assert current_scope["project_ids"] == [str(first_project_id)]
        assert current_scope["dataset_ids"] == [str(first_dataset_id)]
        assert current_key != first_key

    @pytest.mark.django_db
    def test_default_workspace_scope_includes_legacy_null_resources(
        self, organization, workspace, user, project
    ):
        legacy_project = Project.no_workspace_objects.create(
            name="Legacy null project",
            organization=organization,
            workspace=None,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            metadata={},
        )
        legacy_dataset = Dataset.no_workspace_objects.create(
            name="Legacy null dataset",
            organization=organization,
            workspace=None,
            user=user,
        )
        Project.no_workspace_objects.filter(id=legacy_project.id).update(workspace=None)
        Dataset.no_workspace_objects.filter(id=legacy_dataset.id).update(workspace=None)
        legacy_project.refresh_from_db()
        legacy_dataset.refresh_from_db()
        assert legacy_project.workspace_id is None
        assert legacy_dataset.workspace_id is None
        other_workspace = Workspace.objects.create(
            name="Other workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        excluded_project = Project.no_workspace_objects.create(
            name="Other workspace project",
            organization=organization,
            workspace=other_workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            metadata={},
        )
        excluded_dataset = Dataset.no_workspace_objects.create(
            name="Other workspace dataset",
            organization=organization,
            workspace=other_workspace,
            user=user,
        )
        query_config = {
            "project_ids": [],
            "dataset_ids": [],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [],
        }

        scoped = _materialize_dashboard_query_scope(
            query_config,
            workspace,
            trace_metrics=[{"source": "traces"}],
            dataset_metrics=[{"source": "datasets"}],
        )

        assert str(project.id) in scoped["project_ids"]
        assert str(legacy_project.id) in scoped["project_ids"]
        assert str(excluded_project.id) not in scoped["project_ids"]
        assert str(legacy_dataset.id) in scoped["dataset_ids"]
        assert str(excluded_dataset.id) not in scoped["dataset_ids"]

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AnalyticsQueryService")
    def test_cross_workspace_project_ids_returns_400(
        self, mock_analytics_cls, auth_client, organization, user
    ):
        other_ws = Workspace.objects.create(
            name="Other workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        other_project = Project.objects.create(
            name="Other workspace project",
            organization=organization,
            workspace=other_ws,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            metadata={},
        )
        response = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "project_ids": [str(other_project.id)],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 400
        mock_analytics_cls.return_value.execute_ch_query.assert_not_called()

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AnalyticsQueryService")
    def test_invalid_dataset_ids_returns_400(self, mock_analytics_cls, auth_client):
        response = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "dataset_ids": [str(uuid.uuid4())],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "row_count",
                        "name": "row_count",
                        "type": "system_metric",
                        "source": "datasets",
                        "aggregation": "count",
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 400


class TestDashboardUpdateValidation:
    @pytest.mark.django_db
    def test_patch_empty_name_rejected(self, auth_client, dashboard):
        response = auth_client.patch(
            f"/tracer/dashboard/{dashboard.id}/",
            {"name": ""},
            format="json",
        )
        assert response.status_code == 400
        dashboard.refresh_from_db()
        assert dashboard.name == "Test Dashboard"

    @pytest.mark.django_db
    def test_patch_whitespace_name_rejected(self, auth_client, dashboard):
        response = auth_client.patch(
            f"/tracer/dashboard/{dashboard.id}/",
            {"name": "   "},
            format="json",
        )
        assert response.status_code == 400
        dashboard.refresh_from_db()
        assert dashboard.name == "Test Dashboard"


class TestFilterValuesEndpoint:
    URL = "/tracer/dashboard/filter_values/"

    @pytest.mark.django_db
    def test_missing_metric_name_returns_400(self, auth_client):
        response = auth_client.get(
            self.URL, {"source": "traces", "metric_type": "system_metric"}
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_blank_metric_name_returns_400(self, auth_client):
        response = auth_client.get(self.URL, {"metric_name": "", "source": "traces"})
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_invalid_source_returns_400(self, auth_client):
        response = auth_client.get(
            self.URL, {"metric_name": "model", "source": "bogus"}
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_invalid_metric_type_returns_400(self, auth_client):
        response = auth_client.get(
            self.URL, {"metric_name": "model", "metric_type": "bogus"}
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_non_uuid_dataset_id_returns_400(self, auth_client):
        response = auth_client.get(
            self.URL,
            {
                "metric_name": "col",
                "source": "dataset_column",
                "dataset_id": "not-a-uuid",
            },
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=False)
    def test_system_metric_returns_empty_when_clickhouse_disabled(
        self, _mock_ch, auth_client
    ):
        response = auth_client.get(
            self.URL, {"metric_name": "model", "metric_type": "system_metric"}
        )
        assert response.status_code == 200
        assert response.json()["result"]["values"] == []

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_traces_system_metric_returns_distinct_values(
        self, mock_analytics_cls, _mock_ch, auth_client, project
    ):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [{"val": "gpt-4"}, {"val": "claude-3"}]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.get(
            self.URL,
            {
                "source": "traces",
                "metric_type": "system_metric",
                "metric_name": "model",
                "project_ids": str(project.id),
            },
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert "gpt-4" in body
        assert "claude-3" in body

    @pytest.mark.django_db
    def test_dataset_column_non_uuid_column_returns_400(self, auth_client):
        response = auth_client.get(
            self.URL,
            {
                "source": "dataset_column",
                "metric_name": "not-a-uuid",
                "dataset_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_dataset_column_unknown_column_returns_empty(self, auth_client):
        # A column/dataset not owned by this workspace resolves to no values.
        response = auth_client.get(
            self.URL,
            {
                "source": "dataset_column",
                "metric_name": str(uuid.uuid4()),
                "dataset_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["values"] == []

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.V2AnalyticsQueryService")
    def test_traces_enduser_metric_returns_values(
        self, mock_analytics_cls, _mock_ch, auth_client, project
    ):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [{"val": "external"}, {"val": "internal"}]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.get(
            self.URL,
            {
                "source": "traces",
                "metric_type": "system_metric",
                "metric_name": "user_id_type",
                "project_ids": str(project.id),
            },
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert "external" in body
        assert "internal" in body

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=False)
    def test_simulation_source_fails_closed_when_clickhouse_disabled(
        self, _mock_ch, auth_client
    ):
        response = auth_client.get(
            self.URL,
            {
                "source": "simulation",
                "metric_type": "system_metric",
                "metric_name": "status",
            },
        )
        assert response.status_code == 503
        payload = response.json()
        assert payload["code"] == "service_unavailable"
        assert "temporarily unavailable" in json.dumps(payload)

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.AnalyticsQueryService")
    def test_dataset_column_flattens_array_cells(
        self, mock_analytics_cls, _mock_ch, auth_client, organization, workspace
    ):
        from model_hub.models.choices import (
            DataTypeChoices,
            SourceChoices,
            StatusType,
        )
        from model_hub.models.develop_dataset import Column, Dataset

        dataset = Dataset.objects.create(
            name="DS", organization=organization, workspace=workspace
        )
        column = Column.objects.create(
            id=uuid.uuid4(),
            name="lang",
            data_type=DataTypeChoices.ARRAY.value,
            source=SourceChoices.OTHERS.value,
            status=StatusType.RUNNING.value,
            dataset=dataset,
        )
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [
            {"val": '["English","French"]'},
            {"val": '["English","Spanish"]'},
        ]
        mock_service.execute_ch_query.return_value = mock_result
        mock_analytics_cls.return_value = mock_service

        response = auth_client.get(
            self.URL,
            {
                "source": "dataset_column",
                "metric_name": str(column.id),
                "dataset_id": str(dataset.id),
            },
        )
        assert response.status_code == 200
        labels = {v["value"] for v in response.json()["result"]["values"]}
        # array cells flattened to individual elements, deduped
        assert labels == {"English", "French", "Spanish"}
        assert '["English","French"]' not in labels


class TestFilterValuesAnnotationBranches:
    URL = "/tracer/dashboard/filter_values/"

    def _label(self, organization, workspace, ltype, settings=None):
        from model_hub.models.develop_annotations import AnnotationsLabels

        return AnnotationsLabels.objects.create(
            name=f"L-{ltype}",
            type=ltype,
            organization=organization,
            workspace=workspace,
            settings=settings or {},
        )

    @pytest.mark.django_db
    def test_star_label_returns_star_options(
        self, auth_client, organization, workspace
    ):
        label = self._label(organization, workspace, "star", {"no_of_stars": 3})
        response = auth_client.get(
            self.URL,
            {
                "source": "traces",
                "metric_type": "annotation_metric",
                "metric_name": str(label.id),
            },
        )
        assert response.status_code == 200
        values = response.json()["result"]["values"]
        assert [v["value"] for v in values] == ["1", "2", "3"]
        assert values[0]["label"] == "1 star"
        assert values[2]["label"] == "3 stars"

    @pytest.mark.django_db
    def test_thumbs_label_returns_up_down_options(
        self, auth_client, organization, workspace
    ):
        label = self._label(organization, workspace, "thumbs_up_down")
        response = auth_client.get(
            self.URL,
            {
                "source": "traces",
                "metric_type": "annotation_metric",
                "metric_name": str(label.id),
            },
        )
        assert response.status_code == 200
        values = response.json()["result"]["values"]
        assert {v["value"] for v in values} == {"thumbs_up", "thumbs_down"}

    @pytest.mark.django_db
    def test_workspace_label_without_requested_project_score_returns_empty(
        self,
        auth_client,
        project,
        organization,
        workspace,
    ):
        from tracer.services.annotation_label_source import (
            AnnotationLabelScoresProjectPG,
        )

        label = self._label(organization, workspace, "thumbs_up_down")
        with patch.object(
            AnnotationLabelScoresProjectPG,
            "label_has_scores_for_projects",
            return_value=False,
        ) as visibility_read:
            response = auth_client.get(
                self.URL,
                {
                    "source": "traces",
                    "metric_type": "annotation_metric",
                    "metric_name": str(label.id),
                    "project_ids": str(project.id),
                    "page_size": 20,
                },
            )

        assert response.status_code == 200
        assert response.json()["result"]["values"] == []
        visibility_read.assert_called_once_with(label.id, [str(project.id)])

    @pytest.mark.django_db
    def test_workspace_label_with_requested_project_score_returns_values(
        self,
        auth_client,
        project,
        organization,
        workspace,
    ):
        from tracer.services.annotation_label_source import (
            AnnotationLabelScoresProjectPG,
        )

        label = self._label(organization, workspace, "thumbs_up_down")
        with patch.object(
            AnnotationLabelScoresProjectPG,
            "label_has_scores_for_projects",
            return_value=True,
        ) as visibility_read:
            response = auth_client.get(
                self.URL,
                {
                    "source": "traces",
                    "metric_type": "annotation_metric",
                    "metric_name": str(label.id),
                    "project_ids": str(project.id),
                    "page_size": 20,
                },
            )

        assert response.status_code == 200
        assert {item["value"] for item in response.json()["result"]["values"]} == {
            "thumbs_up",
            "thumbs_down",
        }
        visibility_read.assert_called_once_with(label.id, [str(project.id)])

    @pytest.mark.django_db
    def test_unknown_label_returns_empty(self, auth_client):
        response = auth_client.get(
            self.URL,
            {
                "source": "traces",
                "metric_type": "annotation_metric",
                "metric_name": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["values"] == []

    @pytest.mark.django_db
    def test_label_from_unrequested_project_does_not_expose_settings(
        self,
        auth_client,
        project,
        organization,
        workspace,
    ):
        from model_hub.models.develop_annotations import AnnotationsLabels

        other_project = Project.objects.create(
            name="Other project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        foreign_label = AnnotationsLabels.no_workspace_objects.create(
            name="Other project choices",
            type="categorical",
            organization=organization,
            workspace=workspace,
            project=other_project,
            settings={
                "options": [
                    {"label": "must-not-leak"},
                    {"label": "also-private"},
                ],
                "strategy": None,
                "auto_annotate": False,
                "multi_choice": False,
                "rule_prompt": "",
            },
        )

        response = auth_client.get(
            self.URL,
            {
                "source": "traces",
                "metric_type": "annotation_metric",
                "metric_name": str(foreign_label.id),
                "project_ids": str(project.id),
            },
        )

        assert response.status_code == 200
        assert response.json()["result"]["values"] == []

    @pytest.mark.django_db
    def test_label_from_another_workspace_does_not_expose_settings(
        self,
        auth_client,
        project,
        user,
    ):
        from accounts.models.organization import Organization
        from model_hub.models.develop_annotations import AnnotationsLabels

        other_organization = Organization.objects.create(name="Other organization")
        other_workspace = Workspace.objects.create(
            name="Other workspace",
            organization=other_organization,
            created_by=user,
        )
        foreign_label = AnnotationsLabels.no_workspace_objects.create(
            name="Foreign choices",
            type="categorical",
            organization=other_organization,
            workspace=other_workspace,
            settings={
                "options": [
                    {"label": "tenant-secret"},
                    {"label": "also-secret"},
                ],
                "strategy": None,
                "auto_annotate": False,
                "multi_choice": False,
                "rule_prompt": "",
            },
        )

        response = auth_client.get(
            self.URL,
            {
                "source": "traces",
                "metric_type": "annotation_metric",
                "metric_name": str(foreign_label.id),
                "project_ids": str(project.id),
            },
        )

        assert response.status_code == 200
        assert response.json()["result"]["values"] == []


class TestFilterValuesEvalBranches:
    URL = "/tracer/dashboard/filter_values/"

    @staticmethod
    def _eval_config(project, organization, workspace, *, output, choices=None):
        from model_hub.models.evals_metric import EvalTemplate
        from tracer.models.custom_eval_config import CustomEvalConfig

        template = EvalTemplate.no_workspace_objects.create(
            name=f"{output} eval",
            organization=organization,
            workspace=workspace,
            config={"output": output},
            choices=choices or [],
        )
        config = CustomEvalConfig.no_workspace_objects.create(
            name=f"{output} config",
            project=project,
            eval_template=template,
        )
        return config, template

    @pytest.mark.django_db
    def test_config_and_template_ids_resolve_choice_values(
        self,
        auth_client,
        project,
        organization,
        workspace,
    ):
        config, template = self._eval_config(
            project,
            organization,
            workspace,
            output="choices",
            choices=["Accepted", "Rejected"],
        )

        for metric_name in (str(config.id), str(template.id)):
            response = auth_client.get(
                self.URL,
                {
                    "source": "traces",
                    "metric_type": "eval_metric",
                    "metric_name": metric_name,
                    "project_ids": str(project.id),
                },
            )

            assert response.status_code == 200
            assert response.json()["result"]["values"] == [
                {"value": "Accepted", "label": "Accepted"},
                {"value": "Rejected", "label": "Rejected"},
            ]

    @pytest.mark.django_db
    def test_pass_fail_config_returns_canonical_values(
        self,
        auth_client,
        project,
        organization,
        workspace,
    ):
        config, _template = self._eval_config(
            project,
            organization,
            workspace,
            output="Pass/Fail",
        )

        response = auth_client.get(
            self.URL,
            {
                "source": "traces",
                "metric_type": "eval_metric",
                "metric_name": str(config.id),
                "project_ids": str(project.id),
            },
        )

        assert response.status_code == 200
        assert response.json()["result"]["values"] == [
            {"value": "Passed", "label": "Passed"},
            {"value": "Failed", "label": "Failed"},
        ]

    @pytest.mark.django_db
    def test_eval_from_unrequested_project_does_not_expose_choices(
        self,
        auth_client,
        project,
        organization,
        workspace,
    ):
        other_project = Project.objects.create(
            name="Other eval project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        config, template = self._eval_config(
            other_project,
            organization,
            workspace,
            output="choices",
            choices=["must-not-leak"],
        )

        for metric_name in (str(config.id), str(template.id)):
            response = auth_client.get(
                self.URL,
                {
                    "source": "traces",
                    "metric_type": "eval_metric",
                    "metric_name": metric_name,
                    "project_ids": str(project.id),
                },
            )

            assert response.status_code == 200
            assert response.json()["result"]["values"] == []


class TestSimulationAgents:
    URL = "/tracer/dashboard/simulation-agents/"

    def _agent(self, organization, workspace, name="Agent A", deleted=False):
        from simulate.models.agent_definition import AgentDefinition

        agent = AgentDefinition.objects.create(
            agent_name=name,
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            description="test agent",
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        if deleted:
            agent.deleted = True
            agent.save(update_fields=["deleted"])
        return agent

    @pytest.mark.django_db
    def test_returns_workspace_agents_without_obs_link(
        self, auth_client, organization, workspace
    ):
        agent = self._agent(organization, workspace, "Voice Agent")
        response = auth_client.get(self.URL)
        assert response.status_code == 200
        agents = response.json()["result"]["agents"]
        found = next((a for a in agents if a["id"] == str(agent.id)), None)
        assert found is not None
        assert found["name"] == "Voice Agent"
        assert found["observability_project_id"] is None

    @pytest.mark.django_db
    def test_excludes_deleted_agents(self, auth_client, organization, workspace):
        agent = self._agent(organization, workspace, "Deleted Agent", deleted=True)
        response = auth_client.get(self.URL)
        assert response.status_code == 200
        ids = {a["id"] for a in response.json()["result"]["agents"]}
        assert str(agent.id) not in ids

    @pytest.mark.django_db
    def test_excludes_other_workspace_agents(
        self, auth_client, organization, user, workspace
    ):
        other_ws = Workspace.objects.create(
            name="Other workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        agent = self._agent(organization, other_ws, "Other WS Agent")
        response = auth_client.get(self.URL)
        assert response.status_code == 200
        ids = {a["id"] for a in response.json()["result"]["agents"]}
        assert str(agent.id) not in ids


class TestWidgetExecutePreviewBranches:
    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    def test_execute_query_widget_without_metrics_returns_400(
        self, _mock_ch, auth_client, dashboard, user
    ):
        widget = DashboardWidget.objects.create(
            dashboard=dashboard,
            name="No metrics",
            position=0,
            width=6,
            height=4,
            query_config={"granularity": "day", "metrics": []},
            chart_config={},
            created_by=user,
        )
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/{widget.id}/query/"
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=False)
    def test_preview_returns_400_when_clickhouse_disabled(
        self, _mock_ch, auth_client, dashboard, sample_query_config
    ):
        response = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/preview/",
            {"query_config": sample_query_config},
            format="json",
        )
        assert response.status_code == 400


class TestWidgetWriteIsolation:
    """Foreign-workspace widget *writes* must be rejected with no mutation.
    (Read isolation is covered by TestWidgetReadEndpoints; this covers writes:
    create / update / destroy / reorder / duplicate.)"""

    def _foreign(self, organization, user):
        other_ws = Workspace.objects.create(
            name="Other WS",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        dash = Dashboard.objects.create(
            workspace=other_ws,
            name="Foreign Dashboard",
            created_by=user,
            updated_by=user,
        )
        widget = DashboardWidget.objects.create(
            dashboard=dash,
            name="Foreign Widget",
            position=0,
            width=6,
            height=4,
            query_config={},
            chart_config={},
            created_by=user,
        )
        return dash, widget

    @pytest.mark.django_db
    def test_create_under_foreign_dashboard_blocked(
        self, auth_client, organization, user
    ):
        dash, _ = self._foreign(organization, user)
        before = DashboardWidget.objects.filter(dashboard=dash).count()
        resp = auth_client.post(
            f"/tracer/dashboard/{dash.id}/widgets/",
            {
                "name": "Injected",
                "position": 0,
                "width": 6,
                "height": 4,
                "query_config": {},
                "chart_config": {},
            },
            format="json",
        )
        assert resp.status_code == 404
        assert DashboardWidget.objects.filter(dashboard=dash).count() == before

    @pytest.mark.django_db
    def test_update_foreign_widget_blocked(self, auth_client, organization, user):
        dash, widget = self._foreign(organization, user)
        resp = auth_client.put(
            f"/tracer/dashboard/{dash.id}/widgets/{widget.id}/",
            {
                "name": "Hijacked",
                "position": 0,
                "width": 6,
                "height": 4,
                "query_config": {},
                "chart_config": {},
            },
            format="json",
        )
        assert resp.status_code == 404
        widget.refresh_from_db()
        assert widget.name == "Foreign Widget"

    @pytest.mark.django_db
    def test_destroy_foreign_widget_blocked(self, auth_client, organization, user):
        dash, widget = self._foreign(organization, user)
        resp = auth_client.delete(f"/tracer/dashboard/{dash.id}/widgets/{widget.id}/")
        assert resp.status_code == 404
        widget.refresh_from_db()
        assert widget.deleted is False

    @pytest.mark.django_db
    def test_reorder_foreign_dashboard_blocked(self, auth_client, organization, user):
        dash, widget = self._foreign(organization, user)
        resp = auth_client.post(
            f"/tracer/dashboard/{dash.id}/widgets/reorder/",
            {"order": [str(widget.id)]},
            format="json",
        )
        assert resp.status_code == 404
        widget.refresh_from_db()
        assert widget.position == 0

    @pytest.mark.django_db
    def test_duplicate_foreign_widget_blocked(self, auth_client, organization, user):
        dash, widget = self._foreign(organization, user)
        before = DashboardWidget.objects.filter(dashboard=dash).count()
        resp = auth_client.post(
            f"/tracer/dashboard/{dash.id}/widgets/{widget.id}/duplicate/"
        )
        # 400 rather than 404: duplicate_widget catches the queryset's Http404
        # and re-emits it as 400. Isolation still holds — no copy is created.
        assert resp.status_code == 400
        assert DashboardWidget.objects.filter(dashboard=dash).count() == before


class TestQueryEngineFailure:
    """A ClickHouse error mid-execution must degrade gracefully (no 500 crash)
    on every query-executing endpoint."""

    @pytest.mark.django_db
    @patch(
        "tracer.views.dashboard.DashboardWidgetViewSet._execute_ch_query_config"
    )
    def test_query_action_survives_ch_failure(
        self, mock_execute_query, auth_client, observe_project
    ):
        mock_execute_query.side_effect = Exception("CH exploded")
        resp = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "project_ids": [str(observe_project.id)],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
            },
            format="json",
        )
        # Direct dashboard reads fail closed with a sanitized client error,
        # never a 500 or an indefinitely pending response.
        assert resp.status_code == 400

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.get_clickhouse_client")
    def test_execute_query_survives_ch_failure(
        self, mock_get_client, _mock_enabled, auth_client, dashboard, dashboard_widget
    ):
        client = MagicMock()
        client.execute_read.side_effect = Exception("CH exploded")
        mock_get_client.return_value = client
        resp = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/query/"
        )
        assert resp.status_code == 400

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
    @patch("tracer.views.dashboard.get_clickhouse_client")
    def test_preview_survives_ch_failure(
        self,
        mock_get_client,
        _mock_enabled,
        auth_client,
        dashboard,
        sample_query_config,
    ):
        client = MagicMock()
        client.execute_read.side_effect = Exception("CH exploded")
        mock_get_client.return_value = client
        resp = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/preview/",
            {"query_config": sample_query_config},
            format="json",
        )
        assert resp.status_code == 400


class TestWidgetMutationErrorBranches:
    """Nonexistent-target error branches for the mutation actions."""

    @pytest.mark.django_db
    def test_destroy_nonexistent_widget_returns_404(self, auth_client, dashboard):
        resp = auth_client.delete(
            f"/tracer/dashboard/{dashboard.id}/widgets/{uuid.uuid4()}/"
        )
        assert resp.status_code == 404

    @pytest.mark.django_db
    def test_duplicate_nonexistent_widget_rejected(self, auth_client, dashboard):
        before = DashboardWidget.objects.filter(dashboard=dashboard).count()
        resp = auth_client.post(
            f"/tracer/dashboard/{dashboard.id}/widgets/{uuid.uuid4()}/duplicate/"
        )
        assert resp.status_code == 400
        assert DashboardWidget.objects.filter(dashboard=dashboard).count() == before


class TestQueryMalformedInput:
    @pytest.mark.django_db
    def test_malformed_project_uuid_returns_400(self, auth_client):
        resp = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "project_ids": ["not-a-uuid"],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
            },
            format="json",
        )
        assert resp.status_code == 400


class TestXSSPayloadNonExecutable:
    """A markup/XSS metric name is currently echoed back in the response body
    (pre-existing; the parameterize-the-attribute-key follow-up removes the
    reflection). Until then, assert the reflection is inert: the response is
    served as application/json, so a reflected <script> is JSON text, never
    rendered HTML."""

    @pytest.mark.django_db
    @patch("tracer.views.dashboard.AnalyticsQueryService")
    def test_xss_metric_name_response_is_json_not_html(
        self, mock_analytics_cls, auth_client, observe_project
    ):
        service = MagicMock()
        result = MagicMock()
        result.data = []
        service.execute_ch_query.return_value = result
        mock_analytics_cls.return_value = service
        payload = '<script>alert("xss")</script>'
        resp = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "project_ids": [str(observe_project.id)],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": payload,
                        "name": payload,
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
            },
            format="json",
        )
        assert resp.status_code == 400
        # Non-executable: served as JSON, so a reflected payload is inert text.
        assert resp["Content-Type"].startswith("application/json")


@pytest.mark.django_db
def test_dashboard_query_serves_inline_rollup_without_scheduling_worker(
    auth_client,
    observe_project,
):
    rollup_analytics = MagicMock()
    rollup_analytics.execute_ch_query.return_value = SimpleNamespace(
        data=[
            {
                "time_bucket": datetime(2026, 8, 1, tzinfo=UTC),
                "metric_0": 12.0,
            }
        ],
        columns=["time_bucket", "metric_0"],
    )

    with (
        patch("tracer.views.dashboard.read_or_schedule_exact_snapshot") as scheduler,
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService",
            return_value=rollup_analytics,
        ),
        patch(
            "tracer.views.dashboard.AnalyticsQueryService",
            side_effect=AssertionError("public poll must not use legacy ClickHouse"),
        ),
    ):
        response = auth_client.post(
            "/tracer/dashboard/query/?refresh=true",
            {
                "project_ids": [str(observe_project.id)],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                    }
                ],
            },
            format="json",
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["query_status"] == "complete"
    assert result["query_complete"] is True
    assert result["query_sampled"] is False
    assert result["query_exact"] is False
    assert result["query_provenance"] == "materialized_rollup"
    scheduler.assert_not_called()


@pytest.mark.django_db
def test_dashboard_query_replays_legacy_metric_filter_without_400(
    auth_client,
    observe_project,
):
    captured = {}

    def _rollup(query_config, *, deadline):
        captured.update(query_config=query_config, deadline=deadline)
        return {
            "metrics": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
            "query_exact": False,
            "query_provenance": "materialized_rollup",
        }

    with (
        patch(
            "tracer.views.dashboard._read_dashboard_rollup_fast_path",
            side_effect=_rollup,
        ),
        patch("tracer.views.dashboard.read_or_schedule_exact_snapshot") as scheduler,
    ):
        response = auth_client.post(
            "/tracer/dashboard/query/",
            {
                "project_ids": [str(observe_project.id)],
                "granularity": "day",
                "time_range": {"preset": "7D"},
                "metrics": [
                    {
                        "id": "latency",
                        "name": "latency",
                        "type": "system_metric",
                        "aggregation": "avg",
                        "source": "traces",
                        "filters": [
                            {
                                "metric_name": "status",
                                "metric_type": "system_metric",
                                "operator": "equal_to",
                                "source": "traces",
                                "value": "OK",
                            }
                        ],
                    }
                ],
            },
            format="json",
        )

    assert response.status_code == 200
    scheduler.assert_not_called()
    normalized_filter = captured["query_config"]["metrics"][0]["filters"][0]
    assert {
        key: value
        for key, value in normalized_filter.items()
        if key != "canonical_filter"
    } == {
        "metric_name": "status",
        "metric_type": "system_metric",
        "operator": "equal_to",
        "source": "traces",
        "value": "OK",
    }
    assert normalized_filter["canonical_filter"] == {
        "column_id": "status",
        "source": "traces",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "OK",
            "col_type": "SYSTEM_METRIC",
        },
    }


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["execute", "preview"])
def test_widget_query_serves_inline_rollup_without_scheduling_worker(
    action,
    auth_client,
    dashboard,
    dashboard_widget,
    observe_project,
):
    query_config = {
        "project_ids": [str(observe_project.id)],
        "granularity": "day",
        "time_range": {"preset": "7D"},
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ],
    }
    dashboard_widget.query_config = query_config
    dashboard_widget.save(update_fields=["query_config"])

    rollup_analytics = MagicMock()
    rollup_analytics.execute_ch_query.return_value = SimpleNamespace(
        data=[
            {
                "time_bucket": datetime(2026, 8, 1, tzinfo=UTC),
                "metric_0": 12.0,
            }
        ],
        columns=["time_bucket", "metric_0"],
    )

    with (
        patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True),
        patch("tracer.views.dashboard.read_or_schedule_exact_snapshot") as scheduler,
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService",
            return_value=rollup_analytics,
        ),
        patch(
            "tracer.views.dashboard.get_clickhouse_client",
            side_effect=AssertionError("public poll must not use legacy ClickHouse"),
        ),
    ):
        if action == "execute":
            response = auth_client.post(
                f"/tracer/dashboard/{dashboard.id}/widgets/"
                f"{dashboard_widget.id}/query/?refresh=true"
            )
        else:
            response = auth_client.post(
                f"/tracer/dashboard/{dashboard.id}/widgets/preview/?refresh=true",
                {"query_config": query_config},
                format="json",
            )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["query_status"] == "complete"
    assert result["query_complete"] is True
    assert result["query_sampled"] is False
    assert result["query_exact"] is False
    assert result["query_provenance"] == "materialized_rollup"
    scheduler.assert_not_called()
