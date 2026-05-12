"""
End-to-end tests for prompt, tracing, experiment, simulation, and user tools
via the /mcp/internal/tool-call/ HTTP POST endpoint.

Each test creates real DB entries, invokes tools through the MCP HTTP layer,
and verifies both the HTTP response and the underlying database state.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from mcp_server.models.usage import MCPUsageRecord
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models.agent_definition import AgentDefinition
from simulate.models.persona import Persona
from simulate.models.simulator_agent import SimulatorAgent
from tracer.models.project import Project
from tracer.models.trace import Trace

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOOL_CALL_URL = "/mcp/internal/tool-call/"

SAMPLE_PROMPT_CONFIG = [
    {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello {{name}}"},
        ],
        "model": "gpt-4o",
        "configuration": {"temperature": 0.7, "max_tokens": 1024},
    }
]

SAMPLE_PROMPT_CONFIG_V2 = [
    {
        "messages": [
            {"role": "system", "content": "You are a concise assistant."},
            {"role": "user", "content": "Summarise {{text}}"},
        ],
        "model": "gpt-4o-mini",
        "configuration": {"temperature": 0.3, "max_tokens": 512},
    }
]


def call_tool(auth_client, tool_name, params=None):
    """Shortcut to POST a tool-call and return the response."""
    return auth_client.post(
        TOOL_CALL_URL,
        {"tool_name": tool_name, "params": params or {}},
        format="json",
    )


def assert_success(response, msg=""):
    """Assert 200 + status=True."""
    assert response.status_code == 200, (
        f"Expected 200 but got {response.status_code}. {msg} "
        f"Body: {getattr(response, 'data', '')}"
    )
    assert (
        response.data["status"] is True
    ), f"Expected status=True. {msg} Body: {response.data}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_resource_limit():
    with patch(
        "model_hub.services.dataset_service._check_resource_limit",
        return_value=True,
    ):
        yield


@pytest.fixture
def mock_temporal():
    mock_client = MagicMock()
    mock_client.start_workflow = MagicMock(return_value="fake-workflow-id")
    with patch("tfc.temporal.get_client", return_value=mock_client):
        yield mock_client


# ---------------------------------------------------------------------------
# 1. Prompt E2E Workflow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPromptE2EWorkflow:
    """Full CRUD lifecycle for prompt templates and versions via MCP endpoint."""

    def test_create_prompt_template_and_verify_db(self, auth_client, user, workspace):
        """Create a prompt template and verify it exists in the database."""
        response = call_tool(
            auth_client,
            "create_prompt_template",
            {
                "name": "E2E Test Template",
                "description": "Created by E2E test",
            },
        )
        assert_success(response, "create_prompt_template")

        data = response.data["result"]["data"]
        template_id = data["template_id"]

        # Verify DB
        template = PromptTemplate.all_objects.get(id=template_id)
        assert template.name == "E2E Test Template", "Template name should match"
        assert template.organization == user.organization, "Template org should match"
        assert template.deleted is False, "Template should not be deleted"

        # A default v1 version should have been created
        versions = PromptVersion.all_objects.filter(original_template=template)
        assert versions.count() >= 1, "At least one version should exist"

    def test_create_prompt_template_with_config(self, auth_client, user, workspace):
        """Create template with explicit prompt_config and verify snapshot in DB."""
        response = call_tool(
            auth_client,
            "create_prompt_template",
            {
                "name": "Config Template",
                "prompt_config": SAMPLE_PROMPT_CONFIG,
            },
        )
        assert_success(response, "create_prompt_template with config")

        data = response.data["result"]["data"]
        version_id = data["version_id"]

        version = PromptVersion.all_objects.get(id=version_id)
        assert (
            version.prompt_config_snapshot is not None
        ), "prompt_config_snapshot should be stored"
        snapshot = version.prompt_config_snapshot
        # Snapshot is stored as a dict with configuration/messages keys
        assert isinstance(snapshot, (list, dict)), "Snapshot should be list or dict"
        if isinstance(snapshot, list):
            assert snapshot[0]["model"] == "gpt-4o", "Model in snapshot should match"
        else:
            cfg = snapshot.get("configuration", {})
            assert cfg.get("model") == "gpt-4o", "Model in snapshot should match"

    def test_list_prompt_templates(self, auth_client, user, workspace):
        """Create two templates, list them, verify count and names."""
        call_tool(auth_client, "create_prompt_template", {"name": "List Test A"})
        call_tool(auth_client, "create_prompt_template", {"name": "List Test B"})

        response = call_tool(auth_client, "list_prompt_templates", {})
        assert_success(response, "list_prompt_templates")

        data = response.data["result"]["data"]
        assert data["total"] >= 2, "Should have at least 2 templates"
        names = [t["name"] for t in data["templates"]]
        assert "List Test A" in names, "Template A should appear"
        assert "List Test B" in names, "Template B should appear"

    def test_get_prompt_template(self, auth_client, user, workspace):
        """Create a template, get it by ID, verify response fields."""
        create_resp = call_tool(
            auth_client,
            "create_prompt_template",
            {
                "name": "Get Test",
                "description": "Get test desc",
            },
        )
        template_id = create_resp.data["result"]["data"]["template_id"]

        response = call_tool(
            auth_client,
            "get_prompt_template",
            {
                "template_id": template_id,
            },
        )
        assert_success(response, "get_prompt_template")

        data = response.data["result"]["data"]
        assert data["id"] == template_id, "ID should match"
        assert data["name"] == "Get Test", "Name should match"
        assert data["description"] == "Get test desc", "Description should match"
        assert "default_version" in data, "Should include defaultVersion info"

    def test_create_prompt_version(self, auth_client, user, workspace):
        """Create template, then add a new version, verify it is v2 in DB."""
        create_resp = call_tool(
            auth_client,
            "create_prompt_template",
            {
                "name": "Version Test",
                "prompt_config": SAMPLE_PROMPT_CONFIG,
            },
        )
        template_id = create_resp.data["result"]["data"]["template_id"]

        response = call_tool(
            auth_client,
            "create_prompt_version",
            {
                "template_id": template_id,
                "prompt_config": SAMPLE_PROMPT_CONFIG_V2,
                "commit_message": "Updated to v2",
            },
        )
        assert_success(response, "create_prompt_version")

        data = response.data["result"]["data"]
        assert data["version"] == "v2", "New version should be v2"

        # Verify in DB
        version = PromptVersion.all_objects.get(id=data["version_id"])
        assert version.template_version == "v2", "DB version string should be v2"
        assert version.original_template_id == uuid.UUID(
            template_id
        ), "Version should reference correct template"

    def test_list_prompt_versions(self, auth_client, user, workspace):
        """Create template and extra version, list versions, verify all returned."""
        create_resp = call_tool(
            auth_client,
            "create_prompt_template",
            {
                "name": "List Versions Test",
                "prompt_config": SAMPLE_PROMPT_CONFIG,
            },
        )
        template_id = create_resp.data["result"]["data"]["template_id"]

        # Create second version
        call_tool(
            auth_client,
            "create_prompt_version",
            {
                "template_id": template_id,
                "prompt_config": SAMPLE_PROMPT_CONFIG_V2,
            },
        )

        response = call_tool(
            auth_client,
            "list_prompt_versions",
            {
                "template_id": template_id,
            },
        )
        assert_success(response, "list_prompt_versions")

        data = response.data["result"]["data"]
        assert data["total"] >= 2, "Should have at least 2 versions"
        version_names = [v["version"] for v in data["versions"]]
        assert "v1" in version_names, "v1 should be listed"
        assert "v2" in version_names, "v2 should be listed"

    def test_update_prompt_template(self, auth_client, user, workspace):
        """Create template, update its name, verify DB change."""
        create_resp = call_tool(
            auth_client,
            "create_prompt_template",
            {
                "name": "Update Test Original",
            },
        )
        template_id = create_resp.data["result"]["data"]["template_id"]

        response = call_tool(
            auth_client,
            "update_prompt_template",
            {
                "template_id": template_id,
                "name": "Update Test Renamed",
            },
        )
        assert_success(response, "update_prompt_template")

        # Verify DB
        template = PromptTemplate.all_objects.get(id=template_id)
        assert template.name == "Update Test Renamed", "Name should be updated in DB"

    def test_delete_prompt_template(self, auth_client, user, workspace):
        """Create template, delete it, verify soft-delete in DB."""
        create_resp = call_tool(
            auth_client,
            "create_prompt_template",
            {
                "name": "Delete Test",
            },
        )
        template_id = create_resp.data["result"]["data"]["template_id"]

        response = call_tool(
            auth_client,
            "delete_prompt_template",
            {
                "template_id": template_id,
            },
        )
        assert_success(response, "delete_prompt_template")

        # Verify soft-delete
        template = PromptTemplate.all_objects.get(id=template_id)
        assert template.deleted is True, "Template should be soft-deleted"
        assert template.deleted_at is not None, "deleted_at should be set"

        # Versions should also be soft-deleted
        versions = PromptVersion.all_objects.filter(original_template_id=template_id)
        for v in versions:
            assert v.deleted is True, f"Version {v.template_version} should be deleted"

    def test_duplicate_name_error(self, auth_client, user, workspace):
        """Creating two templates with the same name should error."""
        call_tool(auth_client, "create_prompt_template", {"name": "Dupe Name"})
        response = call_tool(
            auth_client, "create_prompt_template", {"name": "Dupe Name"}
        )

        # Should return 200 but with error status
        assert response.status_code == 200, "Endpoint should still return 200"
        assert (
            response.data["status"] is False
        ), "Status should be False for duplicate name"

    def test_full_prompt_lifecycle(self, auth_client, user, workspace):
        """Complete lifecycle: create -> version -> commit -> list -> update -> delete."""
        # 1. Create
        resp = call_tool(
            auth_client,
            "create_prompt_template",
            {
                "name": "Lifecycle Test",
                "prompt_config": SAMPLE_PROMPT_CONFIG,
            },
        )
        assert_success(resp, "lifecycle create")
        template_id = resp.data["result"]["data"]["template_id"]

        # 2. Create version v2
        resp = call_tool(
            auth_client,
            "create_prompt_version",
            {
                "template_id": template_id,
                "prompt_config": SAMPLE_PROMPT_CONFIG_V2,
                "commit_message": "v2 changes",
            },
        )
        assert_success(resp, "lifecycle create_version")

        # 3. Commit v1
        resp = call_tool(
            auth_client,
            "commit_prompt_version",
            {
                "template_id": template_id,
                "version_name": "v1",
                "message": "Committing v1",
                "set_default": True,
            },
        )
        assert_success(resp, "lifecycle commit")
        assert resp.data["result"]["data"]["is_default"] is True

        # 4. List versions
        resp = call_tool(
            auth_client,
            "list_prompt_versions",
            {
                "template_id": template_id,
            },
        )
        assert_success(resp, "lifecycle list_versions")
        assert resp.data["result"]["data"]["total"] >= 2

        # 5. Update template name
        resp = call_tool(
            auth_client,
            "update_prompt_template",
            {
                "template_id": template_id,
                "name": "Lifecycle Renamed",
            },
        )
        assert_success(resp, "lifecycle update")
        assert (
            PromptTemplate.all_objects.get(id=template_id).name == "Lifecycle Renamed"
        )

        # 6. Delete
        resp = call_tool(
            auth_client,
            "delete_prompt_template",
            {
                "template_id": template_id,
            },
        )
        assert_success(resp, "lifecycle delete")
        assert PromptTemplate.all_objects.get(id=template_id).deleted is True

    def test_create_prompt_template_records_usage(self, auth_client, user, workspace):
        """Verify that a tool call creates an MCPUsageRecord."""
        call_tool(auth_client, "create_prompt_template", {"name": "Usage Record Test"})

        records = MCPUsageRecord.objects.filter(tool_name="create_prompt_template")
        assert records.exists(), "Usage record should be created"
        assert records.first().response_status == "success"


# ---------------------------------------------------------------------------
# 2. Tracing E2E Workflow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTracingE2EWorkflow:
    """CRUD lifecycle for tracing projects, traces, and monitors via MCP endpoint."""

    def test_create_project_and_verify_db(self, auth_client, user, workspace):
        """Create a tracing project, verify it in the database."""
        response = call_tool(
            auth_client,
            "create_project",
            {
                "name": "E2E Trace Project",
                "trace_type": "observe",
                "model_type": "GenerativeLLM",
            },
        )
        assert_success(response, "create_project")

        data = response.data["result"]["data"]
        project_id = data["project_id"]

        project = Project.all_objects.get(id=project_id)
        assert project.name == "E2E Trace Project", "Project name should match"
        assert project.trace_type == "observe", "trace_type should match"
        assert project.organization == user.organization, "Org should match"

    def test_list_projects(self, auth_client, user, workspace):
        """Create a project, list projects, verify it appears."""
        call_tool(
            auth_client,
            "create_project",
            {
                "name": "Listed Project",
                "trace_type": "observe",
                "model_type": "GenerativeLLM",
            },
        )

        response = call_tool(auth_client, "list_projects", {})
        assert_success(response, "list_projects")

        data = response.data["result"]["data"]
        assert data["total"] >= 1, "Should have at least 1 project"
        names = [p["name"] for p in data["projects"]]
        assert "Listed Project" in names, "Created project should be listed"

    def test_search_traces_empty(self, auth_client, user, workspace):
        """Search traces when none exist, verify empty result."""
        response = call_tool(auth_client, "search_traces", {})
        assert_success(response, "search_traces empty")

        data = response.data["result"]["data"]
        assert data["total"] == 0, "Should have 0 traces"
        assert data["traces"] == [], "Traces list should be empty"

    def test_search_traces_with_data(self, auth_client, user, workspace):
        """Create project + trace via ORM, search via tool, verify found."""
        project = Project(
            name="Trace Search Project",
            trace_type="observe",
            model_type="GenerativeLLM",
            organization=user.organization,
            workspace=workspace,
            user=user,
        )
        project.save()

        trace = Trace(
            name="test-trace-e2e",
            project=project,
            input={"query": "hello"},
            output={"response": "world"},
            tags=["e2e", "test"],
        )
        trace.save()

        response = call_tool(
            auth_client,
            "search_traces",
            {
                "project_id": str(project.id),
                "name": "test-trace",
            },
        )
        assert_success(response, "search_traces with data")

        data = response.data["result"]["data"]
        assert data["total"] >= 1, "Should find at least 1 trace"
        trace_ids = [t["id"] for t in data["traces"]]
        assert str(trace.id) in trace_ids, "Created trace should be found"

    def test_get_trace(self, auth_client, user, workspace):
        """Create trace via ORM, get via tool, verify fields."""
        project = Project(
            name="Get Trace Project",
            trace_type="observe",
            model_type="GenerativeLLM",
            organization=user.organization,
            workspace=workspace,
            user=user,
        )
        project.save()

        trace = Trace(
            name="get-trace-test",
            project=project,
            input={"q": "test input"},
            output={"a": "test output"},
            tags=["e2e"],
        )
        trace.save()

        response = call_tool(
            auth_client,
            "get_trace",
            {
                "trace_id": str(trace.id),
            },
        )
        assert_success(response, "get_trace")

        data = response.data["result"]["data"]
        assert data["id"] == str(trace.id), "Trace ID should match"
        assert data["name"] == "get-trace-test", "Trace name should match"
        assert data["project"] == "Get Trace Project", "Project name should match"
        assert data["has_error"] is False, "Should have no error"
        assert "e2e" in data["tags"], "Tags should contain 'e2e'"

    def test_update_project(self, auth_client, user, workspace):
        """Create project, update name, verify DB change."""
        resp = call_tool(
            auth_client,
            "create_project",
            {
                "name": "Update Project Original",
                "trace_type": "observe",
                "model_type": "GenerativeLLM",
            },
        )
        project_id = resp.data["result"]["data"]["project_id"]

        response = call_tool(
            auth_client,
            "update_project",
            {
                "project_id": project_id,
                "name": "Update Project Renamed",
            },
        )
        assert_success(response, "update_project")

        project = Project.all_objects.get(id=project_id)
        assert project.name == "Update Project Renamed", "Name should be updated"

    def test_delete_project(self, auth_client, user, workspace):
        """Create project, delete it, verify it is removed."""
        resp = call_tool(
            auth_client,
            "create_project",
            {
                "name": "Delete Project Test",
                "trace_type": "observe",
                "model_type": "GenerativeLLM",
            },
        )
        project_id = resp.data["result"]["data"]["project_id"]

        response = call_tool(
            auth_client,
            "delete_project",
            {
                "project_id": project_id,
            },
        )
        assert_success(response, "delete_project")
        assert response.data["result"]["data"]["deleted"] is True

        # The project.delete() call may be a hard or soft delete depending
        # on the model's delete method. Verify it is gone from normal queryset.
        assert not Project.objects.filter(
            id=project_id
        ).exists(), "Project should not appear in default queryset after delete"

    def test_create_alert_monitor(self, auth_client, user, workspace):
        """Create an alert monitor, verify it in DB."""
        from tracer.models.monitor import UserAlertMonitor

        response = call_tool(
            auth_client,
            "create_alert_monitor",
            {
                "name": "E2E Error Monitor",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "critical_threshold_value": 10.0,
                "alert_frequency": 60,
            },
        )
        assert_success(response, "create_alert_monitor")

        data = response.data["result"]["data"]
        monitor_id = data["monitor_id"]

        monitor = UserAlertMonitor.objects.get(id=monitor_id)
        assert monitor.name == "E2E Error Monitor", "Monitor name should match"
        assert monitor.metric_type == "count_of_errors", "Metric type should match"
        assert (
            float(monitor.critical_threshold_value) == 10.0
        ), "Critical threshold should match"

    def test_trace_analytics(self, auth_client, user, workspace):
        """Call get_trace_analytics, verify response structure."""
        response = call_tool(
            auth_client,
            "get_trace_analytics",
            {
                "time_range": "24h",
            },
        )
        assert_success(response, "get_trace_analytics")

        data = response.data["result"]["data"]
        assert "total_traces" in data, "Should contain totalTraces"
        assert "error_rate" in data, "Should contain errorRate"
        assert "total_tokens" in data, "Should contain totalTokens"
        assert "avg_latency_ms" in data, "Should contain avgLatencyMs"
        assert data["time_range"] == "24h", "timeRange should echo back"

    def test_list_alert_monitors(self, auth_client, user, workspace):
        """Create a monitor, list monitors, verify it appears."""
        from tracer.models.monitor import UserAlertMonitor

        call_tool(
            auth_client,
            "create_alert_monitor",
            {
                "name": "List Monitor Test",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "warning_threshold_value": 5.0,
            },
        )

        response = call_tool(auth_client, "list_alert_monitors", {})
        assert_success(response, "list_alert_monitors")

        data = response.data["result"]["data"]
        assert data["total"] >= 1, "Should have at least 1 monitor"
        names = [m["name"] for m in data["monitors"]]
        assert "List Monitor Test" in names, "Created monitor should be listed"


# ---------------------------------------------------------------------------
# 3. Experiment E2E Workflow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExperimentE2EWorkflow:
    """Experiment tools via MCP endpoint."""

    def test_list_experiments_empty(self, auth_client, user, workspace):
        """List experiments when none exist, verify count=0."""
        response = call_tool(auth_client, "list_experiments", {})
        assert_success(response, "list_experiments empty")

        data = response.data["result"]["data"]
        assert data["total"] == 0, "Should have 0 experiments"
        assert data["experiments"] == [], "Experiments list should be empty"

    def test_create_experiment(
        self, auth_client, user, workspace, mock_temporal, mock_resource_limit
    ):
        """Create dataset + experiment (needs temporal mock), verify response."""
        from model_hub.models.develop_dataset import Column, Dataset

        # Create a dataset with an input column (required for experiments)
        dataset = Dataset(
            name="Experiment Dataset",
            organization=user.organization,
            workspace=workspace,
            user=user,
        )
        dataset.save()
        Column.objects.create(
            dataset=dataset,
            name="input",
            data_type="text",
            source="OTHERS",
        )

        # The create_experiment tool may not exist yet; if so, verify listing
        # shows data when we create an experiment via ORM.
        from model_hub.models.experiments import ExperimentsTable

        experiment_prompt_config = [
            {
                "name": "E2E Config",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Hello {{name}}"},
                ],
                "model": ["gpt-4o"],
                "configuration": {"temperature": 0.7, "max_tokens": 1024},
            }
        ]
        experiment = ExperimentsTable(
            name="E2E Experiment",
            dataset=dataset,
            status="NotStarted",
            prompt_config=experiment_prompt_config,
        )
        experiment.save()

        response = call_tool(auth_client, "list_experiments", {})
        assert_success(response, "list_experiments after create")

        data = response.data["result"]["data"]
        assert data["total"] >= 1, "Should have at least 1 experiment"
        names = [e["name"] for e in data["experiments"]]
        assert "E2E Experiment" in names, "Created experiment should appear"

    def test_get_experiment_not_found(self, auth_client, user, workspace):
        """Get experiment with fake ID, verify error."""
        fake_id = str(uuid.uuid4())
        response = call_tool(
            auth_client,
            "get_experiment_results",
            {
                "experiment_id": fake_id,
            },
        )
        # Should return 200 but with error status
        assert response.status_code == 200, "Endpoint should return 200"
        assert response.data["status"] is False, "Status should be False for not found"


# ---------------------------------------------------------------------------
# 4. Simulation E2E Workflow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSimulationE2EWorkflow:
    """CRUD lifecycle for simulation entities via MCP endpoint."""

    def test_create_agent_definition(self, auth_client, user, workspace):
        """Create agent definition, verify AgentDefinition in DB."""
        response = call_tool(
            auth_client,
            "create_agent_definition",
            {
                "agent_name": "E2E Agent",
                "agent_type": "voice",
                "provider": "vapi",
                "model": "gpt-4o",
                "description": "Test agent for E2E",
                "language": "en",
                "contactNumber": "+15555550100",
            },
        )
        assert_success(response, "create_agent_definition")

        data = response.data["result"]["data"]
        agent_id = data["id"]

        agent = AgentDefinition.all_objects.get(id=agent_id)
        assert agent.agent_name == "E2E Agent", "Agent name should match"
        assert agent.agent_type == "voice", "Agent type should match"
        assert agent.provider == "vapi", "Provider should match"
        assert agent.organization == user.organization, "Org should match"
        assert agent.workspace == workspace, "Workspace should match"
        assert agent.deleted is False, "Agent should not be deleted"

    def test_list_agent_definitions(self, auth_client, user, workspace):
        """Create agent, list agents, verify it appears."""
        call_tool(
            auth_client,
            "create_agent_definition",
            {
                "agent_name": "Listed Agent",
                "agent_type": "text",
                "language": "en",
            },
        )

        response = call_tool(auth_client, "list_agents", {})
        assert_success(response, "list_agents")

        data = response.data["result"]["data"]
        assert data["total"] >= 1, "Should have at least 1 agent"
        names = [a["name"] for a in data["agents"]]
        assert "Listed Agent" in names, "Created agent should be listed"

    def test_create_persona(self, auth_client, user, workspace):
        """Create persona, verify Persona in DB."""
        response = call_tool(
            auth_client,
            "create_persona",
            {
                "name": "E2E Persona",
                "description": "Test persona",
                "simulation_type": "voice",
                "personality": ["Friendly and cooperative"],
                "tone": "casual",
                "verbosity": "balanced",
            },
        )
        assert_success(response, "create_persona")

        data = response.data["result"]["data"]
        persona_id = data["id"]

        persona = Persona.all_objects.get(id=persona_id)
        assert persona.name == "E2E Persona", "Persona name should match"
        assert persona.simulation_type == "voice", "Simulation type should match"
        assert persona.persona_type == "workspace", "Type should be workspace"
        assert persona.tone == "casual", "Tone should match"
        assert persona.deleted is False, "Persona should not be deleted"

    def test_list_personas(self, auth_client, user, workspace):
        """Create persona, list, verify in response."""
        call_tool(
            auth_client,
            "create_persona",
            {
                "name": "Listed Persona",
                "description": "Listed persona description",
                "simulation_type": "text",
            },
        )

        response = call_tool(
            auth_client,
            "list_personas",
            {
                "persona_type": "workspace",
            },
        )
        assert_success(response, "list_personas")

        data = response.data["result"]["data"]
        assert data["total"] >= 1, "Should have at least 1 persona"
        names = [p["name"] for p in data["personas"]]
        assert "Listed Persona" in names, "Created persona should be listed"

    def test_create_simulator_agent(self, auth_client, user, workspace):
        """Create simulator agent, verify SimulatorAgent in DB."""
        response = call_tool(
            auth_client,
            "create_simulator_agent",
            {
                "name": "E2E Sim Agent",
                "prompt": "You are a test caller.",
                "model": "gpt-4o",
                "voice_provider": "openai",
                "voice_name": "alloy",
                "llm_temperature": 0.5,
            },
        )
        assert_success(response, "create_simulator_agent")

        data = response.data["result"]["data"]
        sa_id = data["id"]

        sa = SimulatorAgent.all_objects.get(id=sa_id)
        assert sa.name == "E2E Sim Agent", "Simulator agent name should match"
        assert sa.model == "gpt-4o", "Model should match"
        assert sa.voice_provider == "openai", "Voice provider should match"
        assert sa.voice_name == "alloy", "Voice name should match"
        assert float(sa.llm_temperature) == 0.5, "Temperature should match"

    def test_update_agent_definition(self, auth_client, user, workspace):
        """Create agent, update name, verify DB change."""
        resp = call_tool(
            auth_client,
            "create_agent_definition",
            {
                "agent_name": "Update Agent Original",
                "agent_type": "text",
                "language": "en",
            },
        )
        agent_id = resp.data["result"]["data"]["id"]

        response = call_tool(
            auth_client,
            "update_agent_definition",
            {
                "agent_id": agent_id,
                "agent_name": "Update Agent Renamed",
                "description": "Updated description",
            },
        )
        assert_success(response, "update_agent_definition")

        agent = AgentDefinition.all_objects.get(id=agent_id)
        assert agent.agent_name == "Update Agent Renamed", "Name should be updated"
        assert (
            agent.description == "Updated description"
        ), "Description should be updated"

    def test_delete_agent_definition(self, auth_client, user, workspace):
        """Create agent, delete, verify soft-delete in DB."""
        resp = call_tool(
            auth_client,
            "create_agent_definition",
            {
                "agent_name": "Delete Agent Test",
                "agent_type": "text",
                "language": "en",
            },
        )
        agent_id = resp.data["result"]["data"]["id"]

        response = call_tool(
            auth_client,
            "delete_agent_definition",
            {
                "agent_id": agent_id,
            },
        )
        assert_success(response, "delete_agent_definition")
        assert response.data["result"]["data"]["deleted"] is True

        agent = AgentDefinition.all_objects.get(id=agent_id)
        assert agent.deleted is True, "Agent should be soft-deleted"
        assert agent.deleted_at is not None, "deleted_at should be set"

    def test_duplicate_persona_name_error(self, auth_client, user, workspace):
        """Creating two personas with the same name should error."""
        call_tool(
            auth_client,
            "create_persona",
            {
                "name": "Dupe Persona",
                "simulation_type": "voice",
            },
        )
        response = call_tool(
            auth_client,
            "create_persona",
            {
                "name": "Dupe Persona",
                "simulation_type": "voice",
            },
        )
        assert response.status_code == 200, "Endpoint should return 200"
        assert (
            response.data["status"] is False
        ), "Status should be False for duplicate persona name"


# ---------------------------------------------------------------------------
# 5. User E2E Workflow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserE2EWorkflow:
    """User and workspace context tools via MCP endpoint."""

    def test_whoami(self, auth_client, user, workspace):
        """Call whoami, verify user email in response."""
        response = call_tool(auth_client, "whoami", {})
        assert_success(response, "whoami")

        content = response.data["result"]["content"]
        assert user.email in content, "User email should appear in whoami content"

        data = response.data["result"]["data"]
        assert data["user_email"] == user.email, "Data should contain user email"
        assert (
            data["workspace_name"] == workspace.name
        ), "Data should contain workspace name"

    def test_list_workspaces(self, auth_client, user, workspace):
        """Call list_workspaces, verify response."""
        response = call_tool(auth_client, "list_workspaces", {})
        assert_success(response, "list_workspaces")

        data = response.data["result"]["data"]
        assert len(data["workspaces"]) >= 1, "Should have at least 1 workspace"
        ws_names = [ws["name"] for ws in data["workspaces"]]
        assert workspace.name in ws_names, "Current workspace should be listed"

    def test_list_users(self, auth_client, user, workspace):
        """Call list_users, verify current user in list."""
        response = call_tool(auth_client, "list_users", {})
        assert_success(response, "list_users")

        data = response.data["result"]["data"]
        assert data["total"] >= 1, "Should have at least 1 user"
        emails = [u["email"] for u in data["users"]]
        assert user.email in emails, "Current user email should be in list"

    def test_get_user_permissions(self, auth_client, user, workspace):
        """Call get_user_permissions, verify response structure."""
        response = call_tool(auth_client, "get_user_permissions", {})
        assert_success(response, "get_user_permissions")

        data = response.data["result"]["data"]
        assert "can_read" in data, "Should have canRead field"
        assert "can_write" in data, "Should have canWrite field"
        assert "can_access" in data, "Should have canAccess field"
        assert data["user_email"] == user.email, "User email should match"
        assert data["workspace_id"] == str(workspace.id), "Workspace ID should match"

    def test_create_workspace(self, auth_client, user, workspace):
        """Create workspace, verify in DB."""
        from accounts.models.workspace import Workspace

        response = call_tool(
            auth_client,
            "create_workspace",
            {
                "name": "E2E New Workspace",
                "description": "Created by E2E test",
            },
        )
        assert_success(response, "create_workspace")

        data = response.data["result"]["data"]
        ws_id = data["workspace_id"]

        ws = Workspace.objects.get(id=ws_id)
        assert ws.name == "E2E New Workspace", "Workspace name should match"
        assert ws.organization == user.organization, "Org should match"
        assert ws.is_active is True, "Workspace should be active"

    def test_list_organizations(self, auth_client, user, workspace):
        """Call list_organizations, verify org in response."""
        response = call_tool(auth_client, "list_organizations", {})
        assert_success(response, "list_organizations")

        data = response.data["result"]["data"]
        assert data["total"] >= 1, "Should have at least 1 organization"
        org_names = [o["name"] for o in data["organizations"]]
        assert user.organization.name in org_names, "User's org should be in the list"


# ---------------------------------------------------------------------------
# 6. Cross-cutting concerns
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCrossCuttingE2E:
    """Tests for session management and usage tracking across tool domains."""

    def test_session_reuse_across_tools(self, auth_client, user, workspace):
        """Verify that passing session_id reuses the same session across tools."""
        from mcp_server.models.session import MCPSession

        # First call to get a session
        resp1 = call_tool(auth_client, "whoami", {})
        session_id = resp1.data["session_id"]

        # Second call with same session, different tool
        resp2 = auth_client.post(
            TOOL_CALL_URL,
            {
                "tool_name": "list_projects",
                "params": {},
                "session_id": session_id,
            },
            format="json",
        )
        assert (
            resp2.data["session_id"] == session_id
        ), "Session should be reused across different tools"

        session = MCPSession.objects.get(id=session_id)
        assert session.tool_call_count == 2, "Session should track 2 calls"

    def test_usage_records_across_domains(self, auth_client, user, workspace):
        """Verify usage records are created for tools in different domains."""
        call_tool(auth_client, "whoami", {})
        call_tool(auth_client, "list_projects", {})
        call_tool(auth_client, "list_experiments", {})

        assert MCPUsageRecord.objects.filter(tool_name="whoami").exists()
        assert MCPUsageRecord.objects.filter(tool_name="list_projects").exists()
        assert MCPUsageRecord.objects.filter(tool_name="list_experiments").exists()

    def test_nonexistent_tool_returns_404(self, auth_client, user, workspace):
        """Calling a nonexistent tool should return 404."""
        response = call_tool(auth_client, "nonexistent_tool_xyz", {})
        assert response.status_code == 404, "Nonexistent tool should return 404"
