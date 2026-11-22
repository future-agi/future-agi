"""Authorize legacy HTTP/tool role requests using current scoped memberships."""

from django.db import transaction
from django.db.models import Q
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.member_role_service import (
    MemberRoleUpdateError,
    update_member_role,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.error_codes import get_error_message

ORG_ROLES = {
    str(role)
    for role in (
        OrganizationRoles.OWNER,
        OrganizationRoles.ADMIN,
        OrganizationRoles.MEMBER,
        OrganizationRoles.MEMBER_VIEW_ONLY,
    )
}
WS_ROLES = {"workspace_admin", "workspace_member", "workspace_viewer"}


class RoleWorkspaceNotFound(NotFound):
    default_detail = "Workspace not found in organization"


@transaction.atomic
def assign_role(
    *,
    actor,
    organization,
    target_user_id,
    role,
    workspace_id=None,
    workspace_only=False,
):
    if (
        organization is None
        or actor is None
        or not User.objects.filter(pk=actor.pk, is_active=True).exists()
    ):
        raise PermissionDenied("An active organization membership is required.")
    if role not in (WS_ROLES if workspace_only else ORG_ROLES | WS_ROLES):
        raise ValidationError("Invalid role.")
    # Lock order matches the canonical writers (update_member_role, member
    # deactivation): this organization's membership rows first (of=self, in
    # membership-pk order), then the User rows of the members found, in
    # user-pk order. Memberships of different organizations are disjoint and
    # User rows are taken in one global order, so no cycle can form within or
    # across organizations, and a user outside the organization is never locked.
    memberships = {
        str(member.user_id): member
        for member in OrganizationMembership.no_workspace_objects.select_for_update(
            of=("self",)
        )
        .filter(
            user_id__in=[actor.pk, target_user_id],
            organization=organization,
        )
        .order_by("pk")
    }
    users = {
        str(user.pk): user
        for user in User.objects.select_for_update()
        .filter(pk__in=[member.user_id for member in memberships.values()])
        .order_by("pk")
    }
    for user_id in list(memberships):
        # The active-user recheck below reads the locked User rows; a member
        # whose User row is not visible is treated as absent.
        if user_id in users:
            memberships[user_id].user = users[user_id]
        else:
            del memberships[user_id]
    actor_membership = memberships.get(str(actor.pk))
    if (
        actor_membership is None
        or not actor_membership.is_active
        or not actor_membership.user.is_active
    ):
        raise PermissionDenied("An active organization membership is required.")
    actor_level = actor_membership.level_or_legacy
    new_level = Level.from_string(role)
    workspace = None
    if workspace_id:
        workspace = Workspace.no_workspace_objects.filter(
            pk=workspace_id,
            organization=organization,
            is_active=True,
        ).first()
        if workspace is None:
            raise RoleWorkspaceNotFound()

    actor_ws = None
    if role in ORG_ROLES:
        if actor_level < Level.ADMIN or new_level > actor_level:
            raise PermissionDenied(
                "Only an authorized organization administrator can assign this role."
            )
        kwargs = {"org_level": new_level}
        if workspace is not None:
            kwargs.update(
                ws_level=Level.get_default_ws_level(new_level),
                workspace_id=workspace.pk,
            )
    else:
        if workspace is None:
            raise ValidationError(
                "Workspace context is required for workspace-level roles"
            )
        if actor_level < Level.ADMIN:
            actor_ws = (
                WorkspaceMembership.no_workspace_objects.select_for_update()
                .filter(
                    Q(organization_membership=actor_membership)
                    | Q(organization_membership__isnull=True),
                    user=actor,
                    workspace=workspace,
                    is_active=True,
                )
                .first()
            )
            # As in the canonical workspace role writer, a workspace admin
            # below organization Admin may only assign roles below its own;
            # only an organization Admin or Owner grants workspace admin.
            if (
                actor_ws is None
                or actor_ws.level_or_legacy < Level.WORKSPACE_ADMIN
                or new_level >= actor_ws.level_or_legacy
            ):
                raise PermissionDenied("Workspace administrator access is required.")
        kwargs = {"ws_level": new_level, "workspace_id": workspace.pk}

    if str(actor.pk) == str(target_user_id):
        raise ValidationError("Cannot change your own role")
    target = memberships.get(str(target_user_id))
    if target is None:
        raise NotFound("User not found in organization")
    # Match the canonical role-update API's target-user hierarchy.
    if actor_level < Level.OWNER and actor_level <= target.level_or_legacy:
        raise PermissionDenied("You cannot manage a user at or above your own level.")
    if role in ORG_ROLES and new_level < Level.ADMIN <= target.level_or_legacy:
        # Promotion to Admin/Owner made the target a workspace admin in every
        # workspace of the organization. As in the Members dialog, a demotion
        # names the workspaces it keeps, so every other workspace is revoked;
        # without a workspace there is nothing to keep, and the request is refused.
        if workspace is None:
            raise ValidationError(
                "Workspace context is required to demote an organization administrator"
            )
        kwargs.update(
            workspace_access=[
                {"workspace_id": workspace.pk, "level": kwargs["ws_level"]}
            ],
            workspace_access_provided=True,
        )
    if actor_ws is not None:
        target_ws = (
            WorkspaceMembership.no_workspace_objects.select_for_update()
            .filter(
                user=target.user,
                workspace=workspace,
                is_active=True,
            )
            .first()
        )
        if target_ws and target_ws.level_or_legacy >= actor_ws.level_or_legacy:
            raise PermissionDenied(
                "Only an organization administrator can manage workspace administrators."
            )
    if workspace_only:
        # The tool reports whether it added, restored or updated a membership.
        # Capture the previous display value under the organization-member locks.
        previous = (
            WorkspaceMembership._base_manager.filter(user=target.user, workspace=workspace)
            .values("deleted", "role")
            .first()
        )
    try:
        changes = update_member_role(
            organization=organization,
            actor=actor,
            target_user_id=target.user_id,
            **kwargs,
        )
    except MemberRoleUpdateError as exc:
        if exc.status_code == 403:
            raise PermissionDenied(get_error_message(exc.code)) from exc
        raise ValidationError(get_error_message(exc.code)) from exc
    if workspace_only:
        if previous is None:
            changes["action"] = "added"
        elif previous["deleted"]:
            changes["action"] = "re-added"
        else:
            changes["action"] = f"updated (role changed from {previous['role']})"
    return target.user, workspace, changes
