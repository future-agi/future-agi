"""
The floor on an org's managed_models.default_max_tokens_estimate.

The estimate is the output a managed request that sets no max_tokens is held
for (below the model's own maximum output). The contract's minimum is 1024:

- a save below 1024 (top level, or a rollout's managed_models) is refused
  with 400 naming the field;
- a stored config with a lower value is pushed (and served to the gateway's
  bulk sync) at 1024, so the org keeps its config instead of failing the
  contract and dropping out of the bulk response; the stored row is not
  rewritten;
- 1024 and above, and an unset estimate, are saved and pushed as they are.
"""

import secrets
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import _build_payload

PUSH = "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
FLOOR = 1024


def _rollout(managed_models):
    return {
        "rollout": {
            "version": "v5",
            "percent": 10,
            "sticky_by": "session",
            "routing": {},
            "managed_models": managed_models,
        }
    }


def _stored(organization, **sections):
    """A config row created directly, without the save-time checks."""
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, **sections
    )


@pytest.mark.integration
class TestSaveRefusesAnEstimateBelowTheFloor:
    @pytest.mark.parametrize("value", [1, 100, FLOOR - 1])
    @patch(PUSH, return_value=True)
    def test_top_level(self, _push, auth_client, user, value):
        response = auth_client.post(
            "/agentcc/org-configs/",
            {
                "managed_models": {
                    "enabled": True,
                    "default_max_tokens_estimate": value,
                }
            },
            format="json",
        )
        assert response.status_code == 400, response.json()
        assert "default_max_tokens_estimate" in str(response.json())
        assert not AgentccOrgConfig.no_workspace_objects.filter(
            organization=user.organization
        ).exists()

    @patch(PUSH, return_value=True)
    def test_rollout(self, _push, auth_client, user):
        response = auth_client.post(
            "/agentcc/org-configs/",
            {"routing": _rollout({"enabled": True, "default_max_tokens_estimate": 1})},
            format="json",
        )
        assert response.status_code == 400, response.json()
        assert "default_max_tokens_estimate" in str(response.json())

    @pytest.mark.parametrize("value", [FLOOR, 2048, 4096])
    @patch(PUSH, return_value=True)
    def test_at_or_above_the_floor_is_saved(self, _push, auth_client, user, value):
        response = auth_client.post(
            "/agentcc/org-configs/",
            {
                "managed_models": {
                    "enabled": True,
                    "default_max_tokens_estimate": value,
                }
            },
            format="json",
        )
        assert response.status_code == 200, response.json()
        active = AgentccOrgConfig.no_workspace_objects.get(
            organization=user.organization, is_active=True, deleted=False
        )
        assert active.managed_models["default_max_tokens_estimate"] == value


@pytest.mark.integration
class TestPushReadsAStoredLowEstimateAsTheFloor:
    @pytest.mark.parametrize(
        "spelling", ["default_max_tokens_estimate", "defaultMaxTokensEstimate"]
    )
    def test_top_level(self, user, spelling):
        config = _stored(user.organization, managed_models={spelling: 1})
        payload = _build_payload(
            str(user.organization_id), config, with_upstream_keys=False
        )
        assert payload["managed_models"]["default_max_tokens_estimate"] == FLOOR
        config.refresh_from_db()
        assert config.managed_models == {spelling: 1}, "the stored row was rewritten"

    def test_rollout(self, user):
        config = _stored(
            user.organization,
            routing=_rollout({"default_max_tokens_estimate": 7}),
        )
        payload = _build_payload(
            str(user.organization_id), config, with_upstream_keys=False
        )
        rollout = payload["routing"]["rollout"]
        assert rollout["managed_models"]["default_max_tokens_estimate"] == FLOOR
        config.refresh_from_db()
        assert config.routing["rollout"]["managed_models"] == {
            "default_max_tokens_estimate": 7
        }

    @pytest.mark.parametrize("value", [FLOOR, 2048])
    def test_at_or_above_the_floor_is_pushed_as_stored(self, user, value):
        config = _stored(
            user.organization, managed_models={"default_max_tokens_estimate": value}
        )
        payload = _build_payload(
            str(user.organization_id), config, with_upstream_keys=False
        )
        assert payload["managed_models"]["default_max_tokens_estimate"] == value

    def test_unset_stays_unset(self, user):
        config = _stored(user.organization, managed_models={"enabled": True})
        payload = _build_payload(
            str(user.organization_id), config, with_upstream_keys=False
        )
        assert "default_max_tokens_estimate" not in payload["managed_models"]


@pytest.mark.integration
def test_bulk_sync_keeps_an_org_with_a_stored_low_estimate(organization):
    _stored(organization, managed_models={"default_max_tokens_estimate": 1})
    token = secrets.token_hex(32)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", token):
        response = client.get("/agentcc/org-configs/bulk/")
    assert response.status_code == 200, response.content
    configs = response.json()["result"]
    assert str(organization.id) in configs, "the org dropped out of the bulk sync"
    managed = configs[str(organization.id)]["managed_models"]
    assert managed["default_max_tokens_estimate"] == FLOOR


def test_the_floor_is_one_number_on_both_sides():
    """The contract's minimum, Django's push floor and the gateway's
    billing.MinOrgDefaultMaxOut are the same number."""
    import json
    import re
    from pathlib import Path

    from agentcc.services.config_push import MANAGED_ESTIMATE_FLOOR

    root = Path(__file__).resolve().parents[3]
    schema = json.loads(
        (root / "api_contracts/gateway/agentcc-admin.schema.json").read_text()
    )
    managed = schema["$defs"]["ManagedModelsConfig"]["properties"]
    assert managed["default_max_tokens_estimate"]["minimum"] == FLOOR
    go = (root / "agentcc-gateway/internal/billing/estimate.go").read_text()
    match = re.search(r"^const MinOrgDefaultMaxOut = (\d+)$", go, re.MULTILINE)
    assert match, "MinOrgDefaultMaxOut not found in estimate.go"
    assert int(match.group(1)) == FLOOR == MANAGED_ESTIMATE_FLOOR
