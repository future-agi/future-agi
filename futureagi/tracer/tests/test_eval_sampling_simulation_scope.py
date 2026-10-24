"""Sampling surfaces never admit the hidden project of a simulated run."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from simulate.models.agent_definition import AgentDefinition
from tracer.models.eval_task import EvalTask
from tracer.services.simulation_project import ensure_simulation_project

pytestmark = pytest.mark.django_db


@pytest.fixture
def hidden_sampling_project(project, custom_eval_config):
    agent = AgentDefinition.objects.create(
        agent_name="Sampling scope test",
        description="Hidden project admission regression",
        agent_type="text",
        inbound=True,
        organization=project.organization,
        workspace=project.workspace,
    )
    hidden = ensure_simulation_project(agent)
    # An otherwise valid evaluator must not be the reason admission is refused.
    custom_eval_config.project = hidden
    custom_eval_config.save(update_fields=["project"])
    return hidden


@pytest.fixture
def policy():
    now = timezone.now()
    return {
        "version": 1,
        "sampling_unit": "session",
        "mode": "closed",
        "window": {
            "start": (now - timedelta(days=1)).isoformat(),
            "end": (now - timedelta(minutes=1)).isoformat(),
        },
        "default": {"target_n": 1},
    }


def test_preview_refuses_hidden_project_before_reading(
    auth_client, hidden_sampling_project, custom_eval_config, policy
):
    with patch("tracer.views.eval_sampling.preview_policy") as preview:
        response = auth_client.post(
            "/tracer/eval-task/sampling_preview/",
            {
                "project": str(hidden_sampling_project.id),
                "row_type": "spans",
                "evals": [str(custom_eval_config.id)],
                "sampling_policy": policy,
            },
            format="json",
        )
    assert response.status_code == 400, response.content
    assert "Project not found" in response.json()["result"]
    preview.assert_not_called()


def test_create_refuses_hidden_project_without_leaving_a_task(
    auth_client, hidden_sampling_project, custom_eval_config, policy
):
    response = auth_client.post(
        "/tracer/eval-task/",
        {
            "project": str(hidden_sampling_project.id),
            "name": "Hidden policy task",
            "row_type": "spans",
            "run_type": "historical",
            "sampling_rate": 100,
            "sampling_policy": policy,
            "evals": [str(custom_eval_config.id)],
        },
        format="json",
    )
    assert response.status_code == 400, response.content
    assert "project" in response.json()["result"]
    assert not EvalTask.objects.filter(project=hidden_sampling_project).exists()


@pytest.mark.parametrize("explicit_cohort", [False, True])
def test_audit_refuses_even_a_preexisting_hidden_project_task(
    auth_client, hidden_sampling_project, policy, explicit_cohort
):
    import uuid

    # Simulation never makes an EvalTask; defend against an inconsistent or
    # preexisting row as well, rather than trusting a project FK join as admission.
    task = EvalTask.objects.create(
        project=hidden_sampling_project,
        name="Preexisting hidden policy task",
        row_type="spans",
        run_type="historical",
        sampling_policy=policy,
    )
    query = {"eval_task_id": str(task.id)}
    if explicit_cohort:
        query["cohort_id"] = str(uuid.uuid4())
    with patch("tracer.views.eval_sampling.sampling_audit", return_value={}) as audit:
        response = auth_client.get("/tracer/eval-task/sampling_audit/", query)
    assert response.status_code == 404, response.content
    audit.assert_not_called()
