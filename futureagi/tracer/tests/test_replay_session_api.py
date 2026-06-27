import uuid

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition
from simulate.models.agent_version import AgentVersion
from simulate.models.scenarios import Scenarios
from simulate.models.simulator_agent import SimulatorAgent
from tfc.constants.roles import OrganizationRoles
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.models.replay_session import ReplaySession, ReplaySessionStep


REPLAY_SESSION_PATH = "/tracer/replay-session/"


def result(response):
    return response.json().get("result", response.json())


@pytest.mark.django_db
def test_replay_session_create_list_retrieve_and_eval_configs(
    auth_client,
    observe_project,
    trace_session,
    organization,
    workspace,
):
    eval_template = EvalTemplate.no_workspace_objects.create(
        name=f"replay_eval_{uuid.uuid4().hex[:8]}",
        description="Replay Eval",
        organization=organization,
        workspace=workspace,
        eval_tags=["replay"],
        config={
            "models": ["gpt-4o", "gpt-4o-mini"],
            "required_keys": ["input"],
            "optional_keys": ["output"],
        },
    )
    eval_config = CustomEvalConfig.no_workspace_objects.create(
        name="Replay Config",
        project=observe_project,
        eval_template=eval_template,
        config={"params": {"threshold": 0.75}},
        mapping={"input": "input"},
        error_localizer=True,
    )

    create_response = auth_client.post(
        REPLAY_SESSION_PATH,
        data={
            "project_id": str(observe_project.id),
            "replay_type": "session",
            "ids": [str(trace_session.id)],
            "select_all": False,
        },
        format="json",
    )

    assert create_response.status_code == status.HTTP_201_CREATED
    created = result(create_response)
    assert created["project"] == str(observe_project.id)
    assert created["replay_type"] == "session"
    assert created["ids"] == [str(trace_session.id)]
    assert created["select_all"] is False
    assert created["current_step"] == ReplaySessionStep.INIT
    assert created["exists"] is False
    assert created["suggestions"]["agent_type"] == "text"

    list_response = auth_client.get(
        REPLAY_SESSION_PATH,
        data={"project_id": str(observe_project.id), "page": 1, "limit": 5},
    )
    assert list_response.status_code == status.HTTP_200_OK
    assert any(row["id"] == created["id"] for row in list_response.json()["results"])

    detail_response = auth_client.get(f"{REPLAY_SESSION_PATH}{created['id']}/")
    assert detail_response.status_code == status.HTTP_200_OK
    detail = result(detail_response)
    assert detail["id"] == created["id"]
    assert detail["project"] == str(observe_project.id)
    assert detail["agent_definition"] is None
    assert detail["scenario"] is None

    configs_response = auth_client.get(
        f"{REPLAY_SESSION_PATH}eval-configs/",
        data={"project_id": str(observe_project.id)},
    )
    assert configs_response.status_code == status.HTTP_200_OK
    configs_payload = result(configs_response)
    config_rows = configs_payload["eval_configs"]
    assert [row["id"] for row in config_rows] == [str(eval_config.id)]
    assert config_rows[0]["name"] == "Replay Config"
    assert config_rows[0]["eval_template"]["required_keys"] == ["input"]
    assert config_rows[0]["available_models"] == ["gpt-4o", "gpt-4o-mini"]
    assert set(configs_payload["common_models"]) == {"gpt-4o", "gpt-4o-mini"}


@pytest.mark.django_db
def test_replay_session_generate_scenario_links_agent_and_scenario(
    auth_client,
    observe_project,
    trace_session,
    workspace,
    monkeypatch,
):
    replay_session = ReplaySession.no_workspace_objects.create(
        project=observe_project,
        replay_type="session",
        ids=[str(trace_session.id)],
        select_all=False,
        current_step=ReplaySessionStep.INIT,
    )
    agent = AgentDefinition.objects.create(
        agent_name="Replay Agent",
        description="Replay agent description",
        agent_type="text",
        inbound=True,
        organization=observe_project.organization,
        workspace=workspace,
        languages=["en"],
    )
    AgentVersion.objects.create(
        agent_definition=agent,
        organization=observe_project.organization,
        workspace=workspace,
        version_number=1,
        version_name="v1",
        description="Replay version",
        commit_message="initial",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    simulator_agent = SimulatorAgent.objects.create(
        name="Replay Simulator",
        prompt="Replay prompt",
        voice_provider="openai",
        voice_name="alloy",
        model="gpt-4o-mini",
        organization=observe_project.organization,
        workspace=workspace,
    )
    scenario = Scenarios.objects.create(
        name="Replay Scenario",
        description="Replay scenario description",
        source="Session Replay",
        scenario_type=Scenarios.ScenarioTypes.GRAPH,
        organization=observe_project.organization,
        workspace=workspace,
        status=StatusType.PROCESSING.value,
        agent_definition=agent,
        simulator_agent=simulator_agent,
        metadata={"project_id": str(observe_project.id), "created_from": "test"},
    )
    workflow_calls = []

    monkeypatch.setattr(
        "tracer.views.replay_session.get_transcripts",
        lambda **kwargs: {
            str(trace_session.id): {
                "replay_type": "session",
                "transcript": '[{"input":"hello","output":"hi"}]',
            }
        },
    )
    monkeypatch.setattr(
        "tracer.views.replay_session.get_or_create_agent_definition",
        lambda **kwargs: agent,
    )
    monkeypatch.setattr(
        "tracer.views.replay_session.create_scenario",
        lambda **kwargs: scenario,
    )
    monkeypatch.setattr(
        "tracer.views.replay_session.start_create_graph_scenario_workflow_sync",
        lambda validated_data, scenario_id: workflow_calls.append(
            {"validated_data": validated_data, "scenario_id": scenario_id}
        )
        or "workflow-test",
    )

    response = auth_client.post(
        f"{REPLAY_SESSION_PATH}{replay_session.id}/generate-scenario/",
        data={
            "agent_name": "Replay Agent",
            "agent_description": "Replay agent description",
            "scenario_name": "Replay Scenario",
            "agent_type": "text",
            "no_of_rows": 3,
            "custom_columns": [{"name": "intent", "type": "text"}],
            "generate_graph": False,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    payload = result(response)
    assert payload["id"] == str(replay_session.id)
    assert payload["current_step"] == ReplaySessionStep.GENERATING
    assert payload["agent_definition_id"] == str(agent.id)
    assert payload["agent_definition_latest_version_id"] == str(agent.latest_version.id)
    assert payload["scenario_id"] == str(scenario.id)
    replay_session.refresh_from_db()
    assert replay_session.agent_definition_id == agent.id
    assert replay_session.scenario_id == scenario.id
    assert replay_session.current_step == ReplaySessionStep.GENERATING
    assert workflow_calls == [
        {
            "scenario_id": str(scenario.id),
            "validated_data": {
                "agent_definition_id": str(agent.id),
                "no_of_rows": 3,
                "generate_graph": False,
                "graph": None,
                "personas": [],
                "custom_columns": [{"name": "intent", "type": "text"}],
                "transcripts": {
                    str(trace_session.id): {
                        "replay_type": "session",
                        "transcript": '[{"input":"hello","output":"hi"}]',
                    }
                },
            },
        }
    ]


@pytest.mark.django_db
def test_replay_session_routes_hide_out_of_scope_projects_and_sessions(
    auth_client,
    organization,
    user,
    workspace,
):
    other_workspace = Workspace.no_workspace_objects.create(
        name="Replay Other Workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    other_workspace_project = Project.no_workspace_objects.create(
        name="Replay Other Workspace Project",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    other_workspace_replay = ReplaySession.no_workspace_objects.create(
        project=other_workspace_project,
        replay_type="session",
        ids=[str(uuid.uuid4())],
        select_all=False,
        current_step=ReplaySessionStep.INIT,
    )

    other_organization = Organization.objects.create(name="Replay Other Org")
    other_user = User.objects.create_user(
        email="replay-other@example.com",
        password="testpassword123",
        name="Replay Other User",
        organization=other_organization,
        organization_role=OrganizationRoles.OWNER,
    )
    other_org_workspace = Workspace.no_workspace_objects.create(
        name="Replay Other Org Workspace",
        organization=other_organization,
        is_default=True,
        is_active=True,
        created_by=other_user,
    )
    other_org_project = Project.no_workspace_objects.create(
        name="Replay Other Org Project",
        organization=other_organization,
        workspace=other_org_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    other_org_replay = ReplaySession.no_workspace_objects.create(
        project=other_org_project,
        replay_type="trace",
        ids=[str(uuid.uuid4())],
        select_all=False,
        current_step=ReplaySessionStep.INIT,
    )

    for project, replay_session in (
        (other_workspace_project, other_workspace_replay),
        (other_org_project, other_org_replay),
    ):
        create_response = auth_client.post(
            REPLAY_SESSION_PATH,
            data={
                "project_id": str(project.id),
                "replay_type": "session",
                "ids": [str(uuid.uuid4())],
                "select_all": False,
            },
            format="json",
        )
        assert create_response.status_code == status.HTTP_400_BAD_REQUEST

        list_response = auth_client.get(
            REPLAY_SESSION_PATH,
            data={"project_id": str(project.id)},
        )
        assert list_response.status_code == status.HTTP_200_OK
        assert list_response.json()["results"] == []

        detail_response = auth_client.get(f"{REPLAY_SESSION_PATH}{replay_session.id}/")
        assert detail_response.status_code == status.HTTP_404_NOT_FOUND

        eval_configs_response = auth_client.get(
            f"{REPLAY_SESSION_PATH}eval-configs/",
            data={"project_id": str(project.id)},
        )
        assert eval_configs_response.status_code == status.HTTP_404_NOT_FOUND
