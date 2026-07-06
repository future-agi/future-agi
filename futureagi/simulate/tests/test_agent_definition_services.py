"""
Unit tests for AgentDefinition service layer functions.

Tests cover:
- sync_provider_credentials: creation, update, legacy adoption, provider routing
- resolve_api_key_for_version: version creds, legacy fallback, None version
- is_masked: various masking patterns
"""

import pytest

from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from simulate.services.agent_definition import (
    is_masked,
    resolve_api_key_for_version,
    sync_provider_credentials,
)
from simulate.services.types.agent_definition import ProviderCredentialsInput


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Test Service Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12345678901",
        inbound=True,
        description="Test agent for service tests",
        provider="vapi",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_version(db, agent_definition):
    return agent_definition.create_version(
        description="Initial version",
        commit_message="First version",
        status=AgentVersion.StatusChoices.ACTIVE,
    )


# ============================================================================
# Tests for is_masked()
# ============================================================================


class TestIsMasked:
    def test_masked_value(self):
        assert is_masked("********") is True

    def test_short_masked(self):
        assert is_masked("****") is True

    def test_elided_format(self):
        assert is_masked("abcd...efgh") is True

    def test_empty_string(self):
        assert is_masked("") is False

    def test_none(self):
        assert is_masked(None) is False

    def test_real_api_key(self):
        assert is_masked("sk-real-key-12345") is False

    def test_random_string(self):
        assert is_masked("hello world") is False


# ============================================================================
# Tests for sync_provider_credentials()
# ============================================================================


class TestSyncProviderCredentials:

    def test_creates_vapi_credentials(self, agent_version):
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="vapi",
                api_key="sk-vapi-key",
                assistant_id="asst_vapi",
                provider_was_provided=True,
            ),
        )
        creds = ProviderCredentials.objects.get(agent_version=agent_version)
        assert creds.provider_type == ProviderCredentials.ProviderType.VAPI
        assert creds.get_api_key() == "sk-vapi-key"
        assert creds.assistant_id == "asst_vapi"

    def test_creates_retell_credentials(self, agent_version):
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="retell",
                api_key="sk-retell-key",
                assistant_id="asst_retell",
                provider_was_provided=True,
            ),
        )
        creds = ProviderCredentials.objects.get(agent_version=agent_version)
        assert creds.provider_type == ProviderCredentials.ProviderType.RETELL
        assert creds.get_api_key() == "sk-retell-key"
        assert creds.assistant_id == "asst_retell"

    def test_creates_livekit_credentials(self, agent_version):
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="livekit",
                livekit_api_key="lk-api-key",
                livekit_api_secret="lk-secret",
                livekit_url="https://livekit.example.com",
                livekit_agent_name="test-agent",
                livekit_config_json={"setting": "value"},
                livekit_max_concurrency=10,
                provider_was_provided=True,
            ),
        )
        creds = ProviderCredentials.objects.get(agent_version=agent_version)
        assert creds.provider_type == ProviderCredentials.ProviderType.LIVEKIT
        assert creds.get_api_key() == "lk-api-key"
        assert creds.server_url == "https://livekit.example.com"
        assert creds.agent_name == "test-agent"
        assert creds.max_concurrency == 10

    def test_updates_existing_credentials(self, agent_version):
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="vapi",
                api_key="original-key",
                assistant_id="asst_original",
                provider_was_provided=True,
            ),
        )
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="vapi",
                api_key="updated-key",
                assistant_id="asst_updated",
                provider_was_provided=True,
            ),
        )
        creds = ProviderCredentials.objects.get(agent_version=agent_version)
        assert creds.get_api_key() == "updated-key"
        assert creds.assistant_id == "asst_updated"
        assert ProviderCredentials.objects.filter(agent_version=agent_version).count() == 1

    def test_switches_provider_clears_old_secrets(self, agent_version):
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="vapi",
                api_key="sk-vapi-key",
                provider_was_provided=True,
            ),
        )
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="livekit",
                livekit_api_key="lk-new-key",
                livekit_api_secret="lk-new-secret",
                livekit_url="https://new.example.com",
                livekit_agent_name="new-agent",
                provider_was_provided=True,
            ),
        )
        creds = ProviderCredentials.objects.get(agent_version=agent_version)
        assert creds.provider_type == ProviderCredentials.ProviderType.LIVEKIT
        assert creds.server_url == "https://new.example.com"

    def test_masked_key_does_not_overwrite(self, agent_version):
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="vapi",
                api_key="sk-preserved-key",
                provider_was_provided=True,
            ),
        )
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="vapi",
                api_key="********",
                provider_was_provided=True,
            ),
        )
        creds = ProviderCredentials.objects.get(agent_version=agent_version)
        assert creds.get_api_key() == "sk-preserved-key"

    def test_noop_when_no_credential_fields(self, agent_version):
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="vapi",
                provider_was_provided=False,
            ),
        )
        assert not ProviderCredentials.objects.filter(agent_version=agent_version).exists()

    def test_adopts_legacy_credentials(self, agent_version):
        agent = agent_version.agent_definition
        ProviderCredentials.objects.create(
            agent_definition=agent,
            provider_type=ProviderCredentials.ProviderType.VAPI,
            api_key="sk-legacy-key",
            assistant_id="asst_legacy",
        )
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="vapi",
                provider_was_provided=False,
            ),
        )
        legacy = ProviderCredentials.objects.get(agent_version=agent_version)
        assert legacy.agent_definition is None
        assert legacy.get_api_key() == "sk-legacy-key"


# ============================================================================
# Tests for resolve_api_key_for_version()
# ============================================================================


class TestResolveApiKeyForVersion:
    def test_returns_none_for_no_version(self):
        assert resolve_api_key_for_version(None) is None

    def test_returns_none_when_no_credentials(self, agent_version):
        assert resolve_api_key_for_version(agent_version) is None

    def test_returns_key_from_direct_credentials(self, agent_version):
        ProviderCredentials.objects.create(
            agent_version=agent_version,
            provider_type=ProviderCredentials.ProviderType.VAPI,
            api_key="sk-direct-key",
        )
        assert resolve_api_key_for_version(agent_version) == "sk-direct-key"

    def test_falls_back_to_legacy_credentials(self, agent_version):
        agent = agent_version.agent_definition
        ProviderCredentials.objects.create(
            agent_definition=agent,
            provider_type=ProviderCredentials.ProviderType.VAPI,
            api_key="sk-legacy-fallback-key",
        )
        assert resolve_api_key_for_version(agent_version) == "sk-legacy-fallback-key"
