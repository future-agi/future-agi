"""Focused contracts for bounded eval-task attribute discovery."""

from types import SimpleNamespace

from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.views.eval_task import _hydrate_usage_sources
from tracer.views.observation_span import ObservationSpanView


def test_attribute_inventory_marks_healthy_bounded_read_as_sampled(monkeypatch):
    analytics = AnalyticsQueryService()
    monkeypatch.setattr(
        analytics,
        "get_span_attribute_keys_ch_for_projects",
        lambda project_ids: [{"key": "customer_tier", "type": "string"}],
    )

    result = analytics.get_span_attribute_keys_ch("project-id")

    assert list(result) == [{"key": "customer_tier", "type": "string"}]
    assert result.query_complete is False
    assert result.query_status == "sampled"
    assert result.query_error_code == "sample_limit"
    assert result.query_sampled is True


def test_attribute_inventory_failure_keeps_guaranteed_key_and_marks_degraded(
    monkeypatch,
):
    analytics = AnalyticsQueryService()

    def _timeout(project_ids):
        raise TimeoutError("internal ClickHouse detail must not reach the response")

    monkeypatch.setattr(
        analytics,
        "get_span_attribute_keys_ch_for_projects",
        _timeout,
    )

    result = analytics.get_span_attribute_keys_ch("project-id")

    assert result == [{"key": "final_status", "type": "string"}]
    assert result.query_complete is False
    assert result.query_status == "degraded"
    assert result.query_error_code == "read_budget_exceeded"
    assert result.query_sampled is False


def test_eval_picker_keeps_result_array_and_exposes_query_state():
    view = ObservationSpanView()

    response = view._eval_attribute_list_response(
        ["final_status"],
        view._attribute_query_state(
            query_status="degraded",
            query_error_code="read_budget_exceeded",
        ),
    )

    assert response.data["result"] == ["final_status"]
    assert response.data["query_complete"] is False
    assert response.data["query_status"] == "degraded"
    assert response.data["query_error_code"] == "read_budget_exceeded"
    assert response.data["query_sampled"] is False


def test_cardinality_failure_is_degraded_and_not_cached(monkeypatch):
    from tracer.views import observation_span as span_views

    def _timeout(self, query, params=None, timeout_ms=None, settings=None):
        raise TimeoutError("bounded cardinality timeout")

    cache_sets = []
    monkeypatch.setattr(AnalyticsQueryService, "execute_ch_query", _timeout)
    monkeypatch.setattr(span_views.django_cache, "get", lambda key: None)
    monkeypatch.setattr(
        span_views.django_cache,
        "set",
        lambda key, value, timeout: cache_sets.append((key, value, timeout)),
    )

    max_spans, max_traces, query_state = (
        ObservationSpanView()._observed_mapping_cardinality_with_status("project-id")
    )

    assert (max_spans, max_traces) == (1, 1)
    assert query_state == {
        "query_complete": False,
        "query_status": "degraded",
        "query_sampled": False,
        "query_error_code": "read_budget_exceeded",
    }
    assert cache_sets == []


def test_usage_hydration_failure_preserves_page_contract_and_marks_degraded(
    monkeypatch,
):
    import tracer.services.clickhouse.v2 as clickhouse_v2

    def _timeout():
        raise TimeoutError("bounded source hydration timeout")

    monkeypatch.setattr(clickhouse_v2, "get_reader", _timeout)
    logs = [
        SimpleNamespace(
            observation_span_id="span-1",
            trace_session_id=None,
        )
    ]

    spans, sessions, query_state = _hydrate_usage_sources(
        logs,
        project_id="project-id",
    )

    assert spans == {}
    assert sessions == {}
    assert query_state == {
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "read_budget_exceeded",
    }


def test_usage_hydration_without_source_ids_is_complete():
    spans, sessions, query_state = _hydrate_usage_sources(
        [],
        project_id="project-id",
    )

    assert spans == {}
    assert sessions == {}
    assert query_state == {
        "query_complete": True,
        "query_status": "complete",
    }


def test_usage_hydration_span_id_shortfall_is_degraded(monkeypatch):
    import tracer.services.clickhouse.v2 as clickhouse_v2

    class _EmptyReader:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def list_by_ids(self, span_ids, *, include_heavy, project_id):
            return []

    monkeypatch.setattr(clickhouse_v2, "get_reader", _EmptyReader)
    logs = [
        SimpleNamespace(
            observation_span_id="missing-span",
            trace_session_id=None,
        )
    ]

    spans, sessions, query_state = _hydrate_usage_sources(
        logs,
        project_id="project-id",
    )

    assert spans == {}
    assert sessions == {}
    assert query_state == {
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "query_failed",
    }
