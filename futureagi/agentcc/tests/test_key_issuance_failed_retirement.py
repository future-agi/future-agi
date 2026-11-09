"""UKR-F1: committed FAILED hashes are revoked and never forgotten.

Run with Django's test runner: no pytest fixtures or network are required.
Only the gateway client is mocked. Bulk assertions call the real primary-backed
producer and render its JSON; authentication is not under test in that helper.
"""

import hashlib
import json
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, call, patch

from django.db import connection, transaction
from django.test import RequestFactory, TransactionTestCase
from django.utils import timezone
from rest_framework.renderers import JSONRenderer
from structlog.testing import capture_logs

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.tests.personal_keys import session_client_for
from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge
from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.services.key_issuance import Issuance
from agentcc.views.api_key_bulk import APIKeyBulkView
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context


class FailedIssuanceRetirementTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        clear_workspace_context()
        self.addCleanup(clear_workspace_context)
        self.gateway = MagicMock(spec=GatewayClient)
        self.gateway.import_committed_key.return_value = False
        self.gateway.revoke_key_confirmed.return_value = False
        gateway = patch.object(
            auth_bridge, "get_gateway_client", return_value=self.gateway
        )
        self.get_client = gateway.start()
        self.addCleanup(gateway.stop)
        self.organization = Organization.objects.create(name="Synthetic UKR-F1")
        self.user = User.objects.create_user(
            email=f"synthetic-ukr-{uuid.uuid4().hex}@example.invalid",
            name="Synthetic UKR-F1 actor",
            organization=self.organization,
            organization_role=OrganizationRoles.OWNER,
        )
        OrganizationMembership.no_workspace_objects.get_or_create(
            user=self.user,
            organization=self.organization,
            defaults={
                "is_active": True,
                "role": OrganizationRoles.OWNER,
                "level": Level.OWNER,
            },
        )

    def pending(self):
        raw = "synthetic-ukr-f1-credential-" + uuid.uuid4().hex
        issuance = Issuance("default")
        with transaction.atomic():
            issuance.api_key = AgentccAPIKey.unscoped_objects.create(
                gateway_key_id="synthetic-ukr-f1-" + uuid.uuid4().hex,
                key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                key_prefix="synthetic-ukr-f1",
                name="Synthetic UKR-F1 key",
                owner=self.user.email,
                user=self.user,
                organization=self.organization,
                workspace=None,
                status=AgentccAPIKey.PENDING,
            )
        self.assertTrue(connection.get_autocommit())
        self.assertFalse(connection.in_atomic_block)
        return issuance, issuance.api_key, raw

    def assert_failed(self, issuance, key):
        key.refresh_from_db()
        self.assertEqual(key.status, AgentccAPIKey.FAILED)
        self.assertTrue(issuance.committed)
        self.assertFalse(issuance.provisioned)
        self.assertFalse(auth_bridge.gateway_loadable_keys().filter(pk=key.pk).exists())
        self.assertFalse(auth_bridge.gateway_inflight_keys().filter(pk=key.pk).exists())

    def bulk(self):
        # The actual producer, queries, membership gates and JSON renderer run.
        request = RequestFactory().get("/agentcc/api-keys/bulk/")
        response = APIKeyBulkView().get(request)
        self.assertEqual(response.status_code, 200)
        return json.loads(JSONRenderer().render(response.data))

    def assert_retired(self, body, key):
        self.assertNotIn(key.gateway_key_id, {row["id"] for row in body["result"]})
        self.assertIn(
            {"id": key.gateway_key_id, "key_hash": key.key_hash.lower()},
            body["retired"],
        )

    def assert_outcome(self, logs, outcome, key, raw):
        events = [
            entry for entry in logs
            if entry.get("event") == "gateway_key_provisioning_revoke"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["outcome"], outcome)
        self.assertEqual(set(events[0]), {"event", "outcome", "log_level"})
        rendered = json.dumps(logs)
        for material in (raw, key.key_hash, key.key_prefix, key.gateway_key_id):
            self.assertNotIn(material, rendered)

    def test_import_lands_then_raises_records_failed_before_one_revoke(self):
        issuance, key, raw = self.pending()
        held = []
        states_at_revoke = []

        def lost_answer(payload):
            held.append(payload)
            raise GatewayClientError("synthetic lost answer " + raw)

        def revoke(key_id):
            stored = AgentccAPIKey.unscoped_objects.get(pk=key.pk)
            states_at_revoke.append((key_id, stored.status, connection.in_atomic_block))
            return True

        self.gateway.import_committed_key.side_effect = lost_answer
        self.gateway.revoke_key_confirmed.side_effect = revoke
        with capture_logs() as logs:
            issuance.after_commit()
        self.get_client.assert_called_once_with()
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["key_hash"], key.key_hash)
        self.assertNotIn(raw, json.dumps(held))
        self.assert_failed(issuance, key)
        self.assertEqual(states_at_revoke, [(key.gateway_key_id, AgentccAPIKey.FAILED, False)])
        self.gateway.revoke_key_confirmed.assert_called_once_with(key.gateway_key_id)
        self.gateway.revoke_key.assert_not_called()
        self.assert_outcome(logs, "confirmed", key, raw)

    def test_import_false_ack_still_revokes_once(self):
        issuance, key, raw = self.pending()
        self.gateway.revoke_key_confirmed.return_value = True
        with capture_logs() as logs:
            issuance.after_commit()
        self.assert_failed(issuance, key)
        self.get_client.assert_called_once_with()
        self.gateway.import_committed_key.assert_called_once()
        self.gateway.revoke_key_confirmed.assert_called_once_with(key.gateway_key_id)
        self.assert_outcome(logs, "confirmed", key, raw)

    def test_revoke_raises_without_escaping_or_logging_key_material(self):
        issuance, key, raw = self.pending()
        self.gateway.import_committed_key.side_effect = RuntimeError(raw)
        self.gateway.revoke_key_confirmed.side_effect = RuntimeError(
            "synthetic revoke failure " + raw + key.key_hash + key.key_prefix
        )
        with capture_logs() as logs:
            # Direct call: robust=True must not hide an escaping exception.
            issuance.after_commit()
        self.assert_failed(issuance, key)
        self.gateway.revoke_key_confirmed.assert_called_once_with(key.gateway_key_id)
        self.assert_outcome(logs, "failed", key, raw)
        self.assert_retired(self.bulk(), key)

    def test_revoke_404_finishes_callback_and_keeps_failed_negative_fact(self):
        issuance, key, raw = self.pending()
        self.gateway.revoke_key_confirmed.side_effect = GatewayClientError(
            "synthetic absent key " + raw, status_code=404
        )
        with capture_logs() as logs:
            issuance.after_commit()
        self.assert_failed(issuance, key)
        self.gateway.revoke_key_confirmed.assert_called_once_with(key.gateway_key_id)
        self.assert_outcome(logs, "not_found", key, raw)
        self.assert_retired(self.bulk(), key)

    def test_unconfirmed_revoke_next_bulk_pull_retires_hash_and_id(self):
        issuance, key, raw = self.pending()
        held = []

        def import_without_ack(payload):
            held.append(payload)
            return False

        self.gateway.import_committed_key.side_effect = import_without_ack
        with capture_logs() as logs:
            issuance.after_commit()
        self.assert_failed(issuance, key)
        self.gateway.revoke_key_confirmed.assert_called_once_with(key.gateway_key_id)
        self.assert_outcome(logs, "unconfirmed", key, raw)
        self.assertEqual(held[0]["key_hash"], key.key_hash)
        body = self.bulk()  # The very first pull after FAILED was committed.
        self.assert_retired(body, key)
        self.assertNotIn(raw, json.dumps(body))

    def test_failed_retirement_outlives_seven_days_for_every_revoke_outcome(self):
        for outcome in (True, False, "raise"):
            with self.subTest(outcome=outcome):
                issuance, key, _ = self.pending()
                self.gateway.revoke_key_confirmed.return_value = outcome is True
                self.gateway.revoke_key_confirmed.side_effect = (
                    RuntimeError("synthetic unavailable") if outcome == "raise" else None
                )
                issuance.after_commit()
                self.assert_failed(issuance, key)
                AgentccAPIKey.unscoped_objects.filter(pk=key.pk).update(
                    updated_at=timezone.now() - timedelta(days=8)
                )
                self.assert_retired(self.bulk(), key)

    def test_recent_failed_rows_retire_independently_of_revoke_outcome(self):
        for outcome in (True, False):
            with self.subTest(outcome=outcome):
                issuance, key, _ = self.pending()
                self.gateway.revoke_key_confirmed.return_value = outcome
                issuance.after_commit()
                self.assert_failed(issuance, key)
                self.assert_retired(self.bulk(), key)

        for unavailable in ("raises", "none"):
            with self.subTest(client=unavailable):
                issuance, key, _ = self.pending()
                self.get_client.reset_mock(side_effect=True)
                self.gateway.reset_mock()
                self.get_client.return_value = None
                if unavailable == "raises":
                    self.get_client.side_effect = GatewayClientError(
                        "Synthetic gateway unavailable", status_code=503
                    )
                issuance.after_commit()
                self.get_client.assert_called_once_with()
                self.gateway.import_committed_key.assert_not_called()
                self.gateway.revoke_key_confirmed.assert_not_called()
                self.gateway.revoke_key.assert_not_called()
                self.assert_failed(issuance, key)
                self.assert_retired(self.bulk(), key)

    def test_reconciliation_retries_failed_row_until_remote_revoke_succeeds(self):
        issuance, key, _ = self.pending()
        self.gateway.revoke_key_confirmed.side_effect = RuntimeError("synthetic outage")
        issuance.after_commit()
        self.assert_failed(issuance, key)
        AgentccAPIKey.unscoped_objects.filter(pk=key.pk).update(
            updated_at=timezone.now() - timedelta(days=8)
        )
        self.gateway.list_keys.return_value = {"data": [{
            "id": key.gateway_key_id,
            "status": "active",
            "key_prefix": key.key_prefix,
            "metadata": {"org_id": str(self.organization.pk)},
        }]}
        self.gateway.revoke_key.side_effect = GatewayClientError("synthetic outage")
        with self.assertRaises(GatewayClientError):
            auth_bridge.sync_keys(org=self.organization)
        self.assert_failed(issuance, key)
        self.gateway.revoke_key.side_effect = None
        auth_bridge.sync_keys(org=self.organization)
        self.assert_failed(issuance, key)
        self.assertEqual(self.gateway.revoke_key.call_args_list, [
            call(key.gateway_key_id), call(key.gateway_key_id)
        ])
        self.gateway.import_keys.assert_not_called()

    def test_success_activates_without_any_revoke(self):
        issuance, key, _ = self.pending()
        self.gateway.import_committed_key.return_value = True
        issuance.after_commit()
        key.refresh_from_db()
        self.assertEqual(key.status, AgentccAPIKey.ACTIVE)
        self.assertTrue(issuance.provisioned)
        self.gateway.revoke_key_confirmed.assert_not_called()
        self.gateway.revoke_key.assert_not_called()
        body = self.bulk()
        self.assertIn(key.gateway_key_id, {row["id"] for row in body["result"]})
        self.assertNotIn(key.gateway_key_id, {row["id"] for row in body["retired"]})

    def test_pending_is_inflight_but_a_stale_instance_cannot_hide_failed_state(self):
        issuance, key, _ = self.pending()
        body = self.bulk()
        self.assertNotIn(key.gateway_key_id, {row["id"] for row in body["result"]})
        self.assertNotIn(key.gateway_key_id, {row["id"] for row in body["retired"]})
        AgentccAPIKey.unscoped_objects.filter(pk=key.pk).update(status=AgentccAPIKey.FAILED)
        self.assertEqual(key.status, AgentccAPIKey.PENDING)
        issuance.after_commit()
        self.gateway.import_committed_key.assert_not_called()
        self.assert_retired(self.bulk(), key)

    def test_deleted_failed_hash_still_retires_but_old_revoked_horizon_is_unchanged(self):
        issuance, key, _ = self.pending()
        issuance.after_commit()
        AgentccAPIKey.unscoped_objects.filter(pk=key.pk).update(
            deleted=True, updated_at=timezone.now() - timedelta(days=8)
        )
        self.assert_retired(self.bulk(), key)
        AgentccAPIKey.unscoped_objects.filter(pk=key.pk).update(status=AgentccAPIKey.REVOKED)
        self.assertNotIn(key.gateway_key_id, {row["id"] for row in self.bulk()["retired"]})

    def test_failed_api_creation_returns_no_credential(self):
        # e06eb3a5 views/api_key.py:185-186,172 rejects the original spaced name
        # with 400 before issuance; serializers/api_key.py:220-224 validates it.
        # Do not change that API contract to satisfy a post-commit expectation.
        self.gateway.import_committed_key.side_effect = RuntimeError("synthetic lost answer")
        client = session_client_for(self.user, self.organization)
        response = client.post(
            "/agentcc/api-keys/", {"name": "Synthetic UKR-F1 API key"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["result"],
            "name: Name can only contain letters, numbers, hyphens, and underscores",
        )

        def assert_no_credential_fields(value):
            if isinstance(value, dict):
                self.assertTrue({
                    "key", "secret", "key_hash", "key_prefix", "gateway_key_id",
                }.isdisjoint(value))
                for child in value.values():
                    assert_no_credential_fields(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_credential_fields(child)

        assert_no_credential_fields(response.data)
        self.assertFalse(AgentccAPIKey.unscoped_objects.filter(
            organization=self.organization,
        ).exists())
        self.get_client.assert_not_called()
        self.gateway.import_committed_key.assert_not_called()
        self.gateway.revoke_key_confirmed.assert_not_called()

        # A valid name reaches the real failed-import path: api_key.py:277-283
        # returns only this 503 code, before raw-key delivery at :290-291.
        response = client.post(
            "/agentcc/api-keys/", {"name": "synthetic-ukr-f1-api-key"}, format="json"
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data, {"code": "KEY_PROVISIONING_FAILED"})
        assert_no_credential_fields(response.data)
        self.gateway.import_committed_key.assert_called_once()
        key = AgentccAPIKey.unscoped_objects.get(organization=self.organization)
        self.assertEqual(key.status, AgentccAPIKey.FAILED)
        self.assertNotIn(key.key_hash, json.dumps(response.data))
        self.assertNotIn(key.key_prefix, json.dumps(response.data))
        self.assertNotIn(key.gateway_key_id, json.dumps(response.data))
        self.assert_retired(self.bulk(), key)
