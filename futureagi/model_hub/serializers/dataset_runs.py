"""Contracts for a dataset's experiment runs and its baseline pin (SRV-4)."""

from rest_framework import serializers

from model_hub.serializers.experiment_run_api import ExperimentRunItemCountsSerializer
from model_hub.services.dataset_runs import RUN_STATUSES
from tfc.utils.api_serializers import StrictInputSerializer


class DatasetRunsQuerySerializer(serializers.Serializer):
    status = serializers.CharField(
        required=False,
        default="",
        allow_blank=True,
        max_length=128,
        help_text=(
            "Comma-separated run statuses: draft, queued, running, partial, "
            "completed, failed, cancelled."
        ),
    )
    dataset_version_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Only runs on this version of the dataset.",
    )
    limit = serializers.IntegerField(default=50, min_value=1, max_value=200)
    offset = serializers.IntegerField(default=0, min_value=0, max_value=2147483647)

    def validate_status(self, value):
        statuses = [part.strip() for part in value.split(",") if part.strip()]
        unknown = sorted(set(statuses) - set(RUN_STATUSES))
        if unknown:
            raise serializers.ValidationError(
                f"Unknown run status: {', '.join(unknown)}."
            )
        return statuses


class DatasetBaselineSerializer(serializers.Serializer):
    baseline_run_id = serializers.UUIDField(allow_null=True)
    label = serializers.CharField(allow_null=True)
    pinned_by = serializers.CharField(
        allow_null=True, help_text="Name, else email, of who pinned it."
    )
    pinned_at = serializers.DateTimeField(allow_null=True)


class DatasetRunSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    ordinal = serializers.IntegerField(
        allow_null=True,
        help_text="1, 2, 3 ... per dataset in creation order; never reused.",
    )
    label = serializers.CharField(allow_null=True, help_text='"#" + ordinal.')
    name = serializers.CharField(
        allow_null=True,
        allow_blank=True,
        help_text="metadata.name as the caller sent it (the SDK sends one).",
    )
    status = serializers.ChoiceField(choices=list(RUN_STATUSES))
    task_kind = serializers.ChoiceField(choices=["prompt", "graph", "external"])
    dataset_version_id = serializers.UUIDField()
    experiment_id = serializers.UUIDField(allow_null=True)
    final = serializers.BooleanField(
        help_text="False when the caller marked the run non_final (a sample)."
    )
    is_baseline = serializers.BooleanField()
    plan_size = serializers.IntegerField()
    item_counts = ExperimentRunItemCountsSerializer()
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    app_url = serializers.CharField(
        allow_null=True,
        help_text=(
            "Link to this run in the app: the dataset's Experiments tab, "
            "whose run= parameter opens the run's panel with its traces, "
            "joined by run id. Null when the deployment sets no frontend URL."
        ),
    )


class DatasetRunsResultSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField()
    baseline = DatasetBaselineSerializer()
    runs = DatasetRunSerializer(many=True)
    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    has_more = serializers.BooleanField()


class DatasetRunsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetRunsResultSerializer()


class DatasetBaselinePinRequestSerializer(StrictInputSerializer):
    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    run_id = serializers.UUIDField()


class DatasetBaselineResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetBaselineSerializer()
