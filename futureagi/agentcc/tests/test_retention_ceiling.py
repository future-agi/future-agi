"""privacy.retention days are capped at 36500.

The gateway contract caps both TTLs at 36500 days (100 years), and every
org-config write validates privacy.retention through the generated contract
(validate_org_config).
"""

from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import OrgConfigRejected, validate_org_config

UPDATE_CONFIG = "/agentcc/gateways/default/update-config/"


@pytest.mark.integration
@patch("agentcc.services.config_push._assemble_providers", return_value={})
class TestRetentionCeiling:
    @pytest.mark.parametrize("key", ["request_log_days", "attempt_days"])
    def test_retention_days_above_ceiling_refused(self, _providers, organization, key):
        too_long = AgentccOrgConfig(
            organization=organization,
            version=1,
            privacy={"retention": {key: 36501}},
        )
        with pytest.raises(OrgConfigRejected, match="36500"):
            validate_org_config(too_long)

        longest = AgentccOrgConfig(
            organization=organization, version=1, privacy={"retention": {key: 36500}}
        )
        validate_org_config(longest)

    @pytest.mark.api
    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_config_refuses_the_overflowing_ttl(
        self, _push, _providers, auth_client, organization
    ):
        refused = auth_client.post(
            UPDATE_CONFIG,
            {"privacy": {"retention": {"request_log_days": 1000000}}},
            format="json",
        )
        assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.json()
        assert not AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization
        ).exists()

        ok = auth_client.post(
            UPDATE_CONFIG,
            {"privacy": {"retention": {"request_log_days": 36500}}},
            format="json",
        )
        assert ok.status_code == status.HTTP_200_OK, ok.json()
