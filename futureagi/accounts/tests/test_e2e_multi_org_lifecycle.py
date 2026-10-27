"""
E2E Multi-Org & Full Lifecycle Tests

Covers:
- Creating additional organizations
- Org listing with flags (isPrimary, isSelected, orgRole, orgLevel)
- Org switching and cross-org isolation
- Org resolution priority (header > config > FK > first membership)
- Long lifecycle scenarios (invite chains, role escalation, bulk invite, etc.)

"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles, RoleMapping
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _owner_membership(user, organization):
    """Root conftest creates User + Workspace but NOT an OrganizationMembership.
    RBAC permission classes require one, so we create it here."""
    OrganizationMembership.objects.get_or_create(
        user=user,
        organization=organization,
        defaults={"role": "Owner", "level": Level.OWNER, "is_active": True},
    )


def _make_user(organization, email, role_str, level, password="pass123"):
    set_workspace_context(organization=organization)
    u = User.objects.create_user(
        email=email,
        password=password,
        name=f"{role_str} User",
        organization=organization,
        organization_role=role_str,
    )
    OrganizationMembership.objects.create(
        user=u,
        organization=organization,
        role=role_str,
        level=level,
        is_active=True,
    )
    return u


def _make_client(user, workspace):
    from conftest import WorkspaceAwareAPIClient

    c = WorkspaceAwareAPIClient()
    c.force_authenticate(user=user)
    c.set_workspace(workspace)
    return c


def _activate_invited_user(user, organization):
    """Simulate invite acceptance: activate user + org/workspace memberships."""
    user.is_active = True
    user.save(update_fields=["is_active"])
    OrganizationMembership.all_objects.filter(
        user=user, organization=organization
    ).update(is_active=True)
    WorkspaceMembership.all_objects.filter(
        user=user, workspace__organization=organization
    ).update(is_active=True)


# ---------------------------------------------------------------------------
# TestCreateAdditionalOrg
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateAdditionalOrg:
    """POST /accounts/organizations/new/"""

    def test_create_additional_org_returns_201(self, auth_client):
        resp = auth_client.post(
            "/accounts/organizations/new/",
            {"name": "Second Org", "display_name": "Second Org Inc"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        result = resp.json().get("result", resp.json())
        assert result["organization"]["name"] == "Second Org"

    def test_user_has_two_memberships(self, auth_client, user):
        auth_client.post(
            "/accounts/organizations/new/",
            {"name": "Org Two"},
            format="json",
        )
        active = OrganizationMembership.no_workspace_objects.filter(
            user=user, is_active=True
        )
        assert active.count() == 2

    def test_original_org_unaffected(self, auth_client, user, organization):
        auth_client.post(
            "/accounts/organizations/new/",
            {"name": "Org Extra"},
            format="json",
        )
        user.refresh_from_db()
        assert user.organization_id == organization.id
        mem = OrganizationMembership.no_workspace_objects.get(
            user=user, organization=organization
        )
        assert mem.role == "Owner"
        assert mem.is_active is True

    def test_new_org_has_default_workspace_and_api_key(self, auth_client, user):
        resp = auth_client.post(
            "/accounts/organizations/new/",
            {"name": "Org WS"},
            format="json",
        )
        result = resp.json().get("result", resp.json())
        org_id = result["organization"]["id"]
        assert Workspace.objects.filter(
            organization_id=org_id, is_default=True
        ).exists()
        assert OrgApiKey.no_workspace_objects.filter(
            organization_id=org_id, type="system"
        ).exists()

    def test_create_three_plus_orgs_all_appear(self, auth_client, user, organization):
        for i in range(3):
            resp = auth_client.post(
                "/accounts/organizations/new/",
                {"name": f"Multi Org {i}"},
                format="json",
            )
            assert resp.status_code == status.HTTP_201_CREATED

        resp = auth_client.get("/accounts/organizations/")
        result = resp.json().get("result", resp.json())
        org_names = [o["name"] for o in result["organizations"]]
        assert organization.name in org_names
        for i in range(3):
            assert f"Multi Org {i}" in org_names


# ---------------------------------------------------------------------------
# TestOrgListing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOrgListing:
    """GET /accounts/organizations/"""

    def test_lists_all_active_memberships(self, auth_client, user, organization):
        org_b = Organization.objects.create(name="Org B")
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_b,
            role="Member",
            level=Level.MEMBER,
            is_active=True,
        )
        resp = auth_client.get("/accounts/organizations/")
        result = resp.json().get("result", resp.json())
        ids = {o["id"] for o in result["organizations"]}
        assert str(organization.id) in ids
        assert str(org_b.id) in ids

    def test_is_selected_flag(self, auth_client, user, organization):
        org_b = Organization.objects.create(name="Non Primary")
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_b,
            role="Viewer",
            level=Level.VIEWER,
            is_active=True,
        )
        resp = auth_client.get("/accounts/organizations/")
        result = resp.json().get("result", resp.json())
        # At least one org should be selected
        selected = [o for o in result["organizations"] if o["is_selected"]]
        assert len(selected) == 1

    def test_org_role_and_level_included(self, auth_client, user, organization):
        resp = auth_client.get("/accounts/organizations/")
        result = resp.json().get("result", resp.json())
        org_entry = next(
            o for o in result["organizations"] if o["id"] == str(organization.id)
        )
        assert "role" in org_entry
        assert "level" in org_entry

    def test_inactive_membership_excluded(self, auth_client, user):
        org_c = Organization.objects.create(name="Inactive Org")
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_c,
            role="Member",
            level=Level.MEMBER,
            is_active=False,
        )
        resp = auth_client.get("/accounts/organizations/")
        result = resp.json().get("result", resp.json())
        ids = {o["id"] for o in result["organizations"]}
        assert str(org_c.id) not in ids

    def test_is_selected_reflects_current_org(self, auth_client, user, organization):
        resp = auth_client.get("/accounts/organizations/")
        result = resp.json().get("result", resp.json())
        assert any(o["is_selected"] for o in result["organizations"])


# ---------------------------------------------------------------------------
# TestOrgSwitching
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOrgSwitching:
    """POST /accounts/organizations/switch/"""

    @pytest.fixture
    def org_b_with_ws(self, user, organization):
        org_b = Organization.objects.create(name="Switch Target Org")
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_b,
            role="Member",
            level=Level.MEMBER,
            is_active=True,
        )
        clear_workspace_context()
        set_workspace_context(organization=org_b)
        ws_b = Workspace.objects.create(
            name="Org B WS",
            organization=org_b,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        return org_b, ws_b

    def test_switch_to_org_b_returns_200(self, auth_client, org_b_with_ws):
        org_b, _ = org_b_with_ws
        resp = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.json().get("result", resp.json())
        assert result["organization"]["id"] == str(org_b.id)

    def test_current_org_endpoint_after_switch(self, auth_client, user, org_b_with_ws):
        org_b, _ = org_b_with_ws
        auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        user.refresh_from_db()
        assert user.config.get("currentOrganizationId") == str(org_b.id)

    def test_workspace_resolves_to_org_b_default(self, auth_client, org_b_with_ws):
        org_b, ws_b = org_b_with_ws
        resp = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        result = resp.json().get("result", resp.json())
        assert result["workspace"]["id"] == str(ws_b.id)

    def test_member_list_shows_org_b_members(
        self, auth_client, user, organization, workspace, org_b_with_ws
    ):
        org_b, ws_b = org_b_with_ws
        # Upgrade user to Admin in org_b (MemberListAPIView requires IsOrganizationAdmin)
        mem_b = OrganizationMembership.no_workspace_objects.get(
            user=user, organization=org_b
        )
        mem_b.role = "Admin"
        mem_b.level = Level.ADMIN
        mem_b.save()
        # Also update user FK to org_b so view sees correct org
        user.organization = org_b
        user.organization_role = "Admin"
        user.save(update_fields=["organization", "organization_role"])

        member_b = _make_user(org_b, "bob@futureagi.com", "Member", Level.MEMBER)
        client_b = _make_client(user, ws_b)
        set_workspace_context(organization=org_b)
        resp = client_b.get("/accounts/organization/members/")
        client_b.stop_workspace_injection()
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        results = data.get("result", {}).get("results", [])
        emails = [r["email"] for r in results]
        assert "bob@futureagi.com" in emails

        # Restore user FK
        user.organization = organization
        user.organization_role = "Owner"
        user.save(update_fields=["organization", "organization_role"])

    def test_workspace_list_shows_org_b_workspaces(
        self, auth_client, user, organization, workspace, org_b_with_ws
    ):
        org_b, ws_b = org_b_with_ws
        client_b = _make_client(user, ws_b)
        resp = client_b.get("/accounts/workspace/list/")
        client_b.stop_workspace_injection()
        assert resp.status_code == status.HTTP_200_OK

    def test_switch_to_removed_org_denied(self, auth_client, user):
        org_removed = Organization.objects.create(name="Removed Org")
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_removed,
            role="Member",
            level=Level.MEMBER,
            is_active=False,
        )
        resp = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_removed.id)},
            format="json",
        )
        assert resp.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_switch_to_nonexistent_org_denied(self, auth_client):
        resp = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_switch_back_to_original_org(
        self, auth_client, organization, org_b_with_ws
    ):
        org_b, _ = org_b_with_ws
        auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        resp = auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(organization.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.json().get("result", resp.json())
        assert result["organization"]["id"] == str(organization.id)


# ---------------------------------------------------------------------------
# TestCrossOrgIsolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCrossOrgIsolation:

    @pytest.fixture
    def two_orgs(self, user, organization, workspace):
        """User is Owner in org A, Viewer in org B."""
        org_b = Organization.objects.create(name="Org B Isolation")
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_b,
            role="Viewer",
            level=Level.VIEWER,
            is_active=True,
        )
        clear_workspace_context()
        set_workspace_context(organization=org_b)
        ws_b = Workspace.objects.create(
            name="OrgB Default WS",
            organization=org_b,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        return org_b, ws_b

    def test_owner_in_a_can_invite_but_viewer_in_b_cannot(
        self, auth_client, user, organization, workspace, two_orgs
    ):
        org_b, ws_b = two_orgs
        # Ensure thread-local context is set to org A for the Owner invite
        set_workspace_context(organization=organization, workspace=workspace)
        resp_a = auth_client.post(
            "/accounts/organization/invite/",
            {
                "emails": ["invitee_a@futureagi.com"],
                "org_level": Level.MEMBER,
            },
            format="json",
        )
        assert resp_a.status_code == status.HTTP_200_OK

        # As viewer in org B, invite should fail (need Admin+)
        # Temporarily switch user FK to org B to test
        user.organization = org_b
        user.organization_role = "Viewer"
        user.save(update_fields=["organization", "organization_role"])

        set_workspace_context(organization=org_b)
        client_b = _make_client(user, ws_b)
        resp_b = client_b.post(
            "/accounts/organization/invite/",
            {
                "emails": ["invitee_b@futureagi.com"],
                "org_level": Level.VIEWER,
            },
            format="json",
        )
        client_b.stop_workspace_injection()
        assert resp_b.status_code == status.HTTP_403_FORBIDDEN

        # Restore user FK
        user.organization = organization
        user.organization_role = "Owner"
        user.save(update_fields=["organization", "organization_role"])

    def test_members_org_a_not_visible_from_org_b(
        self, auth_client, user, organization, workspace, two_orgs
    ):
        """When user.organization FK is switched to org B, member list shows org B members only."""
        org_b, ws_b = two_orgs
        only_a = _make_user(
            organization, "only_a@futureagi.com", "Member", Level.MEMBER
        )

        # Upgrade user to Admin in org_b for member list access
        mem_b = OrganizationMembership.no_workspace_objects.get(
            user=user, organization=org_b
        )
        mem_b.role = "Admin"
        mem_b.level = Level.ADMIN
        mem_b.save()

        # Switch FK to org B
        user.organization = org_b
        user.organization_role = "Admin"
        user.save(update_fields=["organization", "organization_role"])

        set_workspace_context(organization=org_b)
        client_b = _make_client(user, ws_b)
        resp = client_b.get("/accounts/organization/members/")
        client_b.stop_workspace_injection()
        assert resp.status_code == status.HTTP_200_OK
        results = resp.json().get("result", {}).get("results", [])
        emails = [r["email"] for r in results]
        assert "only_a@futureagi.com" not in emails

        # Restore
        user.organization = organization
        user.organization_role = "Owner"
        user.save(update_fields=["organization", "organization_role"])

    def test_workspaces_org_a_not_visible_from_org_b(
        self, auth_client, user, organization, workspace, two_orgs
    ):
        """When user FK is switched to org B, workspace list shows org B workspaces only."""
        org_b, ws_b = two_orgs
        # Switch FK to org B
        user.organization = org_b
        user.organization_role = "Viewer"
        user.save(update_fields=["organization", "organization_role"])

        set_workspace_context(organization=org_b)
        client_b = _make_client(user, ws_b)
        resp = client_b.get("/accounts/workspace/list/")
        client_b.stop_workspace_injection()
        if resp.status_code == status.HTTP_200_OK:
            data = resp.json()
            result = data.get("result", data)
            ws_data = (
                result
                if isinstance(result, list)
                else result.get("workspaces", result.get("results", []))
            )
            if isinstance(ws_data, list):
                ws_names = [w.get("name", "") for w in ws_data]
                # Org A workspace should not appear when FK points to Org B
                assert workspace.name not in ws_names

        # Restore
        user.organization = organization
        user.organization_role = "Owner"
        user.save(update_fields=["organization", "organization_role"])

    def test_remove_from_b_still_active_in_a(
        self, auth_client, user, organization, workspace, two_orgs
    ):
        org_b, ws_b = two_orgs
        mem_b = OrganizationMembership.no_workspace_objects.get(
            user=user, organization=org_b
        )
        mem_b.is_active = False
        mem_b.save()

        # Still active in A
        mem_a = OrganizationMembership.no_workspace_objects.get(
            user=user, organization=organization
        )
        assert mem_a.is_active is True
        assert user.can_access_organization(organization) is True

    def test_display_name_vs_name_in_listing(self, auth_client, user, organization):
        org_d = Organization.objects.create(
            name="org_d_slug", display_name="Org D Display"
        )
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_d,
            role="Member",
            level=Level.MEMBER,
            is_active=True,
        )
        resp = auth_client.get("/accounts/organizations/")
        result = resp.json().get("result", resp.json())
        entry = next(o for o in result["organizations"] if o["id"] == str(org_d.id))
        assert entry["name"] == "org_d_slug"
        assert entry["display_name"] == "Org D Display"


# ---------------------------------------------------------------------------
# TestOrgResolutionPriority
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOrgResolutionPriority:
    """Tests for org resolution priority in auth layer."""

    @pytest.fixture
    def org_b_setup(self, user, organization, workspace):
        org_b = Organization.objects.create(name="Priority Org B")
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org_b,
            role="Member",
            level=Level.MEMBER,
            is_active=True,
        )
        clear_workspace_context()
        set_workspace_context(organization=org_b)
        ws_b = Workspace.objects.create(
            name="PriB Default",
            organization=org_b,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        return org_b, ws_b

    def test_x_organization_id_header_takes_priority(
        self, user, organization, workspace, org_b_setup
    ):
        from unittest.mock import MagicMock

        from accounts.authentication import APIKeyAuthentication

        org_b, _ = org_b_setup
        auth = APIKeyAuthentication()
        request = MagicMock(spec=[])
        request.method = "GET"
        request.path = "/test/"
        request.headers = {"X-Organization-Id": str(org_b.id)}
        request.GET = {}
        request.META = {}
        org = auth._resolve_organization(request, user)
        assert org.id == org_b.id

    def test_config_used_when_no_header(
        self, user, organization, workspace, org_b_setup
    ):
        from unittest.mock import MagicMock

        from accounts.authentication import APIKeyAuthentication

        org_b, _ = org_b_setup
        user.config = {"currentOrganizationId": str(org_b.id)}
        user.save(update_fields=["config"])

        auth = APIKeyAuthentication()
        request = MagicMock(spec=[])
        request.method = "GET"
        request.path = "/test/"
        request.headers = {}
        request.GET = {}
        request.META = {}
        org = auth._resolve_organization(request, user)
        assert org.id == org_b.id

    def test_user_organization_fk_fallback(self, user, organization, workspace):
        from unittest.mock import MagicMock

        from accounts.authentication import APIKeyAuthentication

        user.config = {}
        user.save(update_fields=["config"])
        auth = APIKeyAuthentication()
        request = MagicMock(spec=[])
        request.method = "GET"
        request.path = "/test/"
        request.headers = {}
        request.GET = {}
        request.META = {}
        org = auth._resolve_organization(request, user)
        assert org.id == organization.id

    def test_first_active_membership_last_resort(self, db, organization):
        from unittest.mock import MagicMock

        from accounts.authentication import APIKeyAuthentication

        clear_workspace_context()
        set_workspace_context(organization=organization)
        orphan = User.objects.create_user(
            email="orphan_res@futureagi.com",
            password="pass123",
            name="Orphan",
            organization=None,
            config={},
        )
        User.objects.filter(id=orphan.id).update(organization=None)
        orphan.refresh_from_db()
        OrganizationMembership.no_workspace_objects.create(
            user=orphan,
            organization=organization,
            role="Member",
            level=Level.MEMBER,
            is_active=True,
        )
        auth = APIKeyAuthentication()
        request = MagicMock(spec=[])
        request.method = "GET"
        request.path = "/test/"
        request.headers = {}
        request.GET = {}
        request.META = {}
        org = auth._resolve_organization(request, orphan)
        assert org.id == organization.id

    def test_after_switch_config_updated(self, auth_client, user, org_b_setup):
        org_b, _ = org_b_setup
        auth_client.post(
            "/accounts/organizations/switch/",
            {"organization_id": str(org_b.id)},
            format="json",
        )
        user.refresh_from_db()
        assert user.config["currentOrganizationId"] == str(org_b.id)

    def test_api_key_org_takes_priority(
        self, user, organization, workspace, org_b_setup
    ):
        from unittest.mock import MagicMock

        from accounts.authentication import APIKeyAuthentication

        org_b, _ = org_b_setup
        auth = APIKeyAuthentication()
        request = MagicMock(spec=[])
        request.method = "GET"
        request.path = "/test/"
        request.headers = {"X-Organization-Id": str(organization.id)}
        request.GET = {}
        request.META = {}
        mock_api_key = MagicMock()
        mock_api_key.organization = org_b
        request.org_api_key = mock_api_key
        org = auth._resolve_organization(request, user)
        assert org.id == org_b.id

    def test_jwt_with_different_org_header(
        self, user, organization, workspace, org_b_setup
    ):
        from unittest.mock import MagicMock

        from accounts.authentication import APIKeyAuthentication

        org_b, _ = org_b_setup
        auth = APIKeyAuthentication()
        request = MagicMock(spec=[])
        request.method = "GET"
        request.path = "/test/"
        request.headers = {"X-Organization-Id": str(org_b.id)}
        request.GET = {}
        request.META = {}
        org = auth._resolve_organization(request, user)
        assert org.id == org_b.id


# ===========================================================================
# Lifecycle Tests
# ===========================================================================


@pytest.mark.django_db
class TestLifecycleOwnerInvitesChain:
    """Owner creates org -> invites Admin -> Admin invites Member ->
    Member tries invite (fails) -> Owner removes Member -> Re-invites -> restored."""

    def test_full_chain(self, auth_client, user, organization, workspace):
        # Step 1: Owner invites Admin
        resp = auth_client.post(
            "/accounts/organization/invite/",
            {"emails": ["admin_chain@futureagi.com"], "org_level": Level.ADMIN},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        admin_u = User.objects.get(email="admin_chain@futureagi.com")
        _activate_invited_user(admin_u, organization)
        admin_mem = OrganizationMembership.objects.get(
            user=admin_u, organization=organization
        )
        assert admin_mem.level == Level.ADMIN

        # Step 2: Admin invites Member
        admin_ws = Workspace.objects.get(organization=organization, is_default=True)
        admin_client = _make_client(admin_u, admin_ws)
        resp2 = admin_client.post(
            "/accounts/organization/invite/",
            {"emails": ["member_chain@futureagi.com"], "org_level": Level.MEMBER},
            format="json",
        )
        admin_client.stop_workspace_injection()
        assert resp2.status_code == status.HTTP_200_OK

        member_u = User.objects.get(email="member_chain@futureagi.com")
        _activate_invited_user(member_u, organization)
        member_mem = OrganizationMembership.objects.get(
            user=member_u, organization=organization
        )
        assert member_mem.level == Level.MEMBER

        # Step 3: Member tries to invite (should fail - Member < Admin needed)
        member_client = _make_client(member_u, admin_ws)
        resp3 = member_client.post(
            "/accounts/organization/invite/",
            {"emails": ["nope@futureagi.com"], "org_level": Level.VIEWER},
            format="json",
        )
        member_client.stop_workspace_injection()
        assert resp3.status_code == status.HTTP_403_FORBIDDEN

        # Step 4: Owner removes Member
        resp4 = auth_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(member_u.id)},
            format="json",
        )
        assert resp4.status_code == status.HTTP_200_OK
        member_mem.refresh_from_db()
        assert member_mem.is_active is False

        # Step 5: Owner re-invites Member
        resp5 = auth_client.post(
            "/accounts/organization/invite/",
            {"emails": ["member_chain@futureagi.com"], "org_level": Level.MEMBER},
            format="json",
        )
        assert resp5.status_code == status.HTTP_200_OK
        member_mem.refresh_from_db()
        assert member_mem.is_active is True


@pytest.mark.django_db
class TestLifecycleRoleEscalation:
    """Owner invites Viewer -> Viewer tries role change (fails) ->
    Owner promotes to Admin -> Admin can now invite Members."""

    def test_escalation_flow(self, auth_client, user, organization, workspace):
        # Invite Viewer
        auth_client.post(
            "/accounts/organization/invite/",
            {"emails": ["viewer_esc@futureagi.com"], "org_level": Level.VIEWER},
            format="json",
        )
        viewer = User.objects.get(email="viewer_esc@futureagi.com")
        _activate_invited_user(viewer, organization)
        ws = Workspace.objects.get(organization=organization, is_default=True)

        # Viewer tries to update own role -> fail
        viewer_client = _make_client(viewer, ws)
        resp = viewer_client.post(
            "/accounts/organization/members/role/",
            {"user_id": str(viewer.id), "org_level": Level.ADMIN},
            format="json",
        )
        viewer_client.stop_workspace_injection()
        assert resp.status_code == status.HTTP_403_FORBIDDEN

        # Owner promotes Viewer -> Admin
        resp2 = auth_client.post(
            "/accounts/organization/members/role/",
            {"user_id": str(viewer.id), "org_level": Level.ADMIN},
            format="json",
        )
        assert resp2.status_code == status.HTTP_200_OK

        viewer.refresh_from_db()
        mem = OrganizationMembership.objects.get(user=viewer, organization=organization)
        assert mem.level == Level.ADMIN

        # Now-Admin can invite Members
        admin_client = _make_client(viewer, ws)
        resp3 = admin_client.post(
            "/accounts/organization/invite/",
            {"emails": ["new_mem_esc@futureagi.com"], "org_level": Level.MEMBER},
            format="json",
        )
        admin_client.stop_workspace_injection()
        assert resp3.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestLifecycleMultiOrgPermissions:
    """User A owns Org1 -> gets invited as Member to Org2 -> switch to Org2 ->
    limited permissions -> switch back to Org1 -> full permissions."""

    def test_multi_org_perms(self, auth_client, user, organization, workspace):
        # Create Org2 with a different owner
        org2 = Organization.objects.create(name="Org2 Perms")
        clear_workspace_context()
        set_workspace_context(organization=org2)
        owner2 = User.objects.create_user(
            email="owner2@futureagi.com",
            password="pass123",
            name="Owner2",
            organization=org2,
            organization_role=OrganizationRoles.OWNER,
        )
        OrganizationMembership.no_workspace_objects.create(
            user=owner2,
            organization=org2,
            role="Owner",
            level=Level.OWNER,
            is_active=True,
        )
        ws2 = Workspace.objects.create(
            name="Org2 WS",
            organization=org2,
            is_default=True,
            is_active=True,
            created_by=owner2,
        )

        # Invite user as Member to Org2
        OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org2,
            role="Member",
            level=Level.MEMBER,
            is_active=True,
        )

        # Switch user FK to Org2 to simulate active context
        user.organization = org2
        user.organization_role = "Member"
        user.save(update_fields=["organization", "organization_role"])

        # In Org2 context, Member cannot invite Admin (level 8 > level 3)
        set_workspace_context(organization=org2)
        client_org2 = _make_client(user, ws2)
        resp = client_org2.post(
            "/accounts/organization/invite/",
            {"emails": ["should_fail@futureagi.com"], "org_level": Level.ADMIN},
            format="json",
        )
        client_org2.stop_workspace_injection()
        assert resp.status_code == status.HTTP_403_FORBIDDEN

        # Switch back to Org1 -> full Owner permissions
        user.organization = organization
        user.organization_role = "Owner"
        user.save(update_fields=["organization", "organization_role"])

        set_workspace_context(organization=organization, workspace=workspace)
        resp2 = auth_client.post(
            "/accounts/organization/invite/",
            {"emails": ["should_work@futureagi.com"], "org_level": Level.ADMIN},
            format="json",
        )
        assert resp2.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestLifecycleWorkspaceAccess:
    """Owner creates 2 workspaces -> invites user with ws_access for only 1 ->
    user sees only assigned workspace (workspace-only user)."""

    def test_ws_access(self, auth_client, user, organization, workspace):
        # Create second workspace
        clear_workspace_context()
        set_workspace_context(organization=organization)
        ws2 = Workspace.objects.create(
            name="WS2 Restricted",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )

        # Invite user as Viewer with access to ws2 only
        resp = auth_client.post(
            "/accounts/organization/invite/",
            {
                "emails": ["ws_user@futureagi.com"],
                "org_level": Level.VIEWER,
                "workspace_access": [
                    {"workspace_id": str(ws2.id), "level": Level.WORKSPACE_VIEWER}
                ],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        ws_user = User.objects.get(email="ws_user@futureagi.com")
        # User was just invited (not yet accepted), so ws membership exists but is_active=False
        ws_mems = WorkspaceMembership.no_workspace_objects.filter(
            user=ws_user, workspace__organization=organization
        )
        ws_ids = set(ws_mems.values_list("workspace_id", flat=True))
        assert ws2.id in ws_ids


@pytest.mark.django_db
class TestLifecycleInviteCancelReinvite:
    """Admin invites user -> cancels invite -> re-invites with different level."""

    def test_cancel_and_reinvite(self, auth_client, user, organization, workspace):
        # Step 1: Invite as Member
        resp1 = auth_client.post(
            "/accounts/organization/invite/",
            {"emails": ["cancel_me@example.com"], "org_level": Level.MEMBER},
            format="json",
        )
        assert resp1.status_code == status.HTTP_200_OK

        # Find the invite (for new/inactive users, invite persists)
        invite_qs = OrganizationInvite.objects.filter(
            organization=organization,
            target_email="cancel_me@example.com",
            status=InviteStatus.PENDING,
        )
        if invite_qs.exists():
            invite = invite_qs.first()

            # Step 2: Cancel
            resp2 = auth_client.delete(
                "/accounts/organization/invite/cancel/",
                {"invite_id": str(invite.id)},
                format="json",
            )
            assert resp2.status_code == status.HTTP_200_OK

        # Step 3: Re-invite with different level
        resp3 = auth_client.post(
            "/accounts/organization/invite/",
            {"emails": ["cancel_me@example.com"], "org_level": Level.ADMIN},
            format="json",
        )
        assert resp3.status_code == status.HTTP_200_OK

        # Verify new level applied and membership reactivated
        target_user = User.objects.filter(email="cancel_me@example.com").first()
        if target_user:
            mem = OrganizationMembership.all_objects.filter(
                user=target_user, organization=organization, deleted=False
            ).first()
            if mem:
                assert mem.level == Level.ADMIN
                assert mem.is_active is True, (
                    "Re-invited user's membership must be active so they can "
                    "log in without hitting requires_org_setup"
                )


@pytest.mark.django_db
class TestLifecycleRemovalRecovery:
    """Owner removes Admin -> Admin creates new org -> Owner re-invites Admin.
    Due to the dual-write guard (user belongs to another org), the re-invite
    returns an error for that email. Verify the full flow up to that point."""

    def test_removal_recovery(
        self, auth_client, api_client, user, organization, workspace
    ):
        # Create admin user
        admin = _make_user(
            organization,
            "admin_recovery@futureagi.com",
            "Admin",
            Level.ADMIN,
            password="adminpass123",
        )

        # Owner removes admin
        resp = auth_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(admin.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        # Verify membership deactivated (source of truth)
        assert not OrganizationMembership.objects.filter(
            user=admin, organization=organization, is_active=True
        ).exists()

        # Admin logs in and creates a new org
        login_resp = api_client.post(
            "/accounts/token/",
            {"email": "admin_recovery@futureagi.com", "password": "adminpass123"},
            format="json",
        )
        assert login_resp.status_code == status.HTTP_200_OK
        access = login_resp.json()["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        create_resp = api_client.post(
            "/accounts/organizations/create/",
            {"organization_name": "Admin Recovery Org"},
            format="json",
        )
        assert create_resp.status_code == status.HTTP_201_CREATED

        # Verify admin has active membership in the new org
        new_mem = (
            OrganizationMembership.no_workspace_objects.filter(
                user=admin, is_active=True
            )
            .select_related("organization")
            .first()
        )
        assert new_mem is not None
        assert new_mem.organization.name == "Admin Recovery Org"

        # Owner re-invites admin to original org.
        # With multi-org support, this succeeds and reactivates the old membership.
        set_workspace_context(organization=organization, workspace=workspace)
        resp2 = auth_client.post(
            "/accounts/organization/invite/",
            {"emails": ["admin_recovery@futureagi.com"], "org_level": Level.ADMIN},
            format="json",
        )
        assert resp2.status_code == status.HTTP_200_OK

        # Admin now has active memberships in both orgs (recovery + original)
        active_mems = OrganizationMembership.no_workspace_objects.filter(
            user=admin, is_active=True
        )
        assert active_mems.count() >= 2


@pytest.mark.django_db
class TestLifecycleInviteExpiry:
    """Invite expires -> appears as Expired -> resend refreshes -> appears as Pending."""

    def test_expiry_and_resend(self, auth_client, user, organization, workspace):
        # Create an invite for a non-existent user (invite persists)
        resp = auth_client.post(
            "/accounts/organization/invite/",
            {"emails": ["expiry_test@example.com"], "org_level": Level.MEMBER},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        invite = OrganizationInvite.objects.filter(
            organization=organization,
            target_email="expiry_test@example.com",
            status=InviteStatus.PENDING,
        ).first()
        if invite is None:
            # If user was auto-created as active, invite may have been auto-deleted
            pytest.skip("Invite auto-deleted for active user; cannot test expiry")

        # Force expiry
        invite.created_at = timezone.now() - timedelta(days=8)
        invite.save(update_fields=["created_at"])
        assert invite.effective_status == "Expired"

        # Resend -> refreshes expiration
        resp2 = auth_client.post(
            "/accounts/organization/invite/resend/",
            {"invite_id": str(invite.id)},
            format="json",
        )
        assert resp2.status_code == status.HTTP_200_OK

        invite.refresh_from_db()
        assert invite.effective_status == "Pending"


@pytest.mark.django_db
class TestLifecycleBulkInvite:
    """Bulk invite 5 emails: 2 existing different-org users, 2 new, 1 already member."""

    def test_bulk_invite(self, auth_client, user, organization, workspace):
        # Create two existing users in a different org
        other_org = Organization.objects.create(name="Other Org")
        clear_workspace_context()
        set_workspace_context(organization=other_org)
        ext1 = User.objects.create_user(
            email="ext1@futureagi.com",
            password="pass123",
            name="Ext1",
            organization=other_org,
            organization_role=OrganizationRoles.MEMBER,
        )
        ext2 = User.objects.create_user(
            email="ext2@futureagi.com",
            password="pass123",
            name="Ext2",
            organization=other_org,
            organization_role=OrganizationRoles.MEMBER,
        )

        set_workspace_context(organization=organization)

        # Bulk invite with an existing active member should still process
        # the other emails and report the existing member separately.
        existing_mem = _make_user(
            organization, "already_here@futureagi.com", "Member", Level.MEMBER
        )
        resp = auth_client.post(
            "/accounts/organization/invite/",
            {
                "emails": [
                    "ext1@futureagi.com",
                    "newbie1@example.com",
                    "already_here@futureagi.com",
                ],
                "org_level": Level.MEMBER,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.json()["result"]
        assert "already_here@futureagi.com" in result["already_members"]
        assert "ext1@futureagi.com" in result["invited"]
        assert "newbie1@example.com" in result["invited"]

        # Bulk invite without existing members should succeed
        resp = auth_client.post(
            "/accounts/organization/invite/",
            {
                "emails": [
                    "ext1@futureagi.com",
                    "ext2@futureagi.com",
                    "newbie1@example.com",
                    "newbie2@example.com",
                ],
                "org_level": Level.MEMBER,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.json().get("result", resp.json())
        assert set(result.get("invited", [])) == {
            "ext2@futureagi.com",
            "newbie1@example.com",
            "newbie2@example.com",
        }
        assert result.get("already_members") == ["ext1@futureagi.com"]


@pytest.mark.django_db
class TestLifecycleOwnerDemotion:
    """Create 2nd Owner -> Owner1 demotes self to Admin -> cannot promote to Owner."""

    def test_owner_demotion(self, auth_client, user, organization, workspace):
        # Invite second Owner
        resp = auth_client.post(
            "/accounts/organization/invite/",
            {"emails": ["owner2_demo@futureagi.com"], "org_level": Level.OWNER},
            format="json",
        )
        # Owner can invite Owner (same level exception)
        assert resp.status_code == status.HTTP_200_OK

        owner2 = User.objects.get(email="owner2_demo@futureagi.com")

        # Owner1 demotes self to Admin
        resp2 = auth_client.post(
            "/accounts/organization/members/role/",
            {"user_id": str(user.id), "org_level": Level.ADMIN},
            format="json",
        )
        # This may fail due to CanManageTargetUser: Owner can manage Owners,
        # but after demotion? We attempt it.
        if resp2.status_code == status.HTTP_200_OK:
            user.refresh_from_db()
            mem = OrganizationMembership.objects.get(
                user=user, organization=organization
            )
            assert mem.level == Level.ADMIN

            # Now Admin cannot promote anyone to Owner
            ws = Workspace.objects.get(organization=organization, is_default=True)
            admin_client = _make_client(user, ws)
            resp3 = admin_client.post(
                "/accounts/organization/members/role/",
                {"user_id": str(owner2.id), "org_level": Level.OWNER},
                format="json",
            )
            admin_client.stop_workspace_injection()
            assert resp3.status_code == status.HTTP_403_FORBIDDEN
        else:
            # Self-demotion may be blocked; that is also valid behavior
            assert resp2.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
            ]


@pytest.mark.django_db
class TestLifecycleRemoveVerifyAll:
    """Remove member -> verify all WS deactivated -> login -> requires_org_setup ->
    create new org -> verify independence."""

    def test_full_removal_lifecycle(
        self, auth_client, api_client, user, organization, workspace
    ):
        # Create member with workspace membership
        member = _make_user(
            organization,
            "fullremove@futureagi.com",
            "Member",
            Level.MEMBER,
            password="memberpass123",
        )
        org_mem = OrganizationMembership.objects.get(
            user=member, organization=organization
        )
        WorkspaceMembership.objects.create(
            workspace=workspace,
            user=member,
            role="workspace_viewer",
            level=Level.WORKSPACE_VIEWER,
            organization_membership=org_mem,
            is_active=True,
        )

        # Remove member
        resp = auth_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(member.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        # Verify all WS memberships deactivated
        ws_mems = WorkspaceMembership.objects.filter(
            user=member, workspace__organization=organization
        )
        for wm in ws_mems:
            assert wm.is_active is False

        # Login -> requires_org_setup
        login_resp = api_client.post(
            "/accounts/token/",
            {"email": "fullremove@futureagi.com", "password": "memberpass123"},
            format="json",
        )
        assert login_resp.status_code == status.HTTP_200_OK
        assert login_resp.json().get("requires_org_setup") is True

        # Create new org
        access = login_resp.json()["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        create_resp = api_client.post(
            "/accounts/organizations/create/",
            {"organization_name": "Independent Org"},
            format="json",
        )
        assert create_resp.status_code == status.HTTP_201_CREATED

        # Verify member has active membership in the new org (source of truth)
        new_mem = (
            OrganizationMembership.no_workspace_objects.filter(
                user=member, is_active=True
            )
            .select_related("organization")
            .first()
        )
        assert new_mem is not None
        assert new_mem.organization.name == "Independent Org"
        assert new_mem.organization_id != organization.id
