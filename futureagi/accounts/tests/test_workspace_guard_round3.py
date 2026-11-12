"""Round-3 regressions: no global-role/path wildcard or hidden credential rows."""

from types import SimpleNamespace

import pytest
from django.urls import resolve
from rest_framework.test import APIRequestFactory

from accounts.models.organization import Organization
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace
from accounts.tests import test_workspace_landing_boundary as boundary_tests
from accounts.tests import workspace_write_support as write_support
from accounts.tests.test_workspace_landing import org_member
from accounts.tests.workspace_write_support import (
    credential_headers,
    membership_snapshot,
    request_view,
    write_snapshot,
)
from agentcc.key_permissions import has_workspace_write_access
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.api_errors import first_error_code

boundary = boundary_tests.boundary
landing = boundary_tests.landing
scope_no_egress = write_support.scope_no_egress
write_effects = write_support.write_effects


@pytest.mark.parametrize("role", ["Owner", "Admin"])
@pytest.mark.parametrize("org_state", ["no-workspaces", "inactive-default"])
@pytest.mark.parametrize("credential", ["session", "key"])
def test_global_actor_without_workspace_cannot_insert(
    landing, write_effects, role, org_state, credential
):
    org = Organization.objects.create(name="Unprovisioned write boundary")
    user, _ = org_member(
        org,
        "global-write@example.test",
        role,
        Level.OWNER if role == "Owner" else Level.ADMIN,
    )
    if org_state == "inactive-default":
        Workspace._base_manager.create(
            name="Inactive default",
            organization=org,
            created_by=user,
            is_default=True,
            is_active=False,
        )
    headers = credential_headers(user, org, credential)
    before = write_snapshot()
    memberships = membership_snapshot()
    response = request_view(
        boundary_tests.CREATE_DATASET, headers, boundary_tests.CREATE_BODY
    )
    assert response.status_code == 403, response.data
    assert first_error_code(response.data) == "workspace_required"
    assert write_snapshot() == before
    assert membership_snapshot() == memberships
    write_effects.quota.assert_not_called()
    write_effects.create.assert_not_called()


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
@pytest.mark.parametrize(
    "path",
    [
        "/datasets/00000000-0000-0000-0000-0000000002fa/",
        "/datasets/logout/",
        "/datasets/onboarding/",
        "/datasets/passkeys/",
        "/datasets/passkey/",
        "/datasets/update-user-full-name/",
        "/datasets/workspace/switch/",
        "/datasets/organizations/switch/",
        "/prefix/model-hub/annotation-queues/q/items/i/complete/",
    ],
)
def test_path_substring_cannot_exempt_data_write(boundary, write_effects, path):
    # Resolve the real data-write handler, but give it a route containing a
    # legacy role-check skip. Routing is the authority, never path substrings.
    headers = credential_headers(boundary.user, boundary.landing.org)
    request = APIRequestFactory().post(
        path, boundary_tests.CREATE_BODY, format="json", **headers
    )
    match = resolve(boundary_tests.CREATE_DATASET)
    request.resolver_match = match
    before = write_snapshot()
    memberships = membership_snapshot()
    try:
        response = match.func(request, *match.args, **match.kwargs)
    finally:
        clear_workspace_context()
    assert response.status_code == 403, response.data
    assert first_error_code(response.data) == "workspace_required"
    assert write_snapshot() == before
    assert membership_snapshot() == memberships
    write_effects.quota.assert_not_called()
    write_effects.create.assert_not_called()


@pytest.mark.parametrize("boundary", ["absent", "inactive-default"], indirect=True)
def test_empty_landing_personal_key_lookup_and_lifecycle(
    boundary, write_effects, monkeypatch
):
    monkeypatch.setattr("accounts.views.keys._publish_key_revocation", lambda key: None)
    monkeypatch.setattr(
        "tfc.deployment_telemetry.events.record_event", lambda *a, **k: None
    )
    org = boundary.landing.org
    headers = credential_headers(boundary.user, org)
    own = OrgApiKey._base_manager.create(
        user=boundary.user, organization=org, type="user", name="Own visible key"
    )
    # Explicit scoping must not expose another user's, another org's, deleted,
    # or retired organization credentials when bypassing the empty-scope manager.
    for name, overrides in [
        ("Other user", {"user": boundary.landing.owner}),
        ("Other org", {"organization": Organization.objects.create(name="Elsewhere")}),
        ("Deleted", {"deleted": True}),
        ("Retired", {"type": "org"}),
    ]:
        fields = {
            "user": boundary.user,
            "organization": org,
            "type": "user",
            "name": name,
        }
        fields.update(overrides)
        OrgApiKey._base_manager.create(**fields)
    memberships = membership_snapshot()
    listed = request_view("/accounts/key/get_secret_keys/", headers, method="get")
    assert listed.status_code == 200
    assert {str(row["id"]) for row in listed.data["result"]["table"]} == {str(own.pk)}
    duplicate = request_view(
        "/accounts/key/generate_secret_key/", headers, {"key_name": own.name}
    )
    assert duplicate.status_code == 400
    assert (
        OrgApiKey._base_manager.filter(user=boundary.user, name=own.name).count() == 1
    )
    for action, enabled in [("disable_key", False), ("enable_key", True)]:
        response = request_view(
            f"/accounts/key/{action}/", headers, {"key_id": str(own.pk)}
        )
        assert response.status_code == 200
        own.refresh_from_db()
        assert own.enabled is enabled
    assert membership_snapshot() == memberships


@pytest.mark.parametrize("boundary", ["absent"], indirect=True)
def test_legacy_role_exemption_cannot_allow_empty_data_write(
    boundary, write_effects, monkeypatch
):
    match = resolve(boundary_tests.CREATE_DATASET)
    monkeypatch.setattr(match.func.cls, "workspace_write_exempt", True, raising=False)
    headers = credential_headers(boundary.user, boundary.landing.org)
    before = write_snapshot()
    response = request_view(
        boundary_tests.CREATE_DATASET, headers, boundary_tests.CREATE_BODY
    )
    assert response.status_code == 403, response.data
    assert first_error_code(response.data) == "workspace_required"
    assert write_snapshot() == before
    write_effects.create.assert_not_called()


@pytest.mark.parametrize("bound", [False, True])
def test_gateway_write_mirror_requires_workspace(bound):
    request = SimpleNamespace(
        workspace=None,
        auth_scope=object() if bound else None,
        user=SimpleNamespace(has_global_workspace_access=lambda org: True),
        organization=object(),
    )
    assert has_workspace_write_access(request) is False


def test_member_cannot_bootstrap_via_org_switch(landing, write_effects):
    org = Organization.objects.create(name="Member cannot provision")
    user, _ = org_member(org, "member-bootstrap@example.test", "Member", Level.MEMBER)
    headers = credential_headers(user, org)
    before = membership_snapshot()
    response = request_view(
        "/accounts/organizations/switch/", headers, {"organization_id": str(org.pk)}
    )
    assert response.status_code == 200, response.data
    assert not Workspace._base_manager.filter(organization=org).exists()
    assert membership_snapshot() == before
