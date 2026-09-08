from datetime import timedelta

from django.conf import settings
from rest_framework import serializers

from tfc.utils.serializer_fields import JsonValueField
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
from tracer.services.filter_principal_context import (
    FilterPrincipalContextError,
    bind_request_my_annotations_principal,
)

EVAL_TASK_USAGE_DEFAULT_PAGE_SIZE = settings.EVAL_TASK_USAGE_DEFAULT_PAGE_SIZE
EVAL_TASK_USAGE_MAX_PAGE_SIZE = settings.EVAL_TASK_USAGE_MAX_PAGE_SIZE
EVAL_TASK_USAGE_MAX_PAGE = settings.EVAL_TASK_USAGE_MAX_PAGE_NUMBER
EVAL_TASK_LIST_DEFAULT_PAGE_SIZE = settings.EVAL_TASK_LIST_DEFAULT_PAGE_SIZE
EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE = (
    settings.EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE
)


class PageSizeQuerySerializer(serializers.Serializer):
    """``page_size`` and its clamp, for endpoints that page.

    Split out from ``PaginationQuerySerializer`` so the usage contract can
    reuse the clamp without also re-declaring ``page`` — that one is owned by
    ``ExtendedPageNumberPagination``.
    """

    page_size = serializers.IntegerField(
        required=False,
        default=EVAL_TASK_USAGE_DEFAULT_PAGE_SIZE,
        min_value=1,
    )

    def validate_page_size(self, value):
        return min(value, EVAL_TASK_USAGE_MAX_PAGE_SIZE)


class EvalTaskUsageQuerySerializer(StrictInputSerializer):
    """Bounded query contract for the task usage chart and log page."""

    eval_task_id = serializers.UUIDField(required=True)
    period = serializers.ChoiceField(
        choices=("30m", "1h", "6h", "1d", "7d", "30d", "90d", "180d", "365d"),
        required=False,
        default="30d",
    )
    eval_id = serializers.UUIDField(required=False)
    page = serializers.IntegerField(
        required=False,
        default=1,
        min_value=1,
        max_value=EVAL_TASK_USAGE_MAX_PAGE,
    )
    page_size = serializers.IntegerField(
        required=False,
        default=EVAL_TASK_USAGE_DEFAULT_PAGE_SIZE,
        min_value=1,
        max_value=EVAL_TASK_USAGE_MAX_PAGE_SIZE,
    )
    limit = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=EVAL_TASK_USAGE_MAX_PAGE_SIZE,
        help_text="Legacy alias for page_size.",
    )
    eval_aggregation = serializers.BooleanField(required=False, default=False)
    span_aggregation = serializers.BooleanField(required=False, default=False)
    include_summary = serializers.BooleanField(required=False, default=True)
    start_date = serializers.DateTimeField(required=False)
    end_date = serializers.DateTimeField(required=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        is_aggregation = attrs["eval_aggregation"] or attrs["span_aggregation"]
        if not is_aggregation and (start_date is None) != (end_date is None):
            raise serializers.ValidationError(
                "start_date and end_date must be provided together."
            )
        if start_date is not None and end_date is not None and start_date >= end_date:
            raise serializers.ValidationError("start_date must be before end_date.")
        if (
            start_date is not None
            and end_date is not None
            and end_date - start_date > timedelta(days=366)
        ):
            raise serializers.ValidationError(
                "Evaluation usage date range cannot exceed 366 days."
            )
        legacy_limit = attrs.pop("limit", None)
        if legacy_limit is not None:
            if "page_size" in self.initial_data:
                raise serializers.ValidationError(
                    "Use either page_size or its legacy limit alias, not both."
                )
            attrs["page_size"] = legacy_limit
        return attrs


class EvalTaskUsageStatsSerializer(serializers.Serializer):
    total_runs = serializers.IntegerField(min_value=0)
    runs_period = serializers.IntegerField(min_value=0)
    success_count = serializers.IntegerField(min_value=0)
    error_count = serializers.IntegerField(min_value=0)
    pass_rate = serializers.FloatField(min_value=0, max_value=100)
    total_runs_is_lower_bound = serializers.BooleanField(required=False)
    runs_period_is_lower_bound = serializers.BooleanField(required=False)


class EvalTaskUsageEvalSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    output_type = serializers.CharField()
    template_id = serializers.UUIDField(allow_null=True)
    model = serializers.CharField(allow_null=True, allow_blank=True)


class EvalTaskUsageChartPointSerializer(serializers.Serializer):
    timestamp = serializers.DateTimeField()
    calls = serializers.IntegerField(min_value=0)
    pass_count = serializers.IntegerField(min_value=0)
    fail_count = serializers.IntegerField(min_value=0)
    avg_score = serializers.FloatField(allow_null=True)
    avg_latency_ms = serializers.FloatField(min_value=0)


class EvalTaskUsageLogDetailSerializer(serializers.Serializer):
    detail_complete = serializers.BooleanField()
    omitted_fields = serializers.ListField(child=serializers.CharField())
    eval_name = serializers.CharField(allow_null=True, allow_blank=True)
    model = serializers.CharField(allow_null=True, allow_blank=True)
    warnings = JsonValueField()
    output_type = serializers.CharField(allow_null=True, allow_blank=True)
    target_type = serializers.CharField(allow_null=True, allow_blank=True)
    span_name = serializers.CharField(allow_null=True, allow_blank=True)
    span_id = serializers.CharField(allow_null=True, allow_blank=True)
    trace_id = serializers.CharField(allow_null=True, allow_blank=True)
    session_id = serializers.CharField(allow_null=True, allow_blank=True)
    session_name = serializers.CharField(allow_null=True, allow_blank=True)
    output_bool = serializers.BooleanField(allow_null=True)
    output_float = serializers.FloatField(allow_null=True)
    output_str = serializers.CharField(allow_null=True, allow_blank=True)
    results_explanation = JsonValueField(allow_null=True)
    error_message = serializers.CharField(allow_null=True, allow_blank=True)
    input_variables = JsonValueField()


class EvalTaskUsageLogSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    input = serializers.CharField(allow_blank=True)
    result = serializers.CharField(allow_blank=True)
    score = serializers.FloatField(allow_null=True)
    reason = serializers.CharField(allow_blank=True)
    status = serializers.ChoiceField(choices=("success", "error"))
    source = serializers.CharField()
    warnings = JsonValueField()
    created_at = serializers.DateTimeField()
    span_id = serializers.CharField(allow_null=True, allow_blank=True)
    trace_id = serializers.CharField(allow_null=True, allow_blank=True)
    session_id = serializers.CharField(allow_null=True, allow_blank=True)
    eval_id = serializers.UUIDField(allow_null=True)
    eval_name = serializers.CharField(allow_null=True, allow_blank=True)
    model = serializers.CharField(allow_null=True, allow_blank=True)
    detail = EvalTaskUsageLogDetailSerializer()


class EvalTaskUsageLogsSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=0)
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = EvalTaskUsageLogSerializer(many=True)
    total_pages = serializers.IntegerField(min_value=0)
    current_page = serializers.IntegerField(min_value=1)
    has_more = serializers.BooleanField(required=False)
    count_is_lower_bound = serializers.BooleanField(required=False)
    page_limit_reached = serializers.BooleanField(required=False)


class EvalTaskUsageAggregationMetadataSerializer(serializers.Serializer):
    query_complete = serializers.BooleanField()
    sampled = serializers.BooleanField()
    error = serializers.ChoiceField(choices=("sample_limit",), allow_null=True)
    provenance = serializers.CharField()
    row_limit = serializers.IntegerField(min_value=1)
    rows_scanned = serializers.IntegerField(min_value=0)
    rows_matched = serializers.IntegerField(min_value=0)


class EvalTaskUsageResultSerializer(serializers.Serializer):
    eval_task_id = serializers.UUIDField()
    stats = EvalTaskUsageStatsSerializer(required=False)
    evals = EvalTaskUsageEvalSerializer(many=True, required=False)
    chart = EvalTaskUsageChartPointSerializer(many=True, required=False)
    logs = EvalTaskUsageLogsSerializer(required=False)
    period_requested = serializers.CharField(required=False)
    period_used = serializers.CharField(required=False)
    eval_aggregation = JsonValueField(required=False)
    span_aggregation = JsonValueField(required=False)
    aggregation_metadata = EvalTaskUsageAggregationMetadataSerializer(required=False)
    query_complete = serializers.BooleanField(required=False)
    query_status = serializers.ChoiceField(
        choices=("complete", "sampled"), required=False
    )
    query_sampled = serializers.BooleanField(required=False)
    error = serializers.ChoiceField(choices=("sample_limit",), required=False)
    provenance = serializers.CharField(required=False)


class EvalTaskUsageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = EvalTaskUsageResultSerializer()


class PaginationQuerySerializer(PageSizeQuerySerializer):
    """Shared query-params validator for eval-log endpoints."""

    page = serializers.IntegerField(required=False, default=0, min_value=0)


class EvalTaskListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    name = serializers.CharField(required=False, allow_blank=True)
    filters = filter_list_query_param_field(required=False, default=list)
    sort_params = SortParamListQueryParamField(required=False, default=list)
    page_number = serializers.IntegerField(required=False, default=0, min_value=0)
    page_size = serializers.IntegerField(
        required=False,
        default=EVAL_TASK_LIST_DEFAULT_PAGE_SIZE,
        min_value=1,
        max_value=settings.INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE,
    )


class EvalTaskListWithProjectNameQuerySerializer(EvalTaskListQuerySerializer):
    page_size = serializers.IntegerField(
        required=False,
        default=EVAL_TASK_LIST_WITH_PROJECT_DEFAULT_PAGE_SIZE,
        min_value=1,
        max_value=settings.INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE,
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
    # Progress block so the UI can render an "X of Y complete" bar while a
    # historical task is draining. Not persisted — computed on read from the
    # task's entry status counts. ``None`` for continuous tasks, which run
    # indefinitely and don't have a meaningful "expected" total.
    progress = serializers.SerializerMethodField()
    filters = eval_task_filters_field(required=False, allow_null=True, default=dict)

    def validate_filters(self, value):
        """Never persist a client-selected principal for user-relative filters."""

        request = self.context.get("request")
        try:
            return bind_request_my_annotations_principal(request, value)
        except FilterPrincipalContextError as exc:
            raise serializers.ValidationError(str(exc)) from exc

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
