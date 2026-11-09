"""validate_mcp's rules hold on every config write path (W2b integration, M2 IR-4).

update-config and the single-section MCP actions never ran validate_mcp, and
POST org-configs/ ran it before the org lock. The writer now reruns the rules
under the lock against the version it replaces, so no path can set or change a
virtual server id, add a stdio or credential-bearing server, change a stored
server to one, or forward a refused header (M2 review P2 and P3).
"""

import re
from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig

UPDATE_CONFIG = "/agentcc/gateways/default/update-config/"
ORG_CONFIGS = "/agentcc/org-configs/"
SAVE_PUSH = "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
VS_ID = re.compile(r"^vs_[0-9A-HJKMNP-TV-Z]{26}$")
CLIENT_ID = "vs_01M4ZEB9E1A6Y8ATM7S5TJZ9YY"


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _store(organization, mcp):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, mcp=mcp
    )


@pytest.mark.integration
@pytest.mark.api
@patch(SAVE_PUSH, return_value=True)
@patch("agentcc.views.gateway.push_org_config", return_value=True)
class TestMCPRulesOnEveryWrite:
    def test_update_config_assigns_new_ids_and_refuses_client_ids(
        self, _gw_push, _push, auth_client, organization
    ):
        _store(organization, {"virtual_servers": {"eng": {"id": CLIENT_ID}}})

        ok = auth_client.post(
            UPDATE_CONFIG,
            {"mcp": {"virtual_servers": {"ops": {"members": ["pager"]}}}},
            format="json",
        )
        assert ok.status_code == status.HTTP_200_OK, ok.json()
        stored = _active(organization).mcp["virtual_servers"]
        assert stored["eng"]["id"] == CLIENT_ID
        assert VS_ID.match(stored["ops"]["id"]) and stored["ops"]["id"] != CLIENT_ID

        for virtual_servers in (
            {"new": {"id": "vs_01M4ZEB9E0J3AADR85Z4T4428X"}},
            {"eng": {"id": "vs_01M4ZEB9E0J3AADR85Z4T4428X"}},
        ):
            refused = auth_client.post(
                UPDATE_CONFIG,
                {"mcp": {"virtual_servers": virtual_servers}},
                format="json",
            )
            assert refused.status_code == status.HTTP_400_BAD_REQUEST, virtual_servers
            assert refused.json()["code"] == "vs_id_immutable"
        assert _active(organization).mcp["virtual_servers"] == stored

    def test_update_config_refuses_refused_header_and_stdio_server(
        self, _gw_push, _push, auth_client, organization
    ):
        _store(organization, {"servers": {"gh": {"url": "https://g/mcp"}}})
        for servers in (
            {"gh": {"forward_headers": ["Authorization"]}},
            {"new": {"url": "/bin/sh", "transport": "stdio"}},
            {"new": {"url": "https://u:pw@x/mcp"}},
            {"gh": {"transport": "stdio"}},
            {"gh": {"url": "file:///etc/passwd"}},
        ):
            response = auth_client.post(
                UPDATE_CONFIG, {"mcp": {"servers": servers}}, format="json"
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST, servers
        assert _active(organization).mcp == {
            "servers": {"gh": {"url": "https://g/mcp"}}
        }

    def test_update_mcp_server_cannot_add_or_switch_to_stdio(
        self, _gw_push, _push, auth_client, organization
    ):
        _store(organization, {"servers": {"gh": {"url": "https://g/mcp"}}})
        for server_id, config in (
            ("new", {"transport": "stdio", "url": "/bin/sh"}),
            ("gh", {"transport": "stdio", "url": "/bin/sh"}),
        ):
            response = auth_client.post(
                "/agentcc/gateways/default/update-mcp-server/",
                {"server_id": server_id, "config": config},
                format="json",
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST, server_id
        assert _active(organization).mcp == {
            "servers": {"gh": {"url": "https://g/mcp"}}
        }

    def test_post_org_configs_cannot_switch_a_stored_server_to_stdio(
        self, _gw_push, _push, auth_client, organization
    ):
        first = auth_client.post(
            ORG_CONFIGS,
            {"mcp": {"servers": {"gh": {"url": "https://g/mcp", "transport": "http"}}}},
            format="json",
        )
        assert first.status_code == status.HTTP_200_OK, first.json()

        for entry in (
            {"url": "/bin/sh", "transport": "stdio"},
            {"url": "file:///etc/passwd", "transport": "http"},
            {"url": "https://x\x7f/mcp", "transport": "http"},
        ):
            response = auth_client.post(
                ORG_CONFIGS, {"mcp": {"servers": {"gh": entry}}}, format="json"
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST, entry
        assert _active(organization).mcp["servers"]["gh"]["transport"] == "http"

    def test_unchanged_legacy_server_still_saves(
        self, _gw_push, _push, auth_client, organization
    ):
        legacy = {"servers": {"old": {"url": "mcp.internal", "transport": "stdio"}}}
        _store(organization, legacy)

        response = auth_client.post(
            UPDATE_CONFIG,
            {"mcp": {"servers": {"old": {"tools_cache_ttl": "10m"}}}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).mcp["servers"]["old"]["transport"] == "stdio"

    def test_post_resending_the_stored_id_keeps_it(
        self, _gw_push, _push, auth_client, organization
    ):
        # M2 review P3: the org resolves on POST, so a resent stored id is kept.
        first = auth_client.post(
            ORG_CONFIGS,
            {"mcp": {"virtual_servers": {"eng": {"members": ["s"]}}}},
            format="json",
        )
        assert first.status_code == status.HTTP_200_OK, first.json()
        assigned = _active(organization).mcp["virtual_servers"]["eng"]["id"]

        again = auth_client.post(
            ORG_CONFIGS,
            {"mcp": {"virtual_servers": {"eng": {"id": assigned, "members": ["t"]}}}},
            format="json",
        )

        assert again.status_code == status.HTTP_200_OK, again.json()
        assert _active(organization).mcp["virtual_servers"]["eng"]["id"] == assigned
