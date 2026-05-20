"""
API tests for GET /simulate/run-tests/ (RunTestListView).
"""

from uuid import uuid4

import pytest
from rest_framework import status

from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import AgentDefinition, RunTest, Scenarios
from simulate.models.agent_version import AgentVersion


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def prompt_template(db, organization, workspace):
    return PromptTemplate.objects.create(
        name="Test prompt template",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def prompt_version_v10(db, prompt_template):
    # template_version is a CharField on the model — the response serializer
    # must accept any string, not just numeric values.
    return PromptVersion.objects.create(
        original_template=prompt_template,
        template_version="v10",
    )


@pytest.fixture
def scenario_with_prompt_version(
    db,
    organization,
    workspace,
    agent_definition,
    prompt_template,
    prompt_version_v10,
):
    return Scenarios.objects.create(
        name="Scenario linked to v10 prompt version",
        source="seed",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        source_type=Scenarios.SourceTypes.AGENT_DEFINITION,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        prompt_template=prompt_template,
        prompt_version=prompt_version_v10,
    )


@pytest.fixture
def run_test_with_v10_scenario(
    db,
    organization,
    workspace,
    agent_definition,
    scenario_with_prompt_version,
):
    run_test = RunTest.objects.create(
        name="Run test referencing v10-prompt scenario",
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
    )
    run_test.scenarios.set([scenario_with_prompt_version])
    return run_test


@pytest.mark.integration
@pytest.mark.api
class TestRunTestListPromptVersionRegression:
    def test_list_succeeds_when_scenario_prompt_version_is_non_numeric(
        self, auth_client, run_test_with_v10_scenario
    ):
        response = auth_client.get("/simulate/run-tests/?page=1&limit=25&search=")

        assert response.status_code == status.HTTP_200_OK, response.content

        body = response.json()
        run_test = next(
            r for r in body["results"] if r["id"] == str(run_test_with_v10_scenario.id)
        )
        scenario = run_test["scenarios_detail"][0]
        assert scenario["prompt_version_detail"]["template_version"] == "v10"


@pytest.mark.integration
@pytest.mark.api
class TestRunTestRuntimeContracts:
    def test_list_rejects_unknown_query_param(self, auth_client):
        response = auth_client.get("/simulate/run-tests/?page=1&legacyPageSize=25")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacyPageSize"] == ["Unknown field."]

    def test_legacy_run_test_list_rejects_unknown_query_param(self, auth_client):
        response = auth_client.get("/simulate/api/run-tests/?page=1&legacyPageSize=25")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacyPageSize"] == ["Unknown field."]

    def test_create_accepts_declared_agent_version_field(
        self, auth_client, agent_definition, scenario_with_prompt_version
    ):
        agent_version = agent_definition.create_version(
            description="Runtime contract version",
            commit_message="Test version",
            status=AgentVersion.StatusChoices.ACTIVE,
        )

        response = auth_client.post(
            "/simulate/run-tests/create/",
            {
                "name": "Runtime Contract Run",
                "agent_definition_id": str(agent_definition.id),
                "agent_version": str(agent_version.id),
                "scenario_ids": [str(scenario_with_prompt_version.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.content
        run_test = RunTest.objects.get(id=response.json()["id"])
        assert run_test.agent_version_id == agent_version.id

    def test_create_rejects_unknown_body_field(
        self, auth_client, agent_definition, scenario_with_prompt_version
    ):
        response = auth_client.post(
            "/simulate/run-tests/create/",
            {
                "name": "Unknown Field Run",
                "agent_definition_id": str(agent_definition.id),
                "scenario_ids": [str(scenario_with_prompt_version.id)],
                "legacy_extra": True,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_update_rejects_unknown_body_field(
        self, auth_client, run_test_with_v10_scenario
    ):
        response = auth_client.patch(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/",
            {"name": "Updated", "legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_execute_rejects_unknown_body_field(
        self, auth_client, run_test_with_v10_scenario
    ):
        response = auth_client.post(
            f"/simulate/run-tests/{run_test_with_v10_scenario.id}/execute/",
            {"legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_call_execution_list_rejects_unknown_query_param(self, auth_client):
        response = auth_client.get("/simulate/api/call-executions/?legacyPageSize=25")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacyPageSize"] == ["Unknown field."]

    def test_test_execution_detail_rejects_unknown_query_param(self, auth_client):
        response = auth_client.get(f"/simulate/test-executions/{uuid4()}/?legacy=1")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy"] == ["Unknown field."]

    def test_call_execution_status_update_rejects_unknown_body_field(self, auth_client):
        response = auth_client.patch(
            f"/simulate/call-executions/{uuid4()}/",
            {"status": "failed", "legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]
