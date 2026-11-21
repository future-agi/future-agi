import copy

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from simulate.models import AgentDefinition, AgentVersion, HarnessAgentVersionRuntime
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services import agent_setup as service
from simulate.services.agent_compatibility import (
    CLEAN,
    TOOL_REMOVED_OR_CHANGED,
    compare_documents,
)
from simulate.services.agent_reading_sandbox import _store_reading
from simulate.services.agent_setup import AgentStoreError, store_agent
from simulate.tests.test_agent_overview_api import (
    _code_agent,
    _link,
    _observe_project,
)
from simulate.tests.test_agent_reading_jobs import _read
from simulate.tests.test_agent_setup_service import (
    HASH_A,
    HASH_B,
    source_agent,
)
from simulate.tests.test_agent_setup_service import (
    definition as definition,  # noqa: F401
)
from simulate.tests.test_agent_setup_service import (
    version as version,  # noqa: F401
)
from simulate.tests.test_code_archives import storage as storage  # noqa: F401
from simulate.tests.test_harness_connected_versions import _connect, _runtime

pytestmark = pytest.mark.django_db

HASH_C = "sh1:" + "c" * 64


def _with_parameters(parameters):
    document = source_agent()
    document["tools"][0]["parameters"] = copy.deepcopy(parameters)
    return document


TICKET = {
    "type": "object",
    "description": "Open a ticket.",
    "properties": {
        "description": {"type": "string"},
        "ticket": {
            "type": "object",
            "description": "The ticket.",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
        },
    },
    "required": ["description"],
}


def test_a_parameter_named_description_changing_type_is_a_changed_tool():
    after = copy.deepcopy(TICKET)
    after["properties"]["description"] = {"type": "integer"}

    result = compare_documents(_with_parameters(TICKET), _with_parameters(after))

    assert result["status"] == TOOL_REMOVED_OR_CHANGED
    assert [tool["name"] for tool in result["tools_changed"]] == ["get_booking"]


def test_removing_a_nested_parameter_named_title_is_a_changed_tool():
    after = copy.deepcopy(TICKET)
    del after["properties"]["ticket"]["properties"]["title"]

    result = compare_documents(_with_parameters(TICKET), _with_parameters(after))

    assert result["status"] == TOOL_REMOVED_OR_CHANGED


def test_rewording_a_schema_description_alone_stays_clean():
    after = copy.deepcopy(TICKET)
    after["description"] = "File a support ticket."
    after["properties"]["ticket"]["description"] = "What went wrong."

    result = compare_documents(_with_parameters(TICKET), _with_parameters(after))

    assert result["status"] == CLEAN


def test_a_stale_reading_row_cannot_replace_a_build_document(version):
    store_agent(
        agent_version_id=version.id,
        agent=source_agent(),
        sdk_version="0.2.3",
        origin=AgentVersionSetup.Origin.READING,
    )
    stale = AgentVersionSetup.all_objects.get(agent_version_id=version.id)
    assert (
        store_agent(
            agent_version_id=version.id,
            agent=source_agent(name="build_a", setup_hash=HASH_B),
            sdk_version="0.2.3",
            replace_reading=True,
        )
        == "replaced"
    )

    with pytest.raises(AgentStoreError) as error:
        service._replace_agent(
            stale,
            version,
            source_agent(name="build_b", setup_hash=HASH_C),
            "0.2.3",
            origin=AgentVersionSetup.Origin.BUILD,
        )

    assert error.value.code == "setup_outcome_already_stored"
    row = AgentVersionSetup.all_objects.get(agent_version_id=version.id)
    assert (row.setup_hash, row.name, row.origin) == (
        HASH_B,
        "build_a",
        AgentVersionSetup.Origin.BUILD,
    )
    assert stale.setup_hash == HASH_A


@pytest.mark.usefixtures("storage")
def test_the_first_voice_reading_moves_the_versions_snapshot_to_voice(
    organization, workspace, settings
):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    agent = AgentDefinition.objects.create(
        agent_name="support",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )
    first = _connect(agent, prompt="")
    assert first.configuration_snapshot["agent_type"] == "text"
    runtime = _runtime(first)
    assert (
        runtime.preflight_status == HarnessAgentVersionRuntime.PreflightStatus.PENDING
    )
    document = source_agent(type="voice")

    outcome = _store_reading(
        runtime, agent, _read(document), {"agent": {"name": "support"}}, None
    )

    assert outcome.status == "stored"
    agent.refresh_from_db()
    assert agent.agent_type == AgentDefinition.AgentTypeChoices.VOICE
    snapshot = AgentVersion.no_workspace_objects.get(id=first.id).configuration_snapshot
    assert snapshot["agent_type"] == "voice"
    assert snapshot["description"] == document["behaviour"][0]["instructions"]


def _workspaceless_code_agent(organization):
    agent = _code_agent(organization, None, "Harbor Hotels")
    AgentDefinition.no_workspace_objects.filter(id=agent.id).update(workspace=None)
    agent.refresh_from_db()
    assert agent.workspace_id is None
    return agent


def test_a_workspaceless_code_agent_cannot_link_another_workspaces_project(
    auth_client, organization, workspace, user
):
    agent = _workspaceless_code_agent(organization)
    other = Workspace.objects.create(
        name="Other", organization=organization, is_active=True, created_by=user
    )
    elsewhere = _observe_project(organization, other, "Other production")

    response = _link(auth_client, agent, str(elsewhere.id))

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    agent.refresh_from_db()
    assert agent.production_project_id is None


def test_a_workspaceless_code_agent_links_a_default_workspace_project(
    auth_client, organization, workspace
):
    agent = _workspaceless_code_agent(organization)
    chosen = _observe_project(organization, workspace, "Harbor production")

    response = _link(auth_client, agent, str(chosen.id))

    assert response.status_code == status.HTTP_200_OK, response.content
    agent.refresh_from_db()
    assert agent.production_project_id == chosen.id
