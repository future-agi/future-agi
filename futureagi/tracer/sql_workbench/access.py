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


def authorize(actor: Actor, project_id, *, write=False):
    if not all((actor.user_id, actor.organization_id, actor.workspace_id, project_id)):
        raise AccessDenied("An active organization and workspace are required.")
    org = OrganizationMembership.no_workspace_objects.filter(
        user_id=actor.user_id,
        user__is_active=True,
        organization_id=actor.organization_id,
        is_active=True,
    ).first()
    membership = WorkspaceMembership.no_workspace_objects.filter(
        user_id=actor.user_id,
        workspace_id=actor.workspace_id,
        workspace__organization_id=actor.organization_id,
        workspace__is_active=True,
        workspace__deleted=False,
        is_active=True,
    ).first()
    if org is None or membership is None:
        raise AccessDenied("Project not available.")
    required = Level.MEMBER if write else Level.VIEWER
    # An organization viewer cannot gain write access from a stale workspace role.
    if min(org.level_or_legacy, membership.level_or_legacy) < required:
        raise AccessDenied("Your role does not allow this action.")
    project = Project.no_workspace_objects.filter(
        id=project_id,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        trace_type="observe",
    ).first()
    if project is None:
        raise AccessDenied("Project not available.")
    return project
