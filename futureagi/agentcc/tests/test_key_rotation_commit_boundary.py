"""Real-commit RED targets: transaction=True is not Django TestCase isolation."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from django.db import OperationalError, connection, transaction
from django.urls import resolve

from accounts.models.audit_log import AuditAction
from accounts.tests.personal_keys import session_client_for
from agentcc.models import AgentccAPIKey
from agentcc.services.key_transactions import (
    KeyTransactionUnavailable, key_creation_transaction, revoke_key_with_audit,
)
from agentcc.tests.test_agentcc_permissions import _user_with_level
from agentcc.tests.test_key_rotation_ownership import KEYS, events, isolated_gateway, key_for
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db(transaction=True)


def request_fixture(organization, workspace):
    assert connection.get_autocommit() and not connection.in_atomic_block
    actor = _user_with_level(organization, Level.ADMIN)
    old = key_for(actor, organization)
    client = session_client_for(actor, organization, workspace)
    payload = {"name": "replacement", "rotates_key_id": str(old.pk)}
    return actor, old, client, payload


def no_remote(gateway):
    gateway.create_key.assert_not_called()
    gateway.import_committed_key.assert_not_called()
    gateway.revoke_key_confirmed.assert_not_called()
    gateway.revoke_key.assert_not_called()


def test_ambient_atomic_control_really_raises(isolated_gateway):
    assert connection.get_autocommit() and not connection.in_atomic_block
    with transaction.atomic():
        with pytest.raises(KeyTransactionUnavailable):
            with key_creation_transaction():
                pytest.fail("Refusal must occur before yielding to caller work")
    no_remote(isolated_gateway)


@pytest.mark.parametrize("depth", [1, 2])
def test_caller_outer_commit_failure_is_refused_before_issuance(
    organization, workspace, isolated_gateway, depth
):
    _, old, client, payload = request_fixture(organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    with patch.object(connection, "commit", side_effect=OperationalError("fixture commit")):
        with pytest.raises(OperationalError):
            with ExitStack() as stack:
                for _ in range(depth):
                    stack.enter_context(transaction.atomic())
                response = client.post(KEYS, payload, format="json")
                assert response.status_code == 503
                assert "key" not in (response.data.get("result") or {})
    no_remote(isolated_gateway)
    assert AgentccAPIKey.unscoped_objects.count() == before
    assert not events(organization).exists()
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE


def test_manual_transaction_is_refused_before_issuance(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    transaction.set_autocommit(False)
    try:
        assert not connection.in_atomic_block
        with patch("agentcc.services.key_issuance.secrets.token_hex") as generate:
            with pytest.raises(KeyTransactionUnavailable):
                with key_creation_transaction():
                    pytest.fail("Manual transaction yielded")
            response = client.post(KEYS, {**payload, "revoke_rotated_key": True}, format="json")
            assert response.status_code == 503
            generate.assert_not_called()
        no_remote(isolated_gateway)
    finally:
        transaction.rollback()
        transaction.set_autocommit(True)
    assert AgentccAPIKey.unscoped_objects.count() == before
    assert not events(organization).exists()
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE


def test_low_level_savepoint_schedule_refuses_before_work(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    before = AgentccAPIKey.unscoped_objects.count()
    with transaction.atomic():
        sid = transaction.savepoint()
        assert sid not in connection.savepoint_ids
        with pytest.raises(KeyTransactionUnavailable):
            with key_creation_transaction():
                pytest.fail("Low-level savepoint yielded")
        response = client.post(KEYS, {**payload, "revoke_rotated_key": True}, format="json")
        assert response.status_code == 503
        transaction.savepoint_rollback(sid)
    no_remote(isolated_gateway)
    assert AgentccAPIKey.unscoped_objects.count() == before
    assert not events(organization).exists()
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE


@pytest.mark.parametrize("rotate", [False, True])
def test_final_outer_commit_failure_has_no_remote_key(organization, workspace, isolated_gateway, rotate):
    _, old, client, payload = request_fixture(organization, workspace)
    if not rotate:
        payload.pop("rotates_key_id")
    before = AgentccAPIKey.unscoped_objects.count()
    observations = []

    def fail_commit():
        observations.append(events(organization).count())
        no_remote(isolated_gateway)
        raise OperationalError("fixture commit")

    with patch.object(connection, "commit", side_effect=fail_commit):
        response = client.post(KEYS, payload, format="json")
    assert response.status_code == 503
    assert observations == [2 if rotate else 1]
    no_remote(isolated_gateway)
    assert AgentccAPIKey.unscoped_objects.count() == before
    assert not events(organization).exists()
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE


def test_silent_outer_rollback_is_not_reported_as_created(organization, workspace, isolated_gateway):
    _, old, client, payload = request_fixture(organization, workspace)
    namespace = resolve(KEYS).func.cls.create.__globals__
    record = namespace["record_key_event"]
    before = AgentccAPIKey.unscoped_objects.count()

    def record_then_rollback(*args, **kwargs):
        result = record(*args, **kwargs)
        if args[2] == AuditAction.GATEWAY_KEY_ROTATED:
            transaction.set_rollback(True)
        return result

    with patch.dict(namespace, {"record_key_event": record_then_rollback}):
        response = client.post(KEYS, payload, format="json")
    assert response.status_code == 503
    no_remote(isolated_gateway)
    assert AgentccAPIKey.unscoped_objects.count() == before
    assert not events(organization).exists()


def test_no_compensation_is_needed_when_precommit_fails(organization, workspace, isolated_gateway):
    isolated_gateway.revoke_key.side_effect = RuntimeError("must not compensate")
    test_final_outer_commit_failure_has_no_remote_key(organization, workspace, isolated_gateway, True)


def test_error_in_later_robust_callback_does_not_skip_later_callbacks(
    organization, workspace, isolated_gateway
):
    _, _, client, payload = request_fixture(organization, workspace)
    namespace = resolve(KEYS).func.cls.create.__globals__
    record = namespace["record_key_event"]
    reached = []

    def fail():
        raise RuntimeError("synthetic callback")

    def record_with_callbacks(*args, **kwargs):
        result = record(*args, **kwargs)
        if args[2] == AuditAction.GATEWAY_KEY_ROTATED:
            transaction.on_commit(fail, robust=True)
            transaction.on_commit(lambda: reached.append(True), robust=True)
        return result

    with patch.dict(namespace, {"record_key_event": record_with_callbacks}):
        response = client.post(KEYS, payload, format="json")
    assert response.status_code == 201
    assert reached == [True]
    isolated_gateway.import_committed_key.assert_called_once()
    isolated_gateway.revoke_key.assert_not_called()
    assert events(organization).count() == 2


def test_revoke_final_commit_failure_discards_status_audit_and_gateway_callback(
    organization, workspace, isolated_gateway
):
    _, old, client, _ = request_fixture(organization, workspace)
    with patch.object(connection, "commit", side_effect=OperationalError("fixture commit")):
        response = client.post(f"{KEYS}{old.pk}/revoke/", format="json")
    assert response.status_code == 503
    no_remote(isolated_gateway)
    assert not events(organization).exists()
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE


def test_revoke_nested_in_caller_commit_failure_never_pushes_uncommitted_change(
    organization, isolated_gateway
):
    actor = _user_with_level(organization, Level.ADMIN)
    old = key_for(actor, organization)
    with patch.object(connection, "commit", side_effect=OperationalError("fixture commit")):
        with pytest.raises(OperationalError):
            with transaction.atomic():
                with pytest.raises(KeyTransactionUnavailable):
                    revoke_key_with_audit(old, actor)
                no_remote(isolated_gateway)
    no_remote(isolated_gateway)
    assert not events(organization).exists()
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE


def test_revoke_nested_success_is_refused_before_work(organization, isolated_gateway):
    actor = _user_with_level(organization, Level.ADMIN)
    old = key_for(actor, organization)
    with transaction.atomic():
        with pytest.raises(KeyTransactionUnavailable):
            revoke_key_with_audit(old, actor)
        no_remote(isolated_gateway)
    no_remote(isolated_gateway)
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE
    assert not events(organization).exists()


def test_nested_revoke_response_refuses_without_gateway_call(
    organization, workspace, isolated_gateway
):
    _, old, client, _ = request_fixture(organization, workspace)
    with transaction.atomic():
        response = client.post(f"{KEYS}{old.pk}/revoke/", format="json")
        assert response.status_code == 503
        assert "gateway_revocation_confirmed" not in (response.data.get("result") or {})
        no_remote(isolated_gateway)
    no_remote(isolated_gateway)
    old.refresh_from_db()
    assert old.status == AgentccAPIKey.ACTIVE
    assert not events(organization).exists()
