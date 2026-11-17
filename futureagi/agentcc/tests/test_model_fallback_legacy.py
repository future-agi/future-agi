"""Regression coverage for pre-limit saved chains and their repair paths."""

import copy
import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.model_fallback_bounds import dump_stored_org_config
from agentcc.tests.test_model_fallback_bounds import MODEL, URLS, routing_with_chain


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("spelling", ["model_fallbacks", "modelFallbacks"])
@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("change", ["lengthen", "replace", "reorder", "shorten", "add"])
def test_legacy_chain_edits_still_refuse_over_limit_results(
    auth_client, organization, url, spelling, candidate, change
):
    routing = routing_with_chain(20, spelling, candidate)
    original = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, routing=routing
    )
    updated = copy.deepcopy(routing)
    section = updated["rollout"]["routing"] if candidate else updated
    chain = section[spelling][MODEL]
    if change == "lengthen":
        chain.append("example/new")
    elif change == "replace":
        chain[-1] = "example/new"
    elif change == "reorder":
        chain.reverse()
    elif change == "shorten":
        chain.pop()  # Even a shortened edit must fit the limit.
    else:
        section[spelling]["example/added"] = chain[:]
    with patch(
        "agentcc.services.gateway_client.GatewayClient._request", return_value={}
    ) as transport:
        response = auth_client.post(url, {"routing": updated}, format="json")
    assert response.status_code == 400, response.json()
    assert "15" in json.dumps(response.json())
    assert (
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization).count()
        == 1
    )
    original.refresh_from_db()
    assert original.is_active and original.routing == routing
    transport.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("spelling", ["model_fallbacks", "modelFallbacks"])
@pytest.mark.parametrize("candidate", [False, True])
def test_activation_and_rollback_keep_legacy_chains(
    auth_client, organization, spelling, candidate
):
    routing = routing_with_chain(20, spelling, candidate)
    legacy = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=False, routing=routing
    )
    latest = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=2, is_active=True, routing={}
    )
    with patch(
        "agentcc.services.gateway_client.GatewayClient._request", return_value={}
    ) as transport:
        response = auth_client.post(f"/agentcc/org-configs/{legacy.pk}/activate/")
        assert response.status_code == 200, response.json()
        pushed = transport.call_args.kwargs["json_body"]["routing"]
        if candidate:
            pushed = pushed["rollout"]["routing"]
        assert len(pushed["model_fallbacks"][MODEL]) == 20
        response = auth_client.post(f"/agentcc/org-configs/{latest.pk}/activate/")
        assert response.status_code == 200, response.json()
        response = auth_client.post(f"/agentcc/org-configs/{legacy.pk}/activate/")
        assert response.status_code == 200, response.json()
        assert transport.call_count == 3
    legacy.refresh_from_db()
    assert legacy.routing == routing and legacy.is_active


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("spelling", ["model_fallbacks", "modelFallbacks"])
def test_provider_credential_save_with_legacy_20_chain(
    auth_client, organization, spelling
):
    routing = routing_with_chain(20, spelling)
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, routing=routing
    )
    with patch(
        "agentcc.services.gateway_client.GatewayClient._request", return_value={}
    ) as transport:
        response = auth_client.post(
            "/agentcc/gateways/default/update-provider/",
            {
                "name": "openai",
                "config": {"api_key": "synthetic-test-credential", "models": [MODEL]},
            },
            format="json",
        )
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["gateway_synced"] is True
    pushed = transport.call_args.kwargs["json_body"]
    assert len(pushed["routing"]["model_fallbacks"][MODEL]) == 20
    assert pushed["providers"]["openai"]["api_key"] == "synthetic-test-credential"
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    assert active.routing == routing


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("long_name", [False, True])
def test_many_chains_have_one_short_actionable_error(auth_client, long_name):
    name = "example/primary" + ("x" * 500 if long_name else "")
    routing = {"model_fallbacks": {name: ["backup"] * 16, "two": ["backup"] * 17}}
    routing["rollout"] = routing_with_chain(18, candidate=True)["rollout"]
    with patch(
        "agentcc.services.gateway_client.GatewayClient._request", return_value={}
    ) as transport:
        response = auth_client.post(URLS[1], {"routing": routing}, format="json")
    assert response.status_code == 400, response.json()
    message = response.json()["result"]
    assert isinstance(message, str)
    assert len(message) < 240
    assert "example/primary" in message and "limit is 15" in message
    assert "and 2 more chains over the limit" in message
    assert ".;" not in message and "\n" not in message
    transport.assert_not_called()


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("spelling", ["model_fallbacks", "modelFallbacks"])
def test_legacy_dump_preserves_full_chain_without_mutating_input(candidate, spelling):
    payload = {"routing": routing_with_chain(20, spelling, candidate)}
    before = copy.deepcopy(payload)
    dumped = dump_stored_org_config(payload)["routing"]
    if candidate:
        dumped = dumped["rollout"]["routing"]
    assert dumped["model_fallbacks"][MODEL] == [
        f"example/backup-{i}" for i in range(20)
    ]
    assert payload == before


def test_legacy_dump_still_checks_tail_element_types_and_other_contract_fields():
    routing = routing_with_chain(20)
    routing["model_fallbacks"][MODEL][-1] = {"invalid": "model"}
    with pytest.raises(ValidationError):
        dump_stored_org_config({"routing": routing})
    routing = routing_with_chain(20)
    routing["not_a_routing_field"] = True
    with pytest.raises(ValidationError):
        dump_stored_org_config({"routing": routing})


@pytest.mark.integration
@pytest.mark.api
def test_canonical_patch_drops_stored_shadowed_alias(auth_client, organization):
    routing = routing_with_chain(20, "modelFallbacks")
    routing["model_fallbacks"] = {MODEL: ["example/short"]}
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, routing=routing
    )
    with patch(
        "agentcc.services.gateway_client.GatewayClient._request", return_value={}
    ):
        response = auth_client.post(
            URLS[1],
            {"routing": {"model_fallbacks": {MODEL: ["example/repaired"]}}},
            format="json",
        )
    assert response.status_code == 200, response.json()
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    assert active.routing == {"model_fallbacks": {MODEL: ["example/repaired"]}}
