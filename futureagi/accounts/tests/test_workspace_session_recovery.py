"""Focused session-navigation contracts; collaborators are mocked, no services."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts import authentication
from accounts.models.organization_membership import OrganizationMembership
from accounts.views import organization_views, user as user_views


@pytest.fixture
def selected(monkeypatch):
    workspace = SimpleNamespace(pk=UUID(int=1), id=UUID(int=1))
    user = SimpleNamespace(can_access_workspace=Mock(return_value=False))
    monkeypatch.setattr(authentication.Workspace, "objects", Mock(get=Mock(return_value=workspace)))
    monkeypatch.setattr(authentication.WorkspaceMembership, "no_workspace_objects", Mock())
    authentication.WorkspaceMembership.no_workspace_objects.filter.return_value.exists.return_value = True
    organization = SimpleNamespace(id=UUID(int=2))
    return user, workspace, organization


def request_for(workspace, method="GET", route="dataset_detail"):
    return SimpleNamespace(
        method=method, headers={"X-Workspace-Id": str(workspace.pk)}, GET={},
        resolver_match=SimpleNamespace(url_name=route, func=SimpleNamespace(cls=None)),
    )


def test_removed_membership_read_reports_recoverable_selector(selected):
    user, workspace, organization = selected
    with pytest.raises(PermissionDenied) as denied:
        authentication.APIKeyAuthentication()._get_requested_workspace(request_for(workspace), user, organization)
    assert denied.value.get_codes() == "workspace_unavailable"


def test_workspace_list_can_recover_from_removed_membership(selected):
    user, workspace, organization = selected
    request = request_for(workspace, route="workspace_list")
    assert authentication._workspace_selection_optional(request)
    assert authentication.APIKeyAuthentication()._get_requested_workspace(request, user, organization) is None


def test_workspace_list_exemption_is_read_only(selected):
    _, workspace, _ = selected
    assert not authentication._workspace_selection_optional(request_for(workspace, "POST", "workspace_list"))


def test_valid_independent_workspace_remains_selected(selected):
    user, workspace, organization = selected
    user.can_access_workspace.return_value = True
    assert authentication.APIKeyAuthentication()._get_requested_workspace(request_for(workspace), user, organization) is workspace


@pytest.fixture
def creation(monkeypatch):
    organization = SimpleNamespace(pk=UUID(int=3), id=UUID(int=3), name="New", display_name="New", save=Mock())
    constructor = Mock(return_value=organization)
    monkeypatch.setattr(organization_views, "Organization", constructor)
    membership = Mock()
    monkeypatch.setattr(organization_views, "issue_membership", membership)
    workspace = SimpleNamespace(id=UUID(int=4), name="Default", display_name="Default", is_default=True)
    provision = Mock(return_value=workspace)
    monkeypatch.setattr(organization_views, "provision_default_workspace", provision)
    monkeypatch.setattr(organization_views, "identity_keys", Mock(return_value=[]))
    monkeypatch.setattr(organization_views, "lifecycle_lock", lambda **kwargs: nullcontext())
    monkeypatch.setattr(organization_views, "_exists", Mock())
    request = SimpleNamespace(
        user=SimpleNamespace(pk=UUID(int=5), id=UUID(int=5), organization_id=UUID(int=2)),
        validated_data={"name": "New"},
    )
    return SimpleNamespace(request=request, constructor=constructor, membership=membership, provision=provision)


def create(request):
    # Bypass input validation only: the production handler remains intact.
    from inspect import unwrap
    return unwrap(organization_views.CreateAdditionalOrganizationView.post)(organization_views.CreateAdditionalOrganizationView(), request)


def test_scoped_session_creation_is_refused_before_provisioning(creation):
    creation.request.auth_scope = SimpleNamespace(org_id=UUID(int=2))
    with pytest.raises(PermissionDenied) as denied:
        create(creation.request)
    assert denied.value.get_codes() == "saml_scope_conflict"
    assert "sign in" in str(denied.value).lower()
    creation.constructor.assert_not_called()
    creation.membership.assert_not_called()
    creation.provision.assert_not_called()


def test_unscoped_session_keeps_organization_creation(creation):
    response = create(creation.request)
    assert response.status_code == 201
    creation.constructor.assert_called_once()
    creation.membership.assert_called_once()
    creation.provision.assert_called_once()


@pytest.mark.parametrize("scoped", [True, False])
def test_user_info_exposes_session_creation_capability(monkeypatch, scoped):
    user = SimpleNamespace(id=UUID(int=5), config={}, invited_by=None, role="", goals=[], is_authenticated=True)
    manager = Mock()
    manager.select_related.return_value.get.return_value = user
    monkeypatch.setattr(user_views.User, "objects", manager)
    monkeypatch.setattr(user_views, "UserSerializer", lambda user: SimpleNamespace(data={}))
    monkeypatch.setattr(user_views, "get_user_checks", lambda user: {})
    memberships = Mock()
    memberships.filter.return_value.select_related.return_value.first.return_value = None
    monkeypatch.setattr(OrganizationMembership, "no_workspace_objects", memberships)
    request = APIRequestFactory().get("/accounts/user-info/")
    request.organization = None
    if scoped:
        request.auth_scope = SimpleNamespace(org_id=UUID(int=2))
    force_authenticate(request, user=user)
    response = user_views.get_user_info(request)
    assert isinstance(response, Response)
    assert response.data["can_create_organization"] is (not scoped)
