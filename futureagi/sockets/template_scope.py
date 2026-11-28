"""Workspace authorization for relay targets that identify prompt templates."""

from uuid import UUID

from channels.db import database_sync_to_async


@database_sync_to_async
def template_workspace_access(user_id, organization_id, target: UUID) -> bool | None:
    """None means a non-template UUID; those keep the relay's organization scope."""
    from accounts.models import User, Workspace
    from model_hub.models.run_prompt import PromptTemplate

    template = (
        PromptTemplate._base_manager.filter(pk=target)
        .select_related("workspace__organization")
        .first()
    )
    if template is None:
        return None
    if template.deleted or str(template.organization_id) != str(organization_id):
        return False
    workspace = template.workspace
    if workspace is None:
        # As in the HTTP workspace filter, legacy unassigned templates belong
        # to the organization's default workspace, not every workspace.
        workspace = Workspace.no_workspace_objects.filter(
            organization_id=organization_id, is_default=True
        ).first()
    if (
        workspace is None
        or workspace.deleted
        or not workspace.is_active
        or str(workspace.organization_id) != str(organization_id)
    ):
        return False
    # User caches role decisions per request; never reuse a socket-age cache.
    user = User.objects.filter(pk=user_id, is_active=True).first()
    return user is not None and user.can_access_workspace(workspace)
