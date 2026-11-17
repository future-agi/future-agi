import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level


@pytest.mark.django_db
@pytest.mark.parametrize("linked", [True, False], ids=["linked", "legacy"])
def test_org_removal_covers_other_workspaces_without_changing_another_org(
    auth_client, user, organization, workspace, linked
):
    member = User.objects.create_user(
        email="workspace-removal@example.test",
        password="synthetic-test-password",
        name="Workspace member",
        organization=organization,
        organization_role="Member",
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=member, organization=organization, role="Member", level=Level.MEMBER
    )
    second = Workspace.no_workspace_objects.create(
        name="Second workspace", organization=organization, created_by=user
    )
    other_org = Organization.objects.create(name="Another organization")
    other_membership = OrganizationMembership.no_workspace_objects.create(
        user=member, organization=other_org, role="Member", level=Level.MEMBER
    )
    other_workspace = Workspace.no_workspace_objects.create(
        name="Other organization workspace", organization=other_org, created_by=user
    )
    first_row = WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=workspace,
        organization_membership=membership,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
    )
    second_row = WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=second,
        organization_membership=membership if linked else None,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
    )
    other_row = WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=other_workspace,
        organization_membership=other_membership,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
    )

    response = auth_client.delete(
        "/accounts/organization/members/remove/",
        {"user_id": str(member.id)},
        format="json",
    )
    assert response.status_code == 200
    states = dict(
        WorkspaceMembership.no_workspace_objects.filter(
            pk__in=[first_row.pk, second_row.pk, other_row.pk]
        ).values_list("pk", "is_active")
    )
    assert states == {first_row.pk: False, second_row.pk: False, other_row.pk: True}
    membership.refresh_from_db()
    other_membership.refresh_from_db()
    assert membership.is_active is False
    assert other_membership.is_active is True
