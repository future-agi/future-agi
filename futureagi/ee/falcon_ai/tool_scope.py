"""Shared caller scope for Falcon discovery and delegated tool execution."""

from contextlib import contextmanager
from contextvars import ContextVar

from ai_tools.base import ToolContext
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
    get_current_workspace,
    set_workspace_context,
    workspace_context,
)

_background_analysis = ContextVar("falcon_background_analysis", default=None)


def current_tool_context():
    """Carry an authenticated caller into nested synchronous tool dispatch."""
    return ToolContext(
        get_current_user(),
        get_current_organization(),
        get_current_workspace(),
        background_analysis=_background_analysis.get(),
    )


@contextmanager
def authenticated_tool_scope(context):
    """Bind even an empty landing, inside the executing thread, then restore it."""
    with workspace_context(
        context.workspace, context.organization, getattr(context, "user", None)
    ):
        set_workspace_context(require_workspace=True)
        token = _background_analysis.set(getattr(context, "background_analysis", None))
        try:
            yield
        finally:
            _background_analysis.reset(token)


def workspace_access_allowed(context, workspace):
    """Use the canonical access check against current workspace/membership rows."""
    from accounts.models.workspace import Workspace

    if (
        getattr(context, "user", None) is None
        or context.organization is None
        or workspace is None
    ):
        return False
    current = Workspace.objects.filter(
        pk=workspace.pk, organization=context.organization, is_active=True
    ).first()
    return current is not None and context.user.can_access_workspace(current)


def connector_access_allowed(connector, context):
    """Discovery and every dispatch share the same live workspace decision."""
    return (
        context.workspace is not None
        and context.organization is not None
        and connector.organization_id == context.organization.pk
        and connector.workspace_id == context.workspace.pk
        and connector.is_active
        and connector.is_verified
        and workspace_access_allowed(context, connector.workspace)
    )


def accessible_connectors(context):
    """An absent caller or landing offers no external connector metadata."""
    from ee.falcon_ai.models import MCPConnector

    with authenticated_tool_scope(context):
        if context.user is None or context.organization is None:
            return []
        connectors = MCPConnector.objects.filter(
            organization=context.organization, is_active=True, is_verified=True
        ).select_related("workspace__organization")
        return [c for c in connectors if connector_access_allowed(c, context)]


def connector_for_execution(connector_id, tool_name, context):
    """Re-fetch a cached wrapper's connector and re-check access before I/O."""
    from ee.falcon_ai.models import MCPConnector

    guard = getattr(context, "background_analysis", None)
    if guard is not None:
        context = guard.check()
    with authenticated_tool_scope(context):
        connector = (
            MCPConnector.objects.select_related("workspace__organization")
            .filter(pk=connector_id)
            .first()
        )
        if connector is None or not connector_access_allowed(connector, context):
            if guard is not None:
                guard.abort()
            return None
        enabled = connector.enabled_tool_names or []
        discovered = connector.discovered_tools or []
        if (enabled and tool_name not in enabled) or not any(
            schema.get("name") == tool_name for schema in discovered
        ):
            if guard is not None:
                guard.abort()
            return None
        if guard is not None:
            guard.connectors.add((connector_id, tool_name))
        return connector
