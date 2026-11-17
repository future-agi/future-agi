import json

from django.contrib import admin

from ee.falcon_ai.models import (
    Conversation,
    FalconFile,
    FalconMemory,
    FalconUsage,
    MCPConnector,
    Message,
    Skill,
)
from ee.falcon_ai.snapshot_results import scrub_message_tool_calls


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "user", "organization", "workspace", "created_at")
    list_filter = ("organization", "created_at")
    search_fields = ("title", "user__email")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "conversation",
        "role",
        "feedback",
        "token_count",
        "created_at",
    )
    list_filter = ("role", "feedback", "created_at")
    search_fields = ("content",)
    # Never bind the stored result JSON to a form, including on submitted POSTs.
    fields = (
        "deleted",
        "deleted_at",
        "conversation",
        "role",
        "content",
        "thoughts",
        "display_tool_calls",
        "completion_card",
        "files",
        "feedback",
        "token_count",
        "input_tokens",
        "output_tokens",
        "model_used",
        "latency_ms",
        "id",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("id", "created_at", "updated_at", "display_tool_calls")

    @admin.display(description=Message._meta.get_field("tool_calls").verbose_name)
    def display_tool_calls(self, obj):
        # Return ordinary text: Django escapes it at the read-only HTML boundary.
        return json.dumps(scrub_message_tool_calls(obj.tool_calls if obj else []))


@admin.register(FalconUsage)
class FalconUsageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "conversation",
        "user",
        "model_used",
        "input_tokens",
        "output_tokens",
        "tool_calls_count",
        "created_at",
    )
    list_filter = ("model_used", "created_at")
    readonly_fields = ("id", "created_at")


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "slug",
        "is_builtin",
        "is_active",
        "organization",
        "created_at",
    )
    list_filter = ("is_builtin", "is_active", "organization")
    search_fields = ("name", "slug", "description")
    readonly_fields = ("id", "created_at", "updated_at")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(FalconMemory)
class FalconMemoryAdmin(admin.ModelAdmin):
    list_display = ("id", "key", "value_preview", "source", "workspace", "created_at")
    list_filter = ("source", "workspace", "created_at")
    search_fields = ("key", "value")
    readonly_fields = ("id", "created_at", "updated_at")

    def value_preview(self, obj):
        return obj.value[:80] if obj.value else ""

    value_preview.short_description = "Value"


@admin.register(FalconFile)
class FalconFileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "size",
        "content_type",
        "user",
        "organization",
        "created_at",
    )
    list_filter = ("content_type", "created_at")
    search_fields = ("name", "storage_key")
    readonly_fields = ("id", "created_at")


@admin.register(MCPConnector)
class MCPConnectorAdmin(admin.ModelAdmin):
    exclude = (
        "auth_header_value",
        "oauth_client_secret",
        "oauth_access_token",
        "oauth_refresh_token",
        "oauth_code_verifier",
        "oauth_state",
    )
    list_display = (
        "id",
        "name",
        "server_url",
        "transport",
        "auth_type",
        "is_active",
        "is_verified",
        "workspace",
        "created_at",
    )
    list_filter = ("transport", "auth_type", "is_active", "is_verified", "created_at")
    search_fields = ("name", "server_url")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "credential_generation",
        "server_url",
        "transport",
        "auth_type",
        "auth_header_name",
        "auth_header_value",
        "oauth_client_id",
        "oauth_client_secret",
        "oauth_server_metadata",
        "oauth_access_token",
        "oauth_refresh_token",
        "oauth_token_expires_at",
        "oauth_code_verifier",
        "oauth_state",
        "is_verified",
    )
    # Readonly credential columns are NOT rendered either (including ciphertext).
    fields = (
        "id",
        "name",
        "is_active",
        "server_url",
        "is_verified",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        from django.core.exceptions import PermissionDenied

        from ee.falcon_ai.oauth_registration import save_connector

        if not change:
            raise PermissionDenied("Connector creation requires the connector API")
        save_connector(
            obj,
            {
                name: getattr(obj, name)
                for name in ("name", "is_active")
                if name in form.changed_data
            },
            fresh_metadata=True,
        )

    def delete_queryset(self, request, queryset):
        from ee.falcon_ai.disconnect import disconnect_mcp

        for connector in queryset.iterator():
            disconnect_mcp(connector)
