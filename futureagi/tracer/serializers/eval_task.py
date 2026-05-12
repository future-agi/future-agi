from rest_framework import serializers

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import (
    EvalTask,
    EvalTaskLogger,
    EvalTaskStatus,
    RowType,
    RunType,
)
from tracer.models.project import Project


class PaginationQuerySerializer(serializers.Serializer):
    """Shared query-params validator for paginated eval-log endpoints.

    DRF's ``PageNumberPagination`` is 1-indexed; the FE state is 0-indexed,
    so consumers send ``page+1``. ``page_size`` is exposed under the
    paginator's ``page_size_query_param='limit'`` alias too — this
    serializer accepts either spelling to keep older FE callers working.
    """

    page = serializers.IntegerField(required=False, default=1, min_value=1)
    page_size = serializers.IntegerField(
        required=False, default=25, min_value=1, max_value=100
    )


class EvalTaskSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(), many=False
    )
    evals = serializers.PrimaryKeyRelatedField(
        queryset=CustomEvalConfig.objects.all(), many=True
    )
    name = serializers.CharField(min_length=1, max_length=255)
    sampling_rate = serializers.FloatField(min_value=1.0, max_value=100.0)
    spans_limit = serializers.IntegerField(
        min_value=1, max_value=1000000, required=False, allow_null=True
    )
    run_type = serializers.ChoiceField(choices=RunType.choices)
    row_type = serializers.ChoiceField(
        choices=RowType.choices,
        required=False,
        default=RowType.SPANS,
    )
    # Progress block so the UI can render an "X of Y complete" bar
    # while a historical task is draining. Not persisted — computed
    # on read from ``EvalTaskLogger.offset`` (dispatched) and the
    # live ``EvalLogger`` row count (completed). ``None`` for
    # continuous tasks, which run indefinitely and don't have a
    # meaningful "expected" total.
    progress = serializers.SerializerMethodField()

    class Meta:
        model = EvalTask
        fields = [
            "id",
            "project",
            "name",
            "filters",
            "sampling_rate",
            "last_run",
            "spans_limit",
            "run_type",
            "row_type",
            "status",
            "start_time",
            "end_time",
            "created_at",
            "updated_at",
            "evals_details",
            "evals",
            "failed_spans",
            "progress",
        ]

    def get_progress(self, obj):
        if obj.run_type != RunType.HISTORICAL:
            return None
        # Import locally to avoid a circular dependency: ``eval_tasks``
        # imports ``evaluate_observation_span_observe`` from
        # ``tracer.utils.eval``, which in turn imports serializers
        # transitively through the view layer.
        from tracer.utils.eval_tasks import compute_drain_state

        state = compute_drain_state(obj)
        dispatched = state["dispatched"]
        completed = state["completed"]
        percent = round(100.0 * completed / dispatched, 2) if dispatched else None
        return {
            "dispatched": dispatched,
            "completed": completed,
            "missing": state["missing"],
            "percent": percent,
        }

    def validate_evals(self, value):
        if not value:
            raise serializers.ValidationError("At least one eval config is required.")
        return value

    def validate(self, attrs):
        run_type = attrs.get("run_type")
        spans_limit = attrs.get("spans_limit")
        if run_type == RunType.HISTORICAL and not spans_limit:
            raise serializers.ValidationError(
                {"spans_limit": "spans_limit is required for historical runs."}
            )
        if run_type == RunType.CONTINUOUS:
            attrs.pop("spans_limit", None)
        return attrs


class EvalTaskLoggerSerializer(serializers.ModelSerializer):
    eval_task = serializers.PrimaryKeyRelatedField(
        queryset=EvalTask.objects.all(), many=False
    )

    class Meta:
        model = EvalTaskLogger
        fields = ["id", "eval_task", "offset", "status", "errors", "spanids_processed"]


class EditEvalTaskSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=False, allow_blank=False, min_length=1, max_length=255
    )
    filters = serializers.JSONField(required=False, allow_null=True)
    sampling_rate = serializers.FloatField(
        required=False, allow_null=True, min_value=1.0, max_value=100.0
    )
    spans_limit = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=1000000
    )
    run_type = serializers.ChoiceField(choices=RunType.choices, required=False)
    row_type = serializers.ChoiceField(choices=RowType.choices, required=False)
    status = serializers.ChoiceField(
        choices=[(tag.value, tag.name) for tag in EvalTaskStatus], required=False
    )
    evals = serializers.ListField(child=serializers.UUIDField(), required=False)
    edit_type = serializers.ChoiceField(
        choices=[("edit_rerun", "edit_rerun"), ("fresh_run", "fresh_run")],
        required=True,
    )

    def validate_row_type(self, value):
        raise serializers.ValidationError(
            "row_type cannot be changed after task creation. "
            "Create a new evaluation task with the desired row_type instead."
        )

    def validate_evals(self, value):
        try:
            eval_objects = list(
                CustomEvalConfig.objects.filter(id__in=value, deleted=False)
            )

            if len(eval_objects) != len(value):
                found_ids = [str(obj.id) for obj in eval_objects]
                missing_ids = [
                    str(uuid) for uuid in value if str(uuid) not in found_ids
                ]
                if missing_ids:
                    raise serializers.ValidationError(
                        f"Could not find eval configs with IDs: {', '.join(missing_ids)}"
                    )

            return value
        except Exception as e:
            raise serializers.ValidationError(
                f"Invalid eval config IDs: {str(e)}"
            ) from e
