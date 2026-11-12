"""Organization offboarding requires organization-level administration."""

import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import get_resolver
from rest_framework import status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services import offboarding
from accounts.tests import test_e2e_role_and_removal as role_support
from accounts.tests import test_offboarding_cross_org as offboarding_support
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db
@pytest.fixture
def _urlconf_loaded():
    # Import the URLconf (and its import-time service setup) before egress is
    # forbidden, so this module does not depend on another suite running first.
    get_resolver().url_patterns


@pytest.fixture(autouse=True)
def local_only(_urlconf_loaded, settings, monkeypatch):
    # Same isolation as the offboarding suite: local cache, no network egress,
    # external revocation delivery stubbed; real DB revocation logic runs.
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()
    original_connect = socket.socket.connect

    def connect(sock, address):
        if sock.family == socket.AF_UNIX:
            return original_connect(sock, address)
        raise AssertionError("Offboarding tests forbid network egress")

    monkeypatch.setattr(socket.socket, "connect", connect)
    with (
        patch("accounts.api_key_revocation.publish_key_revocation"),
        patch("agentcc.services.auth_bridge.revoke_key"),
        patch("accounts.services.service_grants.publish_grant_revocation"),
    ):
        yield
    cache.clear()

ORG_REMOVE_URL = "/accounts/organization/members/remove/"


@pytest.fixture
def removal_target(organization, workspace, user):
    second_workspace = Workspace.no_workspace_objects.create(
        name="Other viewer workspace", organization=organization, created_by=user
    )
    viewer = role_support._make_user(
        organization, "remove-authz-viewer@futureagi.com", "Viewer", Level.VIEWER
    )
    membership = OrganizationMembership.no_workspace_objects.get(
        user=viewer, organization=organization
    )
    grants = tuple(
        role_support._add_ws_membership(
            viewer, ws, organization, Level.WORKSPACE_VIEWER
        )
        for ws in (workspace, second_workspace)
    )
    return SimpleNamespace(
        user=viewer,
        membership=membership,
        grants=grants,
        tokens=offboarding_support.credentials(viewer, organization),
        key=OrgApiKey._base_manager.create(user=viewer, organization=organization),
    )


@pytest.fixture
def workspace_admin(organization, workspace):
    delegate = role_support._make_user(
        organization, "remove-authz-delegate@futureagi.com", "Member", Level.MEMBER
    )
    role_support._add_ws_membership(
        delegate, workspace, organization, Level.WORKSPACE_ADMIN
    )
    # Pin the reported boundary: Member in the org, Admin in A, no grant in B.
    membership = OrganizationMembership.no_workspace_objects.get(
        user=delegate, organization=organization
    )
    assert membership.level_or_legacy == Level.MEMBER
    assert list(
        WorkspaceMembership._base_manager.filter(
            user=delegate, is_active=True, deleted=False
        ).values_list("workspace_id", "level")
    ) == [(workspace.pk, Level.WORKSPACE_ADMIN)]
    return delegate


def _membership_snapshot(organization):
    """Bypass workspace/deleted filters so a refusal cannot hide changed rows."""
    return (
        list(
            OrganizationMembership._base_manager.filter(organization=organization)
            .order_by("pk")
            .values()
        ),
        list(
            WorkspaceMembership._base_manager.filter(
                workspace__organization=organization
            )
            .order_by("pk")
            .values()
        ),
    )


def _assert_viewer_active(target, workspace_ids):
    target.user.refresh_from_db()
    target.membership.refresh_from_db()
    assert target.user.is_active
    assert target.membership.is_active and not target.membership.deleted
    assert target.membership.level_or_legacy == Level.VIEWER
    assert set(
        WorkspaceMembership._base_manager.filter(
            user=target.user,
            workspace__organization_id=target.membership.organization_id,
            is_active=True,
            deleted=False,
        ).values_list("workspace_id", flat=True)
    ) == workspace_ids
    for token, bearer in target.tokens:
        token.refresh_from_db()
        assert token.is_active
        assert cache.get(f"{token.auth_type}_token_{token.pk}")["token"] == bearer
    target.key.refresh_from_db()
    assert target.key.enabled


def _assert_org_removal(client, target, capture_on_commit):
    with capture_on_commit(execute=True):
        response = client.delete(
            ORG_REMOVE_URL, {"user_id": str(target.user.pk)}, format="json"
        )
    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["result"]["user_id"] == str(target.user.pk)
    target.membership.refresh_from_db()
    assert not target.membership.is_active
    assert target.membership.deleted
    for grant in target.grants:
        grant.refresh_from_db()
        assert not grant.is_active
    target.user.refresh_from_db()
    assert target.user.is_active  # Offboard the organization, not the identity.
    for token, _ in target.tokens:
        token.refresh_from_db()
        assert not token.is_active
        assert cache.get(f"{token.auth_type}_token_{token.pk}") is None
    target.key.refresh_from_db()
    assert not target.key.enabled


def test_workspace_admin_cannot_remove_organization_member(
    auth_client,
    organization,
    workspace_admin,
    removal_target,
    django_capture_on_commit_callbacks,
):
    auth_client.force_authenticate(user=workspace_admin)
    workspace_ids = {grant.workspace_id for grant in removal_target.grants}
    _assert_viewer_active(removal_target, workspace_ids)
    before = _membership_snapshot(organization)

    with (
        patch(
            "accounts.services.offboarding.revoke_organization_credentials",
            wraps=offboarding.revoke_organization_credentials,
        ) as revoke,
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = auth_client.delete(
            ORG_REMOVE_URL,
            {"user_id": str(removal_target.user.pk)},
            format="json",
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
    assert response.data["status"] is False
    assert response.data["type"] == "permission_error"
    assert response.data["code"] == "permission_denied"
    for field in ("detail", "message", "error", "result"):
        assert response.data[field] == "Organization admin access required."
    assert response.data["details"] == {
        "detail": ["Organization admin access required."]
    }
    revoke.assert_not_called()
    assert _membership_snapshot(organization) == before
    _assert_viewer_active(removal_target, workspace_ids)


def test_org_admin_can_remove_organization_member(
    auth_client, organization, removal_target, django_capture_on_commit_callbacks
):
    admin = role_support._make_user(
        organization, "remove-authz-admin@futureagi.com", "Admin", Level.ADMIN
    )
    auth_client.force_authenticate(user=admin)
    _assert_org_removal(
        auth_client, removal_target, django_capture_on_commit_callbacks
    )


def test_org_owner_can_remove_organization_member(
    auth_client, removal_target, django_capture_on_commit_callbacks
):
    # The shared auth_client fixture authenticates the organization's Owner.
    _assert_org_removal(
        auth_client, removal_target, django_capture_on_commit_callbacks
    )


def test_workspace_admin_can_remove_workspace_member_only(
    auth_client,
    workspace,
    workspace_admin,
    removal_target,
    django_capture_on_commit_callbacks,
):
    auth_client.force_authenticate(user=workspace_admin)
    workspace_ids = {grant.workspace_id for grant in removal_target.grants}
    _assert_viewer_active(removal_target, workspace_ids)
    with (
        patch(
            "accounts.services.offboarding.revoke_organization_credentials",
            wraps=offboarding.revoke_organization_credentials,
        ) as revoke,
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = auth_client.delete(
            f"/accounts/workspace/{workspace.pk}/members/remove/",
            {"user_id": str(removal_target.user.pk)},
            format="json",
        )
    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["result"]["user_id"] == str(removal_target.user.pk)
    revoke.assert_not_called()
    _assert_viewer_active(removal_target, workspace_ids - {workspace.pk})
