from rest_framework import serializers

from tfc.utils.api_serializers import StrictInputSerializer


class OmegaObserveTargetQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()

    def to_internal_value(self, data):
        if hasattr(data, "getlist") and any(
            len(data.getlist(key)) != 1 for key in data
        ):
            raise serializers.ValidationError(
                "Query parameters must occur exactly once."
            )
        return super().to_internal_value(data)

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class OmegaObserveTargetResponseSerializer(serializers.Serializer):
    organizationId = serializers.UUIDField()
    workspaceId = serializers.UUIDField()
    projectId = serializers.UUIDField()
    projectName = serializers.CharField(max_length=255)
