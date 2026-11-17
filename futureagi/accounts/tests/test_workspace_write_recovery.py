"""Empty landing denies data mutations, not account and organization recovery."""

import pytest

from accounts.models.auth_token import AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from accounts.tests import test_workspace_landing_boundary as boundary_tests
from accounts.tests import workspace_write_support as write_support
from accounts.tests.workspace_write_support import (
    credential_headers,
    encrypted_token,
    membership_snapshot,
    request_view,
)

boundary = boundary_tests.boundary
landing = boundary_tests.landing
scope_no_egress = write_support.scope_no_egress
write_effects = write_support.write_effects


@pytest.mark.parametrize("boundary", ["absent", "inactive-default"], indirect=True)
def test_empty_landing_refresh_with_live_bearer(boundary, write_effects):
    user = boundary.user
    headers = credential_headers(user, boundary.landing.org)
    refresh = encrypted_token(user, AuthTokenType.REFRESH.value)
    before = membership_snapshot()
    response = request_view("/accounts/token/refresh/", headers, {"refresh": refresh})
    assert response.status_code == 200, response.data
    assert response.data["access"]
    assert membership_snapshot() == before


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
def test_empty_landing_account_profile_and_selection(boundary, write_effects):
    headers = credential_headers(boundary.user, boundary.landing.org)
    before = membership_snapshot()
    selected = request_view(
        "/accounts/organizations/",
        headers,
        {"organization_id": str(boundary.landing.org.pk)},
    )
    assert selected.status_code == 200, selected.data
    profile = request_view(
        "/accounts/update-user-full-name/", headers, {"name": "Updated member"}
    )
    assert profile.status_code == 200, profile.data
    boundary.user.refresh_from_db()
    assert boundary.user.name == "Updated member"
    assert membership_snapshot() == before


@pytest.mark.parametrize("boundary", ["absent", "inactive-default"], indirect=True)
def test_empty_landing_can_create_additional_organization(boundary, write_effects):
    headers = credential_headers(boundary.user, boundary.landing.org)
    response = request_view(
        "/accounts/organizations/new/", headers, {"name": "Additional recovery org"}
    )
    assert response.status_code == 201, response.data
    membership = OrganizationMembership._base_manager.get(
        user=boundary.user, organization__name="Additional recovery org"
    )
    assert membership.is_active
    workspace = Workspace._base_manager.get(organization=membership.organization)
    assert boundary.user.can_access_workspace(workspace)


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
def test_orgless_account_can_create_recovery_organization(
    boundary, write_effects, monkeypatch
):
    user = boundary.user
    OrganizationMembership._base_manager.filter(user=user).update(is_active=False)
    monkeypatch.setattr(
        "accounts.views.organization_views.process_post_registration",
        lambda *a, **kw: None,
    )
    headers = credential_headers(user, boundary.landing.org)
    response = request_view(
        "/accounts/organizations/create/",
        headers,
        {"organization_name": "Recovered org"},
    )
    assert response.status_code == 201, response.data
    workspace = Workspace._base_manager.get(organization__name="Recovered org")
    assert user.can_access_workspace(workspace)


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
def test_read_only_post_marker_keeps_empty_scope(boundary, write_effects):
    from django.urls import resolve
    from rest_framework.response import Response
    from rest_framework.test import APIRequestFactory
    from rest_framework.views import APIView

    from accounts.authentication import APIKeyAuthentication, workspace_read_only

    @workspace_read_only
    class ReadOnlyProbe(APIView):
        authentication_classes = [APIKeyAuthentication]

        def post(self, request):
            assert request.workspace is None
            return Response({"read_only": True})

    headers = credential_headers(boundary.user, boundary.landing.org)
    request = APIRequestFactory().post(
        "/read-only-probe/", {}, format="json", **headers
    )
    # The actual class marker, not a string/path exemption, owns the decision.
    match = resolve("/accounts/workspaces/")
    match.func = ReadOnlyProbe.as_view()
    request.resolver_match = match
    response = match.func(request)
    assert response.status_code == 200, response.data
