"""MCP rotation keeps the existing commit and failed-issuance guarantees.

Database-backed targets. Gateway/config seams are egress-free mocks.
"""

from copy import deepcopy
from unittest.mock import patch

import pytest
from django.db import DatabaseError, OperationalError, connection

from accounts.models import Organization
from accounts.models.audit_log import AuditAction, AuditLog
from agentcc.models import AgentccAPIKey, AgentccOrgConfig
from agentcc.services import auth_bridge, org_config_writer
from agentcc.services.key_issuance import Issuance
from agentcc.tests.test_key_rotation_commit_boundary import no_remote, request_fixture
from agentcc.tests.test_key_rotation_mcp_grants import (
    CONFIG_PUSH, active_config, mcp_for, store_config, virtual_servers_enabled,
)
from agentcc.tests.test_key_rotation_ownership import KEYS, events, isolated_gateway

pytestmark = pytest.mark.django_db(transaction=True)


def rotate(client, payload):
    return client.post(KEYS, {**payload, "revoke_rotated_key": True}, format="json")


def assert_original(organization, old, prior, before):
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE
    assert AgentccAPIKey.unscoped_objects.count() == before
    assert not events(organization).exists()
    assert active_config(organization).pk == prior.pk
    assert AgentccOrgConfig.no_workspace_objects.filter(organization=organization).count() == 1


@pytest.mark.parametrize("failure", ["commit", "revoke-audit", "config-validation"])
def test_rotation_failure_rolls_back_mcp_and_never_pushes(
    organization, workspace, isolated_gateway, failure
):
    _, old, client, payload = request_fixture(organization, workspace)
    prior = store_config(organization, mcp_for(old.gateway_key_id))
    before = AgentccAPIKey.unscoped_objects.count()
    save = AuditLog.save

    def fail_audit(row, *args, **kwargs):
        if row.action == AuditAction.GATEWAY_KEY_REVOKED:
            raise DatabaseError("fixture audit failure")
        return save(row, *args, **kwargs)

    if failure == "commit":
        refusal = patch.object(connection, "commit", side_effect=OperationalError("fixture commit"))
    elif failure == "revoke-audit":
        refusal = patch.object(AuditLog, "save", new=fail_audit)
    else:
        refusal = patch.object(
            org_config_writer.config_push, "validate_org_config",
            side_effect=org_config_writer.config_push.OrgConfigRejected("fixture config"),
        )
    with patch(CONFIG_PUSH, return_value=True) as push, refusal:
        response = rotate(client, payload)
    assert response.status_code >= 400
    assert "key" not in (response.data.get("result") or {})
    assert_original(organization, old, prior, before)
    push.assert_not_called()
    no_remote(isolated_gateway)


def test_config_ack_follows_commit_and_precedes_key_import(
    organization, workspace, isolated_gateway
):
    _, old, client, payload = request_fixture(organization, workspace)
    prior = store_config(organization, mcp_for(old.gateway_key_id))
    order = []
    commit = connection.commit

    def committed():
        result = commit()
        order.append("commit")
        return result

    def push(org_id, config):
        assert "commit" in order
        assert org_id == str(organization.pk)
        assert config.pk != prior.pk
        assert active_config(organization).pk == config.pk
        new = AgentccAPIKey.unscoped_objects.exclude(pk=old.pk).get(organization=organization)
        assert new.status == AgentccAPIKey.PENDING
        old.refresh_from_db()
        assert old.status == AgentccAPIKey.REVOKED
        assert events(organization).count() == 3
        assert config.mcp["virtual_servers"]["support"]["access"]["keys"] == [new.gateway_key_id]
        isolated_gateway.import_committed_key.assert_not_called()
        isolated_gateway.revoke_key_confirmed.assert_not_called()
        order.append("config")
        return True

    def imported(body):
        assert "config" in order
        assert connection.get_autocommit() and not connection.in_atomic_block
        order.append("import")
        return True

    def revoked(identity):
        assert identity == old.gateway_key_id
        assert "import" in order
        order.append("revoke")
        return True

    isolated_gateway.import_committed_key.side_effect = imported
    isolated_gateway.revoke_key_confirmed.side_effect = revoked
    with patch.object(connection, "commit", side_effect=committed), patch(CONFIG_PUSH, side_effect=push):
        response = rotate(client, payload)
    assert response.status_code == 201
    assert response.data["result"]["key"]
    assert [entry for entry in order if entry != "commit"] == ["config", "import", "revoke"]


@pytest.mark.parametrize("outcome", [False, None, "truthy-not-an-ack", "exception"])
def test_unconfirmed_config_keeps_non_active_identity_and_durable_grants(
    organization, workspace, isolated_gateway, outcome
):
    _, old, client, payload = request_fixture(organization, workspace)
    prior = store_config(organization, mcp_for(old.gateway_key_id))
    options = (
        {"side_effect": RuntimeError("fixture unavailable")}
        if outcome == "exception" else {"return_value": outcome}
    )
    with patch(CONFIG_PUSH, **options) as push:
        response = rotate(client, payload)
    assert response.status_code == 503
    assert response.data == {"code": "KEY_PROVISIONING_FAILED"}
    push.assert_called_once()
    isolated_gateway.import_committed_key.assert_not_called()
    isolated_gateway.revoke_key_confirmed.assert_called_once_with(old.gateway_key_id)
    new = AgentccAPIKey.unscoped_objects.exclude(pk=old.pk).get(organization=organization)
    assert new.status == AgentccAPIKey.FAILED
    assert not auth_bridge.gateway_loadable_keys(organization).filter(pk=new.pk).exists()
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.REVOKED
    current = active_config(organization)
    assert current.pk != prior.pk
    access = current.mcp["virtual_servers"]["support"]["access"]
    assert access["keys"] == [new.gateway_key_id]
    assert access["rules"][0]["match"]["key_ids"] == [new.gateway_key_id, "fixture-peer-key"]
    assert events(organization).count() == 3


def test_org_config_lock_precedes_source_key_lock(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    store_config(organization, mcp_for(old.gateway_key_id))
    locks = []

    def observe(execute, sql, params, many, context):
        upper = sql.upper()
        if "FOR UPDATE" in upper or "FOR NO KEY UPDATE" in upper:
            for model in (Organization, AgentccAPIKey):
                if f'"{model._meta.db_table}"' in sql:
                    locks.append(model)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(observe), patch(CONFIG_PUSH, return_value=True):
        response = rotate(client, payload)
    assert response.status_code == 201
    assert locks[0] is Organization
    assert AgentccAPIKey in locks


def test_marker_without_retirement_does_not_move_access(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    original = mcp_for(old.gateway_key_id)
    prior = store_config(organization, original)
    with patch(CONFIG_PUSH, return_value=True) as push:
        response = client.post(KEYS, payload, format="json")
    assert response.status_code == 201
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE
    assert active_config(organization).pk == prior.pk
    assert active_config(organization).mcp == original
    push.assert_not_called()


def test_deferred_push_uses_latest_committed_config_not_stale_rotation_copy(
    organization, workspace, isolated_gateway
):
    actor, old, client, payload = request_fixture(organization, workspace)
    store_config(organization, mcp_for(old.gateway_key_id))
    after_commit = Issuance.after_commit
    latest = []
    pushed = []

    def intervening_write(issuance):
        assert connection.get_autocommit() and not connection.in_atomic_block
        config, _ = org_config_writer.write_config_version(
            organization, {"cache": {"default_ttl": 90}}, user=actor,
            push=lambda _: True,
        )
        latest.append(config)
        return after_commit(issuance)

    def push(org_id, config):
        pushed.append((config.pk, deepcopy(config.cache), deepcopy(config.mcp)))
        return True

    with patch.object(Issuance, "after_commit", new=intervening_write), patch(CONFIG_PUSH, side_effect=push):
        response = rotate(client, payload)
    assert response.status_code == 201
    new = AgentccAPIKey.unscoped_objects.get(pk=response.data["result"]["id"])
    assert active_config(organization).pk == latest[0].pk
    assert pushed == [(latest[0].pk, latest[0].cache, latest[0].mcp)]
    assert pushed[0][1]["default_ttl"] == 90
    assert pushed[0][2]["virtual_servers"]["support"]["access"]["keys"] == [new.gateway_key_id]


def test_refused_rotation_never_writes_or_pushes_config(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    prior = store_config(organization, mcp_for(old.gateway_key_id))
    old.status = AgentccAPIKey.REVOKED
    old.save(update_fields=["status"])
    with patch(CONFIG_PUSH, return_value=True) as push:
        response = rotate(client, payload)
    assert response.status_code == 403
    assert active_config(organization).pk == prior.pk
    assert not events(organization).exists()
    no_remote(isolated_gateway)
    push.assert_not_called()


def test_rotation_without_config_does_not_create_one(organization, workspace, isolated_gateway):
    _, _, client, payload = request_fixture(organization, workspace)
    with patch(CONFIG_PUSH, return_value=True) as push:
        response = rotate(client, payload)
    assert response.status_code == 201
    assert not AgentccOrgConfig.no_workspace_objects.filter(organization=organization).exists()
    push.assert_not_called()
