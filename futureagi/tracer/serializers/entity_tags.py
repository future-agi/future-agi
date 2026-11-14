"""Scoped mutation contracts. An ID alone is never a tag-write selector."""

from rest_framework import serializers

from tfc.utils.api_serializers import ApiErrorResponseSerializer, StrictInputSerializer
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.serializer_fields import StringOrObjectField
from tracer.serializers.observation_span import (
    _SpanReferenceTimestampField,
    _SpanReferenceVersionField,
)
from tracer.services.entity_tags import (
    MAX_MANUAL_TAGS,
    TagInputError,
    normalize_manual_tags,
)

TARGET_FIELDS = (
    "project_id",
    "trace_id",
    "span_id",
    "start_hour",
    "observation_type",
    "service_name",
    "expected_start_time",
    "expected_version",
)


class _IdentityField(serializers.CharField):
    def __init__(self, **kwargs):
        super().__init__(trim_whitespace=False, **kwargs)

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class TagItemField(StringOrObjectField):
    class Meta:
        swagger_schema_fields = {
            "x-string-or-object": True,
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "maxLength": 256},
                "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
            },
            "additionalProperties": False,
            "description": "Exact tag name, or {name, color}; color is an optional six-digit hex value.",
        }


class EntityTagsUpdateSerializer(StrictInputSerializer):
    tags = serializers.ListField(
        child=TagItemField(), max_length=MAX_MANUAL_TAGS, allow_empty=True
    )
    project_id = serializers.UUIDField()
    trace_id = _IdentityField(max_length=128)
    span_id = _IdentityField(max_length=128)
    start_hour = _SpanReferenceTimestampField()
    observation_type = _IdentityField(max_length=64, allow_blank=True)
    service_name = _IdentityField(max_length=255, allow_blank=True)
    expected_start_time = _SpanReferenceTimestampField()
    expected_version = _SpanReferenceVersionField(trim_whitespace=False)
    expected_tag_revision = serializers.IntegerField(
        min_value=0,
        max_value=2**53 - 2,
        help_text="0 for no assignment; otherwise the last returned tag_revision.",
    )

    def to_internal_value(self, data):
        if isinstance(data, dict):
            missing = set(TARGET_FIELDS) | {"expected_tag_revision"}
            missing -= data.keys()
            if missing:
                raise serializers.ValidationError(
                    {
                        name: serializers.ErrorDetail(
                            "tag_scope_required: refetch the selected entity and supply its complete tag target and revision.",
                            code="tag_scope_required",
                        )
                        for name in sorted(missing)
                    }
                )
        return super().to_internal_value(data)

    def validate_tags(self, value):
        try:
            return normalize_manual_tags(value)
        except TagInputError as exc:
            raise serializers.ValidationError(str(exc)) from None

    def validate_expected_tag_revision(self, value):
        # IntegerField accepts numeric strings/floats. Revisions are JSON integers.
        if type(self.initial_data.get("expected_tag_revision")) is not int:
            raise serializers.ValidationError("Expected a JSON integer revision.")
        return value

    def validate(self, attrs):
        hour = attrs["start_hour"]
        if hour.minute or hour.second or hour.microsecond:
            raise serializers.ValidationError(
                {"start_hour": "Must be aligned to a UTC hour."}
            )
        if (
            attrs["expected_start_time"].replace(minute=0, second=0, microsecond=0)
            != hour
        ):
            raise serializers.ValidationError(
                {"expected_start_time": "Must belong to start_hour."}
            )
        return attrs


class TraceTagsUpdateSerializer(EntityTagsUpdateSerializer):
    """The reference must identify the selected root of the URL trace."""


class SpanTagsUpdateSerializer(EntityTagsUpdateSerializer):
    """A verified root uses the trace assignment; other spans use physical keys."""


class TagItemSerializer(serializers.Serializer):
    name = serializers.CharField(trim_whitespace=False)
    color = serializers.RegexField(r"^#[0-9a-fA-F]{6}$", required=False)


class EntityTagsResultSerializer(serializers.Serializer):
    id = serializers.CharField()
    tags = TagItemSerializer(many=True)
    manual_tags = TagItemSerializer(many=True)
    source_tag_names = serializers.ListField(
        child=serializers.CharField(trim_whitespace=False)
    )
    tag_revision = serializers.IntegerField(min_value=1)


class EntityTagsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = EntityTagsResultSerializer()


TAG_UPDATE_RESPONSES = {
    200: EntityTagsResponseSerializer,
    **dict.fromkeys((400, 403, 404, 409, 503), ApiErrorResponseSerializer),
}


def tag_validation_error_response(errors):
    missing_scope = isinstance(errors, dict) and any(
        getattr(error, "code", None) == "tag_scope_required"
        for value in errors.values()
        for error in (value if isinstance(value, list) else [value])
    )
    return GeneralMethods().custom_error_response(
        400, errors, code="tag_scope_required" if missing_scope else "validation_error"
    )
