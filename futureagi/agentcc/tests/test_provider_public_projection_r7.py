"""R7 member HTTP projections and URL-auth boundaries; synthetic values only."""

import base64
from unittest.mock import MagicMock, patch

import pytest
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.tests import test_agentcc_permissions as roles
from agentcc.views import gateway, provider_credential
from integrations.services.credentials import CredentialManager

make_client = roles.make_client
client_with_level = roles.client_with_level
member_client = roles.member_client
admin_client = roles.admin_client

pytestmark = [pytest.mark.integration, pytest.mark.api]
GATEWAY = "/agentcc/gateways/default"
API = "/agentcc/provider-credentials/"
KEY = "cedar-harbor-7913-oriole"
URL_USER = "willow-meadow-2357"
URL_PASSWORD = "violet-canyon-8642-tern"
SAFE = ["gpt-4o", "ft:gpt-3.5-turbo-0125:personal::8sNGh8Xd"]
PUBLIC_URL = "https://models.example.invalid:8443/v1"
AUTH_URL = PUBLIC_URL.replace("//", "//%77" + URL_USER[1:] + ":%76" + URL_PASSWORD[1:] + "@")
URL_ERROR = "Base URL must not contain username or password credentials."


def echoes(value):
    return [value, "route/" + value, base64.b64encode(value.encode()).decode()]


def retained(org, *, name="openai", base_url="", encrypted=None):
    row = AgentccProviderCredential(
        organization=org,
        provider_name=name,
        encrypted_credentials=(
            CredentialManager.encrypt({"api_key": KEY}) if encrypted is None else encrypted
        ),
        base_url=base_url,
        api_format="openai",
        models_list=echoes(KEY) + (echoes(URL_USER) + echoes(URL_PASSWORD) if base_url else []) + SAFE,
    )
    row.disable_auto_workspace_assignment = True
    row.save(force_insert=True)
    return row


def snapshot(row):
    return AgentccProviderCredential.no_workspace_objects.filter(pk=row.pk).values().get()


def gateway_reads(client):
    transport = MagicMock()
    transport.health_check.return_value = {"status": "healthy"}
    transport.get_config.return_value = {}
    with patch.object(gateway, "get_gateway_client", return_value=transport):
        yield client.get(GATEWAY + "/providers/"), "providers"
        yield client.get(GATEWAY + "/config/"), "config"
        yield client.post(GATEWAY + "/health_check/", {}, format="json"), "health"
    transport.set_org_config.assert_not_called()


def gateway_item(response, kind):
    assert response.status_code == 200
    result = response.json()["result"]
    if kind == "config":
        return result["providers"]["openai"]
    if kind == "health":
        return result["providers"]["providers"][0]
    return result["providers"][0]


def test_member_gateway_reads_scrub_retained_models_without_writes(organization, member_client):
    row = retained(organization)
    before = snapshot(row)
    for response, kind in gateway_reads(member_client):
        assert gateway_item(response, kind)["models"] == SAFE
        for value in echoes(KEY):
            assert value not in response.content.decode()
    assert snapshot(row) == before


def test_member_gateway_reads_fail_closed_on_invalid_context(organization, member_client):
    for encrypted in (
        b"synthetic-unreadable-r7",
        CredentialManager.encrypt([KEY]),
        CredentialManager.encrypt({"api_key": 7319}),
    ):
        row = retained(organization, encrypted=encrypted)
        before = snapshot(row)
        for response, kind in gateway_reads(member_client):
            assert gateway_item(response, kind)["models"] == []
        assert snapshot(row) == before
        row.delete()


def test_both_provider_writes_reject_url_userinfo_value_free(organization, admin_client):
    with (
        patch.object(provider_credential.AgentccProviderCredentialViewSet, "_push_config_to_gateway") as push,
        patch.object(gateway.AgentccGatewayViewSet, "_push_current_config") as gateway_push,
        patch("agentcc.services.url_safety.socket.getaddrinfo", return_value=[]),
    ):
        for url in (AUTH_URL, PUBLIC_URL.replace("//", "//" + URL_USER + "@")):
            responses = [
                admin_client.post(API, {"provider_name": "openai", "credentials": {"api_key": KEY}, "base_url": url}, format="json"),
                admin_client.post(GATEWAY + "/update-provider/", {"name": "openai", "config": {"api_key": KEY, "base_url": url}}, format="json"),
            ]
            for response in responses:
                assert response.status_code == 400
                assert URL_ERROR in response.content.decode()
                for value in (KEY, URL_USER, URL_PASSWORD):
                    assert value not in response.content.decode()
        push.assert_not_called()
        gateway_push.assert_not_called()
    assert not AgentccProviderCredential.no_workspace_objects.filter(organization=organization).exists()


def test_legacy_url_auth_is_redacted_from_all_member_projections(organization, member_client):
    row = retained(organization, base_url=AUTH_URL)
    before = snapshot(row)
    for response, kind in gateway_reads(member_client):
        item = gateway_item(response, kind)
        assert item["models"] == SAFE
        if "base_url" in item:
            assert item["base_url"] == PUBLIC_URL
        for value in (URL_USER, URL_PASSWORD, "%77", "%76"):
            assert value not in response.content.decode()
    for suffix in ("", f"{row.pk}/"):
        response = member_client.get(API + suffix)
        assert response.status_code == 200
        item = response.json()["result"]
        item = item[0] if isinstance(item, list) else item
        assert item["models_list"] == SAFE
        assert item["base_url"] == PUBLIC_URL
    with patch.object(provider_credential.AgentccProviderCredentialViewSet, "_fetch_models_from_provider") as fetch:
        response = member_client.post(API + "fetch_models/", {"provider_name": "openai", "saved_models_only": True}, format="json")
        assert response.status_code == 200
        assert response.json()["result"]["models"] == SAFE
        fetch.assert_not_called()
    assert snapshot(row) == before


def test_url_edit_excludes_prior_auth_on_both_write_routes(organization, admin_client):
    with (
        patch.object(provider_credential.AgentccProviderCredentialViewSet, "_push_config_to_gateway", return_value=True),
        patch.object(gateway.AgentccGatewayViewSet, "_push_current_config", return_value=True),
        patch("agentcc.services.url_safety.socket.getaddrinfo", return_value=[]),
    ):
        for name in ("openai", "custom"):
            row = retained(organization, name=name, base_url=AUTH_URL)
            if name == "openai":
                response = admin_client.patch(API + f"{row.pk}/", {"base_url": PUBLIC_URL}, format="json")
            else:
                response = admin_client.post(GATEWAY + "/update-provider/", {"name": name, "config": {"base_url": PUBLIC_URL, "models": row.models_list}}, format="json")
            assert response.status_code == 200
            row.refresh_from_db()
            assert row.base_url == PUBLIC_URL
            assert row.models_list == SAFE


def test_rotation_excludes_retained_url_auth(organization, admin_client):
    row = retained(organization, base_url=AUTH_URL)
    with patch.object(provider_credential.AgentccProviderCredentialViewSet, "_push_config_to_gateway", return_value=True):
        response = admin_client.post(API + f"{row.pk}/rotate/", {"credentials": {"api_key": "amber-forest-5406-finch"}}, format="json")
    assert response.status_code == 200
    assert response.json()["result"]["models_list"] == SAFE
    assert response.json()["result"]["base_url"] == PUBLIC_URL
    row.refresh_from_db()
    assert row.models_list == SAFE
