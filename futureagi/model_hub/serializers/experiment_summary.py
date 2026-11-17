from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from model_hub.models.experiment_summary import (
    ExperimentSummaryBinding,
    ExperimentSummaryResult,
)


class SummaryBindingCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    template_id = serializers.UUIDField()
    version_id = serializers.UUIDField()
    mapping = serializers.DictField(
        child=serializers.CharField(),
        required=False,
        default={"rows": "input.rows"},
        allow_empty=False,
    )


class SummaryBindingSerializer(serializers.ModelSerializer):
    version_id = serializers.UUIDField(read_only=True)
    template_id = serializers.UUIDField(source="version.eval_template_id")
    version_number = serializers.IntegerField(source="version.version_number")
    mapping = serializers.JSONField(source="definition.mapping", read_only=True)

    class Meta:
        model = ExperimentSummaryBinding
        fields = [
            "id",
            "name",
            "template_id",
            "version_id",
            "version_number",
            "mapping",
            "created_at",
        ]
        read_only_fields = fields


class SummaryBindingsSerializer(serializers.Serializer):
    bindings = SummaryBindingSerializer(many=True)


class SummaryBindingResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SummaryBindingSerializer()


class SummaryBindingsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SummaryBindingsSerializer()


class SummaryEvaluateSerializer(serializers.Serializer):
    binding_id = serializers.UUIDField()
    retry_failed = serializers.BooleanField(default=False)


class SummaryScoreSerializer(serializers.ModelSerializer):
    binding_id = serializers.UUIDField(read_only=True)
    run_id = serializers.UUIDField(read_only=True)
    version_id = serializers.UUIDField(read_only=True)
    version_number = serializers.IntegerField(source="version.version_number")
    name = serializers.CharField(source="binding.name")
    interrupted = serializers.SerializerMethodField()
    publication_pending = serializers.SerializerMethodField()

    def get_publication_pending(self, obj) -> bool:
        # A commit may survive without its publication callback ever running.
        return obj.status == "pending" and not obj.dispatched

    def get_interrupted(self, obj) -> bool:
        # Shared task limit is 3600s. Never turn an ambiguous claim into a retry.
        return bool(
            obj.status == "running"
            and obj.started_at
            and obj.started_at < timezone.now() - timedelta(seconds=3700)
        )

    class Meta:
        model = ExperimentSummaryResult
        fields = [
            "id",
            "binding_id",
            "run_id",
            "version_id",
            "version_number",
            "name",
            "status",
            "score",
            "reason",
            "metadata",
            "error_code",
            "attempt",
            "created_at",
            "started_at",
            "completed_at",
            "interrupted",
            "publication_pending",
        ]
        read_only_fields = fields


class SummaryScoresSerializer(serializers.Serializer):
    scores = SummaryScoreSerializer(many=True)


class SummaryScoreResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SummaryScoreSerializer()


class SummaryScoresResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SummaryScoresSerializer()
