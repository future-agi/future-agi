"""Exercise playground trace injection against the PG and ClickHouse stores."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from accounts.models.organization import Organization
from model_hub.views import separate_evals as playground
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.tests._ch_seed import seed_ch_span, seed_ch_trace

pytestmark = pytest.mark.django_db


@pytest.fixture
def projects(organization, workspace):
    def create(org, ws=None):
        return Project.objects.create(
            name=f"playground-{uuid4()}",
            organization=org,
            workspace=ws,
            model_type="GenerativeLLM",
            trace_type="observe",
        )

    return create(organization, workspace), create(
        Organization.objects.create(name="Other organization")
    )


@pytest.fixture
def preview(auth_client, monkeypatch):
    template = SimpleNamespace(config={}, model=None)
    monkeypatch.setattr(
        playground, "_get_accessible_eval_template", lambda *a, **k: template
    )
    monkeypatch.setattr("tfc.ee_gates.turing_oss_gate_for_template", lambda *_: None)
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
    template_id = str(uuid4())

    def post(trace_id):
        run_eval.reset_mock()
        response = auth_client.post(
            "/model-hub/eval-playground/",
            {
                "template_id": template_id,
                "trace_id": str(trace_id),
                "model": "",
                "config": {"data_injection": {"trace_context": True}},
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        run_eval.assert_called_once()
        return response.data, run_eval.call_args.kwargs["trace_context"]

    return post


def _seed_span(project, trace_id, start, name, tokens):
    span_id = str(uuid4())
    seed_ch_span(
        {
            "id": span_id,
            "project_id": str(project.id),
            "trace_id": str(trace_id),
            "name": name,
            "status": "ERROR",
            "status_message": "span failure",
            "observation_type": "llm",
            "model": "test-model",
            "start_time": start,
            "end_time": start + timedelta(seconds=1),
            "latency_ms": 1000,
            "total_tokens": tokens,
            "cost": 0.25,
        }
    )
    return span_id


@pytest.mark.parametrize("source", ["clickhouse", "postgres"])
def test_preview_injects_project_scoped_trace_context(preview, projects, source):
    own, other = projects
    start = datetime.now(UTC).replace(microsecond=0)
    trace = Trace(
        project=own,
        session_id=uuid4(),
        name="Trace from " + source,
        metadata={"source": source},
        tags=["playground"],
        input={"query": "hello"},
        output=["reply"],
        error={"message": "failed"},
        created_at=start,
    )
    if source == "postgres":
        # No curated CH trace row: the original PG metadata must still be used.
        trace.save()
    else:
        seed_ch_trace(trace)
        assert not Trace.objects.filter(pk=trace.id).exists()

    span_id = _seed_span(own, trace.id, start, "own span", 7)
    # A later span shares the trace ID but belongs to an inaccessible project.
    _seed_span(other, trace.id, start + timedelta(seconds=3), "other span", 99)

    _, context = preview(trace.id)
    assert context is not None
    # Both stores must retain the PG payload contract, including decoded JSON.
    assert (
        datetime.fromisoformat(context["created_at"]).replace(tzinfo=UTC)
        == trace.created_at
    )
    assert datetime.fromisoformat(context["start_time"]).replace(tzinfo=UTC) == start
    assert datetime.fromisoformat(context["end_time"]).replace(
        tzinfo=UTC
    ) == start + timedelta(seconds=1)
    assert {
        k: v
        for k, v in context.items()
        if k not in {"created_at", "start_time", "end_time"}
    } == {
        "id": str(trace.id),
        "project_id": str(own.id),
        "name": trace.name,
        "session_id": str(trace.session_id),
        "metadata": trace.metadata,
        "tags": trace.tags,
        "input": trace.input,
        "output": trace.output,
        "error": trace.error,
        "span_count": 1,
        "error_count": 1,
        "total_tokens": 7,
        "total_cost": 0.25,
        "total_latency_ms": 1000,
        "spans": [
            {
                "id": span_id,
                "name": "own span",
                "observation_type": "llm",
                "status": "ERROR",
                "status_message": "span failure",
                "latency_ms": 1000,
                "model": "test-model",
                "total_tokens": 7,
                "cost": 0.25,
                "parent_span_id": None,
            }
        ],
    }


def test_preview_foreign_trace_is_indistinguishable_from_missing(preview, projects):
    _, other = projects
    foreign = Trace(project=other, name="Foreign trace")
    seed_ch_trace(foreign)
    _seed_span(other, foreign.id, datetime.now(UTC), "Foreign span", 21)

    missing_response, missing_context = preview(uuid4())
    foreign_response, foreign_context = preview(foreign.id)
    assert foreign_context is missing_context is None
    assert foreign_response == missing_response
