"""Resolver-based recovery exemptions are method-exact, never path wildcards."""

import pytest
from django.urls import resolve
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from accounts.authentication import (
    APIKeyAuthentication,
    allows_no_organization,
    workspace_read_only,
)
from accounts.tests import test_workspace_landing_boundary as boundary_tests
from accounts.tests import workspace_write_support as write_support
from tfc.middleware.workspace_context import clear_workspace_context

boundary = boundary_tests.boundary
landing = boundary_tests.landing
scope_no_egress = write_support.scope_no_egress
write_effects = write_support.write_effects


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/accounts/2fa/totp/setup/"),
        ("post", "/accounts/2fa/totp/confirm/"),
        ("delete", "/accounts/2fa/totp/"),
        ("post", "/accounts/2fa/recovery-codes/regenerate/"),
        ("post", "/accounts/passkey/register/options/"),
        ("post", "/accounts/passkey/register/verify/"),
        ("patch", "/accounts/passkeys/00000000-0000-0000-0000-000000000001/"),
        ("delete", "/accounts/passkeys/00000000-0000-0000-0000-000000000001/"),
        ("post", "/accounts/logout/"),
        ("post", "/accounts/onboarding/"),
        ("post", "/accounts/update-user-full-name/"),
        ("post", "/accounts/workspace/switch/"),
        ("post", "/accounts/organizations/switch/"),
    ],
)
def test_existing_account_recovery_method_is_exempt(
    boundary, write_effects, method, path
):
    request = getattr(APIRequestFactory(), method)(path, {}, format="json")
    request.resolver_match = resolve(path)
    try:
        APIKeyAuthentication()._set_workspace_context(request, boundary.user)
        assert request.workspace is None
        assert request.organization == boundary.landing.org
    finally:
        clear_workspace_context()


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
@pytest.mark.parametrize("marker", [allows_no_organization, workspace_read_only])
def test_future_write_method_does_not_inherit_recovery_exemption(
    boundary, write_effects, marker
):
    @marker
    class Recovery(APIView):
        def post(self, request):
            pass

    class ExpandedRecovery(Recovery):
        def patch(self, request):
            pass

    request = APIRequestFactory().patch("/method-scope-probe/", {}, format="json")
    match = resolve("/accounts/workspaces/")
    match.func = ExpandedRecovery.as_view()
    request.resolver_match = match
    try:
        with pytest.raises(PermissionDenied) as denied:
            APIKeyAuthentication()._set_workspace_context(request, boundary.user)
        assert denied.value.get_codes() == "workspace_required"
    finally:
        clear_workspace_context()


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
@pytest.mark.parametrize("target", ["parent", "decorated", "child", "sibling"])
def test_read_only_decoration_does_not_mark_inherited_post(
    boundary, write_effects, target
):
    class Parent(APIView):
        def post(self, request):
            pass

    @workspace_read_only
    class ReadOnlyChild(Parent):
        pass

    class Grandchild(ReadOnlyChild):
        pass

    class Sibling(Parent):
        pass

    view = {
        "parent": Parent,
        "decorated": ReadOnlyChild,
        "child": Grandchild,
        "sibling": Sibling,
    }[target]
    request = APIRequestFactory().post("/inherited-post-probe/", {}, format="json")
    match = resolve("/accounts/workspaces/")
    match.func = view.as_view()
    request.resolver_match = match
    try:
        with pytest.raises(PermissionDenied) as denied:
            APIKeyAuthentication()._set_workspace_context(request, boundary.user)
        assert denied.value.get_codes() == "workspace_required"
        assert not getattr(Parent.post, "_no_workspace_methods", ())
    finally:
        clear_workspace_context()
