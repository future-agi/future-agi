from rest_framework import serializers

from model_hub.models.choices import ScoreSource
from model_hub.models.score import SCORE_SOURCE_FK_MAP, Score
from tracer.serializers.filters import StrictInputSerializer


class ScoreSerializer(serializers.ModelSerializer):
    """Read serializer for Score — used in list/detail responses."""

    label_id = serializers.UUIDField(source="label.id", read_only=True)
    label_name = serializers.CharField(source="label.name", read_only=True)
    label_type = serializers.CharField(source="label.type", read_only=True)
    label_settings = serializers.JSONField(source="label.settings", read_only=True)
    label_allow_notes = serializers.BooleanField(
        source="label.allow_notes", read_only=True
    )
    annotator_name = serializers.CharField(
        source="annotator.name", read_only=True, default=None
    )
    annotator_email = serializers.CharField(
        source="annotator.email", read_only=True, default=None
    )
    source_id = serializers.SerializerMethodField()
    queue_id = serializers.SerializerMethodField()
    # Declared explicitly so the default surfaces in the OpenAPI schema (the
    # frontend reads it off the contract); keep in sync with Score.score_source.
    score_source = serializers.ChoiceField(
        choices=ScoreSource.get_choices(), default=ScoreSource.HUMAN.value
    )


    class Meta:
        model = Score
        fields = [
            "id",
            "source_type",
            "source_id",
            "label_id",
            "label_name",
            "label_type",
            "label_settings",
            "label_allow_notes",
            "value",
            "value_history",
            "score_source",
            "notes",
            "annotator",
            "annotator_name",
            "annotator_email",
            "queue_item",
            "queue_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["annotator", "queue_item"]

    def get_source_id(self, obj):
        return str(obj.get_source_id()) if obj.get_source_id() else None

    def get_queue_id(self, obj):
        return str(obj.queue_item.queue_id) if obj.queue_item_id else None


class CreateScoreSerializer(StrictInputSerializer):
    """Write serializer for creating/updating scores."""

    source_type = serializers.ChoiceField(
        choices=list(SCORE_SOURCE_FK_MAP.keys()),
    )
    # CharField because some sources (e.g. ObservationSpan) use non-UUID IDs
    source_id = serializers.CharField()
    label_id = serializers.UUIDField()
    value = serializers.JSONField()
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    score_source = serializers.ChoiceField(
        choices=ScoreSource.get_choices(),
        required=False,
        default=ScoreSource.HUMAN.value,
    )
    # Optional explicit queue context. When omitted, the view falls back to
    # the source's default queue (auto-created if missing) so every Score
    # row has a non-null queue_item — required by the new (source, label,
    # annotator, queue_item) uniqueness.
    queue_item_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class UpdateScoreSerializer(StrictInputSerializer):
    """Strict write serializer for generated score PUT/PATCH routes."""

    value = serializers.JSONField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    score_source = serializers.ChoiceField(
        choices=ScoreSource.get_choices(),
        required=False,
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "At least one of value, notes, or score_source is required."
            )
        return attrs


class ScoreListQuerySerializer(StrictInputSerializer):
    source_type = serializers.ChoiceField(
        choices=list(SCORE_SOURCE_FK_MAP.keys()),
        required=False,
    )
    source_id = serializers.CharField(required=False, allow_blank=True)
    label_id = serializers.UUIDField(required=False)
    annotator_id = serializers.UUIDField(required=False)


class ScoreForSourceQuerySerializer(StrictInputSerializer):
    source_type = serializers.ChoiceField(choices=list(SCORE_SOURCE_FK_MAP.keys()))
    source_id = serializers.CharField()


class ScoreResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ScoreSerializer()


class BulkCreateScoresResultSerializer(serializers.Serializer):
    scores = ScoreSerializer(many=True)
    errors = serializers.ListField(child=serializers.CharField())


class BulkCreateScoresResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = BulkCreateScoresResultSerializer()


class ScoreForSourceResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ScoreSerializer(many=True)
    span_notes = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
    )


class ScoreDeleteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.DictField(child=serializers.BooleanField())


class BulkCreateScoreItemSerializer(StrictInputSerializer):
    label_id = serializers.UUIDField()
    value = serializers.JSONField()
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    score_source = serializers.ChoiceField(
        choices=ScoreSource.get_choices(),
        required=False,
        default=ScoreSource.HUMAN.value,
    )


class BulkCreateScoresSerializer(StrictInputSerializer):
    """Write serializer for creating multiple scores at once (e.g. inline annotator)."""

    source_type = serializers.ChoiceField(
        choices=list(SCORE_SOURCE_FK_MAP.keys()),
    )
    # CharField because some sources (e.g. ObservationSpan) use non-UUID IDs
    source_id = serializers.CharField()
    scores = BulkCreateScoreItemSerializer(many=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    span_notes = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    span_notes_source_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    # Optional explicit queue context — same rationale as in CreateScoreSerializer.
    queue_item_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate_scores(self, value):
        if not value:
            raise serializers.ValidationError("At least one score is required.")
        return value
