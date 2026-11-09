"""DB-REQUIRED T6/T8: real concurrent writers after collection and after check."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import TestCase

from django.db import IntegrityError, close_old_connections, transaction
from django.db.models.deletion import Collector

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.hard_erase import HardEraseRefused, locked_preflight
from accounts.tests import test_hard_erase_db as support


def assert_writer_refused(case, kind, write):
    if kind == "descendant":
        # create_user uses typed issuance: revalidation refuses before INSERT.
        with case.assertRaises(HardEraseRefused) as caught:
            write()
        case.assertEqual(
            str(caught.exception),
            "Hard erase refused: issuance refers to an erased identity or organization",
        )
    else:
        case.assertIn(
            kind, ("workspace", "reassignment", "org_grant", "workspace_grant")
        )
        # These writers still use raw ORM FK enforcement, not typed issuance.
        with case.assertRaises(IntegrityError) as caught:
            write()
        cause = caught.exception.__cause__
        sqlstate = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        case.assertEqual(sqlstate, "23503", "writer must fail its foreign key")


class WriterRefusalContractTests(TestCase):
    @staticmethod
    def fail_fk(sqlstate="23503"):
        cause = Exception("synthetic database constraint")
        cause.sqlstate = sqlstate
        raise IntegrityError("synthetic FK rejection") from cause

    @staticmethod
    def fail_typed(reason="issuance refers to an erased identity or organization"):
        raise HardEraseRefused(f"Hard erase refused: {reason}")

    def test_descendant_requires_the_exact_typed_refusal(self):
        assert_writer_refused(self, "descendant", self.fail_typed)
        with self.assertRaises(AssertionError):
            assert_writer_refused(
                self, "descendant", lambda: self.fail_typed("unrelated refusal")
            )
        with self.assertRaises(IntegrityError):
            assert_writer_refused(self, "descendant", self.fail_fk)

    def test_raw_writers_require_a_foreign_key_violation(self):
        for kind in ("workspace", "reassignment", "org_grant", "workspace_grant"):
            with self.subTest(kind=kind):
                assert_writer_refused(self, kind, self.fail_fk)
                with self.assertRaises(AssertionError):
                    assert_writer_refused(self, kind, lambda: self.fail_fk("23505"))
                with self.assertRaises(HardEraseRefused):
                    assert_writer_refused(self, kind, self.fail_typed)

    def test_success_is_never_a_refusal(self):
        for kind in (
            "descendant", "workspace", "reassignment", "org_grant", "workspace_grant"
        ):
            with self.subTest(kind=kind), self.assertRaises(AssertionError):
                assert_writer_refused(self, kind, lambda: None)


class HardEraseRaceTests(support.IsolatedEraseTestCase):
    # Reuse just the isolated fixtures/egress mocks, not another test class's tests.
    setUp = support.HardEraseDBTests.setUp
    person = support.HardEraseDBTests.person
    workspace = support.HardEraseDBTests.workspace
    raw = support.HardEraseDBTests.raw

    def writer(self, kind, target):
        other = self.person(self.o2)
        workspace = self.workspace(owner=other)
        if kind == "workspace":
            return lambda: self.workspace(owner=target)
        if kind == "reassignment":

            def reassign():
                workspace.created_by = target
                workspace.save(update_fields=["created_by"])
                return workspace

            return reassign
        if kind == "descendant":
            return lambda: self.person(self.o2, invited_by=target)
        if kind == "org_grant":
            return lambda: OrganizationMembership.objects.create(
                user=other,
                organization=self.o2,
                invited_by=target,
            )
        return lambda: WorkspaceMembership.objects.create(
            user=other,
            workspace=workspace,
            invited_by=target,
        )

    def connected(self, fn):
        close_old_connections()
        try:
            with transaction.atomic():
                return fn()
        finally:
            close_old_connections()

    def test_t6_committed_reference_after_original_collector_is_not_missed(self):
        for kind in (
            "workspace",
            "reassignment",
            "descendant",
            "org_grant",
            "workspace_grant",
        ):
            with self.subTest(kind=kind):
                target = self.person()  # membershipless; initially erasable
                write = self.writer(kind, target)
                stale = Collector(using="default", origin=target)
                stale.collect([target])
                with ThreadPoolExecutor(max_workers=1) as pool:
                    protected = pool.submit(self.connected, write).result(timeout=10)
                with self.assertRaises(HardEraseRefused):
                    stale.delete()
                self.assertTrue(self.raw(User).filter(pk=target.pk).exists())
                self.assertTrue(
                    self.raw(type(protected)).filter(pk=protected.pk).exists()
                )

    def test_t6_writer_after_checked_closure_cannot_commit_dangling_reference(self):
        for kind in (
            "workspace",
            "reassignment",
            "descendant",
            "org_grant",
            "workspace_grant",
        ):
            with self.subTest(kind=kind):
                target = self.person()
                target_pk = target.pk
                write = self.writer(kind, target)
                started = Event()

                def run(started=started, write=write):
                    started.set()
                    return self.connected(write)

                with ThreadPoolExecutor(max_workers=1) as pool:
                    with locked_preflight([target], using="default") as collector:
                        future = pool.submit(run)
                        self.assertTrue(started.wait(5))
                        self.assertFalse(future.done())
                        collector.delete()
                    # A refusal must match this writer's exact protection path;
                    # a completed write or an unrelated error is not PASS.
                    assert_writer_refused(self, kind, lambda: future.result(timeout=10))
                self.assertFalse(self.raw(User).filter(pk=target_pk).exists())
                self.assertFalse(self.raw(User).filter(invited_by_id=target_pk).exists())
                self.assertFalse(
                    self.raw(Workspace).filter(created_by_id=target_pk).exists()
                )
                for model in (OrganizationMembership, WorkspaceMembership):
                    self.assertFalse(
                        self.raw(model).filter(invited_by_id=target_pk).exists()
                    )

    def test_t8_fixture_preflight_observes_racing_descendant(self):
        # Never invoke the management command. Its boundary uses this same
        # selected Organization root, before its first ClickHouse constructor.
        write = self.writer("descendant", self.u)
        stale = Collector(using="default", origin=self.o1)
        stale.collect([self.o1])
        with ThreadPoolExecutor(max_workers=1) as pool:
            descendant = pool.submit(self.connected, write).result(timeout=10)
        with self.assertRaises(HardEraseRefused):
            stale.delete()
        self.assertTrue(self.raw(User).filter(pk=descendant.pk).exists())
