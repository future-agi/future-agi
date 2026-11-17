"""Provider edit regressions using persisted credentials and the real config builder."""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.provider_credential import AgentccProviderCredential
from integrations.services.credentials import CredentialManager

pytestmark = pytest.mark.django_db

BASE_URL = "https://bedrock-runtime.eu-west-1.amazonaws.com"
STORED = {
    "aws_access_key_id": "fixture-stored-access",
    "aws_secret_access_key": "fixture-stored-secret",
    "aws_session_token": "fixture-stored-session",
    "aws_region": "eu-west-1",
}
REPLACEMENT = {
    "aws_access_key_id": "fixture-replacement-access",
    "aws_secret_access_key": "fixture-replacement-secret",
}


@pytest.fixture
def gateway(provider_dns):
    provider_dns({"bedrock-runtime.eu-west-1.amazonaws.com": ["93.184.216.34"]})
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


@pytest.fixture
def credential(organization):
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        provider_name="bedrock",
        api_format="bedrock",
        base_url=BASE_URL,
        encrypted_credentials=CredentialManager.encrypt(STORED),
        models_list=["saved-model"],
    )


def save(client, fields):
    response = client.post(
        "/agentcc/gateways/default/update-provider/",
        {"name": "bedrock", "config": {
            "api_format": "bedrock", "base_url": BASE_URL,
            "models": ["saved-model"], **fields,
        }},
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["gateway_synced"] is True
    return response


def persisted(credential):
    credential.refresh_from_db()
    return CredentialManager.decrypt(bytes(credential.encrypted_credentials))


def pushed(gateway):
    gateway.set_org_config.assert_called_once()
    return gateway.set_org_config.call_args.args[1]["providers"]["bedrock"]


@pytest.mark.parametrize("optional_token", [{}, {"aws_session_token": ""}, {"aws_session_token": " \t "}])
def test_complete_pair_replacement_clears_old_sts_token(
    auth_client, credential, gateway, optional_token
):
    save(auth_client, {**REPLACEMENT, **optional_token})

    expected = {**STORED, **REPLACEMENT}
    expected.pop("aws_session_token")
    assert persisted(credential) == expected
    assert "aws_session_token" not in credential.extra_config
    config = pushed(gateway)
    assert not config.get("aws_session_token")
    assert config["aws_access_key_id"] == REPLACEMENT["aws_access_key_id"]
    assert config["aws_secret_access_key"] == REPLACEMENT["aws_secret_access_key"]
    assert config["aws_region"] == "eu-west-1"


def test_sts_to_sts_replacement_uses_the_new_token(auth_client, credential, gateway):
    fields = {**REPLACEMENT, "aws_session_token": "fixture-replacement-session"}
    save(auth_client, fields)
    assert persisted(credential) == {**STORED, **fields}
    assert pushed(gateway)["aws_session_token"] == fields["aws_session_token"]


@pytest.mark.parametrize("fields", [
    {"default_timeout": 45, "aws_region": "eu-west-1"},
    {"aws_access_key_id": "", "aws_secret_access_key": " ", "aws_session_token": " "},
    {"aws_access_key_id": "fixture-replacement-access"},
    {"aws_secret_access_key": "fixture-replacement-secret"},
])
def test_unrelated_blank_or_partial_edit_keeps_the_saved_token(
    auth_client, credential, gateway, fields
):
    save(auth_client, fields)
    assert persisted(credential)["aws_session_token"] == STORED["aws_session_token"]
    config = pushed(gateway)
    assert config["aws_session_token"] == STORED["aws_session_token"]
    assert config["aws_region"] == "eu-west-1"
    assert config["base_url"] == BASE_URL
    if "default_timeout" in fields:
        assert credential.default_timeout_seconds == 45


def test_removed_token_remains_in_model_screening_context(auth_client, credential, gateway):
    response = save(auth_client, {
        **REPLACEMENT,
        "models": ["saved-model", STORED["aws_session_token"]],
    })
    assert persisted(credential) == {"aws_region": "eu-west-1", **REPLACEMENT}
    assert credential.models_list == ["saved-model"]
    assert response.json()["result"]["dropped_model_count"] == 1
    assert pushed(gateway)["models"] == ["saved-model"]
    assert STORED["aws_session_token"] not in response.content.decode()
