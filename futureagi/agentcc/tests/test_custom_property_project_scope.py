"""ALERT-ACCESS-1 (TEN-1028-05): a custom property's project is one of the
caller's organization's projects, whether or not a workspace is resolved.

At the base the writable project field looked projects up through the default
manager, which narrows to the request's workspace only when one is set. With
no workspace resolved, another organization's project id was accepted (and
the answer told whether that id exists). Now the field only accepts the
organization's own projects, and refuses every project when the organization
is unknown.
"""

import uuid
from unittest.mock import patch

import pytest

from agentcc.models.custom_property import AgentccCustomPropertySchema
from agentcc.models.project import AgentccProject
from agentcc.serializers.custom_property import AgentccCustomPropertySchemaSerializer
from agentcc.tests.r7_console_a_support import owner_of_new_org
from tfc.middleware.workspace_context import clear_workspace_context

URL = "/agentcc/custom-properties/"


@pytest.fixture
def own_project(organization, workspace):
    return AgentccProject.no_workspace_objects.create(
        organization=organization, workspace=workspace, name="own-project"
    )


@pytest.fixture
def foreign_project(db):
    other_org, _, other_workspace = owner_of_new_org("ALERT-ACCESS other org")
    return AgentccProject.no_workspace_objects.create(
        organization=other_org, workspace=other_workspace, name="foreign-project"
    )


def _no_workspace():
    """The request resolves no workspace (a user with no workspace membership
    in the organization): the default manager applies no workspace filter."""
    return patch("tfc.utils.base_model.get_current_workspace", return_value=None)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestCustomPropertyProjectIsTheOrgsOwn:
    def test_another_orgs_project_is_refused_on_create(
        self, auth_client, foreign_project
    ):
        with _no_workspace():
            response = auth_client.post(
                URL,
                {
                    "name": "tier",
                    "property_type": "string",
                    "project": str(foreign_project.id),
                },
                format="json",
            )

        assert response.status_code == 400, response.content
        assert "project" in response.json().get("details", {})
        assert not AgentccCustomPropertySchema.no_workspace_objects.filter(
            name="tier"
        ).exists()

    def test_a_foreign_and_a_missing_project_answer_alike(
        self, auth_client, foreign_project
    ):
        missing = uuid.uuid4()
        answers = []
        with _no_workspace():
            for project_id in (foreign_project.id, missing):
                response = auth_client.post(
                    URL,
                    {
                        "name": "tier",
                        "property_type": "string",
                        "project": str(project_id),
                    },
                    format="json",
                )
                assert response.status_code == 400, response.content
                answers.append(
                    response.json()["details"]["project"][0].replace(
                        str(project_id), "<id>"
                    )
                )

        assert answers[0] == answers[1]

    def test_another_orgs_project_is_refused_on_update(
        self, auth_client, organization, foreign_project
    ):
        schema = AgentccCustomPropertySchema.no_workspace_objects.create(
            organization=organization, name="tier", property_type="string"
        )

        with _no_workspace():
            response = auth_client.patch(
                f"{URL}{schema.id}/",
                {"project": str(foreign_project.id)},
                format="json",
            )

        assert response.status_code == 400, response.content
        schema.refresh_from_db()
        assert schema.project_id is None

    def test_the_orgs_own_project_is_accepted(
        self, auth_client, organization, own_project
    ):
        with _no_workspace():
            created = auth_client.post(
                URL,
                {
                    "name": "tier",
                    "property_type": "string",
                    "project": str(own_project.id),
                },
                format="json",
            )

        assert created.status_code == 200, created.content
        schema = AgentccCustomPropertySchema.no_workspace_objects.get(name="tier")
        assert schema.organization_id == organization.id
        assert schema.project_id == own_project.id

    def test_the_orgs_own_project_is_accepted_in_its_workspace(
        self, auth_client, own_project
    ):
        created = auth_client.post(
            URL,
            {"name": "tier", "property_type": "string", "project": str(own_project.id)},
            format="json",
        )

        assert created.status_code == 200, created.content

    def test_no_project_is_still_accepted(self, auth_client):
        with _no_workspace():
            created = auth_client.post(
                URL, {"name": "tier", "property_type": "string"}, format="json"
            )

        assert created.status_code == 200, created.content


@pytest.mark.django_db
class TestCustomPropertySerializerScope:
    def test_the_project_is_looked_up_in_the_given_org_only(
        self, organization, own_project, foreign_project
    ):
        clear_workspace_context()
        data = {"name": "tier", "property_type": "string"}

        foreign = AgentccCustomPropertySchemaSerializer(
            data={**data, "project": str(foreign_project.id)},
            context={"organization": organization},
        )
        own = AgentccCustomPropertySchemaSerializer(
            data={**data, "project": str(own_project.id)},
            context={"organization": organization},
        )

        assert not foreign.is_valid()
        assert "project" in foreign.errors
        assert own.is_valid(), own.errors

    def test_without_an_org_no_project_is_accepted(self, own_project):
        clear_workspace_context()

        serializer = AgentccCustomPropertySchemaSerializer(
            data={
                "name": "tier",
                "property_type": "string",
                "project": str(own_project.id),
            }
        )

        assert not serializer.is_valid()
        assert "project" in serializer.errors
