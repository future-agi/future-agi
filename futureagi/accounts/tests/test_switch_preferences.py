"""Switches merge preferences from storage, not a bearer-cached User snapshot."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.views.organization_selection import SwitchOrganizationView
from accounts.views.workspace_management import SwitchWorkspaceAPIView
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context


@pytest.fixture
def selections(db):
    clear_workspace_context()
    org_a = Organization.objects.create(name="Preference Alpha")
    org_b = Organization.objects.create(name="Preference Beta")
    user = User.objects.create_user(
        email="switch-preferences@example.test",
        password="test-password",
        name="Preference User",
        organization=org_a,
        organization_role=OrganizationRoles.OWNER,
        config={},
    )
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_a,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
    )
    membership_b = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_b,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
    )
    a1 = Workspace.objects.create(
        name="Alpha Default", organization=org_a, created_by=user, is_default=True
    )
    a2 = Workspace.objects.create(
        name="Alpha Other", organization=org_a, created_by=user
    )
    b1 = Workspace.objects.create(
        name="Beta Default", organization=org_b, created_by=user, is_default=True
    )
    workspace_membership_b = WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=b1,
        organization_membership=membership_b,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
    )
    user.config = {
        "currentOrganizationId": str(org_a.pk),
        "selected_organization_id": str(org_a.pk),
        "currentWorkspaceId": str(a1.pk),
        "defaultWorkspaceId": str(a1.pk),
        "orgWorkspaceMap": {str(org_a.pk): str(a1.pk), str(org_b.pk): str(b1.pk)},
        "theme": "light",
    }
    user.save(update_fields=["config"])
    # Each cache hit returns a separate copy of this pre-switch snapshot.
    cached_user = User.objects.select_related("organization").get(pk=user.pk)
    yield SimpleNamespace(
        user=user,
        cached_user=cached_user,
        org_a=org_a,
        org_b=org_b,
        a1=a1,
        a2=a2,
        b1=b1,
        workspace_membership_b=workspace_membership_b,
    )
    clear_workspace_context()


def post_selection(view, path, payload, user, organization):
    request = APIRequestFactory().post(
        path, payload, format="json", HTTP_X_ORGANIZATION_ID=str(organization.pk)
    )
    request.organization = organization
    force_authenticate(request, user=user)
    response = view.as_view()(request)
    assert response.status_code == 200, response.data
    assert request.organization == organization
    return response.data.get("result", response.data)


def switch_workspace(selections, user):
    return post_selection(
        SwitchWorkspaceAPIView,
        "/accounts/workspace/switch/",
        {"new_workspace_id": str(selections.a2.pk)},
        user,
        selections.org_a,
    )


def switch_organization(user, source, target):
    return post_selection(
        SwitchOrganizationView,
        "/accounts/organizations/switch/",
        {"organization_id": str(target.pk)},
        user,
        source,
    )


@pytest.mark.parametrize("cached", [False, True], ids=["fresh-user", "cached-user"])
def test_workspace_preference_survives_organization_round_trip(selections, cached):
    def request_user():
        if cached:
            return deepcopy(selections.cached_user)
        return User.objects.select_related("organization").get(pk=selections.user.pk)

    switch_workspace(selections, request_user())
    selections.user.refresh_from_db()
    assert selections.user.config["orgWorkspaceMap"][str(selections.org_a.pk)] == str(
        selections.a2.pk
    )

    result = switch_organization(request_user(), selections.org_a, selections.org_b)
    assert result["workspace"]["id"] == str(selections.b1.pk)
    result = switch_organization(request_user(), selections.org_b, selections.org_a)
    assert result["workspace"]["id"] == str(selections.a2.pk)
    selections.user.refresh_from_db()
    assert selections.user.config == {
        **selections.cached_user.config,
        "currentWorkspaceId": str(selections.a2.pk),
        "defaultWorkspaceId": str(selections.a2.pk),
        "orgWorkspaceMap": {
            str(selections.org_a.pk): str(selections.a2.pk),
            str(selections.org_b.pk): str(selections.b1.pk),
        },
    }


@pytest.mark.parametrize("has_workspace", [True, False])
def test_organization_switch_preserves_newer_saved_preferences(
    selections, has_workspace
):
    if not has_workspace:
        selections.workspace_membership_b.is_active = False
        selections.workspace_membership_b.save(update_fields=["is_active"])
    saved = deepcopy(selections.cached_user.config)
    saved.update(
        theme="dark",
        notificationPreferences={"email": False},
        currentWorkspaceId=str(selections.a2.pk),
        defaultWorkspaceId=str(selections.a2.pk),
    )
    saved["orgWorkspaceMap"][str(selections.org_a.pk)] = str(selections.a2.pk)
    User.objects.filter(pk=selections.user.pk).update(config=saved)

    result = switch_organization(
        deepcopy(selections.cached_user), selections.org_a, selections.org_b
    )
    expected = deepcopy(saved)
    expected.update(
        currentOrganizationId=str(selections.org_b.pk),
        selected_organization_id=str(selections.org_b.pk),
    )
    if has_workspace:
        assert result["workspace"]["id"] == str(selections.b1.pk)
        expected["currentWorkspaceId"] = str(selections.b1.pk)
    else:
        assert "workspace" not in result
    selections.user.refresh_from_db()
    assert selections.user.config == expected


def test_workspace_switch_preserves_newer_organization_and_preferences(selections):
    saved = deepcopy(selections.cached_user.config)
    saved.update(
        currentOrganizationId=str(selections.org_b.pk),
        selected_organization_id=str(selections.org_b.pk),
        currentWorkspaceId=str(selections.b1.pk),
        theme="dark",
        notificationPreferences={"email": False},
    )
    User.objects.filter(pk=selections.user.pk).update(config=saved)

    result = switch_workspace(selections, deepcopy(selections.cached_user))
    assert result["workspace"]["id"] == str(selections.a2.pk)
    expected = deepcopy(saved)
    expected.update(
        currentWorkspaceId=str(selections.a2.pk),
        defaultWorkspaceId=str(selections.a2.pk),
    )
    expected["orgWorkspaceMap"][str(selections.org_a.pk)] = str(selections.a2.pk)
    selections.user.refresh_from_db()
    assert selections.user.config == expected


@pytest.mark.parametrize("switch", ["organization", "workspace"])
def test_switch_initializes_empty_preferences(selections, switch):
    User.objects.filter(pk=selections.user.pk).update(config={})
    user = User.objects.select_related("organization").get(pk=selections.user.pk)
    if switch == "organization":
        switch_organization(user, selections.org_a, selections.org_b)
        expected = {
            "currentOrganizationId": str(selections.org_b.pk),
            "selected_organization_id": str(selections.org_b.pk),
            "currentWorkspaceId": str(selections.b1.pk),
            "orgWorkspaceMap": {str(selections.org_b.pk): str(selections.b1.pk)},
        }
    else:
        switch_workspace(selections, user)
        expected = {
            "currentWorkspaceId": str(selections.a2.pk),
            "defaultWorkspaceId": str(selections.a2.pk),
            "orgWorkspaceMap": {str(selections.org_a.pk): str(selections.a2.pk)},
        }
    selections.user.refresh_from_db()
    assert selections.user.config == expected
    assert user.config == expected
