"""/model-hub/annotations-labels/ resolves a label's project only within the
caller's organization and workspace, on create and on update.

An id outside the caller's scope gets the same answer as an id that does not
exist.
"""

import uuid

import pytest
from rest_framework import status

import model_hub.serializers.develop_annotations  # noqa: F401  (imported with no workspace bound)
import model_hub.views.develop_annotations  # noqa: F401
from model_hub.models.ai_model import AIModel
from model_hub.models.develop_annotations import AnnotationsLabels
from tfc.utils.tests.two_tenants import make_tenant, same_answer
from tracer.models.project import Project

URL = "/model-hub/annotations-labels/"
SETTINGS = {
    "options": [{"label": "yes"}, {"label": "no"}],
    "multi_choice": False,
    "rule_prompt": "",
    "auto_annotate": False,
    "strategy": None,
}


def _project(tenant, name, workspace="tenant"):
    return Project.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace if workspace == "tenant" else workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _body(project_id, **extra):
    body = {
        "name": f"label-{uuid.uuid4().hex[:8]}",
        "type": "categorical",
        "settings": SETTINGS,
        "project": str(project_id),
    }
    body.update(extra)
    return body


@pytest.fixture
def setup(db):
    tenant_a = make_tenant("A")
    tenant_b = make_tenant("B")
    b_project = _project(tenant_b, "B project")
    data = {
        "a": tenant_a,
        "b": tenant_b,
        "a_project": _project(tenant_a, "A project"),
        "a_project_no_workspace": _project(
            tenant_a, "A project, no workspace", workspace=None
        ),
        "b_project": b_project,
        "b_label": AnnotationsLabels.no_workspace_objects.create(
            name="B label",
            type="categorical",
            settings=SETTINGS,
            organization=tenant_b.organization,
            workspace=tenant_b.workspace,
            project=b_project,
        ),
    }
    yield data
    tenant_a.client.stop_workspace_injection()
    tenant_b.client.stop_workspace_injection()


FOREIGN_PROJECTS = ["a_project", "a_project_no_workspace"]


@pytest.mark.integration
@pytest.mark.api
class TestAnnotationLabelTenantScope:
    def test_annotation_label_create_accepts_own_project(self, setup):
        response = setup["b"].client.post(
            URL, _body(setup["b_project"].id), format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        assert AnnotationsLabels.no_workspace_objects.filter(
            project=setup["b_project"], organization=setup["b"].organization
        ).exists()

    @pytest.mark.parametrize("foreign", FOREIGN_PROJECTS)
    def test_annotation_label_create_rejects_other_organization_project(
        self, setup, foreign
    ):
        foreign_id = setup[foreign].id
        unknown_id = uuid.uuid4()

        foreign_response = setup["b"].client.post(
            URL, _body(foreign_id, name="same name"), format="json"
        )
        unknown_response = setup["b"].client.post(
            URL, _body(unknown_id, name="same name"), format="json"
        )

        assert foreign_response.status_code == status.HTTP_400_BAD_REQUEST, (
            foreign_response.content
        )
        assert same_answer(foreign_response, foreign_id, unknown_response, unknown_id)
        assert not AnnotationsLabels.all_objects.filter(project_id=foreign_id).exists()

    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_annotation_label_update_rejects_other_organization_project(
        self, setup, method
    ):
        label = setup["b_label"]
        foreign_id = setup["a_project"].id
        unknown_id = uuid.uuid4()
        send = getattr(setup["b"].client, method)

        foreign_response = send(
            f"{URL}{label.id}/", _body(foreign_id, name=label.name), format="json"
        )
        unknown_response = send(
            f"{URL}{label.id}/", _body(unknown_id, name=label.name), format="json"
        )

        assert foreign_response.status_code == status.HTTP_400_BAD_REQUEST, (
            foreign_response.content
        )
        assert same_answer(foreign_response, foreign_id, unknown_response, unknown_id)
        label.refresh_from_db()
        assert label.project_id == setup["b_project"].id
