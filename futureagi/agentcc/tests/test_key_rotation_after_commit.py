"""Post-commit failure states with an egress-free, hash-only fake gateway."""

from unittest.mock import call, patch

import pytest
from django.db import DatabaseError, connection
from django.db.models.query import QuerySet

from accounts.models.audit_log import AuditAction
from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge
from agentcc.tests.test_key_rotation_atomic_command import command
from agentcc.tests.test_key_rotation_commit_boundary import no_remote, request_fixture
from agentcc.tests.test_key_rotation_ownership import KEYS, events
from agentcc.tests.test_key_rotation_ownership import isolated_gateway as _isolated_gateway

pytestmark = pytest.mark.django_db(transaction=True)

# Register the imported pytest fixture on this module.
isolated_gateway = _isolated_gateway


def assert_committed_pair(organization, old, payload):
    assert not connection.in_atomic_block and connection.get_autocommit()
    new = AgentccAPIKey.unscoped_objects.get(gateway_key_id=payload["id"])
    assert new.pk != old.pk and new.status == AgentccAPIKey.PENDING
    assert new.key_hash == payload["key_hash"]
    assert new.user_id == old.user_id and new.organization_id == old.organization_id
    assert events(organization).get(action=AuditAction.GATEWAY_KEY_CREATED).target_id == new.pk
    rotated = events(organization).get(action=AuditAction.GATEWAY_KEY_ROTATED)
    assert rotated.target_id == new.pk
    assert rotated.changes == {"old_key_id": str(old.pk), "new_key_id": str(new.pk)}
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.REVOKED
    assert events(organization).get(action=AuditAction.GATEWAY_KEY_REVOKED).target_id == old.pk
    return new


def test_remote_creation_sees_committed_identity_hash_and_audit_pair(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    order = []

    def imported(body):
        new = assert_committed_pair(organization, old, body)
        assert not auth_bridge.gateway_loadable_keys(organization).filter(pk=new.pk).exists()
        order.append("create")
        return True

    def revoked(key_id):
        assert not connection.in_atomic_block
        assert key_id == old.gateway_key_id
        assert order == ["create"]
        order.append("revoke")
        return False

    isolated_gateway.import_committed_key.side_effect = imported
    isolated_gateway.revoke_key_confirmed.side_effect = revoked
    response = client.post(KEYS, command(payload), format="json")
    assert response.status_code == 201
    assert order == ["create", "revoke"]
    assert response.data["result"]["status"] == "active"
    isolated_gateway.create_key.assert_not_called()


@pytest.mark.parametrize("failure", ["exception", "unconfirmed", "response-lost"])
def test_remote_create_failure_keeps_non_active_row_and_runs_later_revoke(
    organization, workspace, isolated_gateway, failure
):
    _, old, client, payload = request_fixture(organization, workspace)
    remote = []

    def failed_import(body):
        new = assert_committed_pair(organization, old, body)
        if failure == "response-lost":
            remote.append((new.pk, body["key_hash"]))
        if failure != "unconfirmed":
            raise RuntimeError("synthetic gateway failure")
        return False

    isolated_gateway.import_committed_key.side_effect = failed_import
    response = client.post(KEYS, command(payload), format="json")
    assert response.status_code == 503
    assert response.data == {"code": "KEY_PROVISIONING_FAILED"}
    new = AgentccAPIKey.unscoped_objects.exclude(pk=old.pk).get(organization=organization)
    assert new.status == AgentccAPIKey.FAILED
    assert not auth_bridge.gateway_loadable_keys(organization).filter(pk=new.pk).exists()
    assert events(organization).count() == 3
    assert isolated_gateway.revoke_key_confirmed.call_args_list == [
        call(new.gateway_key_id), call(old.gateway_key_id),
    ]
    # Even an unknown transport outcome has its pre-existing committed pair.
    for local_id, key_hash in remote:
        assert AgentccAPIKey.unscoped_objects.filter(pk=local_id, key_hash=key_hash).exists()
        assert events(organization).filter(target_id=local_id, action=AuditAction.GATEWAY_KEY_CREATED).exists()
    isolated_gateway.revoke_key.assert_not_called()


def test_failed_state_write_failure_still_leaves_pending_not_active(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    isolated_gateway.import_committed_key.return_value = False
    update = QuerySet.update

    def fail_state(queryset, **kwargs):
        if queryset.model is AgentccAPIKey and kwargs.get("status") == AgentccAPIKey.FAILED:
            raise DatabaseError("fixture failed-state write")
        return update(queryset, **kwargs)

    with patch.object(QuerySet, "update", new=fail_state):
        response = client.post(KEYS, command(payload), format="json")
    assert response.status_code == 503
    new = AgentccAPIKey.unscoped_objects.exclude(pk=old.pk).get(organization=organization)
    assert new.status == AgentccAPIKey.PENDING
    assert not auth_bridge.gateway_loadable_keys(organization).filter(pk=new.pk).exists()
    assert isolated_gateway.revoke_key_confirmed.call_args_list == [
        call(new.gateway_key_id), call(old.gateway_key_id),
    ]


def test_activation_write_failure_never_returns_an_active_secret(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    update = QuerySet.update

    def fail_active(queryset, **kwargs):
        if queryset.model is AgentccAPIKey and kwargs.get("status") == AgentccAPIKey.ACTIVE:
            raise DatabaseError("fixture activation failure")
        return update(queryset, **kwargs)

    with patch.object(QuerySet, "update", new=fail_active):
        response = client.post(KEYS, command(payload), format="json")
    assert response.status_code == 503
    new = AgentccAPIKey.unscoped_objects.exclude(pk=old.pk).get(organization=organization)
    assert new.status == AgentccAPIKey.FAILED
    assert events(organization).count() == 3
    assert isolated_gateway.revoke_key_confirmed.call_args_list == [
        call(new.gateway_key_id), call(old.gateway_key_id),
    ]


def test_old_revoke_failure_keeps_new_key_and_reports_pending(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    isolated_gateway.revoke_key_confirmed.side_effect = RuntimeError("fixture revoke failure")
    response = client.post(KEYS, command(payload), format="json")
    assert response.status_code == 201
    result = response.data["result"]
    assert result["status"] == AgentccAPIKey.ACTIVE and result["key"]
    assert result["rotation"]["gateway_revocation_confirmed"] is False
    assert result["rotation"]["warning"] == (
        "This key may still work. Revocation has not been confirmed at the gateway."
    )
    assert events(organization).count() == 3


@pytest.mark.parametrize("step", ["membership", "key-insert", "expiry-save", "revoke-save"])
def test_local_failure_points_have_no_remote_effects(organization, workspace, isolated_gateway, step):
    _, old, client, payload = request_fixture(organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    save = AgentccAPIKey.save
    exists = QuerySet.exists

    def failing_save(key, *args, **kwargs):
        if (
            (step == "key-insert" and key._state.adding)
            or (step == "expiry-save" and "expires_at" in kwargs.get("update_fields", []))
            or (step == "revoke-save" and key.pk == old.pk and key.status == AgentccAPIKey.REVOKED)
        ):
            raise DatabaseError("fixture local write")
        return save(key, *args, **kwargs)

    def failing_membership(queryset):
        if step == "membership" and queryset.model.__name__ == "OrganizationMember":
            return False
        return exists(queryset)

    with patch.object(AgentccAPIKey, "save", new=failing_save):
        if step == "membership":
            with patch("agentcc.services.key_issuance.live_gateway_memberships") as membership:
                membership.return_value.using.return_value.filter.return_value.exists.return_value = False
                response = client.post(KEYS, command(payload), format="json")
        else:
            response = client.post(KEYS, command(payload), format="json")
    assert response.status_code == 503
    no_remote(isolated_gateway)
    assert AgentccAPIKey.unscoped_objects.count() == before
    assert not events(organization).exists()
