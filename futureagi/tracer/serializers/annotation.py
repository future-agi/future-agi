from rest_framework import serializers

from model_hub.models.develop_annotations import AnnotationsLabels
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace
from tracer.models.trace_annotation import TraceAnnotation


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


class GetTraceAnnotationSerializer(serializers.Serializer):
    observation_span_id = serializers.CharField(
        required=False, max_length=255, allow_null=True
    )
    trace_id = serializers.UUIDField(required=False, allow_null=True)
    annotators = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_null=True
    )
    exclude_annotators = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_null=True
    )


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


# Simple bulk annotation serializer
class BulkAnnotationSerializer(serializers.Serializer):
    records = serializers.ListField(child=serializers.DictField())


class BulkAnnotationAnnotationRequestSerializer(serializers.Serializer):
    annotation_label_id = serializers.UUIDField()
    value = serializers.CharField(required=False, allow_blank=True)
    value_float = serializers.FloatField(required=False)
    value_bool = serializers.BooleanField(required=False)
    value_str_list = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )


class BulkAnnotationNoteRequestSerializer(serializers.Serializer):
    text = serializers.CharField()


class BulkAnnotationRecordRequestSerializer(serializers.Serializer):
    observation_span_id = serializers.CharField()
    annotations = BulkAnnotationAnnotationRequestSerializer(
        many=True,
        required=False,
    )
    notes = BulkAnnotationNoteRequestSerializer(many=True, required=False)


class BulkAnnotationRequestSerializer(serializers.Serializer):
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
