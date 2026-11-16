"""Connect-only Copilot Studio and Foundry agents: admission, connection check and build inputs."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from simulate.serializers.harness_job import (
    HarnessAgentSerializer,
    complete_provider_families,
    missing_provider_credentials,
)
from simulate.services.harness_agent_versions import _connector_agent_type
from simulate.services.harness_credential_probes import probe_microsoft_agent
from simulate.services.harness_provider import _preflight_credential_probe
from simulate.services.hosted_harness_gateway import (
    _authoring_egress_domains,
    _provider_import_authoring_material,
    _resolved_egress_domains,
)
from simulate.services.microsoft_agents import (
    COPILOT_DIRECTLINE_SECRET,
    MICROSOFT_CLIENT_SECRET,
    microsoft_config_error,
)

BOT_ID = "11111111-2222-3333-4444-555555555555"
COPILOT_CONFIG = {
    "tenant_id": "tenant-1",
    "client_id": "client-1",
    "dataverse_url": "https://org123.crm.dynamics.com",
    "agent_id": BOT_ID,
}
FOUNDRY_CONFIG = {
    "tenant_id": "tenant-1",
    "client_id": "client-1",
    "project_endpoint": "https://acme.services.ai.azure.com/api/projects/support",
    "agent_name": "order-assistant",
}
SECRET = {MICROSOFT_CLIENT_SECRET: "client-secret"}


def _response(status, body=None):
    response = Mock(status_code=status)
    response.json.return_value = body if body is not None else {}
    return response


@pytest.mark.parametrize(
    ("connector", "config", "error"),
    [
        ("copilot_studio", COPILOT_CONFIG, None),
        ("foundry", FOUNDRY_CONFIG, None),
        (
            "copilot_studio",
            {**COPILOT_CONFIG, "agent_id": "", "tenant_id": ""},
            "Tenant ID, Agent ID are required",
        ),
        (
            "copilot_studio",
            {**COPILOT_CONFIG, "dataverse_url": "https://org.dynamics.com.evil.io"},
            "Dataverse environment URL must be an https URL on *.dynamics.com",
        ),
        (
            "foundry",
            {**FOUNDRY_CONFIG, "project_endpoint": "http://acme.services.ai.azure.com"},
            "Project endpoint must be an https URL on",
        ),
    ],
)
def test_config_must_name_the_agent_on_a_microsoft_host(connector, config, error):
    result = microsoft_config_error(connector, config)
    if error is None:
        assert result is None
    else:
        assert result is not None and result.startswith(error)


def test_serializer_admits_connect_only_and_refuses_other_modes():
    ok = HarnessAgentSerializer(
        data={
            "connector": "foundry",
            "mode": "connect_only",
            "config": FOUNDRY_CONFIG,
        }
    )
    assert ok.is_valid(), ok.errors

    imported = HarnessAgentSerializer(
        data={
            "connector": "foundry",
            "mode": "provider_import",
            "config": FOUNDRY_CONFIG,
        }
    )
    assert not imported.is_valid()
    assert "connect_only only" in str(imported.errors)

    bad_host = HarnessAgentSerializer(
        data={
            "connector": "copilot_studio",
            "mode": "connect_only",
            "config": {**COPILOT_CONFIG, "dataverse_url": "https://example.com"},
        }
    )
    assert not bad_host.is_valid()


def test_client_secret_is_required_but_never_completes_an_auto_family():
    copilot = {"connector": "copilot_studio", "config": COPILOT_CONFIG}
    assert missing_provider_credentials(copilot) == [MICROSOFT_CLIENT_SECRET]

    # A repository's own AZURE_CLIENT_SECRET must not stand in for a voice provider key.
    auto = {
        "connector": "auto",
        "secret_refs": {MICROSOFT_CLIENT_SECRET: {"manager": "platform-vault"}},
    }
    assert complete_provider_families(auto) == []


def test_microsoft_agents_are_stored_as_text_agents():
    assert _connector_agent_type("copilot_studio") == "text"
    assert _connector_agent_type("foundry") == "text"


@patch("simulate.services.harness_credential_probes.requests.get")
@patch("simulate.services.harness_credential_probes.requests.post")
def test_copilot_check_signs_in_then_reads_the_agent_and_its_parts(post, get):
    post.return_value = _response(200, {"access_token": "entra-token"})
    get.side_effect = [
        _response(200, {"name": "Order Assistant"}),
        _response(200, {"value": [{"componenttype": 15}]}),
    ]

    results = probe_microsoft_agent("copilot_studio", COPILOT_CONFIG, SECRET)

    assert [(r.provider, r.ok) for r in results] == [
        ("copilot_studio", True),
        ("copilot_studio_target", True),
    ]
    assert results[1].target_name == "Order Assistant"
    assert post.call_args.args[0] == (
        "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
    )
    assert post.call_args.kwargs["data"]["scope"] == (
        "https://org123.crm.dynamics.com/.default"
    )
    assert get.call_args_list[0].args[0] == (
        f"https://org123.crm.dynamics.com/api/data/v9.2/bots({BOT_ID})?$select=name"
    )
    assert "botcomponents" in get.call_args_list[1].args[0]


@patch("simulate.services.harness_credential_probes.requests.get")
@patch("simulate.services.harness_credential_probes.requests.post")
def test_a_wrong_secret_is_named_and_the_agent_is_not_read(post, get):
    post.return_value = _response(
        401,
        {
            "error": "invalid_client",
            "error_description": "AADSTS7000215: Invalid client secret provided.\r\n"
            "Trace ID: abc",
        },
    )

    results = probe_microsoft_agent("foundry", FOUNDRY_CONFIG, SECRET)

    assert len(results) == 1
    assert results[0].ok is False
    assert "Client secret is wrong" in results[0].message
    get.assert_not_called()


@patch("simulate.services.harness_credential_probes.requests.get")
@patch("simulate.services.harness_credential_probes.requests.post")
def test_copilot_role_that_cannot_read_parts_is_named(post, get):
    post.return_value = _response(200, {"access_token": "entra-token"})
    get.side_effect = [_response(200, {"name": "Agent"}), _response(403)]

    results = probe_microsoft_agent("copilot_studio", COPILOT_CONFIG, SECRET)

    assert results[1].ok is False
    assert "Copilot component rows" in results[1].message


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (403, "Foundry User role"),
        (404, "No agent named 'order-assistant'"),
    ],
)
@patch("simulate.services.harness_credential_probes.requests.get")
@patch("simulate.services.harness_credential_probes.requests.post")
def test_foundry_read_failures_say_what_to_fix(post, get, status, message):
    post.return_value = _response(200, {"access_token": "entra-token"})
    get.return_value = _response(status)

    results = probe_microsoft_agent("foundry", FOUNDRY_CONFIG, SECRET)

    assert results[1].provider == "foundry_target"
    assert results[1].ok is False
    assert message in results[1].message
    assert get.call_args.args[0] == (
        "https://acme.services.ai.azure.com/api/projects/support"
        "/agents/order-assistant?api-version=v1"
    )


@patch("simulate.services.harness_credential_probes.requests.get")
@patch("simulate.services.harness_credential_probes.requests.post")
def test_direct_line_is_checked_only_when_given(post, get):
    post.side_effect = [
        _response(200, {"access_token": "entra-token"}),
        _response(403),
    ]
    get.side_effect = [_response(200, {"name": "Agent"}), _response(200, {})]

    results = probe_microsoft_agent(
        "copilot_studio",
        COPILOT_CONFIG,
        {**SECRET, COPILOT_DIRECTLINE_SECRET: "dl-secret"},
    )

    assert results[-1].provider == "copilot_directline"
    assert results[-1].ok is False
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer dl-secret"


def test_preflight_runs_the_microsoft_check_for_connect_only():
    payload = {
        "source": {"kind": "provider"},
        "agent": {
            "connector": "foundry",
            "mode": "connect_only",
            "config": FOUNDRY_CONFIG,
        },
        "credential_values": {"azure_client_secret": "client-secret"},
    }
    with patch(
        "simulate.services.harness_credential_probes.probe_microsoft_agent",
        return_value=[],
    ) as probe:
        _preflight_credential_probe(payload)

    probe.assert_called_once_with(
        "foundry", FOUNDRY_CONFIG, {MICROSOFT_CLIENT_SECRET: "client-secret"}
    )


def test_build_gets_only_the_client_secret_and_microsoft_egress():
    payload = {"agent": {"connector": "copilot_studio", "mode": "connect_only"}}
    with patch(
        "simulate.services.hosted_harness_gateway.PlatformSecretResolver.resolve",
        return_value={
            MICROSOFT_CLIENT_SECRET: "client-secret",
            COPILOT_DIRECTLINE_SECRET: "not-needed-for-a-build",
        },
    ):
        material, connector = _provider_import_authoring_material(
            SimpleNamespace(), payload
        )

    assert connector == "copilot_studio"
    assert material == {MICROSOFT_CLIENT_SECRET: "client-secret"}

    domains = _authoring_egress_domains(
        {},
        "copilot_studio",
        provider_profile=True,
        agent_config=COPILOT_CONFIG,
    )
    assert domains[:2] == ["login.microsoftonline.com", "org123.crm.dynamics.com"]
    assert "login.microsoftonline.com" not in _authoring_egress_domains(
        {}, "copilot_studio", provider_profile=False, agent_config=COPILOT_CONFIG
    )


@pytest.mark.parametrize(
    ("connector", "config", "agent_host"),
    [
        ("foundry", FOUNDRY_CONFIG, "acme.services.ai.azure.com"),
        ("copilot_studio", COPILOT_CONFIG, "org123.crm.dynamics.com"),
    ],
)
def test_run_sandbox_launch_also_allows_the_microsoft_hosts(connector, config, agent_host):
    # The launch path builds its own allowlist; a run that authors on a cache miss reads
    # the agent from there, so it needs Entra and the agent's host as well.
    payload = {"agent": {"connector": connector, "mode": "connect_only", "config": config}}
    domains = _resolved_egress_domains(payload, {MICROSOFT_CLIENT_SECRET: "opaque"})
    assert {"login.microsoftonline.com", agent_host} <= set(domains)
