"""The tools and managed-tool rules hold on every config write path.

The byok_only refusal (400 capability_managed_not_allowed) is an mcp rule: it runs
on the parsed mcp section (so a multipart save, whose mcp arrives as a JSON string,
is refused too), it is keyed under mcp, and the writer reruns it for update-config.
update-config also reruns validate_tools' rules, and a managed_tools glob is checked
under every spelling of the key.
"""

import json
from unittest.mock import patch

import pytest
from django.http import QueryDict
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.org_config import (
    CAPABILITY_MANAGED_NOT_ALLOWED,
    AgentccOrgConfigWriteSerializer,
)

ORG_CONFIGS = "/agentcc/org-configs/"
UPDATE_CONFIG = "/agentcc/gateways/default/update-config/"
SAVE_PUSH = "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"


def _managed(*allow, key="managed_tools"):
    return {"enabled": True, key: {"enabled": True, "allow": list(allow)}}


def _configs(organization):
    return AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization, deleted=False
    )


def test_byok_only_refusal_is_keyed_under_mcp():
    serializer = AgentccOrgConfigWriteSerializer(
        data={"mcp": _managed("payments/stripe")}
    )
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"mcp"}, serializer.errors
    assert serializer.errors["mcp"][0].code == CAPABILITY_MANAGED_NOT_ALLOWED


def test_byok_only_refused_when_mcp_arrives_as_a_json_string():
    """A multipart or form save carries mcp as a JSON string; the refusal reads the
    parsed section."""
    for key in ("managed_tools", "managedTools"):
        data = QueryDict(mutable=True)
        data["mcp"] = json.dumps(_managed("search/brave", key=key))
        serializer = AgentccOrgConfigWriteSerializer(data=data)
        assert not serializer.is_valid(), key
        assert serializer.errors["mcp"][0].code == CAPABILITY_MANAGED_NOT_ALLOWED


def test_managed_tools_globs_checked_under_every_spelling():
    """The glob shape check reads every spelling of managed_tools."""
    for key in ("managed_tools", "managedTools"):
        serializer = AgentccOrgConfigWriteSerializer(
            data={"mcp": _managed("not a glob", key=key)}
        )
        assert not serializer.is_valid(), key
        assert "not a glob" in str(serializer.errors["mcp"]), (key, serializer.errors)


@pytest.mark.integration
@pytest.mark.api
@patch(SAVE_PUSH, return_value=True)
@patch("agentcc.views.gateway.push_org_config", return_value=True)
@patch("agentcc.services.config_push._assemble_providers", return_value={})
class TestToolsRulesOnEveryWrite:
    def test_multipart_save_naming_a_byok_only_vendor_is_400(
        self, _providers, _gw_push, _push, auth_client, organization
    ):
        response = auth_client.post(
            ORG_CONFIGS,
            {"mcp": json.dumps(_managed("search/brave"))},
            format="multipart",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert CAPABILITY_MANAGED_NOT_ALLOWED in str(response.json())
        assert not _configs(organization).exists()

    def test_update_config_refuses_a_byok_only_managed_glob(
        self, _providers, _gw_push, _push, auth_client, organization
    ):
        for key in ("managed_tools", "managedTools"):
            response = auth_client.post(
                UPDATE_CONFIG,
                {"mcp": _managed("sandboxes/e2b", key=key)},
                format="json",
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST, (
                key,
                response.json(),
            )
            assert response.json()["code"] == CAPABILITY_MANAGED_NOT_ALLOWED, (
                key,
                response.json(),
            )
        assert not _configs(organization).exists()

        ok = auth_client.post(
            UPDATE_CONFIG, {"mcp": _managed("search/*", "search/exa")}, format="json"
        )
        assert ok.status_code == status.HTTP_200_OK, ok.json()

    def test_update_config_reruns_validate_tools(
        self, _providers, _gw_push, _push, auth_client, organization
    ):
        for tools in (
            {"policy": {"domains": {"allow": ["https://example.com"]}}},
            {"capabilities": {"Search!": {"enabled": True}}},
            {"capabilities": {"search": {"providers": ["exa", "exa"]}}},
            {"capabilities": {"search": {"enabled": "yes"}}},
        ):
            response = auth_client.post(UPDATE_CONFIG, {"tools": tools}, format="json")
            assert response.status_code == status.HTTP_400_BAD_REQUEST, (
                tools,
                response.json(),
            )
        assert not _configs(organization).exists()

        tools = {"capabilities": {"search": {"enabled": True, "providers": ["exa"]}}}
        ok = auth_client.post(UPDATE_CONFIG, {"tools": tools}, format="json")
        assert ok.status_code == status.HTTP_200_OK, ok.json()
        stored = _configs(organization).get(is_active=True).tools
        assert stored["capabilities"]["search"]["providers"] == ["exa"]
