# Pytest fixtures are imported from the existing routed tenancy suite.
# ruff: noqa: F811
"""J25 trace-context regression coverage through the authenticated playground.

Real request validation, tenant ORM queries, entry gates, and Observe's trace
identity resolver run unchanged. Only ClickHouse I/O and evaluation execution
are replaced; these tests do not claim a live collector or judge run.
"""

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from model_hub.models.ai_model import AIModel
from model_hub.tests.test_tenancy_sweep_eval_templates_r1 import (
    eval_factory,  # noqa: F401 - shared fixture
    routed,
    sweep_scope,  # noqa: F401 - shared fixture
    target_space,
)
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.trace_detail_reads import PhysicalSpanIdentity

pytestmark = pytest.mark.django_db
START = datetime(2026, 11, 9, 12, tzinfo=UTC)


def _project(space):
    return Project.no_workspace_objects.create(
        organization=space.organization,
        workspace=space,
        name=f"j25-project-{uuid4().hex}",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _span(project, trace_id, **overrides):
    row = {
        "project_id": str(project.pk),
        "trace_id": str(trace_id),
        "id": uuid4().hex[:16],
        "parent_span_id": "",
        "name": "collector-root",
        "observation_type": "agent",
        "start_time": START,
        "end_time": START + timedelta(seconds=1),
        "latency_ms": 1000,
        "model": "synthetic-model",
        "total_tokens": 12,
        "cost": 0.25,
        "status": "OK",
        "status_message": "",
        "trace_session_id": str(uuid4()),
        "metadata_json": '{"source":"collector"}',
        "input": '{"question":"synthetic input"}',
        "output": '"synthetic output"',
        "tags": '["collector-tag"]',
        "latest_version": 1,
        "latest_is_deleted": 0,
    }
    row.update(overrides)
    return row


class TraceTransport:
    """Synthetic storage, retaining the production reader's project predicates."""

    def __init__(self):
        self.rows = []
        self.span_reads = []
        self.detail_reads = []
        self.fail_at = None

    def list_by_trace(self, trace_id, *, project_id=None):
        self.span_reads.append((trace_id, project_id))
        if self.fail_at == "spans":
            raise RuntimeError("synthetic span read failure")
        # Deliberately reproduce the reader's unscoped behavior if the caller
        # omits project_id, so the pre-fix cross-tenant leak fails the test.
        return [
            SimpleNamespace(**row)
            for row in self.rows
            if row["trace_id"] == str(trace_id)
            and (project_id is None or row["project_id"] == str(project_id))
            and not row["latest_is_deleted"]
        ]

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        self.detail_reads.append(params)
        assert timeout_ms > 0
        assert settings["max_threads"] == 1
        assert "PREWHERE project_id IN %(detail_project_ids)s" in query
        assert "trace_id = %(detail_trace_id)s" in query
        candidates = [
            row
            for row in self.rows
            if row["project_id"] in params["detail_project_ids"]
            and row["trace_id"] == params["detail_trace_id"]
        ]
        if "detail_span_identities" not in params:
            if self.fail_at == "identity":
                raise RuntimeError("synthetic identity read failure")
            return SimpleNamespace(
                data=[{**row, "span_id": row["id"]} for row in candidates]
            )
        if self.fail_at == "content":
            raise RuntimeError("synthetic content read failure")
        identities = set(params["detail_span_identities"])
        rows = []
        for row in candidates:
            identity = PhysicalSpanIdentity(
                row["project_id"], row["trace_id"], row["id"], row["start_time"]
            )
            key = (
                identity.project_id,
                identity.trace_id,
                identity.span_id,
                identity.start_us,
            )
            if key in identities and not row["latest_is_deleted"]:
                rows.append(row)
        if self.fail_at == "incomplete":
            rows = []
        return SimpleNamespace(data=rows)


@pytest.fixture
def preview(sweep_scope, eval_factory, monkeypatch):
    from model_hub.views import separate_evals
    from tracer.services.clickhouse import v2
    from tracer.services.clickhouse.v2 import query_service

    template = eval_factory()
    projects = {
        boundary: _project(target_space(sweep_scope, boundary))
        for boundary in ("own", "workspace", "organization")
    }
    trace_id = uuid4()
    transport = TraceTransport()
    root = _span(projects["own"], trace_id)
    transport.rows.append(root)
    monkeypatch.setattr(v2, "get_reader", lambda: nullcontext(transport))
    monkeypatch.setattr(query_service, "V2AnalyticsQueryService", lambda: transport)
    execute = Mock(return_value={"score": 1, "reason": "synthetic execution"})
    monkeypatch.setattr(separate_evals, "run_eval_func", execute)

    def post(**overrides):
        body = {
            "template_id": str(template.pk),
            "model": None,
            "mapping": {"input": "caller-owned input"},
            "trace_id": str(trace_id),
        }
        body.update(overrides)
        return routed(sweep_scope, "post", "/model-hub/eval-playground/", body)

    def persist_trace():
        return Trace.no_workspace_objects.create(
            id=trace_id,
            project=projects["own"],
            name="legacy trace",
            metadata={"source": "postgres"},
            tags=["legacy-tag"],
            input={"legacy": "input"},
            output={"legacy": "output"},
        )

    return SimpleNamespace(
        post=post,
        execute=execute,
        transport=transport,
        projects=projects,
        trace_id=trace_id,
        root=root,
        persist_trace=persist_trace,
    )


def _context(preview):
    response = preview.post()
    assert response.status_code == 200, response.data
    preview.execute.assert_called_once()
    context = preview.execute.call_args.kwargs["trace_context"]
    assert context is not None
    return context


def test_collector_only_trace_reaches_evaluator(preview):
    assert not Trace.no_workspace_objects.filter(id=preview.trace_id).exists()
    child = _span(
        preview.projects["own"],
        preview.trace_id,
        parent_span_id=preview.root["id"],
        name="collector-child",
        start_time=START + timedelta(seconds=1),
        end_time=START + timedelta(seconds=2),
        status="ERROR",
    )
    preview.transport.rows.append(child)

    context = _context(preview)

    assert context["id"] == str(preview.trace_id)
    assert context["project_id"] == str(preview.projects["own"].pk)
    assert context["name"] == "collector-root"
    assert context["metadata"] == {"source": "collector"}
    assert context["tags"] == ["collector-tag"]
    assert context["input"] == {"question": "synthetic input"}
    assert context["output"] == "synthetic output"
    assert context["session_id"] == preview.root["trace_session_id"]
    assert context["error"] is True
    assert context["error_count"] == 1
    assert context["span_count"] == 2
    assert context["total_tokens"] == 24
    assert context["total_cost"] == 0.5
    assert context["total_latency_ms"] == 2000
    assert context["start_time"] == str(START)
    assert context["end_time"] == str(START + timedelta(seconds=2))
    assert [span["name"] for span in context["spans"]] == [
        "collector-root",
        "collector-child",
    ]
    assert not Trace.no_workspace_objects.filter(id=preview.trace_id).exists()


@pytest.mark.parametrize("storage", ["postgres", "collector"])
def test_same_trace_id_never_mixes_projects(preview, storage):
    if storage == "postgres":
        preview.persist_trace()
    for boundary in ("organization", "workspace"):
        preview.transport.rows.append(
            _span(
                preview.projects[boundary],
                preview.trace_id,
                name=f"foreign-{boundary}-span",
                status_message=f"foreign-{boundary}-message",
                model=f"foreign-{boundary}-model",
                total_tokens=999,
                cost=999.0,
                status="ERROR",
                latest_version=999,
            )
        )

    context = _context(preview)

    assert context["project_id"] == str(preview.projects["own"].pk)
    assert [span["name"] for span in context["spans"]] == ["collector-root"]
    assert context["span_count"] == 1
    assert context["error_count"] == 0
    assert context["total_tokens"] == 12
    assert context["total_cost"] == 0.25
    assert "foreign-" not in json.dumps(context)
    if storage == "postgres":
        assert preview.transport.span_reads == [
            (str(preview.trace_id), str(preview.projects["own"].pk))
        ]
        assert preview.transport.detail_reads == []
    else:
        for params in preview.transport.detail_reads:
            assert set(params["detail_project_ids"]) == {
                str(preview.projects["own"].pk)
            }


def test_collector_collision_selects_latest_authorized_project(preview, sweep_scope):
    newer = _project(sweep_scope.workspace)
    preview.transport.rows.append(
        _span(newer, preview.trace_id, name="newer-copy", latest_version=2)
    )
    context = _context(preview)
    assert context["project_id"] == str(newer.pk)
    assert context["name"] == "newer-copy"
    assert context["span_count"] == 1
    assert [span["name"] for span in context["spans"]] == ["newer-copy"]
    assert preview.transport.detail_reads[-1]["detail_project_ids"] == (str(newer.pk),)


@pytest.mark.parametrize("boundary", ["missing", "organization", "workspace"])
def test_unresolvable_trace_is_rejected_before_evaluation(preview, boundary):
    preview.transport.rows.clear()
    if boundary != "missing":
        preview.transport.rows.append(
            _span(preview.projects[boundary], preview.trace_id)
        )
    response = preview.post()
    assert response.status_code == 400, response.data
    assert "The selected trace is unavailable." in json.dumps(response.data)
    preview.execute.assert_not_called()


@pytest.mark.parametrize("fail_at", ["spans", "identity", "content", "incomplete"])
def test_trace_read_failure_is_rejected_before_evaluation(preview, fail_at):
    if fail_at == "spans":
        preview.persist_trace()
    preview.transport.fail_at = fail_at
    response = preview.post()
    assert response.status_code == 400, response.data
    assert "The selected trace is unavailable." in json.dumps(response.data)
    assert "synthetic" not in json.dumps(response.data)
    preview.execute.assert_not_called()


def test_tombstoned_collector_trace_is_not_evaluated(preview):
    preview.root["latest_is_deleted"] = 1
    response = preview.post()
    assert response.status_code == 400, response.data
    preview.execute.assert_not_called()
    assert len(preview.transport.detail_reads) == 1


def test_legacy_trace_preserves_postgres_metadata_and_empty_spans(preview):
    trace = preview.persist_trace()
    preview.transport.rows.clear()
    context = _context(preview)
    assert context["name"] == trace.name
    assert context["metadata"] == {"source": "postgres"}
    assert context["tags"] == ["legacy-tag"]
    assert context["input"] == {"legacy": "input"}
    assert context["output"] == {"legacy": "output"}
    assert context["span_count"] == 0
    assert context["spans"] == []
    assert context["created_at"] == trace.created_at.isoformat()
    assert preview.transport.detail_reads == []


def test_foreign_postgres_row_does_not_shadow_collector_trace(preview):
    Trace.no_workspace_objects.create(
        id=preview.trace_id,
        project=preview.projects["organization"],
        name="foreign-postgres-trace",
        input="foreign-postgres-input",
    )
    context = _context(preview)
    assert context["name"] == "collector-root"
    assert context["project_id"] == str(preview.projects["own"].pk)
    assert "foreign-postgres" not in json.dumps(context)


def test_collector_summary_cap_does_not_truncate_aggregates(preview):
    preview.transport.rows.extend(
        _span(
            preview.projects["own"],
            preview.trace_id,
            parent_span_id=preview.root["id"],
        )
        for _ in range(200)
    )
    context = _context(preview)
    assert len(context["spans"]) == 200
    assert context["span_count"] == 201
    assert context["total_tokens"] == 201 * 12
    assert context["total_cost"] == 201 * 0.25


def test_explicit_context_does_not_trigger_trace_lookup(preview):
    supplied = {"id": "caller-context", "spans": []}
    response = preview.post(trace_context=supplied)
    assert response.status_code == 200, response.data
    assert preview.execute.call_args.kwargs["trace_context"] == supplied
    assert preview.transport.span_reads == []
    assert preview.transport.detail_reads == []


def test_no_trace_id_preserves_non_trace_evaluations(preview):
    response = preview.post(trace_id=None)
    assert response.status_code == 200, response.data
    assert preview.execute.call_args.kwargs["trace_context"] is None
    assert preview.transport.span_reads == []
    assert preview.transport.detail_reads == []
