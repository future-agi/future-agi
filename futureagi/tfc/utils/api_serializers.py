from rest_framework import serializers


class ApiErrorResponseSerializer(serializers.Serializer):
    """Common GeneralMethods error response envelope."""

    status = serializers.BooleanField(default=False)
    result = serializers.JSONField(required=False, allow_null=True)
    message = serializers.JSONField(required=False, allow_null=True)


class ApiSelectionTooLargeErrorSerializer(serializers.Serializer):
    """Bulk-selection cap error envelope used by annotation queue item imports."""

    status = serializers.BooleanField(default=False)
    result = serializers.JSONField(required=False, allow_null=True)
    message = serializers.JSONField(required=False, allow_null=True)
    code = serializers.IntegerField(default=400)
    error = serializers.JSONField(required=False)
