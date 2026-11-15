"""Real authenticated save/preflight regressions for credential source selection.

All credentials and tenants are synthetic. Only external provider HTTP and the
Temporal activity context (in the shared prepare-call helper) are replaced.
"""

from unittest.mock import Mock

import pytest
from rest_framework.test import APIClient

from agentcc.services.credential_manager import mask_key
from simulate.models.agent_definition import AgentDefinition, ProviderCredentials
from simulate.models.agent_version import AgentVersion
from simulate.services.agent_definition import (
    resolve_api_key_for_version,
    resolve_api_secret_for_version,
    resolve_provider_configuration_for_version,
)
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.tests.test_vapi_inplace_edit import (
    _assert_no_configuration_writes,
    _configuration_state,
    _prepare_saved_version,
    seed_version,
    update_agent,
)
from tracer.tests.test_vapi_inplace_edit import vapi_setup as shared_vapi_setup

# Expose the existing fixture without shadowing an unused imported name in
# every test signature. This remains the same real-authentication fixture.
vapi_setup = shared_vapi_setup

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def save_version(client, agent, payload):
    return client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        {
            "commit_message": "Synthetic credential save",
            "observability_enabled": False,
            **payload,
        },
        format="json",
    )


@pytest.mark.parametrize("route", ["edit", "operations", "create", "create-source"])
@pytest.mark.parametrize(
    "old_provider,new_provider",
    [
        ("livekit", "livekit_bridge"),
        ("livekit_bridge", "livekit"),
        ("livekit", "livekit"),
    ],
)
@pytest.mark.parametrize("snapshot_provider", [True, False], ids=["authored", "legacy"])
@pytest.mark.parametrize("secrets", ["omitted", "masked"])
def test_livekit_alias_saves_preserve_selected_credentials(
    vapi_setup, route, old_provider, new_provider, snapshot_provider, secrets
):
    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, original, credentials = seed_version(provider, "")
    agent.provider = old_provider
    agent.save(update_fields=["provider"])
    credentials.provider_type = "livekit"
    credentials.api_key = "synthetic-livekit-key"
    credentials.api_secret = "synthetic-livekit-secret"
    credentials.server_url = "wss://synthetic.invalid"
    credentials.agent_name = "Synthetic LiveKit agent"
    credentials.config_json = {"synthetic": {"preserve": True}}
    credentials.max_concurrency = 3
    credentials.save()
    original.configuration_snapshot = original.create_snapshot(
        commit_message="Fixture"
    )
    if not snapshot_provider:
        original.configuration_snapshot.pop("provider")
    original.save(update_fields=["configuration_snapshot"])
    before = (
        credentials.api_key,
        credentials.api_secret,
        original.configuration_snapshot.copy(),
    )
    payload = {"provider": new_provider, "description": "Synthetic alias save"}
    if secrets == "masked":
        payload.update(
            livekit_api_key=mask_key("synthetic-livekit-key"),
            livekit_api_secret="********",
        )
        # The operations serializer does not accept agent-name edits. Exercise
        # its masked-key save without adding an unsupported request field.
        if route != "operations":
            payload["livekit_agent_name"] = "Synthetic renamed agent"
    if route == "create-source":
        payload["source_version_id"] = str(original.id)
    creating = route.startswith("create")
    saved = (
        save_version(client, agent, payload)
        if creating
        else update_agent(client, agent, payload, route)
    )
    assert saved.status_code == (201 if creating else 200), saved.data
    clear_workspace_context()
    agent.refresh_from_db()
    selected = agent.active_version
    selected_credentials = selected.credentials
    assert agent.provider == new_provider
    # Alias normalization is only for comparisons; authored routing is retained.
    assert selected.configuration_snapshot["provider"] == new_provider
    assert selected_credentials.provider_type == "livekit"
    assert (selected_credentials.api_key, selected_credentials.api_secret) == before[:2]
    assert (
        resolve_api_key_for_version(selected),
        resolve_api_secret_for_version(selected),
    ) == ("synthetic-livekit-key", "synthetic-livekit-secret")
    assert selected_credentials.server_url == "wss://synthetic.invalid"
    assert selected_credentials.agent_name == (
        "Synthetic renamed agent"
        if secrets == "masked" and route != "operations"
        else "Synthetic LiveKit agent"
    )
    assert selected_credentials.config_json == {"synthetic": {"preserve": True}}
    assert selected_credentials.max_concurrency == 3
    assert agent.api_key == "synthetic-key-A"
    assert "livekit_api_key" not in selected.configuration_snapshot
    assert "livekit_api_secret" not in selected.configuration_snapshot
    if creating:
        assert selected.pk != original.pk
        original.refresh_from_db()
        credentials.refresh_from_db()
        assert (
            credentials.api_key,
            credentials.api_secret,
            original.configuration_snapshot,
        ) == before
        # Reading a historical alias must not canonicalize its stored provider.
        with _assert_no_configuration_writes():
            historical = client.get(
                f"/simulate/agent-definitions/{agent.id}/versions/{original.id}/"
            )
        assert historical.status_code == 200, historical.data
        assert historical.data["configuration_snapshot"].get("provider") == (
            old_provider if snapshot_provider else None
        )
    for secret in ("synthetic-livekit-key", "synthetic-livekit-secret"):
        assert secret not in str(saved.data)


@pytest.mark.parametrize(
    "old_provider,new_provider",
    [
        ("livekit", "livekit"),
        ("livekit_bridge", "livekit_bridge"),
        ("livekit", "livekit_bridge"),
        ("livekit_bridge", "livekit"),
    ],
)
def test_livekit_create_replaces_unreadable_selected_credentials(
    vapi_setup, old_provider, new_provider
):
    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, original, credentials = seed_version(provider, "")
    agent.provider = old_provider
    agent.save(update_fields=["provider"])
    credentials.provider_type = "livekit"
    credentials.api_key = "enc::synthetic-invalid-key"
    credentials.api_secret = "enc::synthetic-invalid-secret"
    credentials.server_url = "wss://synthetic.invalid"
    credentials.agent_name = "Synthetic LiveKit agent"
    credentials.config_json = {"synthetic": {"preserve": True}}
    credentials.max_concurrency = 3
    credentials.save()
    original.configuration_snapshot = original.create_snapshot(
        commit_message="Fixture"
    )
    original.save(update_fields=["configuration_snapshot"])
    original_snapshot = original.configuration_snapshot.copy()
    source_row = ProviderCredentials.objects.filter(pk=credentials.pk).values().get()
    for decrypt in (credentials.get_api_key, credentials.get_api_secret):
        with pytest.raises(ValueError, match="Failed to decrypt credentials"):
            decrypt()

    new_key = "synthetic-livekit-replacement-key"
    new_secret = "synthetic-livekit-replacement-secret"
    # No generic api_key or source_version_id: active/latest selects the old row,
    # but neither old ciphertext is needed when both LiveKit secrets are fresh.
    saved = save_version(
        client,
        agent,
        {
            "provider": new_provider,
            "livekit_api_key": new_key,
            "livekit_api_secret": new_secret,
        },
    )
    assert saved.status_code == 201, saved.data
    response_snapshot = saved.data["version"]["configuration_snapshot"]
    assert saved.data["version"]["api_key"] == mask_key(new_key)
    assert response_snapshot["livekit_api_key"] == mask_key(new_key)
    assert response_snapshot["livekit_api_secret"] == "********"
    assert response_snapshot["livekit_url"] == "wss://synthetic.invalid"
    assert response_snapshot["livekit_agent_name"] == "Synthetic LiveKit agent"
    assert response_snapshot["livekit_config_json"] == {
        "synthetic": {"preserve": True}
    }
    assert response_snapshot["livekit_max_concurrency"] == 3
    for secret in (new_key, new_secret):
        assert secret not in str(saved.data)

    clear_workspace_context()
    agent.refresh_from_db()
    selected = agent.active_version
    selected_credentials = selected.credentials
    assert selected.pk != original.pk
    assert selected_credentials.pk != credentials.pk
    assert selected_credentials.provider_type == "livekit"
    assert selected_credentials.get_api_key() == new_key
    assert selected_credentials.get_api_secret() == new_secret
    assert selected_credentials.api_key.startswith("enc::")
    assert selected_credentials.api_secret.startswith("enc::")
    assert selected_credentials.server_url == "wss://synthetic.invalid"
    assert selected_credentials.agent_name == "Synthetic LiveKit agent"
    assert selected_credentials.config_json == {"synthetic": {"preserve": True}}
    assert selected_credentials.max_concurrency == 3
    assert selected.configuration_snapshot["provider"] == new_provider
    assert "livekit_api_key" not in selected.configuration_snapshot
    assert "livekit_api_secret" not in selected.configuration_snapshot
    assert agent.api_key == "synthetic-key-A"
    original.refresh_from_db()
    assert original.configuration_snapshot == original_snapshot
    assert (
        ProviderCredentials.objects.filter(pk=credentials.pk).values().get()
        == source_row
    )


@pytest.mark.parametrize("route", ["edit", "operations", "create"])
@pytest.mark.parametrize("key", [None, "********", "synthetic-key-C"])
def test_livekit_to_real_provider_still_requires_fresh_credentials(
    vapi_setup, route, key
):
    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, original, credentials = seed_version(provider, "")
    agent.provider = "livekit_bridge"
    agent.contact_number = "+16505550100"
    agent.save(update_fields=["provider", "contact_number"])
    credentials.provider_type = "livekit"
    credentials.api_secret = "synthetic-livekit-secret"
    credentials.save()
    original.configuration_snapshot["provider"] = "livekit_bridge"
    original.save(update_fields=["configuration_snapshot"])
    before = _configuration_state(agent)
    payload = {"provider": "retell", "assistant_id": "assistant-C"}
    if key is not None:
        payload["api_key"] = key
    replacing = key == "synthetic-key-C"
    if replacing:
        saved = (
            save_version(client, agent, payload)
            if route == "create"
            else update_agent(client, agent, payload, route)
        )
    else:
        with _assert_no_configuration_writes():
            saved = (
                save_version(client, agent, payload)
                if route == "create"
                else update_agent(client, agent, payload, route)
            )
    assert saved.status_code == (
        (201 if route == "create" else 200) if replacing else 400
    ), saved.data
    clear_workspace_context()
    if replacing:
        agent.refresh_from_db()
        selected = agent.active_version
        assert selected.configuration_snapshot["provider"] == "retell"
        assert selected.credentials.provider_type == "retell"
        assert selected.credentials.get_api_key() == "synthetic-key-C"
        assert selected.credentials.api_secret == ""
    else:
        assert "new API key is required" in str(saved.data)
        assert _configuration_state(agent) == before
    for secret in ("synthetic-key-A", "synthetic-key-C", "synthetic-livekit-secret"):
        assert secret not in str(saved.data)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("switch_route", ["edit", "operations", "create"])
@pytest.mark.parametrize("send_provider", [False, True])
def test_switch_then_default_source_omitted_create_keeps_new_provider_pair(
    vapi_setup, switch_route, send_provider
):
    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, original, _ = seed_version(provider, "assistant-A")
    agent.contact_number = "+16505550100"
    agent.save(update_fields=["contact_number"])
    original.configuration_snapshot = original.create_snapshot(
        commit_message="Fixture"
    )
    original.save(update_fields=["configuration_snapshot"])
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    legacy_state = (legacy.api_key, legacy.assistant_id, legacy.agent_version_id)
    switch_payload = {
        "provider": "bland",
        "api_key": "synthetic-key-C",
        "assistant_id": "pathway-C",
    }
    switched = (
        save_version(client, agent, switch_payload)
        if switch_route == "create"
        else update_agent(client, agent, switch_payload, switch_route)
    )
    assert switched.status_code == (
        201 if switch_route == "create" else 200
    ), switched.data
    clear_workspace_context()
    agent.refresh_from_db()
    switched_version = agent.active_version
    selected_before = (
        switched_version.configuration_snapshot.copy(),
        switched_version.credentials.api_key,
    )
    payload = {"description": "Synthetic omitted-key follow-up"}
    if send_provider:
        payload["provider"] = "bland"
    # No source_version_id, api_key, or assistant_id: active/latest is authority.
    saved = save_version(client, agent, payload)
    assert saved.status_code == 201, saved.data
    assert saved.data["version"]["api_key"] == "synt...ey-C"
    assert (
        saved.data["version"]["configuration_snapshot"]["assistant_id"] == "pathway-C"
    )
    clear_workspace_context()
    agent.refresh_from_db()
    selected = agent.active_version
    assert selected.pk != switched_version.pk
    configuration = resolve_provider_configuration_for_version(selected)
    assert (configuration.api_key, configuration.assistant_id) == (
        "synthetic-key-C",
        "pathway-C",
    )
    assert selected.credentials.pk != legacy.pk
    assert selected.credentials.get_api_key() == "synthetic-key-C"
    assert selected.configuration_snapshot["provider"] == "bland"
    prepared = _prepare_saved_version(provider, agent, selected)
    assert prepared.error is None
    assert prepared.client_provider == "bland"
    assert (prepared.client_api_key, prepared.client_assistant_id) == (
        "synthetic-key-C",
        "pathway-C",
    )
    switched_version.refresh_from_db()
    legacy.refresh_from_db()
    assert (
        switched_version.configuration_snapshot,
        switched_version.credentials.api_key,
    ) == selected_before
    assert legacy.agent_definition_id == agent.pk
    assert (legacy.api_key, legacy.assistant_id, legacy.agent_version_id) == legacy_state
    for representation in (switched.data, saved.data):
        for secret in ("synthetic-key-A", "synthetic-key-C"):
            assert secret not in str(representation)


@pytest.mark.parametrize("source", ["selected", "legacy", "definition", "raw-empty"])
def test_default_source_omitted_create_keeps_vapi_positive_sources(
    vapi_setup, source
):
    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, original, credentials = seed_version(
        provider, "assistant-S", with_credentials=source in ("selected", "raw-empty")
    )
    agent.api_key = "synthetic-key-B"
    agent.assistant_id = "assistant-B"
    agent.save(update_fields=["api_key", "assistant_id"])
    if source == "selected":
        credentials.api_key = "synthetic-key-C"
        credentials.assistant_id = "assistant-C"
        credentials.save()
    elif source == "raw-empty":
        credentials.api_key = ""
        credentials.save()
    legacy = None
    if source in ("selected", "legacy", "raw-empty"):
        legacy = ProviderCredentials.objects.create(
            agent_definition=agent,
            provider_type="vapi",
            api_key="synthetic-key-A",
            assistant_id="assistant-A",
        )
        legacy_ciphertext = legacy.api_key
    saved = save_version(client, agent, {"description": "Synthetic default source"})
    assert saved.status_code == 201, saved.data
    suffix = {
        "selected": "C",
        "legacy": "A",
        "definition": "B",
        "raw-empty": "B",
    }[source]
    assert saved.data["version"]["api_key"] == f"synt...ey-{suffix}"
    assert (
        saved.data["version"]["configuration_snapshot"]["assistant_id"]
        == f"assistant-{suffix}"
    )
    clear_workspace_context()
    agent.refresh_from_db()
    selected = agent.active_version
    configuration = resolve_provider_configuration_for_version(selected)
    assert (configuration.api_key, configuration.assistant_id) == (
        f"synthetic-key-{suffix}",
        f"assistant-{suffix}",
    )
    if source == "raw-empty":
        assert selected.credentials.api_key == ""
    if source == "definition":
        assert not ProviderCredentials.objects.filter(agent_version=selected).exists()
    if source in ("selected", "raw-empty"):
        legacy.refresh_from_db()
        assert legacy.api_key == legacy_ciphertext
        assert legacy.agent_version_id is None
    if source == "legacy":
        assert selected.credentials.pk == legacy.pk
    assert selected.pk != original.pk


@pytest.mark.parametrize("source", ["empty-ciphertext", "wrong-provider"])
@pytest.mark.parametrize("replacement", [False, True])
def test_default_source_omitted_create_keeps_unusable_vapi_authority_closed(
    vapi_setup, source, replacement
):
    from integrations.services.credentials import CredentialManager

    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, _, credentials = seed_version(provider, "assistant-A")
    credentials.api_key = "enc::" + CredentialManager.encrypt({"v": ""}).decode(
        "utf-8"
    )
    if source == "wrong-provider":
        credentials.provider_type = "retell"
    credentials.save()
    ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-L",
        assistant_id="assistant-L",
    )
    before = _configuration_state(agent)
    payload = {"description": "Synthetic source refusal"}
    if replacement:
        payload.update(api_key="synthetic-key-C", assistant_id="assistant-C")
        saved = save_version(client, agent, payload)
    else:
        with _assert_no_configuration_writes():
            saved = save_version(client, agent, payload)
    assert saved.status_code == (201 if replacement else 400), saved.data
    clear_workspace_context()
    if replacement:
        # Read a fresh ORM instance separately: the persisted replacement and
        # the immediate POST response must agree after legacy-row adoption.
        created = AgentVersion.objects.get(pk=saved.data["version"]["id"])
        assert created.credentials.get_api_key() == "synthetic-key-C"
        assert created.credentials.assistant_id == "assistant-C"
        assert saved.data["version"]["api_key"] == "synt...ey-C"
        assert (
            saved.data["version"]["configuration_snapshot"]["assistant_id"]
            == "assistant-C"
        )
    else:
        assert "selected source version has no usable API key" in str(saved.data)
        assert _configuration_state(agent) == before
    for secret in ("synthetic-key-C", "synthetic-key-L"):
        assert secret not in str(saved.data)


def check_preflights(client, agent, version, monkeypatch, *, explicit_source, key):
    upstream = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr(
        "tracer.services.observability_providers.requests.get", upstream
    )
    responses = []
    for action in ("verify_api_key", "verify_assistant_id"):
        payload = {
            "provider": "vapi",
            "agent_id": str(agent.id),
            "api_key": key,
        }
        if explicit_source:
            payload["source_version_id"] = str(version.id)
        if action == "verify_assistant_id":
            payload["assistant_id"] = "assistant-A"
        with _assert_no_configuration_writes():
            response = client.post(
                f"/tracer/observability-provider/{action}/", payload, format="json"
            )
        responses.append(response)
    return responses, upstream


@pytest.mark.parametrize(
    "null_agent", [True, False], ids=["legacy-agent", "default-agent"]
)
@pytest.mark.parametrize("explicit_source", [True, False])
def test_legacy_null_sources_are_visible_from_default_workspace(
    vapi_setup, monkeypatch, null_agent, explicit_source
):
    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, version, credentials = seed_version(provider, "assistant-A")
    if null_agent:
        AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(workspace=None)
    AgentVersion.no_workspace_objects.filter(pk=version.pk).update(workspace=None)
    credentials_ciphertext = credentials.api_key
    checks, upstream = check_preflights(
        client,
        agent,
        version,
        monkeypatch,
        explicit_source=explicit_source,
        key="synt...ey-A",
    )
    for response in checks:
        assert response.status_code == 200, response.data
    assert upstream.call_count == 2
    assert all(
        call.kwargs["headers"] == {"Authorization": "Bearer synthetic-key-A"}
        for call in upstream.call_args_list
    )
    assert upstream.call_args.args[0].endswith("/assistant-A")
    saved = save_version(
        client,
        agent,
        {
            "api_key": "synt...ey-A",
            **({"source_version_id": str(version.id)} if explicit_source else {}),
        },
    )
    assert saved.status_code == 201, saved.data
    assert saved.data["version"]["api_key"] == "synt...ey-A"
    assert (
        saved.data["version"]["configuration_snapshot"]["assistant_id"] == "assistant-A"
    )
    clear_workspace_context()
    agent = AgentDefinition.no_workspace_objects.get(pk=agent.pk)
    assert agent.workspace_id == (None if null_agent else provider.workspace_id)
    assert agent.active_version.credentials.get_api_key() == "synthetic-key-A"
    version.refresh_from_db()
    credentials.refresh_from_db()
    assert version.workspace_id is None
    assert credentials.api_key == credentials_ciphertext
    for representation in [*(response.data for response in checks), saved.data]:
        assert "synthetic-key-A" not in str(representation)


@pytest.mark.parametrize(
    "scope",
    [
        "nondefault-request",
        "nondefault-agent",
        "nondefault-null-version",
        "other-org",
    ],
)
@pytest.mark.parametrize("key", ["synt...ey-A", "synthetic-replacement-key"])
def test_legacy_null_sources_do_not_expand_tenant_visibility(
    vapi_setup, monkeypatch, user, scope, key
):
    from accounts.models import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import OrgApiKey
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    provider, client = vapi_setup
    agent, version, _ = seed_version(provider, "assistant-A")
    AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(workspace=None)
    AgentVersion.no_workspace_objects.filter(pk=version.pk).update(workspace=None)
    if scope == "other-org":
        other_org = Organization.objects.create(name="Synthetic other organization")
        AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(
            organization=other_org
        )
        AgentVersion.no_workspace_objects.filter(pk=version.pk).update(
            organization=other_org
        )
    else:
        other_workspace = Workspace.objects.create(
            name="Synthetic nondefault workspace",
            organization=provider.organization,
            created_by=user,
        )
        if scope in ("nondefault-agent", "nondefault-null-version"):
            AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(
                workspace=other_workspace
            )
        if scope in ("nondefault-request", "nondefault-null-version"):
            membership = OrganizationMembership.no_workspace_objects.get(
                user=user, organization=provider.organization
            )
            WorkspaceMembership.no_workspace_objects.create(
                user=user,
                workspace=other_workspace,
                organization_membership=membership,
                role=OrganizationRoles.WORKSPACE_ADMIN,
                level=Level.WORKSPACE_ADMIN,
                is_active=True,
            )
            api_key = OrgApiKey.no_workspace_objects.create(
                name="Synthetic nondefault key",
                type="user",
                user=user,
                organization=provider.organization,
                workspace=other_workspace,
            )
            client = APIClient()
            client.credentials(
                HTTP_X_API_KEY=api_key.api_key,
                HTTP_X_SECRET_KEY=api_key.secret_key,
                HTTP_X_WORKSPACE_ID=str(other_workspace.id),
            )
    clear_workspace_context()
    before = _configuration_state(agent)
    checks, upstream = check_preflights(
        client, agent, version, monkeypatch, explicit_source=True, key=key
    )
    for response in checks:
        assert response.status_code == 400, response.data
    with _assert_no_configuration_writes():
        saved = save_version(
            client,
            agent,
            {
                "source_version_id": str(version.id),
                "api_key": key,
                "description": "Must not be written",
            },
        )
    assert saved.status_code == (
        400 if scope == "nondefault-null-version" else 404
    ), saved.data
    upstream.assert_not_called()
    clear_workspace_context()
    assert _configuration_state(agent) == before
    for representation in [*(response.data for response in checks), saved.data]:
        assert "synthetic-key-A" not in str(representation)
        assert "synthetic-replacement-key" not in str(representation)


def test_legacy_null_sources_still_require_current_org_membership(
    vapi_setup, monkeypatch, user
):
    from accounts.models.organization_membership import OrganizationMembership

    provider, client = vapi_setup
    agent, version, _ = seed_version(provider, "assistant-A")
    AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(workspace=None)
    AgentVersion.no_workspace_objects.filter(pk=version.pk).update(workspace=None)
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=provider.organization
    ).update(is_active=False)
    before = _configuration_state(agent)
    checks, upstream = check_preflights(
        client,
        agent,
        version,
        monkeypatch,
        explicit_source=True,
        key="synt...ey-A",
    )
    for response in checks:
        assert response.status_code in (401, 403), response.data
    with _assert_no_configuration_writes():
        saved = save_version(client, agent, {"source_version_id": str(version.id)})
    assert saved.status_code in (401, 403), saved.data
    upstream.assert_not_called()
    clear_workspace_context()
    assert _configuration_state(agent) == before
