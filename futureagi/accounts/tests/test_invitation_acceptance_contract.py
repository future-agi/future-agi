"""Normal recipient acceptance preserves independent pending invitations."""
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from django.contrib.auth.models import AnonymousUser

from accounts.models.organization_invite import InviteStatus
from accounts.models.organization_membership import OrganizationMembership
from accounts.tests.test_invitation_boundary_contract import emailed_link, tenants
from accounts.views.signup import _accept_invitation_mail

pytestmark = pytest.mark.django_db


def accept(link, data, *, user=None, access=None):
    uid, token = urlsplit(link).path.rstrip("/").split("/")[-2:]
    return _accept_invitation_mail(SimpleNamespace(
        method="POST", headers={"Authorization": "Bearer " + access} if access else {},
        user=user or AnonymousUser(), validated_data=data,
    ), uid, token)


def test_recipient_accepts_second_org_then_first_without_password_reset(tenants):
    rows, user, invites = tenants
    first = emailed_link(user, rows[0])
    second = emailed_link(user, rows[1])
    response = accept(second, {"new_password": "Synthetic-invite-contract-782!", "repeat_password": "Synthetic-invite-contract-782!"})
    assert response.status_code == 200
    for invite in invites:
        invite.refresh_from_db()
    assert invites[1].status == InviteStatus.ACCEPTED
    assert invites[0].status == InviteStatus.PENDING
    user.refresh_from_db()
    saved_password = user.password
    assert user.config["selected_organization_id"] == str(rows[1].org.id)
    response = accept(first, {}, user=user, access=response.data["access"])
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.password == saved_password
    assert user.config["selected_organization_id"] == str(rows[0].org.id)
    assert OrganizationMembership.no_workspace_objects.filter(user=user, is_active=True).count() == 2


def test_successfully_accepted_invitation_is_one_time(tenants):
    rows, user, _ = tenants
    link = emailed_link(user, rows[0])
    fields = {"new_password": "Synthetic-invite-contract-782!", "repeat_password": "Synthetic-invite-contract-782!"}
    assert accept(link, fields).status_code == 200
    assert accept(link, fields).status_code == 400


def test_authenticated_recipient_accepts_grants_outside_current_workspace(tenants):
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.middleware.workspace_context import clear_workspace_context, set_workspace_context

    rows, user, invites = tenants
    first = emailed_link(user, rows[0])
    fields = {"new_password": "Synthetic-invite-contract-782!", "repeat_password": "Synthetic-invite-contract-782!"}
    response = accept(first, fields)
    assert response.status_code == 200
    user.refresh_from_db()
    invites[1].level = Level.MEMBER
    invites[1].workspace_access = [{"workspace_id": str(rows[1].workspace.id), "level": Level.WORKSPACE_MEMBER}]
    invites[1].save(update_fields=["level", "workspace_access"])
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=rows[1].org, level=Level.MEMBER, role="Member", is_active=False,
    )
    workspace_membership = WorkspaceMembership.no_workspace_objects.create(
        user=user, workspace=rows[1].workspace, organization_membership=membership,
        role="workspace_member", level=Level.WORKSPACE_MEMBER, is_active=False,
    )
    link = emailed_link(user, rows[1])
    set_workspace_context(organization=rows[0].org, workspace=rows[0].workspace)
    try:
        result = accept(link, {}, user=user, access=response.data["access"])
        assert result.status_code == 200
    finally:
        clear_workspace_context()
    workspace_membership.refresh_from_db()
    assert workspace_membership.is_active is True
    assert workspace_membership.workspace_id == rows[1].workspace.id
