"""Provider configuration must fail at save, before encryption or a config push."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.serializers.provider_credential import (
    AgentccProviderCredentialCreateSerializer,
    AgentccProviderCredentialUpdateSerializer,
)
from agentcc.services.url_safety import ensure_provider_base_url_allowed
from integrations.services.credentials import CredentialManager

CREDENTIALS = "/agentcc/provider-credentials/"
CONSOLE = "/agentcc/gateways/default/update-provider/"
FORMATS = {
    "openai",
    "anthropic",
    "gemini",
    "google",
    "bedrock",
    "cohere",
    "azure",
    "deepgram",
    "cartesia",
}


def test_api_formats_cover_the_gateway_factory():
    root = Path(__file__).resolve().parents[3]
    source = (root / "agentcc-gateway/internal/providers/registry.go").read_text()
    factory = source.split("func createProvider(", 1)[1].split("\n}", 1)[0]
    accepted = {
        value
        for case in re.findall(r"case ([^:]+):", factory)
        for value in re.findall(r'"([^"]+)"', case)
    }
    assert accepted == FORMATS
    for serializer in (
        AgentccProviderCredentialCreateSerializer,
        AgentccProviderCredentialUpdateSerializer,
    ):
        assert set(serializer().fields["api_format"].choices) == accepted


@pytest.fixture(autouse=True)
def isolated(monkeypatch, settings, provider_dns):
    settings.CLOUD_DEPLOYMENT = ""
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    monkeypatch.delenv("AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", raising=False)
    provider_dns(
        {"public.invalid": ["93.184.216.34"], "private.invalid": ["10.2.3.4"]},
        passthrough=True,
    )
    with (
        patch("agentcc.services.config_push.get_gateway_client") as gateway,
        patch("agentcc.services.gateway_client.get_gateway_client") as operator,
    ):
        operator.return_value.get_config.return_value = {
            "provider_ids": ["operator-openai"]
        }
        yield gateway


def payload(**changes):
    return {
        "provider_name": "custom",
        "credentials": {"api_key": "synthetic-provider-key"},
        "base_url": "https://public.invalid/v1",
        **changes,
    }


@pytest.mark.integration
@pytest.mark.parametrize("api_format", ["ollama", "not-a-format", "OPENAI", ""])
@pytest.mark.parametrize("console", [False, True])
def test_bad_format_does_not_save(
    auth_client, organization, isolated, api_format, console
):
    data = payload(api_format=api_format)
    if console:
        data = {
            "name": "custom",
            "config": {
                "api_key": "synthetic-provider-key",
                "base_url": data["base_url"],
                "api_format": api_format,
            },
        }
    response = auth_client.post(
        CONSOLE if console else CREDENTIALS, data, format="json"
    )
    assert response.status_code == 400, response.content
    for accepted in FORMATS:
        assert accepted in response.content.decode()
    assert not AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization
    ).exists()
    isolated.set_org_config.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "key", ["weight", "base_url", "timeout", "models", "organization_id"]
)
@pytest.mark.parametrize("value", ["ignored-value", ""])
def test_only_credential_fields_are_accepted(
    auth_client, organization, isolated, key, value
):
    response = auth_client.post(
        CREDENTIALS,
        payload(credentials={"api_key": "synthetic-provider-key", key: value}),
        format="json",
    )
    assert response.status_code == 400, response.content
    message = response.content.decode()
    assert key in message and "Allowed" in message and "aws_region" in message
    assert not AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization
    ).exists()
    isolated.set_org_config.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize("console", [False, True])
def test_unknown_provider_needs_base_url(auth_client, organization, console):
    data = (
        {"name": "typo-provider", "config": {"api_key": "synthetic-provider-key"}}
        if console
        else payload(provider_name="typo-provider", base_url="")
    )
    response = auth_client.post(
        CONSOLE if console else CREDENTIALS, data, format="json"
    )
    assert response.status_code == 400, response.content
    assert (
        "typo-provider" in response.content.decode()
        and "base_url" in response.content.decode()
    )
    assert not AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization
    ).exists()


@pytest.mark.integration
def test_partial_update_and_rotation_preserve_the_saved_row(auth_client, organization):
    response = auth_client.post(CREDENTIALS, payload(), format="json")
    assert response.status_code == 201, response.content
    row = AgentccProviderCredential.no_workspace_objects.get(organization=organization)
    original = bytes(row.encrypted_credentials)
    for change in ({"api_format": "ollama"}, {"base_url": ""}):
        response = auth_client.patch(f"{CREDENTIALS}{row.id}/", change, format="json")
        assert response.status_code == 400, response.content
    response = auth_client.post(
        f"{CREDENTIALS}{row.id}/rotate/",
        {
            "credentials": {
                "api_key": "replacement",
                "base_url": "https://public.invalid",
            }
        },
        format="json",
    )
    assert response.status_code == 400, response.content
    assert "Allowed" in response.content.decode()
    row.refresh_from_db()
    assert row.api_format == "openai" and row.base_url == "https://public.invalid/v1"
    assert bytes(row.encrypted_credentials) == original


@pytest.mark.parametrize("deployment", ["US", " eu ", "DEV"])
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://[::1]",
        "http://[fe80::1]",
        "http://169.254.169.254",
        "http://10.2.3.4",
        "http://192.168.1.2",
        "http://[fd00::1]",
        "http://private.invalid",
        "http://localhost.",
        "http://host.docker.internal",
        "http://metadata.google.internal",
    ],
)
def test_hosted_save_cannot_opt_in_or_grandfather_private_urls(
    settings, monkeypatch, deployment, url
):
    settings.CLOUD_DEPLOYMENT = deployment
    monkeypatch.setenv("AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", "true")
    with pytest.raises(ValueError):
        ensure_provider_base_url_allowed(url)
    with pytest.raises(ValueError):
        ensure_provider_base_url_allowed(url, saved_base_url=url)


@pytest.mark.integration
@pytest.mark.parametrize("console", [False, True])
def test_hosted_routes_refuse_docker_host_even_when_it_does_not_resolve(
    auth_client, organization, settings, console
):
    settings.CLOUD_DEPLOYMENT = "US"
    with patch(
        "agentcc.services.url_safety.socket.getaddrinfo",
        side_effect=__import__("socket").gaierror,
    ):
        data = payload(base_url="http://host.docker.internal:11434")
        if console:
            data = {
                "name": "custom",
                "config": {
                    "api_key": "synthetic-provider-key",
                    "base_url": data["base_url"],
                },
            }
        response = auth_client.post(
            CONSOLE if console else CREDENTIALS, data, format="json"
        )
    assert response.status_code == 400, response.content
    assert not AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization
    ).exists()


@pytest.mark.parametrize(
    "url", ["http://private.invalid:11434", "http://host.docker.internal:11434"]
)
def test_self_hosted_private_opt_in_still_works(monkeypatch, provider_dns, url):
    provider_dns(
        {"private.invalid": ["10.2.3.4"], "host.docker.internal": ["192.168.65.254"]}
    )
    monkeypatch.setenv("AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", "true")
    ensure_provider_base_url_allowed(url)


@pytest.mark.integration
@pytest.mark.parametrize("api_format", sorted(FORMATS))
def test_oq3_valid_payload_formats_and_aws_aliases_survive(
    auth_client, organization, api_format
):
    credentials = {
        "api_key": "synthetic-provider-key",
        "access_key": "test-access",
        "secret_key": "test-secret",
        "region": "us-east-1",
    }
    response = auth_client.post(
        CREDENTIALS,
        payload(
            api_format=api_format,
            credentials=credentials,
            extra_config={"api_path_prefix": "", "weight": 3},
        ),
        format="json",
    )
    assert response.status_code == 201, response.content
    row = AgentccProviderCredential.no_workspace_objects.get(organization=organization)
    assert CredentialManager.decrypt(bytes(row.encrypted_credentials)) == credentials
    assert row.extra_config == {"api_path_prefix": "", "weight": 3}


@pytest.mark.integration
@pytest.mark.parametrize("console", [False, True])
def test_oq3_configured_operator_alias_keeps_default_endpoint(auth_client, console):
    data = (
        {"name": "operator-openai", "config": {"api_key": "synthetic-provider-key"}}
        if console
        else payload(provider_name="operator-openai", base_url="")
    )
    response = auth_client.post(
        CONSOLE if console else CREDENTIALS, data, format="json"
    )
    assert response.status_code == (200 if console else 201), response.content


@pytest.mark.integration
@pytest.mark.parametrize(
    "url",
    [
        "ftp://public.invalid",
        "public.invalid",
        "https:///v1",
        "http://public.invalid:bad",
        "http://public.invalid:70000",
        "http://public.invalid/a b",
    ],
)
@pytest.mark.parametrize("console", [False, True])
def test_malformed_urls_are_refused_on_both_save_paths(auth_client, url, console):
    data = payload(base_url=url)
    if console:
        data = {
            "name": "custom",
            "config": {"api_key": "synthetic-provider-key", "base_url": url},
        }
    response = auth_client.post(
        CONSOLE if console else CREDENTIALS, data, format="json"
    )
    assert response.status_code == 400, response.content
