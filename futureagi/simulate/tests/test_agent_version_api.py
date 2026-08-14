"""
API integration tests for Agent Version endpoints.

Tests cover:
- AgentVersionListView: GET /simulate/agent-definitions/{id}/versions/
- CreateAgentVersionView: POST /simulate/agent-definitions/{id}/versions/create/
- AgentVersionDetailView: GET /simulate/agent-definitions/{id}/versions/{id}/
- ActivateAgentVersionView: POST /simulate/agent-definitions/{id}/versions/{id}/activate/
- DeleteAgentVersionView: DELETE /simulate/agent-definitions/{id}/versions/{id}/delete/
- RestoreAgentVersionView: POST /simulate/agent-definitions/{id}/versions/{id}/restore/
- AgentVersionEvalSummaryView: GET .../eval-summary/
- AgentVersionCallExecutionView: GET .../call-executions/
"""

import uuid

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from agentcc.services.credential_manager import mask_key
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    CallExecution,
    Scenarios,
    SimulateEvalConfig,
)
from simulate.models.agent_definition import ProviderCredentials
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import TestExecution as TestExecutionModel

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def agent_definition(db, organization, workspace):
    """Create a voice agent definition."""
    return AgentDefinition.objects.create(
        agent_name="Test Voice Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12345678901",
        inbound=True,
        description="Test voice agent",
        provider="vapi",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_version(db, agent_definition):
    """Create an active version."""
    return agent_definition.create_version(
        description="Initial version",
        commit_message="First version",
        status=AgentVersion.StatusChoices.ACTIVE,
    )


@pytest.fixture
def second_version(db, agent_definition, agent_version):
    """Create a second archived version."""
    return agent_definition.create_version(
        description="Second version",
        commit_message="Second version",
        status=AgentVersion.StatusChoices.ARCHIVED,
    )


def _url(agent_id, suffix=""):
    return f"/simulate/agent-definitions/{agent_id}/versions/{suffix}"


def _version_url(agent_id, version_id, suffix=""):
    return f"/simulate/agent-definitions/{agent_id}/versions/{version_id}/{suffix}"


# ============================================================================
# TestListAgentVersions
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestListAgentVersions:
    """Tests for GET /simulate/agent-definitions/{id}/versions/"""

    def test_list_success(self, auth_client, agent_definition, agent_version):
        response = auth_client.get(_url(agent_definition.id))
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "results" in data
        assert "count" in data
        assert data["count"] >= 1
        # Verify version fields present
        version_data = data["results"][0]
        assert "id" in version_data
        assert "version_number" in version_data
        assert "version_name_display" in version_data
        assert "is_active" in version_data
        assert "is_latest" in version_data

    def test_agent_not_found(self, auth_client):
        response = auth_client.get(_url(uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_pagination(
        self, auth_client, agent_definition, agent_version, second_version
    ):
        response = auth_client.get(_url(agent_definition.id) + "?page=1&limit=1")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["results"]) == 1
        assert data["count"] == 2

    def test_unauthenticated(self, api_client, agent_definition):
        response = api_client.get(_url(agent_definition.id))
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestCreateAgentVersion
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestCreateAgentVersion:
    """Tests for POST /simulate/agent-definitions/{id}/versions/create/"""

    def test_create_success(self, auth_client, agent_definition, agent_version):
        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {
                "commit_message": "Updated prompts",
                "description": "Better refund handling",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["message"] == "Agent version created successfully"
        assert "version" in data
        assert data["version"]["version_number"] == 2

    def test_create_archives_previous_active_version(
        self, auth_client, agent_definition, agent_version
    ):
        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {
                "commit_message": "Updated prompts",
                "description": "Only one version should stay active",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        agent_version.refresh_from_db()
        new_version_id = response.json()["version"]["id"]
        new_version = AgentVersion.objects.get(id=new_version_id)
        assert new_version.status == AgentVersion.StatusChoices.ACTIVE
        assert agent_version.status == AgentVersion.StatusChoices.ARCHIVED
        assert (
            AgentVersion.objects.filter(
                agent_definition=agent_definition,
                status=AgentVersion.StatusChoices.ACTIVE,
            ).count()
            == 1
        )

    def test_create_with_masked_api_key_preserves_existing_secret(
        self, auth_client, agent_definition, agent_version
    ):
        raw_api_key = "sk-version-preserve-secret-123456"
        agent_definition.provider = "vapi"
        agent_definition.api_key = raw_api_key
        agent_definition.assistant_id = "asst_version_masked"
        agent_definition.authentication_method = "api_key"
        agent_definition.save()
        ProviderCredentials.objects.create(
            agent_definition=agent_definition,
            provider_type=ProviderCredentials.ProviderType.VAPI,
            api_key=raw_api_key,
            assistant_id="asst_version_masked",
        )

        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {
                "commit_message": "Masked key roundtrip",
                "api_key": mask_key(raw_api_key),
                "description": "Update without rotating credentials",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        agent_definition.refresh_from_db()
        assert agent_definition.api_key == raw_api_key
        version = agent_definition.latest_version
        assert version.credentials.get_api_key() == raw_api_key
        serialized = response.json()
        assert raw_api_key not in str(serialized)
        assert serialized["version"]["api_key"] == mask_key(
            raw_api_key
        )

    def test_create_with_masked_api_key_preserves_direct_version_creds(
        self, auth_client, agent_definition, agent_version
    ):
        raw_api_key = "sk-version-direct-creds-789012"
        # Setup: create credentials directly on the version FK
        from simulate.services.agent_definition import sync_provider_credentials
        from simulate.services.types.agent_definition import ProviderCredentialsInput

        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="vapi",
                api_key=raw_api_key,
                assistant_id="asst_direct_version",
                provider_was_provided=True,
            ),
        )
        assert agent_version.credentials.get_api_key() == raw_api_key

        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {
                "commit_message": "Masked key from version creds",
                "api_key": mask_key(raw_api_key),
                "description": "Should resolve from active version creds",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        new_version = agent_definition.latest_version
        assert new_version.id != agent_version.id
        assert new_version.credentials.get_api_key() == raw_api_key
        serialized = response.json()
        assert raw_api_key not in str(serialized)
        assert serialized["version"]["api_key"] == mask_key(raw_api_key)

    def test_creates_snapshot(self, auth_client, agent_definition, agent_version):
        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {"commit_message": "Snapshot test"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        version_id = response.json()["version"]["id"]
        version = AgentVersion.objects.get(id=version_id)
        assert version.configuration_snapshot is not None
        assert isinstance(version.configuration_snapshot, dict)
        assert "agent_name" in version.configuration_snapshot

    def test_updates_agent_fields(self, auth_client, agent_definition, agent_version):
        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {
                "agent_name": "Renamed Agent",
                "commit_message": "Renamed",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        agent_definition.refresh_from_db()
        assert agent_definition.agent_name == "Renamed Agent"

    def test_agent_not_found(self, auth_client):
        response = auth_client.post(
            _url(uuid.uuid4(), "create/"),
            {"commit_message": "Test"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_invalid_data(self, auth_client, agent_definition, agent_version):
        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {"agent_name": "   "},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_unknown_body_field(
        self, auth_client, agent_definition, agent_version
    ):
        response = auth_client.post(
            _url(agent_definition.id, "create/"),
            {"commit_message": "Updated prompts", "legacy_extra": "ignore me"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert data["details"]["legacy_extra"] == ["Unknown field."]

    def test_unauthenticated(self, api_client, agent_definition):
        response = api_client.post(
            _url(agent_definition.id, "create/"),
            {"commit_message": "Test"},
            format="json",
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestGetAgentVersion
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestGetAgentVersion:
    """Tests for GET /simulate/agent-definitions/{id}/versions/{id}/"""

    def test_get_success(self, auth_client, agent_definition, agent_version):
        response = auth_client.get(_version_url(agent_definition.id, agent_version.id))
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == str(agent_version.id)
        assert "configuration_snapshot" in data
        assert isinstance(data["configuration_snapshot"], dict)

    def test_agent_not_found(self, auth_client, agent_version):
        response = auth_client.get(_version_url(uuid.uuid4(), agent_version.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_version_not_found(self, auth_client, agent_definition):
        response = auth_client.get(_version_url(agent_definition.id, uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_snapshot_uuids_are_strings(
        self, auth_client, agent_definition, agent_version
    ):
        response = auth_client.get(_version_url(agent_definition.id, agent_version.id))
        data = response.json()
        snapshot = data["configuration_snapshot"]
        for key, value in snapshot.items():
            if value is not None:
                assert isinstance(value, (str, int, float, bool, list, dict)), (
                    f"Snapshot key '{key}' has type {type(value)}"
                )

    def test_unauthenticated(self, api_client, agent_definition, agent_version):
        response = api_client.get(_version_url(agent_definition.id, agent_version.id))
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestActivateAgentVersion
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestActivateAgentVersion:
    """Tests for POST /simulate/agent-definitions/{id}/versions/{id}/activate/"""

    def test_activate_success(
        self, auth_client, agent_definition, agent_version, second_version
    ):
        response = auth_client.post(
            _version_url(agent_definition.id, second_version.id, "activate/")
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Agent version activated successfully"
        assert data["version"]["status"] == "active"

    def test_archives_other_versions(
        self, auth_client, agent_definition, agent_version, second_version
    ):
        auth_client.post(
            _version_url(agent_definition.id, second_version.id, "activate/")
        )
        agent_version.refresh_from_db()
        assert agent_version.status == AgentVersion.StatusChoices.ARCHIVED

    def test_agent_not_found(self, auth_client, agent_version):
        response = auth_client.post(
            _version_url(uuid.uuid4(), agent_version.id, "activate/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_version_not_found(self, auth_client, agent_definition):
        response = auth_client.post(
            _version_url(agent_definition.id, uuid.uuid4(), "activate/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, api_client, agent_definition, agent_version):
        response = api_client.post(
            _version_url(agent_definition.id, agent_version.id, "activate/")
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestDeleteAgentVersion
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestDeleteAgentVersion:
    """Tests for DELETE /simulate/agent-definitions/{id}/versions/{id}/delete/"""

    def test_delete_success(
        self, auth_client, agent_definition, agent_version, second_version
    ):
        # Delete the archived version (not the only active one)
        response = auth_client.delete(
            _version_url(agent_definition.id, second_version.id, "delete/")
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Agent version deleted successfully"

    def test_cannot_delete_only_active(
        self, auth_client, agent_definition, agent_version
    ):
        response = auth_client.delete(
            _version_url(agent_definition.id, agent_version.id, "delete/")
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Cannot delete the only active version" in response.json()["error"]

    def test_agent_not_found(self, auth_client, agent_version):
        response = auth_client.delete(
            _version_url(uuid.uuid4(), agent_version.id, "delete/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_version_not_found(self, auth_client, agent_definition):
        response = auth_client.delete(
            _version_url(agent_definition.id, uuid.uuid4(), "delete/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, api_client, agent_definition, agent_version):
        response = api_client.delete(
            _version_url(agent_definition.id, agent_version.id, "delete/")
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestRestoreAgentVersion
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestRestoreAgentVersion:
    """Tests for POST /simulate/agent-definitions/{id}/versions/{id}/restore/"""

    def test_restore_success(self, auth_client, agent_definition, agent_version):
        response = auth_client.post(
            _version_url(agent_definition.id, agent_version.id, "restore/")
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Agent definition restored successfully from version"
        assert "agent" in data
        assert "version" in data

    def test_restores_agent_fields(self, auth_client, agent_definition, agent_version):
        # Change agent name
        agent_definition.agent_name = "Changed Name"
        agent_definition.save()

        # Restore from version snapshot
        auth_client.post(
            _version_url(agent_definition.id, agent_version.id, "restore/")
        )

        agent_definition.refresh_from_db()
        assert agent_definition.agent_name == "Test Voice Agent"

    def test_agent_not_found(self, auth_client, agent_version):
        response = auth_client.post(
            _version_url(uuid.uuid4(), agent_version.id, "restore/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_version_not_found(self, auth_client, agent_definition):
        response = auth_client.post(
            _version_url(agent_definition.id, uuid.uuid4(), "restore/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# Populated-case fixtures (shared by eval-summary + call-executions tests)
# ============================================================================


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Version API Simulator",
        prompt="You simulate users.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def scenario(db, organization, workspace, agent_definition):
    return Scenarios.objects.create(
        name="Version API Scenario",
        source="stub",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, simulator_agent):
    return RunTest.objects.create(
        name="Version API Run Test",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def test_execution(db, run_test, simulator_agent, agent_definition, agent_version):
    return TestExecutionModel.objects.create(
        run_test=run_test,
        status=TestExecutionModel.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        agent_version=agent_version,
    )


@pytest.fixture
def pass_fail_template(db, organization):
    return EvalTemplate.objects.create(
        name="Version API Pass/Fail Template",
        config={"output": "Pass/Fail"},
        organization=organization,
        output_type_normalized="pass_fail",
    )


@pytest.fixture
def score_template(db, organization):
    return EvalTemplate.objects.create(
        name="Version API Score Template",
        config={"output": "score"},
        organization=organization,
        output_type_normalized="percentage",
    )


@pytest.fixture
def pass_fail_config(db, run_test, pass_fail_template):
    return SimulateEvalConfig.objects.create(
        name="Quality Gate",
        eval_template=pass_fail_template,
        run_test=run_test,
    )


@pytest.fixture
def score_config(db, run_test, score_template):
    return SimulateEvalConfig.objects.create(
        name="Accuracy Score",
        eval_template=score_template,
        run_test=run_test,
    )


# ============================================================================
# TestAgentVersionEvalSummary
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestAgentVersionEvalSummary:
    """Tests for GET .../eval-summary/"""

    def test_empty_eval_summary(self, auth_client, agent_definition, agent_version):
        response = auth_client.get(
            _version_url(agent_definition.id, agent_version.id, "eval-summary/")
        )
        assert response.status_code == status.HTTP_200_OK
        # Empty array when no eval configs exist
        assert response.json() == {"status": True, "result": []}

    def test_populated_eval_summary_returns_per_template_summary(
        self,
        auth_client,
        agent_definition,
        agent_version,
        scenario,
        test_execution,
        pass_fail_config,
        score_config,
    ):
        # Seed: 2 passed + 1 failed on pass/fail; scores 0.8/0.6/0.4 on score
        pf_id = str(pass_fail_config.id)
        score_id = str(score_config.id)
        seeded = [
            ("Passed", 0.8),
            ("Passed", 0.6),
            ("Failed", 0.4),
        ]
        for i, (verdict, score) in enumerate(seeded):
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                agent_version=agent_version,
                phone_number=f"+9500000{i:03d}",
                status="completed",
                eval_outputs={
                    pf_id: {
                        "name": "Quality Gate",
                        "output": verdict,
                        "output_type": "Pass/Fail",
                    },
                    score_id: {
                        "name": "Accuracy Score",
                        "output": score,
                        "output_type": "score",
                    },
                },
            )

        response = auth_client.get(
            _version_url(agent_definition.id, agent_version.id, "eval-summary/")
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["status"] is True
        summaries = {entry["name"]: entry for entry in body["result"]}

        pf_summary = summaries["Version API Pass/Fail Template"]
        assert pf_summary["output_type"] == "Pass/Fail"
        assert len(pf_summary["result"]) == 1
        pf_config_row = pf_summary["result"][0]
        assert pf_config_row["total_cells"] == 3
        assert pf_config_row["output"]["pass_count"] == 2
        assert pf_config_row["output"]["fail_count"] == 1

        score_summary = summaries["Version API Score Template"]
        assert score_summary["output_type"] == "score"
        assert len(score_summary["result"]) == 1
        score_config_row = score_summary["result"][0]
        assert score_config_row["total_cells"] == 3
        # (0.8 + 0.6 + 0.4) / 3 = 0.6 -> scaled x100 = 60.0
        assert score_config_row["avg_score"] == 60.0

    def test_populated_eval_summary_other_workspace_returns_404(
        self, auth_client, organization, user, agent_definition
    ):
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Eval Summary Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_agent = AgentDefinition.no_workspace_objects.create(
            agent_name="Hidden Agent",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+13334445555",
            inbound=True,
            description="Hidden agent",
            provider="vapi",
            organization=organization,
            workspace=other_workspace,
            languages=["en"],
        )
        hidden_version = AgentVersion.no_workspace_objects.create(
            agent_definition=hidden_agent,
            organization=organization,
            workspace=other_workspace,
            description="Hidden",
            commit_message="Hidden",
            status=AgentVersion.StatusChoices.ACTIVE,
        )

        response = auth_client.get(
            _version_url(hidden_agent.id, hidden_version.id, "eval-summary/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_populated_eval_summary_unknown_version_returns_404(
        self, auth_client, agent_definition
    ):
        response = auth_client.get(
            _version_url(agent_definition.id, uuid.uuid4(), "eval-summary/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, api_client, agent_definition, agent_version):
        response = api_client.get(
            _version_url(agent_definition.id, agent_version.id, "eval-summary/")
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


# ============================================================================
# TestAgentVersionCallExecutions
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestAgentVersionCallExecutions:
    """Tests for GET .../call-executions/"""

    def test_empty_call_executions(self, auth_client, agent_definition, agent_version):
        response = auth_client.get(
            _version_url(agent_definition.id, agent_version.id, "call-executions/")
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "results" in data
        assert data["count"] == 0

    def test_populated_call_executions_returns_full_list(
        self,
        auth_client,
        agent_definition,
        agent_version,
        scenario,
        test_execution,
        pass_fail_config,
    ):
        # Seed 3 completed calls with distinct phone numbers + non-empty eval_outputs
        pf_id = str(pass_fail_config.id)
        seeded = []
        for i, verdict in enumerate(["Passed", "Failed", "Passed"]):
            call = CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                agent_version=agent_version,
                phone_number=f"+9600000{i:03d}",
                status="completed",
                eval_outputs={
                    pf_id: {
                        "name": "Quality Gate",
                        "output": verdict,
                        "output_type": "Pass/Fail",
                    }
                },
            )
            seeded.append(call)

        response = auth_client.get(
            _version_url(agent_definition.id, agent_version.id, "call-executions/")
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 3
        assert len(data["results"]) == 3

        returned_by_id = {row["id"]: row for row in data["results"]}
        for call in seeded:
            row = returned_by_id[str(call.id)]
            assert row["status"] == "completed"
            assert row["phone_number"] == call.phone_number
            assert row["eval_outputs"] == call.eval_outputs

    def test_populated_call_executions_other_workspace_returns_404(
        self, auth_client, organization, user
    ):
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Call Exec Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_agent = AgentDefinition.no_workspace_objects.create(
            agent_name="Hidden Agent CE",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+14445556666",
            inbound=True,
            description="Hidden agent",
            provider="vapi",
            organization=organization,
            workspace=other_workspace,
            languages=["en"],
        )
        hidden_version = AgentVersion.no_workspace_objects.create(
            agent_definition=hidden_agent,
            organization=organization,
            workspace=other_workspace,
            description="Hidden",
            commit_message="Hidden",
            status=AgentVersion.StatusChoices.ACTIVE,
        )

        response = auth_client.get(
            _version_url(hidden_agent.id, hidden_version.id, "call-executions/")
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_populated_call_executions_pagination(
        self,
        auth_client,
        agent_definition,
        agent_version,
        scenario,
        test_execution,
        pass_fail_config,
    ):
        pf_id = str(pass_fail_config.id)
        for i in range(15):
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                agent_version=agent_version,
                phone_number=f"+9700000{i:03d}",
                status="completed",
                eval_outputs={
                    pf_id: {
                        "name": "Quality Gate",
                        "output": "Passed",
                        "output_type": "Pass/Fail",
                    }
                },
            )

        response = auth_client.get(
            _version_url(agent_definition.id, agent_version.id, "call-executions/")
            + "?limit=5"
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["results"]) == 5
        assert data["count"] >= 15
        # 15 rows / 5 per page = 3 pages
        assert data["total_pages"] == 3

    def test_unauthenticated(self, api_client, agent_definition, agent_version):
        response = api_client.get(
            _version_url(agent_definition.id, agent_version.id, "call-executions/")
        )
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]
