"""Standalone revocation owns its commit, including low-level savepoint callers."""

from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.test import TransactionTestCase
from django.urls import resolve

from accounts.models.audit_log import AuditAction
from agentcc.models import AgentccAPIKey
from agentcc.services import key_transactions
from agentcc.services.key_transactions import KeyTransactionUnavailable, revoke_key_with_audit
from agentcc.tests.test_key_rotation_commit_boundary import no_remote, request_fixture
from agentcc.tests.test_key_rotation_ownership import KEYS, events, isolated_gateway


class StandaloneRevokeCommitBoundaryTests(TransactionTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, organization, workspace, isolated_gateway):
        self.organization = organization
        self.workspace = workspace
        self.gateway = isolated_gateway

    def setUp(self):
        super().setUp()
        self.actor, self.old, self.client, _ = request_fixture(
            self.organization, self.workspace,
        )
        self.url = f"{KEYS}{self.old.pk}/revoke/"

    def assert_unchanged(self):
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, AgentccAPIKey.ACTIVE)
        self.assertFalse(events(self.organization).exists())
        no_remote(self.gateway)

    def test_low_level_savepoint_revoke_rollback_then_outer_commit(self):
        self.assertTrue(connection.get_autocommit())
        self.assertFalse(connection.in_atomic_block)
        with transaction.atomic():
            sid = transaction.savepoint()
            self.assertNotIn(sid, connection.savepoint_ids)
            with self.assertNumQueries(0), self.assertRaises(KeyTransactionUnavailable):
                revoke_key_with_audit(self.old, self.actor)
            transaction.savepoint_rollback(sid)
        # The outer block COMMITTED: no escaped callback may revoke the key.
        self.assert_unchanged()

    def test_revoke_callback_refuses_before_object_lookup_in_low_level_savepoint(self):
        view = resolve(self.url).func.cls
        with transaction.atomic():
            sid = transaction.savepoint()
            with patch.object(view, "get_object") as lookup:
                response = self.client.post(self.url, format="json")
                self.assertEqual(response.status_code, 503)
                lookup.assert_not_called()
            transaction.savepoint_rollback(sid)
        self.assert_unchanged()

    def test_manual_transaction_refuses_service_and_callback_before_work(self):
        transaction.set_autocommit(False)
        try:
            self.assertFalse(connection.in_atomic_block)
            with self.assertNumQueries(0), self.assertRaises(KeyTransactionUnavailable):
                revoke_key_with_audit(self.old, self.actor)
            with patch.object(resolve(self.url).func.cls, "get_object") as lookup:
                self.assertEqual(self.client.post(self.url, format="json").status_code, 503)
                lookup.assert_not_called()
        finally:
            transaction.rollback()
            transaction.set_autocommit(True)
        self.assert_unchanged()

    def test_actual_revoke_callback_opts_out_of_atomic_requests(self):
        using = self.old._state.db
        self.assertIn(using, resolve(self.url).func._non_atomic_requests)
        observations = []

        def after_commit(key_id):
            self.assertEqual(key_id, self.old.gateway_key_id)
            self.assertTrue(connection.get_autocommit())
            self.assertFalse(connection.in_atomic_block)
            self.old.refresh_from_db()
            self.assertEqual(self.old.status, AgentccAPIKey.REVOKED)
            audit = events(self.organization).get(action=AuditAction.GATEWAY_KEY_REVOKED)
            self.assertEqual(audit.actor_id, self.actor.pk)
            observations.append(True)
            return False

        self.gateway.revoke_key_confirmed.side_effect = after_commit
        with patch.dict(connection.settings_dict, {"ATOMIC_REQUESTS": True}):
            response = self.client.post(self.url, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(observations, [True])
        self.assertIs(response.data["result"]["gateway_revocation_confirmed"], False)

    def test_standalone_on_commit_is_robust_inside_durable_block(self):
        original = transaction.on_commit
        registrations = []

        def register(callback, using=None, robust=False):
            conn = transaction.get_connection(using)
            registrations.append((robust, conn.in_atomic_block, conn.atomic_blocks[-1].durable))
            return original(callback, using=using, robust=robust)

        with patch.object(transaction, "on_commit", side_effect=register):
            revoke_key_with_audit(self.old, self.actor)
        self.assertIn((True, True, True), registrations)
        self.gateway.revoke_key_confirmed.assert_called_once_with(self.old.gateway_key_id)

    def test_silent_rollback_does_not_report_revocation_success(self):
        original = key_transactions.record_key_event

        def audit_then_rollback(*args, **kwargs):
            original(*args, **kwargs)
            transaction.set_rollback(True)

        with patch.object(key_transactions, "record_key_event", side_effect=audit_then_rollback):
            response = self.client.post(self.url, format="json")
        self.assertEqual(response.status_code, 503)
        self.assert_unchanged()
