from rest_framework import serializers


class ApiSuccessResponseSerializer(serializers.Serializer):
    """Common GeneralMethods success response envelope."""

    status = serializers.BooleanField(default=True)
    result = serializers.JSONField(required=False, allow_null=True)


class EmptyRequestSerializer(serializers.Serializer):
    """Explicit contract for mutation endpoints that accept no request body."""

    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def to_internal_value(self, data):
        if data is None or data == "":
            return {}
        if hasattr(data, "__len__") and len(data) == 0:
            return {}
        raise serializers.ValidationError(
            "This endpoint does not accept a request body."
        )


class ApiErrorResponseSerializer(serializers.Serializer):
    """Common GeneralMethods error response envelope."""

    status = serializers.BooleanField(default=False)
    result = serializers.JSONField(required=False, allow_null=True)
    message = serializers.JSONField(required=False, allow_null=True)
    error = serializers.JSONField(required=False, allow_null=True)


class ApiTextErrorResponseSerializer(serializers.Serializer):
    """GeneralMethods error envelope for string-only failures."""

    status = serializers.BooleanField(default=False)
    result = serializers.CharField(required=False, allow_null=True)
    message = serializers.CharField(required=False, allow_null=True)


class ApiErrorWithDetailsResponseSerializer(ApiTextErrorResponseSerializer):
    """Typed mixed legacy error shape used while older endpoints are normalized."""

    error = serializers.CharField(required=False, allow_null=True)
    details = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        allow_empty=True,
    )


class ApiDetailErrorResponseSerializer(serializers.Serializer):
    """DRF authentication/permission error envelope."""

    detail = serializers.CharField()


class ApiSelectionTooLargeErrorSerializer(serializers.Serializer):
    """Bulk-selection cap error envelope used by annotation queue item imports."""

    status = serializers.BooleanField(default=False)
    result = serializers.JSONField(required=False, allow_null=True)
    message = serializers.JSONField(required=False, allow_null=True)
    code = serializers.IntegerField(default=400)
    error = serializers.JSONField(required=False)


class HealthCheckResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.CharField()


class DeploymentInfoResultSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=("oss", "ee", "cloud"))


class DeploymentInfoResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = DeploymentInfoResultSerializer()


class LangfuseHealthResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("OK",))
    version = serializers.CharField()


class LangfuseTracesMetaSerializer(serializers.Serializer):
    page = serializers.IntegerField()
    limit = serializers.IntegerField()
    total_items = serializers.IntegerField()
    total_pages = serializers.IntegerField()


class LangfuseTracesResponseSerializer(serializers.Serializer):
    data = serializers.ListField(child=serializers.JSONField())
    meta = LangfuseTracesMetaSerializer()


class CallWebsocketRequestSerializer(serializers.Serializer):
    message = serializers.CharField()
    send_to_uuid = serializers.BooleanField(required=False, default=False)
    uuid = serializers.CharField(required=False, allow_blank=True)


class CallWebsocketResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.CharField()


class CallWebsocketErrorResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=False)
    result = serializers.CharField()
    message = serializers.CharField()
