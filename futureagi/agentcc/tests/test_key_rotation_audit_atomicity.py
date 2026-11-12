"""Required actor audit persistence, with fake gateway calls only."""

from unittest.mock import patch

import pytest
from django.db import DatabaseError, connection
from django.db.models.query import QuerySet

from accounts.models.audit_log import AuditAction, AuditLog
from accounts.tests.personal_keys import session_client_for
from agentcc.models import AgentccAPIKey
from agentcc.services.key_audit import KeyAuditUnavailable, record_key_event
from agentcc.tests.test_agentcc_permissions import _user_with_level
from agentcc.tests.test_key_rotation_ownership import (
    KEYS,
    events,
    isolated_gateway,
    key_for,
)
from tfc.constants.levels import Level
from tfc.utils import audit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("action", [AuditAction.GATEWAY_KEY_CREATED, AuditAction.GATEWAY_KEY_ROTATED])
@pytest.mark.parametrize("failure", ["raise", "none"])
def test_required_audit_failure_rolls_back_replacement_and_compensates(
    organization, workspace, isolated_gateway, action, failure
):
    actor = _user_with_level(organization, Level.ADMIN)
    old = key_for(actor, organization)
    client = session_client_for(actor, organization, workspace)
    original_create = QuerySet.create
    failed_actions = []
    observations = []
    def fail_required_row(queryset, **kwargs):
        if queryset.model is AuditLog and kwargs.get("action") == action:
            observations.append((
                connection.in_atomic_block,
                queryset.db,
                events(organization).filter(action=AuditAction.GATEWAY_KEY_CREATED).exists(),
            ))
            failed_actions.append(action)
            if failure == "none":
                return None
            raise DatabaseError("Injected audit insert failure")
        return original_create(queryset, **kwargs)

    before = AgentccAPIKey.unscoped_objects.count()
    with patch.object(QuerySet, "create", new=fail_required_row):
        response = client.post(
            KEYS, {"name": "replacement", "rotates_key_id": str(old.pk)}, format="json"
        )
    assert response.status_code == 503
    assert "key" not in (response.data.get("result") or {})
    assert failed_actions == [action]
    assert observations == [(True, connection.alias, action == AuditAction.GATEWAY_KEY_ROTATED)]
    isolated_gateway.import_committed_key.assert_not_called()
    isolated_gateway.revoke_key_confirmed.assert_not_called()
    assert AgentccAPIKey.unscoped_objects.count() == before
    assert not events(organization).exists()
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE


def test_plain_create_also_refuses_audit_insert_failure(
    organization, workspace, isolated_gateway
):
    actor = _user_with_level(organization, Level.ADMIN)
    client = session_client_for(actor, organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    with patch.object(AuditLog, "save", side_effect=DatabaseError("Injected audit failure")):
        response = client.post(KEYS, {"name": "personal key"}, format="json")
    assert response.status_code == 503
    isolated_gateway.import_committed_key.assert_not_called()
    isolated_gateway.revoke_key_confirmed.assert_not_called()
    assert AgentccAPIKey.unscoped_objects.count() == before
    assert not events(organization).exists()


@pytest.mark.parametrize("shared_logger_failure", ["none", "raise"])
def test_muted_or_failed_best_effort_logger_cannot_drop_required_rows(
    organization, workspace, isolated_gateway, shared_logger_failure
):
    actor = _user_with_level(organization, Level.ADMIN)
    old = key_for(actor, organization)
    client = session_client_for(actor, organization, workspace)
    with (
        patch.object(audit, "is_audit_muted", return_value=True),
        patch.object(audit, "log_audit", return_value=None) as optional_logger,
    ):
        if shared_logger_failure == "raise":
            optional_logger.side_effect = AssertionError("Optional logger must not be used")
        response = client.post(
            KEYS, {"name": "replacement", "rotates_key_id": str(old.pk)}, format="json"
        )
    assert response.status_code == 201
    optional_logger.assert_not_called()
    created = events(organization).get(action=AuditAction.GATEWAY_KEY_CREATED)
    rotated = events(organization).get(action=AuditAction.GATEWAY_KEY_ROTATED)
    assert created.actor_id == rotated.actor_id == actor.pk
    assert created._state.db == rotated._state.db == connection.alias
    assert rotated.changes["old_key_id"] == str(old.pk)
    isolated_gateway.revoke_key_confirmed.assert_not_called()


def test_audit_helper_refuses_outside_key_transaction(organization):
    actor = _user_with_level(organization, Level.ADMIN)
    old = key_for(actor, organization)
    assert not connection.in_atomic_block
    with pytest.raises(KeyAuditUnavailable):
        record_key_event(old, actor, AuditAction.GATEWAY_KEY_CREATED)
    assert not events(organization).exists()


def test_revoke_audit_failure_refuses_unrecorded_local_change_and_gateway_push(
    organization, workspace, isolated_gateway
):
    actor = _user_with_level(organization, Level.ADMIN)
    member = _user_with_level(organization, Level.MEMBER)
    old = key_for(member, organization)
    client = session_client_for(actor, organization, workspace)
    with patch.object(AuditLog, "save", side_effect=DatabaseError("Injected audit failure")):
        response = client.post(f"{KEYS}{old.pk}/revoke/", format="json")
    assert response.status_code == 503
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE
    assert not events(organization).exists()
    isolated_gateway.create_key.assert_not_called()
    isolated_gateway.revoke_key_confirmed.assert_not_called()


def test_revoke_push_observes_committed_status_and_actor_audit(
    organization, workspace, isolated_gateway
):
    actor = _user_with_level(organization, Level.ADMIN)
    member = _user_with_level(organization, Level.MEMBER)
    old = key_for(member, organization)
    client = session_client_for(actor, organization, workspace)

    def observe(gateway_id):
        assert gateway_id == old.gateway_key_id
        assert not connection.in_atomic_block
        old.refresh_from_db()
        assert old.status == AgentccAPIKey.REVOKED
        event = events(organization).get(action=AuditAction.GATEWAY_KEY_REVOKED)
        assert event.actor_id == actor.pk
        assert event.target_id == old.pk
        assert event._state.db == old._state.db
        return False

    isolated_gateway.revoke_key_confirmed.side_effect = observe
    response = client.post(f"{KEYS}{old.pk}/revoke/", format="json")
    assert response.status_code == 200
    isolated_gateway.revoke_key_confirmed.assert_called_once_with(old.gateway_key_id)
    assert "warning" in response.data["result"]


def test_failed_revoke_push_keeps_committed_audit_and_local_revocation(
    organization, workspace, isolated_gateway
):
    actor = _user_with_level(organization, Level.ADMIN)
    old = key_for(actor, organization)
    client = session_client_for(actor, organization, workspace)
    isolated_gateway.revoke_key_confirmed.side_effect = RuntimeError("Injected gateway push failure")
    response = client.post(f"{KEYS}{old.pk}/revoke/", format="json")
    assert response.status_code == 200
    assert "warning" in response.data["result"]
    assert "Injected gateway push failure" not in str(response.data)
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.REVOKED
    assert events(organization).get(action=AuditAction.GATEWAY_KEY_REVOKED).actor_id == actor.pk
