"""Combined own-key command with a real durable commit, never TestCase wrapping."""

import uuid
from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import DatabaseError, OperationalError, connection, transaction
from django.urls import resolve
from django.utils import timezone

from accounts.models import Organization
from accounts.models.audit_log import AuditAction, AuditLog
from agentcc.models import AgentccAPIKey
from agentcc.services.key_transactions import KeyTransactionUnavailable, key_creation_transaction
from agentcc.tests.test_agentcc_permissions import _user_with_level
from agentcc.tests.test_key_rotation_commit_boundary import no_remote, request_fixture
from agentcc.tests.test_key_rotation_ownership import KEYS, events, isolated_gateway, key_for
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db(transaction=True)


def command(payload):
    return {**payload, "revoke_rotated_key": True}


def assert_restored(old, organization, before):
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE
    assert AgentccAPIKey.unscoped_objects.count() == before
    assert not events(organization).exists()


@pytest.mark.parametrize("ambient", [False, True])
def test_failure_between_create_and_revoke_persists_neither_step(
    organization, workspace, isolated_gateway, ambient
):
    actor, old, client, payload = request_fixture(organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    namespace = resolve(KEYS).func.cls.create.__globals__
    observations = []

    def fail_before_revoke(source, user, **kwargs):
        assert connection.in_atomic_block
        assert source.pk == old.pk and user.pk == actor.pk
        assert kwargs["locked_source"] is True
        assert AgentccAPIKey.unscoped_objects.count() == before + 1
        assert events(organization).count() == 2
        observations.append(True)
        no_remote(isolated_gateway)
        raise OperationalError("fixture between-step failure")

    with transaction.atomic() if ambient else nullcontext():
        with patch.dict(namespace, {"revoke_key_with_audit": fail_before_revoke}):
            response = client.post(KEYS, command(payload), format="json")
        assert response.status_code == 503
    assert observations == ([] if ambient else [True])
    assert_restored(old, organization, before)
    no_remote(isolated_gateway)


@pytest.mark.parametrize("ambient", [False, True])
def test_explicit_rotation_owns_commit_or_refuses_ambient(organization, workspace, isolated_gateway, ambient):
    actor, old, client, payload = request_fixture(organization, workspace)
    old.expires_at = timezone.now() + timedelta(days=1)
    old.save(update_fields=["expires_at"])
    before = AgentccAPIKey.unscoped_objects.count()
    with transaction.atomic() if ambient else nullcontext():
        response = client.post(KEYS, command(payload), format="json")
        assert response.status_code == (503 if ambient else 201)
    if ambient:
        no_remote(isolated_gateway)
        assert_restored(old, organization, before)
        return
    result = response.data["result"]
    new = AgentccAPIKey.unscoped_objects.get(pk=result["id"])
    assert new.user_id == actor.pk and new.owner == actor.email
    assert new.status == AgentccAPIKey.ACTIVE and new.expires_at == old.expires_at
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.REVOKED
    assert result["rotation"]["status"] == "revoked"
    assert result["rotation"]["expiry_copied"] is True
    assert result["rotation"]["gateway_revocation_confirmed"] is False
    assert "warning" in result["rotation"]
    assert events(organization).count() == 3
    isolated_gateway.revoke_key_confirmed.assert_called_once_with(old.gateway_key_id)


@pytest.mark.parametrize("rollback", ["exception", "silent"])
def test_later_outer_rollback_has_no_replacement_or_old_key_push(organization, workspace, isolated_gateway, rollback):
    _, old, client, payload = request_fixture(organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    try:
        with transaction.atomic():
            response = client.post(KEYS, command(payload), format="json")
            assert response.status_code == 503
            assert "key" not in (response.data.get("result") or {})
            if rollback == "silent":
                transaction.set_rollback(True)
            else:
                raise RuntimeError("fixture rollback")
    except RuntimeError:
        assert rollback == "exception"
    assert_restored(old, organization, before)
    no_remote(isolated_gateway)


@pytest.mark.parametrize("explicit", [False, True])
def test_atomic_requests_callback_is_non_atomic_for_create_and_rotate(
    organization, workspace, isolated_gateway, explicit
):
    _, _, client, payload = request_fixture(organization, workspace)
    using = AgentccAPIKey.no_workspace_objects.select_for_update().db
    callback = resolve(KEYS).func
    assert using in callback._non_atomic_requests
    if explicit:
        payload = command(payload)
    with patch.dict(connection.settings_dict, {"ATOMIC_REQUESTS": True}):
        response = client.post(KEYS, payload, format="json")
    assert response.status_code == 201
    assert not connection.in_atomic_block and connection.get_autocommit()
    isolated_gateway.import_committed_key.assert_called_once()


def test_revoke_audit_failure_rolls_back_the_whole_explicit_rotation(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    save = AuditLog.save

    def fail_revoke(row, *args, **kwargs):
        if row.action == AuditAction.GATEWAY_KEY_REVOKED:
            raise DatabaseError("fixture audit failure")
        return save(row, *args, **kwargs)

    with patch.object(AuditLog, "save", new=fail_revoke):
        response = client.post(KEYS, command(payload), format="json")
    assert response.status_code == 503
    assert_restored(old, organization, before)
    no_remote(isolated_gateway)


@pytest.mark.parametrize("explicit_false", [False, True])
def test_marker_alone_still_never_revokes(organization, workspace, isolated_gateway, explicit_false):
    _, old, client, payload = request_fixture(organization, workspace)
    if explicit_false:
        payload["revoke_rotated_key"] = False
    response = client.post(KEYS, payload, format="json")
    assert response.status_code == 201 and "rotation" not in response.data["result"]
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE
    isolated_gateway.revoke_key_confirmed.assert_not_called()


def test_command_without_marker_is_not_plain_creation(organization, workspace, isolated_gateway):
    _, _, client, _ = request_fixture(organization, workspace)
    response = client.post(KEYS, {"name": "replacement", "revoke_rotated_key": True}, format="json")
    assert response.status_code == 403
    assert "Key rotation is not permitted" in str(response.data)
    no_remote(isolated_gateway)


def test_nested_savepoint_refuses_each_issuance(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    with transaction.atomic():
        assert client.post(KEYS, payload, format="json").status_code == 503
        with transaction.atomic():
            assert client.post(KEYS, command(payload), format="json").status_code == 503
            transaction.set_rollback(True)
    assert_restored(old, organization, before)
    no_remote(isolated_gateway)


def test_prior_caller_callback_cannot_skip_issuance_because_ambient_is_refused(
    organization, workspace, isolated_gateway
):
    _, old, client, payload = request_fixture(organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()

    def fail():
        raise RuntimeError("fixture callback")

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            transaction.on_commit(fail)
            assert client.post(KEYS, command(payload), format="json").status_code == 503
    assert_restored(old, organization, before)
    no_remote(isolated_gateway)


def test_commit_marker_requires_real_outermost_commit(isolated_gateway):
    assert not connection.in_atomic_block and connection.get_autocommit()
    with key_creation_transaction() as issuance:
        assert issuance.committed is False
    assert issuance.committed is True
    with transaction.atomic():
        with pytest.raises(KeyTransactionUnavailable):
            with key_creation_transaction():
                pytest.fail("ambient issuance accepted")
    no_remote(isolated_gateway)


def test_final_commit_failure_rolls_back_both_halves_of_explicit_rotation(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    with patch.object(connection, "commit", side_effect=OperationalError("fixture commit")):
        response = client.post(KEYS, command(payload), format="json")
    assert response.status_code == 503
    assert_restored(old, organization, before)
    no_remote(isolated_gateway)


def test_explicit_rotation_has_the_same_ownership_and_expiry_refusal(organization, workspace, isolated_gateway):
    actor, old, client, payload = request_fixture(organization, workspace)
    peer = _user_with_level(organization, Level.MEMBER)
    peer_key = key_for(peer, organization)
    foreign_org = Organization.objects.create(name="Other rotation fixture")
    foreign = key_for(peer, foreign_org)
    old.expires_at = timezone.now() - timedelta(seconds=1)
    old.save(update_fields=["expires_at"])
    bodies = []
    for source_id in (old.pk, peer_key.pk, foreign.pk, uuid.uuid4()):
        response = client.post(KEYS, command({**payload, "rotates_key_id": str(source_id)}), format="json")
        assert response.status_code == 403
        assert str(source_id) not in str(response.data)
        bodies.append(response.data)
    assert all(body == bodies[0] for body in bodies)
    assert not events(organization).exists()
    for source in [peer_key, foreign]:
        source.refresh_from_db()
        assert source.status == AgentccAPIKey.ACTIVE
    no_remote(isolated_gateway)
