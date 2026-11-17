"""New chain edits fit the gateway's budget; unchanged legacy chains still save."""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from agentcc.contracts.gateway_admin import RolloutRoutingConfig, RoutingConfig
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.org_config import AgentccOrgConfigWriteSerializer

ROOT = Path(__file__).resolve().parents[3]
MODEL = "example/primary"
URLS = ("/agentcc/org-configs/", "/agentcc/gateways/default/update-config/")


def routing_with_chain(length, spelling="model_fallbacks", candidate=False):
    routing = {spelling: {MODEL: [f"example/backup-{i}" for i in range(length)]}}
    if candidate:
        return {
            "rollout": {
                "version": "candidate",
                "percent": 10,
                "sticky_by": "request",
                "routing": routing,
            }
        }
    return routing


def test_model_fallback_bounds_agree():
    """Contract -> generated Django constraint -> console -> runtime Go bound."""
    schema = json.loads(
        (ROOT / "api_contracts/gateway/agentcc-admin.schema.json").read_text()
    )
    limits = []
    for model in (RoutingConfig, RolloutRoutingConfig):
        limits.append(
            schema["$defs"][model.__name__]["properties"]["model_fallbacks"][
                "additionalProperties"
            ]["maxItems"]
        )
        field = model.model_json_schema()["properties"]["model_fallbacks"]
        dictionary = next(
            branch for branch in field["anyOf"] if branch.get("type") == "object"
        )
        limits.append(dictionary["additionalProperties"]["maxItems"])
    sources = (
        (
            "frontend/src/sections/gateway/fallbacks/fallbackLimits.js",
            r"export const MAX_MODEL_FALLBACKS = (\d+);",
        ),
        (
            "agentcc-gateway/internal/tenant/r7_routing_accessors.go",
            r"const MaxModelFallbacks = (\d+)",
        ),
        (
            "agentcc-gateway/internal/config/config.go",
            r"const MaxModelFallbacks = (\d+)",
        ),
    )
    for path, pattern in sources:
        match = re.search(pattern, (ROOT / path).read_text())
        assert match, path
        limits.append(int(match[1]))
    assert limits == [15] * len(limits)


@pytest.mark.parametrize("model", [RoutingConfig, RolloutRoutingConfig])
@pytest.mark.parametrize("spelling", ["model_fallbacks", "modelFallbacks"])
@pytest.mark.parametrize("length", [0, 14, 15, 16, 17])
def test_generated_contract_bounds(model, spelling, length):
    value = routing_with_chain(length, spelling)
    if length <= 15:
        assert len(model.model_validate(value).model_fallbacks[MODEL]) == length
    else:
        with pytest.raises(ValidationError) as exc:
            model.model_validate(value)
        error = exc.value.errors()[0]
        assert error["loc"][-1] == MODEL
        assert error["ctx"]["max_length"] == 15
        assert error["ctx"]["actual_length"] == length


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("spelling", ["model_fallbacks", "modelFallbacks"])
def test_full_save_serializer_refuses_long_chain(candidate, spelling):
    serializer = AgentccOrgConfigWriteSerializer(
        data={"routing": routing_with_chain(16, spelling, candidate)}
    )
    assert not serializer.is_valid()
    message = str(serializer.errors)
    assert MODEL in message and "16" in message and "15" in message


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("spelling", ["model_fallbacks", "modelFallbacks"])
@pytest.mark.parametrize("length", [15, 16, 17])
def test_save_and_merge_patch_bounds(
    auth_client, organization, url, candidate, spelling, length
):
    original = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        routing={"strategy": "round_robin", spelling: {"other": ["backup"]}},
    )
    with (
        patch(
            "agentcc.views.gateway.push_org_config", return_value=True
        ) as update_push,
        patch(
            "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
            return_value=True,
        ) as save_push,
    ):
        response = auth_client.post(
            url,
            {"routing": routing_with_chain(length, spelling, candidate)},
            format="json",
        )
    versions = AgentccOrgConfig.no_workspace_objects.filter(organization=organization)
    original.refresh_from_db()
    if length > 15:
        assert response.status_code == 400, response.json()
        message = json.dumps(response.json())
        assert MODEL in message and str(length) in message and "15" in message
        assert versions.count() == 1
        assert original.is_active
        assert original.routing[spelling] == {"other": ["backup"]}
        update_push.assert_not_called()
        save_push.assert_not_called()
    else:
        assert response.status_code in (200, 201), response.json()
        assert versions.count() == 2
        active = versions.get(is_active=True)
        routing = active.routing["rollout"]["routing"] if candidate else active.routing
        chains = routing.get(spelling, routing.get("model_fallbacks"))
        assert chains[MODEL] == [f"example/backup-{i}" for i in range(length)]
        if url.endswith("update-config/"):
            assert active.routing["strategy"] == "round_robin"
            assert active.routing[spelling]["other"] == ["backup"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("spelling", ["model_fallbacks", "modelFallbacks"])
@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("section", ["cache", "guardrails", "routing"])
def test_kept_legacy_long_chain_saves(
    auth_client, organization, url, spelling, candidate, section
):
    routing = routing_with_chain(20, spelling, candidate)
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, routing=routing
    )
    value = {
        "routing": routing,
        "cache": {"enabled": False},
        "guardrails": {"fail_open": False},
    }[section]
    with patch(
        "agentcc.services.gateway_client.GatewayClient._request", return_value={}
    ) as transport:
        response = auth_client.post(url, {section: value}, format="json")
    assert response.status_code in (200, 201), response.json()
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    assert active.routing == routing
    pushed = transport.call_args.kwargs["json_body"]["routing"]
    if candidate:
        pushed = pushed["rollout"]["routing"]
    assert pushed["model_fallbacks"][MODEL] == [
        f"example/backup-{i}" for i in range(20)
    ]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("replacement", [None, [], ["example/backup"]])
@pytest.mark.parametrize("spelling", ["model_fallbacks", "modelFallbacks"])
@pytest.mark.parametrize("candidate", [False, True])
def test_merge_patch_can_repair_legacy_long_chain(
    auth_client, organization, replacement, spelling, candidate
):
    routing = routing_with_chain(20, spelling, candidate)
    section = routing["rollout"]["routing"] if candidate else routing
    section[spelling]["untouched"] = ["example/other"] * 20
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, routing=routing
    )
    change = {"model_fallbacks": {MODEL: replacement}}
    if candidate:
        change = {"rollout": {"routing": change}}
    with patch(
        "agentcc.services.gateway_client.GatewayClient._request", return_value={}
    ) as transport:
        response = auth_client.post(URLS[1], {"routing": change}, format="json")
    assert response.status_code == 200, response.json()
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    repaired = active.routing["rollout"]["routing"] if candidate else active.routing
    assert "modelFallbacks" not in repaired
    assert repaired["model_fallbacks"].get(MODEL) == replacement
    assert repaired["model_fallbacks"]["untouched"] == ["example/other"] * 20
    transport.assert_called_once()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("candidate", [False, True])
def test_shadowed_alias_cannot_save_a_long_chain(
    auth_client, organization, url, candidate
):
    routing = routing_with_chain(16, "modelFallbacks", candidate)
    section = routing["rollout"]["routing"] if candidate else routing
    section["model_fallbacks"] = {MODEL: ["example/short"]}
    with (
        patch(
            "agentcc.views.gateway.push_org_config", return_value=True
        ) as update_push,
        patch(
            "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
            return_value=True,
        ) as save_push,
    ):
        response = auth_client.post(url, {"routing": routing}, format="json")
    assert response.status_code == 400, response.json()
    message = json.dumps(response.json())
    assert MODEL in message and "16" in message and "15" in message
    assert not AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization
    ).exists()
    update_push.assert_not_called()
    save_push.assert_not_called()
