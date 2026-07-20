from rest_framework import serializers

from tfc.utils.api_errors import API_ERROR_TYPE_CHOICES
from tfc.utils.serializer_fields import JsonValueField


class ApiSuccessResponseSerializer(serializers.Serializer):
    """Common GeneralMethods success response envelope."""

    status = serializers.BooleanField(default=True)
    result = serializers.JSONField(required=False, allow_null=True)


class StrictInputMixin:
    """Reject unknown request fields so API aliases cannot drift in silently."""

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            unknown = sorted(set(data.keys()) - set(self.fields.keys()))
            if unknown:
                raise serializers.ValidationError(
                    {key: ["Unknown field."] for key in unknown}
                )
        return super().to_internal_value(data)


class StrictInputSerializer(StrictInputMixin, serializers.Serializer):
    """Base serializer for strict request/query contracts."""


class StrictInputModelSerializer(StrictInputMixin, serializers.ModelSerializer):
    """ModelSerializer variant for strict request/query contracts."""


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
            {
                serializers.api_settings.NON_FIELD_ERRORS_KEY: [
                    "This endpoint does not accept a request body."
                ]
            }
        )


class ApiErrorObjectSerializer(serializers.Serializer):
    """Structured nested error object for errors that need extra context."""

    code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    detail = serializers.DictField(
        child=JsonValueField(),
        required=False,
        allow_empty=True,
    )


class ApiErrorResponseSerializer(serializers.Serializer):
    """Common GeneralMethods error response envelope."""

    status = serializers.BooleanField(default=False)
    type = serializers.ChoiceField(
        choices=API_ERROR_TYPE_CHOICES,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    detail = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    result = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    attr = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    details = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        allow_empty=True,
    )


class ApiTextErrorResponseSerializer(serializers.Serializer):
    """GeneralMethods error envelope for string-only failures."""

    status = serializers.BooleanField(default=False)
    type = serializers.ChoiceField(
        choices=API_ERROR_TYPE_CHOICES,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    detail = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    result = serializers.CharField(required=False, allow_null=True)
    message = serializers.CharField(required=False, allow_null=True)
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    attr = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    details = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        allow_empty=True,
    )


class ApiErrorWithDetailsResponseSerializer(ApiTextErrorResponseSerializer):
    """Typed mixed legacy error shape used while older endpoints are normalized."""

    code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    details = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        allow_empty=True,
    )


class ManagementAPIErrorResponseSerializer(serializers.Serializer):
    """Default typed error envelope for management API endpoints."""

    status = serializers.BooleanField(default=False, required=False)
    type = serializers.ChoiceField(
        choices=API_ERROR_TYPE_CHOICES,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    detail = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    result = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    attr = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    details = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        allow_empty=True,
    )


class ApiDetailErrorResponseSerializer(ApiTextErrorResponseSerializer):
    """DRF authentication/permission error envelope."""

    detail = serializers.CharField(required=True, allow_blank=True)


# The contracted 413 "a cap was exceeded" codes. Declared on the ``code`` field
# of the envelope below — the field the flat error actually carries the domain
# code on — so the FE typed client pattern-matches a schema value, not a literal.
TOO_LARGE_ERROR_CODES = ("export_too_large", "items_too_large")


class ApiTooLargeErrorSerializer(ApiTextErrorResponseSerializer):
    """413 cap-exceeded envelope with ``code`` narrowed to the contracted set."""

    code = serializers.ChoiceField(
        choices=TOO_LARGE_ERROR_CODES, required=False, allow_blank=True
    )


class ApiSelectionTooLargeDetailSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=("selection_too_large",))
    message = serializers.CharField()
    total_matching = serializers.IntegerField()
    cap = serializers.IntegerField()


class ApiSelectionTooLargeErrorSerializer(serializers.Serializer):
    """Bulk-selection cap error envelope used by annotation queue item imports."""

    status = serializers.BooleanField(default=False)
    result = serializers.CharField(required=False, allow_null=True)
    type = serializers.ChoiceField(
        choices=("selection_too_large",), required=False, allow_blank=True
    )
    code = serializers.CharField(required=False, default="selection_too_large")
    detail = serializers.CharField(required=False)
    message = serializers.CharField()
    error = ApiSelectionTooLargeDetailSerializer()


class PaginationMetadataSerializer(serializers.Serializer):
    """Common metadata shape for offset/page-number result envelopes."""

    total_count = serializers.IntegerField()
    current_page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    next_page = serializers.IntegerField(required=False, allow_null=True)


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


class CallWebsocketErrorResponseSerializer(ApiTextErrorResponseSerializer):
    status = serializers.BooleanField(default=False)
    result = serializers.CharField()
    message = serializers.CharField()
