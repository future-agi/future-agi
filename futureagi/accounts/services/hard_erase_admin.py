"""Truthful admin refusal without replacing Django's permission checks."""

from django.db import router, transaction
from django.http import HttpResponseForbidden

from accounts.services.hard_erase import HardEraseRefused


class HardEraseAdminMixin:
    def delete_model(self, request, obj):
        # Model.delete -> the common Collector boundary, not a receiver.
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # delete_selected passes the entire batch; the collector refuses it as
        # one transaction rather than looping over and partly deleting targets.
        return super().delete_queryset(request, queryset)

    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(request, object_id, extra_context)
        except HardEraseRefused as exc:
            return HttpResponseForbidden(str(exc), content_type="text/plain")

    def changelist_view(self, request, extra_context=None):
        try:
            # delete_selected writes its admin LogEntry BEFORE delete_queryset.
            # Roll that back too, rather than recording a deletion we refused.
            with transaction.atomic(using=router.db_for_write(self.model)):
                return super().changelist_view(request, extra_context)
        except HardEraseRefused as exc:
            return HttpResponseForbidden(str(exc), content_type="text/plain")
