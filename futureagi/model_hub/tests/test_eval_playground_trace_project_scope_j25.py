"""Trace-context hydration must keep colliding trace IDs project-scoped."""

import json
from types import SimpleNamespace

import pytest
from rest_framework import status

from accounts.models import Organization
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.project import Project
from tracer.models.trace import Trace


@pytest.mark.django_db
def test_eval_playground_trace_context_is_scoped_to_authorized_project(
    auth_client, user, workspace, monkeypatch
):
    project = Project.objects.create(
        name="j25-playground-project",
        organization=user.organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    foreign_org = Organization.objects.create(name="j25-foreign-organization")
    foreign_project = Project.no_workspace_objects.create(
        name="j25-foreign-project",
        organization=foreign_org,
        workspace=None,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    trace = Trace.objects.create(project=project, name="j25-authorized-trace")
    template = EvalTemplate.no_workspace_objects.create(
        name="j25-playground-code",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        config={
            "code": "def evaluate(output=None, **kwargs):\n    return True",
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "required_keys": ["output"],
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )

    # CH can hold the same trace ID in two projects; PG authorizes only one.
    own_span = SimpleNamespace(
        id="j25-authorized-span",
        trace_id=str(trace.id),
        project_id=str(project.id),
        name="j25-authorized-marker",
        observation_type="agent",
        status="OK",
        status_message="",
        total_tokens=7,
        cost=0.25,
        latency_ms=12,
        start_time=None,
        end_time=None,
        model="",
        parent_span_id="",
    )
    foreign_span = SimpleNamespace(
        **{
            **vars(own_span),
            "id": "j25-foreign-span",
            "project_id": str(foreign_project.id),
            "name": "j25-foreign-marker",
            "status": "ERROR",
            "total_tokens": 101,
            "cost": 5.0,
            "latency_ms": 500,
        }
    )
    reader_calls = []
    evaluation_inputs = []

    class FakeReader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def list_by_trace(self, trace_id, *, project_id=None):
            reader_calls.append((trace_id, {"project_id": project_id}))
            return [
                span
                for span in (own_span, foreign_span)
                if span.trace_id == trace_id
                and (project_id is None or span.project_id == project_id)
            ]

    def capture_evaluation(*args, **kwargs):
        evaluation_inputs.append(kwargs)
        return {"output": "Passed", "reason": "synthetic evaluation"}

    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", FakeReader)
    monkeypatch.setattr(
        "model_hub.views.separate_evals.run_eval_func", capture_evaluation
    )

    response = auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": "",
            "mapping": {"output": "same"},
            "config": {"params": {}},
            "trace_id": str(trace.id),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    assert reader_calls == [(str(trace.id), {"project_id": str(trace.project_id)})]
    assert len(evaluation_inputs) == 1
    trace_context = evaluation_inputs[0]["trace_context"]
    assert trace_context is not None
    assert trace_context["id"] == str(trace.id)
    assert trace_context["project_id"] == str(trace.project_id)
    assert [span["id"] for span in trace_context["spans"]] == [own_span.id]
    assert own_span.name in json.dumps(trace_context)
    assert foreign_span.name not in json.dumps(trace_context)
    assert trace_context["span_count"] == 1
    assert trace_context["error_count"] == 0
    assert trace_context["total_tokens"] == own_span.total_tokens
    assert trace_context["total_cost"] == own_span.cost
    assert trace_context["total_latency_ms"] == own_span.latency_ms
