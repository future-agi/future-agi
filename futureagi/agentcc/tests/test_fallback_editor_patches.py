"""Apply the patch bodies pinned by both Console editors through update-config."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig

_FIXTURES = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "api_contracts/gateway/fixtures/fallback-editor-patches.json"
    ).read_text()
)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("storage", ["camel-only", "canonical-only", "both-spelling"])
@pytest.mark.parametrize("edit", _FIXTURES["edits"], ids=lambda edit: edit["name"])
def test_console_fallback_patch_stores_and_pushes_only_effective_chains(
    auth_client, organization, storage, edit
):
    routing = {"strategy": "cost"}
    if storage == "camel-only":
        routing["modelFallbacks"] = copy.deepcopy(_FIXTURES["chains"])
    else:
        routing["model_fallbacks"] = copy.deepcopy(_FIXTURES["chains"])
        if storage == "both-spelling":
            routing["modelFallbacks"] = copy.deepcopy(_FIXTURES["shadowedChains"])
    original = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, routing=routing
    )

    # Vitest asserts the actual POST bodies from both editors against this same
    # fixture. Mock only HTTP transport, not the config writer or serialization.
    with patch(
        "agentcc.services.gateway_client.GatewayClient._request", return_value={}
    ) as transport:
        response = auth_client.post(
            "/agentcc/gateways/default/update-config/",
            copy.deepcopy(edit["patch"]),
            format="json",
        )

    assert response.status_code == 200, response.json()
    assert response.json()["result"]["gateway_synced"] is True
    expected = {"strategy": "cost", "model_fallbacks": edit["after"]}
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    assert active.pk != original.pk
    assert active.routing == expected
    transport.assert_called_once()
    assert transport.call_args.args[0] == "PUT"
    assert transport.call_args.kwargs["json_body"]["routing"] == expected
    original.refresh_from_db()
    assert original.routing == routing
    assert not original.is_active
