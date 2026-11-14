"""Capture the real playground dispatch without running a model or database."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec
from uuid import UUID, uuid4

import pytest
from django.contrib.auth.models import AnonymousUser
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.views import separate_evals as playground
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.span_reader import (
    _DATA_KEYS,
    CHSpanReader,
    _row_to_chspan,
)

pytestmark = pytest.mark.unit
START = datetime(2026, 11, 3, 12, tzinfo=UTC)
PROJECT_ID, SESSION_ID, CANONICAL_ID, TRACE_ID = (str(uuid4()) for _ in range(4))
KNOWN = {"fi.cost.kind": "provider_reported"}
UNKNOWN = {"fi.cost.kind": "unknown", "fi.usage.scope": "turn"}
SOURCE_SUMMARY = {
    "name": "invoke_agent",
    "observation_type": "agent",
    "cost": 17.0,
    "total_tokens": 43071,
    "attrs_string": {
        "fi.usage.contribution": "none",
        "fi.usage.scope": "turn",
        "fi.source.summary.gen_ai.cost.total": "17",
    },
}


def _span(**overrides):
    # Exercise the production decoder: stored unknown zero must become None.
    row = dict.fromkeys(_DATA_KEYS, "")
    row.update(
        id=str(uuid4()),
        project_id=PROJECT_ID,
        trace_id=TRACE_ID,
        trace_session_id=CANONICAL_ID,
        name="chat",
        observation_type="llm",
        start_time=START,
        end_time=START + timedelta(seconds=1),
        latency_ms=1000,
        status="OK",
        total_tokens=120,
        prompt_tokens=100,
        completion_tokens=20,
        cost=0.0,
        attrs_string=KNOWN,
        attrs_number={},
        attrs_bool={},
        is_deleted=0,
        streaming=None,
        temperature=None,
        top_p=None,
        max_tokens=None,
        metadata="{}",
        resource_attrs="{}",
        attributes_extra="{}",
        tags="[]",
        span_events="[]",
    )
    row.update(overrides)
    return _row_to_chspan(tuple(row[key] for key in _DATA_KEYS))


class _TraceQuery:
    def order_by(self, *fields):
        assert fields == ("created_at",)
        return [
            SimpleNamespace(
                id=UUID(TRACE_ID),
                name="trace",
                created_at=START,
                error=False,
            )
        ]

    def count(self):
        return 1


@pytest.fixture
def preview(monkeypatch):
    org = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4())
    fields = {
        "project_id": PROJECT_ID,
        "display_name": "Renamed session",
        "external_session_id": "external-session",
        "bookmarked": True,
        "first_seen": START - timedelta(days=1),
    }
    resolve = Mock(return_value={SESSION_ID: fields})
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields",
        resolve,
    )
    project_filter = Mock(return_value=SimpleNamespace(exists=lambda: True))
    monkeypatch.setattr(Project.objects, "filter", project_filter)
    trace_filter = Mock(return_value=_TraceQuery())
    monkeypatch.setattr(Trace.objects, "filter", trace_filter)
    reader = create_autospec(CHSpanReader, instance=True)
    reader.__enter__.return_value = reader
    reader._resolve_session_ids_to_canonical.return_value = {SESSION_ID: CANONICAL_ID}
    reader.session_trace_ids.return_value = [TRACE_ID]
    reader.list_by_session.return_value = []
    get_reader = Mock(return_value=reader)
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", get_reader)
    monkeypatch.setattr("tfc.ee_gates.turing_oss_gate_for_template", lambda *_: None)
    # Binding validation also reads the persisted template-model fallback.
    template = SimpleNamespace(config={}, model=None)
    get_template = Mock(return_value=template)
    monkeypatch.setattr(playground, "_get_accessible_eval_template", get_template)
    run_eval = Mock(
        return_value={
            "output": "Passed",
            "reason": "model-free capture",
            "model": "",
            "metadata": {},
            "output_type": "Pass/Fail",
            "log_id": None,
        }
    )
    monkeypatch.setattr(playground, "run_eval_func", run_eval)

    def post(*, authenticated=True, **overrides):
        payload = {
            "template_id": str(uuid4()),
            "session_id": SESSION_ID,
            "model": "",
            "config": {"data_injection": {"session_context": True}},
        }
        payload.update(overrides)
        request = APIRequestFactory().post(
            "/model-hub/eval-playground/", payload, format="json"
        )
        request.organization = org
        request.workspace = workspace
        force_authenticate(
            request,
            user=(
                SimpleNamespace(is_authenticated=True)
                if authenticated
                else AnonymousUser()
            ),
        )
        return playground.EvalPlayGroundAPIView.as_view()(request)

    return SimpleNamespace(
        post=post,
        fields=fields,
        resolve=resolve,
        project_filter=project_filter,
        trace_filter=trace_filter,
        reader=reader,
        get_reader=get_reader,
        run_eval=run_eval,
        org=org,
        workspace=workspace,
        template=template,
    )


@pytest.mark.parametrize(
    "rows,status,subtotal,known,unknown,tokens",
    [
        ([{"cost": 1.25}, {"attrs_string": UNKNOWN}], "partial", 1.25, 1, 1, 240),
        ([{"attrs_string": UNKNOWN}], "unknown", 0.0, 0, 1, 120),
        ([{}], "complete", 0.0, 1, 0, 120),
        ([{"cost": 1.25}, {}], "complete", 1.25, 2, 0, 240),
        ([{}, {"attrs_string": UNKNOWN}], "partial", 0.0, 1, 1, 240),
        ([SOURCE_SUMMARY], "complete", 0.0, 0, 0, 0),
        (
            [SOURCE_SUMMARY, {"cost": 1.25}, {"attrs_string": UNKNOWN}],
            "partial",
            1.25,
            1,
            1,
            240,
        ),
        (
            [
                {
                    "id": "wrapper",
                    "name": "pydantic_ai.agent.run",
                    "observation_type": "agent",
                    "cost": 2.5,
                    "total_tokens": 240,
                    "prompt_tokens": 200,
                    "completion_tokens": 40,
                },
                {"parent_span_id": "wrapper", "cost": 1.25},
                {"parent_span_id": "wrapper", "cost": 1.25},
            ],
            "complete",
            2.5,
            2,
            0,
            240,
        ),
    ],
    ids=[
        "known-and-unknown",
        "unknown-only",
        "genuine-zero",
        "known-and-zero",
        "zero-and-unknown",
        "source-summary-only",
        "source-summary-and-requests",
        "proven-pydantic-wrapper-and-equal-siblings",
    ],
)
def test_preview_dispatches_coverage_aware_session_context(
    preview,
    rows,
    status,
    subtotal,
    known,
    unknown,
    tokens,
):
    spans = [_span(**row) for row in rows]
    for span in spans:
        if span.attrs_string.get("fi.cost.kind") == "unknown":
            assert span.cost is None
    preview.reader.list_by_session.return_value = spans

    response = preview.post()

    assert response.status_code == 200, response.data
    preview.run_eval.assert_called_once()
    args, kwargs = preview.run_eval.call_args
    assert args[0]["data_injection"] == {"session_context": True}
    assert args[2] is preview.template
    assert args[3] is preview.org
    assert kwargs["workspace"] is preview.workspace
    context = kwargs["session_context"]
    coverage = {
        "status": status,
        "known_subtotal": subtotal,
        "known_fact_count": known,
        "unknown_fact_count": unknown,
    }
    assert context["cost_coverage"] == coverage
    assert context["traces"][0]["cost_coverage"] == coverage
    assert context["total_cost"] == subtotal
    assert context["total_tokens"] == tokens
    assert context["traces"][0]["total_tokens"] == tokens
    assert context["total_spans"] == len(spans)
    assert context["traces"][0]["span_count"] == len(spans)
    assert context["trace_count"] == 1
    assert context["id"] == CANONICAL_ID
    assert context["project_id"] == PROJECT_ID
    assert context["name"] == "Renamed session"
    assert context["bookmarked"] is True
    assert context["created_at"] == preview.fields["first_seen"].isoformat()
    assert context["start_time"] == str(START)
    assert context["end_time"] == str(START + timedelta(seconds=1))
    assert context["duration_seconds"] == 1.0
    assert context["error_count"] == 0
    assert (
        "monetary coverage does not prove capture completeness" in context["cost_note"]
    )
    if unknown:
        assert "NOT a complete total" in context["cost_note"][:60]
        assert "Unknown price is not free" in context["cost_note"]
    preview.resolve.assert_called_once_with([UUID(SESSION_ID)])
    preview.project_filter.assert_called_once_with(
        id=PROJECT_ID, organization=preview.org
    )
    preview.reader._resolve_session_ids_to_canonical.assert_called_once_with(
        [SESSION_ID]
    )
    preview.reader.session_trace_ids.assert_called_once_with(PROJECT_ID, CANONICAL_ID)
    preview.reader.list_by_session.assert_called_once_with(
        CANONICAL_ID, project_id=PROJECT_ID
    )
    preview.trace_filter.assert_called_once_with(
        project_id=PROJECT_ID,
        id__in=[TRACE_ID],
        deleted=False,
    )


def test_preview_preserves_session_metadata_fallbacks(preview):
    preview.fields.update(display_name=None, bookmarked=False, first_seen=None)
    assert preview.post().status_code == 200
    context = preview.run_eval.call_args.kwargs["session_context"]
    assert context["name"] == "external-session"
    assert context["bookmarked"] is False
    assert context["created_at"] is None
    assert context["start_time"] is None
    assert context["end_time"] is None
    assert context["duration_seconds"] is None
    assert context["total_cost"] == 0
    assert context["cost_coverage"] == {
        "status": "complete",
        "known_subtotal": 0.0,
        "known_fact_count": 0,
        "unknown_fact_count": 0,
    }


@pytest.mark.parametrize("unavailable", ["missing", "foreign-project", "lookup-failed"])
def test_preview_never_reads_session_usage_without_verified_project(
    preview, unavailable
):
    if unavailable == "missing":
        preview.resolve.return_value = {}
    elif unavailable == "foreign-project":
        preview.project_filter.return_value.exists = lambda: False
    else:
        preview.project_filter.side_effect = RuntimeError("project lookup unavailable")
    assert preview.post().status_code == 200
    assert preview.run_eval.call_args.kwargs["session_context"] is None
    preview.get_reader.assert_not_called()
    preview.trace_filter.assert_not_called()


@pytest.mark.parametrize(
    "method", ["_resolve_session_ids_to_canonical", "list_by_session"]
)
def test_preview_does_not_publish_zero_context_on_read_failure(preview, method):
    getattr(preview.reader, method).side_effect = RuntimeError("telemetry unavailable")
    assert preview.post().status_code == 200
    assert preview.run_eval.call_args.kwargs["session_context"] is None


def test_preview_keeps_explicit_context_without_resolving_session(preview):
    supplied = {"id": SESSION_ID, "name": "caller-supplied"}
    assert preview.post(session_context=supplied).status_code == 200
    assert preview.run_eval.call_args.kwargs["session_context"] == supplied
    preview.resolve.assert_not_called()
    preview.get_reader.assert_not_called()


def test_preview_requires_authentication_before_session_lookup(preview):
    assert preview.post(authenticated=False).status_code in (401, 403)
    preview.resolve.assert_not_called()
    preview.get_reader.assert_not_called()
    preview.run_eval.assert_not_called()
