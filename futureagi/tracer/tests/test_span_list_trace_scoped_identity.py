"""Regression coverage for trace-scoped OTel span identities in span lists."""

import inspect
from datetime import datetime
from types import SimpleNamespace

import pytest
from rest_framework import status

from tracer.selectors.trace_filter_reads import BoundedFilterPage
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.v2.span_selectors import merge_content_rows
from tracer.views import observation_span as observation_span_view
from tracer.views.observation_span import ObservationSpanView

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
CONFIG_ID = "22222222-2222-2222-2222-222222222222"
LABEL_ID = "33333333-3333-3333-3333-333333333333"


def _builder() -> SpanListQueryBuilder:
    return SpanListQueryBuilder(
        project_id=PROJECT_ID,
        eval_config_ids=[CONFIG_ID],
        annotation_label_ids=[LABEL_ID],
    )


class _EmptyConfigQuery:
    def filter(self, *args, **kwargs):
        return self

    def select_related(self, *args):
        return []


class _BoundedNonObserveBuilder:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def bounded_filter_degraded_error_code(self):
        return None

    def supports_bounded_filter_scan(self):
        return True


class _NoQueryAnalytics:
    def execute_ch_query(self, *args, **kwargs):
        raise AssertionError(
            "incomplete or empty bounded pages must not query enrichment"
        )


def _bounded_page(*, rows, complete):
    return BoundedFilterPage(
        rows=rows,
        has_more=False,
        complete=complete,
        status="complete" if complete else "degraded",
        error_code=None if complete else "read_budget_exceeded",
        total_rows_lower_bound=len(rows),
        elapsed_ms=750.0,
        query_count=2,
        rows_returned=len(rows),
        result_payload_bytes=0,
        attempts=(),
    )


def _run_non_observe_bounded_page(monkeypatch, page):
    monkeypatch.setattr(
        observation_span_view,
        "SpanListQueryBuilderV2",
        _BoundedNonObserveBuilder,
    )
    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
        lambda **_kwargs: page,
    )
    monkeypatch.setattr(
        observation_span_view,
        "CustomEvalConfig",
        SimpleNamespace(objects=_EmptyConfigQuery()),
    )
    monkeypatch.setattr(
        observation_span_view,
        "get_annotation_labels_for_project",
        lambda _project_id: [],
    )
    monkeypatch.setattr(observation_span_view, "get_default_span_config", lambda: [])
    monkeypatch.setattr(
        observation_span_view,
        "update_column_config_based_on_eval_config",
        lambda config, _evals: config,
    )
    monkeypatch.setattr(
        observation_span_view,
        "update_span_column_config_based_on_annotations",
        lambda config, _labels: config,
    )

    return ObservationSpanView()._list_spans_non_observe_clickhouse(
        request=SimpleNamespace(),
        project_version_id="44444444-4444-4444-4444-444444444444",
        project_version=SimpleNamespace(project_id=PROJECT_ID),
        analytics=_NoQueryAnalytics(),
        validated_data={
            "filters": [
                {
                    "column_id": "final_status",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "Rejected",
                        "col_type": "SPAN_ATTRIBUTE",
                    },
                }
            ],
            "page_number": 0,
            "page_size": 25,
        },
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "rows",
    [
        [],
        [
            {
                "project_id": PROJECT_ID,
                "trace_id": "trace-a",
                "id": "partial-span",
                "start_time": datetime(2026, 7, 30, 12, 0),
            }
        ],
    ],
    ids=["empty-incomplete", "partial-incomplete"],
)
def test_non_observe_incomplete_bounded_page_fails_closed(monkeypatch, rows):
    response = _run_non_observe_bounded_page(
        monkeypatch,
        _bounded_page(rows=rows, complete=False),
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.data["type"] == "service_unavailable"
    assert response.data["code"] == "service_unavailable"
    assert (
        response.data["detail"]
        == "Filtered span data is temporarily unavailable. Please retry."
    )
    assert "table" not in response.data
    assert "read_budget_exceeded" not in str(response.data)


@pytest.mark.unit
def test_non_observe_complete_empty_bounded_page_is_success(monkeypatch):
    response = _run_non_observe_bounded_page(
        monkeypatch,
        _bounded_page(rows=[], complete=True),
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["result"] == {
        "column_config": [],
        "metadata": {
            "total_rows": 0,
            "total_rows_is_lower_bound": True,
            "has_more": False,
            "query_complete": True,
            "query_status": "complete",
            "query_error_code": None,
            "query_elapsed_ms": 750.0,
            "query_count": 2,
            "query_rows_returned": 0,
            "query_result_payload_bytes": 0,
        },
        "table": [],
    }


@pytest.mark.unit
def test_non_observe_eval_config_discovery_does_not_read_pg_telemetry():
    source = inspect.getsource(ObservationSpanView._list_spans_non_observe_clickhouse)

    assert "EvalLogger.objects" not in source
    assert "get_eval_config_ids_with_data_ch" in source


@pytest.mark.unit
def test_non_observe_list_has_no_pg_telemetry_fallback():
    source = inspect.getsource(ObservationSpanView.list_spans)

    assert "_list_spans_postgres" not in source


@pytest.mark.unit
@pytest.mark.parametrize(
    "method_name",
    ["_list_spans_clickhouse", "_list_spans_non_observe_clickhouse"],
)
def test_span_list_rows_expose_project_scoped_physical_identity(method_name):
    """Keep the wire row identity aligned with the physical CH25 span key."""

    source = inspect.getsource(getattr(ObservationSpanView, method_name))

    assert '"project_id": str(row.get("project_id", ""))' in source
    assert '"trace_id": str(row.get("trace_id", ""))' in source
    assert '"span_id": span_id' in source
    assert '"start_time": row.get("start_time")' in source


@pytest.mark.unit
def test_content_query_uses_physical_identity_and_latest_version():
    started = datetime(2026, 7, 30, 12, 0)
    sql, params = _builder().build_content_query(
        ["shared"],
        span_identities=[
            (PROJECT_ID, "trace-a", "shared", started),
            (PROJECT_ID, "trace-b", "shared", started),
        ],
    )

    assert "toUnixTimestamp64Micro(start_time)" in sql
    assert "IN %(content_span_identities)s" in sql
    assert "ORDER BY _peerdb_version DESC" in sql
    assert "LIMIT 1 BY project_id, trace_id, id, start_time" in sql
    assert params["content_span_identities"] == (
        (PROJECT_ID, "trace-a", "shared", 1_785_412_800_000_000),
        (PROJECT_ID, "trace-b", "shared", 1_785_412_800_000_000),
    )
    assert params["content_span_dates"] == (started.date(),)


@pytest.mark.unit
def test_content_merge_does_not_cross_trace_or_physical_span():
    first = datetime(2026, 7, 30, 12, 0)
    second = datetime(2026, 7, 30, 12, 1)
    rows = [
        {
            "project_id": "project-a",
            "trace_id": "trace-a",
            "id": "shared",
            "start_time": first,
        },
        {
            "project_id": "project-a",
            "trace_id": "trace-b",
            "id": "shared",
            "start_time": first,
        },
        {
            "project_id": "project-a",
            "trace_id": "trace-a",
            "id": "shared",
            "start_time": second,
        },
    ]
    content = [
        {
            "project_id": "project-a",
            "trace_id": "trace-a",
            "id": "shared",
            "start_time": first,
            "input": "a-first",
        },
        {
            "project_id": "project-a",
            "trace_id": "trace-b",
            "id": "shared",
            "start_time": first,
            "input": "b-first",
        },
        {
            "project_id": "project-a",
            "trace_id": "trace-a",
            "id": "shared",
            "start_time": second,
            "input": "a-second",
        },
    ]

    merge_content_rows(
        rows,
        content,
        id_key=("project_id", "trace_id", "id", "start_time"),
        keys=("input",),
    )

    assert [row["input"] for row in rows] == ["a-first", "b-first", "a-second"]


@pytest.mark.unit
def test_eval_query_and_pivot_keep_same_span_id_in_two_traces_separate():
    entities = [("trace-a", "shared"), ("trace-b", "shared")]
    sql, params = _builder().build_eval_query(["shared"], span_entities=entities)

    assert "NOT isNull(trace_id)" in sql
    assert "(toString(trace_id), observation_span_id)" in sql
    assert params["eval_span_entities"] == tuple(entities)

    rows = [
        {
            "trace_id": "trace-a",
            "observation_span_id": "shared",
            "eval_config_id": CONFIG_ID,
            "avg_score": 0.25,
            "success_count": 1,
        },
        {
            "trace_id": "trace-b",
            "observation_span_id": "shared",
            "eval_config_id": CONFIG_ID,
            "avg_score": 0.75,
            "success_count": 1,
        },
        {
            "trace_id": None,
            "observation_span_id": "shared",
            "eval_config_id": CONFIG_ID,
            "avg_score": 1.0,
            "success_count": 1,
        },
    ]
    pivot = SpanListQueryBuilder.pivot_eval_results(rows, key_by_trace=True)

    assert pivot[("trace-a", "shared")][CONFIG_ID] == 25.0
    assert pivot[("trace-b", "shared")][CONFIG_ID] == 75.0
    assert len(pivot) == 2


@pytest.mark.unit
def test_annotation_query_and_pivot_fail_closed_without_trace_identity():
    entities = [("trace-a", "shared"), ("trace-b", "shared")]
    sql, params = _builder().build_annotation_query(["shared"], span_entities=entities)

    assert "NOT isNull(trace_id)" in sql
    assert "(toString(trace_id), observation_span_id)" in sql
    assert params["annotation_span_entities"] == tuple(entities)

    rows = [
        {
            "trace_id": "trace-a",
            "observation_span_id": "shared",
            "label_id": LABEL_ID,
            "value": '{"text":"a"}',
        },
        {
            "trace_id": "trace-b",
            "observation_span_id": "shared",
            "label_id": LABEL_ID,
            "value": '{"text":"b"}',
        },
        {
            "trace_id": None,
            "observation_span_id": "shared",
            "label_id": LABEL_ID,
            "value": '{"text":"legacy-ambiguous"}',
        },
    ]
    pivot = SpanListQueryBuilder.pivot_annotation_results(
        rows, {LABEL_ID: "text"}, key_by_trace=True
    )

    assert pivot[("trace-a", "shared")][LABEL_ID] == "a"
    assert pivot[("trace-b", "shared")][LABEL_ID] == "b"
    assert len(pivot) == 2
