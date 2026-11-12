"""Rehydrate only the requesting principal for delegated trace analysis.

There is no ambient/system-user fallback. Old queued jobs without a principal
fail closed; callers must dispatch again with their authenticated scope.
"""

from django.core.exceptions import ValidationError
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from accounts.models.user import User
from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from tracer.models.project import Project


def resolve_analysis_scope(
    project_id, requesting_user_id, organization_id, workspace_id
):
    """Load explicit IDs unscoped, then authorize before any data or agent effects."""
    denied = PermissionDenied(
        "The requested analysis workspace is unavailable.", code="workspace_unavailable"
    )
    if not all((project_id, requesting_user_id, organization_id, workspace_id)):
        raise denied
    try:
        user = User.objects.filter(pk=requesting_user_id, is_active=True).first()
        workspace = (
            Workspace._base_manager.select_related("organization")
            .filter(
                pk=workspace_id,
                organization_id=organization_id,
                is_active=True,
                deleted=False,
            )
            .first()
        )
        if (
            user is None
            or workspace is None
            or not user.can_access_workspace(workspace)
        ):
            raise denied
        if not user.can_write_to_workspace(workspace):
            raise PermissionDenied("Write access denied to this workspace")
        # A NULL-workspace legacy project belongs only to the default workspace;
        # a queued job cannot migrate to a newly selected landing or another org.
        scope = Q(workspace=workspace)
        if workspace.is_default:
            scope |= Q(workspace__isnull=True)
        project = Project._base_manager.filter(
            scope,
            pk=project_id,
            organization_id=organization_id,
            deleted=False,
        ).first()
        if project is None:
            raise denied
    except (ValidationError, ValueError, TypeError) as exc:
        raise denied from exc
    return project, ToolContext(user, workspace.organization, workspace)
