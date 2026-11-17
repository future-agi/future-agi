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
from agentcc.services.provider_validation import (
    KNOWN_PROVIDER_NAMES,
    PROVIDER_API_FORMATS,
    ensure_provider_configuration,
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
    "systemone",
    "typesafe",
}


def _gateway_factory_formats(source):
    constants = {
        name: value
        for block in re.findall(r"(?ms)^const\s*\(\n(.*?)^\)", source)
        for name, value in re.findall(
            r'(?m)^\s*(\w+)(?:\s+string)?\s*=\s*"([^"]+)"', block
        )
    }
    factory = source.split("func createProvider(", 1)[1].split("\n}", 1)[0]
    accepted = set()
    for case in re.findall(r"(?m)^\s*case\s+([^:]+):", factory):
        for token in case.split(","):
            token = token.strip()
            literal = re.fullmatch(r'"([^"]+)"', token)
            if literal:
                accepted.add(literal[1])
            else:
                assert token in constants, f"Unresolved gateway api_format: {token}"
                accepted.add(constants[token])
    return accepted


def test_gateway_factory_formats_refuse_unresolved_constants():
    source = (
        "func createProvider() {\n switch cfg.APIFormat {\n case UnknownFormat:\n }\n}"
    )
    with pytest.raises(
        AssertionError, match="Unresolved gateway api_format: UnknownFormat"
    ):
        _gateway_factory_formats(source)


def test_api_formats_cover_the_gateway_factory():
    root = Path(__file__).resolve().parents[3]
    source = (root / "agentcc-gateway/internal/providers/registry.go").read_text()
    accepted = _gateway_factory_formats(source)
    assert accepted == FORMATS == set(PROVIDER_API_FORMATS)
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
        yield gateway.return_value


def payload(**changes):
    return {
        "provider_name": "custom",
        "credentials": {"api_key": "synthetic-provider-key"},
        "base_url": "https://public.invalid/v1",
        **changes,
    }


def _save_provider(auth_client, console, provider_name, api_format, base_url=""):
    data = payload(
        provider_name=provider_name, api_format=api_format, base_url=base_url
    )
    if console:
        data = {
            "name": provider_name,
            "config": {
                "api_key": "synthetic-provider-key",
                "api_format": api_format,
                "base_url": base_url,
            },
        }
    return auth_client.post(CONSOLE if console else CREDENTIALS, data, format="json")


DECISION_NONVENDORS = ("operator-openai", "groq", "systemone", "custom_org_typesafe")


def _decision_save_request(
    organization, console, provider_name, api_format, service_account, base_url=""
):
    """Console regressions update an existing row, preserving omitted credentials."""
    credentials = {"api_key": "synthetic-provider-key"}
    if service_account:
        credentials["service_account_json"] = '{"type":"synthetic-service-account"}'
    if not console:
        return (
            CREDENTIALS,
            payload(
                provider_name=provider_name,
                api_format=api_format,
                base_url=base_url,
                credentials=credentials,
            ),
            None,
        )
    row = AgentccProviderCredential.no_workspace_objects.create(
        organization=organization,
        provider_name=provider_name,
        encrypted_credentials=CredentialManager.encrypt(credentials),
        base_url="https://public.invalid/original",
        api_format=api_format,
        display_name="Original provider",
        models_list=["original-model"],
        extra_config={"weight": 3},
    )
    return (
        CONSOLE,
        {
            "name": provider_name,
            # In particular, omit service_account_json: the console rejects
            # direct non-Vertex input, but preserves it from an existing row.
            "config": {"api_format": api_format, "base_url": base_url},
        },
        row,
    )


@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
@pytest.mark.parametrize("provider_name", DECISION_NONVENDORS)
def test_decision_operator_names_require_explicit_base_url(api_format, provider_name):
    with patch("agentcc.services.gateway_client.get_gateway_client") as operator:
        operator.return_value.get_config.return_value = {
            "provider_ids": [provider_name]
        }
        with pytest.raises(ValueError, match="base_url"):
            ensure_provider_configuration(provider_name, api_format, "")
        operator.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize("console", [False, True], ids=["create", "existing-console"])
@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
@pytest.mark.parametrize("provider_name", DECISION_NONVENDORS)
@pytest.mark.parametrize("service_account", [False, True])
@pytest.mark.parametrize("operator_configured", [False, True])
def test_decision_nonvendors_refuse_empty_url_before_save(
    auth_client,
    organization,
    isolated,
    console,
    api_format,
    provider_name,
    service_account,
    operator_configured,
):
    route, data, row = _decision_save_request(
        organization, console, provider_name, api_format, service_account
    )
    rows = AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization
    ).order_by("pk")
    before = list(rows.values())
    assert len(before) == (1 if console else 0)
    with (
        patch("agentcc.services.gateway_client.get_gateway_client") as operator,
        patch.object(
            CredentialManager, "encrypt", wraps=CredentialManager.encrypt
        ) as encrypt,
    ):
        operator.return_value.get_config.return_value = {
            "provider_ids": [provider_name] if operator_configured else []
        }
        response = auth_client.post(route, data, format="json")
        assert response.status_code == 400, response.content
        assert provider_name in response.content.decode()
        assert "base_url" in response.content.decode()
        encrypt.assert_not_called()
        operator.assert_not_called()
    # Compare all persisted fields, including ciphertext and timestamps, not
    # just row count. The existing console row must survive byte-for-byte.
    assert list(rows.values()) == before
    if row is not None:
        row.refresh_from_db()
        assert row.base_url == "https://public.invalid/original"
    isolated.set_org_config.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize("console", [False, True], ids=["create", "existing-console"])
@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
@pytest.mark.parametrize("provider_name", DECISION_NONVENDORS)
@pytest.mark.parametrize("service_account", [False, True])
def test_decision_explicit_urls_save_without_operator_lookup(
    auth_client, organization, console, api_format, provider_name, service_account
):
    base_url = "https://public.invalid/v1"
    route, data, original = _decision_save_request(
        organization, console, provider_name, api_format, service_account, base_url
    )
    with patch("agentcc.services.gateway_client.get_gateway_client") as operator:
        operator.return_value.get_config.return_value = {
            "provider_ids": [provider_name]
        }
        response = auth_client.post(route, data, format="json")
        operator.assert_not_called()
    assert response.status_code == (200 if console else 201), response.content
    row = AgentccProviderCredential.no_workspace_objects.get(
        organization=organization, provider_name=provider_name
    )
    assert row.api_format == api_format and row.base_url == base_url
    credentials = CredentialManager.decrypt(bytes(row.encrypted_credentials))
    assert credentials["api_key"] == "synthetic-provider-key"
    assert ("service_account_json" in credentials) == service_account
    if original is not None:
        assert row.pk == original.pk
        assert bytes(row.encrypted_credentials) == bytes(original.encrypted_credentials)


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


@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
def test_decision_formats_do_not_add_default_provider_names(api_format):
    assert api_format in PROVIDER_API_FORMATS
    assert api_format not in KNOWN_PROVIDER_NAMES
    ensure_provider_configuration(api_format, api_format, "https://public.invalid/v1")


@pytest.mark.integration
@pytest.mark.parametrize("console", [False, True])
@pytest.mark.parametrize(
    "api_format,provider_name",
    [
        (api_format, name)
        for api_format in ("openai", "systemone", "typesafe")
        for name in ("typo-provider", "systemone")
    ]
    + [
        ("openai", name)
        for name in ("typesafe", "typesafe-proxy", "perplexity-decider")
    ],
)
def test_unknown_provider_needs_base_url(
    auth_client, organization, isolated, console, api_format, provider_name
):
    response = _save_provider(auth_client, console, provider_name, api_format)
    assert response.status_code == 400, response.content
    assert (
        provider_name in response.content.decode()
        and "base_url" in response.content.decode()
    )
    assert not AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization
    ).exists()
    isolated.set_org_config.assert_not_called()


@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
@pytest.mark.parametrize("provider_name", sorted(KNOWN_PROVIDER_NAMES - {"perplexity"}))
def test_decision_known_names_without_defaults_need_base_url(api_format, provider_name):
    with pytest.raises(ValueError, match="base_url"):
        ensure_provider_configuration(provider_name, api_format, "")


@pytest.mark.integration
@pytest.mark.parametrize("console", [False, True])
@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
@pytest.mark.parametrize("provider_name", ["openai", "groq", "anthropic"])
def test_decision_native_names_need_base_url(
    auth_client, organization, isolated, console, api_format, provider_name
):
    response = _save_provider(auth_client, console, provider_name, api_format)
    assert response.status_code == 400, response.content
    assert provider_name in response.content.decode()
    assert "base_url" in response.content.decode()
    assert not AgentccProviderCredential.no_workspace_objects.filter(
        organization=organization
    ).exists()
    isolated.set_org_config.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize("console", [False, True])
@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
@pytest.mark.parametrize(
    "provider_name",
    [
        "typesafe",
        "perplexity",
        "typesafe-proxy",
        "perplexity-decider",
        "TyPeSaFe",
        "PeRpLeXiTy",
        "typesafe_org_synthetic",
        "perplexity_org_synthetic",
    ],
)
@pytest.mark.parametrize("service_account", [False, True])
def test_decision_vendor_defaults_save_without_base_url(
    auth_client, organization, console, api_format, provider_name, service_account
):
    route, data, original = _decision_save_request(
        organization, console, provider_name, api_format, service_account
    )
    with patch("agentcc.services.gateway_client.get_gateway_client") as operator:
        response = auth_client.post(route, data, format="json")
        operator.assert_not_called()
    assert response.status_code == (200 if console else 201), response.content
    row = AgentccProviderCredential.no_workspace_objects.get(
        organization=organization, provider_name=provider_name
    )
    assert row.base_url == "" and row.api_format == api_format
    credentials = CredentialManager.decrypt(bytes(row.encrypted_credentials))
    assert credentials["api_key"] == "synthetic-provider-key"
    assert ("service_account_json" in credentials) == service_account
    if original is not None:
        assert row.pk == original.pk
        assert bytes(row.encrypted_credentials) == bytes(original.encrypted_credentials)


@pytest.mark.parametrize("api_format", sorted(FORMATS - {"systemone", "typesafe"}))
@pytest.mark.parametrize("provider_name", sorted(KNOWN_PROVIDER_NAMES))
def test_existing_formats_keep_native_name_defaults(api_format, provider_name):
    with patch("agentcc.services.gateway_client.get_gateway_client") as operator:
        ensure_provider_configuration(provider_name, api_format, "")
        operator.assert_not_called()


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
@pytest.mark.parametrize("api_format", sorted(FORMATS - {"systemone", "typesafe"}))
@pytest.mark.parametrize("provider_name", ["operator-openai", "openai", "groq"])
def test_oq3_configured_operator_alias_keeps_default_endpoint(
    auth_client, organization, console, api_format, provider_name
):
    # The control API lists only operator providers with a configured base URL.
    with patch("agentcc.services.gateway_client.get_gateway_client") as operator:
        operator.return_value.get_config.return_value = {
            "provider_ids": [provider_name]
        }
        response = _save_provider(auth_client, console, provider_name, api_format)
    assert response.status_code == (200 if console else 201), response.content
    row = AgentccProviderCredential.no_workspace_objects.get(
        organization=organization, provider_name=provider_name
    )
    assert row.base_url == "" and row.api_format == api_format


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
