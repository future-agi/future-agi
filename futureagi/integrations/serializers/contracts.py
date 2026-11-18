from rest_framework import serializers

from integrations.serializers.integration_connection import (
    IntegrationConnectionDetailSerializer,
    IntegrationConnectionListSerializer,
)
from integrations.serializers.sync_log import SyncLogSerializer
from tfc.utils.api_serializers import (
    ApiErrorResponseSerializer,
    EmptyRequestSerializer,
    PaginationMetadataSerializer,
    StrictInputSerializer,
)


class IntegrationErrorResponseSerializer(ApiErrorResponseSerializer):
    """Integration API error envelope; kept named for generated API docs."""


class IntegrationEmptyRequestSerializer(EmptyRequestSerializer):
    """No-body integration action request."""


class IntegrationConnectionListQuerySerializer(StrictInputSerializer):
    page_number = serializers.IntegerField(required=False, min_value=0, default=0)
    page_size = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=100,
        default=20,
    )


class SyncLogListQuerySerializer(IntegrationConnectionListQuerySerializer):
    connection_id = serializers.UUIDField(required=False)


class IntegrationConnectionListResultSerializer(serializers.Serializer):
    metadata = PaginationMetadataSerializer()
    connections = IntegrationConnectionListSerializer(many=True)


class IntegrationConnectionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = IntegrationConnectionListResultSerializer()


class IntegrationConnectionDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = IntegrationConnectionDetailSerializer()


class IntegrationValidationProjectSerializer(serializers.Serializer):
    id = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    name = serializers.CharField(allow_blank=True, allow_null=True, required=False)


class IntegrationValidationViewerSerializer(serializers.Serializer):
    id = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    name = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    email = serializers.EmailField(allow_blank=True, allow_null=True, required=False)


class IntegrationValidationResultSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    projects = IntegrationValidationProjectSerializer(many=True, required=False)
    total_traces = serializers.IntegerField(required=False, min_value=0)
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    viewer = IntegrationValidationViewerSerializer(required=False)


class IntegrationValidationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = IntegrationValidationResultSerializer()


class IntegrationMessageResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class IntegrationMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = IntegrationMessageResultSerializer()


class SyncLogListResultSerializer(serializers.Serializer):
    metadata = PaginationMetadataSerializer()
    sync_logs = SyncLogSerializer(many=True)


class SyncLogListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = SyncLogListResultSerializer()


INTEGRATION_ERROR_RESPONSES = {
    400: IntegrationErrorResponseSerializer,
    404: IntegrationErrorResponseSerializer,
    500: IntegrationErrorResponseSerializer,
}

INTEGRATION_SYNC_ERROR_RESPONSES = {
    **INTEGRATION_ERROR_RESPONSES,
    409: IntegrationErrorResponseSerializer,
}
