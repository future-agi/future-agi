"""Sync-log reads must use the same membership boundary as connections."""

import pytest
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from accounts.services.token_service import issue_tokens
from integrations.models import SyncLog
from integrations.views.sync_log import SyncLogViewSet
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


@pytest.mark.django_db
def test_removed_member_cannot_read_historical_sync_log(
    auth_client, organization, workspace, integration_connection
):
    set_workspace_context(organization=organization)
    member = User.objects.create_user(
        email="removed-sync-reader@example.com",
        password="dummy-password",
        name="Removed reader",
        organization=organization,
        organization_role="Member",
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=organization,
        role="Member",
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=workspace,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=membership,
    )
    log = SyncLog.objects.create(
        connection=integration_connection, started_at=timezone.now(), status="failed"
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(member)['access']}")
    # Positive control with real token authentication.
    assert client.get(f"/integrations/sync-logs/{log.id}/").status_code == 200
    assert (
        auth_client.delete(
            "/accounts/organization/members/remove/",
            {"user_id": str(member.id)},
            format="json",
        ).status_code
        == 200
    )
    # Removal revokes the old access token. A still-active user can authenticate
    # again without organization access; a fresh token must not revive stale-FK reads.
    assert client.get(f"/integrations/sync-logs/{log.id}/").status_code == 401
    member.refresh_from_db()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(member)['access']}")
    assert client.get(f"/integrations/sync-logs/{log.id}/").status_code == 403
    assert client.get("/integrations/sync-logs/").status_code == 403


@pytest.mark.django_db
def test_no_workspace_does_not_expand_members_scope(
    organization, integration_connection
):
    set_workspace_context(organization=organization)
    member = User.objects.create_user(
        email="no-workspace-reader@example.com",
        password="dummy-password",
        name="No workspace",
        organization=organization,
        organization_role="Member",
    )
    OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=organization,
        role="Member",
        level=Level.MEMBER,
        is_active=True,
    )
    SyncLog.objects.create(
        connection=integration_connection, started_at=timezone.now(), status="success"
    )
    request = Request(APIRequestFactory().get("/integrations/sync-logs/"))
    request.user, request.organization, request.workspace = member, organization, None
    clear_workspace_context()
    set_workspace_context(workspace=None, organization=organization, user=member)
    view = SyncLogViewSet()
    view.request = request
    assert not view.get_queryset().exists()


@pytest.mark.django_db
def test_error_notification_uses_active_membership_and_safe_history(
    organization, integration_connection
):
    from unittest.mock import patch

    from integrations.temporal.activities import _send_error_notification

    set_workspace_context(organization=organization)
    receivers = []
    for index, active in enumerate([True, False]):
        person = User.objects.create_user(
            email=f"sync-admin-{index}@example.com",
            password="dummy-password",
            name="Notification reader",
            organization=organization,
            organization_role="Admin",
        )
        OrganizationMembership.no_workspace_objects.create(
            user=person,
            organization=organization,
            role="Admin",
            level=Level.ADMIN,
            is_active=active,
        )
        if active:
            receivers.append(person.email)
    # Isolate this fixture's recipient set without relying on the account fixture's role.
    OrganizationMembership.no_workspace_objects.filter(
        organization=organization
    ).exclude(user__email__in=receivers).update(is_active=False)
    integration_connection.status_message = "synthetic-private-notification-value"
    with patch("tfc.utils.email.email_helper") as send:
        _send_error_notification(integration_connection)
    assert send.call_count == 1
    assert send.call_args.args[3] == receivers
    assert "synthetic-private-notification-value" not in str(send.call_args.args[2])
