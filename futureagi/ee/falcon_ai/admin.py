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
    readonly_fields = ("id", "created_at", "updated_at")


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
    readonly_fields = ("id", "created_at", "updated_at")
