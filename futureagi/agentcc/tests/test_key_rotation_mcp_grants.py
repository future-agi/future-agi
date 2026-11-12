"""J14.W12-F02: own-key rotation must retain its configured MCP access.

Database-backed tests. All
remote key/config operations use the existing isolated gateway fixture or a
mock config-push seam. No credential or MCP endpoint is contacted.
"""

from copy import deepcopy
from unittest.mock import patch

import pytest

from accounts.models import Organization
from agentcc.models import AgentccAPIKey, AgentccOrgConfig
from agentcc.tests.test_key_rotation_commit_boundary import request_fixture
from agentcc.tests.test_key_rotation_ownership import KEYS
from agentcc.tests.test_key_rotation_ownership import isolated_gateway as _isolated_gateway

pytestmark = pytest.mark.django_db(transaction=True)
isolated_gateway = _isolated_gateway
CONFIG_PUSH = "agentcc.services.config_push.push_org_config"


@pytest.fixture(autouse=True)
def virtual_servers_enabled(settings):
    settings.AGENTCC_MCP_VIRTUAL_SERVERS_ENABLED = True


def mcp_for(key_id, *, grant=True, rule=True):
    access = {
        "keys": [key_id] if grant else [],
        "agents": [],
        "users": [],
        "default_for_org": False,
        "rules": [],
    }
    if rule:
        access["rules"] = [
            {
                "match": {"key_ids": [key_id, "fixture-peer-key"]},
                "allow": ["docs/read_*"],
                "deny": ["docs/delete_*"],
            },
        ]
    return {
        "servers": {"docs": {"url": "https://docs.example.com/mcp", "transport": "http"}},
        "virtual_servers": {
            "support": {
                "id": "vs_01M4ZEB9E1A6Y8ATM7S5TJZ9YY",
                "members": ["docs"],
                "access": access,
            },
            "unrelated": {
                "id": "vs_01M4ZEB9E1A6Y8ATM7S5TJZ9YZ",
                "members": ["docs"],
                "access": {"keys": ["fixture-peer-key"]},
            },
        },
    }


def store_config(organization, mcp):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        mcp=deepcopy(mcp),
        cache={"enabled": True, "default_ttl": 60},
    )


def active_config(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


@pytest.mark.parametrize("grant,rule", [(True, False), (False, True), (True, True)])
def test_successful_rotation_versions_and_pushes_mcp_references(
    organization, workspace, isolated_gateway, grant, rule
):
    _, old, client, payload = request_fixture(organization, workspace)
    original = mcp_for(old.gateway_key_id, grant=grant, rule=rule)
    prior = store_config(organization, original)
    # Matching strings in another organization's config must remain untouched.
    other_org = Organization.objects.create(name="MCP rotation other fixture")
    other = store_config(other_org, original)
    pushed = []

    def push(org_id, config):
        pushed.append((org_id, config.pk, deepcopy(config.mcp)))
        return True

    with patch(CONFIG_PUSH, side_effect=push):
        response = client.post(
            KEYS, {**payload, "revoke_rotated_key": True}, format="json"
        )

    assert response.status_code == 201
    result = response.data["result"]
    new = AgentccAPIKey.unscoped_objects.get(pk=result["id"])
    assert result["key"] and new.status == AgentccAPIKey.ACTIVE
    assert new.gateway_key_id != old.gateway_key_id
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.REVOKED

    expected = deepcopy(original)
    access = expected["virtual_servers"]["support"]["access"]
    if grant:
        access["keys"] = [new.gateway_key_id]
    if rule:
        access["rules"][0]["match"]["key_ids"] = [
            new.gateway_key_id, "fixture-peer-key"
        ]
    current = active_config(organization)
    # Exact equality also pins empty agent/user lists, narrowing allow/deny
    # patterns, the other key's match, and unrelated server configuration.
    assert current.mcp == expected
    assert current.pk != prior.pk and current.version == prior.version + 1
    assert current.cache == prior.cache
    prior.refresh_from_db()
    other.refresh_from_db()
    assert not prior.is_active and prior.mcp == original
    assert other.is_active and other.version == 1 and other.mcp == original
    # A successful reveal must follow the config push, not only a DB rewrite.
    assert pushed == [(str(organization.pk), current.pk, expected)]


def test_unconfirmed_mcp_push_never_returns_ready_replacement(
    organization, workspace, isolated_gateway
):
    _, old, client, payload = request_fixture(organization, workspace)
    store_config(organization, mcp_for(old.gateway_key_id))

    with patch(CONFIG_PUSH, return_value=False) as push:
        response = client.post(
            KEYS, {**payload, "revoke_rotated_key": True}, format="json"
        )

    assert response.status_code >= 400
    assert "key" not in (response.data.get("result") or {})
    push.assert_called_once()


def test_rotation_without_mcp_references_does_not_rewrite_config(
    organization, workspace, isolated_gateway
):
    _, old, client, payload = request_fixture(organization, workspace)
    original = mcp_for("fixture-unrelated-key")
    prior = store_config(organization, original)

    with patch(CONFIG_PUSH, return_value=True) as push:
        response = client.post(
            KEYS, {**payload, "revoke_rotated_key": True}, format="json"
        )

    assert response.status_code == 201
    assert response.data["result"]["key"]
    current = active_config(organization)
    assert current.pk == prior.pk and current.mcp == original
    push.assert_not_called()
