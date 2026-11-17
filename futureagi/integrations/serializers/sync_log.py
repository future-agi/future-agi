from rest_framework import serializers

from integrations.models import SyncLog
from integrations.services.diagnostic_projection import public_sync_message
from integrations.services.export_failures import safe_details


class SyncLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyncLog
        fields = [
            "id",
            "connection",
            "status",
            "started_at",
            "completed_at",
            "traces_fetched",
            "traces_created",
            "traces_updated",
            "spans_synced",
            "scores_synced",
            "error_message",
            "error_details",
            "sync_from",
            "sync_to",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Historical rows may contain raw text in either field. Validate values
        # as well as keys and reconstruct the sentence without parsing raw text.
        data["error_details"] = safe_details(data.get("error_details"))
        data["error_message"] = public_sync_message(instance)
        return data
