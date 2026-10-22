"""The generated agent tools and the observability switch.

``create_agent``, ``update_agent`` and ``create_agent_version`` are generated from
the MCP catalog and call the real API views in-process. These tests send every
case through the path an MCP tool call takes below the transport: the tool from
the registry, the executor, the real DRF view, the real switch and the database.
Only the two reads the switch makes about the agent and the provider list are
stood in, as in ``test_observability_switch``.

``catches:`` comments name the bug a test is there to catch.
"""

import uuid
from unittest.mock import patch

import pytest

from ai_tools import error_codes
from ai_tools.base import ToolContext
from ai_tools.registry import registry as tool_registry
from mcp_server.api_executor import (
    APIExecutionError,
    MCPRequestContext,
    ensure_urlconf_loaded,
    executor,
)
from mcp_server.generated_registry import registry
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.services import observability_switch
from tracer.services.observability_switch import observability_is_on
from tracer.tests.test_observability_switch import (
    AGENT_ID_MESSAGE,
    _agent,
    _agent_with_row,
    _created,
    _stored_agent,
    make_summary,
    seams,
)

REFUSAL_CODE = "observability_prerequisite_missing"
PROJECT_ERROR = "observability project could not be created"


@pytest.fixture
def mcp(auth_client, user, workspace):
    """The caller an MCP credential authenticated as, on a cold worker."""
    # The HTTP client of the fixture patches ``APIView.initial``; the MCP path
    # resolves the tenant from the request headers instead, so take it off.
    auth_client.stop_workspace_injection()
    clear_workspace_context()
    ensure_urlconf_loaded()
    return MCPRequestContext(
        user=user, organization=user.organization, workspace=workspace
    )


def _result(name, arguments, mcp):
    """Run the tool as the assistant does; return its ``ToolResult``."""
    context = ToolContext(
        user=mcp.user, organization=mcp.organization, workspace=mcp.workspace
    )
    return tool_registry.get(name).run(arguments, context)


def _body(name, arguments, mcp):
    """Run the tool as an MCP client does; return the view's answer."""
    return executor.execute_sync(registry.get(name), arguments, mcp)


def _create_arguments(name, **overrides):
    arguments = {
        "agent_name": name,
        "agent_type": "voice",
        "commit_message": "Initial version",
        "provider": "vapi",
        "contact_number": "+12345678901",
        "inbound": True,
        "languages": ["en"],
        "description": "An observed bot",
        "assistant_id": "assistant-1",
        "api_key": "test-key",
        "authentication_method": "api_key",
        "observability_enabled": True,
    }
    arguments.update(overrides)
    return arguments


def _agent_with_version(organization, workspace, **fields):
    agent = _agent(organization, workspace, **fields)
    agent.create_version(
        description="first",
        commit_message="first",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    return agent


def _versions(agent):
    return AgentVersion.objects.filter(agent_definition=agent).count()


def _assert_missing_assistant_id(result):
    """The tool answered with the switch's refusal, field by field."""
    assert result.is_error
    assert result.error_code == error_codes.VALIDATION_ERROR
    assert AGENT_ID_MESSAGE in result.content
    assert result.data["code"] == REFUSAL_CODE
    assert result.data["details"] == {"assistant_id": [AGENT_ID_MESSAGE]}


# ---------------------------------------------------------------------------
# The schemas
# ---------------------------------------------------------------------------


def test_update_agent_accepts_observability_enabled():
    # catches: the update tool schema dropping the field, so the tool cannot send it
    tool = registry.get("update_agent")
    field = tool.input_schema["properties"]["observability_enabled"]

    assert field["type"] == "boolean"
    assert "observability_enabled" in tool.request["parameters"]["body"]
    assert "observability_enabled" not in tool.input_schema.get("required", [])
    # The schema accepts the field as the executor validates it.
    tool.validator.validate(
        {"agent_id": str(uuid.uuid4()), "observability_enabled": True}
    )


def test_create_agent_version_has_no_default_for_observability_enabled():
    # catches: the version tool schema keeping a default of false, which makes
    # every call that leaves the field out ask for the switch to be turned off
    tool = registry.get("create_agent_version")
    field = tool.input_schema["properties"]["observability_enabled"]

    assert field["type"] == "boolean"
    assert "default" not in field
    assert "observability_enabled" not in tool.input_schema.get("required", [])


# ---------------------------------------------------------------------------
# create_agent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateAgentTool:
    def test_refusal_is_a_validation_error_and_leaves_no_agent(self, mcp):
        # catches: the switch's refusal coming back as a success, or as an
        # internal error, or the agent row surviving a refused create
        name = f"Refused {uuid.uuid4().hex[:8]}"
        created = _created()
        credentials = ProviderCredentials.no_workspace_objects.count()
        refusing = make_summary(missing=("provider_agent_id",))

        with seams(refusing):
            result = _result("create_agent", _create_arguments(name), mcp)

        _assert_missing_assistant_id(result)
        assert not AgentDefinition.no_workspace_objects.filter(agent_name=name).exists()
        assert not AgentVersion.objects.filter(
            agent_definition__agent_name=name
        ).exists()
        assert ProviderCredentials.no_workspace_objects.count() == credentials
        assert _created() == created

    def test_turn_on_links_the_new_agent(self, mcp):
        # catches: the create tool not passing observability_enabled to the view
        name = f"Observed {uuid.uuid4().hex[:8]}"

        with seams(make_summary()):
            result = _result("create_agent", _create_arguments(name), mcp)

        assert not result.is_error
        agent = AgentDefinition.no_workspace_objects.get(agent_name=name)
        assert observability_is_on(agent)

    def test_project_error_writes_nothing(self, mcp):
        # catches: a half-made agent left behind when the switch cannot make
        # its project (the view answers 500 and rolls its writes back)
        name = f"Broken {uuid.uuid4().hex[:8]}"
        created = _created()

        with (
            seams(make_summary()),
            patch.object(
                observability_switch,
                "create_observability_provider",
                return_value={"error": "Invalid data"},
            ),
            pytest.raises(APIExecutionError) as caught,
        ):
            _body("create_agent", _create_arguments(name), mcp)

        assert caught.value.status_code == 500
        assert PROJECT_ERROR in str(caught.value)
        assert not AgentDefinition.no_workspace_objects.filter(agent_name=name).exists()
        assert not AgentVersion.objects.filter(
            agent_definition__agent_name=name
        ).exists()
        assert _created() == created


# ---------------------------------------------------------------------------
# update_agent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateAgentTool:
    def test_true_turns_observability_on(self, mcp, organization, workspace):
        # catches: the edit view ignoring observability_enabled
        agent = _agent(organization, workspace)
        assert not observability_is_on(agent)

        with seams(make_summary(agent_definition_id=agent.id)):
            result = _result(
                "update_agent",
                {"agent_id": str(agent.id), "observability_enabled": True},
                mcp,
            )

        assert not result.is_error
        stored = _stored_agent(agent)
        assert stored.observability_provider_id is not None
        assert stored.observability_provider.enabled
        assert observability_is_on(stored)

    def test_false_turns_observability_off(self, mcp, organization, workspace):
        # catches: false being read as "no change", or the row being left enabled
        agent = _agent_with_row(organization, workspace, enabled=True)
        assert observability_is_on(_stored_agent(agent))
        link = agent.observability_provider_id

        with seams(make_summary(agent_definition_id=agent.id)):
            result = _result(
                "update_agent",
                {"agent_id": str(agent.id), "observability_enabled": False},
                mcp,
            )

        assert not result.is_error
        stored = _stored_agent(agent)
        assert stored.observability_provider_id == link
        assert not stored.observability_provider.enabled
        assert not observability_is_on(stored)

    @pytest.mark.parametrize("enabled", [True, False], ids=["on", "off"])
    def test_leaving_the_field_out_changes_nothing(
        self, mcp, organization, workspace, enabled
    ):
        # catches: an absent observability_enabled read as false (turns an agent
        # off on any other edit) or as true (turns it on)
        agent = _agent_with_row(organization, workspace, enabled=enabled)
        link = agent.observability_provider_id
        created = _created()

        with seams(make_summary(agent_definition_id=agent.id)):
            result = _result(
                "update_agent",
                {"agent_id": str(agent.id), "description": "Edited description"},
                mcp,
            )

        assert not result.is_error
        stored = _stored_agent(agent)
        assert stored.description == "Edited description"
        assert stored.observability_provider_id == link
        assert stored.observability_provider.enabled is enabled
        assert observability_is_on(stored) is enabled
        assert _created() == created

    def test_refusal_is_a_validation_error_and_writes_nothing(
        self, mcp, organization, workspace
    ):
        # catches: the refusal leaving the edit's column writes or its credential
        # row behind, or the link being set before the switch decides
        agent = _agent_with_version(organization, workspace, agent_name="Before")
        credentials = ProviderCredentials.no_workspace_objects.count()
        created = _created()
        refusing = make_summary(
            agent_definition_id=agent.id, missing=("provider_agent_id",)
        )

        arguments = {
            "agent_id": str(agent.id),
            "agent_name": "After",
            "api_key": "new-test-key",
            "observability_enabled": True,
        }

        with seams(refusing):
            result = _result("update_agent", arguments, mcp)
            # catches: the refusal answered with another 4xx that the tool layer
            # would still call a validation error
            with pytest.raises(APIExecutionError) as caught:
                _body("update_agent", arguments, mcp)

        _assert_missing_assistant_id(result)
        assert caught.value.status_code == 400
        stored = _stored_agent(agent)
        assert (stored.agent_name, stored.api_key) == ("Before", "test-key")
        assert stored.observability_provider_id is None
        assert ProviderCredentials.no_workspace_objects.count() == credentials
        assert _created() == created

    def test_project_error_writes_nothing(self, mcp, organization, workspace):
        # catches: the edit's column writes surviving a failed project creation
        agent = _agent_with_version(organization, workspace, agent_name="Before")
        credentials = ProviderCredentials.no_workspace_objects.count()
        created = _created()

        with (
            seams(make_summary(agent_definition_id=agent.id)),
            patch.object(
                observability_switch,
                "create_observability_provider",
                return_value={"error": "Invalid data"},
            ),
            pytest.raises(APIExecutionError) as caught,
        ):
            _body(
                "update_agent",
                {
                    "agent_id": str(agent.id),
                    "agent_name": "After",
                    "api_key": "new-test-key",
                    "observability_enabled": True,
                },
                mcp,
            )

        assert caught.value.status_code == 500
        assert PROJECT_ERROR in str(caught.value)
        stored = _stored_agent(agent)
        assert (stored.agent_name, stored.api_key) == ("Before", "test-key")
        assert stored.observability_provider_id is None
        assert ProviderCredentials.no_workspace_objects.count() == credentials
        assert _created() == created


# ---------------------------------------------------------------------------
# create_agent_version
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateAgentVersionTool:
    def test_turn_on_links_the_agent_and_the_version_reads_on(
        self, mcp, organization, workspace
    ):
        # catches: the version tool not turning the switch on, or the returned
        # version snapshot taken before the switch (reads off)
        agent = _agent_with_version(organization, workspace)
        versions = _versions(agent)

        with seams(make_summary(agent_definition_id=agent.id)):
            body = _body(
                "create_agent_version",
                {
                    "agent_id": str(agent.id),
                    "commit_message": "observability",
                    "observability_enabled": True,
                },
                mcp,
            )

        stored = _stored_agent(agent)
        assert observability_is_on(stored)
        assert _versions(agent) == versions + 1
        version = body["version"]
        assert version["commit_message"] == "observability"
        assert version["configuration_snapshot"]["observability_enabled"] is True

    def test_refusal_rolls_back_every_write(self, mcp, organization, workspace):
        # catches: the refusal leaving the new version, the changed columns or the
        # credential row behind
        agent = _agent_with_version(organization, workspace, agent_name="Before")
        versions = _versions(agent)
        credentials = ProviderCredentials.no_workspace_objects.count()
        created = _created()
        refusing = make_summary(
            agent_definition_id=agent.id, missing=("provider_agent_id",)
        )

        with seams(refusing):
            result = _result(
                "create_agent_version",
                {
                    "agent_id": str(agent.id),
                    "commit_message": "observability",
                    "agent_name": "After",
                    "api_key": "new-test-key",
                    "observability_enabled": True,
                },
                mcp,
            )

        _assert_missing_assistant_id(result)
        stored = _stored_agent(agent)
        assert (stored.agent_name, stored.api_key) == ("Before", "test-key")
        assert stored.observability_provider_id is None
        assert _versions(agent) == versions
        assert ProviderCredentials.no_workspace_objects.count() == credentials
        assert _created() == created
