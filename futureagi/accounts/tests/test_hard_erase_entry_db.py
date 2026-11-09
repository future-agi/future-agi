"""DB-REQUIRED real role promotion versus erasure, independent PG connections.

Not part of the offline runner. Use only the existing isolated test_ database
fixture runner with its fail-closed network guard.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from unittest.mock import patch

from django.db import close_old_connections, connection

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.services import hard_erase, member_role_service
from accounts.services.hard_erase import HardEraseRefused, authorized_scope
from accounts.tests import test_hard_erase_db as support
from tfc.constants.levels import Level


class RoleEraseOrderingDBTests(support.IsolatedEraseTestCase):
    def setUp(self):
        support.HardEraseDBTests.setUp(self)
        # Only this role-promotion fixture receives authority. The shared erase
        # fixture must remain membershipless for its negative controls.
        OrganizationMembership.objects.create(
            user=self.u,
            organization=self.o1,
            role="Owner",
            level=Level.OWNER,
            is_active=True,
        )
        from tfc.middleware.workspace_context import workspace_context

        self.enterContext(workspace_context(None, organization=self.o1, user=self.u))

    person = support.HardEraseDBTests.person
    workspace = support.HardEraseDBTests.workspace
    raw = support.HardEraseDBTests.raw

    def test_role_actor_has_live_authority_and_can_promote_in_scope(self):
        from tfc.permissions.utils import (
            get_effective_workspace_level,
            get_org_membership,
        )

        membership = get_org_membership(self.u)
        self.assertIsNotNone(membership)
        self.assertEqual(membership.organization_id, self.o1.pk)
        self.assertEqual(membership.level_or_legacy, Level.OWNER)
        workspace = self.workspace(owner=self.u, org=self.o1)
        self.assertGreaterEqual(
            get_effective_workspace_level(self.u, workspace.pk), Level.WORKSPACE_ADMIN
        )
        target = self.person(self.o1)
        target_membership = OrganizationMembership.objects.create(
            user=target, organization=self.o1, role="Member", level=Level.MEMBER
        )
        member_role_service.update_member_role(
            organization=self.o1,
            actor=self.u,
            target_user_id=target.pk,
            org_level=Level.ADMIN,
        )
        target_membership.refresh_from_db()
        self.assertEqual(target_membership.level, Level.ADMIN)
        self.assertEqual(
            self.raw(WorkspaceMembership).get(user=target, workspace=workspace).level,
            Level.WORKSPACE_ADMIN,
        )

    def test_unprivileged_actor_still_cannot_promote(self):
        actor, target = self.person(self.o1), self.person(self.o1)
        member = OrganizationMembership.objects.create(
            user=target, organization=self.o1, role="Member", level=Level.MEMBER
        )
        with self.assertRaises(member_role_service.MemberRoleUpdateError) as caught:
            member_role_service.update_member_role(
                organization=self.o1,
                actor=actor,
                target_user_id=target.pk,
                org_level=Level.ADMIN,
            )
        self.assertEqual(caught.exception.code, "ROLE_ASSIGN_FORBIDDEN")
        self.assertEqual(caught.exception.status_code, 403)
        member.refresh_from_db()
        self.assertEqual(member.level, Level.MEMBER)
        self.assertFalse(self.raw(WorkspaceMembership).filter(user=target).exists())

    def test_both_independent_connection_orderings_have_no_row_advisory_inversion(self):
        for erase_first in (False, True):
            with self.subTest(erase_first=erase_first):
                target = self.person(self.o1)
                OrganizationMembership.objects.create(
                    user=target,
                    organization=self.o1,
                    role="Member",
                    level=Level.MEMBER,
                )
                workspace = self.workspace(owner=self.u, org=self.o1)
                first_inside, second_attempt, release = Event(), Event(), Event()
                original_apply = member_role_service._apply_org_level_change
                original_rows = hard_erase._lock_rows
                role_rows = []

                def apply_role(
                    *,
                    role_rows=role_rows,
                    erase_first=erase_first,
                    first_inside=first_inside,
                    release=release,
                    original_apply=original_apply,
                    **kwargs,
                ):
                    role_rows.append("entered")
                    if not erase_first:
                        first_inside.set()
                        if not release.wait(5):
                            raise AssertionError("role barrier timed out")
                    result = original_apply(**kwargs)
                    self.assertEqual(
                        self.raw(OrganizationMembership)
                        .get(user_id=target.pk, organization=self.o1)
                        .level,
                        Level.ADMIN,
                    )
                    self.assertEqual(
                        self.raw(WorkspaceMembership)
                        .get(user_id=target.pk, workspace=workspace)
                        .level,
                        Level.WORKSPACE_ADMIN,
                    )
                    return result

                def erase_rows(
                    *args,
                    erase_first=erase_first,
                    first_inside=first_inside,
                    release=release,
                    original_rows=original_rows,
                    **kwargs,
                ):
                    if erase_first:
                        first_inside.set()
                        if not release.wait(5):
                            raise AssertionError("erase barrier timed out")
                    return original_rows(*args, **kwargs)

                def run(
                    erase,
                    erase_first=erase_first,
                    second_attempt=second_attempt,
                    target=target,
                ):
                    close_old_connections()
                    try:

                        def observed(execute, sql, params, many, context):
                            if erase != erase_first and "pg_advisory_xact_lock" in sql:
                                second_attempt.set()
                            return execute(sql, params, many, context)

                        with connection.execute_wrapper(observed):
                            # A bounded server timeout makes a regression fail,
                            # rather than leave a stuck test process indefinitely.
                            with connection.cursor() as cursor:
                                cursor.execute("SET lock_timeout = '4s'")
                            if erase:
                                with authorized_scope([self.o1.pk], using="default"):
                                    self.raw(User).filter(pk=target.pk).delete()
                                return "erased"
                            try:
                                member_role_service.update_member_role(
                                    organization=self.o1,
                                    actor=self.u,
                                    target_user_id=target.pk,
                                    org_level=Level.ADMIN,
                                )
                            except HardEraseRefused:
                                return "refused"
                            return "promoted"
                    finally:
                        connection.close()
                        close_old_connections()

                with (
                    patch.object(
                        member_role_service,
                        "_apply_org_level_change",
                        side_effect=apply_role,
                    ),
                    patch.object(hard_erase, "_lock_rows", side_effect=erase_rows),
                    ThreadPoolExecutor(
                        max_workers=2, thread_name_prefix="erase-entry"
                    ) as pool,
                ):
                    first = pool.submit(run, erase_first)
                    try:
                        self.assertTrue(first_inside.wait(5))
                        second = pool.submit(run, not erase_first)
                        self.assertTrue(second_attempt.wait(5))
                        self.assertFalse(second.done(), current_thread().name)
                        if erase_first:
                            self.assertEqual(role_rows, [])
                    finally:
                        release.set()
                    outcomes = {first.result(timeout=6), second.result(timeout=6)}
                self.assertEqual(
                    outcomes, {"erased", "refused" if erase_first else "promoted"}
                )
                self.assertFalse(self.raw(User).filter(pk=target.pk).exists())
                self.assertFalse(
                    self.raw(WorkspaceMembership)
                    .filter(workspace=workspace, user_id=target.pk)
                    .exists()
                )
