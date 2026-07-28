import json
import uuid

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from tfc.constants.roles import OrganizationRoles
from tracer.models.observability_provider import (
    ObservabilityProvider,
    ProviderChoices,
)
from tracer.models.project import Project

PROVIDERS_PATH = "/tracer/observability-provider/"
WEBHOOK_PATH = "/tracer/webhook/"


def result(response):
    body = response.json()
    return body.get("result", body)


def create_webhook_agent_fixture(
    *,
    organization,
    workspace,
    assistant_id,
    api_key,
    enabled=True,
):
    project = Project.no_workspace_objects.create(
        name=f"Webhook Project {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    provider = ObservabilityProvider.no_workspace_objects.create(
        project=project,
        provider=ProviderChoices.RETELL,
        enabled=enabled,
        organization=organization,
        workspace=workspace,
        metadata={"assistant_id": assistant_id},
    )
    agent_definition = AgentDefinition.no_workspace_objects.create(
        agent_name=f"Webhook Agent {uuid.uuid4().hex[:8]}",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=False,
        description="Disposable webhook agent fixture",
        assistant_id=assistant_id,
        provider=ProviderChoices.RETELL,
        organization=organization,
        workspace=workspace,
        observability_provider=provider,
    )
    AgentVersion.no_workspace_objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        version_number=1,
        status=AgentVersion.StatusChoices.ACTIVE,
        description="Webhook fixture version",
        commit_message="webhook fixture",
        configuration_snapshot={
            "assistant_id": assistant_id,
            "provider": ProviderChoices.RETELL,
        },
    )
    ProviderCredentials.no_workspace_objects.create(
        agent_version=agent_definition.latest_version,
        provider_type=ProviderCredentials.ProviderType.RETELL,
        api_key=api_key,
        assistant_id=assistant_id,
    )
    return agent_definition


@pytest.mark.django_db
def test_observability_provider_crud_and_project_workspace(
    auth_client,
    organization,
    workspace,
):
    project_name = f"provider-project-{uuid.uuid4().hex[:8]}"

    create_response = auth_client.post(
        PROVIDERS_PATH,
        data={
            "project_name": project_name,
            "provider": ProviderChoices.RETELL,
            "enabled": True,
            "metadata": {"assistant_id": "assistant-local-test"},
        },
        format="json",
    )

    assert create_response.status_code == status.HTTP_200_OK
    created = result(create_response)
    assert created["provider"] == ProviderChoices.RETELL
    assert created["enabled"] is True
    assert created["workspace"] == str(workspace.id)
    assert created["organization"] == str(organization.id)

    project = Project.no_workspace_objects.get(id=created["project"])
    assert project.name == project_name
    assert project.trace_type == "observe"
    assert project.organization_id == organization.id
    assert project.workspace_id == workspace.id

    list_response = auth_client.get(
        PROVIDERS_PATH,
        data={"project_id": str(project.id), "page_number": 0, "page_size": 10},
    )
    assert list_response.status_code == status.HTTP_200_OK
    list_payload = result(list_response)
    assert list_payload["metadata"]["total_count"] == 1
    assert [provider["id"] for provider in list_payload["providers"]] == [created["id"]]

    detail_response = auth_client.get(f"{PROVIDERS_PATH}{created['id']}/")
    assert detail_response.status_code == status.HTTP_200_OK
    detail = result(detail_response)
    assert detail["id"] == created["id"]
    assert detail["project"] == str(project.id)

    patch_response = auth_client.patch(
        f"{PROVIDERS_PATH}{created['id']}/",
        data={"enabled": False, "metadata": {"assistant_id": "assistant-updated"}},
        format="json",
    )
    assert patch_response.status_code == status.HTTP_200_OK
    patched = result(patch_response)
    assert patched["enabled"] is False
    assert patched["metadata"] == {"assistant_id": "assistant-updated"}

    immutable_project_response = auth_client.put(
        f"{PROVIDERS_PATH}{created['id']}/",
        data={
            "project": str(uuid.uuid4()),
            "provider": ProviderChoices.RETELL,
        },
        format="json",
    )
    assert immutable_project_response.status_code == status.HTTP_400_BAD_REQUEST

    delete_response = auth_client.delete(f"{PROVIDERS_PATH}{created['id']}/")
    assert delete_response.status_code == status.HTTP_200_OK
    provider = ObservabilityProvider.all_objects.get(id=created["id"])
    assert provider.deleted is True


@pytest.mark.django_db
def test_observability_provider_create_uses_active_non_default_workspace(
    api_client,
    organization,
    user,
):
    other_workspace = Workspace.no_workspace_objects.create(
        name="Provider Setup Workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    api_client.force_authenticate(user=user)
    api_client.set_workspace(other_workspace)

    create_response = api_client.post(
        PROVIDERS_PATH,
        data={
            "project_name": f"provider-non-default-{uuid.uuid4().hex[:8]}",
            "provider": ProviderChoices.VAPI,
            "enabled": True,
            "metadata": {"assistant_id": "assistant-workspace-test"},
        },
        format="json",
    )

    assert create_response.status_code == status.HTTP_200_OK
    created = result(create_response)
    project = Project.no_workspace_objects.get(id=created["project"])
    assert created["workspace"] == str(other_workspace.id)
    assert project.workspace_id == other_workspace.id


@pytest.mark.django_db
def test_observability_provider_routes_hide_out_of_scope_rows(
    auth_client,
    organization,
    user,
    workspace,
):
    other_workspace = Workspace.no_workspace_objects.create(
        name="Provider Other Workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    other_workspace_project = Project.no_workspace_objects.create(
        name="Provider Other Workspace Project",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    other_workspace_provider = ObservabilityProvider.no_workspace_objects.create(
        project=other_workspace_project,
        provider=ProviderChoices.RETELL,
        organization=organization,
        workspace=other_workspace,
        metadata={"assistant_id": "other-workspace"},
    )

    other_organization = Organization.objects.create(name="Provider Other Org")
    other_user = User.objects.create_user(
        email="provider-other@example.com",
        password="testpassword123",
        name="Provider Other User",
        organization=other_organization,
        organization_role=OrganizationRoles.OWNER,
    )
    other_org_workspace = Workspace.no_workspace_objects.create(
        name="Provider Other Org Workspace",
        organization=other_organization,
        is_default=True,
        is_active=True,
        created_by=other_user,
    )
    other_org_project = Project.no_workspace_objects.create(
        name="Provider Other Org Project",
        organization=other_organization,
        workspace=other_org_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    other_org_provider = ObservabilityProvider.no_workspace_objects.create(
        project=other_org_project,
        provider=ProviderChoices.VAPI,
        organization=other_organization,
        workspace=other_org_workspace,
        metadata={"assistant_id": "other-org"},
    )

    list_response = auth_client.get(PROVIDERS_PATH)
    assert list_response.status_code == status.HTTP_200_OK
    assert result(list_response)["providers"] == []

    for project, provider in (
        (other_workspace_project, other_workspace_provider),
        (other_org_project, other_org_provider),
    ):
        filtered_list_response = auth_client.get(
            PROVIDERS_PATH,
            data={"project_id": str(project.id)},
        )
        assert filtered_list_response.status_code == status.HTTP_200_OK
        assert result(filtered_list_response)["providers"] == []

        detail_response = auth_client.get(f"{PROVIDERS_PATH}{provider.id}/")
        assert detail_response.status_code == status.HTTP_400_BAD_REQUEST

        patch_response = auth_client.patch(
            f"{PROVIDERS_PATH}{provider.id}/",
            data={"enabled": False},
            format="json",
        )
        assert patch_response.status_code == status.HTTP_400_BAD_REQUEST

        delete_response = auth_client.delete(f"{PROVIDERS_PATH}{provider.id}/")
        assert delete_response.status_code == status.HTTP_400_BAD_REQUEST

    other_workspace_provider.refresh_from_db()
    other_org_provider.refresh_from_db()
    assert other_workspace_provider.deleted is False
    assert other_org_provider.deleted is False


@pytest.mark.django_db
def test_observability_provider_verify_actions(auth_client, monkeypatch):
    calls = []

    def fake_verify_api_key(provider, api_key):
        calls.append(("api_key", provider, api_key))
        return 200

    def fake_verify_assistant_id(provider, assistant_id, api_key):
        calls.append(("assistant_id", provider, assistant_id, api_key))
        return 400

    monkeypatch.setattr(
        "tracer.views.observability_provider.ObservabilityService.verify_api_key",
        fake_verify_api_key,
    )
    monkeypatch.setattr(
        "tracer.views.observability_provider.ObservabilityService.verify_assistant_id",
        fake_verify_assistant_id,
    )

    invalid_provider_response = auth_client.post(
        f"{PROVIDERS_PATH}verify_api_key/",
        data={"provider": "not-a-provider", "api_key": "unused"},
        format="json",
    )
    assert invalid_provider_response.status_code == status.HTTP_400_BAD_REQUEST
    assert calls == []

    api_key_response = auth_client.post(
        f"{PROVIDERS_PATH}verify_api_key/",
        data={"provider": ProviderChoices.RETELL, "api_key": "test-key"},
        format="json",
    )
    assert api_key_response.status_code == status.HTTP_200_OK

    assistant_response = auth_client.post(
        f"{PROVIDERS_PATH}verify_assistant_id/",
        data={
            "provider": ProviderChoices.RETELL,
            "api_key": "test-key",
            "assistant_id": "assistant-test",
        },
        format="json",
    )
    assert assistant_response.status_code == status.HTTP_400_BAD_REQUEST
    assert calls == [
        ("api_key", ProviderChoices.RETELL, "test-key"),
        ("assistant_id", ProviderChoices.RETELL, "assistant-test", "test-key"),
    ]


@pytest.mark.django_db
def test_webhook_signed_retell_callback_dispatches_for_enabled_agent(
    api_client,
    organization,
    workspace,
    monkeypatch,
):
    assistant_id = f"retell-assistant-{uuid.uuid4().hex[:8]}"
    api_key = "retell-test-secret"
    agent_definition = create_webhook_agent_fixture(
        organization=organization,
        workspace=workspace,
        assistant_id=assistant_id,
        api_key=api_key,
    )
    payload = {
        "event": "call_analyzed",
        "interaction_type": "voice",
        "call": {
            "agent_id": assistant_id,
            "call_id": f"call-{uuid.uuid4().hex[:8]}",
        },
    }
    dispatched = []
    signature = retell_signature(payload, api_key)

    def fake_delay(**kwargs):
        dispatched.append(kwargs)

    monkeypatch.setattr(
        "tracer.views.observability_provider.normalize_and_store_logs.delay",
        fake_delay,
    )

    response = api_client.post(
        WEBHOOK_PATH,
        payload,
        format="json",
        HTTP_X_RETELL_SIGNATURE=signature,
    )

    assert response.status_code == status.HTTP_200_OK
    assert "Processed: 1" in result(response)
    assert dispatched == [
        {
            "body": payload,
            "agent_definition_id": agent_definition.id,
        }
    ]


@pytest.mark.django_db
def test_webhook_signed_retell_callback_runs_inline_when_dispatch_unavailable(
    api_client,
    organization,
    workspace,
    monkeypatch,
):
    assistant_id = f"retell-assistant-{uuid.uuid4().hex[:8]}"
    api_key = "retell-test-secret"
    agent_definition = create_webhook_agent_fixture(
        organization=organization,
        workspace=workspace,
        assistant_id=assistant_id,
        api_key=api_key,
    )
    payload = {
        "event": "call_analyzed",
        "interaction_type": "voice",
        "call": {
            "agent_id": assistant_id,
            "call_id": f"call-{uuid.uuid4().hex[:8]}",
        },
    }
    calls = []
    signature = retell_signature(payload, api_key)

    def failing_delay(**kwargs):
        calls.append(("delay", kwargs))
        raise RuntimeError("temporal unavailable")

    def fake_run_sync(**kwargs):
        calls.append(("run_sync", kwargs))

    monkeypatch.setattr(
        "tracer.views.observability_provider.normalize_and_store_logs.delay",
        failing_delay,
    )
    monkeypatch.setattr(
        "tracer.views.observability_provider.normalize_and_store_logs.run_sync",
        fake_run_sync,
    )

    response = api_client.post(
        WEBHOOK_PATH,
        payload,
        format="json",
        HTTP_X_RETELL_SIGNATURE=signature,
    )

    expected_kwargs = {
        "body": payload,
        "agent_definition_id": agent_definition.id,
    }
    assert response.status_code == status.HTTP_200_OK
    assert "Processed: 1" in result(response)
    assert calls == [("delay", expected_kwargs), ("run_sync", expected_kwargs)]


@pytest.mark.django_db
def test_webhook_invalid_signature_rejects_without_dispatch(
    api_client,
    organization,
    workspace,
    monkeypatch,
):
    assistant_id = f"retell-assistant-{uuid.uuid4().hex[:8]}"
    create_webhook_agent_fixture(
        organization=organization,
        workspace=workspace,
        assistant_id=assistant_id,
        api_key="retell-test-secret",
    )
    payload = {
        "event": "call_analyzed",
        "interaction_type": "voice",
        "call": {
            "agent_id": assistant_id,
            "call_id": f"call-{uuid.uuid4().hex[:8]}",
        },
    }
    dispatched = []

    monkeypatch.setattr(
        "tracer.views.observability_provider.normalize_and_store_logs.delay",
        lambda **kwargs: dispatched.append(kwargs),
    )

    response = api_client.post(
        WEBHOOK_PATH,
        payload,
        format="json",
        HTTP_X_RETELL_SIGNATURE="bad-signature",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert result(response) == "Invalid webhook signature"
    assert dispatched == []


def retell_signature(payload, api_key):
    from retell.lib.webhook_auth import symmetric

    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return symmetric["sign"](body, api_key)


@pytest.mark.parametrize(
    "serializer_cls",
    ["VerifyApiKeyRequestSerializer", "VerifyAssistantIdRequestSerializer"],
)
@pytest.mark.parametrize(
    "provider",
    [
        ProviderChoices.BLAND,
        ProviderChoices.TWILIO,
        ProviderChoices.OTHERS,
        ProviderChoices.ELEVEN_LABS,
    ],
)
def test_verify_request_serializer_rejects_unsupported_providers(serializer_cls, provider):
    """The verify request schema only advertises VAPI/RETELL, so an unsupported
    provider is rejected at validation (discoverable to clients), not at runtime."""
    import tracer.serializers.observability_provider as ser

    s = getattr(ser, serializer_cls)(
        data={"provider": provider, "api_key": "x", "assistant_id": "y", "agent_id": "z"}
    )
    assert not s.is_valid()
    assert "provider" in s.errors


@pytest.mark.parametrize(
    "serializer_cls",
    ["VerifyApiKeyRequestSerializer", "VerifyAssistantIdRequestSerializer"],
)
@pytest.mark.parametrize("provider", [ProviderChoices.VAPI, ProviderChoices.RETELL])
def test_verify_request_serializer_accepts_supported_providers(serializer_cls, provider):
    import tracer.serializers.observability_provider as ser

    s = getattr(ser, serializer_cls)(data={"provider": provider, "api_key": "x"})
    assert s.is_valid(), s.errors
