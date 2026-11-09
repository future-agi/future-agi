"""Live-source expiry and explicit revocation confirmation contracts."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db import transaction
from django.urls import resolve
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.tests.test_key_rotation_ownership import (
    KEYS,
    REFUSAL,
    events,
    isolated_gateway,
    key_for,
)
from accounts.tests.personal_keys import session_client_for
from agentcc.tests.test_agentcc_permissions import _user_with_level
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("level", [Level.OWNER, Level.ADMIN])
def test_expired_active_source_matches_missing_and_foreign_refusal(
    organization, workspace, isolated_gateway, level
):
    actor = _user_with_level(organization, level)
    peer = _user_with_level(organization, Level.MEMBER)
    expiry = timezone.now() - timedelta(seconds=1)
    expired = key_for(actor, organization, expires_at=expiry)
    foreign = key_for(peer, organization)
    client = session_client_for(actor, organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    bodies = []
    for marker in (str(expired.pk), str(foreign.pk), str(uuid.uuid4())):
        response = client.post(
            KEYS, {"name": "replacement", "rotates_key_id": marker}, format="json"
        )
        assert response.status_code == 403
        assert REFUSAL in str(response.data)
        bodies.append(response.data)
        for value in (marker, str(actor.pk), str(peer.pk), actor.email, peer.email):
            assert value not in str(response.data)
        assert AgentccAPIKey.unscoped_objects.count() == before
    assert bodies[0] == bodies[1] == bodies[2]
    expired.refresh_from_db()
    assert expired.status == AgentccAPIKey.ACTIVE
    assert expired.expires_at == expiry
    assert not expired.deleted
    assert not events(organization).exists()
    isolated_gateway.create_key.assert_not_called()
    isolated_gateway.revoke_key_confirmed.assert_not_called()


@pytest.mark.parametrize("has_expiry", [False, True])
def test_unexpired_or_unbounded_source_can_still_create_replacement(
    organization, workspace, isolated_gateway, has_expiry
):
    actor = _user_with_level(organization, Level.ADMIN)
    expiry = timezone.now() + timedelta(days=1) if has_expiry else None
    old = key_for(actor, organization, expires_at=expiry)
    response = session_client_for(actor, organization, workspace).post(
        KEYS, {"name": "replacement", "rotates_key_id": str(old.pk)}, format="json"
    )
    assert response.status_code == 201
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE
    assert old.expires_at == expiry
    assert events(organization).filter(action="gateway_key.created").count() == 1
    assert events(organization).filter(action="gateway_key.rotated").count() == 1
    isolated_gateway.import_committed_key.assert_called_once()
    isolated_gateway.revoke_key_confirmed.assert_not_called()


@pytest.mark.parametrize("elapsed_seconds", [0, 1])
def test_expiry_at_or_before_post_lock_check_is_refused(
    organization, workspace, isolated_gateway, elapsed_seconds
):
    actor = _user_with_level(organization, Level.ADMIN)
    # The DB predicate sees a future expiry. Advance only the view's post-lock
    # clock to cover a source that expired while waiting to acquire the lock.
    expiry = timezone.now() + timedelta(days=1)
    old = key_for(actor, organization, expires_at=expiry)
    view = resolve(KEYS).func.cls
    clock = SimpleNamespace(
        now=MagicMock(return_value=expiry + timedelta(seconds=elapsed_seconds))
    )
    with patch.dict(view.create.__globals__, {"timezone": clock}):
        response = session_client_for(actor, organization, workspace).post(
            KEYS, {"name": "replacement", "rotates_key_id": str(old.pk)}, format="json"
        )
    assert response.status_code == 403
    assert REFUSAL in str(response.data)
    assert str(old.pk) not in str(response.data)
    clock.now.assert_called_once()
    isolated_gateway.create_key.assert_not_called()
    isolated_gateway.revoke_key_confirmed.assert_not_called()
    assert not events(organization).exists()
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE


@pytest.mark.parametrize("gateway_fails", [False, True])
def test_revoke_separates_local_status_from_gateway_confirmation(
    organization, workspace, isolated_gateway, gateway_fails
):
    actor = _user_with_level(organization, Level.ADMIN)
    old = key_for(actor, organization)
    if gateway_fails:
        isolated_gateway.revoke_key_confirmed.side_effect = RuntimeError("synthetic failure")
    response = session_client_for(actor, organization, workspace).post(
        f"{KEYS}{old.pk}/revoke/", format="json"
    )
    assert response.status_code == 200
    result = response.data["result"]
    assert result["status"] == AgentccAPIKey.REVOKED
    assert result["gateway_revocation_confirmed"] is False
    assert bool(result.get("warning")) is True
    assert "synthetic failure" not in str(response.data)
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.REVOKED
    assert events(organization).filter(action="gateway_key.revoked").count() == 1
    isolated_gateway.revoke_key_confirmed.assert_called_once_with(old.gateway_key_id)


def test_ambient_revoke_cannot_return_a_success_before_commit(
    organization, workspace, isolated_gateway
):
    actor = _user_with_level(organization, Level.ADMIN)
    old = key_for(actor, organization)
    client = session_client_for(actor, organization, workspace)
    with transaction.atomic():
        response = client.post(f"{KEYS}{old.pk}/revoke/", format="json")
        assert response.status_code == 503
        isolated_gateway.revoke_key_confirmed.assert_not_called()
    isolated_gateway.revoke_key_confirmed.assert_not_called()
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE
    assert not events(organization).exists()
