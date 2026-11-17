"""Unavailable selectors must not disclose workspace existence on writes."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from rest_framework.exceptions import PermissionDenied

from accounts.authentication import APIKeyAuthentication, _workspace_selection_optional
from accounts.models.workspace import WorkspaceMembership
from accounts.tests import workspace_write_support as support
from accounts.tests.test_workspace_landing import landing as landing
from accounts.tests.test_workspace_landing_boundary import CREATE_BODY, CREATE_DATASET
from tfc.utils.api_errors import first_error_code

scope_no_egress = support.scope_no_egress
write_effects = support.write_effects
pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("credential", ["session", "key"])
def test_unjoined_and_missing_write_selector_have_identical_response(
    landing, write_effects, credential
):
    WorkspaceMembership._base_manager.filter(pk=landing.other_mem.pk).delete()
    assert landing.user.can_access_workspace(landing.default)
    assert not landing.user.can_access_workspace(landing.other)
    headers = support.credential_headers(landing.user, landing.org, credential)
    before = support.write_snapshot()
    responses = []
    for target in (landing.other.pk, uuid4()):
        response = support.request_view(
            CREATE_DATASET, {**headers, "HTTP_X_WORKSPACE_ID": str(target)}, CREATE_BODY
        )
        responses.append(
            (response.status_code, first_error_code(response.data), response.data)
        )
    assert responses[0] == responses[1]
    assert responses[0][:2] == (403, "workspace_unavailable")
    assert support.write_snapshot() == before
    write_effects.create.assert_not_called()
    write_effects.quota.assert_not_called()


def test_unjoined_and_missing_write_selector_have_identical_query_path(landing):
    WorkspaceMembership._base_manager.filter(pk=landing.other_mem.pk).delete()
    paths = []
    for target in (landing.other.pk, uuid4()):
        request = SimpleNamespace(
            method="POST",
            path=CREATE_DATASET,
            GET={},
            headers={"X-Workspace-Id": str(target)},
            resolver_match=resolve(CREATE_DATASET),
        )
        with CaptureQueriesContext(connection) as queries:
            with pytest.raises(PermissionDenied) as error:
                APIKeyAuthentication()._get_requested_workspace(
                    request, landing.user, landing.org
                )
        assert error.value.get_codes() == "workspace_unavailable"
        paths.append(
            [
                q["sql"]
                .replace(str(target), "SELECTOR")
                .replace(target.hex, "SELECTOR")
                for q in queries
            ]
        )
    assert len(paths[0]) == len(paths[1]) == 1
    assert paths[0] == paths[1]


ACCOUNT_ROUTES = [
    "workspace/switch/",
    "organizations/switch/",
    "update-user-full-name/",
    "onboarding/",
    "logout/",
    "2fa/status/",
    "2fa/totp/setup/",
    "2fa/totp/confirm/",
    "2fa/totp/",
    "2fa/verify/totp/",
    "2fa/verify/recovery/",
    "2fa/verify/passkey/options/",
    "2fa/verify/passkey/",
    "2fa/recovery-codes/",
    "2fa/recovery-codes/regenerate/",
    "passkey/register/options/",
    "passkey/register/verify/",
    "passkeys/",
    "passkeys/00000000-0000-0000-0000-000000000001/",
    "passkey/authenticate/options/",
    "passkey/authenticate/verify/",
]


@pytest.mark.parametrize("route", ACCOUNT_ROUTES)
def test_exact_account_route_keeps_optional_selector(route):
    path = f"/accounts/{route}"
    request = SimpleNamespace(method="POST", path=path, resolver_match=resolve(path))
    assert _workspace_selection_optional(request)


@pytest.mark.parametrize(
    "path", ["/x/onboarding/", "/x/2fa/totp/", "/x/workspace/switch/"]
)
def test_account_substring_does_not_exempt_unrelated_resolved_view(path):
    request = SimpleNamespace(
        method="POST", path=path, resolver_match=resolve(CREATE_DATASET)
    )
    assert not _workspace_selection_optional(request)


@pytest.mark.parametrize(
    "role,level,joined,active",
    [
        ("Member", 3, False, True),
        ("Member", 3, True, True),
        ("Owner", 15, False, True),
        ("Admin", 8, False, True),
        ("Owner", None, False, True),
        ("Admin", None, False, True),
        ("Owner", 3, False, True),
        ("Member", 8, False, True),
        ("Owner", 15, True, False),
    ],
)
def test_write_selector_matches_canonical_workspace_access(
    landing, role, level, joined, active
):
    from accounts.models.organization_membership import OrganizationMembership

    OrganizationMembership.no_workspace_objects.filter(
        user=landing.user, organization=landing.org
    ).update(role=role, level=level, is_active=active)
    if not joined:
        WorkspaceMembership._base_manager.filter(pk=landing.other_mem.pk).delete()
    request = SimpleNamespace(
        method="POST",
        path=CREATE_DATASET,
        GET={},
        headers={"X-Workspace-Id": str(landing.other.pk)},
        resolver_match=resolve(CREATE_DATASET),
    )
    auth = APIKeyAuthentication()
    if landing.user.can_access_workspace(landing.other):
        assert (
            auth._get_requested_workspace(request, landing.user, landing.org).pk
            == landing.other.pk
        )
    else:
        with pytest.raises(PermissionDenied) as error:
            auth._get_requested_workspace(request, landing.user, landing.org)
        assert error.value.get_codes() == "workspace_unavailable"
