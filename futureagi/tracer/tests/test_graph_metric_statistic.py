"""Every Observe system-metric graph response names its statistic.

The latency series is the t-digest median on every path, so the response
says ``metric_statistic: "median"`` and the UI labels it "Latency (median)".
These tests pin:

* the statistic maps against the series each builder actually publishes;
* the stamp on every envelope the public entry points return (complete,
  cached, pending, degraded and refused-to-background), for trace/span,
  session and users graphs, and its absence on eval series;
* the ChartsView bundle's declared ``system_metric_statistics``;
* the serializer contract the frontend's generated parser follows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tracer.services.clickhouse import graph_dispatch, session_graph
from tracer.services.clickhouse.exact_graph_reads import ExactGraphReadError
from tracer.services.clickhouse.graph_metric_statistic import (
    METRIC_STATISTIC_CHOICES,
    SESSION_METRIC_STATISTICS,
    TRACE_METRIC_STATISTICS,
    USER_METRIC_STATISTICS,
    chart_bundle_statistics,
    system_metric_statistic,
)

PROJECT = str(uuid4())
ORG = str(uuid4())

_MODEL_FILTER = {
    "column_id": "model",
    "filter_config": {
        "filter_type": "text",
        "filter_op": "equals",
        "filter_value": "gpt-4",
        "col_type": "SYSTEM_METRIC",
    },
}


def _complete(metric_id="latency"):
    return {
        "metric_name": metric_id,
        "data": [],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }


def _pending(metric_id="latency"):
    return graph_dispatch._pending_graph_payload(metric_id)


class _Analytics:
    supports_per_query_read_settings = True


class _NoReadPolicyAnalytics:
    supports_per_query_read_settings = False


# ---------------------------------------------------------------------------
# Maps
# ---------------------------------------------------------------------------


class TestStatisticMaps:
    def test_latency_is_the_median_on_every_surface(self):
        for surface in ("trace", "session", "users"):
            assert system_metric_statistic(surface, "latency") == "median"

    def test_choices_match_the_serializer_enum(self):
        from tracer.serializers.filters import OBSERVE_GRAPH_METRIC_STATISTIC_CHOICES

        assert METRIC_STATISTIC_CHOICES == OBSERVE_GRAPH_METRIC_STATISTIC_CHOICES
        for statistics in (
            TRACE_METRIC_STATISTICS,
            SESSION_METRIC_STATISTICS,
            USER_METRIC_STATISTICS,
        ):
            assert set(statistics.values()) <= set(METRIC_STATISTIC_CHOICES)

    def test_trace_map_covers_exactly_the_published_series(self):
        from tracer.services.clickhouse.query_builders.time_series import (
            TimeSeriesQueryBuilder,
        )

        builder = TimeSeriesQueryBuilder(project_id=PROJECT, filters=[], interval="day")
        builder.start_date = datetime(2026, 7, 1, tzinfo=UTC)
        builder.end_date = datetime(2026, 7, 2, tzinfo=UTC)
        published = set(builder.format_result([], []))
        assert set(TRACE_METRIC_STATISTICS) == published
        assert set(graph_dispatch._SYSTEM_METRIC_FIELDS) == published

    def test_session_map_covers_exactly_the_accepted_metrics(self):
        assert set(SESSION_METRIC_STATISTICS) == set(
            session_graph.SESSION_SYSTEM_METRICS
        )

    def test_users_map_covers_exactly_the_published_series(self):
        from tracer.services.clickhouse.v2.query_builders.user_time_series import (
            UserTimeSeriesQueryBuilderV2,
        )

        builder = UserTimeSeriesQueryBuilderV2(
            project_id=PROJECT, filters=[], interval="day"
        )
        builder.start_date = datetime(2026, 7, 1, tzinfo=UTC)
        builder.end_date = datetime(2026, 7, 2, tzinfo=UTC)
        assert set(USER_METRIC_STATISTICS) == set(builder.format_result([], []))

    @pytest.mark.parametrize(
        ("surface", "metric_id", "expected"),
        [
            # The trace dispatcher publishes latency for unknown/blank ids.
            ("trace", "time_to_first_token", "median"),
            ("trace", "", "median"),
            ("trace", None, "median"),
            ("trace", " Latency ", "median"),
            ("trace", "tokens", "sum"),
            ("trace", "traffic", "count"),
            ("trace", "cost", "mean"),
            ("trace", "error_rate", "percentage"),
            ("session", "session_count", "count"),
            ("session", "avg_duration", "mean"),
            ("session", "total_cost", "sum"),
            ("session", "unknown", None),
            # The users reader publishes active_users for unknown ids.
            ("users", "unknown", "count"),
            ("users", "LATENCY", "count"),
            ("users", "avg_traces_per_user", "mean"),
            ("users", "total_cost", "sum"),
        ],
    )
    def test_resolution_follows_the_published_series(
        self, surface, metric_id, expected
    ):
        assert system_metric_statistic(surface, metric_id) == expected

    def test_chart_bundle_statistics(self):
        assert chart_bundle_statistics() == {
            "latency": "median",
            "tokens": "sum",
            "cost": "mean",
            "traffic": "count",
        }


# ---------------------------------------------------------------------------
# Trace and span graph envelopes
# ---------------------------------------------------------------------------


def _trace_graph(metric_id="latency", *, filters=(), analytics=None, **kwargs):
    return graph_dispatch.fetch_system_metric_graph_ch(
        analytics=analytics or _Analytics(),
        project_id=PROJECT,
        filters=list(filters),
        interval="day",
        metric_id=metric_id,
        **kwargs,
    )


class TestTraceGraphEnvelopes:
    @pytest.mark.parametrize(
        ("metric_id", "expected"),
        [("latency", "median"), ("tokens", "sum"), ("bogus", "median")],
    )
    def test_unfiltered_rollup(self, monkeypatch, metric_id, expected):
        monkeypatch.setattr(
            graph_dispatch,
            "_fetch_rollup_system_metric_graph",
            lambda **call: _complete(call["metric_id"]),
        )
        assert _trace_graph(metric_id)["metric_statistic"] == expected

    def test_unfiltered_degraded_without_read_policy(self):
        response = _trace_graph(analytics=_NoReadPolicyAnalytics())
        assert response["query_status"] == "degraded"
        assert response["metric_statistic"] == "median"

    def test_filtered_cached_complete(self, monkeypatch):
        monkeypatch.setattr(
            graph_dispatch, "_read_or_refresh_exact_graph", lambda **_: _complete()
        )
        response = _trace_graph(filters=[_MODEL_FILTER], organization_id=ORG)
        assert response["query_status"] == "complete"
        assert response["metric_statistic"] == "median"

    def test_filtered_pending_refresh(self, monkeypatch):
        monkeypatch.setattr(
            graph_dispatch, "_read_or_refresh_exact_graph", lambda **_: _pending()
        )
        response = _trace_graph(filters=[_MODEL_FILTER], organization_id=ORG)
        assert response["query_status"] == "pending"
        assert response["metric_statistic"] == "median"

    def test_filtered_refused_to_background(self, monkeypatch):
        monkeypatch.setattr(
            graph_dispatch, "_read_or_refresh_exact_graph", lambda **_: None
        )
        monkeypatch.setattr(
            graph_dispatch,
            "_affordable_raw_graph_seed",
            lambda **_: graph_dispatch._GraphReadUnaffordable(estimated_rows=None),
        )
        monkeypatch.setattr(
            graph_dispatch,
            "_schedule_unaffordable_graph_read",
            lambda **call: call["pending_payload"],
        )
        response = _trace_graph(filters=[_MODEL_FILTER], organization_id=ORG)
        assert response["query_status"] == "pending"
        assert response["metric_statistic"] == "median"

    def test_filtered_direct_complete_and_degraded(self, monkeypatch):
        monkeypatch.setattr(
            graph_dispatch, "_read_or_refresh_exact_graph", lambda **_: None
        )
        monkeypatch.setattr(
            graph_dispatch, "_affordable_raw_graph_seed", lambda **_: None
        )
        monkeypatch.setattr(
            graph_dispatch,
            "_fetch_direct_raw_system_metric_graph",
            lambda **call: _complete(call["metric_id"]),
        )
        assert _trace_graph(filters=[_MODEL_FILTER])["metric_statistic"] == "median"

        def fail(**_):
            raise ExactGraphReadError("boom")

        monkeypatch.setattr(
            graph_dispatch, "_fetch_direct_raw_system_metric_graph", fail
        )
        response = _trace_graph("cost", filters=[_MODEL_FILTER])
        assert response["query_status"] == "degraded"
        assert response["metric_statistic"] == "mean"


# ---------------------------------------------------------------------------
# Session graph envelopes
# ---------------------------------------------------------------------------


def _session_graph(config, *, filters=(), analytics=None):
    return session_graph.fetch_session_graph_ch(
        analytics=analytics or _Analytics(),
        project_id=PROJECT,
        filters=list(filters),
        interval="day",
        req_data_config=config,
    )


class TestSessionGraphEnvelopes:
    def test_rollup_latency(self, monkeypatch):
        monkeypatch.setattr(
            session_graph,
            "_fetch_rollup_system_metric_graph",
            lambda **call: _complete(call["metric_id"]),
        )
        response = _session_graph({"type": "SYSTEM_METRIC", "id": "latency"})
        assert response["metric_statistic"] == "median"

    def test_rollup_degraded_without_read_policy(self):
        response = _session_graph(
            {"type": "SYSTEM_METRIC", "id": "latency"},
            analytics=_NoReadPolicyAnalytics(),
        )
        assert response["query_status"] == "degraded"
        assert response["metric_statistic"] == "median"

    @pytest.mark.parametrize(
        ("metric_id", "expected"),
        [("latency", "median"), ("tokens", "sum"), ("session_count", "count")],
    )
    def test_exact_snapshot_pending(self, monkeypatch, metric_id, expected):
        monkeypatch.setattr(
            session_graph,
            "read_or_schedule_exact_snapshot",
            lambda namespace, identity, **call: call["pending_payload"],
        )
        response = _session_graph(
            {"type": "SYSTEM_METRIC", "id": metric_id}, filters=[_MODEL_FILTER]
        )
        assert response["query_status"] == "pending"
        assert response["metric_statistic"] == expected

    def test_bounded_average_traces_per_session(self, monkeypatch):
        monkeypatch.setattr(
            session_graph,
            "_fetch_system_metric_graph",
            lambda **call: _complete(call["metric_id"]),
        )
        response = _session_graph(
            {"type": "SYSTEM_METRIC", "id": "avg_traces_per_session"}
        )
        assert response["metric_statistic"] == "mean"

    def test_eval_series_carry_no_statistic(self, monkeypatch):
        monkeypatch.setattr(
            session_graph, "fetch_eval_graph_ch", lambda **_: _complete("eval")
        )
        response = _session_graph({"type": "EVAL", "id": str(uuid4())})
        assert "metric_statistic" not in response


# ---------------------------------------------------------------------------
# Users aggregate graph envelopes
# ---------------------------------------------------------------------------


def _users_graph(metric_id="latency", **kwargs):
    return graph_dispatch.fetch_user_system_metric_graph_ch(
        analytics=_Analytics(),
        project_id=PROJECT,
        filters=[],
        interval="day",
        metric_id=metric_id,
        **kwargs,
    )


class TestUsersGraphEnvelopes:
    def test_cached_complete(self, monkeypatch):
        monkeypatch.setattr(
            graph_dispatch, "_read_or_refresh_exact_graph", lambda **_: _complete()
        )
        assert _users_graph(organization_id=ORG)["metric_statistic"] == "median"

    def test_refused_to_background(self, monkeypatch):
        monkeypatch.setattr(
            graph_dispatch, "_read_or_refresh_exact_graph", lambda **_: None
        )
        monkeypatch.setattr(
            graph_dispatch,
            "_affordable_user_graph_read",
            lambda **_: graph_dispatch._GraphReadUnaffordable(estimated_rows=None),
        )
        monkeypatch.setattr(
            graph_dispatch,
            "_schedule_unaffordable_graph_read",
            lambda **call: call["pending_payload"],
        )
        response = _users_graph(organization_id=ORG)
        assert response["query_status"] == "pending"
        assert response["metric_statistic"] == "median"

    @pytest.mark.parametrize(
        ("metric_id", "expected"),
        [("latency", "median"), ("total_cost", "sum"), ("bogus", "count")],
    )
    def test_direct_complete(self, monkeypatch, metric_id, expected):
        monkeypatch.setattr(
            graph_dispatch, "_affordable_user_graph_read", lambda **_: None
        )
        monkeypatch.setattr(
            graph_dispatch,
            "read_exact_user_system_graph",
            lambda **call: _complete(call["metric_id"]),
        )
        assert _users_graph(metric_id)["metric_statistic"] == expected

    def test_direct_degraded(self, monkeypatch):
        monkeypatch.setattr(
            graph_dispatch, "_affordable_user_graph_read", lambda **_: None
        )

        def fail(**_):
            raise ExactGraphReadError("boom")

        monkeypatch.setattr(graph_dispatch, "read_exact_user_system_graph", fail)
        response = _users_graph()
        assert response["query_status"] == "degraded"
        assert response["metric_statistic"] == "median"


# ---------------------------------------------------------------------------
# ChartsView (/project/get_graph_data/, /charts/fetch_graph/)
# ---------------------------------------------------------------------------


class TestChartsViewSeries:
    @pytest.mark.parametrize(
        ("metric_id", "expected"),
        [("latency", "median"), ("tokens", "sum"), ("bogus", "median")],
    )
    def test_single_series_is_stamped(self, monkeypatch, metric_id, expected):
        from tracer.utils import graphs_optimized

        monkeypatch.setattr(
            graphs_optimized,
            "_read_direct_system_metrics",
            lambda **_: {
                "latency": [],
                "tokens": [],
                "cost": [],
                "traffic": [],
                "query_complete": True,
                "query_status": "complete",
                "query_sampled": False,
            },
        )
        response = graphs_optimized.get_system_metric_data(
            interval="day",
            filters=[],
            property="average",
            req_data_config={"type": "SYSTEM_METRIC", "id": metric_id},
            system_metric_filters={"project_id": PROJECT},
            observe_type="charts",
        )
        assert response["metric_statistic"] == expected

    def test_charts_fetch_graph_bundle_declares_series_statistics(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from unittest import mock

        from tracer.services.clickhouse import graph_action_deadline
        from tracer.views import charts

        bundle = {
            "latency": [],
            "tokens": [],
            "cost": [],
            "traffic": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }
        validated = {
            "req_data_config": {"type": "SYSTEM_METRICS", "id": ""},
            "interval": "day",
            "filters": [],
            "property": "average",
            "project_id": PROJECT,
            "allow_sampled": False,
            "refresh": False,
        }

        def is_valid(serializer, *, raise_exception=False):
            serializer._validated_data = validated
            serializer._errors = {}
            return True

        view = charts.ChartsView()
        view._gm = SimpleNamespace(success_response=lambda result: ("ok", result))
        request = SimpleNamespace(
            method="GET", data={}, query_params={}, workspace=SimpleNamespace(id=ORG)
        )
        project = SimpleNamespace(id=PROJECT, organization_id=ORG)
        with (
            mock.patch.object(
                graph_action_deadline, "start_graph_action_deadline", return_value=None
            ),
            mock.patch.object(
                graph_action_deadline,
                "finish_graph_action_response",
                side_effect=lambda _deadline, response: response,
            ),
            mock.patch.object(
                charts, "graph_action_postgres_budget", return_value=nullcontext()
            ),
            mock.patch.object(charts.FetchGraphSerializer, "is_valid", new=is_valid),
            mock.patch.object(
                charts,
                "get_request_organization",
                return_value=SimpleNamespace(id=ORG),
            ),
            mock.patch.object(
                charts, "bind_request_my_annotations_principal", return_value=[]
            ),
            mock.patch.object(charts.Project.objects, "get", return_value=project),
            mock.patch.object(charts, "get_all_system_metrics", return_value=bundle),
        ):
            status_marker, result = charts.ChartsView.fetch_graph(view, request)

        assert status_marker == "ok"
        assert result == {
            **bundle,
            "system_metric_statistics": chart_bundle_statistics(),
        }


# ---------------------------------------------------------------------------
# Serializer contract (what swagger and the generated zod parser publish)
# ---------------------------------------------------------------------------


class TestResponseContract:
    def test_observe_graph_result_declares_metric_statistic(self):
        from tracer.serializers.filters import ObserveGraphDataResponseSerializer

        ok = ObserveGraphDataResponseSerializer(
            data={
                "status": True,
                "result": {**_complete(), "metric_statistic": "median"},
            }
        )
        assert ok.is_valid(), ok.errors
        assert ok.validated_data["result"]["metric_statistic"] == "median"

        wrong = ObserveGraphDataResponseSerializer(
            data={
                "status": True,
                "result": {**_complete(), "metric_statistic": "average"},
            }
        )
        assert not wrong.is_valid()

    def test_project_graph_result_declares_bundle_statistics(self):
        from tracer.serializers.project import ProjectGraphDataResponseSerializer

        serializer = ProjectGraphDataResponseSerializer(
            data={
                "status": True,
                "result": {
                    "system_metrics": {"latency": [], "tokens": []},
                    "system_metric_statistics": chart_bundle_statistics(),
                    "evaluations": {},
                },
            }
        )
        assert serializer.is_valid(), serializer.errors
        assert (
            serializer.validated_data["result"]["system_metric_statistics"]["latency"]
            == "median"
        )

    def test_swagger_publishes_the_statistic_fields(self):
        import json
        from pathlib import Path

        swagger_path = (
            Path(__file__).resolve().parents[3]
            / "api_contracts"
            / "openapi"
            / "swagger.json"
        )
        definitions = json.loads(swagger_path.read_text())["definitions"]
        observe = definitions["ObserveGraphDataResult"]["properties"]
        assert observe["metric_statistic"]["enum"] == list(METRIC_STATISTIC_CHOICES)
        project = definitions["ProjectGraphDataResult"]["properties"]
        assert project["system_metric_statistics"]["additionalProperties"][
            "enum"
        ] == list(METRIC_STATISTIC_CHOICES)
