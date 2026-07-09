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
from tracer.serializers.filters import (
    SortParamListQueryParamField,
    StrictInputSerializer,
    eval_task_filters_field,
    filter_list_query_param_field,
)


class PaginationQuerySerializer(serializers.Serializer):
    """Shared query-params validator for eval-log endpoints."""

    page = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(required=False, default=25, min_value=1)

    def validate_page_size(self, value):
        return min(value, 100)


class EvalTaskListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    name = serializers.CharField(required=False, allow_blank=True)
    filters = filter_list_query_param_field(required=False, default=list)
    sort_params = SortParamListQueryParamField(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False, default=30, min_value=1, max_value=500
    )


class EvalTaskListWithProjectNameQuerySerializer(EvalTaskListQuerySerializer):
    page_size = serializers.IntegerField(
        required=False, default=10, min_value=1, max_value=500
    )


class EvalTaskIdQuerySerializer(StrictInputSerializer):
    eval_task_id = serializers.UUIDField(required=True)


class EvalTaskDeleteRequestSerializer(StrictInputSerializer):
    eval_task_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=True,
    )


class EvalTaskCreateResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()


class EvalTaskCreateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = EvalTaskCreateResultSerializer()


class EvalTaskMessageResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class EvalTaskMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = EvalTaskMessageResultSerializer()


class EvalTaskUpdateResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    edit_type = serializers.ChoiceField(
        choices=[("edit_rerun", "edit_rerun"), ("fresh_run", "fresh_run")]
    )
    task_id = serializers.UUIDField()


class EvalTaskUpdateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = EvalTaskUpdateResultSerializer()


class EvalTaskSerializer(serializers.ModelSerializer):
    """An evaluation task that runs one or more configured evals over a trace
    project's spans/traces. Create it to start evaluation; read it (with a live
    ``progress`` block) to track completion. ``run_type`` is the key choice:
    ``historical`` evaluates the existing matching spans once and then completes;
    ``continuous`` keeps evaluating new spans as they arrive and never
    auto-completes.
    """

    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(),
        many=False,
        help_text="UUID of the trace project to evaluate (from list_projects).",
    )
    evals = serializers.PrimaryKeyRelatedField(
        queryset=CustomEvalConfig.objects.all(),
        many=True,
        help_text=(
            "List of custom_eval_config UUIDs to run on the project "
            "(from list_custom_eval_configs)."
        ),
    )
    name = serializers.CharField(
        min_length=1,
        max_length=255,
        help_text="Human-readable name for this eval task.",
    )
    sampling_rate = serializers.FloatField(
        min_value=1.0,
        max_value=100.0,
        help_text="Percentage (1-100) of matching spans to evaluate.",
    )
    spans_limit = serializers.IntegerField(
        min_value=1,
        max_value=1000000,
        required=False,
        allow_null=True,
        help_text=(
            "Max number of spans to evaluate. Required for a 'historical' run "
            "(it bounds the one-time pass)."
        ),
    )
    run_type = serializers.ChoiceField(
        choices=RunType.choices,
        help_text=(
            "'historical' = evaluate the existing matching spans once, then the "
            "task completes; 'continuous' = keep evaluating new spans as they "
            "arrive (runs indefinitely, never auto-completes)."
        ),
    )
    row_type = serializers.ChoiceField(
        choices=RowType.choices,
        required=False,
        default=RowType.SPANS,
        help_text="Unit of evaluation: 'spans' (default), 'traces', or 'sessions'.",
    )
    # Progress block so the UI can render an "X of Y complete" bar while a
    # historical task is draining. Not persisted — computed on read from the
    # task's entry status counts. ``None`` for continuous tasks, which run
    # indefinitely and don't have a meaningful "expected" total.
    progress = serializers.SerializerMethodField()
    filters = eval_task_filters_field(required=False, allow_null=True, default=dict)

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
        from tracer.selectors.eval_tasks.progress import count_by_status

        counts = count_by_status(obj)
        done = (
            counts.get("completed", 0)
            + counts.get("errored", 0)
            + counts.get("skipped", 0)
        )
        remaining = counts.get("pending", 0) + counts.get("running", 0)
        total = done + remaining
        percent = round(100.0 * done / total, 2) if total else None
        return {
            "dispatched": total,
            "completed": done,
            "missing": remaining,
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
        fields = ["id", "eval_task", "status", "errors"]


class EditEvalTaskSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=False, allow_blank=False, min_length=1, max_length=255
    )
    filters = eval_task_filters_field(required=False, allow_null=True)
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
        if not value:
            raise serializers.ValidationError("At least one eval config is required.")
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


class EvalTaskUpdateRequestSerializer(EditEvalTaskSerializer):
    eval_task_id = serializers.UUIDField(required=True)
