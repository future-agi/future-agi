"""Regression tests for partial list-page enrichment failures."""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


def _view(view_class):
    view = view_class.__new__(view_class)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        bad_request=lambda message: ("bad_request", message),
    )
    return view


class _SpanBuilder:
    def __init__(self, **kwargs):
        self.params = {}
        self.filters = kwargs.get("filters", [])
        self.page_number = kwargs.get("page_number", 0)
        self.page_size = kwargs.get("page_size", 50)
        self.sort_params = kwargs.get("sort_params", [])

    def requires_bounded_filter_scan(self):
        return False

    def build(self, since=None, **kwargs):
        return "phase_one", {}

    def parse_time_range(self, filters):
        end = datetime.now()
        return end - timedelta(days=1), end

    def supports_latest_attribute_page(self):
        return False

    def build_content_query(self, span_ids):
        return "content", {"content_span_ids": tuple(span_ids)}

    def build_count_query(self):
        return "count", {}


class _SessionBuilder:
    def __init__(self, **kwargs):
        pass

    def build(self):
        return "phase_one", {}

    def build_content_query(self, session_ids):
        return "content", {"content_session_ids": tuple(session_ids)}

    def build_span_attributes_query(self, session_ids):
        return "attributes", {"session_ids": tuple(session_ids)}

    @staticmethod
    def format_sessions(rows, columns):
        return [dict(zip(columns, row, strict=True)) for row in rows]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (TimeoutError("private ClickHouse timeout"), "read_budget_exceeded"),
        (RuntimeError("private query contract details"), "query_failed"),
    ],
)
def test_observe_span_enrichment_failure_preserves_rows_with_safe_metadata(
    failure, expected_code
):
    from tracer.views.observation_span import ObservationSpanView

    now = datetime.now()
    span_id = "span-enrichment-regression"
    phase_one_row = {
        "id": span_id,
        "trace_id": str(uuid.uuid4()),
        "created_at": now,
        "start_time": now,
        "observation_type": "llm",
        "name": "synthetic span",
        "status": "OK",
        "cost": 0,
        "end_user_id": None,
    }

    def _execute(query, params=None, **kwargs):
        if query == "phase_one":
            return SimpleNamespace(data=[dict(phase_one_row)])
        if query == "content":
            raise failure
        if query == "count":
            return SimpleNamespace(data=[{"total": 1}])
        raise AssertionError(f"unexpected query: {query}")

    analytics = mock.MagicMock()
    analytics.execute_ch_query.side_effect = _execute
    organization = SimpleNamespace(id=uuid.uuid4())
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    view = _view(ObservationSpanView)

    with (
        mock.patch(
            "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
            return_value=_SpanBuilder,
        ),
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as configs,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
    ):
        configs.objects.filter.return_value.select_related.return_value = []
        status, payload = view._list_spans_clickhouse(
            request,
            project_id=str(uuid.uuid4()),
            validated_data={"filters": [], "page_number": 0, "page_size": 25},
            analytics=analytics,
            org_project_ids=None,
            org=organization,
        )

    assert status == "ok"
    assert [row["span_id"] for row in payload["table"]] == [span_id]
    assert payload["metadata"]["query_complete"] is False
    assert payload["metadata"]["query_status"] == "degraded"
    assert payload["metadata"]["query_error_code"] == expected_code
    assert "private " not in repr(payload)


@pytest.mark.unit
def test_prototype_span_content_timeout_preserves_rows_with_safe_metadata():
    from tracer.views.observation_span import ObservationSpanView

    now = datetime.now()
    span_id = "prototype-enrichment-regression"
    phase_one_row = {
        "id": span_id,
        "trace_id": str(uuid.uuid4()),
        "start_time": now,
        "observation_type": "llm",
        "name": "synthetic prototype span",
        "status": "OK",
    }

    def _execute(query, params=None, **kwargs):
        if query == "phase_one":
            return SimpleNamespace(data=[dict(phase_one_row)])
        if query == "content":
            raise TimeoutError("private ClickHouse timeout")
        if query == "count":
            return SimpleNamespace(data=[{"total": 1}])
        raise AssertionError(f"unexpected query: {query}")

    analytics = mock.MagicMock()
    analytics.execute_ch_query.side_effect = _execute
    view = _view(ObservationSpanView)
    project_id = uuid.uuid4()
    project_version = SimpleNamespace(project_id=project_id)

    with (
        mock.patch(
            "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
            return_value=_SpanBuilder,
        ),
        mock.patch("tracer.views.observation_span.CustomEvalConfig") as configs,
        mock.patch(
            "tracer.views.observation_span.get_annotation_labels_for_project",
            return_value=[],
        ),
    ):
        configs.objects.filter.return_value.select_related.return_value = []
        status, payload = view._list_spans_non_observe_clickhouse(
            SimpleNamespace(),
            project_version_id=str(uuid.uuid4()),
            project_version=project_version,
            analytics=analytics,
            validated_data={"filters": [], "page_number": 0, "page_size": 25},
        )

    assert status == "ok"
    assert [row["span_id"] for row in payload["table"]] == [span_id]
    assert payload["metadata"]["query_complete"] is False
    assert payload["metadata"]["query_status"] == "degraded"
    assert payload["metadata"]["query_error_code"] == "read_budget_exceeded"
    assert "private ClickHouse" not in repr(payload)


@pytest.mark.unit
def test_session_content_timeout_preserves_rows_with_safe_metadata():
    from tracer.views.trace_session import TraceSessionView

    session_id = str(uuid.uuid4())
    now = datetime.now()
    phase_one_row = {
        "session_id": session_id,
        "start_time": now,
        "trace_count": 1,
    }

    def _execute(query, params=None, **kwargs):
        if query == "phase_one":
            return SimpleNamespace(data=[dict(phase_one_row)])
        if query == "content":
            raise TimeoutError("private ClickHouse timeout")
        if query == "attributes":
            return SimpleNamespace(data=[])
        raise AssertionError(f"unexpected query: {query}")

    analytics = mock.MagicMock()
    analytics.execute_ch_query.side_effect = _execute
    organization = SimpleNamespace(id=uuid.uuid4())
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(organization=organization),
        query_params={},
    )
    view = _view(TraceSessionView)
    view._fetch_session_names = mock.Mock(return_value={})
    view._fetch_end_user_info = mock.Mock(return_value={})
    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, session_config=[])

    with (
        mock.patch(
            "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
            return_value=_SessionBuilder,
        ),
        mock.patch("tracer.views.trace_session.AnnotationsLabels") as labels,
    ):
        labels.objects.filter.return_value = []
        status, payload = view._list_sessions_clickhouse(
            request,
            project_id=project_id,
            project=project,
            analytics=analytics,
            validated_data={"filters": [], "page_number": 0, "page_size": 25},
            org_project_ids=None,
        )

    assert status == "ok"
    assert [row["session_id"] for row in payload["table"]] == [session_id]
    assert payload["metadata"]["query_complete"] is False
    assert payload["metadata"]["query_status"] == "degraded"
    assert payload["metadata"]["query_error_code"] == "read_budget_exceeded"
    assert "private ClickHouse" not in repr(payload)


@pytest.mark.unit
def test_trace_list_metadata_contract_accepts_safe_query_failure_code():
    from tracer.serializers.trace import TraceObserveListMetadataSerializer

    serializer = TraceObserveListMetadataSerializer(
        data={
            "total_rows": 1,
            "query_complete": False,
            "query_status": "degraded",
            "query_error_code": "query_failed",
        }
    )

    assert serializer.is_valid(), serializer.errors

    repo_root = Path(__file__).resolve().parents[3]
    swagger = json.loads(
        (repo_root / "api_contracts" / "openapi" / "swagger.json").read_text()
    )
    assert swagger["definitions"]["TraceObserveListMetadata"]["properties"][
        "query_error_code"
    ]["enum"] == ["read_budget_exceeded", "query_failed"]
