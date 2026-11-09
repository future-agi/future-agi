from django.contrib import admin

from agentcc.models import (
    AgentccAPIKey,
    AgentccBlocklist,
    AgentccCustomPropertySchema,
    AgentccGuardrailFeedback,
    AgentccGuardrailPolicy,
    AgentccProject,
    AgentccRequestLog,
    AgentccRoutingPolicy,
    AgentccSession,
    AgentccWebhook,
    AgentccWebhookEvent,
)
from agentcc.services.webhook_credential_updates import update_webhook
from agentcc.services.webhook_delivery import shown_delivery_error


@admin.register(AgentccProject)
class AgentccProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(AgentccAPIKey)
class AgentccAPIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "status", "key_prefix", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "owner", "key_prefix")
    readonly_fields = ("user", "organization", "workspace", "created_at", "updated_at")

    def has_add_permission(self, request):
        # Keys are issued only through the creating person's session flow.
        return False


@admin.register(AgentccRequestLog)
class AgentccRequestLogAdmin(admin.ModelAdmin):
    list_display = (
        "request_id",
        "model",
        "provider",
        "status_code",
        "latency_ms",
        "started_at",
    )
    list_filter = ("is_error", "is_stream", "provider")
    search_fields = ("request_id", "model", "provider")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AgentccGuardrailPolicy)
class AgentccGuardrailPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "scope", "mode", "is_active", "priority", "created_at")
    list_filter = ("scope", "mode", "is_active")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AgentccBlocklist)
class AgentccBlocklistAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AgentccGuardrailFeedback)
class AgentccGuardrailFeedbackAdmin(admin.ModelAdmin):
    list_display = ("check_name", "feedback", "created_by", "created_at")
    list_filter = ("feedback", "check_name")
    search_fields = ("check_name", "comment")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AgentccSession)
class AgentccSessionAdmin(admin.ModelAdmin):
    list_display = ("session_id", "name", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("session_id", "name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AgentccWebhook)
class AgentccWebhookAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "url")
    fields = (
        "id",
        "organization",
        "name",
        "url",
        "events",
        "is_active",
        "description",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("id", "organization", "url", "created_at", "updated_at")

    def has_add_permission(self, request):
        # Credentials and destination changes use the authorized API boundary.
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            return
        # Never let a stale admin instance save context, storage or internal state.
        update_webhook(
            obj,
            {
                name: form.cleaned_data[name]
                for name in ("name", "events", "is_active", "description")
                if name in form.changed_data
            },
        )


@admin.register(AgentccWebhookEvent)
class AgentccWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "webhook", "status", "attempts", "created_at")
    list_filter = ("status", "event_type")
    search_fields = ("event_type",)
    fields = (
        "deleted",
        "deleted_at",
        "organization",
        "webhook",
        "event_type",
        "payload",
        "status",
        "attempts",
        "max_attempts",
        "last_attempt_at",
        "last_response_code",
        "display_last_error",
        "next_retry_at",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at", "display_last_error")

    @admin.display(
        description=AgentccWebhookEvent._meta.get_field("last_error").verbose_name
    )
    def display_last_error(self, obj):
        return shown_delivery_error(obj.last_error)


@admin.register(AgentccCustomPropertySchema)
class AgentccCustomPropertySchemaAdmin(admin.ModelAdmin):
    list_display = ("name", "property_type", "required", "created_at")
    list_filter = ("property_type", "required")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AgentccRoutingPolicy)
class AgentccRoutingPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")
