"""/tracer/eval-task/ resolves eval configs only within the caller's
organization and workspace, on create and on update.

An id outside the caller's scope gets the same answer as an id that does not
exist.
"""

import uuid

import pytest
from rest_framework import status

import tracer.serializers.eval_task  # noqa: F401  (imported with no workspace bound)
import tracer.views.eval_task  # noqa: F401
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from tfc.utils.tests.two_tenants import make_tenant, same_answer
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.project import Project

URL = "/tracer/eval-task/"


def _project(tenant, name):
    return Project.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _config(project, template, name):
    return CustomEvalConfig.no_workspace_objects.create(
        name=name,
        project=project,
        eval_template=template,
        config={},
        mapping={"input": "input", "output": "output"},
        filters={},
    )


@pytest.fixture
def setup(db):
    tenant_a = make_tenant("A")
    tenant_b = make_tenant("B")
    template = EvalTemplate.no_workspace_objects.create(
        name="shared-system-template",
        owner=OwnerChoices.SYSTEM.value,
        config={"type": "pass_fail", "criteria": "Is the output correct?"},
    )
    a_project = _project(tenant_a, "A project")
    b_project = _project(tenant_b, "B project")
    b_config = _config(b_project, template, "B config")
    task = EvalTask.no_workspace_objects.create(
        project=b_project,
        name="B task",
        filters={},
        sampling_rate=50.0,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
    )
    task.evals.add(b_config)
    data = {
        "a": tenant_a,
        "b": tenant_b,
        "a_config": _config(a_project, template, "A config"),
        "b_project": b_project,
        "b_config": b_config,
        "b_other_config": _config(b_project, template, "B other config"),
        "task": task,
    }
    yield data
    tenant_a.client.stop_workspace_injection()
    tenant_b.client.stop_workspace_injection()


def _full_body(setup, eval_id):
    return {
        "project": str(setup["b_project"].id),
        "name": "B task",
        "run_type": RunType.CONTINUOUS,
        "sampling_rate": 50.0,
        "filters": {},
        "evals": [str(eval_id)],
    }


def _task_eval_ids(setup):
    return set(
        EvalTask.no_workspace_objects.get(id=setup["task"].id).evals.values_list(
            "id", flat=True
        )
    )


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskTenantScope:
    def test_eval_task_patch_rejects_other_organization_eval_config(self, setup):
        foreign_id = setup["a_config"].id
        unknown_id = uuid.uuid4()
        detail = f"{URL}{setup['task'].id}/"

        foreign = setup["b"].client.patch(
            detail, {"evals": [str(foreign_id)]}, format="json"
        )
        unknown = setup["b"].client.patch(
            detail, {"evals": [str(unknown_id)]}, format="json"
        )

        assert foreign.status_code == status.HTTP_400_BAD_REQUEST, foreign.content
        assert same_answer(foreign, foreign_id, unknown, unknown_id)
        assert _task_eval_ids(setup) == {setup["b_config"].id}

    def test_eval_task_put_rejects_other_organization_eval_config(self, setup):
        foreign_id = setup["a_config"].id
        unknown_id = uuid.uuid4()
        detail = f"{URL}{setup['task'].id}/"

        foreign = setup["b"].client.put(
            detail, _full_body(setup, foreign_id), format="json"
        )
        unknown = setup["b"].client.put(
            detail, _full_body(setup, unknown_id), format="json"
        )

        assert foreign.status_code == status.HTTP_400_BAD_REQUEST, foreign.content
        assert same_answer(foreign, foreign_id, unknown, unknown_id)
        assert _task_eval_ids(setup) == {setup["b_config"].id}

    def test_eval_task_create_rejects_other_organization_eval_config(self, setup):
        foreign_id = setup["a_config"].id
        unknown_id = uuid.uuid4()
        before = EvalTask.no_workspace_objects.count()

        foreign = setup["b"].client.post(
            URL, _full_body(setup, foreign_id), format="json"
        )
        unknown = setup["b"].client.post(
            URL, _full_body(setup, unknown_id), format="json"
        )

        assert foreign.status_code == status.HTTP_400_BAD_REQUEST, foreign.content
        assert same_answer(foreign, foreign_id, unknown, unknown_id)
        assert EvalTask.no_workspace_objects.count() == before

    def test_eval_task_patch_accepts_own_eval_config(self, setup):
        response = setup["b"].client.patch(
            f"{URL}{setup['task'].id}/",
            {"evals": [str(setup["b_other_config"].id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        assert _task_eval_ids(setup) == {setup["b_other_config"].id}
