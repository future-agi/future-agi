"""Shared helpers for MCP usage tracking (used by both transport.py and mcp_app.py)."""

import json
import uuid

import structlog

from mcp_server.models.connection import MCPConnection
from mcp_server.models.session import MCPSession
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.models.usage import MCPUsageRecord

logger = structlog.get_logger(__name__)


def get_or_create_connection(user, organization, workspace):
    """Get or create an MCPConnection for the given user + workspace."""
    connection, _ = MCPConnection.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        deleted=False,
        defaults={"organization": organization, "connection_mode": "stdio"},
    )
    MCPToolGroupConfig.no_workspace_objects.get_or_create(connection=connection)
    return connection


def get_or_create_session(connection, session_id=None, transport="stdio"):
    """Get or create an MCPSession.

    For stateless transports (streamable_http), reuses the most recent active
    session for the same connection instead of creating a new one per request.
    A session is considered "current" if it was active in the last 30 minutes.
    """
    from datetime import timedelta

    from django.utils import timezone

    if session_id:
        try:
            session = MCPSession.objects.get(id=session_id, connection=connection)
            if session.status == "disconnected":
                session.status = "active"
                session.save(update_fields=["status", "last_activity_at"])
            return session
        except MCPSession.DoesNotExist:
            pass

    if transport == "streamable_http":
        # Stateless HTTP has no caller-provided session identifier, so reuse
        # the recent logical session instead of creating one for every POST.
        cutoff = timezone.now() - timedelta(minutes=30)
        recent = (
            MCPSession.objects.filter(
                connection=connection,
                transport=transport,
                status="active",
                last_activity_at__gte=cutoff,
            )
            .order_by("-last_activity_at")
            .first()
        )
        if recent:
            return recent

    return MCPSession.objects.create(
        connection=connection,
        user=connection.user,
        organization=connection.organization,
        workspace=connection.workspace,
        transport=transport,
    )


def get_enabled_tools(connection):
    """Get the set of enabled tool names for a connection."""
    from mcp_server.generated_registry import registry

    try:
        config = connection.tool_config
    except MCPToolGroupConfig.DoesNotExist:
        config, _ = MCPToolGroupConfig.no_workspace_objects.get_or_create(
            connection=connection
        )

    enabled_groups = config.enabled_groups
    disabled_tools = set(config.disabled_tools or [])

    enabled_tool_names = set()
    for tool in registry.list_all():
        if (
            tool.group in enabled_groups
            and tool.name not in disabled_tools
            and tool.is_available()
        ):
            enabled_tool_names.add(tool.name)

    return enabled_tool_names


class _UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, uuid.UUID):
            return str(obj)
        # Tool params may arrive as Pydantic model instances (FastMCP validates
        # nested fields like render_widget's WidgetConfig into model objects),
        # which json can't serialize natively.
        from pydantic import BaseModel as _PydanticBaseModel

        if isinstance(obj, _PydanticBaseModel):
            return obj.model_dump(mode="json")
        # Last-resort fallback so usage recording never crashes a tool call.
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


def _sanitize_params(params):
    """Serialize audit parameters without persisting provider credentials."""
    if params is None:
        return {}
    from tfc.logging.sentry import SENSITIVE_KEY_SUBSTRINGS

    def redact(value):
        if isinstance(value, dict):
            return {
                key: (
                    "[Filtered]"
                    if any(
                        part in key.lower().replace("-", "_")
                        for part in (
                            *SENSITIVE_KEY_SUBSTRINGS,
                            "credential",
                            "headers",
                            "config_json",
                        )
                    )
                    else redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    return redact(json.loads(json.dumps(params, cls=_UUIDEncoder)))


def record_usage(session, tool_name, tool_group, params, status, error_msg, latency_ms):
    """Record a tool call in MCPUsageRecord."""
    sanitized = _sanitize_params(params)
    # API/provider errors can echo input credentials. Keep these error details
    # in the caller's response, not the persistent analytics record.
    if error_msg and sanitized != json.loads(
        json.dumps(params or {}, cls=_UUIDEncoder)
    ):
        error_msg = (
            "Tool call failed; credential-bearing details omitted from audit log."
        )
    MCPUsageRecord.objects.create(
        session=session,
        organization=session.organization,
        workspace=session.workspace,
        user=session.user,
        tool_name=tool_name,
        tool_group=tool_group,
        request_params=sanitized,
        response_status=status,
        error_message=error_msg,
        latency_ms=latency_ms,
    )


def update_session_counters(session, is_error: bool):
    """Update session tool_call_count and error_count."""
    from django.db.models import F
    from django.utils import timezone

    MCPSession.objects.filter(pk=session.pk).update(
        tool_call_count=F("tool_call_count") + 1,
        error_count=F("error_count") + int(is_error),
        last_activity_at=timezone.now(),
    )
