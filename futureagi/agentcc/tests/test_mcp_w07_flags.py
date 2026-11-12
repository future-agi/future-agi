"""W07: pure middleware/service-entry tests; no database or provider access."""

import itertools
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from agentcc import mcp_features
from agentcc.services import connector_catalog as catalog

CATALOG = "AGENTCC_MCP_CATALOG_ENABLED"
VAULT = "AGENTCC_MCP_CREDENTIAL_VAULT_ENABLED"
VIRTUAL = "AGENTCC_MCP_VIRTUAL_SERVERS_ENABLED"
FLAGS = (CATALOG, VAULT, VIRTUAL)
MATRIX = list(itertools.product((False, True), repeat=3))


def respond(path, method="get"):
    downstream = Mock(return_value=HttpResponse(status=204))
    response = mcp_features.MCPSubfeatureMiddleware(downstream)(
        getattr(RequestFactory(), method)(path)
    )
    return response, downstream


def test_source_defaults_on(settings):
    for flag in FLAGS:
        assert getattr(settings, flag, None) is True


@pytest.mark.parametrize("catalog_on,vault_on,virtual_on", MATRIX)
def test_connect_matrix_refuses_before_probe_or_any_write(
    catalog_on, vault_on, virtual_on
):
    class ReachedExistingBody(Exception):
        pass

    with (
        override_settings(
            **dict(zip(FLAGS, (catalog_on, vault_on, virtual_on), strict=True))
        ),
        patch.object(catalog, "load_bundle", side_effect=ReachedExistingBody) as bundle,
        patch.object(catalog, "_listing") as probe,
        patch.object(catalog, "write_config_version_with") as writer,
        patch.object(catalog.mcp_credentials, "store") as store,
        patch.object(catalog.mcp_credentials, "revoke_server_credentials") as revoke,
    ):
        if catalog_on and vault_on and virtual_on:
            with pytest.raises(ReachedExistingBody):
                catalog.add_connector(
                    None,
                    "fixture",
                    provider_id=None,
                    mode="agent",
                    url_variables={},
                    actor=None,
                )
            bundle.assert_called_once()
        else:
            with pytest.raises(catalog.ConnectorConflict) as error:
                catalog.add_connector(
                    None,
                    "fixture",
                    provider_id=None,
                    mode="agent",
                    url_variables={},
                    actor=None,
                )
            expected = (
                "mcp_catalog_disabled"
                if not catalog_on
                else (
                    "mcp_credential_vault_disabled"
                    if not vault_on
                    else "mcp_virtual_servers_disabled"
                )
            )
            assert error.value.code == expected
            bundle.assert_not_called()
        # Even ON, a failure before validation completes cannot leave partial writes.
        probe.assert_not_called()
        writer.assert_not_called()
        store.assert_not_called()
        revoke.assert_not_called()


@pytest.mark.parametrize(
    "flag,path",
    [
        (CATALOG, "/agentcc/connectors/catalog/"),
        (CATALOG, "/agentcc/connectors/catalog/acme/"),
        (CATALOG, "/agentcc/connectors/apps/acme/add/"),
        (CATALOG, "/agentcc/connectors/apps/acme/oauth/"),
        (CATALOG, "/agentcc/connectors/apps/acme/oauth/discovery/"),
        (VAULT, "/agentcc/mcp/credentials/"),
        (VAULT, "/agentcc/mcp/credentials/fixture/rotate/"),
        (VAULT, "/agentcc/mcp/credentials/fixture/use-managed/"),
        (VIRTUAL, "/agentcc/mcp/service/virtual-servers/ensure/"),
        (VIRTUAL, "/agentcc/mcp/service/agent-keys/"),
    ],
)
@pytest.mark.parametrize("on", [False, True])
def test_api_off_on_before_io(flag, path, on):
    values = dict.fromkeys(FLAGS, True)
    values[flag] = on
    with override_settings(
        **values,
        AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED=True,
        AGENTCC_MCP_K2_APPS_ENABLED=True,
    ):
        with patch.object(mcp_features, "catalog_app_visible", return_value=True):
            response, downstream = respond(path, "post")
    assert response.status_code == (204 if on else 404)
    if not on:
        assert json.loads(response.content)["code"].endswith("_disabled")
        downstream.assert_not_called()
    else:
        downstream.assert_called_once()


@pytest.mark.parametrize(
    "path",
    [
        "/agentcc/gateways/default/update-mcp-server/",
        "/agentcc/gateways/default/remove-mcp-server/",
        "/agentcc/gateways/default/mcp-tools/",
        "/agentcc/gateways/default/test-mcp-tool/",
        "/agentcc/gateways/default/mcp-served-tools/",
        "/agentcc/mcp/probe/",
        "/mcp/oauth/authorize/",
        "/mcp/oauth/consent/",
        "/mcp/oauth/token/",
        "/mcp/oauth/approve-info/",
        "/mcp/oauth/approve/",
        "/agentcc/request-logs/export/",
        "/agentcc/mcp/service/agent-keys/fixture/revoke/",
    ],
)
def test_pre_existing_routes_and_key_cleanup_survive_all_off(path):
    with override_settings(**dict.fromkeys(FLAGS, False)):
        response, downstream = respond(path, "delete")
    assert response.status_code == 204
    downstream.assert_called_once()


@pytest.mark.parametrize("value", [None, False, "true", 1, True])
def test_capabilities_fail_closed(value):
    with override_settings(**dict.fromkeys(FLAGS, value)):
        for name in ("catalog", "credential_vault", "virtual_servers"):
            assert mcp_features.capabilities()[name] is (value is True)
    with patch.object(mcp_features, "settings", SimpleNamespace()):
        assert not mcp_features.capabilities()["catalog"]


def test_disabled_sections_preserved_on_unrelated_edits_but_not_activation():
    from rest_framework.exceptions import ValidationError

    from agentcc.mcp_release_gates import refuse_disabled_config

    stored = {
        "virtual_servers": {"named": {"members": ["static"]}},
        "servers": {"static": {"url": "https://fixture.invalid"}},
    }
    with override_settings(**dict.fromkeys(FLAGS, False)):
        refuse_disabled_config({**stored, "enabled": True}, stored)
        with pytest.raises(ValidationError):
            refuse_disabled_config({**stored, "virtual_servers": {"new": {}}}, stored)
        with pytest.raises(ValidationError):
            refuse_disabled_config({"servers": stored["servers"]}, stored)
        with pytest.raises(ValidationError):
            refuse_disabled_config(stored, stored, activation=True)
        with pytest.raises(ValidationError):
            refuse_disabled_config(
                {"servers": stored["servers"]}, stored, activation=True
            )
        refuse_disabled_config({"servers": stored["servers"]}, {})


@pytest.mark.parametrize("spelling", ["virtual_servers", "virtualServers"])
@pytest.mark.parametrize(
    "operation", ["create", "change", "delete", "activate", "rollback"]
)
def test_disabled_virtual_alias_mutations_refused(spelling, operation):
    from rest_framework.exceptions import ValidationError

    from agentcc.mcp_release_gates import refuse_disabled_config

    named = {"restricted": {"members": ["static"], "access": {"keys": ["key-id"]}}}
    saved = {spelling: named}
    candidate, stored = {
        "create": (saved, {}),
        "change": ({spelling: {"other": {"members": ["static"]}}}, saved),
        "delete": ({}, saved),
        "activate": (saved, saved),
        "rollback": ({}, saved),
    }[operation]
    with override_settings(**dict.fromkeys(FLAGS, False)):
        with pytest.raises(ValidationError) as error:
            refuse_disabled_config(
                candidate, stored, activation=operation in {"activate", "rollback"}
            )
    assert error.value.get_codes() == ["mcp_virtual_servers_disabled"]


@pytest.mark.parametrize("old_spelling", ["virtual_servers", "virtualServers"])
@pytest.mark.parametrize("new_spelling", ["virtual_servers", "virtualServers"])
def test_disabled_virtual_aliases_preserve_unrelated_static_edits(
    old_spelling, new_spelling
):
    from agentcc.mcp_release_gates import refuse_disabled_config

    named = {"restricted": {"members": ["static"]}}
    stored = {
        old_spelling: named,
        "servers": {"static": {"url": "https://old.invalid"}},
    }
    candidate = {
        new_spelling: named,
        "servers": {"static": {"url": "https://new.invalid"}},
    }
    with override_settings(**dict.fromkeys(FLAGS, False)):
        refuse_disabled_config(candidate, stored)


@pytest.mark.parametrize("canonical", [None, {}])
def test_disabled_virtual_alias_precedence_uses_present_canonical_field(canonical):
    from rest_framework.exceptions import ValidationError

    from agentcc.mcp_release_gates import refuse_disabled_config

    named = {"restricted": {"members": ["static"]}}
    # AliasChoices prefers the present snake-case field, even if empty. The
    # ordinary contract validator separately rejects a duplicate alias as extra.
    shadowed = {"virtual_servers": canonical, "virtualServers": named}
    with override_settings(**dict.fromkeys(FLAGS, False)):
        refuse_disabled_config(shadowed, {})
        refuse_disabled_config({}, shadowed)
        with pytest.raises(ValidationError):
            refuse_disabled_config(shadowed, {"virtualServers": named})
        with pytest.raises(ValidationError):
            refuse_disabled_config({"virtualServers": named}, shadowed)


@pytest.mark.parametrize("spelling", ["virtual_servers", "virtualServers"])
def test_virtual_alias_mutations_allowed_when_enabled(spelling):
    from agentcc.mcp_release_gates import refuse_disabled_config

    saved = {spelling: {"restricted": {"members": ["static"]}}}
    with override_settings(**dict.fromkeys(FLAGS, True)):
        refuse_disabled_config(saved, {})
        refuse_disabled_config({}, saved)
        refuse_disabled_config(saved, saved, activation=True)
        refuse_disabled_config({}, saved, activation=True)


def test_c26_status_schema_preserves_all_independent_capabilities():
    from agentcc.serializers import contracts

    assert hasattr(contracts, "MCPFeaturesSerializer")
    with override_settings(
        **dict.fromkeys(FLAGS, True),
        AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED=True,
        AGENTCC_MCP_TOOL_CALL_DOWNLOAD_ENABLED=True,
        AGENTCC_SIEM_EXPORT_ENABLED=True,
    ):
        values = mcp_features.capabilities()
    serializer = contracts.MCPFeaturesSerializer(data=values)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == values
    assert "features" in contracts.GatewayMCPStatusResultSerializer().fields


@pytest.mark.parametrize("siem", [False, True])
@pytest.mark.parametrize("switches", MATRIX)
def test_c26_siem_gate_independent_of_w07_and_w14(siem, switches):
    with override_settings(
        **dict(zip(FLAGS, switches, strict=True)),
        AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED=False,
        AGENTCC_MCP_TOOL_CALL_DOWNLOAD_ENABLED=False,
        AGENTCC_SIEM_EXPORT_ENABLED=siem,
    ):
        response, downstream = respond("/agentcc/siem/destinations/")
    assert response.status_code == (204 if siem else 404)
    if siem:
        downstream.assert_called_once()
    else:
        downstream.assert_not_called()
        assert response["Cache-Control"] == "no-store"
