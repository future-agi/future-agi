import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.constants.roles import OrganizationRoles
from tracer.models.project import Project


USER_CODE_EXAMPLE_PATH = "/tracer/users/get_code_example/"


@pytest.mark.django_db
def test_user_code_example_uses_scoped_observe_project(auth_client, observe_project):
    response = auth_client.get(
        USER_CODE_EXAMPLE_PATH,
        data={"project_id": str(observe_project.id)},
    )

    assert response.status_code == status.HTTP_200_OK
    code_example = response.json()["result"]
    assert f'project_name="{observe_project.name}"' in code_example
    assert "project_type=ProjectType.OBSERVE" in code_example
    assert "OpenAIInstrumentor().instrument" in code_example


@pytest.mark.django_db
def test_user_code_example_without_project_uses_default_project_name(auth_client):
    response = auth_client.get(USER_CODE_EXAMPLE_PATH)

    assert response.status_code == status.HTTP_200_OK
    code_example = response.json()["result"]
    assert 'project_name="New Project"' in code_example
    assert "user_id=\"newuser@example.com\"" in code_example


@pytest.mark.django_db
def test_user_code_example_rejects_non_observe_project(auth_client, project):
    response = auth_client.get(
        USER_CODE_EXAMPLE_PATH,
        data={"project_id": str(project.id)},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["message"] == "Project type must be 'observe'."


@pytest.mark.django_db
def test_user_code_example_hides_projects_outside_request_scope(
    auth_client,
    organization,
    user,
    workspace,
):
    other_workspace = Workspace.no_workspace_objects.create(
        name="Other Workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    other_workspace_project = Project.no_workspace_objects.create(
        name="Do Not Leak Same Org Workspace Project",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )

    other_organization = Organization.objects.create(name="Other Organization")
    other_user = User.objects.create_user(
        email="other-user@example.com",
        password="testpassword123",
        name="Other User",
        organization=other_organization,
        organization_role=OrganizationRoles.OWNER,
    )
    other_org_workspace = Workspace.no_workspace_objects.create(
        name="Other Org Workspace",
        organization=other_organization,
        is_default=True,
        is_active=True,
        created_by=other_user,
    )
    other_org_project = Project.no_workspace_objects.create(
        name="Do Not Leak Other Org Project",
        organization=other_organization,
        workspace=other_org_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )

    for out_of_scope_project in (other_workspace_project, other_org_project):
        response = auth_client.get(
            USER_CODE_EXAMPLE_PATH,
            data={"project_id": str(out_of_scope_project.id)},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert out_of_scope_project.name.encode() not in response.content
        assert b"project_name=" not in response.content
