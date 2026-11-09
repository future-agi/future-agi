"""W12 key-based client installs are independent of every OAuth release gate."""

import pytest
from django.conf import settings
from django.test import override_settings

from agentcc import mcp_features
from agentcc.serializers.contracts import MCPFeaturesSerializer
from agentcc.tests.test_mcp_admin_api import GATEWAY, V1_STATUS, faked_gateway

FLAG = "AGENTCC_MCP_CLIENT_INSTALLS_ENABLED"
OTHER_FLAGS = {
    "AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED": False,
    "AGENTCC_MCP_CLIENT_OAUTH_ENABLED": False,
    "AGENTCC_MCP_K2_APPS_ENABLED": False,
}


def test_deployment_default_is_off():
    assert getattr(settings, FLAG, None) is False


@pytest.mark.parametrize("value", [False, None, "true", 1, True])
def test_client_installs_requires_explicit_boolean(value):
    with override_settings(**{**OTHER_FLAGS, FLAG: value}):
        features = mcp_features.capabilities()
    assert features == {
        "upstream_oauth": False,
        "client_oauth": False,
        "k2_apps": False,
        "client_installs": value is True,
        "tool_call_explorer": False,
        "tool_call_download": False,
        "siem_export": False,
    }
    serializer = MCPFeaturesSerializer(data=features)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == features


def test_missing_client_installs_setting_is_off_even_with_oauth_on():
    with override_settings(**dict.fromkeys(OTHER_FLAGS, True), **{FLAG: True}):
        delattr(settings, FLAG)
        assert mcp_features.capabilities()["client_installs"] is False


@pytest.mark.django_db
@pytest.mark.parametrize("enabled", [False, True])
def test_status_reports_deployment_capability_without_enabling_oauth(
    auth_client, enabled
):
    with (
        override_settings(**{**OTHER_FLAGS, FLAG: enabled}),
        faked_gateway() as gateway,
    ):
        gateway.on("GET", "/-/mcp/status", body=V1_STATUS)
        response = auth_client.get(f"{GATEWAY}/mcp-status/")
    assert response.status_code == 200, response.json()
    assert gateway.calls_to("GET", "/-/mcp/status")
    assert response.json()["result"]["features"] == {
        "upstream_oauth": False,
        "client_oauth": False,
        "k2_apps": False,
        "client_installs": enabled,
        "tool_call_explorer": False,
        "tool_call_download": False,
        "siem_export": False,
    }
