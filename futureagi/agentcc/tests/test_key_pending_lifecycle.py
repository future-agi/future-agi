"""Pending lifecycle and robust callback regressions, authored for real commits."""

from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.urls import resolve

from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge
from agentcc.tests.test_key_rotation_atomic_command import command
from agentcc.tests.test_key_rotation_commit_boundary import request_fixture
from agentcc.tests.test_key_rotation_ownership import KEYS, events, isolated_gateway

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("state", [AgentccAPIKey.PENDING, AgentccAPIKey.FAILED])
def test_sync_never_activates_failed_or_pending_key(organization, workspace, isolated_gateway, state):
    _, old, _, _ = request_fixture(organization, workspace)
    old.status = state
    old.save(update_fields=["status"])
    isolated_gateway.list_keys.return_value = {"data": [{
        "id": old.gateway_key_id, "status": "active", "key_prefix": old.key_prefix,
        "metadata": {"org_id": str(organization.pk), "user_id": str(old.user_id)},
    }]}
    auth_bridge.sync_keys(org=organization)
    old.refresh_from_db()
    assert old.status == state
    assert not auth_bridge.gateway_loadable_keys(organization).filter(pk=old.pk).exists()
    if state == AgentccAPIKey.FAILED:
        isolated_gateway.revoke_key.assert_called_once_with(old.gateway_key_id)
    else:
        isolated_gateway.revoke_key.assert_not_called()
    isolated_gateway.import_keys.assert_not_called()


def test_failed_revoke_callback_does_not_skip_a_later_callback(organization, workspace, isolated_gateway):
    _, _, client, payload = request_fixture(organization, workspace)
    namespace = resolve(KEYS).func.cls.create.__globals__
    revoke = namespace["revoke_key_with_audit"]
    reached = []
    isolated_gateway.revoke_key_confirmed.side_effect = RuntimeError("fixture revoke failure")

    def revoke_and_register(*args, **kwargs):
        result = revoke(*args, **kwargs)
        transaction.on_commit(lambda: reached.append(True), robust=True)
        return result

    with patch.dict(namespace, {"revoke_key_with_audit": revoke_and_register}):
        response = client.post(KEYS, command(payload), format="json")
    assert response.status_code == 201
    assert reached == [True]
    assert response.data["result"]["rotation"]["gateway_revocation_confirmed"] is False


def test_lost_response_does_not_remove_committed_identity_or_audits(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    namespace = resolve(KEYS).func.cls.create.__globals__
    serializer = namespace["AgentccAPIKeySerializer"]

    def fail_response(*args, **kwargs):
        assert not connection.in_atomic_block and connection.get_autocommit()
        raise RuntimeError("fixture response failure")

    with patch.dict(namespace, {"AgentccAPIKeySerializer": fail_response}):
        response = client.post(KEYS, command(payload), format="json")
    assert response.status_code == 500
    assert events(organization).count() == 3
    new = AgentccAPIKey.unscoped_objects.exclude(pk=old.pk).get(organization=organization)
    assert new.status == AgentccAPIKey.ACTIVE
    assert "key" not in (response.data.get("result") or {})
    isolated_gateway.import_committed_key.assert_called_once()
    isolated_gateway.revoke_key.assert_not_called()
