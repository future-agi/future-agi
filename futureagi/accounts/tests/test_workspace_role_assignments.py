"""Workspace role updates through the HTTP endpoint and member tool."""

import uuid

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models import User
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from ai_tools.base import ToolContext
from ai_tools.tools.users.add_workspace_member import (
    AddWorkspaceMemberInput,
    AddWorkspaceMemberTool,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def credential(user, kind):
    """Create a real access credential without depending on reset features."""
    row = AuthToken.objects.create(
        user=user, auth_type=kind, is_active=True, last_used_at=timezone.now()
    )
    return row, generate_encrypted_message({"user_id": str(user.id), "id": str(row.id)})


def principal(org, ws, level):
    user = User.objects.create_user(
        email=f"role-{uuid.uuid4().hex}@example.test",
        password="Unique-Grant-Pass-978!",
        organization=org,
        organization_role=Level.to_org_string(level),
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=Level.to_org_string(level),
        level=level,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=ws,
        organization_membership=membership,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    return user, membership


def invoke(kind, actor, org, ws, target, role="workspace_admin"):
    if kind == "tool":
        result = AddWorkspaceMemberTool().execute(
            AddWorkspaceMemberInput(workspace_id=ws.pk, user_id=target.pk, role=role),
            ToolContext(user=actor, organization=org, workspace=ws),
        )
        return 403 if result.is_error else 200
    from tfc.middleware.workspace_context import (
        clear_workspace_context,
        get_current_organization,
        get_current_user,
        get_current_workspace,
        set_workspace_context,
    )

    _, token = credential(actor, AuthTokenType.ACCESS)
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {token}",
        HTTP_X_ORGANIZATION_ID=str(org.pk),
        HTTP_X_WORKSPACE_ID=str(ws.pk),
    )
    # A real request starts without the test thread's tenant context. Requests sent
    # under the fixture's workspace context left later prompt-template listings in
    # the same test process empty.
    saved = get_current_workspace(), get_current_organization(), get_current_user()
    clear_workspace_context()
    try:
        return client.post(
            "/accounts/user/role/update/",
            {"user_id": str(target.pk), "workspace_id": str(ws.pk), "new_role": role},
            format="json",
        ).status_code
    finally:
        set_workspace_context(workspace=saved[0], organization=saved[1], user=saved[2])


@pytest.mark.parametrize("kind", ["http", "tool"])
@pytest.mark.parametrize("target_state", ["typed_member", "legacy_null", "absent"])
def test_ordinary_member_cannot_grant_workspace_admin_but_owner_can(
    user, organization, workspace, kind, target_state
):
    actor, _ = principal(organization, workspace, Level.MEMBER)
    target, _ = principal(organization, workspace, Level.MEMBER)
    existing = WorkspaceMembership.no_workspace_objects.filter(
        user=target, workspace=workspace
    )
    if target_state == "legacy_null":
        existing.update(level=None)
    elif target_state == "absent":
        existing.delete()
    status = invoke(kind, actor, organization, workspace, target)
    membership = WorkspaceMembership.no_workspace_objects.filter(
        user=target, workspace=workspace
    ).first()
    effective = membership.level_or_legacy if membership else None
    assert status == 403, f"status={status}; resulting workspace level={effective}"
    assert effective == (None if target_state == "absent" else Level.WORKSPACE_MEMBER)
    assert invoke(kind, user, organization, workspace, target) == 200
    membership = WorkspaceMembership.no_workspace_objects.get(
        user=target, workspace=workspace
    )
    assert membership.level == Level.WORKSPACE_ADMIN
    assert membership.organization_membership.organization_id == organization.pk


@pytest.mark.parametrize("kind", ["http", "tool"])
def test_stale_legacy_owner_role_does_not_override_current_membership(
    user, organization, workspace, kind
):
    actor, membership = principal(organization, workspace, Level.MEMBER)
    actor.organization_role = OrganizationRoles.OWNER
    actor.save(update_fields=["organization_role"])
    target, _ = principal(organization, workspace, Level.MEMBER)
    assert invoke(kind, actor, organization, workspace, target) == 403
    membership.is_active = False
    membership.save(update_fields=["is_active"])
    assert invoke(kind, actor, organization, workspace, target) in (401, 403)
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_MEMBER
    )


@pytest.mark.parametrize("kind", ["http", "tool"])
def test_workspace_grant_cannot_target_foreign_org_user(
    user, organization, workspace, kind
):
    other = Organization.objects.create(name="Foreign grant org")
    ws = Workspace.no_workspace_objects.create(
        name="Foreign grant ws", organization=other, is_default=True, created_by=user
    )
    target, _ = principal(other, ws, Level.MEMBER)
    assert invoke(kind, user, organization, workspace, target) in (400, 403, 404)
    assert not WorkspaceMembership.no_workspace_objects.filter(
        user=target, workspace=workspace
    ).exists()


@pytest.mark.parametrize("kind", ["http", "tool"])
def test_workspace_admin_can_manage_lower_member_only_in_administered_workspace(
    user, organization, workspace, kind
):
    actor, org_member = principal(organization, workspace, Level.MEMBER)
    WorkspaceMembership.no_workspace_objects.filter(
        user=actor, workspace=workspace
    ).update(role="workspace_admin", level=Level.WORKSPACE_ADMIN)
    target, _ = principal(organization, workspace, Level.VIEWER)
    assert (
        invoke(kind, actor, organization, workspace, target, "workspace_member") == 200
    )
    other_ws = Workspace.no_workspace_objects.create(
        name="Unadministered", organization=organization, created_by=user
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=actor,
        workspace=other_ws,
        organization_membership=org_member,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
    )
    assert invoke(kind, actor, organization, other_ws, target) == 403
    assert not WorkspaceMembership.no_workspace_objects.filter(
        user=target, workspace=other_ws
    ).exists()


def test_legacy_http_org_role_changes_use_current_membership_level(
    user, organization, workspace
):
    admin, _ = principal(organization, workspace, Level.ADMIN)
    target, _ = principal(organization, workspace, Level.MEMBER)
    assert invoke("http", admin, organization, workspace, target, "Owner") == 403
    assert invoke("http", admin, organization, workspace, target, "Admin") == 200
    membership = OrganizationMembership.no_workspace_objects.get(
        user=target, organization=organization
    )
    assert membership.level == Level.ADMIN
    target.refresh_from_db()
    assert target.organization_role == "Admin"
    assert invoke("http", user, organization, workspace, target, "Viewer") == 200
    membership.refresh_from_db()
    assert membership.level == Level.VIEWER
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_VIEWER
    )


@pytest.mark.parametrize("kind", ["http", "tool"])
def test_admin_role_edit_does_not_activate_invited_user_and_requires_live_or_pending_membership(
    user, organization, workspace, kind
):
    from accounts.models.organization_invite import InviteStatus, OrganizationInvite

    target, membership = principal(organization, workspace, Level.MEMBER)
    target.is_active = False
    target.save(update_fields=["is_active"])
    assert invoke(kind, user, organization, workspace, target) == 200
    target.refresh_from_db()
    assert not target.is_active
    membership.is_active = False
    membership.save(update_fields=["is_active"])
    assert invoke(kind, user, organization, workspace, target, "workspace_member") in (
        400,
        403,
    )
    OrganizationInvite.objects.create(
        target_email=target.email,
        organization=organization,
        level=Level.MEMBER,
        invited_by=user,
        status=InviteStatus.PENDING,
    )
    assert (
        invoke(kind, user, organization, workspace, target, "workspace_member") == 200
    )
    target.refresh_from_db()
    membership.refresh_from_db()
    assert not target.is_active and not membership.is_active


@pytest.mark.django_db(transaction=True)
def test_user_disabled_between_precheck_and_locked_authority_read_cannot_grant(
    user, organization, workspace, mocker
):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections
    from rest_framework.exceptions import PermissionDenied

    from accounts.services.legacy_role_assignments import assign_role

    target, _ = principal(organization, workspace, Level.MEMBER)
    original_filter = User.objects.filter
    disabled = False

    def disable_in_other_connection():
        close_old_connections()
        try:
            return original_filter(pk=user.pk).update(is_active=False)
        finally:
            close_old_connections()

    def filter_with_barrier(*args, **kwargs):
        queryset = original_filter(*args, **kwargs)
        if kwargs == {"pk": user.pk, "is_active": True}:
            original_exists = queryset.exists

            def checked_then_disabled():
                nonlocal disabled
                assert original_exists()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    assert (
                        executor.submit(disable_in_other_connection).result(timeout=20)
                        == 1
                    )
                disabled = True
                return True

            queryset.exists = checked_then_disabled
        return queryset

    mocker.patch.object(User.objects, "filter", side_effect=filter_with_barrier)
    with pytest.raises(PermissionDenied):
        assign_role(
            actor=user,
            organization=organization,
            target_user_id=target.pk,
            role="workspace_admin",
            workspace_id=workspace.pk,
        )
    assert disabled
    user.refresh_from_db()
    assert not user.is_active
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_MEMBER
    )


@pytest.mark.parametrize("kind", ["http", "tool"])
@pytest.mark.parametrize("actor_level", [Level.ADMIN, Level.OWNER])
def test_current_administrator_can_grant_without_self_escalation(
    organization, workspace, kind, actor_level
):
    actor, _ = principal(organization, workspace, actor_level)
    target, _ = principal(organization, workspace, Level.MEMBER)
    assert invoke(kind, actor, organization, workspace, target) == 200
    assert invoke(kind, actor, organization, workspace, actor) in (400, 403)
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_ADMIN
    )
    assert (
        OrganizationMembership.no_workspace_objects.get(
            user=actor, organization=organization
        ).level
        == actor_level
    )


@pytest.mark.parametrize("kind", ["http", "tool"])
@pytest.mark.parametrize("state", ["inactive", "deleted", "missing", "downgraded"])
def test_stale_administrator_cannot_bypass_current_membership(
    organization, workspace, kind, state
):
    actor, membership = principal(organization, workspace, Level.ADMIN)
    target, _ = principal(organization, workspace, Level.MEMBER)
    if state == "missing":
        membership.delete()
    else:
        updates = {
            "inactive": {"is_active": False},
            "deleted": {"deleted": True},
            "downgraded": {"level": Level.MEMBER, "role": "Member"},
        }[state]
        OrganizationMembership.no_workspace_objects.filter(pk=membership.pk).update(
            **updates
        )
    assert invoke(kind, actor, organization, workspace, target) in (400, 401, 403, 404)
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_MEMBER
    )


@pytest.mark.parametrize("kind", ["http", "tool"])
def test_secondary_org_admin_uses_current_explicit_membership(
    organization, workspace, user, kind
):
    from tfc.middleware.workspace_context import (
        clear_workspace_context,
        set_workspace_context,
    )

    other = Organization.objects.create(name="Secondary role org")
    other_ws = Workspace.no_workspace_objects.create(
        name="Secondary", organization=other, created_by=user
    )
    actor, _ = principal(organization, workspace, Level.VIEWER)
    secondary = OrganizationMembership.no_workspace_objects.create(
        user=actor, organization=other, role="Admin", level=Level.ADMIN, is_active=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=actor,
        workspace=other_ws,
        organization_membership=secondary,
        role="workspace_admin",
        level=Level.WORKSPACE_ADMIN,
        is_active=True,
    )
    target, _ = principal(other, other_ws, Level.MEMBER)
    set_workspace_context(workspace=workspace, organization=organization, user=actor)
    try:
        assert invoke(kind, actor, other, other_ws, target) == 200
    finally:
        clear_workspace_context()
    actor.refresh_from_db()
    assert (
        actor.organization_id == organization.pk and actor.organization_role == "Viewer"
    )
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=other_ws
        ).level
        == Level.WORKSPACE_ADMIN
    )
    assert not WorkspaceMembership.no_workspace_objects.filter(
        user=target, workspace=workspace
    ).exists()


@pytest.mark.parametrize("kind", ["http", "tool"])
@pytest.mark.parametrize("state", ["inactive", "foreign"])
def test_administrator_cannot_write_invalid_workspace(
    user, organization, workspace, kind, state
):
    target, _ = principal(organization, workspace, Level.MEMBER)
    other = (
        Organization.objects.create(name="Different role org")
        if state == "foreign"
        else organization
    )
    destination = Workspace.no_workspace_objects.create(
        name="Ineligible destination",
        organization=other,
        created_by=user,
        is_active=state != "inactive",
    )
    assert invoke(kind, user, organization, destination, target) in (400, 401, 403, 404)
    assert not WorkspaceMembership.no_workspace_objects.filter(
        user=target, workspace=destination
    ).exists()
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_MEMBER
    )


@pytest.mark.parametrize("role", ["Owner", "Admin", "Member", "Viewer"])
def test_workspace_tool_rejects_organization_roles(user, organization, workspace, role):
    target, _ = principal(organization, workspace, Level.MEMBER)
    assert invoke("tool", user, organization, workspace, target, role) == 403
    assert (
        OrganizationMembership.no_workspace_objects.get(
            user=target, organization=organization
        ).level
        == Level.MEMBER
    )
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_MEMBER
    )


@pytest.fixture
def actual_role_http(settings):
    from threading import Thread
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    from django.core.wsgi import get_wsgi_application
    from rest_framework.views import APIView

    assert APIView.initial.__name__ == "initial", (
        "Actual HTTP must not inject authentication or tenant context"
    )
    settings.ALLOWED_HOSTS = ["testserver", "127.0.0.1"]

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    server = make_server(
        "127.0.0.1", 0, get_wsgi_application(), handler_class=QuietHandler
    )
    worker = Thread(target=server.serve_forever)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(10)
    assert not worker.is_alive()


def actual_headers(actor, organization, workspace):
    _, token = credential(actor, AuthTokenType.ACCESS)
    return {
        "Authorization": "Bearer " + token,
        "X-Organization-Id": str(organization.pk),
        "X-Workspace-Id": str(workspace.pk),
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("query_name", ["workspace_id", "workspaceId"])
def test_actual_http_legacy_query_selects_requested_workspace(
    user, organization, workspace, actual_role_http, query_name
):
    import httpx

    target, membership = principal(organization, workspace, Level.MEMBER)
    other = Workspace.no_workspace_objects.create(
        name="Query destination", organization=organization, created_by=user
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=target,
        workspace=other,
        organization_membership=membership,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
    )
    # A legacy query client names its workspace only in the query. A different
    # X-Workspace-Id alongside it is a contradictory selection that
    # authentication may refuse; that is not the contract exercised here.
    headers = actual_headers(user, organization, workspace)
    del headers["X-Workspace-Id"]
    with httpx.Client(
        base_url=actual_role_http,
        trust_env=False,
        headers=headers,
    ) as client:
        response = client.post(
            "/accounts/user/role/update/",
            params={query_name: str(other.pk)},
            json={"user_id": str(target.pk), "new_role": "workspace_admin"},
        )
    assert response.status_code == 200, response.text
    assert (
        WorkspaceMembership.no_workspace_objects.get(user=target, workspace=other).level
        == Level.WORKSPACE_ADMIN
    )
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_MEMBER
    )


@pytest.mark.django_db(transaction=True)
def test_actual_http_grant_then_membership_revocation_denies_same_credential(
    user, organization, workspace, actual_role_http
):
    import httpx

    target, _ = principal(organization, workspace, Level.MEMBER)
    with httpx.Client(
        base_url=actual_role_http,
        trust_env=False,
        headers=actual_headers(user, organization, workspace),
    ) as client:
        payload = {
            "user_id": str(target.pk),
            "new_role": "workspace_admin",
            "workspace_id": str(workspace.pk),
        }
        granted = client.post("/accounts/user/role/update/", json=payload)
        assert granted.status_code == 200, granted.text
        OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=organization
        ).update(level=Level.MEMBER, role="Member")
        payload["new_role"] = "workspace_viewer"
        denied = client.post("/accounts/user/role/update/", json=payload)
        assert denied.status_code == 403, denied.text
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_ADMIN
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("query_name", ["workspace_id", "workspaceId"])
@pytest.mark.parametrize("selection", ["malformed", "foreign"])
def test_actual_http_invalid_query_workspace_never_changes_default(
    user, organization, workspace, actual_role_http, query_name, selection
):
    import httpx

    target, _ = principal(organization, workspace, Level.MEMBER)
    if selection == "foreign":
        foreign_org = Organization.objects.create(name="Foreign query scope")
        foreign = Workspace.no_workspace_objects.create(
            name="Foreign query workspace", organization=foreign_org, created_by=user
        )
        value = str(foreign.pk)
    else:
        value = "not-a-workspace-uuid"
    with httpx.Client(
        base_url=actual_role_http,
        trust_env=False,
        headers=actual_headers(user, organization, workspace),
    ) as client:
        response = client.post(
            "/accounts/user/role/update/",
            params={query_name: value},
            json={"user_id": str(target.pk), "new_role": "workspace_admin"},
        )
    assert response.status_code in (400, 403, 404), response.text
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_MEMBER
    )
    assert WorkspaceMembership.no_workspace_objects.filter(user=target).count() == 1


@pytest.mark.django_db(transaction=True)
def test_workspace_admin_revoked_before_locked_authority_cannot_grant(
    organization, workspace, mocker
):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections, connection
    from rest_framework.exceptions import PermissionDenied

    from accounts.services.legacy_role_assignments import assign_role

    actor, _ = principal(organization, workspace, Level.MEMBER)
    actor_ws = WorkspaceMembership.no_workspace_objects.get(
        user=actor, workspace=workspace
    )
    actor_ws.role = "workspace_admin"
    actor_ws.level = Level.WORKSPACE_ADMIN
    actor_ws.save(update_fields=["role", "level"])
    target, _ = principal(organization, workspace, Level.VIEWER)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        actor_pid = cursor.fetchone()[0]
    manager = WorkspaceMembership.no_workspace_objects
    original_lock = manager.select_for_update
    revoked = []

    def revoke_in_other_connection():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                assert cursor.fetchone()[0] != actor_pid
            return manager.filter(pk=actor_ws.pk).update(is_active=False)
        finally:
            close_old_connections()

    def before_locked_read(*args, **kwargs):
        if not revoked:
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert (
                    executor.submit(revoke_in_other_connection).result(timeout=20) == 1
                )
            revoked.append(True)
        return original_lock(*args, **kwargs)

    mocker.patch.object(manager, "select_for_update", side_effect=before_locked_read)
    with pytest.raises(PermissionDenied, match="Workspace administrator"):
        assign_role(
            actor=actor,
            organization=organization,
            target_user_id=target.pk,
            role="workspace_admin",
            workspace_id=workspace.pk,
        )
    assert revoked
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_MEMBER
    )


def post_as(actor, org, ws, path, body):
    """Send one authenticated request in ``org``/``ws`` the way ``invoke`` does."""
    from tfc.middleware.workspace_context import (
        clear_workspace_context,
        get_current_organization,
        get_current_user,
        get_current_workspace,
        set_workspace_context,
    )

    _, token = credential(actor, AuthTokenType.ACCESS)
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {token}",
        HTTP_X_ORGANIZATION_ID=str(org.pk),
        HTTP_X_WORKSPACE_ID=str(ws.pk),
    )
    saved = get_current_workspace(), get_current_organization(), get_current_user()
    clear_workspace_context()
    try:
        return client.post(path, body, format="json")
    finally:
        set_workspace_context(workspace=saved[0], organization=saved[1], user=saved[2])


def active_ws_levels(target, org):
    return {
        row.workspace_id: row.level_or_legacy
        for row in WorkspaceMembership.no_workspace_objects.filter(
            user=target, workspace__organization=org, is_active=True
        )
    }


@pytest.mark.parametrize("promoted", ["Admin", "Owner"])
@pytest.mark.parametrize("demoted", ["Member", "Viewer"])
def test_legacy_demotion_revokes_workspace_admin_rows_granted_by_promotion(
    user, organization, workspace, promoted, demoted
):
    second = Workspace.no_workspace_objects.create(
        name="Promotion-only workspace", organization=organization, created_by=user
    )
    target, _ = principal(organization, workspace, Level.MEMBER)
    assert active_ws_levels(target, organization) == {
        workspace.pk: Level.WORKSPACE_MEMBER
    }
    assert invoke("http", user, organization, workspace, target, promoted) == 200
    # The canonical writer makes an organization Admin/Owner a workspace admin
    # in every workspace of the organization.
    assert active_ws_levels(target, organization)[second.pk] == Level.WORKSPACE_ADMIN
    assert invoke("http", user, organization, workspace, target, demoted) == 200
    membership = OrganizationMembership.no_workspace_objects.get(
        user=target, organization=organization
    )
    assert membership.level == Level.STRING_TO_LEVEL[demoted]
    assert active_ws_levels(target, organization) == {
        workspace.pk: Level.get_default_ws_level(Level.STRING_TO_LEVEL[demoted])
    }


def test_legacy_demotion_of_administrator_without_workspace_is_refused(
    user, organization, workspace
):
    from rest_framework.exceptions import ValidationError

    from accounts.services.legacy_role_assignments import assign_role

    second = Workspace.no_workspace_objects.create(
        name="Promotion-only workspace", organization=organization, created_by=user
    )
    target, membership = principal(organization, workspace, Level.MEMBER)
    assign_role(
        actor=user,
        organization=organization,
        target_user_id=target.pk,
        role="Admin",
        workspace_id=workspace.pk,
    )
    with pytest.raises(ValidationError, match="Workspace context is required"):
        assign_role(
            actor=user,
            organization=organization,
            target_user_id=target.pk,
            role="Member",
        )
    membership.refresh_from_db()
    assert membership.level == Level.ADMIN
    assert active_ws_levels(target, organization) == {
        workspace.pk: Level.WORKSPACE_ADMIN,
        second.pk: Level.WORKSPACE_ADMIN,
    }


def test_legacy_non_admin_org_role_change_keeps_other_workspace_access(
    user, organization, workspace
):
    """Control: only a demotion out of Admin/Owner rewrites workspace access."""
    second = Workspace.no_workspace_objects.create(
        name="Existing second workspace", organization=organization, created_by=user
    )
    target, membership = principal(organization, workspace, Level.MEMBER)
    WorkspaceMembership.no_workspace_objects.create(
        user=target,
        workspace=second,
        organization_membership=membership,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    assert invoke("http", user, organization, workspace, target, "Viewer") == 200
    assert active_ws_levels(target, organization) == {
        workspace.pk: Level.WORKSPACE_VIEWER,
        second.pk: Level.WORKSPACE_MEMBER,
    }
    assert invoke("http", user, organization, workspace, target, "Admin") == 200
    assert active_ws_levels(target, organization) == {
        workspace.pk: Level.WORKSPACE_ADMIN,
        second.pk: Level.WORKSPACE_ADMIN,
    }


@pytest.mark.parametrize("endpoint", ["legacy", "canonical"])
def test_secondary_org_role_change_keeps_primary_org_owner_protected(user, endpoint):
    """A role change in organization B must not rewrite the profile role that
    organization A's legacy paths read as the user's A role."""
    org_a = Organization.objects.create(name="Primary org A")
    ws_a = Workspace.no_workspace_objects.create(
        name="A workspace", organization=org_a, created_by=user
    )
    org_b = Organization.objects.create(name="Secondary org B")
    ws_b = Workspace.no_workspace_objects.create(
        name="B workspace", organization=org_b, created_by=user
    )
    sole_owner, owner_a = principal(org_a, ws_a, Level.OWNER)
    admin_a, _ = principal(org_a, ws_a, Level.ADMIN)
    admin_b, _ = principal(org_b, ws_b, Level.ADMIN)
    member_b = OrganizationMembership.no_workspace_objects.create(
        user=sole_owner,
        organization=org_b,
        role="Member",
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=sole_owner,
        workspace=ws_b,
        organization_membership=member_b,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    if endpoint == "legacy":
        path = "/accounts/user/role/update/"
        body = {
            "user_id": str(sole_owner.pk),
            "workspace_id": str(ws_b.pk),
            "new_role": "Viewer",
        }
    else:
        path = "/accounts/organization/members/role/"
        body = {
            "user_id": str(sole_owner.pk),
            "org_level": Level.VIEWER,
            "workspace_access": [
                {"workspace_id": str(ws_b.pk), "level": Level.WORKSPACE_VIEWER}
            ],
        }
    changed = post_as(admin_b, org_b, ws_b, path, body)
    assert changed.status_code == 200, changed.content
    member_b.refresh_from_db()
    assert member_b.level == Level.VIEWER

    # Organization A's administrator still cannot deactivate A's sole owner.
    deactivated = post_as(
        admin_a,
        org_a,
        ws_a,
        "/accounts/user/deactivate/",
        {"user_id": str(sole_owner.pk)},
    )
    owner_a.refresh_from_db()
    sole_owner.refresh_from_db()
    assert deactivated.status_code == 400, deactivated.content
    assert owner_a.is_active
    assert sole_owner.is_active
    assert sole_owner.organization_id == org_a.pk
    assert sole_owner.organization_role == "Owner"


@pytest.mark.parametrize("kind", ["http", "tool"])
def test_workspace_admin_below_org_admin_cannot_grant_workspace_admin(
    user, organization, workspace, kind
):
    """Like the canonical workspace role writer (WS_ROLE_ASSIGN_FORBIDDEN), only
    an organization Admin or Owner may make an account a workspace admin."""
    actor, _ = principal(organization, workspace, Level.MEMBER)
    WorkspaceMembership.no_workspace_objects.filter(
        user=actor, workspace=workspace
    ).update(role="workspace_admin", level=Level.WORKSPACE_ADMIN)
    # The target is below the actor at organization level, so only the
    # workspace role decides.
    target, _ = principal(organization, workspace, Level.VIEWER)
    assert invoke(kind, actor, organization, workspace, target) == 403
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_MEMBER
    )
    # Control: the same workspace admin can still set lower workspace roles,
    # and an organization Admin can grant workspace admin.
    assert (
        invoke(kind, actor, organization, workspace, target, "workspace_viewer") == 200
    )
    admin, _ = principal(organization, workspace, Level.ADMIN)
    assert invoke(kind, admin, organization, workspace, target) == 200
    assert (
        WorkspaceMembership.no_workspace_objects.get(
            user=target, workspace=workspace
        ).level
        == Level.WORKSPACE_ADMIN
    )


@pytest.mark.parametrize("missing", ["workspace", "user"])
def test_role_update_keeps_resource_error_contract(user, organization, workspace, missing):
    target, _ = principal(organization, workspace, Level.MEMBER)
    workspace_id = uuid.uuid4() if missing == "workspace" else workspace.pk
    user_id = uuid.uuid4() if missing == "user" else target.pk
    role = "workspace_admin"
    response = post_as(user, organization, workspace, "/accounts/user/role/update/", {
        "workspace_id": str(workspace_id), "user_id": str(user_id), "new_role": role,
    })
    assert response.status_code == (400 if missing == "workspace" else 404)
    if missing == "user":
        assert response.json()["code"] == "not_found"
    else:
        assert response.json()["detail"] == "Invalid workspace_id or workspace not in your organization"
    result = AddWorkspaceMemberTool().execute(
        AddWorkspaceMemberInput(workspace_id=workspace_id, user_id=user_id, role=role),
        ToolContext(user=user, organization=organization, workspace=workspace),
    )
    assert result.error_code == "NOT_FOUND"
    assert missing.capitalize() in result.content


def test_workspace_role_update_keeps_result_fields(user, organization, workspace):
    target, _ = principal(organization, workspace, Level.MEMBER)
    response = post_as(user, organization, workspace, "/accounts/user/role/update/", {
        "workspace_id": str(workspace.pk), "user_id": str(target.pk), "new_role": "workspace_admin",
    })
    assert response.status_code == 200
    assert response.json()["result"] == {
        "message": "User workspace role updated successfully",
        "user_id": str(target.pk), "new_role": "workspace_admin",
        "workspace": workspace.name, "level": "workspace",
    }


@pytest.mark.parametrize("deleted", [True, False])
def test_workspace_member_tool_keeps_action_description(user, organization, workspace, deleted):
    target, _ = principal(organization, workspace, Level.MEMBER)
    if deleted:
        WorkspaceMembership._base_manager.filter(user=target, workspace=workspace).update(
            deleted=True, is_active=False
        )
    result = AddWorkspaceMemberTool().execute(
        AddWorkspaceMemberInput(workspace_id=workspace.pk, user_id=target.pk, role="workspace_admin"),
        ToolContext(user=user, organization=organization, workspace=workspace),
    )
    assert not result.is_error
    assert result.data["action"] == ("re-added" if deleted else "updated (role changed from workspace_member)")
