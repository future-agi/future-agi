from django.contrib import admin

from integrations.models import IntegrationConnection, SyncLog
from integrations.services.disconnect import disconnect_integration


@admin.register(IntegrationConnection)
class IntegrationConnectionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "platform",
        "display_name",
        "status",
        "external_project_name",
        "last_synced_at",
        "total_traces_synced",
        "created_at",
    )
    list_filter = ("platform", "status", "backfill_completed")
    search_fields = ("display_name", "external_project_name")
    readonly_fields = ("encrypted_credentials",)

    def delete_queryset(self, request, queryset):
        for connection in queryset.iterator():
            disconnect_integration(connection)


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "connection",
        "status",
        "started_at",
        "completed_at",
        "traces_fetched",
        "spans_synced",
        "scores_synced",
    )
    list_filter = ("status",)
    raw_id_fields = ("connection",)
