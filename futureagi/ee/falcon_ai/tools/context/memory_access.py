"""Live writer authorization at the shared-memory mutation boundary."""

import logging

from ai_tools.base import ToolContext, ToolResult

logger = logging.getLogger(__name__)


def memory_write_denial(context: ToolContext) -> ToolResult | None:
    """Fail closed; cached admission and background status never grant writes."""
    from accounts.models.user import User
    from accounts.models.workspace import Workspace

    try:
        if (
            context.user is not None
            and context.organization is not None
            and context.workspace is not None
        ):
            # Fetch both objects anew: permission helpers must not run against
            # a cached principal or a workspace that moved organizations.
            current_user = User.objects.filter(
                pk=context.user.pk, is_active=True
            ).first()
            current_workspace = Workspace.objects.filter(
                pk=context.workspace.pk,
                organization_id=context.organization.pk,
                is_active=True,
            ).first()
            if (
                current_user is not None
                and current_workspace is not None
                and current_user.can_write_to_workspace(current_workspace)
            ):
                return None
    except Exception:
        # Authorization lookup failures must not fall through to an ORM write.
        logger.warning("Failed to verify Falcon memory write access")

    return ToolResult.permission_denied(
        "Workspace write access is required to change shared memories."
    )
