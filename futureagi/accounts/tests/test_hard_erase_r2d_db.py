"""DB-REQUIRED ruling 349: real registry, ORM joins, logs and audit receivers."""

import uuid
from unittest.mock import patch

from django.apps import apps
from django.db import connection, models

from accounts.models import User
from accounts.models.audit_log import AuditLog
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.services.hard_erase import HardEraseRefused, authorized_scope
from accounts.services.hard_erase_sql_policy import protected_models
from accounts.tests import test_hard_erase_db as support


class OrdinaryWritesDBTests(support.IsolatedEraseTestCase):
    setUp = support.HardEraseDBTests.setUp
    person = support.HardEraseDBTests.person
    workspace = support.HardEraseDBTests.workspace
    raw = support.HardEraseDBTests.raw

    def test_real_prism_registry_and_modifier_refusals(self):
        from accounts.services.hard_erase_sql import _TABLES

        gateway = [
            m
            for m in protected_models(apps.get_models())
            if m.__module__.startswith("agentcc.")
        ]
        self.assertGreaterEqual(len(gateway), 14)
        for model in gateway:
            self.assertEqual(model._meta.app_label, "prism")
            self.assertIn(model._meta.db_table, _TABLES)
            table = connection.ops.quote_name(model._meta.db_table)
            for text in (
                f"TRUNCATE TABLE ONLY public.{table} CASCADE",
                f"DELETE FROM public.{table}",
            ):
                for many in (False, True):
                    with (
                        self.assertRaises(HardEraseRefused),
                        connection.cursor() as cursor,
                    ):
                        (cursor.executemany if many else cursor.execute)(
                            text, [[]] if many else []
                        )
        for text in (
            'UPDATE public."accounts_user" * AS u SET organization_id=NULL',
            'UPDATE public."accounts_user" * u SET organization_id=NULL',
            'TRUNCATE TABLE ONLY public."accounts_user" CASCADE',
        ):
            with self.assertRaises(HardEraseRefused), connection.cursor() as cursor:
                cursor.execute(text)

    def test_log_bulk_inserts_and_organization_erase_accept_free_form_labels(self):
        from accounts.models import Organization
        from agentcc.models.mcp_tool_call import AgentccToolCall
        from agentcc.models.request_log import AgentccRequestLog

        org = Organization.objects.create(name="logs-only-" + uuid.uuid4().hex)
        for label in ("", "end-user-42"):
            request = AgentccRequestLog(
                organization=org, user_id=label, request_id=uuid.uuid4().hex
            )
            AgentccRequestLog._base_manager.bulk_create([request])
            event = uuid.uuid4().hex
            call = AgentccToolCall(
                organization=org, user_id=label, event_id=event, tool_call_id=event
            )
            AgentccToolCall.objects.bulk_create([call])
            self.assertTrue(
                AgentccRequestLog._base_manager.filter(pk=request.pk).exists()
            )
            self.assertTrue(AgentccToolCall.objects.filter(pk=call.pk).exists())
        pk = org.pk
        org.delete()
        self.assertFalse(self.raw(Organization).filter(pk=pk).exists())

    def test_real_offboarding_and_reactivation_preserve_joined_updates(self):
        from accounts.services.offboarding import (
            offboard_organization_member,
            reactivate_organization_member,
        )
        from tfc.constants.roles import OrganizationRoles

        OrganizationMembership.objects.create(
            user=self.u, organization=self.o1, role=OrganizationRoles.MEMBER
        )
        workspace = self.workspace(owner=self.v, org=self.o1)
        member = WorkspaceMembership.objects.create(user=self.u, workspace=workspace)
        foreign = WorkspaceMembership.objects.create(
            user=self.u, workspace=self.workspace(owner=self.v, org=self.o2)
        )
        offboard_organization_member(self.u, self.o1, actor=self.v)
        member.refresh_from_db()
        foreign.refresh_from_db()
        self.assertFalse(member.is_active)
        self.assertTrue(foreign.is_active)
        reactivate_organization_member(self.u, self.o1, actor=self.v)
        member.refresh_from_db()
        self.assertTrue(member.is_active)
        self.assertTrue(
            AuditLog._base_manager.filter(
                organization=self.o1,
                actor=self.v,
                action="member.removed",
                target_id=str(self.u.pk),
            ).exists()
        )

    def test_membership_removal_and_person_erase_persist_receiver_audits(self):
        from tfc.constants.roles import OrganizationRoles

        # A valid admin in the same org, deliberately absent from the closure.
        OrganizationMembership.objects.create(
            user=self.v, organization=self.o1, role=OrganizationRoles.ADMIN
        )
        for operation in ("workspace", "organization", "person"):
            user = self.person(self.o1)
            org_member = OrganizationMembership.objects.create(
                user=user, organization=self.o1
            )
            member = WorkspaceMembership.objects.create(
                user=user,
                workspace=self.workspace(owner=self.u, org=self.o1),
                organization_membership=org_member,
            )
            target = str(user.pk)
            with patch(
                "accounts.signals.membership_signals.get_current_actor",
                return_value=self.v,
            ):
                if operation == "person":
                    with authorized_scope([self.o1.pk], using="default"):
                        self.raw(User).filter(pk=user.pk).delete()
                elif operation == "organization":
                    self.raw(OrganizationMembership).filter(pk=org_member.pk).delete()
                else:
                    models.QuerySet(model=WorkspaceMembership, using="default").filter(
                        pk=member.pk
                    ).delete()
            self.assertTrue(
                AuditLog._base_manager.filter(
                    organization=self.o1,
                    actor=self.v,
                    action="member.removed",
                    scope="workspace",
                    target_id=target,
                ).exists()
            )
            if operation != "workspace":
                self.assertTrue(
                    AuditLog._base_manager.filter(
                        organization=self.o1,
                        actor=self.v,
                        action="member.removed",
                        scope="organization",
                        target_id=target,
                    ).exists()
                )
