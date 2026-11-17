"""Django admin: read-only on every Hub model (SPEC §10). Changes go through the maintainer API,
so they are checked and audited; a `hub_maintainer` grant made in admin is audited by signals.py."""

from django.contrib import admin

from omega_hub import models


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EmailOutboxAdmin(ReadOnlyAdmin):
    list_display = ("template", "status", "attempts", "created_at", "next_attempt_at", "dead_at")
    list_filter = ("status", "template")
    search_fields = ("subject", "body", "last_error")
    readonly_fields = tuple(field.name for field in models.EmailOutbox._meta.fields)


HUB_MODELS = (
    models.Publisher, models.Entry, models.Version, models.Submission, models.CheckJob, models.Like,
    models.DownloadDaily, models.Pick, models.Report, models.EntryRedirect, models.PublisherRedirect,
    models.BlockedDigest, models.HubToken, models.AuditEvent, models.EmailOutbox,
)

for model in HUB_MODELS:
    admin.site.register(model, EmailOutboxAdmin if model is models.EmailOutbox else ReadOnlyAdmin)
