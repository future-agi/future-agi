"""Actual bulk producer snapshots for delayed Go consumer schedules.

KEY_ROTATION_BULK_CASES optionally exports synthetic test data for the Go bridge
regression. That gate requires this producer and the Go consumer in one run;
export uses create-only output and never includes a raw key or admin token.
"""

import hashlib
import json
import os
import secrets
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import TransactionTestCase
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge
from agentcc.tests.test_agentcc_permissions import _user_with_level
from agentcc.tests.test_key_rotation_atomic_command import command
from agentcc.tests.test_key_rotation_commit_boundary import request_fixture
from agentcc.tests.test_key_rotation_ownership import KEYS, events, key_for
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context


def synthetic_raw(pair):
    return auth_bridge.GATEWAY_KEY_SCHEME + pair * 24


def digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class BulkIssuanceProtocolTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        # Django reserves `fixtures` for loaddata filenames, not pytest fixtures.
        # Plain factories keep these real-commit tests runnable by both runners.
        clear_workspace_context()
        self.addCleanup(clear_workspace_context)
        self.organization = Organization.objects.create(name="Synthetic bulk protocol")
        # _user_with_level requires the default workspace to exist first.
        creator = User.objects.create_user(
            email=f"synthetic-bulk-{secrets.token_hex(8)}@example.invalid",
            name="Synthetic bulk creator",
            organization=self.organization,
            organization_role=OrganizationRoles.OWNER,
        )
        self.workspace = Workspace.objects.create(
            name="Synthetic bulk workspace", organization=self.organization,
            is_default=True, is_active=True, created_by=creator,
        )
        view = resolve(KEYS).func.cls
        bridge = view.create.__globals__["auth_bridge"]
        self.gateway = MagicMock()
        self.gateway.create_key.side_effect = AssertionError("Legacy remote issuance forbidden")
        self.gateway.import_committed_key.return_value = True
        self.gateway.revoke_key_confirmed.return_value = False
        gateway = patch.object(bridge, "get_gateway_client", return_value=self.gateway)
        gateway.start()
        self.addCleanup(gateway.stop)
        config = patch.dict(view.create.__globals__, {"ensure_first_org_config": MagicMock()})
        config.start()
        self.addCleanup(config.stop)
        self.actor, self.old, self.client, self.payload = request_fixture(
            self.organization, self.workspace,
        )
        self.old.key_hash = digest(synthetic_raw("b2"))
        self.old.save(update_fields=["key_hash", "updated_at"])
        token = secrets.token_hex(32)
        self.admin = APIClient()
        self.admin.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        auth = patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", token)
        auth.start()
        self.addCleanup(auth.stop)

    def bulk(self, **headers):
        response = self.admin.get("/agentcc/api-keys/bulk/", **headers)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def assert_membership(self, body, key, *, positive=False, retired=False):
        self.assertEqual(key.gateway_key_id in {row["id"] for row in body["result"]}, positive)
        self.assertEqual(key.gateway_key_id in {row["id"] for row in body["retired"]}, retired)
        self.assertEqual(key.key_hash in {row["key_hash"] for row in body["retired"]}, retired)

    def test_pending_payload_and_precommand_snapshot_export_for_real_go_consumer(self):
        cases = {"schema": "key-rotation-bulk-schedules-v1", "before": self.bulk()}
        self.assert_membership(cases["before"], self.old, positive=True)
        pending_ids = []

        def import_after_commit(payload):
            self.assertTrue(connection.get_autocommit())
            self.assertFalse(connection.in_atomic_block)
            key = AgentccAPIKey.unscoped_objects.get(gateway_key_id=payload["id"])
            self.assertEqual(key.status, AgentccAPIKey.PENDING)
            self.assertEqual(events(self.organization).count(), 3)
            self.assertEqual(payload["key_hash"], digest(synthetic_raw("a1")))
            cases["imported"] = payload
            cases["pending"] = self.bulk()
            self.assert_membership(cases["pending"], key)
            self.assert_membership(cases["pending"], self.old, retired=True)
            pending_ids.append(key.pk)
            return True

        self.gateway.import_committed_key.side_effect = import_after_commit
        with patch("agentcc.services.key_issuance.secrets.token_hex", return_value="a1" * 24):
            response = self.client.post(KEYS, command(self.payload), format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(pending_ids), 1)
        replacement = AgentccAPIKey.unscoped_objects.get(pk=pending_ids[0])
        self.assertEqual(replacement.status, AgentccAPIKey.ACTIVE)
        self.assertEqual(response.data["result"]["key"], synthetic_raw("a1"))
        cases["active"] = self.bulk()
        self.assert_membership(cases["active"], replacement, positive=True)
        self.assert_membership(cases["active"], self.old, retired=True)
        self.gateway.revoke_key_confirmed.assert_called_once_with(self.old.gateway_key_id)
        self.assertIs(response.data["result"]["rotation"]["gateway_revocation_confirmed"], False)

        # A leftover FAILED hash remains negative even if a remote list says active.
        replacement.status = AgentccAPIKey.FAILED
        replacement.save(update_fields=["status", "updated_at"])
        self.gateway.list_keys.return_value = {"data": [{
            "id": replacement.gateway_key_id, "status": "active",
            "key_prefix": replacement.key_prefix, "metadata": cases["imported"]["metadata"],
        }]}
        auth_bridge.sync_keys(org=self.organization)
        replacement.refresh_from_db()
        self.assertEqual(replacement.status, AgentccAPIKey.FAILED)
        self.gateway.revoke_key.assert_called_once_with(replacement.gateway_key_id)
        self.gateway.import_keys.assert_not_called()
        cases["failed"] = self.bulk()
        self.assert_membership(cases["failed"], replacement, retired=True)

        output = os.environ.get("KEY_ROTATION_BULK_CASES")
        if output:
            # Synthetic rows only; the actual rendered envelopes are unmodified.
            with Path(output).open("x", encoding="utf-8") as stream:
                json.dump(cases, stream, sort_keys=True)
                stream.write("\n")

    def test_pending_is_not_retired_but_still_conflicts_with_config_seed(self):
        key = key_for(self.actor, self.organization, status=AgentccAPIKey.PENDING,
                      key_hash=digest(synthetic_raw("c3")))
        body = self.bulk(HTTP_X_AGENTCC_CONFIG_KEY_HASHES=key.key_hash)
        self.assert_membership(body, key)
        self.assertIn(key.key_hash, body["config_conflicts"])
        self.assertGreaterEqual(body["snapshot"]["issued_ever"], 2)

    def test_terminal_and_authority_removed_pending_rows_still_retire(self):
        for reason in ("deleted", "revoked", "failed", "expired-status", "expiry",
                       "inactive-user", "inactive-membership", "deleted-membership"):
            with self.subTest(reason=reason):
                actor = _user_with_level(self.organization, Level.MEMBER)
                key = key_for(actor, self.organization, status=AgentccAPIKey.PENDING,
                              key_hash=digest("synthetic-retirement-" + reason))
                if reason == "deleted":
                    key.deleted = True
                elif reason in {"revoked", "failed", "expired-status"}:
                    key.status = {"revoked": AgentccAPIKey.REVOKED,
                                  "failed": AgentccAPIKey.FAILED,
                                  "expired-status": AgentccAPIKey.EXPIRED}[reason]
                elif reason == "expiry":
                    key.expires_at = timezone.now() - timedelta(seconds=1)
                elif reason == "inactive-user":
                    actor.is_active = False
                    actor.save(update_fields=["is_active"])
                else:
                    changes = {"is_active": False} if reason == "inactive-membership" else {"deleted": True}
                    OrganizationMembership._base_manager.filter(
                        user=actor, organization=self.organization,
                    ).update(**changes)
                key.save()
                self.assert_membership(self.bulk(), key, retired=True)

    def test_failed_retirement_outlives_seven_day_horizon(self):
        self.old.status = AgentccAPIKey.FAILED
        self.old.save(update_fields=["status", "updated_at"])
        AgentccAPIKey.unscoped_objects.filter(pk=self.old.pk).update(
            updated_at=timezone.now() - timedelta(days=8),
        )
        self.assert_membership(self.bulk(), self.old, retired=True)
