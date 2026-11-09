"""Real commits with a read mirror; no locking read or mutation on that alias.

The existing tfc/settings/test.py:65-72 mirror (default_direct -> default)
provides a second connection without adding settings or a database. This test
routes ordinary key reads there, as tfc/tests/test_router.py:32-49 does with
its replica mirror. The wrapper rejects writes/locking reads on the read alias;
a writable mirror is not itself evidence about a deployed PostgreSQL standby.
"""

from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import pytest
from django.db import connections, transaction
from django.test import TransactionTestCase, override_settings
from django.urls import resolve

from accounts.models.audit_log import AuditAction, AuditLog
from agentcc.models import AgentccAPIKey
from agentcc.services.key_audit import KeyAuditUnavailable, record_key_event
from agentcc.services.key_transactions import KeyTransactionUnavailable, revoke_key_with_audit
from agentcc.tests.test_key_rotation_commit_boundary import request_fixture
from agentcc.tests.test_key_rotation_ownership import KEYS, isolated_gateway

WRITE = "default"
READ = "default_direct"
pytestmark = pytest.mark.django_db(transaction=True, databases=[WRITE, READ])


class KeyReadMirrorRouter:
    def db_for_read(self, model, **hints):
        return READ if model is AgentccAPIKey else WRITE

    def db_for_write(self, model, **hints):
        return WRITE

    def allow_relation(self, obj1, obj2, **hints):
        return True


class StandaloneRevokeWriteAliasTests(TransactionTestCase):
    databases = {WRITE, READ}

    @pytest.fixture(autouse=True)
    def fixtures(self, organization, workspace, isolated_gateway):
        self.organization, self.workspace = organization, workspace
        self.gateway = isolated_gateway

    def setUp(self):
        super().setUp()
        self.assertEqual(connections[WRITE].vendor, "postgresql")
        self.assertEqual(connections[READ].settings_dict["TEST"]["MIRROR"], WRITE)
        self.actor, self.old, self.client, _ = request_fixture(
            self.organization, self.workspace,
        )
        self.url = f"{KEYS}{self.old.pk}/revoke/"
        self.view = resolve(self.url).func.cls
        self.routing = override_settings(DATABASE_ROUTERS=[KeyReadMirrorRouter()])
        self.routing.enable()
        self.addCleanup(self.routing.disable)

    def keys(self, alias=WRITE):
        return AgentccAPIKey.unscoped_objects.using(alias)

    def events(self):
        return AuditLog.no_workspace_objects.using(WRITE).filter(
            organization=self.organization, action=AuditAction.GATEWAY_KEY_REVOKED,
        )

    @contextmanager
    def observe_writes(self):
        self.sql_events, self.registrations, self.callbacks, self.pushes = [], [], [], []
        original = transaction.on_commit
        key_table = AgentccAPIKey._meta.db_table
        audit_table = AuditLog._meta.db_table

        def sql(execute, statement, params, many, context):
            alias = context["connection"].alias
            command = statement.lstrip().split(None, 1)[0].upper()
            locking = "FOR UPDATE" in statement.upper()
            # Reject a regression before it can mutate the writable mirror.
            if alias == READ:
                self.assertFalse(locking, "SELECT FOR UPDATE reached read alias")
                self.assertNotIn(command, {"INSERT", "UPDATE", "DELETE"})
            if key_table in statement and locking:
                self.sql_events.append(("lock", alias))
            if key_table in statement and command == "UPDATE":
                self.sql_events.append(("mutation", alias))
            if audit_table in statement and command == "INSERT":
                self.sql_events.append(("audit", alias))
            return execute(statement, params, many, context)

        def register(callback, using=None, robust=False):
            if callback.__name__ != "push_revocation":
                return original(callback, using=using, robust=robust)
            conn = connections[using]
            self.registrations.append(
                (using, robust, conn.in_atomic_block, conn.atomic_blocks[-1].durable)
            )

            def observed():
                self.callbacks.append((using, conn.get_autocommit(), conn.in_atomic_block))
                return callback()

            return original(observed, using=using, robust=robust)

        def push(identity):
            row = self.keys().get(pk=self.old.pk)
            event = self.events().get(target_id=self.old.pk)
            self.pushes.append((
                row._state.db, event._state.db, row.status,
                event.actor_id, identity == row.gateway_key_id,
            ))
            return False

        with ExitStack() as stack:
            for alias in (WRITE, READ):
                stack.enter_context(connections[alias].execute_wrapper(sql))
            stack.enter_context(patch.object(transaction, "on_commit", side_effect=register))
            self.gateway.revoke_key_confirmed.side_effect = push
            yield

    def assert_primary_revoke(self):
        self.assertEqual(self.sql_events, [
            ("lock", WRITE), ("mutation", WRITE), ("audit", WRITE),
        ])
        self.assertEqual(self.registrations, [(WRITE, True, True, True)])
        self.assertEqual(self.callbacks, [(WRITE, True, False)])
        self.assertEqual(self.pushes, [(
            WRITE, WRITE, AgentccAPIKey.REVOKED, self.actor.pk, True,
        )])
        self.gateway.revoke_key_confirmed.assert_called_once_with(self.old.gateway_key_id)
        self.assertEqual(self.events().count(), 1)

    def test_replica_loaded_service_uses_write_alias_for_lock_mutation_audit_and_callback(self):
        replica_row = self.keys(READ).get(pk=self.old.pk)
        self.assertEqual(replica_row._state.db, READ)
        with self.observe_writes():
            row, pending = revoke_key_with_audit(replica_row, self.actor)
        self.assertEqual(row._state.db, WRITE)
        self.assertEqual(row.status, AgentccAPIKey.REVOKED)
        self.assertTrue(pending)
        self.assert_primary_revoke()

    def test_revoke_endpoint_keeps_replica_lookup_but_writes_and_callbacks_on_primary(self):
        lookups = []
        get_object = self.view.get_object

        def lookup(view):
            row = get_object(view)  # Keep the real organization/owner permission path.
            lookups.append(row._state.db)
            return row

        with self.observe_writes(), patch.object(self.view, "get_object", lookup):
            with patch.dict(connections[WRITE].settings_dict, {"ATOMIC_REQUESTS": True}):
                response = self.client.post(self.url, format="json")
        self.assertEqual(lookups, [READ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["result"]["status"], AgentccAPIKey.REVOKED)
        self.assertIs(response.data["result"]["gateway_revocation_confirmed"], False)
        self.assert_primary_revoke()

    def test_replica_state_cannot_bypass_primary_ambient_transaction_guard(self):
        replica_row = self.keys(READ).get(pk=self.old.pk)
        with self.observe_writes(), transaction.atomic(using=WRITE):
            with self.assertRaises(KeyTransactionUnavailable):
                revoke_key_with_audit(replica_row, self.actor)
        self.assertEqual(self.sql_events, [])
        self.assertEqual(self.registrations, [])
        self.assertEqual(self.callbacks, [])
        self.gateway.revoke_key_confirmed.assert_not_called()
        self.assertEqual(self.keys().get(pk=self.old.pk).status, AgentccAPIKey.ACTIVE)

    def test_locked_source_never_moves_a_replica_lock_to_primary(self):
        with transaction.atomic(using=READ):
            row = self.keys(READ).select_for_update().get(pk=self.old.pk)
            with self.assertRaises(KeyTransactionUnavailable):
                revoke_key_with_audit(row, self.actor, locked_source=True)
        self.assertEqual(self.keys().get(pk=self.old.pk).status, AgentccAPIKey.ACTIVE)
        self.assertFalse(self.events().exists())
        self.gateway.revoke_key_confirmed.assert_not_called()

    def test_audit_writer_refuses_replica_origin_instead_of_writing_on_read_alias(self):
        row = self.keys(READ).get(pk=self.old.pk)
        with transaction.atomic(using=READ):
            with self.assertRaises(KeyAuditUnavailable):
                record_key_event(row, self.actor, AuditAction.GATEWAY_KEY_REVOKED)
        self.assertFalse(self.events().exists())

    def test_stale_service_row_returns_committed_revoke_without_second_audit_or_push(self):
        stale = self.keys(READ).get(pk=self.old.pk)
        row, _ = revoke_key_with_audit(self.old, self.actor)
        first_event = self.events().get()
        self.gateway.revoke_key_confirmed.reset_mock()
        with self.observe_writes():
            current, pending = revoke_key_with_audit(stale, self.actor)
        self.assertEqual(current.status, AgentccAPIKey.REVOKED)
        self.assertEqual(current.updated_at, row.updated_at)
        self.assertTrue(pending)  # Local idempotence is not remote enforcement.
        self.assertEqual(self.sql_events, [("lock", WRITE)])
        self.assertEqual(self.registrations, [])
        self.assertEqual(self.callbacks, [])
        self.assertEqual(self.events().get().pk, first_event.pk)
        self.gateway.revoke_key_confirmed.assert_not_called()

    def test_deleted_locked_row_is_returned_without_audit_mutation_or_push(self):
        stale = self.keys(READ).get(pk=self.old.pk)
        self.keys().filter(pk=self.old.pk).update(deleted=True)
        with self.observe_writes():
            current, pending = revoke_key_with_audit(stale, self.actor)
        self.assertTrue(current.deleted)
        self.assertEqual(current.status, AgentccAPIKey.ACTIVE)
        self.assertTrue(pending)
        self.assertEqual(self.sql_events, [("lock", WRITE)])
        self.assertEqual(self.registrations, [])
        self.assertEqual(self.callbacks, [])
        self.assertFalse(self.events().exists())
        self.gateway.revoke_key_confirmed.assert_not_called()

    def test_repeated_revoke_endpoint_does_not_retry_the_first_remote_push(self):
        self.assertEqual(self.client.post(self.url, format="json").status_code, 200)
        first_event = self.events().get()
        self.gateway.revoke_key_confirmed.reset_mock()
        with self.observe_writes():
            response = self.client.post(self.url, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.data["result"]["gateway_revocation_confirmed"], False)
        self.assertEqual(self.sql_events, [("lock", WRITE)])
        self.assertEqual(self.registrations, [])
        self.assertEqual(self.callbacks, [])
        self.assertEqual(self.events().get().pk, first_event.pk)
        self.gateway.revoke_key_confirmed.assert_not_called()
