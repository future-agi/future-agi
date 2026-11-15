"""
Unit tests for AgentDefinition service layer functions.

Tests cover:
- sync_provider_credentials: creation, update, legacy adoption, provider routing
- resolve_api_key_for_version: version creds, legacy fallback, None version
- is_masked: various masking patterns
"""

import pytest

from accounts.models.workspace import Workspace
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from simulate.services.agent_definition import (
    is_masked,
    resolve_api_key_for_version,
    resolve_stored_api_key,
    sync_provider_credentials,
)
from simulate.services.types.agent_definition import ProviderCredentialsInput
from tfc.middleware.workspace_context import set_workspace_context


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


def test_provider_keys_are_left_out_of_the_input_repr():
    text = repr(
        ProviderCredentialsInput(
            provider="livekit",
            api_key="val-api-key",
            livekit_url="wss://lk.example.test",
            livekit_api_key="val-lk-key",
            livekit_api_secret="val-lk-secret",
        )
    )

    assert "livekit" in text
    for value in ("val-api-key", "val-lk-key", "val-lk-secret"):
        assert value not in text


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

    def test_creates_vapi_credentials_with_concurrency(self, agent_version):
        # Concurrency is generic across providers: a vapi target persists the
        # submitted max_concurrency the same way livekit does (previously the
        # vapi/retell branches discarded it).
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="vapi",
                api_key="sk-vapi-key",
                assistant_id="asst_vapi",
                livekit_max_concurrency=3,
                provider_was_provided=True,
            ),
        )
        creds = ProviderCredentials.objects.get(agent_version=agent_version)
        assert creds.provider_type == ProviderCredentials.ProviderType.VAPI
        assert creds.max_concurrency == 3

    def test_creates_retell_credentials_with_concurrency(self, agent_version):
        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="retell",
                api_key="sk-retell-key",
                assistant_id="asst_retell",
                livekit_max_concurrency=4,
                provider_was_provided=True,
            ),
        )
        creds = ProviderCredentials.objects.get(agent_version=agent_version)
        assert creds.provider_type == ProviderCredentials.ProviderType.RETELL
        assert creds.max_concurrency == 4

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
        assert (
            ProviderCredentials.objects.filter(agent_version=agent_version).count() == 1
        )

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
        assert not ProviderCredentials.objects.filter(
            agent_version=agent_version
        ).exists()

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


@pytest.mark.parametrize("snapshot_provider", [True, False], ids=["authored", "legacy"])
@pytest.mark.parametrize("key", [None, "********", "synthetic-key-B"])
def test_sync_explicit_provider_change_distinguishes_bland_encoding(
    agent_version, snapshot_provider, key
):
    # The writer has already changed the definition to Vapi. For old snapshots
    # without a provider, it supplies the pre-write provider as server context.
    agent = agent_version.agent_definition
    agent.api_key = "synthetic-stale-definition-key"
    agent.save(update_fields=["api_key"])
    before = {"assistant_id": "assistant-A", "description": "preserved"}
    if snapshot_provider:
        before["provider"] = "bland"
    AgentVersion.objects.filter(pk=agent_version.pk).update(
        configuration_snapshot=before
    )
    credentials = ProviderCredentials.objects.create(
        agent_version=agent_version,
        provider_type="vapi",  # Bland's migration-0071 storage encoding
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    data = ProviderCredentialsInput(
        provider="vapi",
        # An authored snapshot must win even over divergent writer context.
        previous_provider="vapi" if snapshot_provider else "bland",
        provider_was_provided=True,
        api_key=key,
    )
    ciphertext = credentials.api_key
    if key != "synthetic-key-B":
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError, match="new API key is required"):
            sync_provider_credentials(agent_version, data)
        agent_version.refresh_from_db()
        credentials.refresh_from_db()
        agent.refresh_from_db()
        assert agent_version.configuration_snapshot == before
        assert credentials.api_key == ciphertext
        assert credentials.assistant_id == "assistant-A"
        assert agent.api_key == "synthetic-stale-definition-key"
    else:
        sync_provider_credentials(agent_version, data)
        agent_version.refresh_from_db()
        credentials.refresh_from_db()
        assert credentials.provider_type == "vapi"
        assert credentials.get_api_key() == key
        assert credentials.assistant_id == ""
        assert agent_version.configuration_snapshot == {
            **before,
            "provider": "vapi",
            "assistant_id": "",
        }


def test_sync_omitted_provider_keeps_authored_bland_identity(agent_version):
    before = {
        "provider": "bland",
        "assistant_id": "assistant-A",
        "description": "preserved",
    }
    AgentVersion.objects.filter(pk=agent_version.pk).update(
        configuration_snapshot=before
    )
    credentials = ProviderCredentials.objects.create(
        agent_version=agent_version,
        provider_type="vapi",
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    sync_provider_credentials(
        agent_version,
        # This family may come from a harness or a definition now set to Vapi.
        # Without an explicit provider write it must not re-author the snapshot.
        ProviderCredentialsInput(provider="vapi", api_key="synthetic-key-B"),
    )
    agent_version.refresh_from_db()
    credentials.refresh_from_db()
    assert agent_version.configuration_snapshot == before
    assert credentials.get_api_key() == "synthetic-key-B"
    assert credentials.assistant_id == "assistant-A"


@pytest.mark.parametrize("assistant", ["assistant-B", "  assistant-B  ", ""])
def test_sync_updates_snapshot_from_saved_identity(agent_version, assistant):
    # The caller can hold a stale version; unrelated snapshot changes must survive.
    snapshot = {"assistant_id": "assistant-A", "description": "latest description"}
    AgentVersion.objects.filter(pk=agent_version.pk).update(
        configuration_snapshot=snapshot
    )
    ProviderCredentials.objects.create(
        agent_version=agent_version,
        provider_type="vapi",
        assistant_id="assistant-A",
        api_key="synthetic-key-A",
    )
    sync_provider_credentials(
        agent_version,
        ProviderCredentialsInput(
            provider="vapi", assistant_id=assistant, api_key="synthetic-key-B"
        ),
    )
    agent_version.refresh_from_db()
    credentials = ProviderCredentials.objects.get(agent_version=agent_version)
    assert credentials.assistant_id == assistant.strip()
    assert agent_version.configuration_snapshot == {
        **snapshot,
        "assistant_id": credentials.assistant_id,
    }
    assert credentials.get_api_key() == "synthetic-key-B"
    assert "api_key" not in agent_version.configuration_snapshot


@pytest.mark.parametrize("provider", ["vapi", "retell"])
@pytest.mark.parametrize("key", [None, "********", "synthetic-key-B"])
@pytest.mark.parametrize(
    "identity,expected",
    [({}, "assistant-A"), ({"assistant_id": None}, ""), ({"assistant_id": ""}, "")],
    ids=["omitted", "explicit-null", "empty-string"],
)
def test_sync_distinguishes_null_identity_from_omission(
    agent_version, provider, key, identity, expected
):
    snapshot = {"assistant_id": "assistant-A", "description": "preserved"}
    AgentVersion.objects.filter(pk=agent_version.pk).update(
        configuration_snapshot=snapshot
    )
    credentials = ProviderCredentials.objects.create(
        agent_version=agent_version,
        provider_type=provider,
        assistant_id="assistant-A",
        api_key="synthetic-key-A",
    )
    sync_provider_credentials(
        agent_version,
        ProviderCredentialsInput(provider=provider, api_key=key, **identity),
    )
    agent_version.refresh_from_db()
    credentials.refresh_from_db()
    assert credentials.assistant_id == expected
    assert agent_version.configuration_snapshot == {
        **snapshot,
        "assistant_id": expected,
    }
    assert credentials.get_api_key() == (
        "synthetic-key-B" if key == "synthetic-key-B" else "synthetic-key-A"
    )


@pytest.mark.parametrize("assistant", ["assistant-B", None, ""])
def test_sync_snapshot_failure_rolls_back_credentials(agent_version, assistant):
    from django.db import connection

    credentials = ProviderCredentials.objects.create(
        agent_version=agent_version,
        provider_type="vapi",
        assistant_id="assistant-A",
        api_key="synthetic-key-A",
    )
    original_key = credentials.api_key
    original_snapshot = agent_version.configuration_snapshot.copy()

    def fail_snapshot_write(execute, sql, params, many, context):
        if sql.startswith('UPDATE "simulate_agent_version"'):
            raise RuntimeError("Synthetic snapshot write failure")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(fail_snapshot_write):
        with pytest.raises(RuntimeError, match="Synthetic snapshot write failure"):
            sync_provider_credentials(
                agent_version,
                ProviderCredentialsInput(
                    provider="vapi",
                    assistant_id=assistant,
                    api_key="synthetic-key-B",
                ),
            )
    credentials.refresh_from_db()
    agent_version.refresh_from_db()
    assert credentials.api_key == original_key
    assert credentials.assistant_id == "assistant-A"
    assert agent_version.configuration_snapshot == original_snapshot


@pytest.mark.parametrize("ambient", ["default", "unrelated"])
def test_sync_locks_the_given_version_whatever_the_ambient_workspace(
    organization, workspace, user, ambient
):
    """The caller already resolved this version; the lock re-reads it by key only.

    Harness creation writes an agent in the job's workspace while the
    thread-local scope can name another one (here the user's default, as the
    ``user`` fixture binds, or an unrelated workspace).
    """
    target = Workspace.objects.create(
        name="Target", organization=organization, created_by=user
    )
    definition = AgentDefinition.no_workspace_objects.create(
        agent_name="Other workspace agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12345678902",
        inbound=True,
        description="Agent outside the ambient workspace",
        provider="vapi",
        organization=organization,
        workspace=target,
        languages=["en"],
    )
    version = definition.create_version(
        description="Initial version",
        commit_message="First version",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    assert version.workspace_id == target.id
    if ambient == "unrelated":
        set_workspace_context(
            workspace=Workspace.objects.create(
                name="Unrelated", organization=organization, created_by=user
            ),
            organization=organization,
            user=user,
        )
    # Precondition: the ambient scope hides the version from the scoped manager.
    assert not AgentVersion.objects.filter(pk=version.pk).exists()

    sync_provider_credentials(
        version,
        ProviderCredentialsInput(
            provider="vapi",
            api_key="synthetic-key-B",
            assistant_id="assistant-B",
            provider_was_provided=True,
        ),
    )

    credentials = ProviderCredentials.no_workspace_objects.get(agent_version=version)
    stored = AgentVersion.no_workspace_objects.get(pk=version.pk)
    assert credentials.get_api_key() == "synthetic-key-B"
    assert credentials.assistant_id == "assistant-B"
    assert stored.configuration_snapshot["assistant_id"] == "assistant-B"
    assert version.configuration_snapshot["assistant_id"] == "assistant-B"


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

    @pytest.mark.parametrize("provider", ["vapi", "retell", "livekit"])
    @pytest.mark.parametrize("for_save", [False, True])
    def test_falls_back_to_legacy_credentials(self, agent_version, provider, for_save):
        agent = agent_version.agent_definition
        agent_version.configuration_snapshot["provider"] = provider
        agent_version.save(update_fields=["configuration_snapshot"])
        ProviderCredentials.objects.create(
            agent_definition=agent,
            provider_type=provider,
            api_key="sk-legacy-fallback-key",
        )
        assert (
            resolve_api_key_for_version(agent_version, for_save=for_save)
            == "sk-legacy-fallback-key"
        )

    @pytest.mark.parametrize("provider", ["vapi", "retell", "livekit", "bland"])
    @pytest.mark.parametrize("key", ["", "synthetic-key-B"])
    @pytest.mark.parametrize("for_save", [False, True])
    def test_existing_row_authority_keeps_other_providers_unchanged(
        self, agent_version, provider, key, for_save
    ):
        agent = agent_version.agent_definition
        agent.provider = provider
        agent.api_key = "synthetic-definition-key"
        agent.save(update_fields=["provider", "api_key"])
        agent_version.configuration_snapshot["provider"] = provider
        agent_version.save(update_fields=["configuration_snapshot"])
        storage_provider = "vapi" if provider == "bland" else provider
        ProviderCredentials.objects.create(
            agent_definition=agent,
            provider_type=storage_provider,
            api_key="synthetic-key-A",
        )
        ProviderCredentials.objects.create(
            agent_version=agent_version,
            provider_type=storage_provider,
            api_key=key,
        )
        # Empty Vapi rows select the definition on read, but must remain empty
        # on save. Retell/LiveKit retain their existing legacy fallback.
        fallback = "synthetic-key-A"
        if provider == "vapi":
            fallback = None if for_save else "synthetic-definition-key"
        assert resolve_api_key_for_version(agent_version, for_save=for_save) == (
            key or fallback
        )

    @pytest.mark.parametrize("for_save", [False, True])
    def test_nonempty_vapi_ciphertext_with_empty_plaintext_is_authoritative(
        self, agent_version, for_save
    ):
        from integrations.services.credentials import CredentialManager

        agent = agent_version.agent_definition
        agent.api_key = "synthetic-definition-key"
        agent.save(update_fields=["api_key"])
        ciphertext = "enc::" + CredentialManager.encrypt({"v": ""}).decode("utf-8")
        ProviderCredentials.objects.create(
            agent_version=agent_version, provider_type="vapi", api_key=ciphertext
        )
        ProviderCredentials.objects.create(
            agent_definition=agent_version.agent_definition,
            provider_type="vapi",
            api_key="synthetic-legacy-key",
        )
        assert resolve_api_key_for_version(agent_version, for_save=for_save) is None

    @pytest.mark.parametrize("for_save", [False, True])
    def test_unreadable_vapi_ciphertext_does_not_fall_back(
        self, agent_version, for_save
    ):
        agent = agent_version.agent_definition
        agent.api_key = "synthetic-definition-key"
        agent.save(update_fields=["api_key"])
        ProviderCredentials.objects.create(
            agent_version=agent_version, provider_type="vapi", api_key="enc::invalid"
        )
        ProviderCredentials.objects.create(
            agent_definition=agent,
            provider_type="vapi",
            api_key="synthetic-legacy-key",
        )
        with pytest.raises(ValueError, match="Failed to decrypt credentials"):
            resolve_api_key_for_version(agent_version, for_save=for_save)


@pytest.mark.parametrize("location", ["version", "legacy"])
@pytest.mark.parametrize("source", ["raw-empty", "empty-ciphertext", "wrong-provider"])
def test_vapi_save_resolution_distinguishes_fallback_from_closed_source(
    agent_version, location, source
):
    from integrations.services.credentials import CredentialManager
    from simulate.services.agent_definition import (
        resolve_provider_configuration_for_version,
    )

    agent = agent_version.agent_definition
    agent.api_key = "synthetic-key-B"
    agent.assistant_id = "assistant-B"
    agent.save(update_fields=["api_key", "assistant_id"])
    key = (
        "enc::" + CredentialManager.encrypt({"v": ""}).decode("utf-8")
        if source == "empty-ciphertext"
        else ""
    )
    credentials = ProviderCredentials.objects.create(
        **(
            {"agent_version": agent_version}
            if location == "version"
            else {"agent_definition": agent}
        ),
        provider_type="retell" if source == "wrong-provider" else "vapi",
        api_key=key,
        assistant_id="assistant-A",
    )
    ciphertext = credentials.api_key
    for_save = resolve_provider_configuration_for_version(agent_version, for_save=True)
    assert for_save.api_key is None
    assert for_save.uses_definition_fallback is (source == "raw-empty")
    effective = resolve_provider_configuration_for_version(agent_version)
    assert effective.api_key == ("synthetic-key-B" if source == "raw-empty" else None)
    assert (
        effective.assistant_id
        == {
            "raw-empty": "assistant-B",
            "empty-ciphertext": "assistant-A",
            "wrong-provider": None,
        }[source]
    )
    credentials.refresh_from_db()
    assert credentials.api_key == ciphertext
    assert credentials.agent_version_id == (
        agent_version.pk if location == "version" else None
    )


@pytest.mark.parametrize("legacy_provider", ["vapi", "bland"])
@pytest.mark.parametrize("for_save", [False, True])
def test_bland_legacy_fallback_checks_migrated_owners_authored_provider(
    agent_version, legacy_provider, for_save
):
    agent = agent_version.agent_definition
    agent.provider = "bland"
    agent.save(update_fields=["provider"])
    agent_version.configuration_snapshot.update(
        provider="bland", assistant_id="pathway-A"
    )
    agent_version.save(update_fields=["configuration_snapshot"])
    ProviderCredentials.objects.create(
        agent_version=agent_version, provider_type="vapi", api_key=""
    )
    owner = agent.create_version(
        description="Synthetic legacy owner", commit_message="owner"
    )
    owner.configuration_snapshot.update(
        provider=legacy_provider, assistant_id="legacy-owner"
    )
    owner.save(update_fields=["configuration_snapshot"])
    owner_snapshot = owner.configuration_snapshot.copy()
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        agent_version=owner,
        provider_type="vapi",
        api_key="synthetic-legacy-key",
        assistant_id="legacy-owner",
    )
    ciphertext = legacy.api_key
    assert resolve_api_key_for_version(agent_version, for_save=for_save) == (
        "synthetic-legacy-key" if legacy_provider == "bland" else None
    )
    owner.refresh_from_db()
    legacy.refresh_from_db()
    assert owner.configuration_snapshot == owner_snapshot
    assert legacy.api_key == ciphertext
    assert legacy.agent_version_id == owner.pk
    assert legacy.agent_definition_id == agent.pk


# ============================================================================
# Tests for resolve_stored_api_key()
# ============================================================================


class TestResolveStoredApiKey:
    def test_returns_none_for_none_org(self):
        assert resolve_stored_api_key(organization=None) is None

    def test_returns_none_when_agent_not_found(self, organization):
        assert (
            resolve_stored_api_key(
                organization=organization,
                agent_id="00000000-0000-0000-0000-000000000000",
            )
            is None
        )

    def test_returns_key_when_no_masked_value(self, organization, agent_definition):
        version = agent_definition.create_version(
            description="v1",
            commit_message="v1",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        ProviderCredentials.objects.create(
            agent_version=version,
            provider_type=ProviderCredentials.ProviderType.VAPI,
            api_key="sk-real-key-12345",
        )
        key = resolve_stored_api_key(
            organization=organization, agent_id=str(agent_definition.id)
        )
        assert key == "sk-real-key-12345"

    def test_returns_key_when_masked_value_matches(
        self, organization, agent_definition
    ):
        version = agent_definition.create_version(
            description="v1",
            commit_message="v1",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        ProviderCredentials.objects.create(
            agent_version=version,
            provider_type=ProviderCredentials.ProviderType.VAPI,
            api_key="sk-real-key-12345",
        )
        key = resolve_stored_api_key(
            organization=organization,
            agent_id=str(agent_definition.id),
            masked_value="sk-r...2345",
        )
        assert key == "sk-real-key-12345"

    def test_returns_none_when_masked_value_mismatches(
        self, organization, agent_definition
    ):
        version = agent_definition.create_version(
            description="v1",
            commit_message="v1",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        ProviderCredentials.objects.create(
            agent_version=version,
            provider_type=ProviderCredentials.ProviderType.VAPI,
            api_key="sk-real-key-12345",
        )
        key = resolve_stored_api_key(
            organization=organization,
            agent_id=str(agent_definition.id),
            masked_value="sk-r...9999",
        )
        assert key is None

    def test_returns_none_when_no_key_and_masked_value_provided(
        self, organization, agent_definition
    ):
        agent_definition.create_version(
            description="v1",
            commit_message="v1",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        key = resolve_stored_api_key(
            organization=organization,
            agent_id=str(agent_definition.id),
            masked_value="sk-r...2345",
        )
        assert key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "provider,row_provider,key,expected_mask,expected_assistant",
    [
        ("vapi", "vapi", "", "synt...ey-B", "assistant-B"),
        ("vapi", "vapi", "synthetic-key-C", "synt...ey-C", "assistant-C"),
        ("vapi", "vapi", "empty-ciphertext", None, "assistant-C"),
        ("vapi", "retell", "", None, ""),
        ("vapi", "retell", "synthetic-key-C", None, ""),
        ("retell", "retell", "", "synt...ey-A", "assistant-S"),
        ("livekit", "livekit", "", "synt...ey-A", "assistant-S"),
        ("retell", "vapi", "", "synt...ey-A", "assistant-S"),
        ("livekit", "vapi", "", "synt...ey-A", "assistant-S"),
        ("bland", "vapi", "", "synt...ey-A", "assistant-S"),
        ("bland", "vapi", "synthetic-key-C", "synt...ey-C", "assistant-S"),
        ("vapi", None, "", "synt...ey-A", "assistant-A"),
    ],
)
def test_version_response_uses_effective_vapi_pair_only(
    agent_version, provider, row_provider, key, expected_mask, expected_assistant
):
    from integrations.services.credentials import CredentialManager
    from simulate.serializers.response.agent_version import (
        AgentVersionResponseSerializer,
    )

    agent = agent_version.agent_definition
    agent.provider = provider
    agent.api_key = "synthetic-key-B"
    agent.assistant_id = "assistant-B"
    agent.save(update_fields=["provider", "api_key", "assistant_id"])
    agent_version.configuration_snapshot = {
        **agent_version.configuration_snapshot,
        "provider": provider,
        "assistant_id": "assistant-S",
    }
    agent_version.save(update_fields=["configuration_snapshot"])
    stored_snapshot = agent_version.configuration_snapshot.copy()
    ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi" if provider == "bland" else provider,
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    if key == "empty-ciphertext":
        key = "enc::" + CredentialManager.encrypt({"v": ""}).decode("utf-8")
    if row_provider:
        credentials = ProviderCredentials.objects.create(
            agent_version=agent_version,
            provider_type=row_provider,
            api_key=key,
            assistant_id="assistant-C",
        )
        stored_key = credentials.api_key

    data = AgentVersionResponseSerializer(agent_version).data
    assert data["api_key"] == expected_mask
    assert data["configuration_snapshot"]["assistant_id"] == expected_assistant
    for secret in ("synthetic-key-A", "synthetic-key-B", "synthetic-key-C"):
        assert secret not in str(data)
    assert agent_version.configuration_snapshot == stored_snapshot
    agent_version.refresh_from_db()
    assert agent_version.configuration_snapshot == stored_snapshot
    if row_provider:
        credentials.refresh_from_db()
        assert credentials.api_key == stored_key
        assert credentials.assistant_id == "assistant-C"


# ============================================================================
# Tests for AgentVersionResponseSerializer LiveKit credential surfacing
# ============================================================================


@pytest.mark.integration
class TestAgentVersionResponseLiveKitCreds:
    """The edit form reads LiveKit creds from the version-detail snapshot.

    ``create_snapshot`` deliberately omits secrets, so the serializer surfaces
    display-only masked values without persisting them.
    """

    def test_livekit_creds_surfaced_masked_in_snapshot(self, agent_version):
        from simulate.serializers.response.agent_version import (
            AgentVersionResponseSerializer,
        )

        sync_provider_credentials(
            agent_version,
            ProviderCredentialsInput(
                provider="livekit",
                livekit_api_key="APIabcdef123456",
                livekit_api_secret="lk-secret-value",
                livekit_url="https://livekit.example.com",
                livekit_agent_name="test-agent",
            ),
        )
        # The create/edit views re-snapshot after credentials sync so the
        # non-secret LiveKit fields land in the snapshot.
        agent_version.configuration_snapshot = agent_version.create_snapshot(
            commit_message=""
        )
        agent_version.save(update_fields=["configuration_snapshot"])

        data = AgentVersionResponseSerializer(agent_version).data
        snapshot = data["configuration_snapshot"]

        assert snapshot["livekit_url"] == "https://livekit.example.com"
        assert snapshot["livekit_agent_name"] == "test-agent"
        # Key is masked, secret is the "********" sentinel — never plaintext.
        assert snapshot["livekit_api_key"] not in ("", "APIabcdef123456")
        assert is_masked(snapshot["livekit_api_key"])
        assert snapshot["livekit_api_secret"] == "********"
        # The persisted snapshot must stay free of secrets (refactor invariant).
        agent_version.refresh_from_db()
        assert "livekit_api_key" not in agent_version.configuration_snapshot
        assert "livekit_api_secret" not in agent_version.configuration_snapshot
