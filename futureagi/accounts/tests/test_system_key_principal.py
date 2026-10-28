"""System credentials use an active organization Owner membership."""

import base64
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.tests.key_scope_helpers import key_client, make_key
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]


@pytest.mark.parametrize("transport", ["basic", "mcp"])
@pytest.mark.parametrize("has_owner", [False, True])
def test_system_key_transports_share_membership_principal(
    key_scope, transport, has_owner
):
    from rest_framework.exceptions import AuthenticationFailed
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    from accounts.authentication import LangfuseBasicAuthentication
    from mcp_server import mcp_app

    scope, other = key_scope
    owner = scope.users[Level.OWNER]
    # None of the members has this org as home, including the actual Owner.
    User.objects.filter(organization=scope.org).update(organization=other.org)
    if not has_owner:
        OrganizationMembership.no_workspace_objects.filter(
            user=owner, organization=scope.org
        ).update(is_active=False)
    key = make_key(scope.org, key_type="system")
    try:
        if transport == "mcp":
            context = mcp_app._authenticate_and_set_context(key.api_key, key.secret_key)
            if has_owner:
                assert context is not None
                assert context.user.pk == owner.pk
                assert context.organization.pk == scope.org.pk
            else:
                assert context is None
        else:
            encoded = base64.b64encode(
                f"{key.api_key}:{key.secret_key}".encode()
            ).decode()
            req = Request(
                APIRequestFactory().get(
                    "/api/test/", HTTP_AUTHORIZATION=f"Basic {encoded}"
                )
            )
            if has_owner:
                user, _ = LangfuseBasicAuthentication().authenticate(req)
                assert user.pk == owner.pk
                assert req.organization.pk == scope.org.pk
            else:
                with pytest.raises(AuthenticationFailed):
                    LangfuseBasicAuthentication().authenticate(req)
    finally:
        mcp_app._clear_context()
        clear_workspace_context()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("home_org", [True, False])
def test_system_key_selects_owner_membership_not_first_home_user(
    key_scope, legacy, home_org
):
    scope, other = key_scope
    owner = scope.users[Level.OWNER]
    User.objects.filter(pk=scope.users[Level.MEMBER].pk).update(
        created_at=timezone.now() - timedelta(days=2)
    )
    if not home_org:
        User.objects.filter(organization=scope.org).update(organization=other.org)
    if legacy:
        OrganizationMembership.no_workspace_objects.filter(
            user=owner, organization=scope.org
        ).update(level=None)
    key = make_key(scope.org, key_type="system")
    response = key_client(key).get("/accounts/user-info/")
    assert response.status_code == 200
    assert response.data["id"] == str(owner.pk)
    assert response.data["organization"]["id"] == str(scope.org.pk)
    assert response.data["org_level"] == Level.OWNER
    clear_workspace_context()
    target = make_key(scope.org, owner)
    deleted = key_client(key).delete(
        "/accounts/key/delete_secret_key/", {"key_id": str(target.pk)}, format="json"
    )
    assert deleted.status_code == 200
    target.refresh_from_db()
    assert target.deleted


@pytest.mark.parametrize(
    "state", ["inactive", "deleted", "demoted", "disabled_user", "deleted_user"]
)
def test_system_key_without_active_owner_is_refused(key_scope, state):
    scope = key_scope[0]
    owner = scope.users[Level.OWNER]
    membership = owner.get_membership(scope.org)
    if state == "disabled_user":
        User.objects.filter(pk=owner.pk).update(is_active=False)
    elif state == "deleted_user":
        # Users are hard-deleted; OrganizationMembership owns soft deletion.
        owner.delete()
    else:
        fields = {
            "inactive": {"is_active": False},
            "deleted": {"deleted": True},
            # A stale Owner role must not override a populated Member level.
            "demoted": {"level": Level.MEMBER},
        }
        OrganizationMembership.all_objects.filter(pk=membership.pk).update(
            **fields[state]
        )
    key = make_key(scope.org, key_type="system")
    response = key_client(key).get("/accounts/user-info/")
    assert response.status_code == 401


def test_system_key_owner_selection_is_stable(key_scope):
    scope = key_scope[0]
    first = scope.users[Level.OWNER].get_membership(scope.org)
    second = scope.users[Level.ADMIN].get_membership(scope.org)
    # Tie creation time: UUID is the deterministic secondary ordering key.
    OrganizationMembership.no_workspace_objects.filter(
        pk__in=[first.pk, second.pk]
    ).update(level=Level.OWNER, role="Owner", created_at=first.created_at)
    expected = first.user_id if str(first.pk) < str(second.pk) else second.user_id
    key = make_key(scope.org, key_type="system")
    for _ in range(2):
        response = key_client(key).get("/accounts/user-info/")
        assert response.status_code == 200
        assert response.data["id"] == str(expected)
        clear_workspace_context()
