"""Session exchange by personal API key (SB W2-KEYS; READINESS E09, L03).

A user's key exchanges for one assertion per request, signed `auth_method=api_key` with the
key's id (`key_id`) and never a browser `sid`. The key acts as its owner, with the owner's
current role, in the organization the key belongs to and the workspace it is pinned to (else
the one selected). It never crosses to another organization, never falls back to another
workspace, and stops the moment it is disabled, deleted or its owner loses access.
"""

import os
from unittest.mock import patch

from django.core.cache import cache

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests.personal_keys import personal_key_for
from accounts.tests.test_omega_session_exchange import ExchangeFixture
from omega_rollout.testing import admit_for_tests


class OmegaSessionApiKeyTests(ExchangeFixture):
    def personal_key(self, *, key_type="user", user=None, workspace=None):
        if key_type == "user":
            return personal_key_for(
                user or self.user, self.organization, workspace=workspace
            )
        # Explicit legacy rows are refusal controls, never successful fixtures.
        return OrgApiKey.no_workspace_objects.create(
            organization=self.organization,
            type=key_type,
            user=user,
            workspace=workspace,
        )

    def key_exchange(self, key, *, body=None, headers=None):
        return self.exchange(
            body=body,
            headers={
                "HTTP_X_API_KEY": key.api_key,
                "HTTP_X_SECRET_KEY": key.secret_key,
                **(headers or {}),
            },
        )

    def test_personal_key_emits_api_key_claim_and_current_role(self):
        key = self.personal_key(workspace=self.workspace)
        claims = self.claims(self.key_exchange(key))
        self.assertEqual(claims["auth_method"], "api_key")
        self.assertEqual(claims["key_id"], str(key.pk))
        self.assertNotIn("sid", claims)
        self.assertEqual(claims["sub"], str(self.user.pk))
        self.assertEqual(claims["role"], "member")

    def test_personal_key_cannot_cross_org_or_use_a_different_workspace(self):
        key = self.personal_key(workspace=self.workspace)
        other_org = Organization.objects.create(name="Other key tenant")
        admit_for_tests(other_org)
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other key workspace", organization=other_org, created_by=self.user
        )
        for headers in (
            {"HTTP_X_ORGANIZATION_ID": str(other_org.pk)},
            {"HTTP_X_WORKSPACE_ID": str(other_workspace.pk)},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(
                    self.key_exchange(key, headers=headers).status_code, 403
                )

    def test_revoked_personal_key_is_refused_on_the_next_exchange(self):
        key = self.personal_key(workspace=self.workspace)
        self.assertEqual(self.key_exchange(key).status_code, 200)
        OrgApiKey.all_objects.filter(pk=key.pk).update(enabled=False)
        response = self.key_exchange(key)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("accessToken", response.data)

    def test_system_and_mcp_keys_are_not_admitted(self):
        for key_type in ("system", "mcp"):
            with self.subTest(key_type=key_type):
                key = self.personal_key(key_type=key_type, user=None)
                response = self.key_exchange(key)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.data["code"], "api_key_retired")
                self.assertEqual(response.data["create_url"], "/dashboard/keys")
                self.assertNotIn("accessToken", response.data)

    def test_api_key_cannot_batch_and_owner_budget_covers_multiple_keys(self):
        key = self.personal_key(workspace=self.workspace)
        self.assertEqual(
            self.key_exchange(
                key, body={"audience": "omega-squad", "count": 2}
            ).status_code,
            400,
        )
        cache.clear()
        with patch.dict(
            os.environ,
            {
                "OMEGA_SESSION_EXCHANGE_KEY_THROTTLE_RATE": "10/min",
                "OMEGA_SESSION_EXCHANGE_KEY_OWNER_THROTTLE_RATE": "1/min",
            },
        ):
            self.assertEqual(self.key_exchange(key).status_code, 200)
            second = self.personal_key(workspace=self.workspace)
            refused = self.key_exchange(second)
        self.assertEqual(refused.status_code, 429)
        self.assertGreaterEqual(int(refused["Retry-After"]), 1)

    def second_organization(self):
        """Tenant B, where the same person is also a member."""
        org = Organization.objects.create(name="Tenant B")
        admit_for_tests(org)
        workspace = Workspace.no_workspace_objects.create(
            name="B workspace", organization=org, created_by=self.user
        )
        membership = OrganizationMembership.no_workspace_objects.create(
            user=self.user, organization=org, role="Member", level=3
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=self.user,
            workspace=workspace,
            organization_membership=membership,
            role="workspace_member",
            level=3,
        )
        return org, workspace

    def test_wrong_secrets_from_one_address_spend_its_budget_for_the_right_one_too(
        self,
    ):
        # Counted before the secret is checked, per key and address; another address keeps its
        # own budget (test_omega_session_key_limits.py).
        key = self.personal_key(workspace=self.workspace)
        cache.clear()
        with patch.dict(
            os.environ, {"OMEGA_SESSION_EXCHANGE_KEY_THROTTLE_RATE": "3/min"}
        ):
            wrong = [
                self.exchange(
                    headers={
                        "HTTP_X_API_KEY": key.api_key,
                        "HTTP_X_SECRET_KEY": "not-the-secret",
                    }
                ).status_code
                for _ in range(3)
            ]
            right = self.key_exchange(key)
        self.assertEqual(wrong, [401, 401, 401])
        self.assertEqual(right.status_code, 429)
        self.assertGreaterEqual(int(right["Retry-After"]), 1)
        self.assertNotIn("accessToken", right.data)

    def test_a_key_of_a_person_in_two_organizations_never_acts_in_the_other(self):
        org_b, workspace_b = self.second_organization()
        key = self.personal_key(workspace=None)
        for headers in (
            {
                "HTTP_X_ORGANIZATION_ID": str(org_b.pk),
                "HTTP_X_WORKSPACE_ID": str(workspace_b.pk),
            },
            {"HTTP_X_WORKSPACE_ID": str(workspace_b.pk)},
        ):
            with self.subTest(headers=list(headers)):
                self.assertEqual(
                    self.key_exchange(key, headers=headers).status_code, 403
                )
        self.assertEqual(
            self.claims(self.key_exchange(key))["org_id"], str(self.organization.pk)
        )
        # The owner leaves A but stays in B, with B as the remembered organization: the A key
        # must not follow its owner into B.
        OrganizationMembership.no_workspace_objects.filter(
            pk=self.membership.pk
        ).update(is_active=False)
        User.objects.filter(pk=self.user.pk).update(
            config={"currentOrganizationId": str(org_b.pk)}
        )
        cache.clear()
        left = self.key_exchange(key)
        self.assertEqual(left.status_code, 401)
        self.assertNotIn("accessToken", left.data)
        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_active)

    def test_an_unpinned_key_cannot_select_a_workspace_its_owner_cannot_reach(self):
        closed = Workspace.no_workspace_objects.create(
            name="No access", organization=self.organization, created_by=self.user
        )
        key = self.personal_key(workspace=None)
        response = self.key_exchange(
            key, headers={"HTTP_X_WORKSPACE_ID": str(closed.pk)}
        )
        self.assertEqual(response.status_code, 403)

    def test_a_pinned_workspace_that_closes_never_falls_back_to_another(self):
        pinned = Workspace.no_workspace_objects.create(
            name="Pinned", organization=self.organization, created_by=self.user
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=self.user,
            workspace=pinned,
            organization_membership=self.membership,
            role="workspace_member",
            level=3,
        )
        key = self.personal_key(workspace=pinned)
        org_only = {"HTTP_X_ORGANIZATION_ID": str(self.organization.pk)}
        self.assertEqual(
            self.claims(self.key_exchange(key, headers=org_only))["workspace_id"],
            str(pinned.pk),
        )
        Workspace.no_workspace_objects.filter(pk=pinned.pk).update(is_active=False)
        refused = self.key_exchange(key, headers=org_only)
        self.assertEqual(refused.status_code, 401)
        self.assertNotIn("accessToken", refused.data)

    def test_owner_downgrade_deactivation_and_a_deleted_key_each_stop_the_key(self):
        key = self.personal_key(workspace=self.workspace)
        OrganizationMembership.no_workspace_objects.filter(
            pk=self.membership.pk
        ).update(level=1)
        viewer = self.key_exchange(key)
        self.assertEqual(viewer.status_code, 403)
        self.assertEqual(viewer.data["code"], "omega_viewer_not_admitted")
        OrganizationMembership.no_workspace_objects.filter(
            pk=self.membership.pk
        ).update(level=3)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.key_exchange(key).status_code, 401)
        User.objects.filter(pk=self.user.pk).update(is_active=True)
        OrgApiKey.all_objects.filter(pk=key.pk).update(deleted=True)
        self.assertEqual(self.key_exchange(key).status_code, 401)

    def test_another_members_key_acts_as_that_member(self):
        other = User.objects.create_user(
            email="other@example.invalid",
            password="disposable",
            organization=self.organization,
        )
        membership = OrganizationMembership.no_workspace_objects.create(
            user=other, organization=self.organization, role="Member", level=3
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=other,
            workspace=self.workspace,
            organization_membership=membership,
            role="workspace_member",
            level=3,
        )
        key = self.personal_key(user=other, workspace=self.workspace)
        claims = self.claims(self.key_exchange(key))
        self.assertEqual(claims["sub"], str(other.pk))
        self.assertEqual(claims["key_id"], str(key.pk))
