"""DB-REQUIRED: T1-T8. Run only on an isolated PostgreSQL test DB.

No command execution and no provider calls. External delivery is patched; SQL and
Django's real Collector/receivers remain real. TransactionTestCase is necessary
for writer-lock and rollback tests. NOT a local production/settings entrypoint.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.db import close_old_connections, connection, models, transaction
from django.db.models.signals import pre_delete
from django.test import TransactionTestCase
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError

from accounts.admin import CustomUserAdmin, OrganizationAdmin
from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.hard_erase import (
    HardEraseRefused,
    authorized_scope,
    locked_preflight,
)


class IsolatedEraseTestCase(TransactionTestCase):
    def _fixture_teardown(self):
        # Test-only destructive reset, not an application erase caller. A normal
        # Django flush now correctly fails the raw-SQL boundary. Require a test
        # database and scope its exception to teardown, never the test body.
        from accounts.services.hard_erase_sql import sql_authority

        if not str(connection.settings_dict["NAME"]).split("/")[-1].startswith("test_"):
            raise AssertionError(
                "hard-erase fixtures require an isolated test_ database"
            )
        with transaction.atomic(), sql_authority("default", "erase"):
            super()._fixture_teardown()


class HardEraseDBTests(IsolatedEraseTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("DB-REQUIRED: PostgreSQL independent connections")
        # Real cache/session operations stay in-process, never venue Redis.
        # UUID locations isolate concurrent cases; settings and cache contents
        # are restored/cleared by cleanups even if fixture setup fails.
        from django.core.cache import caches

        self.enterContext(
            self.settings(
                CACHES={
                    "default": {
                        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                        "LOCATION": f"hard-erase-{uuid.uuid4().hex}",
                    }
                },
                SESSION_ENGINE="django.contrib.sessions.backends.cache",
                SESSION_CACHE_ALIAS="default",
            )
        )
        self.addCleanup(caches["default"].clear)
        # URL resolution lazily imports the distributed lock manager, whose
        # constructor probes Redis. Refuse that ancillary probe at its client
        # boundary so it uses its local fallback; do not loosen the socket guard
        # or mock the admin routing/Collector/identity-lock behavior under test.
        self.enterContext(
            patch(
                "redis.Redis.ping",
                side_effect=RedisConnectionError("offline hard-erase fixture"),
            )
        )
        # libpq's isolated test-DB transport is not Python's socket API. All
        # Python-side provider/HTTP/Redis connections fail closed in these tests.
        for name in (
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "socket.create_connection",
        ):
            guard = patch(
                name, side_effect=AssertionError("test network egress forbidden")
            )
            guard.start()
            self.addCleanup(guard.stop)
        self.delivery = []
        # Prevent test egress even when the successful positive controls commit.
        targets = [
            "django.core.cache.cache.delete_many",
            "omega_hub.services.cache_keys.invalidate_listing",
            "accounts.signals.host_event_signals._drain_in_background",
            "accounts.api_key_revocation.publish_key_revocation",
            "accounts.services.service_grants.publish_grant_revocation",
            "agentcc.services.auth_bridge.revoke_key",
            "agentcc.services.mcp_vendor_connections.offboard_member",
            "omega_hub.services.outbox.forget_address",
            "omega_hub.services.storage.get_store",
            "webhooks.api_offboarding.pause_backlog",
            "model_hub.models.evals_metric.tombstone_feedback_vectors",
        ]
        # The fixture runner must additionally install its fail-closed socket guard.
        for target in targets:
            p = patch(target)
            self.delivery.append(p.start())
            self.addCleanup(p.stop)
        self.o1 = Organization.objects.create(name=f"erase-1-{uuid.uuid4()}")
        self.o2 = Organization.objects.create(name=f"erase-2-{uuid.uuid4()}")
        self.u = self.person(self.o1)
        self.v = self.person(self.o2)

    def person(self, org=None, **kwargs):
        return User.objects.create_user(
            email=f"erase-{uuid.uuid4()}@example.invalid",
            name="Erase fixture",
            organization=org,
            **kwargs,
        )

    def workspace(self, owner=None, org=None, **kwargs):
        return Workspace.objects.create(
            name=f"erase-{uuid.uuid4()}",
            organization=org or self.o2,
            created_by=owner or self.u,
            **kwargs,
        )

    def raw(self, model):
        return models.QuerySet(model=model, using="default")

    def assert_refused(self, action):
        before = {
            m: self.raw(m).count()
            for m in (
                User,
                Organization,
                Workspace,
                OrganizationMembership,
                WorkspaceMembership,
            )
        }
        for mock in self.delivery:
            mock.reset_mock()
        # Grant O1 explicitly so a refusal must find O2/collateral references,
        # not merely stop at the selected person's legacy O1 organization.
        with authorized_scope([self.o1.pk], using="default"):
            with self.assertRaises(HardEraseRefused):
                action()
        self.assertEqual(before, {m: self.raw(m).count() for m in before})
        for mock in self.delivery:
            mock.assert_not_called()

    def test_cache_and_sessions_work_with_all_python_sockets_blocked(self):
        from importlib import import_module

        from django.conf import settings
        from django.core.cache import cache

        cache.set("erase-venue-control", "isolated", timeout=30)
        self.assertEqual(cache.get("erase-venue-control"), "isolated")
        store = import_module(settings.SESSION_ENGINE).SessionStore
        session = store()
        session["erase-venue-control"] = "session-roundtrip"
        session.save()
        restored = store(session_key=session.session_key)
        self.assertEqual(restored["erase-venue-control"], "session-roundtrip")
        session.delete()

    def test_t1_routed_admin_permissions(self):
        self.workspace()
        url = reverse("admin:accounts_user_delete", args=[self.u.pk])
        for actor in (
            self.person(self.o1),
            self.person(self.o1, is_staff=True, is_active=False),
            self.person(self.o1, is_staff=True),
        ):
            self.client.force_login(actor)
            response = self.client.post(url, {"post": "yes"})
            self.assertIn(response.status_code, (302, 403))
            self.assertTrue(self.raw(User).filter(pk=self.u.pk).exists())
        actor = self.person(is_staff=True, is_superuser=True)
        self.client.force_login(actor)
        response = self.client.post(url, {"post": "yes"})
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Hard erase refused", status_code=403)
        self.assertTrue(self.raw(User).filter(pk=self.u.pk).exists())

    def test_t2_retained_and_membershipless_workspace(self):
        w = self.workspace(deleted=True, is_active=False)
        self.assertFalse(
            OrganizationMembership.objects.filter(
                user=self.u, organization=self.o2
            ).exists()
        )
        self.assert_refused(self.u.delete)
        self.assertTrue(self.raw(Workspace).filter(pk=w.pk).exists())
        member = OrganizationMembership.objects.create(
            user=self.u, organization=self.o2, is_active=False, deleted=True
        )
        self.assert_refused(lambda: User.objects.filter(pk=self.u.pk).delete())
        self.assertTrue(self.raw(OrganizationMembership).filter(pk=member.pk).exists())

    def test_t3_descendants_and_collateral_inviter_grants(self):
        self.v.invited_by = self.u
        self.v.save(update_fields=["invited_by"])
        descendant = self.person(self.o2, invited_by=self.v)
        self.workspace(owner=descendant)
        self.assert_refused(self.o1.delete)
        self.assert_refused(lambda: Organization.objects.filter(pk=self.o1.pk).delete())
        self.v.invited_by = None
        self.v.save(update_fields=["invited_by"])
        member = OrganizationMembership.objects.create(
            user=self.v, organization=self.o2, invited_by=self.u
        )
        w = self.workspace(owner=self.v)
        WorkspaceMembership.objects.create(
            user=self.v, workspace=w, invited_by=self.u, organization_membership=member
        )
        self.assert_refused(self.u.delete)

    def test_t4_all_entrypoints_and_mixed_bulk_atomicity(self):
        self.workspace()
        clean = self.person()
        site = AdminSite()
        user_admin = CustomUserAdmin(User, site)
        org_admin = OrganizationAdmin(Organization, site)
        actions = [
            self.u.delete,
            lambda: User.objects.filter(pk__in=[clean.pk, self.u.pk]).delete(),
            lambda: user_admin.delete_model(None, self.u),
            lambda: user_admin.delete_queryset(
                None, User.objects.filter(pk__in=[clean.pk, self.u.pk])
            ),
            self.o1.delete,
            lambda: org_admin.delete_model(None, self.o1),
            lambda: org_admin.delete_queryset(
                None, Organization.objects.filter(pk=self.o1.pk)
            ),
        ]
        for action in actions:
            self.assert_refused(action)
            self.assertTrue(self.raw(User).filter(pk=clean.pk).exists())

    def test_t5_no_receiver_runs_before_refusal_and_late_error_rolls_back(self):
        self.workspace()
        effects = []

        def receiver(sender, instance, using, **kwargs):
            effects.append("receiver")
            transaction.on_commit(lambda: effects.append("commit"), using=using)

        pre_delete.connect(receiver, sender=User, weak=False)
        try:
            self.assert_refused(self.u.delete)
            self.assertEqual(effects, [])
        finally:
            pre_delete.disconnect(receiver, sender=User)
        clean = self.person()
        original = clean.pk

        def fail_late(sender, instance, using, **kwargs):
            transaction.on_commit(lambda: effects.append("leaked"), using=using)
            raise RuntimeError("late receiver error")

        pre_delete.connect(fail_late, sender=User, weak=False)
        try:
            with self.assertRaisesMessage(RuntimeError, "late receiver error"):
                clean.delete()
        finally:
            pre_delete.disconnect(fail_late, sender=User)
        self.assertTrue(self.raw(User).filter(pk=original).exists())
        self.assertNotIn("leaked", effects)
        for mock in self.delivery:
            mock.assert_not_called()

    def test_t5_unknown_alias_fails_closed_before_receivers(self):
        # Unqualified aliases are refused, not silently redirected to default.
        with self.assertRaises(HardEraseRefused):
            with locked_preflight([self.u], using="unqualified"):
                self.fail("must not yield")

    def test_t6_writer_committed_before_preflight_is_seen(self):
        ready, release = Event(), Event()

        def writer():
            close_old_connections()
            try:
                with transaction.atomic():
                    self.workspace()
                    ready.set()
                    if not release.wait(5):
                        raise AssertionError("race release timed out")
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(writer)
            self.assertTrue(ready.wait(5))
            release.set()
            with self.assertRaises(HardEraseRefused):
                self.u.delete()
            future.result(timeout=10)
            self.assertTrue(self.raw(User).filter(pk=self.u.pk).exists())
            self.assertTrue(self.raw(Workspace).filter(created_by=self.u).exists())

    def test_t6_writer_after_check_cannot_commit_reference_to_erased_user(self):
        clean = self.person()
        started = Event()

        def writer():
            close_old_connections()
            try:
                started.set()
                # The table writer lock blocks this until erase commits; then FK
                # validation must fail, never commit an orphan/cascade casualty.
                self.workspace(owner=clean)
            finally:
                close_old_connections()

        from django.db import IntegrityError

        with ThreadPoolExecutor(max_workers=1) as pool:
            with locked_preflight([clean], using="default") as collector:
                future = pool.submit(writer)
                self.assertTrue(started.wait(5))
                self.assertFalse(future.done())
                collector.delete()
            with self.assertRaises(IntegrityError):
                future.result(timeout=10)

    def test_t7_membership_delete_and_authorized_cleanup(self):
        member = OrganizationMembership.objects.create(
            user=self.u, organization=self.o1
        )
        other = OrganizationMembership.objects.create(user=self.u, organization=self.o2)
        self.raw(OrganizationMembership).filter(pk=member.pk).delete()
        self.assertTrue(self.raw(User).filter(pk=self.u.pk).exists())
        self.assertTrue(self.raw(OrganizationMembership).filter(pk=other.pk).exists())
        clean = self.person()
        clean_pk = clean.pk
        clean.delete()
        self.assertFalse(self.raw(User).filter(pk=clean_pk).exists())
        org = Organization.objects.create(name="narrow-cleanup")
        user = self.person(org)
        with authorized_scope([org.pk], using="default"):
            user.delete()
        org.delete()

    def test_t1_related_permissions_and_routed_bulk(self):
        from django.contrib.auth.models import Permission

        self.workspace()
        actor = self.person(is_staff=True)
        actor.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="accounts", codename="delete_user"
            )
        )
        self.client.force_login(actor)
        url = reverse("admin:accounts_user_delete", args=[self.u.pk])
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        actor.user_permissions.set(Permission.objects.all())
        for model, pk in (("user", self.u.pk), ("organization", self.o1.pk)):
            url = reverse(f"admin:accounts_{model}_changelist")
            response = self.client.post(
                url,
                {
                    "action": "delete_selected",
                    "_selected_action": [str(pk)],
                    "post": "yes",
                },
            )
            self.assertEqual(response.status_code, 403)
            self.assertContains(response, "Hard erase refused", status_code=403)

    def test_t5_credential_mutations_and_callbacks_rollback(self):
        from django.db import IntegrityError
        from django.db.models.signals import post_delete

        from accounts.models.auth_token import AuthToken
        from accounts.models.service_grant import BatchCancellationIntent, ServiceGrant
        from accounts.models.user import OrgApiKey
        from accounts.signals.membership_signals import revoke_deleted_person_keys

        clean = self.person()
        key = OrgApiKey._base_manager.create(user=clean, organization=self.o1)
        token = AuthToken._base_manager.create(
            user=clean, auth_type="access", scoped_organization=self.o1
        )
        grant = ServiceGrant.objects.create(
            jti=uuid.uuid4().hex,
            kind="batch",
            kid="test",
            aud="batch",
            org=self.o1.pk,
            user=clean.pk,
            via="fa",
            sub="synthetic",
            scope="batch",
            iat=1,
            exp=2,
            batch_ref=uuid.uuid4(),
        )

        def fail(sender, **kwargs):
            # Real SQL uniqueness error after all pre_delete mutations.
            User.objects.create(email=self.u.email, name="duplicate fixture")

        # Move the real credential receiver after the other User receivers.
        original_receivers = list(pre_delete.receivers)
        pre_delete.disconnect(revoke_deleted_person_keys, sender=User)
        pre_delete.connect(revoke_deleted_person_keys, sender=User)
        post_delete.connect(fail, sender=User, weak=False)
        try:
            with authorized_scope([self.o1.pk], using="default"):
                with self.assertRaises(IntegrityError):
                    clean.delete()
        finally:
            post_delete.disconnect(fail, sender=User)
            with pre_delete.lock:
                pre_delete.receivers = original_receivers
                pre_delete.sender_receivers_cache.clear()
        key.refresh_from_db()
        token.refresh_from_db()
        grant.refresh_from_db()
        self.assertTrue(key.enabled)
        self.assertTrue(token.is_active)
        self.assertIsNone(grant.revoked_at)
        self.assertFalse(BatchCancellationIntent.objects.filter(grant=grant).exists())
        for mock in self.delivery:
            mock.assert_not_called()
        # A legitimate erase must STILL revoke retained non-FK service grants.
        with authorized_scope([self.o1.pk], using="default"):
            clean.delete()
        grant.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)
        self.assertFalse(self.raw(AuthToken).filter(pk=token.pk).exists())

    def test_t8_complete_fixture_preflight_and_injected_reference(self):
        # Exercise the exact shared boundary, NEVER execute the command.
        fixture_org = Organization.objects.create(name="fresh-fixture")
        fixture_user = self.person(fixture_org)
        self.workspace(owner=fixture_user, org=fixture_org)
        with locked_preflight(
            [fixture_org], using="default", origin=fixture_org
        ) as collector:
            collector.delete()
        self.assertFalse(self.raw(Organization).filter(pk=fixture_org.pk).exists())
        with authorized_scope([self.o1.pk], using="default"):
            self.workspace()
            with self.assertRaises(HardEraseRefused):
                with locked_preflight([self.o1], using="default"):
                    self.fail("ClickHouse would have run")
