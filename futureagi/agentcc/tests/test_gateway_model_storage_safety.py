"""The dialog's real save route must not bypass model-ID admission."""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.tests import test_provider_credential_api as api_fixtures
from agentcc.tests.test_provider_model_discovery_safety import KEY, credential_echo_ids
from agentcc.views import gateway as module
from integrations.services.credentials import CredentialManager

secondary_org_client = api_fixtures.secondary_org_client
secondary_org_context = api_fixtures.secondary_org_context


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("saved", [False, True])
def test_dialog_gateway_save_filters_models_before_database_and_push(
    saved, secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    if saved:
        row = AgentccProviderCredential(
            organization=org,
            provider_name="openai",
            api_format="openai",
            encrypted_credentials=CredentialManager.encrypt({"api_key": KEY}),
            models_list=["gpt-4o-mini"],
            extra_config={},
        )
        row.disable_auto_workspace_assignment = True
        row.save(force_insert=True)
    config = {"models": credential_echo_ids()[:-2] + ["gpt-4o"], "api_format": "openai"}
    if not saved:
        config["api_key"] = KEY
    observed = []

    def push(_org):
        observed.append(
            AgentccProviderCredential.no_workspace_objects.get(
                organization=org, provider_name="openai", deleted=False
            ).models_list
        )
        return True

    logger = MagicMock()
    with (
        patch.object(
            module.AgentccGatewayViewSet, "_push_current_config", side_effect=push
        ),
        patch.object(module, "logger", logger),
    ):
        response = secondary_org_client.post(
            "/agentcc/gateways/default/update-provider/",
            {"name": "openai", "config": config},
            format="json",
        )
    assert response.status_code == 200
    assert observed == [["gpt-4o"]]
    row = AgentccProviderCredential.no_workspace_objects.get(
        organization=org, provider_name="openai", deleted=False
    )
    assert row.models_list == ["gpt-4o"]
    assert (
        response.json()["result"]["dropped_model_count"]
        == len(credential_echo_ids()) - 2
    )
    for value in credential_echo_ids()[:-2]:
        assert value not in response.content.decode() + repr(logger.mock_calls)
    assert CredentialManager.decrypt(bytes(row.encrypted_credentials))["api_key"] == KEY
