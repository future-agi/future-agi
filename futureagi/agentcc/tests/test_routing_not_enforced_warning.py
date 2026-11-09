"""routing.conditional_routes and routing.default_model keep their "stored,
not enforced yet" warning (docs-agentcc-711, item 5; cloud proof
j-20261028t0728-c95dee, record 5-conditional-default).

On the proof's gateway neither key changed a request: a gpt-4o chat reached
the upstream as gpt-4o under a route to gpt-4o-mini, and an unlisted model
was refused rather than sent to the default model. Until the gateway runs
them (G26), update-config saves and pushes them with the warning, and a
route in the config file's shape (name/priority/condition/action) is refused
by the contract.

Pushes go to a recording gateway client through the real push code.
"""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.views import gateway as gateway_views

UPDATE_CONFIG = "/agentcc/gateways/default/update-config/"
ROUTE = {
    "field": "model",
    "operator": "$eq",
    "value": "gpt-4o",
    "target": "gpt-4o-mini",
}


@pytest.fixture
def gateway():
    client = MagicMock()
    with (
        patch.object(config_push, "get_gateway_client", return_value=client),
        patch.object(gateway_views, "get_gateway_client", return_value=client),
    ):
        yield client


def _last_routing(gateway, org):
    sent = [
        call.args[1]
        for call in gateway.set_org_config.call_args_list
        if call.args[0] == str(org.id)
    ]
    assert sent, "nothing was pushed"
    return sent[-1]["routing"]


@pytest.fixture
def active(organization):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True
    )


@pytest.mark.integration
@pytest.mark.api
class TestStoredNotEnforcedYet:
    @pytest.mark.parametrize(
        "key, value",
        [("conditional_routes", [ROUTE]), ("default_model", "gpt-4o-mini")],
    )
    def test_saved_and_pushed_with_the_warning(
        self, auth_client, organization, gateway, active, key, value
    ):
        response = auth_client.post(
            UPDATE_CONFIG, {"routing": {key: value}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["warnings"] == [
            {
                "code": "routing_key_not_enforced",
                "param": f"routing.{key}",
                "message": "stored, not enforced yet",
            }
        ]
        assert _last_routing(gateway, organization)[key] == value

    def test_a_route_in_the_config_file_shape_is_refused(
        self, auth_client, organization, gateway, active
    ):
        route = {
            "name": "r1",
            "priority": 1,
            "condition": {"field": "model", "op": "$eq", "value": "gpt-4o"},
            "action": {"provider": "openai", "model_override": "gpt-4o-mini"},
        }

        response = auth_client.post(
            UPDATE_CONFIG, {"routing": {"conditional_routes": [route]}}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert "routing.conditional_routes.0.name" in response.json()["message"]
        gateway.set_org_config.assert_not_called()
