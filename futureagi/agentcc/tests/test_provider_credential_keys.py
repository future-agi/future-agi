"""A credential key outside the provider contract is refused.

Save, rotate and the console's update-provider answer 400 naming a credential key
the gateway's provider contract does not take; a row stored with one shows in
Provider Health as not pushed, never as a healthy chip.
"""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.tests.console_support import GATEWAY, FakeGateway
from integrations.services.credentials import CredentialManager

CREDENTIALS = "/agentcc/provider-credentials/"
GOOD_KEY = "sk-test-0123456789abcdef"


@pytest.fixture
def pushed():
    """Every org config Django pushes, captured, never sent."""
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


def _pushed_providers(pushed):
    assert pushed.set_org_config.called, "nothing was pushed"
    return pushed.set_org_config.call_args[0][1].get("providers") or {}


def _stored(organization, name="openai"):
    return AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization, provider_name=name, deleted=False
    ).first()


def _store_row(organization, credentials, name="openai"):
    return AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        provider_name=name,
        display_name=name.title(),
        encrypted_credentials=CredentialManager.encrypt(credentials),
        api_format="openai",
        models_list=["gpt-4o"],
    )


# --- save --------------------------------------------------------------------


@pytest.mark.integration
def test_create_with_a_key_outside_the_contract_is_refused(
    organization, auth_client, pushed
):
    response = auth_client.post(
        CREDENTIALS,
        {
            "provider_name": "openai",
            "credentials": {"api_key": GOOD_KEY, "organization": "org-x"},
            "models_list": ["gpt-4o"],
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "organization" in response.content.decode()
    assert _stored(organization) is None
    pushed.set_org_config.assert_not_called()


@pytest.mark.integration
def test_create_names_every_key_outside_the_contract(organization, auth_client, pushed):
    response = auth_client.post(
        CREDENTIALS,
        {
            "provider_name": "openai",
            "credentials": {
                "api_key": GOOD_KEY,
                "project": "p-1",
                "organization": "org-x",
            },
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    body = response.content.decode()
    assert "organization" in body and "project" in body


@pytest.mark.integration
def test_create_with_contract_keys_is_saved_and_pushed(
    organization, auth_client, pushed
):
    response = auth_client.post(
        CREDENTIALS,
        {
            "provider_name": "openai",
            "credentials": {"api_key": GOOD_KEY},
            "models_list": ["gpt-4o"],
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    assert "openai" in _pushed_providers(pushed)


@pytest.mark.integration
def test_create_keeps_the_legacy_aws_aliases(organization, auth_client, pushed):
    # access_key/secret_key/region are the contract's aws_* fields by alias.
    response = auth_client.post(
        CREDENTIALS,
        {
            "provider_name": "bedrock",
            "credentials": {
                "api_key": "unused-test",
                "access_key": "AKIATESTKEY",
                "secret_key": "secret-test",
                "region": "us-east-1",
            },
            "api_format": "bedrock",
            "models_list": ["anthropic.claude-3-haiku"],
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    assert "bedrock" in _pushed_providers(pushed)


# --- rotate ------------------------------------------------------------------


@pytest.mark.integration
def test_rotate_with_a_key_outside_the_contract_is_refused(
    organization, auth_client, pushed
):
    row = _store_row(organization, {"api_key": GOOD_KEY})

    response = auth_client.post(
        f"{CREDENTIALS}{row.id}/rotate/",
        {"credentials": {"api_key": "sk-test-rotated", "organization": "org-x"}},
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "organization" in response.content.decode()
    row.refresh_from_db()
    assert CredentialManager.decrypt(bytes(row.encrypted_credentials)) == {
        "api_key": GOOD_KEY
    }
    assert row.last_rotated_at is None
    pushed.set_org_config.assert_not_called()


@pytest.mark.integration
def test_rotate_with_contract_keys_is_saved(organization, auth_client, pushed):
    row = _store_row(organization, {"api_key": GOOD_KEY})

    response = auth_client.post(
        f"{CREDENTIALS}{row.id}/rotate/",
        {"credentials": {"api_key": "sk-test-rotated"}},
        format="json",
    )

    assert response.status_code == 200, response.content
    row.refresh_from_db()
    assert CredentialManager.decrypt(bytes(row.encrypted_credentials)) == {
        "api_key": "sk-test-rotated"
    }
    assert "openai" in _pushed_providers(pushed)


@pytest.mark.integration
def test_rotate_repairs_a_row_stored_with_a_bad_key(organization, auth_client, pushed):
    row = _store_row(organization, {"api_key": GOOD_KEY, "organization": "org-x"})

    response = auth_client.post(
        f"{CREDENTIALS}{row.id}/rotate/",
        {"credentials": {"api_key": GOOD_KEY}},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert "openai" in _pushed_providers(pushed)


# --- the console's update-provider -------------------------------------------


@pytest.mark.integration
def test_update_provider_with_api_secret_is_refused(organization, auth_client, pushed):
    with patch("agentcc.views.gateway.ensure_provider_base_url_allowed"):
        response = auth_client.post(
            f"{GATEWAY}/update-provider/",
            {
                "name": "openai",
                "config": {
                    "api_key": GOOD_KEY,
                    "api_secret": "api-secret-test",
                    "base_url": "https://api.openai.com/v1",
                    "models": ["gpt-4o"],
                },
            },
            format="json",
        )

    assert response.status_code == 400, response.content
    assert "api_secret" in response.content.decode()
    assert _stored(organization) is None
    pushed.set_org_config.assert_not_called()


@pytest.mark.integration
def test_update_provider_with_contract_keys_is_saved(organization, auth_client, pushed):
    with patch("agentcc.views.gateway.ensure_provider_base_url_allowed"):
        response = auth_client.post(
            f"{GATEWAY}/update-provider/",
            {
                "name": "openai",
                "config": {
                    "api_key": GOOD_KEY,
                    "base_url": "https://api.openai.com/v1",
                    "models": ["gpt-4o"],
                },
            },
            format="json",
        )

    assert response.status_code == 200, response.content
    assert "openai" in _pushed_providers(pushed)


# --- a row already stored with such a key ------------------------------------


def _health(auth_client):
    with patch("agentcc.views.gateway.get_gateway_client", return_value=FakeGateway()):
        response = auth_client.get(f"{GATEWAY}/providers/")
    assert response.status_code == 200, response.content
    return {p["id"]: p for p in response.json()["result"]["providers"]}


@pytest.mark.integration
def test_a_stored_row_with_a_bad_key_shows_as_not_pushed(organization, auth_client):
    _store_row(organization, {"api_key": GOOD_KEY, "organization": "org-x"})
    _store_row(organization, {"api_key": GOOD_KEY}, name="groq")

    rows = _health(auth_client)

    bad = rows["openai"]
    assert bad["pushed"] is False
    assert bad["status"] == "unhealthy"
    assert bad["healthy"] is False
    assert bad["status_reason"] == "not pushed: unsupported credential key organization"
    good = rows["groq"]
    assert good["pushed"] is True
    assert good["status"] == "healthy"


@pytest.mark.integration
def test_a_stored_row_that_cannot_be_decrypted_shows_as_not_pushed(
    organization, auth_client
):
    AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        provider_name="openai",
        display_name="OpenAI",
        encrypted_credentials=b"not-a-fernet-token",
        api_format="openai",
        models_list=["gpt-4o"],
    )

    row = _health(auth_client)["openai"]

    assert row["pushed"] is False
    assert row["healthy"] is False
    assert (
        row["status_reason"] == "not pushed: the stored credential cannot be decrypted"
    )
