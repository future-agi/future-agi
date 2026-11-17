"""Real HTTP/DB write seams; no upstream requests or config publication."""

import json
from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.tests import test_provider_credential_api as api_fixtures
from agentcc.tests.test_provider_model_discovery_safety import KEY, credential_echo_ids
from agentcc.views import provider_credential as module
from integrations.services.credentials import CredentialManager

secondary_org_client = api_fixtures.secondary_org_client
secondary_org_context = api_fixtures.secondary_org_context


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("method", ["post", "patch", "put"])
def test_model_writes_drop_ids_before_save_response_or_push(
    method, secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    body = {"models_list": credential_echo_ids()[:-2] + ["gpt-4o"]}
    url = "/agentcc/provider-credentials/"
    if method == "post":
        body.update(provider_name="openai", credentials={"api_key": KEY})
    else:
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
        url += f"{row.pk}/"
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
            module.AgentccProviderCredentialViewSet,
            "_push_config_to_gateway",
            side_effect=push,
        ),
        patch.object(module, "logger", logger),
    ):
        response = getattr(secondary_org_client, method)(url, body, format="json")
    assert response.status_code == (201 if method == "post" else 200)
    row = AgentccProviderCredential.no_workspace_objects.get(
        organization=org, provider_name="openai", deleted=False
    )
    assert row.models_list == ["gpt-4o"]
    assert observed == [["gpt-4o"]]
    assert (
        response.json()["result"]["dropped_model_count"]
        == len(credential_echo_ids()) - 2
    )
    public = (
        response.content.decode()
        + json.dumps(row.models_list)
        + repr(logger.mock_calls)
    )
    for value in credential_echo_ids()[:-2]:
        assert value not in public
    # The credential remains encrypted and usable, not removed as a workaround.
    assert CredentialManager.decrypt(bytes(row.encrypted_credentials))["api_key"] == KEY


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("detail", [False, True])
def test_legacy_rows_are_projected_without_exposing_encoded_or_shaped_models(
    detail, secondary_org_client, secondary_org_context
):
    org, _ = secondary_org_context
    row = AgentccProviderCredential(
        organization=org,
        provider_name="openai",
        api_format="openai",
        encrypted_credentials=CredentialManager.encrypt({"api_key": KEY}),
        models_list=credential_echo_ids()[:-2] + ["gpt-4o"],
        extra_config={},
    )
    row.disable_auto_workspace_assignment = True
    row.save(force_insert=True)
    url = "/agentcc/provider-credentials/" + (f"{row.pk}/" if detail else "")
    response = secondary_org_client.get(url)
    assert response.status_code == 200
    result = response.json()["result"]
    item = result if detail else result[0]
    assert item["models_list"] == ["gpt-4o"]
    for value in credential_echo_ids()[:-2]:
        assert value not in response.content.decode()
    row.refresh_from_db()
    assert row.models_list == credential_echo_ids()[:-2] + ["gpt-4o"]
