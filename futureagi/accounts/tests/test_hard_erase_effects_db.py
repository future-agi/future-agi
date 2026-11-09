"""DB-REQUIRED gateway/webhook/Hub effect qualification; no external delivery."""

import uuid
from unittest import TestCase

from django.db import transaction
from django.db.models.signals import post_delete

from accounts.models import User
from accounts.services.hard_erase import HardEraseRefused, authorized_scope
from accounts.tests import test_hard_erase_db as support
from agentcc.models import AgentccAPIKey
from omega_hub.models import Entry, Like, Publisher, Report
from webhooks.models import Endpoint


class EffectFixtureContractTests(TestCase):
    def test_django_fixture_declaration_is_not_a_bound_builder(self):
        self.assertFalse(callable(EraseEffectsDBTests.fixtures))


class EraseEffectsDBTests(support.IsolatedEraseTestCase):
    setUp = support.HardEraseDBTests.setUp
    person = support.HardEraseDBTests.person
    workspace = support.HardEraseDBTests.workspace
    raw = support.HardEraseDBTests.raw

    def build_effect_fixtures(self):
        self.gateway = AgentccAPIKey._base_manager.create(
            user=self.u,
            organization=self.o2,
            name="erase-fixture",
            gateway_key_id="synthetic-" + uuid.uuid4().hex,
        )
        self.endpoint = Endpoint.objects.create(
            organization=self.o2, owner=self.u, name="erase-fixture"
        )
        self.publisher = Publisher._base_manager.create(
            handle="erase-" + uuid.uuid4().hex[:20],
            kind="user",
            owner_user=self.u,
            display_name="Fixture",
            avatar_key="synthetic/avatar",
        )
        self.entry = Entry._base_manager.create(
            publisher=self.publisher, name="fixture", kind="agent", like_count=1
        )
        Like.objects.create(user=self.u, entry=self.entry)
        self.report = Report._base_manager.create(
            entry=self.entry, reporter=self.u, reason="other", details=self.u.email
        )
        for effect in self.delivery:
            effect.reset_mock()

    def assert_unchanged(self):
        for obj in (
            self.gateway,
            self.endpoint,
            self.publisher,
            self.entry,
            self.report,
        ):
            obj.refresh_from_db()
        self.assertEqual(self.gateway.status, AgentccAPIKey.ACTIVE)
        self.assertEqual(self.endpoint.status, "enabled")
        self.assertEqual(self.publisher.owner_user_id, self.u.pk)
        self.assertIsNone(self.publisher.erased_at)
        self.assertEqual(self.entry.like_count, 1)
        self.assertEqual(self.report.details, self.u.email)
        for effect in self.delivery:
            effect.assert_not_called()

    def test_protected_effect_fixtures_are_untouched_on_refusal(self):
        self.build_effect_fixtures()
        with self.assertRaises(HardEraseRefused):
            self.u.delete()
        self.assert_unchanged()

    def test_late_failure_rolls_back_all_local_effects_and_callbacks(self):
        self.build_effect_fixtures()

        def fail(sender, **kwargs):
            # Real receivers have run; the final transaction must undo them.
            self.assertEqual(
                self.raw(AgentccAPIKey).get(pk=self.gateway.pk).status,
                AgentccAPIKey.REVOKED,
            )
            self.assertEqual(
                Endpoint.objects.get(pk=self.endpoint.pk).status, "disabled_manual"
            )
            self.assertNotEqual(
                self.raw(Report).get(pk=self.report.pk).details, self.u.email
            )
            raise RuntimeError("after-real-effects")

        post_delete.connect(fail, sender=User, weak=False)
        try:
            with authorized_scope([self.o2.pk], using="default"):
                with self.assertRaisesMessage(RuntimeError, "after-real-effects"):
                    self.u.delete()
        finally:
            post_delete.disconnect(fail, sender=User)
        self.assert_unchanged()

    def test_permitted_erase_reconciles_local_effects_before_commit_delivery(self):
        self.build_effect_fixtures()
        with transaction.atomic(), authorized_scope([self.o2.pk], using="default"):
            self.u.delete()
            for effect in self.delivery:
                effect.assert_not_called()
            self.gateway.refresh_from_db()
            self.endpoint.refresh_from_db()
            self.publisher.refresh_from_db()
            self.entry.refresh_from_db()
            self.report.refresh_from_db()
            self.assertEqual(self.gateway.status, AgentccAPIKey.REVOKED)
            self.assertEqual(self.endpoint.status, "disabled_manual")
            self.assertIsNone(self.publisher.owner_user_id)
            self.assertIsNotNone(self.publisher.erased_at)
            self.assertEqual(self.entry.like_count, 0)
            self.assertNotIn(self.u.email, self.report.details)
        # These are real receiver callbacks replaced only at the delivery edge.
        self.delivery[1].assert_called()  # Hub listing cache
        self.delivery[5].assert_called()  # Gateway revoke
        self.delivery[8].assert_called()  # Avatar store
        self.delivery[9].assert_called()  # Webhook backlog pause
