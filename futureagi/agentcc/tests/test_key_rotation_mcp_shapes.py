"""Exact key-scoped migration, including the gateway contract's aliases."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from django.db import transaction

from accounts.tests.personal_keys import session_client_for
from agentcc.models import AgentccAPIKey
from agentcc.services.key_transactions import KeyTransactionUnavailable
from agentcc.tests.test_agentcc_permissions import _user_with_level
from agentcc.tests.test_key_rotation_mcp_grants import (
    CONFIG_PUSH, active_config, mcp_for, store_config, virtual_servers_enabled,
)
from agentcc.tests.test_key_rotation_ownership import KEYS, isolated_gateway, key_for
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("server_field", ["virtual_servers", "virtualServers"])
@pytest.mark.parametrize("key_field", ["key_ids", "keyIds"])
def test_member_rotation_preserves_every_rule_and_only_replaces_exact_key_ids(
    organization, workspace, isolated_gateway, server_field, key_field
):
    actor = _user_with_level(organization, Level.MEMBER)
    old = key_for(actor, organization)
    client = session_client_for(actor, organization, workspace)
    original = mcp_for(old.gateway_key_id)
    servers = original.pop("virtual_servers")
    original[server_field] = servers
    # A key-looking string in a non-key field is not authority to rewrite it.
    access = servers["support"]["access"]
    access["users"] = [old.gateway_key_id]
    access["agents"] = [old.gateway_key_id]
    access["keys"] = [old.gateway_key_id, "fixture-peer-key", old.gateway_key_id]
    match = access["rules"][0]["match"]
    match[key_field] = match.pop("key_ids")
    match["user_ids"] = [old.gateway_key_id]
    match["agent_ids"] = [old.gateway_key_id]
    access["rules"].append({
        "match": {key_field: [old.gateway_key_id]},
        "allow": ["docs/read_one"], "deny": ["docs/delete_one"],
    })
    servers["second"] = {
        "id": "vs_01M4ZEB9E1A6Y8ATM7S5TJZ9YX", "members": ["docs"],
        "access": {"keys": [old.gateway_key_id]},
    }
    prior = store_config(organization, original)
    with patch(CONFIG_PUSH, return_value=True):
        response = client.post(KEYS, {
            "name": "replacement", "rotates_key_id": str(old.pk),
            "revoke_rotated_key": True,
        }, format="json")
    assert response.status_code == 201
    new = AgentccAPIKey.unscoped_objects.get(pk=response.data["result"]["id"])
    expected = deepcopy(original)
    expected_servers = expected[server_field]
    expected_access = expected_servers["support"]["access"]
    expected_access["keys"] = [new.gateway_key_id, "fixture-peer-key", new.gateway_key_id]
    expected_access["rules"][0]["match"][key_field] = [new.gateway_key_id, "fixture-peer-key"]
    expected_access["rules"][1]["match"][key_field] = [new.gateway_key_id]
    expected_servers["second"]["access"]["keys"] = [new.gateway_key_id]
    assert active_config(organization).mcp == expected
    prior.refresh_from_db()
    assert prior.mcp == original and not prior.is_active


@pytest.mark.parametrize("mcp", [None, {}, {"virtual_servers": None}, {
    "virtual_servers": {"public": {"access": {"keys": None, "rules": None}}},
}])
def test_empty_optional_sections_are_not_invented_or_mutated(mcp):
    from agentcc.services.key_rotation_mcp import replace_mcp_key

    original = deepcopy(mcp)
    assert replace_mcp_key(mcp, "old", "new") == original
    assert mcp == original


def test_migration_lock_refuses_a_separate_database_transaction(organization):
    from agentcc.services.key_rotation_mcp import lock_rotation_config

    with transaction.atomic(), patch("agentcc.services.org_config_writer.lock_org") as lock:
        with pytest.raises(KeyTransactionUnavailable):
            lock_rotation_config(organization, using="another-database")
    lock.assert_not_called()


def test_migration_lock_requires_an_owned_transaction(organization):
    from agentcc.services.key_rotation_mcp import lock_rotation_config

    with patch("agentcc.services.org_config_writer.lock_org") as lock:
        with pytest.raises(KeyTransactionUnavailable):
            lock_rotation_config(organization, using="default")
    lock.assert_not_called()
