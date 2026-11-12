"""Launch OFF controls run without the discovery-enabled compatibility fixtures."""

from unittest.mock import patch

import pytest
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.tests import test_provider_credential_api as api_fixtures
from agentcc.tests.test_provider_model_rotation_r6 import NEW, OLD, SAFE
from agentcc.views import gateway
from agentcc.views import provider_credential as module
from django.test import override_settings
from integrations.services.credentials import CredentialManager
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

secondary_org_client = api_fixtures.secondary_org_client
secondary_org_context = api_fixtures.secondary_org_context


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("member", [False, True])
@pytest.mark.parametrize(
    "body",
    [
        {"api_key": NEW, "base_url": "https://caller.invalid", "api_format": "openai"},
        {"provider_name": "openai"},
        {"provider_name": "openai", "base_url": "https://caller.invalid"},
        {"api_key": NEW, "api_format": "systemone"},
    ],
)
def test_default_off_refuses_discovery_before_decrypt_or_transport(
    member, body, settings, user, secondary_org_client, secondary_org_context
):
    # Prove the absence-of-setting default, not merely an explicit override.
    if hasattr(settings, "GATEWAY_PROVIDER_MODEL_DISCOVERY_ENABLED"):
        del settings.GATEWAY_PROVIDER_MODEL_DISCOVERY_ENABLED
    org, workspace = secondary_org_context
    if member:
        OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=org
        ).update(role=OrganizationRoles.MEMBER, level=Level.MEMBER)
        WorkspaceMembership.objects.filter(user=user, workspace=workspace).update(
            role=OrganizationRoles.WORKSPACE_MEMBER, level=Level.WORKSPACE_MEMBER
        )
    with (
        patch.object(module.CredentialManager, "decrypt") as decrypt,
        patch.object(
            module.AgentccProviderCredentialViewSet, "_fetch_models_from_provider"
        ) as transport,
    ):
        response = secondary_org_client.post(
            "/agentcc/provider-credentials/fetch_models/", body, format="json"
        )
    assert response.status_code == 403
    assert response.json()["code"] == "provider_model_discovery_disabled"
    assert "model IDs manually" in response.json()["message"]
    assert NEW not in response.content.decode()
    decrypt.assert_not_called()
    transport.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("route", ["credential", "gateway"])
def test_off_keeps_manual_save_rotation_and_saved_hydration(
    route, secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    with (
        override_settings(GATEWAY_PROVIDER_MODEL_DISCOVERY_ENABLED=False),
        patch.object(
            module.AgentccProviderCredentialViewSet,
            "_push_config_to_gateway",
            return_value=True,
        ),
        patch.object(
            gateway.AgentccGatewayViewSet, "_push_current_config", return_value=True
        ),
        patch.object(
            module.AgentccProviderCredentialViewSet, "_fetch_models_from_provider"
        ) as transport,
    ):
        if route == "credential":
            response = secondary_org_client.post(
                "/agentcc/provider-credentials/",
                {
                    "provider_name": "openai",
                    "credentials": {"api_key": OLD},
                    "models_list": SAFE,
                },
                format="json",
            )
            assert response.status_code == 201
        else:
            response = secondary_org_client.post(
                "/agentcc/gateways/default/update-provider/",
                {"name": "openai", "config": {"api_key": OLD, "models": SAFE}},
                format="json",
            )
            assert response.status_code == 200
        row = AgentccProviderCredential.no_workspace_objects.get(
            organization=org, provider_name="openai", deleted=False
        )
        rotation = secondary_org_client.post(
            f"/agentcc/provider-credentials/{row.pk}/rotate/",
            {"credentials": {"api_key": NEW}},
            format="json",
        )
        assert rotation.status_code == 200
        assert rotation.json()["result"]["models_list"] == SAFE
        row.refresh_from_db()
        assert CredentialManager.decrypt(bytes(row.encrypted_credentials)) == {
            "api_key": NEW
        }
        hydration = secondary_org_client.post(
            "/agentcc/provider-credentials/fetch_models/",
            {"provider_name": "openai", "saved_models_only": True},
            format="json",
        )
        assert hydration.status_code == 200
        assert hydration.json()["result"]["models"] == SAFE
        transport.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
def test_explicit_enable_retains_discovery_contract(secondary_org_client):
    with (
        override_settings(GATEWAY_PROVIDER_MODEL_DISCOVERY_ENABLED=True),
        patch.object(
            module.AgentccProviderCredentialViewSet,
            "_fetch_models_from_provider",
            return_value=[NEW, "gpt-4o"],
        ) as transport,
    ):
        response = secondary_org_client.post(
            "/agentcc/provider-credentials/fetch_models/",
            {"api_key": NEW},
            format="json",
        )
    assert response.status_code == 200
    assert response.json()["result"]["models"] == ["gpt-4o"]
    assert response.json()["result"]["dropped_model_count"] == 1
    transport.assert_called_once()
