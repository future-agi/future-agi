import json

from rest_framework import serializers

from model_hub.models.develop_annotations import AnnotationsLabels
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace
from tracer.models.trace_annotation import TraceAnnotation
from tracer.serializers.filters import StrictInputSerializer


class TraceAnnotationSerializer(serializers.ModelSerializer):
    trace = serializers.PrimaryKeyRelatedField(queryset=Trace.objects.all())
    annotation_label = serializers.PrimaryKeyRelatedField(
        queryset=AnnotationsLabels.objects.all()
    )

    class Meta:
        model = TraceAnnotation
        fields = [
            "id",
            "trace",
            "annotation_label",
            "annotation_value",
            "observation_span",
            "user",
            "annotation_value_bool",
            "annotation_value_float",
            "annotation_value_str_list",
        ]


class UUIDListQueryParamField(serializers.Field):
    class Meta:
        swagger_schema_fields = {
            "type": "string",
            "description": "JSON-encoded UUID list.",
        }

    def to_internal_value(self, data):
        if data in (None, ""):
            return []
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError("Value must be valid JSON.") from exc
        if not isinstance(data, list):
            raise serializers.ValidationError("Value must be a JSON array.")
        return serializers.ListField(child=serializers.UUIDField()).run_validation(data)

    def to_representation(self, value):
        return value or []


class GetTraceAnnotationSerializer(StrictInputSerializer):
    observation_span_id = serializers.CharField(
        required=False, max_length=255, allow_null=True
    )
    trace_id = serializers.UUIDField(required=False, allow_null=True)
    annotators = UUIDListQueryParamField(required=False, default=list)
    exclude_annotators = UUIDListQueryParamField(required=False, default=list)

    def validate(self, attrs):
        if not attrs.get("observation_span_id") and not attrs.get("trace_id"):
            raise serializers.ValidationError(
                "At least one of observation_span_id or trace_id is required"
            )
        return attrs


class SpanNotesSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpanNotes
        fields = [
            "id",
            "span",
            "notes",
            "created_by_user",
            "created_by_annotator",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TraceAnnotationValueResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    annotation_label_name = serializers.CharField()
    annotation_value = serializers.JSONField(allow_null=True)
    annotation_label_id = serializers.UUIDField()
    annotator = serializers.CharField(required=False, allow_null=True)
    annotator_id = serializers.UUIDField(required=False, allow_null=True)
    updated_by = serializers.CharField(required=False, allow_null=True)
    updated_at = serializers.DateTimeField(required=False, allow_null=True)
    annotation_type = serializers.CharField()
    settings = serializers.JSONField(required=False)


class TraceAnnotationNoteResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    notes = serializers.CharField(allow_blank=True)
    created_by_annotator = serializers.CharField()
    created_by_user = serializers.CharField()
    created_by_user_id = serializers.UUIDField()
    updated_at = serializers.DateTimeField()


class GetTraceAnnotationValuesResultSerializer(serializers.Serializer):
    annotations = TraceAnnotationValueResponseSerializer(many=True)
    notes = TraceAnnotationNoteResponseSerializer(many=True)


class GetTraceAnnotationValuesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = GetTraceAnnotationValuesResultSerializer()


class BulkAnnotationAnnotationRequestSerializer(StrictInputSerializer):
    annotation_label_id = serializers.UUIDField()
    value = serializers.CharField(required=False, allow_blank=True)
    value_float = serializers.FloatField(required=False)
    value_bool = serializers.BooleanField(required=False)
    value_str_list = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )


class BulkAnnotationNoteRequestSerializer(StrictInputSerializer):
    text = serializers.CharField()


class BulkAnnotationRecordRequestSerializer(StrictInputSerializer):
    observation_span_id = serializers.CharField()
    annotations = BulkAnnotationAnnotationRequestSerializer(
        many=True,
        required=False,
        default=list,
    )
    notes = BulkAnnotationNoteRequestSerializer(
        many=True,
        required=False,
        default=list,
    )


class BulkAnnotationRequestSerializer(StrictInputSerializer):
    records = BulkAnnotationRecordRequestSerializer(many=True)


class BulkAnnotationResponseResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    annotations_created = serializers.IntegerField()
    annotations_updated = serializers.IntegerField()
    notes_created = serializers.IntegerField()
    succeeded_count = serializers.IntegerField()
    errors_count = serializers.IntegerField()
    warnings_count = serializers.IntegerField()
    warnings = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        allow_null=True,
    )
    errors = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
        allow_null=True,
    )


class BulkAnnotationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = BulkAnnotationResponseResultSerializer()


class AnnotationLabelResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    type = serializers.CharField()
    description = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    settings = serializers.JSONField(required=False, allow_null=True)


class GetAnnotationLabelsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AnnotationLabelResponseSerializer(many=True)


class GetAnnotationLabelsQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
