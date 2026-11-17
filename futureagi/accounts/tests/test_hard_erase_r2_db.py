"""DB-REQUIRED r2: nonidentity collateral, raw paths, legacy pointer, issuance.

Run only in the isolated, egress-denied PostgreSQL runner. No signing key access:
the token encoder is mocked but live_person, SQL, locks, and inserts are real.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from django.db import close_old_connections, connection, models, transaction

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.service_grant import ServiceGrant
from accounts.models.workspace import WorkspaceMembership
from accounts.services import service_grants
from accounts.services.hard_erase import (
    HardEraseRefused,
    authorized_scope,
    locked_preflight,
)
from accounts.tests import test_hard_erase_db as support


class EveryRootDBTests(support.IsolatedEraseTestCase):
    setUp = support.HardEraseDBTests.setUp
    person = support.HardEraseDBTests.person
    workspace = support.HardEraseDBTests.workspace
    raw = support.HardEraseDBTests.raw

    def grant(self, user=None, org=None):
        return ServiceGrant.objects.create(
            jti=uuid.uuid4().hex,
            kind="batch",
            kid="test",
            aud="batch",
            org=(org or self.o2).pk,
            user=(user or self.u).pk,
            via="fa",
            sub="synthetic",
            scope="batch",
            iat=1,
            exp=2,
        )

    def test_nonidentity_root_cannot_remove_collateral_membership(self):
        member = OrganizationMembership.objects.create(
            user=self.u, organization=self.o1
        )
        workspace = self.workspace(owner=self.v)
        collateral = WorkspaceMembership.objects.create(
            user=self.v,
            workspace=workspace,
            organization_membership=member,
            deleted=True,
            is_active=False,
        )
        for delete in (
            lambda: self.raw(type(member)).filter(pk=member.pk).delete(),
            lambda: models.Model.delete(member),
        ):
            with self.assertRaises(HardEraseRefused):
                delete()
            self.assertTrue(
                self.raw(type(collateral)).filter(pk=collateral.pk).exists()
            )
            self.assertTrue(self.raw(type(member)).filter(pk=member.pk).exists())

    def test_raw_delete_sensitive_identity_with_retained_uuid_grant(self):
        user = self.person()
        self.grant(user)
        with self.assertRaises(HardEraseRefused):
            self.raw(User).filter(pk=user.pk)._raw_delete("default")
        self.assertTrue(self.raw(User).filter(pk=user.pk).exists())

    def test_cursor_execute_and_executemany_cannot_mutate_sensitive_tables(self):
        for sql, params, many in (
            ('DELETE FROM "accounts_user" WHERE id = %s', [self.u.pk], False),
            (
                'UPDATE "accounts_user" SET organization_id = NULL WHERE id = %s',
                [self.u.pk],
                False,
            ),
            ('TRUNCATE TABLE "accounts_user" CASCADE', [], False),
            ('DELETE FROM "accounts_user" WHERE id = %s', [[self.u.pk]], True),
            (
                'WITH chosen AS (SELECT 1) DELETE FROM "accounts_user" WHERE id = %s',
                [self.u.pk],
                False,
            ),
        ):
            with self.subTest(sql=sql, many=many):
                with self.assertRaises(HardEraseRefused), connection.cursor() as cursor:
                    (cursor.executemany if many else cursor.execute)(sql, params)
        self.assertTrue(self.raw(User).filter(pk=self.u.pk).exists())

    def test_user_only_erase_with_surviving_legacy_org_succeeds(self):
        pk = self.u.pk
        self.u.delete()
        self.assertTrue(self.raw(type(self.o1)).filter(pk=self.o1.pk).exists())
        self.assertFalse(self.raw(User).filter(pk=pk).exists())

    def connected(self, fn):
        close_old_connections()
        try:
            with transaction.atomic():
                return fn()
        finally:
            close_old_connections()

    def issue(self, user):
        claims = {
            "id": uuid.uuid4().hex,
            "kind": "batch",
            "kid": "test",
            "aud": "batch",
            "org": str(self.o2.pk),
            "user": str(user.pk),
            "workspace": "-",
            "via": "fa",
            "sub": "batch:" + str(uuid.uuid4()),
            "scope": "batch",
            "iat": 1,
            "exp": 2,
        }
        with (
            patch.object(
                service_grants, "issue_grant", return_value=("synthetic", claims)
            ),
            patch.object(service_grants, "pair_keys", return_value={}),
            patch.object(service_grants, "current_kid", return_value="test"),
        ):
            return service_grants.issue_person_grant(
                kind="batch",
                org=self.o2.pk,
                user=user.pk,
                workspace=None,
                sub=claims["sub"],
                ttl=1,
            )

    def test_unrelated_tenant_issuance_does_not_wait_for_erasure(self):
        OrganizationMembership.objects.create(user=self.v, organization=self.o2)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with locked_preflight([self.u], using="default"):
                # Resolving INSIDE the held boundary catches global advisory or
                # table locks. The other org/person must progress independently.
                _, _, grant = pool.submit(
                    self.connected, lambda: self.issue(self.v)
                ).result(timeout=5)
                # Newly created UUID fields retain the claim's text until a DB
                # read; compare identity, then verify the committed row as well.
                self.assertEqual(uuid.UUID(str(grant.user)), self.v.pk)
                self.assertEqual(uuid.UUID(str(grant.org)), self.o2.pk)
                stored = ServiceGrant.objects.get(pk=grant.pk)
                self.assertEqual(stored.user, self.v.pk)
                self.assertEqual(stored.org, self.o2.pk)

    def test_opposite_input_key_order_uses_independent_connections(self):
        from accounts.services.hard_erase_lifecycle import identity_keys, lifecycle_lock

        keys = sorted(identity_keys(organizations=[self.o1.pk], people=[self.u.pk]))
        started, acquired = Event(), Event()
        main_connection = connection.connection

        def second():
            connection.ensure_connection()
            self.assertIsNot(connection.connection, main_connection)
            started.set()
            with lifecycle_lock(keys=reversed(keys)):
                acquired.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with lifecycle_lock(keys=keys):
                future = pool.submit(self.connected, second)
                self.assertTrue(started.wait(5))
                self.assertFalse(acquired.is_set())
            future.result(timeout=10)
        self.assertTrue(acquired.is_set())

    def test_historical_same_scope_backfill_routes_through_typed_boundary(self):
        from importlib import import_module

        org_member = OrganizationMembership.objects.create(
            user=self.v, organization=self.o2
        )
        workspace = self.workspace(owner=self.v)
        member = WorkspaceMembership.objects.create(user=self.v, workspace=workspace)
        self.raw(WorkspaceMembership).filter(pk=member.pk).update(
            organization_membership=None
        )
        sql = import_module(
            "accounts.migrations.0022_backfill_ws_org_membership_fk"
        )._UPDATE_SQL
        with connection.cursor() as cursor:
            cursor.execute(sql, [[str(member.pk)]])
        member.refresh_from_db()
        self.assertEqual(member.organization_membership_id, org_member.pk)

    def test_credential_sealing_raw_update_preserves_ownership(self):
        from accounts.api_key_lookup import _KEPT_UNLESS_SEALED, _write_where_unchanged
        from accounts.models.user import OrgApiKey

        with patch(
            "accounts.api_key_lookup.seal_before_save", return_value=("", set())
        ):
            key = OrgApiKey._base_manager.create(
                user=self.u,
                organization=self.o1,
                name="erase-sealing-positive",
                api_key="synthetic-" + uuid.uuid4().hex,
            )
        row = {
            "pk": key.pk,
            "secret_key": key.secret_key,
            "secret_key_hash": key.secret_key_hash,
        }
        columns = dict.fromkeys(_KEPT_UNLESS_SEALED)
        columns["secret_key_hash"] = "synthetic-noncredential-hash"
        written = _write_where_unchanged(OrgApiKey, "default", [(row, columns)])
        self.assertEqual(written, {key.pk})
        key.refresh_from_db()
        self.assertEqual(key.secret_key, "")
        self.assertEqual(key.secret_key_hash, columns["secret_key_hash"])
        self.assertEqual(key.user_id, self.u.pk)
        self.assertEqual(key.organization_id, self.o1.pk)

    def test_nonce_cleanup_uses_independently_committed_connection(self):
        from accounts.services.envelope_store import DurableNonceStore

        store = DurableNonceStore()
        old, fresh = uuid.uuid4().hex, uuid.uuid4().hex
        with patch(
            "accounts.services.envelope_store.secrets.randbelow", return_value=0
        ):
            self.assertTrue(store.consume("fa-eval", old, 1, 2, 3))
            self.assertTrue(store.consume("fa-eval", fresh, 1000, 1100, 1000))
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM accounts_envelope_nonce WHERE id IN (%s,%s)",
                [old, fresh],
            )
            self.assertEqual({row[0] for row in cursor.fetchall()}, {fresh})

    def test_issuance_first_paused_after_live_person_then_erase_refuses(self):
        member = OrganizationMembership.objects.create(
            user=self.u, organization=self.o2
        )
        validated, release, erasing = Event(), Event(), Event()
        real_live = service_grants.live_person

        def live(*args):
            result = real_live(*args)
            validated.set()
            if not release.wait(10):
                raise AssertionError("issuer release timed out")
            return result

        # Membership alone would already refuse erase. Remove it IN the issuer's
        # transaction after validation, before grant INSERT, to isolate UUID race.
        def issuer():
            def remove_after_validation(*args):
                result = live(*args)
                self.raw(type(member)).filter(pk=member.pk).delete()
                return result

            with patch.object(
                service_grants, "live_person", side_effect=remove_after_validation
            ):
                return self.issue(self.u)

        def erase():
            erasing.set()
            self.u.delete()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.connected, issuer)
            try:
                self.assertTrue(validated.wait(5))
                second = pool.submit(self.connected, erase)
                self.assertTrue(erasing.wait(5))
            finally:
                release.set()
            first.result(timeout=15)
            with self.assertRaises(HardEraseRefused):
                second.result(timeout=15)
        self.assertTrue(self.raw(User).filter(pk=self.u.pk).exists())
        self.assertTrue(
            ServiceGrant.objects.filter(
                user=self.u.pk, revoked_at__isnull=True
            ).exists()
        )

    def test_erase_first_issuer_must_revalidate_after_lifecycle_lock(self):
        from tfc.service_envelope import EnvelopeRejected

        OrganizationMembership.objects.create(user=self.u, organization=self.o2)
        started, validated = Event(), Event()
        user_pk = self.u.pk
        real_live = service_grants.live_person

        def live(*args):
            validated.set()
            return real_live(*args)

        def issuer():
            started.set()
            return self.issue(self.u)

        with (
            patch.object(service_grants, "live_person", side_effect=live),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            with authorized_scope([self.o2.pk], using="default"):
                with locked_preflight([self.u], using="default") as collector:
                    future = pool.submit(self.connected, issuer)
                    self.assertTrue(started.wait(5))
                    self.assertFalse(validated.is_set())
                    collector.delete()
            with self.assertRaises(EnvelopeRejected):
                future.result(timeout=15)
        self.assertFalse(self.raw(User).filter(pk=user_pk).exists())
        self.assertFalse(
            ServiceGrant.objects.filter(user=user_pk, revoked_at__isnull=True).exists()
        )
