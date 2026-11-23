"""An adaptation's agent, read from a version connected on the Agents page."""

import pytest

from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from simulate.services.harness_releases import connected_agent_connection
from simulate.services.hosted_harness import HostedHarnessError


@pytest.fixture
def version(organization):
    definition = AgentDefinition.no_workspace_objects.create(
        agent_name="Clinic chat",
        agent_type="text",
        provider="retell",
        assistant_id="agent_clinic",
        inbound=True,
        organization=organization,
        description="",
    )
    return AgentVersion.no_workspace_objects.create(
        agent_definition=definition,
        version_number=1,
        version_name="v1",
        organization=organization,
        description="",
        commit_message="",
    )


def _key(version, provider_type="retell", value="test-key"):
    ProviderCredentials.objects.create(
        agent_version=version, provider_type=provider_type, api_key=value
    )


@pytest.mark.django_db
class TestConnectedAgentConnection:
    def test_a_retell_chat_agent_is_reached_by_its_id_and_its_stored_key(
        self, organization, version
    ):
        _key(version)

        connection = connected_agent_connection(organization, version)

        assert connection["connector"] == "retell_chat"
        assert connection["mode"] == "provider_import"
        assert connection["config"] == {"agent_id": "agent_clinic"}
        # Only a ref leaves: the key itself is stored as an organization secret.
        assert list(connection["secret_refs"]) == ["RETELL_API_KEY"]
        assert "test-key" not in str(connection)

    def test_a_voice_retell_agent_is_the_voice_connector(self, organization, version):
        version.agent_definition.agent_type = "voice"
        version.agent_definition.save(update_fields=["agent_type"])
        _key(version)

        assert (
            connected_agent_connection(organization, version)["connector"] == "retell"
        )

    def test_a_vapi_agent_is_reached_by_its_assistant_id(self, organization, version):
        version.agent_definition.provider = "vapi"
        version.agent_definition.save(update_fields=["provider"])
        _key(version, provider_type="vapi")

        connection = connected_agent_connection(organization, version)

        assert connection["connector"] == "vapi"
        assert connection["config"] == {"assistant_id": "agent_clinic"}
        assert list(connection["secret_refs"]) == ["VAPI_API_KEY"]

    def test_a_version_without_its_providers_key_is_refused(
        self, organization, version
    ):
        with pytest.raises(HostedHarnessError) as refused:
            connected_agent_connection(organization, version)
        assert refused.value.code == "provider_credentials_missing"

    def test_an_agent_other_than_retell_or_vapi_is_refused(self, organization, version):
        version.agent_definition.provider = "livekit"
        version.agent_definition.save(update_fields=["provider"])

        with pytest.raises(HostedHarnessError) as refused:
            connected_agent_connection(organization, version)
        assert refused.value.code == "adaptation_agent_unsupported"

    def test_an_agent_without_a_provider_id_is_refused(self, organization, version):
        version.agent_definition.assistant_id = ""
        version.agent_definition.save(update_fields=["assistant_id"])
        _key(version)

        with pytest.raises(HostedHarnessError) as refused:
            connected_agent_connection(organization, version)
        assert refused.value.code == "adaptation_agent_unsupported"
