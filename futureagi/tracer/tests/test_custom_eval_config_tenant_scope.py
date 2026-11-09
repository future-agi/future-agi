"""/tracer/custom-eval-config/ resolves eval templates, projects and
knowledge-base files only within the caller's organization and workspace.

An id outside the caller's scope gets the same answer as an id that does not
exist.
"""

import uuid

import pytest
from rest_framework import status

import tracer.serializers.custom_eval_config  # noqa: F401  (imported with no workspace bound)
import tracer.views.custom_eval_config  # noqa: F401
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate
from tfc.utils.tests.two_tenants import make_tenant, same_answer
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project

URL = "/tracer/custom-eval-config/"
TEMPLATE_CONFIG = {"type": "pass_fail", "criteria": "Is the output correct?"}


def _project(tenant, name):
    return Project.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="experiment",
    )


def _user_template(tenant, name):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace,
        owner=OwnerChoices.USER.value,
        config=TEMPLATE_CONFIG,
    )


def _kb_file(tenant, name):
    return KnowledgeBaseFile.no_workspace_objects.create(
        name=name, organization=tenant.organization, workspace=tenant.workspace
    )


@pytest.fixture
def setup(db):
    tenant_a = make_tenant("A")
    tenant_b = make_tenant("B")
    data = {
        "a": tenant_a,
        "b": tenant_b,
        "a_project": _project(tenant_a, "A project"),
        "a_template": _user_template(tenant_a, "tenant-a-template"),
        "a_kb": _kb_file(tenant_a, "A knowledge base"),
        "b_project": _project(tenant_b, "B project"),
        "b_template": _user_template(tenant_b, "tenant-b-template"),
        "b_kb": _kb_file(tenant_b, "B knowledge base"),
    }
    yield data
    tenant_a.client.stop_workspace_injection()
    tenant_b.client.stop_workspace_injection()


def _create_body(setup, **overrides):
    body = {
        "name": f"config-{uuid.uuid4().hex[:8]}",
        "project": str(setup["b_project"].id),
        "eval_template": str(setup["b_template"].id),
        "config": {},
        "mapping": {"input": "input", "output": "output"},
    }
    body.update({key: str(value) for key, value in overrides.items()})
    return body


def _b_config(setup):
    return CustomEvalConfig.no_workspace_objects.create(
        name="B config",
        project=setup["b_project"],
        eval_template=setup["b_template"],
        kb_id=setup["b_kb"],
        config={},
        mapping={"input": "input", "output": "output"},
        filters={},
    )


FOREIGN_FIELDS = {
    "project": "a_project",
    "eval_template": "a_template",
    "kb_id": "a_kb",
}


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigTenantScope:
    def test_custom_eval_config_create_accepts_own_references(self, setup):
        response = setup["b"].client.post(
            URL, _create_body(setup, kb_id=setup["b_kb"].id), format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        config = CustomEvalConfig.no_workspace_objects.get(
            id=response.json()["result"]["id"]
        )
        assert config.project_id == setup["b_project"].id
        assert config.kb_id_id == setup["b_kb"].id

    @pytest.mark.parametrize("field", sorted(FOREIGN_FIELDS))
    def test_custom_eval_config_create_rejects_other_organization_reference(
        self, setup, field
    ):
        foreign_id = setup[FOREIGN_FIELDS[field]].id
        unknown_id = uuid.uuid4()
        before = CustomEvalConfig.no_workspace_objects.count()

        foreign = setup["b"].client.post(
            URL, _create_body(setup, **{field: foreign_id}), format="json"
        )
        unknown = setup["b"].client.post(
            URL, _create_body(setup, **{field: unknown_id}), format="json"
        )

        assert foreign.status_code == status.HTTP_400_BAD_REQUEST, foreign.content
        assert same_answer(foreign, foreign_id, unknown, unknown_id)
        assert CustomEvalConfig.no_workspace_objects.count() == before

    @pytest.mark.parametrize("field", sorted(FOREIGN_FIELDS))
    def test_custom_eval_config_patch_rejects_other_organization_reference(
        self, setup, field
    ):
        config = _b_config(setup)
        foreign_id = setup[FOREIGN_FIELDS[field]].id
        unknown_id = uuid.uuid4()

        foreign = setup["b"].client.patch(
            f"{URL}{config.id}/", {field: str(foreign_id)}, format="json"
        )
        unknown = setup["b"].client.patch(
            f"{URL}{config.id}/", {field: str(unknown_id)}, format="json"
        )

        assert foreign.status_code == status.HTTP_400_BAD_REQUEST, foreign.content
        assert same_answer(foreign, foreign_id, unknown, unknown_id)
        config.refresh_from_db()
        assert config.project_id == setup["b_project"].id
        assert config.eval_template_id == setup["b_template"].id
        assert config.kb_id_id == setup["b_kb"].id

    def test_custom_eval_config_put_rejects_other_organization_project(self, setup):
        config = _b_config(setup)
        body = _create_body(setup, project=setup["a_project"].id)

        response = setup["b"].client.put(f"{URL}{config.id}/", body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        config.refresh_from_db()
        assert config.project_id == setup["b_project"].id

    def test_custom_eval_config_list_hides_other_organization_configs(self, setup):
        # A project with no workspace: a default workspace covers such rows of
        # its own organization only.
        a_project = Project.no_workspace_objects.create(
            name="A project, no workspace",
            organization=setup["a"].organization,
            workspace=None,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="experiment",
        )
        a_config = CustomEvalConfig.no_workspace_objects.create(
            name="A config",
            project=a_project,
            eval_template=setup["a_template"],
            config={},
            mapping={},
            filters={},
        )
        b_config = _b_config(setup)

        response = setup["b"].client.get(URL)

        assert response.status_code == status.HTTP_200_OK, response.content
        body = response.content.decode()
        assert str(b_config.id) in body
        assert str(a_config.id) not in body
