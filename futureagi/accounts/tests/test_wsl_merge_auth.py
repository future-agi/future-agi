"""N01/N02: the signed org and read-only landing contract compose together."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from accounts.authentication import APIKeyAuthentication
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests.test_workspace_landing import landing as _landing_fixture
from accounts.tests.test_workspace_landing import org_member
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    workspace_scope_is_empty,
)

landing = _landing_fixture


@pytest.fixture(autouse=True)
def clean_context():
    clear_workspace_context()
    yield
    clear_workspace_context()


def snapshot():
    return {
        model.__name__: list(model._base_manager.order_by("pk").values())
        for model in (
            Workspace,
            WorkspaceMembership,
            OrganizationMembership,
            OrgApiKey,
        )
    }


def request_for(org, *, bound=True, headers=None):
    request = SimpleNamespace(
        headers=headers or {}, GET={}, method="GET", path="/accounts/user/"
    )
    if bound:
        request.auth_scope = SimpleNamespace(org_id=org.pk)
    return request


def test_bound_landing_prefers_fresh_accessible_workspace_only_in_signed_org(landing):
    # Both the parent's UUID-first and earliest-join fallback pick this third row,
    # never the saved preference. Keep it accessible through the entire scenario.
    earliest = Workspace.objects.create(
        id=UUID(int=0),
        name="Earliest accessible",
        organization=landing.org,
        created_by=landing.owner,
    )
    earliest_membership = WorkspaceMembership.no_workspace_objects.create(
        id=UUID(int=0),
        user=landing.user,
        workspace=earliest,
        organization_membership=landing.other_mem.organization_membership,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
    )
    WorkspaceMembership._base_manager.filter(pk=earliest_membership.pk).update(
        created_at=timezone.now() - timedelta(days=2),
    )
    assert landing.user.can_access_workspace(earliest)
    other_org = Organization.objects.create(name="Other signed organization")
    OrganizationMembership.no_workspace_objects.create(
        user=landing.user,
        organization=other_org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
    )
    foreign = Workspace.objects.create(
        name="Foreign preference", organization=other_org, created_by=landing.owner
    )
    membership = OrganizationMembership.no_workspace_objects.get(
        user=landing.user, organization=other_org
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=landing.user,
        workspace=foreign,
        organization_membership=membership,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
    )
    # Deliberately keep the in-memory user stale while the persisted preference changes.
    landing.user.config = {
        "currentOrganizationId": str(other_org.pk),
        "defaultWorkspaceId": str(foreign.pk),
    }
    User._base_manager.filter(pk=landing.user.pk).update(
        config={
            "orgWorkspaceMap": {str(landing.org.pk): str(landing.other.pk)},
            "defaultWorkspaceId": str(foreign.pk),
        }
    )
    before = snapshot()
    auth = APIKeyAuthentication()
    request = request_for(landing.org)
    auth._set_workspace_context(request, landing.user)
    assert request.organization == landing.org
    assert request.workspace == landing.other
    assert snapshot() == before
    clear_workspace_context()
    WorkspaceMembership._base_manager.filter(pk=landing.other_mem.pk).update(
        is_active=False
    )
    User._base_manager.filter(pk=landing.user.pk).update(
        config={
            "orgWorkspaceMap": {str(landing.org.pk): str(foreign.pk)},
            "currentWorkspaceId": str(foreign.pk),
            "defaultWorkspaceId": str(foreign.pk),
        }
    )
    before = snapshot()
    request = request_for(landing.org)
    auth._set_workspace_context(request, landing.user)
    assert request.organization == landing.org
    assert request.workspace == landing.default
    assert snapshot() == before
    for headers in (
        {"X-Organization-Id": str(other_org.pk)},
        {"X-Workspace-Id": str(foreign.pk)},
    ):
        clear_workspace_context()
        with pytest.raises(PermissionDenied):
            auth._set_workspace_context(
                request_for(landing.org, headers=headers), landing.user
            )
        assert snapshot() == before


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("state", ["absent", "deactivated", "deleted"])
@pytest.mark.parametrize(
    "role,level",
    [
        (OrganizationRoles.MEMBER, Level.MEMBER),
        (OrganizationRoles.MEMBER_VIEW_ONLY, Level.VIEWER),
        (OrganizationRoles.OWNER, Level.OWNER),
        (OrganizationRoles.ADMIN, Level.ADMIN),
    ],
)
def test_authentication_never_provisions_or_reactivates_default_access(
    db, bound, state, role, level
):
    org = Organization.objects.create(name="No accessible landing")
    user, membership = org_member(org, "unprovisioned-merge@example.test", role, level)
    if state != "absent":
        workspace = Workspace.objects.create(
            name="Inactive default",
            organization=org,
            created_by=user,
            is_default=True,
            is_active=False,
        )
        row = WorkspaceMembership.no_workspace_objects.create(
            user=user,
            workspace=workspace,
            organization_membership=membership,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=False,
        )
        if state == "deleted":
            WorkspaceMembership._base_manager.filter(pk=row.pk).update(deleted=True)
    before = snapshot()
    request = request_for(org, bound=bound)
    if bound:
        with pytest.raises(PermissionDenied) as refused:
            APIKeyAuthentication()._set_workspace_context(request, user)
        assert refused.value.get_codes() == "workspace_required"
    else:
        APIKeyAuthentication()._set_workspace_context(request, user)
    assert request.organization == org
    assert request.workspace is None
    assert workspace_scope_is_empty()
    assert snapshot() == before


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("history", ["legacy", "inactive-org", "deleted-org"])
def test_legacy_and_former_org_landing_never_grants_access(db, bound, history):
    org = Organization.objects.create(name="Historical organization")
    user = User.objects.create_user(
        email="historical-landing@example.test",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
        config={},
    )
    workspace = Workspace.objects.create(
        name="Historical default",
        organization=org,
        created_by=user,
        is_default=True,
    )
    if history != "legacy":
        membership = OrganizationMembership.no_workspace_objects.create(
            user=user,
            organization=org,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=user,
            workspace=workspace,
            organization_membership=membership,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
        )
        OrganizationMembership._base_manager.filter(pk=membership.pk).update(
            **({"deleted": True} if history == "deleted-org" else {"is_active": False}),
        )
    else:
        assert not OrganizationMembership._base_manager.filter(user=user).exists()
    User._base_manager.filter(pk=user.pk).update(
        config={
            "currentOrganizationId": str(org.pk),
            "defaultWorkspaceId": str(workspace.pk),
            "orgWorkspaceMap": {str(org.pk): str(workspace.pk)},
        }
    )
    assert not user.can_access_workspace(workspace)
    before = snapshot()
    request = request_for(org, bound=bound)
    if bound:
        with pytest.raises(PermissionDenied) as refused:
            APIKeyAuthentication()._set_workspace_context(request, user)
        assert refused.value.get_codes() == "workspace_required"
        assert request.organization == org
    else:
        APIKeyAuthentication()._set_workspace_context(request, user)
        assert request.organization == (org if history == "legacy" else None)
    assert request.workspace is None
    assert workspace_scope_is_empty()
    assert snapshot() == before
