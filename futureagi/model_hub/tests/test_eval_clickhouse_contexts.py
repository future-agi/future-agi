import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Organization
from ee.usage.models.usage import APICallLog
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.views.separate_evals import (
    EvalPlayGroundAPIView,
    EvalTemplateListChartsView,
    TraceEvalView,
)
from tracer.models.project import Project

PRIVATE_ERROR = "Code: 159. DB::Exception: private ClickHouse stack trace"
CONTEXT_ERROR_MESSAGE = "Evaluation context could not be loaded. Please try again."
EXECUTION_ERROR_MESSAGE = "Evaluation could not be completed. Please try again."


def _eval_playground_payload(eval_template, context_kind=None, context_id=None):
    payload = {
        "template_id": str(eval_template.id),
        "model": "",
        "mapping": {"input": "hello", "output": "world"},
        "config": {"params": {}},
    }
    if context_kind is not None:
        payload[f"{context_kind}_id"] = context_id
    return payload


def _patch_playground_context_resolver(
    monkeypatch,
    context_kind,
    *,
    result=None,
    exc=None,
):
    if context_kind == "span":
        if exc is not None:

            def _get_reader():
                raise exc

            monkeypatch.setattr(
                "tracer.services.clickhouse.v2.get_reader",
                _get_reader,
            )
            return

        reader = MagicMock()
        reader.__enter__.return_value = reader
        reader.__exit__.return_value = False
        reader.scope_by_ids.return_value = {} if result is None else result
        monkeypatch.setattr(
            "tracer.services.clickhouse.v2.get_reader",
            lambda: reader,
        )
        return

    method_name = f"_{context_kind}_context_from_clickhouse"

    def _resolver(_cls, **_kwargs):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(
        EvalPlayGroundAPIView,
        method_name,
        classmethod(_resolver),
    )


def _assert_safe_error(response, *, status_code, code, message):
    assert response.status_code == status_code
    assert response.data["code"] == code
    assert all(
        response.data[field] == message
        for field in ("message", "detail", "error", "result")
    )
    assert PRIVATE_ERROR not in json.dumps(response.data)


@pytest.fixture
def eval_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="ch-only-trace-eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "required_keys": ["input", "output"]},
        criteria="Check {{input}} against {{output}}",
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


class _FakeClickHouseClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute_read(self, query, params, *, timeout_ms, settings):
        self.calls.append(
            {
                "query": query,
                "params": params,
                "timeout_ms": timeout_ms,
                "settings": settings,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response, [], 1.0


@pytest.mark.django_db
def test_eval_list_charts_uses_one_bounded_materialized_clickhouse_aggregate(
    monkeypatch,
):
    template_id = uuid4()
    # The final chart bucket is always "today" in UTC. Keep the fixture aligned
    # with that rolling window so the assertion is stable across midnight.
    bucket = datetime.now(UTC)
    client = _FakeClickHouseClient(
        [[(str(template_id), bucket, 4, 1)]],
    )
    cache_writes = []
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "model_hub.views.separate_evals._eval_metric_cache_get",
        lambda _cache, _key: None,
    )
    monkeypatch.setattr(
        "model_hub.views.separate_evals._eval_metric_cache_set",
        lambda _cache, key, value, *, timeout: cache_writes.append(
            (key, value, timeout)
        ),
    )
    organization = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4(), is_default=False)

    result = EvalTemplateListChartsView()._fetch_charts_from_clickhouse(
        organization,
        workspace,
        [template_id],
    )

    assert result[str(template_id)]["run_count"] == 4
    assert result[str(template_id)]["error_rate"][-1]["value"] == 25.0
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["timeout_ms"] == 750
    assert call["settings"]["max_threads"] == 2
    assert call["settings"]["max_rows_to_read"] == 4_000_000
    assert call["settings"]["max_bytes_to_read"] == 512 * 1024 * 1024
    assert "eval_score" in call["query"]
    assert "eval_output_str" in call["query"]
    assert "JSONExtract" not in call["query"]
    assert "workspace_id = toUUID" in call["query"]
    assert {entry[2] for entry in cache_writes} == {30, 6 * 60 * 60}


@pytest.mark.django_db
def test_eval_list_charts_returns_stale_result_when_clickhouse_exceeds_budget(
    monkeypatch,
):
    template_id = uuid4()
    stale = {str(template_id): {"chart": [], "error_rate": [], "run_count": 9}}
    client = _FakeClickHouseClient([TimeoutError("budget")])
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "model_hub.views.separate_evals._eval_metric_cache_get",
        lambda _cache, key: stale if ":stale:" in key else None,
    )
    monkeypatch.setattr(
        "model_hub.views.separate_evals._eval_metric_cache_set",
        lambda *_args, **_kwargs: None,
    )

    result, metadata = EvalTemplateListChartsView()._fetch_charts_from_clickhouse(
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4(), is_default=True),
        [template_id],
        with_metadata=True,
    )

    assert result == stale
    assert metadata == {
        "query_complete": False,
        "query_status": "stale",
        "data_stale": True,
        "query_error_code": "read_budget_exceeded",
    }
    assert client.calls[0]["timeout_ms"] == 750


@pytest.mark.django_db
def test_eval_list_charts_marks_cold_budget_failure_degraded(monkeypatch):
    template_id = uuid4()
    client = _FakeClickHouseClient([TimeoutError("budget")])
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "model_hub.views.separate_evals._eval_metric_cache_get",
        lambda _cache, _key: None,
    )

    charts, metadata = EvalTemplateListChartsView()._fetch_charts_from_clickhouse(
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4(), is_default=True),
        [template_id],
        with_metadata=True,
    )

    assert charts[str(template_id)]["run_count"] == 0
    assert metadata == {
        "query_complete": False,
        "query_status": "degraded",
        "data_stale": False,
        "query_error_code": "read_budget_exceeded",
    }


@pytest.mark.django_db
def test_eval_list_charts_does_not_mask_programming_error_with_stale_data(
    monkeypatch,
):
    template_id = uuid4()
    stale = {str(template_id): {"chart": [], "error_rate": [], "run_count": 9}}
    client = _FakeClickHouseClient([RuntimeError("secret malformed chart SQL")])
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "model_hub.views.separate_evals._eval_metric_cache_get",
        lambda _cache, key: stale if ":stale:" in key else None,
    )

    with pytest.raises(RuntimeError, match="secret malformed chart SQL"):
        EvalTemplateListChartsView()._fetch_charts_from_clickhouse(
            SimpleNamespace(id=uuid4()),
            SimpleNamespace(id=uuid4(), is_default=True),
            [template_id],
        )


@pytest.mark.django_db
def test_legacy_eval_list_uses_metadata_and_clickhouse_without_pg_usage_scan(
    auth_client,
    eval_template,
    monkeypatch,
):
    template_id = str(eval_template.id)

    def _fake_charts(
        _self,
        _organization,
        _workspace,
        _template_ids,
        *,
        with_metadata=False,
    ):
        charts = {
            template_id: {
                "chart": [],
                "error_rate": [{"timestamp": "2026-02-01T00:00:00", "value": 25.0}],
                "run_count": 4,
            }
        }
        metadata = {
            "query_complete": False,
            "query_status": "stale",
            "data_stale": True,
            "query_error_code": "read_budget_exceeded",
        }
        return (charts, metadata) if with_metadata else charts

    monkeypatch.setattr(
        EvalTemplateListChartsView,
        "_fetch_charts_from_clickhouse",
        _fake_charts,
    )

    with CaptureQueriesContext(connection) as queries:
        response = auth_client.post(
            "/model-hub/get-eval-templates",
            {
                "search_text": eval_template.name,
                "current_page_index": 0,
                "page_size": 10,
            },
            format="json",
        )

    assert response.status_code == 200
    rows = response.data["result"]["row_data"]
    assert rows[0]["id"] == template_id
    assert rows[0]["last30_run"] == 4
    assert rows[0]["error_rate"][0]["value"] == 25.0
    assert response.data["result"]["chart_query_complete"] is False
    assert response.data["result"]["chart_query_status"] == "stale"
    assert response.data["result"]["chart_data_stale"] is True
    assert response.data["result"]["chart_query_error_code"] == "read_budget_exceeded"
    usage_table = APICallLog._meta.db_table.lower()
    assert all(usage_table not in query["sql"].lower() for query in queries)


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
def test_eval_metric_rejects_template_from_another_organization(
    auth_client,
    monkeypatch,
    method,
):
    other_organization = Organization.objects.create(name="other metric organization")
    hidden = EvalTemplate.no_workspace_objects.create(
        name="other-org-metric",
        organization=other_organization,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
        visible_ui=True,
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: pytest.fail("cross-tenant metric must not query ClickHouse"),
    )

    request_data = {"eval_template_id": str(hidden.id)}
    if method == "get":
        response = auth_client.get("/model-hub/get-eval-metrics", request_data)
    else:
        response = auth_client.post(
            "/model-hub/get-eval-metrics",
            request_data,
            format="json",
        )

    assert response.status_code == 404


@pytest.mark.django_db
def test_trace_eval_runs_from_clickhouse_without_a_postgres_trace(
    auth_client,
    eval_template,
    monkeypatch,
):
    trace_id = str(uuid4())
    captured = {}

    monkeypatch.setattr(
        TraceEvalView,
        "_read_trace_from_clickhouse",
        classmethod(
            lambda cls, **_kwargs: {
                "id": trace_id,
                "project_id": str(uuid4()),
                "input": {"input": "hello"},
                "output": {"output": "world"},
            }
        ),
    )

    def fake_run_eval(runtime_config, mapping, *_args, **_kwargs):
        captured["runtime_config"] = runtime_config
        captured["mapping"] = mapping
        return {"output": {"output": "Passed", "reason": "ok"}}

    monkeypatch.setattr(
        "model_hub.views.utils.evals.run_eval_func",
        fake_run_eval,
    )

    response = auth_client.post(
        f"/model-hub/eval-templates/{eval_template.id}/run-on-trace/",
        {"trace_id": trace_id},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["result"]["status"] == "completed"
    assert captured["mapping"] == {
        "input": "hello",
        "output": "world",
    }


@pytest.mark.django_db
def test_trace_lookup_is_project_gated_and_resource_bounded(
    organization,
    workspace,
    monkeypatch,
):
    project = Project.objects.create(
        name="CH-only trace project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    trace_id = uuid4()
    created_at = datetime(2026, 7, 30, tzinfo=UTC)
    client = _FakeClickHouseClient(
        [
            [
                (
                    str(trace_id),
                    str(project.id),
                    "trace",
                    "",
                    '{"customer":"safe"}',
                    '["prod"]',
                    '{"question":"hello"}',
                    '{"answer":"world"}',
                    "",
                    created_at,
                )
            ]
        ]
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: client,
    )

    result = TraceEvalView._read_trace_from_clickhouse(
        trace_id=trace_id,
        organization=organization,
        workspace=workspace,
    )

    assert result["project_id"] == str(project.id)
    assert result["input"] == {"question": "hello"}
    assert result["output"] == {"answer": "world"}
    call = client.calls[0]
    assert call["params"]["project_ids"] == (str(project.id),)
    assert call["timeout_ms"] == 750
    assert call["settings"]["max_threads"] == 2
    assert call["settings"]["max_result_rows"] == 1


@pytest.mark.django_db
def test_session_context_uses_clickhouse_only_and_caps_trace_summaries(
    monkeypatch,
):
    session_id = uuid4()
    project_id = uuid4()
    trace_id = str(uuid4())
    first_seen = datetime(2026, 7, 30, 10, tzinfo=UTC)
    last_seen = datetime(2026, 7, 30, 10, 0, 5, tzinfo=UTC)
    client = _FakeClickHouseClient(
        [
            [(str(session_id), str(project_id), "external-session", first_seen)],
            [
                (
                    1,
                    3,
                    1,
                    42,
                    0.125,
                    first_seen,
                    last_seen,
                    [
                        (
                            first_seen,
                            trace_id,
                            "trace name",
                            3,
                            1,
                            42,
                            5000,
                        )
                    ],
                )
            ],
        ]
    )
    monkeypatch.setattr(
        TraceEvalView,
        "_scoped_project_ids",
        staticmethod(lambda **_kwargs: (str(project_id),)),
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.get_clickhouse_client",
        lambda: client,
    )

    result = EvalPlayGroundAPIView._session_context_from_clickhouse(
        session_id=session_id,
        organization=SimpleNamespace(id=uuid4()),
        workspace=SimpleNamespace(id=uuid4(), is_default=False),
    )

    assert result["project_id"] == str(project_id)
    assert result["trace_count"] == 1
    assert result["total_spans"] == 3
    assert result["duration_seconds"] == 5
    assert result["traces"][0]["id"] == trace_id
    assert all(call["timeout_ms"] == 750 for call in client.calls)
    assert all(call["settings"]["max_threads"] == 2 for call in client.calls)
    assert "Trace.objects" not in " ".join(call["query"] for call in client.calls)


@pytest.mark.django_db
def test_eval_playground_span_context_scopes_before_wide_hydration(
    auth_client,
    eval_template,
    organization,
    workspace,
    monkeypatch,
):
    from tracer.services.clickhouse.v2.span_reader import SpanScope

    span_id = "collector-span-1"
    project = Project.objects.create(
        name="eval playground span scope",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    sentinel_span = object()
    reader = MagicMock()
    reader.__enter__.return_value = reader
    reader.__exit__.return_value = False
    reader.scope_by_ids.return_value = {
        span_id: SpanScope(project_id=str(project.id), trace_id=str(uuid4()))
    }
    reader.get.return_value = sentinel_span
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader",
        lambda: reader,
    )
    monkeypatch.setattr(
        "model_hub.views.separate_evals._chspan_to_eval_playground_view",
        lambda span: span,
    )
    monkeypatch.setattr(
        "model_hub.views.separate_evals._build_span_context",
        lambda span: {"resolved": span is sentinel_span},
    )
    captured = {}

    def _run_eval_func(*_args, **kwargs):
        captured["span_context"] = kwargs.get("span_context")
        return {"output": "Passed", "reason": "ok"}

    monkeypatch.setattr(
        "model_hub.views.separate_evals.run_eval_func",
        _run_eval_func,
    )

    response = auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(eval_template.id),
            "model": "",
            "mapping": {"input": "hello", "output": "world"},
            "config": {"params": {}},
            "span_id": span_id,
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    assert captured["span_context"] == {"resolved": True}
    scope_call = reader.scope_by_ids.call_args
    assert scope_call.args == ([span_id],)
    assert scope_call.kwargs["project_ids"] == (str(project.id),)
    assert scope_call.kwargs["settings"]["max_execution_time"] == 0.75
    assert scope_call.kwargs["settings"]["max_bytes_to_read"] == 256 * 1024 * 1024
    reader.get.assert_called_once()
    get_call = reader.get.call_args
    assert get_call.args == (span_id,)
    assert get_call.kwargs["project_id"] == str(project.id)
    assert get_call.kwargs["settings"]["max_execution_time"] == 0.75


@pytest.mark.django_db
@pytest.mark.parametrize("context_kind", ["span", "trace", "session"])
@pytest.mark.parametrize(
    ("exc_type", "expected_code"),
    [
        (RuntimeError, "query_failed"),
        (TimeoutError, "read_budget_exceeded"),
    ],
)
def test_eval_playground_context_query_failure_is_safe_and_fails_closed(
    auth_client,
    eval_template,
    monkeypatch,
    context_kind,
    exc_type,
    expected_code,
):
    context_id = "collector-span-1" if context_kind == "span" else str(uuid4())
    _patch_playground_context_resolver(
        monkeypatch,
        context_kind,
        exc=exc_type(PRIVATE_ERROR),
    )
    run_eval = Mock(return_value={"output": "Passed"})
    monkeypatch.setattr(
        "model_hub.views.separate_evals.run_eval_func",
        run_eval,
    )

    response = auth_client.post(
        "/model-hub/eval-playground/",
        _eval_playground_payload(eval_template, context_kind, context_id),
        format="json",
    )

    _assert_safe_error(
        response,
        status_code=400,
        code=expected_code,
        message=CONTEXT_ERROR_MESSAGE,
    )
    run_eval.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("context_kind", "expected_message"),
    [
        ("span", "Span not found."),
        ("trace", "Trace not found."),
        ("session", "Session not found."),
    ],
)
def test_eval_playground_missing_context_returns_not_found_without_running_eval(
    auth_client,
    eval_template,
    monkeypatch,
    context_kind,
    expected_message,
):
    context_id = "missing-span" if context_kind == "span" else str(uuid4())
    _patch_playground_context_resolver(monkeypatch, context_kind, result=None)
    run_eval = Mock(return_value={"output": "Passed"})
    monkeypatch.setattr(
        "model_hub.views.separate_evals.run_eval_func",
        run_eval,
    )

    response = auth_client.post(
        "/model-hub/eval-playground/",
        _eval_playground_payload(eval_template, context_kind, context_id),
        format="json",
    )

    _assert_safe_error(
        response,
        status_code=404,
        code="not_found",
        message=expected_message,
    )
    run_eval.assert_not_called()


@pytest.mark.django_db
def test_eval_playground_execution_failure_does_not_leak_private_details(
    auth_client,
    eval_template,
    monkeypatch,
):
    run_eval = Mock(side_effect=RuntimeError(PRIVATE_ERROR))
    monkeypatch.setattr(
        "model_hub.views.separate_evals.run_eval_func",
        run_eval,
    )

    response = auth_client.post(
        "/model-hub/eval-playground/",
        _eval_playground_payload(eval_template),
        format="json",
    )

    _assert_safe_error(
        response,
        status_code=400,
        code="evaluation_failed",
        message=EXECUTION_ERROR_MESSAGE,
    )
    run_eval.assert_called_once()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("exc_type", "expected_code"),
    [
        (RuntimeError, "query_failed"),
        (TimeoutError, "read_budget_exceeded"),
    ],
)
def test_trace_eval_query_failure_is_safe_and_not_misreported_as_not_found(
    auth_client,
    eval_template,
    monkeypatch,
    exc_type,
    expected_code,
):
    trace_id = str(uuid4())

    def _raise_private_error(_cls, **_kwargs):
        raise exc_type(PRIVATE_ERROR)

    monkeypatch.setattr(
        TraceEvalView,
        "_read_trace_from_clickhouse",
        classmethod(_raise_private_error),
    )
    run_eval = Mock(return_value={"output": {"output": "Passed"}})
    monkeypatch.setattr(
        "model_hub.views.utils.evals.run_eval_func",
        run_eval,
    )

    response = auth_client.post(
        f"/model-hub/eval-templates/{eval_template.id}/run-on-trace/",
        {"trace_id": trace_id},
        format="json",
    )

    _assert_safe_error(
        response,
        status_code=400,
        code=expected_code,
        message=CONTEXT_ERROR_MESSAGE,
    )
    run_eval.assert_not_called()


@pytest.mark.django_db
def test_trace_eval_missing_trace_preserves_not_found_contract(
    auth_client,
    eval_template,
    monkeypatch,
):
    trace_id = str(uuid4())
    monkeypatch.setattr(
        TraceEvalView,
        "_read_trace_from_clickhouse",
        classmethod(lambda _cls, **_kwargs: None),
    )
    run_eval = Mock(return_value={"output": {"output": "Passed"}})
    monkeypatch.setattr(
        "model_hub.views.utils.evals.run_eval_func",
        run_eval,
    )

    response = auth_client.post(
        f"/model-hub/eval-templates/{eval_template.id}/run-on-trace/",
        {"trace_id": trace_id},
        format="json",
    )

    _assert_safe_error(
        response,
        status_code=404,
        code="not_found",
        message="Trace not found.",
    )
    run_eval.assert_not_called()


@pytest.mark.django_db
def test_trace_eval_execution_failure_does_not_leak_private_details(
    auth_client,
    eval_template,
    monkeypatch,
):
    trace_id = str(uuid4())
    monkeypatch.setattr(
        TraceEvalView,
        "_read_trace_from_clickhouse",
        classmethod(
            lambda _cls, **_kwargs: {
                "id": trace_id,
                "project_id": str(uuid4()),
                "input": {"input": "hello"},
                "output": {"output": "world"},
            }
        ),
    )
    run_eval = Mock(side_effect=RuntimeError(PRIVATE_ERROR))
    monkeypatch.setattr(
        "model_hub.views.utils.evals.run_eval_func",
        run_eval,
    )

    response = auth_client.post(
        f"/model-hub/eval-templates/{eval_template.id}/run-on-trace/",
        {"trace_id": trace_id},
        format="json",
    )

    _assert_safe_error(
        response,
        status_code=400,
        code="evaluation_failed",
        message=EXECUTION_ERROR_MESSAGE,
    )
    run_eval.assert_called_once()
