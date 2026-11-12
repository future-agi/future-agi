"""B9: replay links are scoped and atomic with agent/version creation."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import OperationalError

from accounts.models.workspace import Workspace
from simulate.models import AgentDefinition, AgentVersion
from tfc.utils.error_codes import get_error_message
from tracer.models.project import Project
from tracer.models.replay_session import ReplaySession

URL = "/simulate/agent-definitions/create/"
MARKER = "B9_PRIVATE_SQL_HOST_MARKER"
pytestmark = pytest.mark.django_db


@pytest.fixture
def project(organization, workspace):
    from model_hub.models.ai_model import AIModel

    return Project.no_workspace_objects.create(
        name="B9 own project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _payload(session_id):
    return {
        "agent_name": "Replay link security",
        "agent_type": "text",
        "commit_message": "Initial version",
        "languages": ["en"],
        "replay_session_id": str(session_id),
    }


def _counts():
    return (
        AgentDefinition.no_workspace_objects.count(),
        AgentVersion.no_workspace_objects.count(),
    )


def _session(project):
    return ReplaySession.no_workspace_objects.create(
        project=project, replay_type="trace", ids=[], select_all=False
    )


def test_unknown_and_foreign_replay_links_are_identical_and_atomic(
    auth_client, organization, workspace, project
):
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="B9 foreign workspace", organization=organization, is_default=False
    )
    foreign_project = Project.no_workspace_objects.create(
        name="B9 foreign project",
        organization=organization,
        workspace=foreign_workspace,
        model_type=project.model_type,
        trace_type="observe",
    )
    foreign_session = _session(foreign_project)
    before = _counts()
    responses = []
    # Refusal must happen before save, not merely be rolled back afterwards.
    with patch.object(
        AgentDefinition.objects, "create", wraps=AgentDefinition.objects.create
    ) as create:
        for session_id in (foreign_session.id, uuid4()):
            response = auth_client.post(URL, _payload(session_id), format="json")
            assert response.status_code == 404
            assert response.data["message"] == get_error_message(
                "REPLAY_SESSION_NOT_FOUND"
            )
            assert _counts() == before
            responses.append(response.data)
        create.assert_not_called()
    assert responses[0] == responses[1]
    foreign_session.refresh_from_db()
    assert foreign_session.agent_definition_id is None


def test_own_workspace_replay_session_links(auth_client, project):
    session = _session(project)
    before = _counts()
    response = auth_client.post(URL, _payload(session.id), format="json")
    assert response.status_code == 201, response.data
    session.refresh_from_db()
    assert str(session.agent_definition_id) == response.data["agent"]["id"]
    assert _counts() == (before[0] + 1, before[1] + 1)


def test_link_write_failure_rolls_back_agent_and_version(auth_client, project):
    session = _session(project)
    before = _counts()
    with patch(
        "tracer.utils.replay_session.ReplaySession.save",
        side_effect=OperationalError(MARKER),
    ):
        response = auth_client.post(URL, _payload(session.id), format="json")
    assert response.status_code == 500
    assert response.data["code"] == "server_error"
    assert response.data["message"] == (
        "Agent definition creation could not be confirmed. "
        "Check the agent definitions before trying again."
    )
    assert MARKER not in str(response.data)
    assert _counts() == before
    session.refresh_from_db()
    assert session.agent_definition_id is None


@pytest.mark.parametrize("with_replay", [False, True], ids=["direct", "replay"])
def test_response_serialization_failure_preserves_created_records_and_guidance(
    auth_client, project, with_replay
):
    session = _session(project)
    payload = _payload(session.id)
    if not with_replay:
        payload.pop("replay_session_id")
    agent_ids = set(AgentDefinition.no_workspace_objects.values_list("id", flat=True))
    version_ids = set(AgentVersion.no_workspace_objects.values_list("id", flat=True))
    before = _counts()

    # Fail only response serialization, after the real create/link atomic block.
    with patch(
        "simulate.views.agent_definition.AgentDefinitionResponseSerializer.to_representation",
        side_effect=OperationalError(MARKER),
    ) as serialize:
        response = auth_client.post(URL, payload, format="json")
    serialize.assert_called_once()
    assert response.status_code == 500
    assert response.data["code"] == "server_error"
    expected = (
        "Agent definition creation could not be confirmed. "
        "Check the agent definitions before trying again."
    )
    for field in ("message", "detail", "error", "result"):
        assert response.data[field] == expected
    assert _counts() == (before[0] + 1, before[1] + 1)
    agent = AgentDefinition.no_workspace_objects.exclude(id__in=agent_ids).get()
    version = AgentVersion.no_workspace_objects.exclude(id__in=version_ids).get()
    assert version.agent_definition_id == agent.id
    assert serialize.call_args.args[0].id == agent.id
    session.refresh_from_db()
    assert session.agent_definition_id == (agent.id if with_replay else None)
    if not with_replay:
        assert not ReplaySession.no_workspace_objects.filter(
            agent_definition_id=agent.id
        ).exists()
    for private in (
        MARKER,
        "OperationalError",
        "Traceback",
        agent.id,
        version.id,
        session.id,
    ):
        assert str(private) not in str(response.data)


@pytest.mark.parametrize("deleted_target", ["session", "project"])
def test_deleted_replay_target_is_not_found(auth_client, project, deleted_target):
    session = _session(project)
    target = session if deleted_target == "session" else project
    target.deleted = True
    target.save(update_fields=["deleted"])
    before = _counts()
    response = auth_client.post(URL, _payload(session.id), format="json")
    assert response.status_code == 404
    assert response.data["message"] == get_error_message("REPLAY_SESSION_NOT_FOUND")
    assert _counts() == before
