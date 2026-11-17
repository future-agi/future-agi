from django.contrib import admin

from mcp_server.models.connection import MCPConnection
from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.models.session import MCPSession
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.models.usage import MCPUsageRecord
from mcp_server.usage_helpers import owned_connections


class _OwnerOnlyConnectionAdmin(admin.ModelAdmin):
    """Credential-bearing connections are not a tenant-wide staff directory.

    Even superusers see only their own tenant's rows. Management endpoints own
    writes; admin cannot reassign a connection or attach a foreign related row.
    """

    connection_path = ""

    def get_queryset(self, request):
        organization = getattr(request, "organization", None)
        connections = owned_connections(request.user, organization)
        if not self.connection_path:
            return connections.defer(
                "oauth_token_encrypted", "oauth_refresh_token_encrypted", "api_key"
            )
        return (
            super()
            .get_queryset(request)
            .filter(**{f"{self.connection_path}__in": connections.values("pk")})
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MCPConnection)
class MCPConnectionAdmin(_OwnerOnlyConnectionAdmin):
    list_display = [
        "id",
        "user",
        "organization",
        "workspace",
        "connection_mode",
        "is_active",
        "created_at",
    ]
    list_filter = ["connection_mode", "is_active"]
    search_fields = ["user__email", "organization__name"]
    raw_id_fields = ["user", "organization", "workspace"]
    exclude = ["oauth_token_encrypted", "oauth_refresh_token_encrypted", "api_key"]


@admin.register(MCPToolGroupConfig)
class MCPToolGroupConfigAdmin(_OwnerOnlyConnectionAdmin):
    connection_path = "connection"
    list_display = ["id", "connection", "enabled_groups"]
    raw_id_fields = ["connection"]


@admin.register(MCPSession)
class MCPSessionAdmin(_OwnerOnlyConnectionAdmin):
    connection_path = "connection"
    list_display = [
        "id",
        "user",
        "status",
        "transport",
        "tool_call_count",
        "started_at",
    ]
    list_filter = ["status", "transport"]
    search_fields = ["user__email"]
    raw_id_fields = ["connection", "user", "organization", "workspace"]


@admin.register(MCPUsageRecord)
class MCPUsageRecordAdmin(_OwnerOnlyConnectionAdmin):
    connection_path = "session__connection"
    list_display = ["id", "tool_name", "response_status", "latency_ms", "called_at"]
    list_filter = ["response_status", "tool_name"]
    search_fields = ["tool_name"]
    raw_id_fields = ["session", "organization", "workspace", "user"]


@admin.register(MCPOAuthClient)
class MCPOAuthClientAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "client_id", "is_active", "created_at"]
    list_filter = ["is_active"]
