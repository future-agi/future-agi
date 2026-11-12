"""No ambient workspace manager or mutable current-user preference is trusted."""

from dataclasses import dataclass
from uuid import UUID

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from tfc.constants.levels import Level
from tracer.models.project import Project


class AccessDenied(Exception):
    pass


@dataclass(frozen=True)
class Actor:
    user_id: UUID
    organization_id: UUID
    workspace_id: UUID


def authorize(actor: Actor, project_id: UUID | None, *, write: bool = False) -> Project:
    if not all((actor.user_id, actor.organization_id, actor.workspace_id, project_id)):
        raise AccessDenied("An active organization and workspace are required.")
    org = OrganizationMembership.no_workspace_objects.filter(
        user_id=actor.user_id,
        user__is_active=True,
        organization_id=actor.organization_id,
        is_active=True,
    ).first()
    if org is None:
        raise AccessDenied("Project not available.")
    # Match User.has_global_workspace_access/get_workspace_role: organization
    # admins outrank missing or stale workspace rows; reads never create a row.
    if org.level_or_legacy >= Level.ADMIN:
        workspace_level = Level.get_default_ws_level(org.level_or_legacy)
    else:
        membership = WorkspaceMembership.no_workspace_objects.filter(
            user_id=actor.user_id, workspace_id=actor.workspace_id, is_active=True
        ).first()
        if membership is None:
            raise AccessDenied("Project not available.")
        workspace_level = membership.level_or_legacy
    required = Level.MEMBER if write else Level.VIEWER
    # An organization viewer cannot gain write access from a stale workspace role.
    if min(org.level_or_legacy, workspace_level) < required:
        raise AccessDenied("Your role does not allow this action.")
    project = Project.no_workspace_objects.filter(
        id=project_id,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        workspace__organization_id=actor.organization_id,
        workspace__is_active=True,
        workspace__deleted=False,
        trace_type="observe",
    ).first()
    if project is None:
        raise AccessDenied("Project not available.")
    return project


def authorization_fingerprint(actor: Actor):
    """The membership rows the actor's access rests on, as comparable values.

    A role change, deactivation, re-creation or any saved edit of either row
    changes it, so a reused result never outlives the access it was read with.
    """

    def describe(row):
        if row is None:
            return None
        return [
            str(row.id),
            row.level,
            row.level_or_legacy,
            bool(row.is_active),
            row.updated_at.isoformat() if row.updated_at else None,
        ]

    org = (
        OrganizationMembership.no_workspace_objects.filter(
            user_id=actor.user_id, organization_id=actor.organization_id
        )
        .order_by("id")
        .first()
    )
    membership = (
        WorkspaceMembership.no_workspace_objects.filter(
            user_id=actor.user_id,
            workspace_id=actor.workspace_id,
            workspace__organization_id=actor.organization_id,
        )
        .order_by("id")
        .first()
    )
    return [describe(org), describe(membership)]
