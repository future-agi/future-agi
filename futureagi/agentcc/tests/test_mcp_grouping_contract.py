"""Configuration hints are bounded stored config, never vendor tool metadata."""

import pytest
from pydantic import ValidationError

from agentcc.contracts.gateway_admin import MCPServerOrgConfig, MCPToolIdentity
from agentcc.serializers.mcp_served import MCPServedIdentitySerializer


@pytest.mark.unit
class TestMCPGroupingContract:
    def test_configured_toolkit_list_survives_typed_round_trip(self):
        value = {
            "url": "https://backend.composio.dev/v3/mcp/synthetic-fixture",
            "toolkits": ["share", "share_point", "composio_search"],
        }
        assert (
            MCPServerOrgConfig.model_validate(value).model_dump(exclude_none=True)
            == value
        )

    @pytest.mark.parametrize("toolkits", [["x" * 65], [""], ["gmail"] * 257])
    def test_configured_toolkit_list_is_bounded(self, toolkits):
        with pytest.raises(ValidationError):
            MCPServerOrgConfig.model_validate({"toolkits": toolkits})

    @pytest.mark.parametrize("platform", ["composio", "pipedream"])
    def test_configuration_provenance_survives_both_contracts(self, platform):
        value = {
            "platform": platform,
            "toolkit_slug": "google_sheets",
            "toolkit_name": "google_sheets",
            "source": "from server configuration",
        }
        assert MCPToolIdentity.model_validate(value).model_dump() == value
        serializer = MCPServedIdentitySerializer(data=value)
        assert serializer.is_valid(), serializer.errors
        assert dict(serializer.validated_data) == value

    def test_vendor_asserted_source_is_not_a_supported_claim(self):
        with pytest.raises(ValidationError):
            MCPToolIdentity.model_validate(
                {
                    "platform": "composio",
                    "toolkit_slug": "gmail",
                    "toolkit_name": "gmail",
                    "source": "vendor-asserted",
                }
            )
