"""validate_tools: the org config tools v1 section.

The section is stored and pushed; validate_tools types it strictly against the
gateway contract, validates the domain policy and the developer-pinned options, and
refuses a save that opts a byok_only vendor into managed use (400
capability_managed_not_allowed). The byok_only cards Django knows must be the ones
the gateway ships.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.contracts import gateway_admin as contract
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.org_config import (
    BYOK_ONLY_TOOL_CARDS,
    CAPABILITY_MANAGED_NOT_ALLOWED,
    AgentccOrgConfigWriteSerializer,
    _go_glob,
)
from agentcc.services.config_push import _build_payload

ORG_CONFIGS = "/agentcc/org-configs/"
SAVE_PUSH = "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
GATEWAY = Path(__file__).resolve().parents[3] / "agentcc-gateway"
SEED_CARDS = GATEWAY / "catalog/tools"
# Cases [pattern, name, matches] that the gateway's TestGlobCasesArePathMatch
# proves are Go's path.Match.
GLOB_CASES = json.loads(
    (GATEWAY / "internal/toolcatalog/testdata/glob_cases.json").read_text()
)["cases"]

TOOLS = {
    "capabilities": {
        "search": {
            "enabled": True,
            "providers": ["exa", "brave", "tavily"],
            "max_cost_usd_per_call": 0.02,
            "defaults": {"num_results": 10, "depth": "standard"},
            "pinned": {"provider": "exa", "safe_search": True},
        },
        "fetch": {"enabled": True, "providers": ["firecrawl"]},
        "documents.parse": {"enabled": False},
    },
    "policy": {
        "domains": {
            "allow": ["*.example.com", "docs.python.org"],
            "deny": ["internal.example.com"],
        },
        "no_training_vendors_only": True,
        "zdr_required": False,
        "regions": ["us", "eu"],
        "retention_override": "metadata",
    },
}


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _errors(data):
    serializer = AgentccOrgConfigWriteSerializer(data=data)
    return serializer.is_valid(), str(serializer.errors)


def _managed(*allow, key="managed_tools"):
    return {"enabled": True, key: {"enabled": True, "allow": list(allow)}}


def _refusals(data):
    """The capability_managed_not_allowed messages a save is refused with."""
    serializer = AgentccOrgConfigWriteSerializer(data=data)
    serializer.is_valid()
    return [
        str(detail)
        for details in serializer.errors.values()
        for detail in details
        if getattr(detail, "code", None) == CAPABILITY_MANAGED_NOT_ALLOWED
    ]


@pytest.mark.integration
@pytest.mark.api
@patch(SAVE_PUSH, return_value=True)
@patch("agentcc.services.config_push._assemble_providers", return_value={})
class TestToolsSaves:
    def test_tools_block_round_trips(
        self, _providers, _push, auth_client, organization
    ):
        response = auth_client.post(ORG_CONFIGS, {"tools": TOOLS}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["tools"] == TOOLS
        active = _active(organization)
        assert active.tools == TOOLS
        assert _build_payload(str(organization.id), active)["tools"] == TOOLS

    def test_byok_only_vendor_managed_400(
        self, _providers, _push, auth_client, organization
    ):
        for glob, key in (
            ("search/brave", "managed_tools"),
            ("*/brave", "managed_tools"),
            ("sandboxes/e2b", "managed_tools"),
            # The contract's camelCase spelling, which config_push pushes as
            # managed_tools.
            ("search/brave", "managedTools"),
            ("payments/stripe", "managedTools"),
        ):
            response = auth_client.post(
                ORG_CONFIGS,
                {"tools": TOOLS, "mcp": _managed(glob, key=key)},
                format="json",
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST, (glob, key)
            body = str(response.json())
            assert CAPABILITY_MANAGED_NOT_ALLOWED in body, (glob, key, body)
            assert glob in body, (glob, key, body)
        assert not AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization
        ).exists()

        # A wildcard vendor, an unconfirmed vendor and a FutureAGI managed
        # server are not a byok_only vendor's managed credential.
        response = auth_client.post(
            ORG_CONFIGS,
            {"tools": TOOLS, "mcp": _managed("search/*", "search/exa", "fagi.web/*")},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).mcp["managed_tools"]["allow"] == [
            "search/*",
            "search/exa",
            "fagi.web/*",
        ]


def test_byok_only_glob_refused_when_the_save_names_only_mcp():
    """The refusal is an mcp rule, so the glob is refused when the tools section is
    left out too, under either spelling of managed_tools."""
    for key in ("managed_tools", "managedTools"):
        ok, errors = _errors({"mcp": _managed("payments/stripe", key=key)})
        assert not ok, key
        assert "payments/stripe" in errors, (key, errors)
        ok, errors = _errors({"mcp": _managed("payments/*", key=key)})
        assert ok, (key, errors)


def test_byok_only_refused_under_every_contract_spelling():
    """The contract reads managedTools as managed_tools, and config_push
    pushes it so; a save that carries both spellings is refused when either
    names a byok_only vendor."""
    spellings = contract.MCPOrgConfig.model_fields["managed_tools"].validation_alias
    assert {"managed_tools", "managedTools"} <= set(spellings.choices)
    pushed = contract.MCPOrgConfig.model_validate(
        _managed("search/brave", key="managedTools")
    ).model_dump(by_alias=True, exclude_none=True)
    assert pushed["managed_tools"]["allow"] == ["search/brave"]

    both = {
        "enabled": True,
        "managed_tools": {"enabled": True, "allow": ["search/exa"]},
        "managedTools": {"enabled": True, "allow": ["search/brave"]},
    }
    assert any("search/brave" in m for m in _refusals({"mcp": both}))


@pytest.mark.parametrize(
    "glob,refused",
    [
        # A \ escape or a [...] class names the vendor it matches, under the
        # gateway's path.Match syntax (Python's fnmatch reads \ literally).
        ("search/br\\ave", True),
        ("search/brav[e]", True),
        ("*/[b]rave", True),
        ("[s]earch/brave", True),
        ("s*/brave", True),
        # A vendor glob with * or ? is a wildcard and names no vendor.
        ("search/brav?", False),
        ("search/b*", False),
        ("search/[x]rave", False),
        ("search/brav[e", False),
        ("fetch/brave", False),
        ("fagi.web/brave", False),
    ],
)
def test_byok_only_glob_spellings(glob, refused):
    refusals = _refusals({"mcp": _managed(glob)})
    assert bool(refusals) == refused, (glob, refusals)
    if refused:
        assert glob in refusals[0], refusals


@pytest.mark.parametrize("pattern,name,matches", GLOB_CASES)
def test_go_glob_matches_path_match(pattern, name, matches):
    regex = _go_glob(pattern)
    assert bool(regex and regex.fullmatch(name)) == matches, (pattern, name)


@pytest.mark.parametrize(
    "domains",
    [
        {"allow": ["https://example.com"]},
        {"allow": ["example.com/docs"]},
        {"deny": ["*"]},
        {"deny": ["*.*.example.com"]},
        {"allow": ["exa mple.com"]},
        {"allow": [""]},
        {"allow": ["-bad.example.com"]},
        {"allow": ["example.com"], "deny": ["EXAMPLE.com"]},
        {"allow": [7]},
    ],
)
def test_domain_policy_validated(domains):
    tools = {"policy": {"domains": domains}}
    ok, errors = _errors({"tools": tools})
    assert not ok, domains
    assert "tools.policy.domains" in errors, errors


def test_domain_policy_accepts_hosts_and_wildcards():
    tools = {
        "policy": {
            "domains": {
                "allow": ["*.example.com", "docs.python.org", "localhost", "10.0.0.1"],
                "deny": ["ads.example.net"],
            }
        }
    }
    ok, errors = _errors({"tools": tools})
    assert ok, errors


@pytest.mark.parametrize(
    "capability",
    [
        {"pinned": {"provider": None}},
        {"pinned": {"Provider": "exa"}},
        {"pinned": {"provider": 3}},
        {"pinned": {"provider": "brave"}, "providers": ["exa"]},
        {"pinned": {"num_results": "10"}, "defaults": {"num_results": 5}},
        {"pinned": {"safe_search": 1}, "defaults": {"safe_search": True}},
        {"defaults": {"depth": None}},
        {"pinned": "exa"},
        {"providers": ["exa", "exa"]},
        {"providers": ["Exa"]},
        {"managed": True},
    ],
)
def test_pinned_options_typed(capability):
    ok, errors = _errors({"tools": {"capabilities": {"search": capability}}})
    assert not ok, capability
    assert "tools.capabilities.search" in errors, errors


def test_pinned_options_accept_consistent_types():
    capability = {
        "providers": ["exa", "brave"],
        "defaults": {"num_results": 5, "safe_search": False, "depth": "standard"},
        "pinned": {"provider": "exa", "num_results": 10, "safe_search": True},
    }
    ok, errors = _errors({"tools": {"capabilities": {"search": capability}}})
    assert ok, errors


@pytest.mark.parametrize(
    "tools",
    [
        [],
        {"capabilities": {"Search": {"enabled": True}}},
        {"capabilities": {"search": {"max_cost_usd_per_call": -1}}},
        {"policy": {"retention_override": "forever"}},
        {"policy": {"zdr": True}},
    ],
)
def test_off_contract_tools_refused(tools):
    ok, errors = _errors({"tools": tools})
    assert not ok, tools
    assert "tools" in errors, errors


def test_byok_only_cards_match_the_gateway_seed_cards():
    """BYOK_ONLY_TOOL_CARDS mirrors the shipped cards whose resale_status is
    byok_only; a card changed on one side only would let a managed opt-in
    through Django that the gateway refuses at load, or the reverse."""
    shipped = set()
    for path in SEED_CARDS.glob("*/*.json"):
        card = json.loads(path.read_text())
        if card.get("resale_status") == "byok_only":
            shipped.add(f"{card['capability']}/{card['vendor']}")

    assert len(shipped) > 40, f"read only {len(shipped)} byok_only cards"
    assert shipped == set(BYOK_ONLY_TOOL_CARDS)
