"""Request and response shapes for project tags and tag assignments."""

from rest_framework import serializers

from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.serializer_fields import StringOrObjectField

TAG_ERROR_RESPONSES = {
    400: ApiErrorResponseSerializer,
    503: ApiErrorResponseSerializer,
}

_TAGS_HELP = (
    'Replaces the current tags. Each item is a name ("prod") or '
    '{"name": "prod", "color": "#22C55E"}. A colour only seeds a tag the '
    "project does not have yet (or has without a colour) and is never stored "
    "on the assignment; recolour with PATCH /tracer/project/{id}/tag-definitions/."
)
_COLOR_HELP = '"#RRGGBB", or "" when no colour is set.'


def _tag_items(value):
    from tracer.services.project_tags import parse_tag_items

    try:
        return parse_tag_items(value)
    except ValueError as exc:
        raise serializers.ValidationError(str(exc)) from exc


def _color(value):
    from tracer.services.project_tags import is_valid_tag_color

    if value and not is_valid_tag_color(value):
        raise serializers.ValidationError(
            f'invalid tag color {value!r}; use "#RRGGBB" or "" to clear it'
        )
    return value


class TagAssignmentRequestSerializer(serializers.Serializer):
    tags = serializers.ListField(
        child=StringOrObjectField(), allow_empty=True, help_text=_TAGS_HELP
    )

    def validate_tags(self, value):
        return _tag_items(value)


class TraceTagsUpdateQuerySerializer(serializers.Serializer):
    project_id = serializers.UUIDField(
        required=False,
        help_text=(
            "The trace's project. Optional; needed only when the trace id "
            "exists in more than one project you can access."
        ),
    )


class SpanTagsUpdateRequestSerializer(TagAssignmentRequestSerializer):
    span_id = serializers.CharField(max_length=255)
    project_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text=(
            "The span's project. Optional; needed only when the span id "
            "exists in more than one project you can access."
        ),
    )


class TagAssignmentResultSerializer(serializers.Serializer):
    id = serializers.CharField()
    tags = serializers.ListField(
        child=serializers.CharField(), help_text="The names now assigned."
    )


class TagAssignmentResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = TagAssignmentResultSerializer()


class ProjectTagDefinitionSerializer(serializers.Serializer):
    name = serializers.CharField()
    color = serializers.CharField(allow_blank=True, help_text=_COLOR_HELP)
    usage_count = serializers.IntegerField(
        help_text=(
            "Live spans in this project whose tags contain the name, once per "
            "span. A trace tag sits on the trace's root span, so it counts once; "
            "a name seen only on a trace row counts 0."
        )
    )


class ProjectTagDefinitionListResultSerializer(serializers.Serializer):
    tags = ProjectTagDefinitionSerializer(many=True)


class ProjectTagDefinitionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ProjectTagDefinitionListResultSerializer()


class ProjectTagDefinitionUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=255,
        trim_whitespace=False,
        help_text="The tag to change, exactly as listed (not trimmed).",
    )
    new_name = serializers.CharField(
        max_length=255,
        required=False,
        help_text=(
            "Rename. Rewrites every assignment in the project; renaming onto "
            "an existing tag merges into it."
        ),
    )
    color = serializers.CharField(
        max_length=7,
        required=False,
        allow_blank=True,
        help_text=f"Recolour. {_COLOR_HELP} Assignments are not touched.",
    )

    def validate_color(self, value):
        return _color(value)

    def validate(self, attrs):
        if "new_name" not in attrs and "color" not in attrs:
            raise serializers.ValidationError(
                'Nothing to change: send "new_name", "color", or both, e.g. '
                '{"name": "prod", "new_name": "production"}.'
            )
        return attrs


class ProjectTagDefinitionResultSerializer(serializers.Serializer):
    name = serializers.CharField()
    color = serializers.CharField(allow_blank=True, help_text=_COLOR_HELP)


class ProjectTagDefinitionUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ProjectTagDefinitionResultSerializer()


class ProjectTagDefinitionDeleteQuerySerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=255,
        trim_whitespace=False,
        help_text=(
            "The tag to delete, exactly as listed (not trimmed). It is also "
            "removed from every assignment."
        ),
    )


class ProjectTagDefinitionDeleteResultSerializer(serializers.Serializer):
    deleted = serializers.BooleanField()


class ProjectTagDefinitionDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ProjectTagDefinitionDeleteResultSerializer()
