"""Failure-semantics guards for tracing list, graph, and detail boundaries."""

from contextlib import nullcontext
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from clickhouse_driver.errors import NetworkError, ServerException

from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.list_cursor import ListCursorError
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailNotFound,
    TraceDetailReadUnavailable,
)

PROJECT_ID = "11111111-1111-4111-8111-111111111111"


def _raise(exc):
    def fail(*_args, **_kwargs):
        raise exc

    return fail


def _result(response):
    return response.data.get("result", response.data)


def _assert_sanitized_400(response):
    assert response.status_code == 400
    assert "could not be loaded" in str(response.data)
    assert "private" not in str(response.data)


def _assert_sanitized_500(response):
    assert response.status_code == 500
    assert "could not be loaded" in str(response.data)
    assert response.data["code"] == "server_error"
    assert "private" not in str(response.data)


def _assert_sanitized_503(response):
    assert response.status_code == 503
    assert "temporarily unavailable" in str(response.data)
    assert response.data["code"] == "service_unavailable"
    assert "private" not in str(response.data)


def _trace_list_call(monkeypatch, exc):
    from tracer.views import trace as trace_view

    project_scope = MagicMock()
    project_scope.filter.return_value.first.return_value = SimpleNamespace(
        trace_type="observe"
    )
    monkeypatch.setattr(
        trace_view, "_project_queryset_for_request", lambda _request: project_scope
    )
    monkeypatch.setattr(
        trace_view, "_get_request_organization", lambda _request: object()
    )
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", MagicMock)
    monkeypatch.setattr(
        trace_view.TraceView,
        "_list_traces_of_session_clickhouse",
        _raise(exc),
    )
    request = SimpleNamespace(
        validated_query_data={"project_id": PROJECT_ID},
    )
    view = trace_view.TraceView()
    view.request = request
    return unwrap(trace_view.TraceView.list_traces_of_session)(view, request)


def _trace_version_list_call(monkeypatch, exc):
    from tracer.views import trace as trace_view

    project_version_scope = MagicMock()
    project_version_scope.filter.return_value.first.return_value = object()
    monkeypatch.setattr(
        trace_view,
        "_project_version_queryset_for_request",
        lambda _request: project_version_scope,
    )
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", MagicMock)
    monkeypatch.setattr(
        trace_view.TraceView,
        "_list_traces_clickhouse",
        _raise(exc),
    )
    request = SimpleNamespace(
        validated_query_data={"project_version_id": "project-version-1"}
    )
    view = trace_view.TraceView()
    view.request = request
    return unwrap(trace_view.TraceView.list_traces)(view, request)


def _span_list_call(monkeypatch, exc):
    from tracer.views import observation_span as span_view

    monkeypatch.setattr(
        span_view.Project.objects, "get", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        span_view, "_get_request_organization", lambda _request: object()
    )
    monkeypatch.setattr(span_view, "AnalyticsQueryService", MagicMock)
    monkeypatch.setattr(
        span_view.ObservationSpanView,
        "_list_spans_clickhouse",
        _raise(exc),
    )
    request = SimpleNamespace(validated_query_data={"project_id": PROJECT_ID})
    view = span_view.ObservationSpanView()
    view.request = request
    return unwrap(span_view.ObservationSpanView.list_spans_observe)(view, request)


def _span_version_list_call(monkeypatch, exc):
    from tracer.services.clickhouse import query_service
    from tracer.views import observation_span as span_view

    serializer = MagicMock()
    serializer.is_valid.return_value = True
    serializer.validated_data = {
        "project_version_id": "project-version-1",
        "filters": [],
    }
    monkeypatch.setattr(
        span_view, "SpanListQuerySerializer", lambda **_kwargs: serializer
    )
    monkeypatch.setattr(
        span_view.ProjectVersion.objects,
        "get",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        span_view, "_project_workspace_scope_q", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        span_view, "_get_request_organization", lambda _request: object()
    )
    monkeypatch.setattr(query_service, "AnalyticsQueryService", MagicMock)
    monkeypatch.setattr(
        span_view.ObservationSpanView,
        "_list_spans_non_observe_clickhouse",
        _raise(exc),
    )
    request = SimpleNamespace(query_params={})
    view = span_view.ObservationSpanView()
    view.request = request
    return unwrap(span_view.ObservationSpanView.list_spans)(view, request)


def _session_list_call(monkeypatch, exc):
    from tracer.views import trace_session as session_view

    project_scope = MagicMock()
    project_scope.get.return_value = SimpleNamespace(source="api")
    monkeypatch.setattr(
        session_view,
        "_project_queryset_for_request",
        lambda _request: project_scope,
    )
    monkeypatch.setattr(
        session_view.TraceSessionView,
        "_build_bookmark_filter",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        session_view.TraceSessionView,
        "_list_sessions_clickhouse",
        _raise(exc),
    )
    organization = object()
    request = SimpleNamespace(
        validated_query_data={
            "project_id": PROJECT_ID,
            "filters": [],
            "page_number": 0,
            "page_size": 25,
        },
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    view = session_view.TraceSessionView()
    view.request = request
    return unwrap(session_view.TraceSessionView.list_sessions)(view, request)


def _voice_list_call(monkeypatch, exc):
    from tracer.views import trace as trace_view

    serializer = MagicMock()
    serializer.is_valid.return_value = True
    serializer.validated_data = {
        "project_id": PROJECT_ID,
        "filters": [],
        "page": 1,
        "page_size": 25,
    }
    monkeypatch.setattr(
        trace_view, "TraceVoiceCallListQuerySerializer", lambda **_kwargs: serializer
    )
    monkeypatch.setattr(
        trace_view.Project.objects, "get", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", MagicMock)
    monkeypatch.setattr(
        trace_view.TraceView,
        "_list_voice_calls_clickhouse",
        _raise(exc),
    )
    organization = object()
    request = SimpleNamespace(
        query_params={},
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    view = trace_view.TraceView()
    view.request = request
    return unwrap(trace_view.TraceView.list_voice_calls)(view, request)


@pytest.mark.unit
@pytest.mark.parametrize(
    "call_boundary",
    [
        _trace_version_list_call,
        _trace_list_call,
        _span_version_list_call,
        _span_list_call,
        _voice_list_call,
    ],
    ids=["version-traces", "traces", "version-spans", "spans", "voice-calls"],
)
def test_list_boundaries_preserve_typed_timeout_response(monkeypatch, call_boundary):
    private_error = "private ClickHouse timeout and SQL"

    response = call_boundary(monkeypatch, ServerException(private_error, code=159))

    assert response.status_code == 503
    assert _result(response) in {
        "Trace data is temporarily unavailable. Please retry.",
        "Span data is temporarily unavailable. Please retry.",
        "Voice call data is temporarily unavailable. Please retry.",
    }
    assert private_error not in str(response.data)


@pytest.mark.unit
@pytest.mark.parametrize(
    "call_boundary",
    [
        _trace_list_call,
        _span_list_call,
        _session_list_call,
    ],
    ids=["traces", "spans", "sessions"],
)
@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("private query compiler invariant"),
        ValueError("private query compiler contract"),
        ServerException("private unknown-column SQL", code=47),
    ],
    ids=["runtime", "value-error", "code-47"],
)
def test_observe_list_boundaries_return_sanitized_500_for_query_defects(
    monkeypatch, call_boundary, exc
):
    response = call_boundary(monkeypatch, exc)

    _assert_sanitized_500(response)


@pytest.mark.unit
@pytest.mark.parametrize(
    "call_boundary",
    [_trace_list_call, _span_list_call, _session_list_call],
    ids=["traces", "spans", "sessions"],
)
@pytest.mark.parametrize(
    "exc",
    [
        ServerException("private timeout SQL", code=159),
        ServerException("private memory SQL", code=241),
        ServerException("private byte-limit SQL", code=307),
        ServerException("private heterogeneous-type SQL", code=386),
        NetworkError("private ClickHouse network address"),
    ],
    ids=["code-159", "code-241", "code-307", "code-386", "network"],
)
def test_observe_list_boundaries_return_sanitized_503_for_unavailable_reads(
    monkeypatch, call_boundary, exc
):
    response = call_boundary(monkeypatch, exc)

    _assert_sanitized_503(response)


@pytest.mark.unit
@pytest.mark.parametrize(
    "call_boundary",
    [
        _trace_version_list_call,
        _trace_list_call,
        _span_version_list_call,
        _span_list_call,
        _session_list_call,
    ],
    ids=["version-traces", "traces", "version-spans", "spans", "sessions"],
)
def test_observe_list_boundaries_keep_known_filter_validation_at_400(
    monkeypatch,
    call_boundary,
):
    response = call_boundary(
        monkeypatch,
        UnsupportedFilterShapeError("private invalid filter detail"),
    )

    assert response.status_code == 400
    assert "filter configuration is invalid" in str(response.data)
    assert "private" not in str(response.data)


@pytest.mark.unit
@pytest.mark.parametrize(
    "call_boundary", [_trace_list_call, _span_list_call], ids=["traces", "spans"]
)
@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("invalid_cursor", "The continuation cursor is invalid."),
        ("cursor_mismatch", "The continuation cursor does not match this request."),
        ("cursor_expired", "The continuation cursor has expired."),
    ],
)
def test_observe_list_boundaries_return_typed_sanitized_cursor_400(
    monkeypatch, call_boundary, code, message
):
    response = call_boundary(monkeypatch, ListCursorError(code, message))

    assert response.status_code == 400
    assert code in str(response.data)
    assert message in str(response.data)
    assert "BadSignature" not in str(response.data)


def _graph_call(monkeypatch, view_kind, outcome, *, allow_sampled=False):
    if view_kind == "trace":
        from tracer.views import trace as graph_view

        project_scope = MagicMock()
        project_scope.filter.return_value.first.return_value = SimpleNamespace(
            trace_type="observe"
        )
        monkeypatch.setattr(
            graph_view,
            "_project_queryset_for_request",
            lambda _request: project_scope,
        )
        view_cls = graph_view.TraceView
    else:
        from tracer.views import observation_span as graph_view

        monkeypatch.setattr(
            graph_view.Project.objects,
            "get",
            lambda *_args, **_kwargs: SimpleNamespace(trace_type="observe"),
        )
        monkeypatch.setattr(
            graph_view, "_project_workspace_scope_q", lambda *_args, **_kwargs: object()
        )
        monkeypatch.setattr(
            graph_view, "_get_request_organization", lambda _request: object()
        )
        view_cls = graph_view.ObservationSpanView

    monkeypatch.setattr(graph_view, "V2AnalyticsQueryService", MagicMock)
    # This helper replaces every ORM manager with a pure mock and deliberately
    # runs without pytest-django database access. Deadline/statement-timeout
    # behavior is covered by test_graph_action_request_deadline.py.
    monkeypatch.setattr(
        graph_view,
        "graph_action_postgres_budget",
        lambda _deadline: nullcontext(),
    )
    graph_fetch = (
        _raise(outcome)
        if isinstance(outcome, BaseException)
        else lambda **_kwargs: outcome
    )
    monkeypatch.setattr(graph_view, "fetch_system_metric_graph_ch", graph_fetch)
    request = SimpleNamespace(
        validated_query_data={"allow_sampled": allow_sampled},
        validated_data={
            "project_id": PROJECT_ID,
            "filters": [],
            "property": "average",
            "interval": "day",
            "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
        },
    )
    view = view_cls()
    view.request = request
    return unwrap(view_cls.get_graph_methods)(view, request)


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
@pytest.mark.parametrize(
    "exc",
    [
        ServerException("private timeout and stack", code=159),
        ServerException("private memory and stack", code=241),
        ServerException("private bytes and stack", code=307),
        ServerException("private type and stack", code=386),
        NetworkError("private graph network host"),
    ],
    ids=["code-159", "code-241", "code-307", "code-386", "network"],
)
def test_graph_boundaries_return_sanitized_503_for_unavailable_reads(
    monkeypatch,
    view_kind,
    exc,
):
    response = _graph_call(monkeypatch, view_kind, exc)

    _assert_sanitized_503(response)


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
def test_graph_boundaries_return_503_for_partial_degraded_coverage(
    monkeypatch,
    view_kind,
):
    response = _graph_call(
        monkeypatch,
        view_kind,
        {
            "metric_name": "latency",
            "data": [
                {
                    "timestamp": "2026-08-03T00:00:00Z",
                    "value": 999,
                    "primary_traffic": 999,
                }
            ],
            "query_complete": False,
            "query_status": "degraded",
            "query_error_code": "sample_limit",
        },
    )

    _assert_sanitized_503(response)


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
def test_graph_boundaries_return_200_while_exact_snapshot_refreshes(
    monkeypatch,
    view_kind,
):
    response = _graph_call(
        monkeypatch,
        view_kind,
        {
            "metric_name": "latency",
            "data": [],
            "query_complete": False,
            "query_status": "pending",
            "query_sampled": False,
            "query_refreshing": True,
        },
    )

    assert response.status_code == 200
    assert response.data["result"]["data"] == []
    assert response.data["result"]["query_status"] == "pending"
    assert response.data["result"]["query_refreshing"] is True


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
def test_graph_boundaries_reject_labelled_sample_even_with_legacy_opt_in(
    monkeypatch,
    view_kind,
):
    point = {
        "timestamp": "2026-08-03T00:00:00Z",
        "value": 7,
        "primary_traffic": 1,
    }
    response = _graph_call(
        monkeypatch,
        view_kind,
        {
            "metric_name": "latency",
            "data": [point],
            "query_complete": False,
            "query_status": "sampled",
            "query_error_code": "sample_limit",
            "query_sampling_strategy": "time_stratified_latest_state",
            "query_sampling_strata": 8,
            "query_sampling_strata_completed": 8,
        },
        allow_sampled=True,
    )

    _assert_sanitized_503(response)


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
def test_primary_graph_boundaries_reject_sample_even_with_legacy_opt_in(
    monkeypatch,
    view_kind,
):
    point = {
        "timestamp": "2026-08-03T00:00:00Z",
        "value": 7,
        "primary_traffic": 1,
    }
    response = _graph_call(
        monkeypatch,
        view_kind,
        {
            "metric_name": "latency",
            "data": [point],
            "query_complete": False,
            "query_status": "sampled",
            "query_sampled": True,
            "query_exact": False,
            "query_provenance": "bounded_candidates",
            "query_error_code": "sample_limit",
            "query_sampling_strategy": "time_stratified_latest_state",
            "query_sampling_strata": 8,
            "query_sampling_strata_completed": 8,
        },
        allow_sampled=True,
    )

    _assert_sanitized_503(response)


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
def test_graph_boundaries_reject_sample_without_client_opt_in(monkeypatch, view_kind):
    response = _graph_call(
        monkeypatch,
        view_kind,
        {
            "metric_name": "latency",
            "data": [
                {
                    "timestamp": "2026-08-03T00:00:00Z",
                    "value": 7,
                    "primary_traffic": 1,
                }
            ],
            "query_complete": False,
            "query_status": "sampled",
            "query_error_code": "sample_limit",
            "query_sampling_strategy": "time_stratified_latest_state",
            "query_sampling_strata": 8,
            "query_sampling_strata_completed": 8,
        },
    )

    _assert_sanitized_503(response)


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("private graph compiler invariant"),
        ServerException("private graph unknown-column SQL", code=47),
    ],
    ids=["runtime", "code-47"],
)
def test_graph_boundaries_return_sanitized_500_for_query_defects(
    monkeypatch, view_kind, exc
):
    response = _graph_call(monkeypatch, view_kind, exc)

    _assert_sanitized_500(response)


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
def test_graph_boundaries_keep_known_filter_validation_at_400(
    monkeypatch,
    view_kind,
):
    response = _graph_call(
        monkeypatch,
        view_kind,
        UnsupportedFilterShapeError("private graph filter detail"),
    )

    assert response.status_code == 400
    assert "filter configuration is invalid" in str(response.data)
    assert "private" not in str(response.data)


def _trace_detail_call(monkeypatch, exc):
    from tracer.services.clickhouse.v2.query_builders.trace_detail import (
        TraceDetailHandlerV2,
    )
    from tracer.views.trace import TraceView

    monkeypatch.setattr(TraceDetailHandlerV2, "fetch", _raise(exc))
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", object)
    request = SimpleNamespace()
    view = TraceView()
    view.request = request
    return view.retrieve(request, pk="trace-1")


def _span_detail_call(monkeypatch, exc):
    from tracer.views import observation_span as span_view

    manager = MagicMock()
    manager.filter.return_value.values_list.return_value.__getitem__.return_value = []
    project_model = SimpleNamespace(
        no_workspace_objects=manager,
        objects=manager,
        DoesNotExist=Project.DoesNotExist,
    )
    monkeypatch.setattr(span_view, "Project", project_model)
    monkeypatch.setattr(
        span_view, "_project_workspace_scope_q", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        span_view, "_get_request_organization", lambda _request: object()
    )
    monkeypatch.setattr(span_view, "V2AnalyticsQueryService", MagicMock)
    monkeypatch.setattr(
        span_view.ObservationSpanView,
        "_retrieve_clickhouse",
        _raise(exc),
    )
    request = SimpleNamespace()
    view = span_view.ObservationSpanView()
    view.request = request
    return unwrap(span_view.ObservationSpanView.retrieve)(view, request, pk="span-1")


@pytest.mark.unit
@pytest.mark.parametrize(
    "call_boundary", [_trace_detail_call, _span_detail_call], ids=["trace", "span"]
)
def test_detail_boundaries_preserve_typed_unavailable_response(
    monkeypatch, call_boundary
):
    response = call_boundary(
        monkeypatch, TraceDetailReadUnavailable("read_budget_exceeded")
    )

    assert response.status_code == 503
    assert "temporarily unavailable" in str(response.data)
    assert "read_budget_exceeded" not in str(response.data)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("call_boundary", "exc"),
    [
        (_trace_detail_call, Trace.DoesNotExist()),
        (_span_detail_call, TraceDetailNotFound()),
    ],
    ids=["trace", "span"],
)
def test_detail_boundaries_preserve_not_found_bad_request(
    monkeypatch, call_boundary, exc
):
    response = call_boundary(monkeypatch, exc)

    assert response.status_code == 400


@pytest.mark.unit
@pytest.mark.parametrize(
    "call_boundary", [_trace_detail_call, _span_detail_call], ids=["trace", "span"]
)
@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("private detail compiler invariant"),
        ServerException("private detail unknown-column SQL", code=47),
    ],
    ids=["runtime", "code-47"],
)
def test_detail_boundaries_preserve_sanitized_400_for_query_defects(
    monkeypatch, call_boundary, exc
):
    response = call_boundary(monkeypatch, exc)

    _assert_sanitized_400(response)


def _eval_name_call(monkeypatch, exc):
    from tracer.views import trace as trace_view

    project_scope = MagicMock()
    project_scope.filter.return_value.first.return_value = SimpleNamespace(
        trace_type="observe"
    )
    config_manager = MagicMock()
    config_manager.filter.return_value.values_list.return_value = ["config-1"]
    analytics = MagicMock()
    analytics.get_eval_config_ids_with_data_ch.side_effect = exc
    monkeypatch.setattr(
        trace_view, "_project_queryset_for_request", lambda _request: project_scope
    )
    monkeypatch.setattr(trace_view.CustomEvalConfig, "objects", config_manager)
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", lambda: analytics)
    request = SimpleNamespace(query_params={"project_id": PROJECT_ID})
    view = trace_view.TraceView()
    view.request = request
    return unwrap(trace_view.TraceView.get_eval_names)(view, request)


def _eval_detail_call(monkeypatch, exc):
    from tracer.views import observation_span as span_view

    config_manager = MagicMock()
    config_manager.filter.return_value.values.return_value.first.return_value = {
        "project_id": PROJECT_ID
    }
    analytics = MagicMock()
    analytics.get_eval_detail_ch.side_effect = exc
    monkeypatch.setattr(
        span_view.CustomEvalConfig, "no_workspace_objects", config_manager
    )
    monkeypatch.setattr(
        span_view, "_project_workspace_scope_q", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        span_view, "_get_request_organization", lambda _request: object()
    )
    monkeypatch.setattr(span_view, "V2AnalyticsQueryService", lambda: analytics)
    request = SimpleNamespace(
        query_params={
            "observation_span_id": "span-1",
            "custom_eval_config_id": "config-1",
        }
    )
    view = span_view.ObservationSpanView()
    view.request = request
    return unwrap(span_view.ObservationSpanView.get_evaluation_details)(view, request)


def _session_eval_logs_call(monkeypatch, exc=None):
    from tracer.views import trace_session as session_view

    analytics = MagicMock()
    if exc is None:
        analytics.execute_ch_query.return_value = MagicMock(data=[])
    else:
        analytics.execute_ch_query.side_effect = exc
    v2_factory = MagicMock(return_value=analytics)
    monkeypatch.setattr(session_view, "V2AnalyticsQueryService", v2_factory)

    session_id = "22222222-2222-4222-8222-222222222222"
    request = SimpleNamespace(query_params={})
    view = session_view.TraceSessionView()
    view.request = request
    view.kwargs = {"pk": session_id}
    view.get_object = lambda: SimpleNamespace(id=session_id, name="Session")
    response = unwrap(session_view.TraceSessionView.eval_logs)(view, request)
    return response, analytics, v2_factory


@pytest.mark.unit
@pytest.mark.parametrize(
    "call_boundary", [_eval_name_call, _eval_detail_call], ids=["names", "detail"]
)
def test_eval_read_boundaries_preserve_typed_timeout_response(
    monkeypatch, call_boundary
):
    private_error = "private eval timeout and SQL"

    response = call_boundary(monkeypatch, ServerException(private_error, code=159))

    assert response.status_code == 503
    assert "temporarily unavailable" in str(response.data)
    assert private_error not in str(response.data)


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        ServerException("private eval timeout SQL", code=159),
        ServerException("private eval memory SQL", code=241),
        ServerException("private eval byte-limit SQL", code=307),
        ServerException("private eval heterogeneous SQL", code=386),
        NetworkError("private eval ClickHouse host"),
    ],
    ids=["code-159", "code-241", "code-307", "code-386", "network"],
)
def test_eval_name_picker_returns_sanitized_503_for_unavailable_reads(monkeypatch, exc):
    response = _eval_name_call(monkeypatch, exc)

    _assert_sanitized_503(response)


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        ServerException("private eval timeout SQL", code=159),
        ServerException("private eval memory SQL", code=241),
        ServerException("private eval byte-limit SQL", code=307),
        ServerException("private eval heterogeneous SQL", code=386),
        NetworkError("private eval ClickHouse host"),
    ],
    ids=["code-159", "code-241", "code-307", "code-386", "network"],
)
def test_eval_detail_returns_sanitized_503_for_unavailable_reads(monkeypatch, exc):
    response = _eval_detail_call(monkeypatch, exc)

    _assert_sanitized_503(response)


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("private eval compiler invariant"),
        ServerException("private eval unknown-column SQL", code=47),
    ],
    ids=["runtime", "code-47"],
)
def test_eval_name_picker_returns_sanitized_500_for_query_defects(monkeypatch, exc):
    response = _eval_name_call(monkeypatch, exc)

    _assert_sanitized_500(response)


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("private eval compiler invariant"),
        ServerException("private eval unknown-column SQL", code=47),
    ],
    ids=["runtime", "code-47"],
)
def test_eval_detail_returns_sanitized_500_for_query_defects(monkeypatch, exc):
    response = _eval_detail_call(monkeypatch, exc)

    _assert_sanitized_500(response)


@pytest.mark.unit
def test_eval_name_picker_uses_direct_write_v2_service(monkeypatch):
    from tracer.views import trace as trace_view

    project_scope = MagicMock()
    project_scope.filter.return_value.first.return_value = SimpleNamespace(
        trace_type="observe"
    )
    config_manager = MagicMock()
    config_manager.filter.return_value.values_list.return_value = ["config-1"]
    analytics = MagicMock()
    analytics.get_eval_config_ids_with_data_ch.return_value = []
    v2_factory = MagicMock(return_value=analytics)
    legacy_factory = MagicMock(
        side_effect=AssertionError("legacy ClickHouse service must not be used")
    )
    monkeypatch.setattr(
        trace_view, "_project_queryset_for_request", lambda _request: project_scope
    )
    monkeypatch.setattr(trace_view.CustomEvalConfig, "objects", config_manager)
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", v2_factory)
    monkeypatch.setattr(trace_view, "AnalyticsQueryService", legacy_factory)
    request = SimpleNamespace(query_params={"project_id": PROJECT_ID})
    view = trace_view.TraceView()
    view.request = request

    response = unwrap(trace_view.TraceView.get_eval_names)(view, request)

    assert response.status_code == 200
    v2_factory.assert_called_once_with()
    legacy_factory.assert_not_called()


@pytest.mark.unit
def test_eval_detail_uses_direct_write_v2_service(monkeypatch):
    from tracer.views import observation_span as span_view

    config_manager = MagicMock()
    config_manager.filter.return_value.values.return_value.first.return_value = {
        "project_id": PROJECT_ID
    }
    analytics = MagicMock()
    analytics.get_eval_detail_ch.return_value = None
    v2_factory = MagicMock(return_value=analytics)
    legacy_factory = MagicMock(
        side_effect=AssertionError("legacy ClickHouse service must not be used")
    )
    monkeypatch.setattr(
        span_view.CustomEvalConfig, "no_workspace_objects", config_manager
    )
    monkeypatch.setattr(
        span_view, "_project_workspace_scope_q", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        span_view, "_get_request_organization", lambda _request: object()
    )
    monkeypatch.setattr(span_view, "V2AnalyticsQueryService", v2_factory)
    monkeypatch.setattr(span_view, "AnalyticsQueryService", legacy_factory)
    request = SimpleNamespace(
        query_params={
            "observation_span_id": "span-1",
            "custom_eval_config_id": "config-1",
        }
    )
    view = span_view.ObservationSpanView()
    view.request = request

    response = unwrap(span_view.ObservationSpanView.get_evaluation_details)(
        view, request
    )

    assert response.status_code == 400
    v2_factory.assert_called_once_with()
    legacy_factory.assert_not_called()
    analytics.get_eval_detail_ch.assert_called_once_with(
        "span-1", "config-1", project_id=PROJECT_ID
    )


@pytest.mark.unit
def test_session_eval_logs_uses_authoritative_table_on_direct_service(
    monkeypatch, settings
):
    # Eval table selection is independent from the spans connection: the
    # authoritative legacy-named table still runs on the direct CH25 service.
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"

    response, analytics, v2_factory = _session_eval_logs_call(monkeypatch)

    assert response.status_code == 200
    v2_factory.assert_called_once_with()
    query = analytics.execute_ch_query.call_args.args[0]
    assert "FROM tracer_eval_logger AS eval_scan" in query
    assert "FROM tracer_eval_logger_v2 AS eval_scan" not in query
    assert "latest_eval._peerdb_is_deleted = 0" in query
    assert "latest_eval.deleted = 0 OR latest_eval.deleted IS NULL" in query
    call = analytics.execute_ch_query.call_args
    assert 0 < call.kwargs["timeout_ms"] <= 30_000
    read_settings = call.kwargs["settings"]
    assert "max_rows_to_read" not in read_settings
    assert read_settings["max_result_rows"] == 1
    assert read_settings["max_result_bytes"] == settings.SESSION_LIST_MAX_RESULT_BYTES
    assert read_settings["max_bytes_to_read"] == settings.OBSERVABILITY_LIST_MAX_BYTES
    assert (
        read_settings["max_memory_usage"]
        == settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES
    )
    assert read_settings["max_threads"] == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        ServerException("private session eval timeout SQL", code=159),
        ServerException("private session eval memory SQL", code=241),
        ServerException("private session eval byte-limit SQL", code=307),
        ServerException("private session eval heterogeneous SQL", code=386),
        NetworkError("private session eval ClickHouse host"),
    ],
    ids=["code-159", "code-241", "code-307", "code-386", "network"],
)
def test_session_eval_logs_returns_sanitized_503_for_unavailable_reads(
    monkeypatch, exc
):
    response, _analytics, _v2_factory = _session_eval_logs_call(monkeypatch, exc)

    _assert_sanitized_503(response)


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("private session eval compiler invariant"),
        ServerException("private session eval unknown-column SQL", code=47),
    ],
    ids=["runtime", "code-47"],
)
def test_session_eval_logs_returns_sanitized_500_for_query_defects(monkeypatch, exc):
    response, _analytics, _v2_factory = _session_eval_logs_call(monkeypatch, exc)

    _assert_sanitized_500(response)
