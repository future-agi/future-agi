"""Explicit contracts for saved formulas, frozen runs and persisted rollups."""

from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers

from tfc.utils.serializer_fields import JsonValueField
from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    DerivedMetricVersion,
)
from tracer.serializers.filters import StrictInputSerializer
from tracer.services.derived_metrics.uncertainty import (
    CI_INTERVALS,
    CI_METHODS,
    CI_REASONS,
    CI_STATUSES,
    NOT_COMPUTED,
)


class MetricObservationConditionSerializer(StrictInputSerializer):
    operator = serializers.ChoiceField(choices=["eq", "ne", "lt", "lte", "gt", "gte"])
    # Scalar comparison values (number, boolean or string); the compiler checks
    # the type against the input. An object-only schema would reject them.
    value = JsonValueField()


class DerivedMetricInputSerializer(StrictInputSerializer):
    alias = serializers.RegexField(r"^[a-z][a-z0-9_]{0,39}$")
    source = serializers.ChoiceField(choices=["eval", "span"])
    config_id = serializers.UUIDField(required=False)
    config_hash = serializers.RegexField(
        r"^(?:[a-f0-9]{64})?$", required=False, allow_blank=True, default=""
    )
    field = serializers.ChoiceField(
        choices=[
            "latency_ms",
            "cost",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
            "attribute_number",
            "attribute_bool",
            "attribute_text",
        ],
        required=False,
    )
    attribute_key = serializers.CharField(
        max_length=160, required=False, allow_blank=True, default=""
    )
    value_type = serializers.ChoiceField(
        choices=["number", "boolean", "text"], required=False
    )
    reducer = serializers.ChoiceField(
        choices=["first", "latest", "mean", "min", "max", "sum", "count", "any", "all"],
        default="latest",
    )
    ordering = serializers.ChoiceField(choices=["sentence", "legacy"], required=False)
    condition_stage = serializers.ChoiceField(
        choices=["after_reduction", "observation"], required=False
    )
    condition = MetricObservationConditionSerializer(required=False)

    def validate(self, attrs):
        if attrs["source"] == "eval" and (
            attrs.get("field") or attrs.get("attribute_key")
        ):
            raise serializers.ValidationError(
                "Evaluation sources cannot select a span field."
            )
        if attrs["source"] == "span" and (
            attrs.get("config_id") or attrs.get("config_hash")
        ):
            raise serializers.ValidationError(
                "Span sources cannot select an evaluator."
            )
        if attrs["source"] == "eval" and not attrs.get("config_id"):
            raise serializers.ValidationError(
                {"config_id": "Select a persisted evaluator."}
            )
        if attrs["source"] == "span" and not attrs.get("field"):
            raise serializers.ValidationError({"field": "Select a numeric span field."})
        if (
            attrs.get("field", "").startswith("attribute_")
            and not attrs["attribute_key"]
        ):
            raise serializers.ValidationError(
                {"attribute_key": "An attribute key is required."}
            )
        return attrs


class DerivedMetricFilterSerializer(StrictInputSerializer):
    field = serializers.ChoiceField(
        choices=["model", "provider", "status", "name", "service_name"]
    )
    value = serializers.CharField(
        max_length=255, allow_blank=True, trim_whitespace=False
    )


class MetricConditionSerializer(StrictInputSerializer):
    alias = serializers.RegexField(r"^[a-z][a-z0-9_]{0,39}$")
    operator = serializers.ChoiceField(choices=["eq", "ne", "lt", "lte", "gt", "gte"])
    value = JsonValueField(required=False)
    right_alias = serializers.RegexField(r"^[a-z][a-z0-9_]{0,39}$", required=False)
    condition_stage = serializers.ChoiceField(
        choices=["after_reduction"], required=False
    )

    def validate(self, attrs):
        if ("value" in attrs) == ("right_alias" in attrs):
            raise serializers.ValidationError(
                "Supply exactly one comparison value or right_alias."
            )
        return attrs


class MetricFieldSpecSerializer(StrictInputSerializer):
    inputs = DerivedMetricInputSerializer(many=True, required=False)
    conditions = MetricConditionSerializer(many=True, required=False, max_length=16)
    value_alias = serializers.RegexField(r"^[a-z][a-z0-9_]{0,39}$", required=False)
    percentile = serializers.FloatField(min_value=0, max_value=1, required=False)


class DerivedMetricWriteSerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, default=""
    )
    formula = serializers.CharField(
        max_length=2048, required=False, allow_blank=True, default=""
    )
    inputs = DerivedMetricInputSerializer(many=True, allow_empty=False, required=False)
    selection_unit = serializers.ChoiceField(
        choices=["span", "session", "user"], required=False
    )
    kind = serializers.ChoiceField(
        choices=DerivedMetricVersion.Kind.choices, default="custom"
    )
    level = serializers.ChoiceField(
        choices=DerivedMetricVersion.Level.choices, required=False
    )
    user_rule = serializers.ChoiceField(
        choices=DerivedMetricVersion.UserRule.choices, required=False, allow_blank=True
    )
    field_spec = MetricFieldSpecSerializer(required=False)
    group_dims = serializers.ListField(
        child=serializers.CharField(max_length=160), max_length=2, required=False
    )
    value_type = serializers.ChoiceField(
        choices=DerivedMetricVersion.ValueType.choices, required=False
    )
    unit = serializers.CharField(max_length=32, required=False, allow_blank=True)
    value_format = serializers.ChoiceField(
        choices=["", "number", "percent", "currency", "duration"],
        required=False,
        allow_blank=True,
    )
    value_labels = serializers.DictField(
        child=serializers.CharField(max_length=80), required=False
    )
    sampling = serializers.DictField(required=False)
    weighting = serializers.ChoiceField(
        choices=["none", "inverse_probability"], default="none"
    )
    cohort_key = serializers.CharField(
        max_length=160, required=False, allow_blank=True, default=""
    )
    filters = DerivedMetricFilterSerializer(many=True, required=False, default=list)
    expected_version = serializers.IntegerField(min_value=1, required=False)

    def validate_inputs(self, value):
        if len(value) > 16:
            raise serializers.ValidationError("At most 16 inputs are allowed.")
        return value

    def validate_filters(self, value):
        if len(value) > 10:
            raise serializers.ValidationError("At most 10 filters are allowed.")
        return value


class DerivedMetricVersionSerializer(serializers.ModelSerializer):
    inputs = DerivedMetricInputSerializer(many=True)
    filters = DerivedMetricFilterSerializer(many=True)
    field_spec = MetricFieldSpecSerializer(read_only=True)
    needs_rebuild = serializers.SerializerMethodField()

    @swagger_serializer_method(serializer_or_field=serializers.BooleanField())
    def get_needs_rebuild(self, obj):
        from tracer.services.derived_metrics.spec import needs_rebuild

        return needs_rebuild(obj)

    class Meta:
        model = DerivedMetricVersion
        fields = [
            "version",
            "formula",
            "inputs",
            "selection_unit",
            "weighting",
            "cohort_key",
            "filters",
            "definition_hash",
            "id",
            "kind",
            "level",
            "user_rule",
            "field_spec",
            "group_dims",
            "value_type",
            "unit",
            "value_format",
            "value_labels",
            "sampling",
            "backfill_status",
            "backfill_days_done",
            "backfill_days_total",
            "history_mode",
            "coverage_start",
            "activated_at",
            "needs_rebuild",
            "created_at",
        ]


class MetricHistoryDefinitionSerializer(DerivedMetricVersionSerializer):
    class Meta(DerivedMetricVersionSerializer.Meta):
        # Snapshot history entries carry no definition.
        swagger_schema_fields = {"x-nullable": True}


class MetricCalculationPausedSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(
        choices=["too_large", "too_busy"],
        help_text="too_large: stored row limit; too_busy: work cannot finish in a pass",
    )
    since = serializers.DateTimeField()
    rows = serializers.IntegerField(
        min_value=0,
        allow_null=True,
        help_text="Stored metric rows measured when it paused",
    )
    limit = serializers.IntegerField(
        min_value=1, help_text="METRICS_MAX_CHECKPOINT_ROWS; raising it resumes"
    )
    work = serializers.DictField(
        child=serializers.IntegerField(min_value=0),
        required=False,
        help_text="Non-identifying busy-slot counts: owners, definitions, captures and capacity",
    )


class DerivedMetricSerializer(serializers.ModelSerializer):
    definition = DerivedMetricVersionSerializer(
        source="active_version", read_only=True, allow_null=True
    )
    draft_definition = serializers.SerializerMethodField()
    calculation_paused = serializers.SerializerMethodField()
    snapshot_supported = serializers.SerializerMethodField()
    snapshot_unsupported_reason = serializers.SerializerMethodField()
    value_mode = serializers.SerializerMethodField()

    @swagger_serializer_method(serializer_or_field=serializers.BooleanField())
    def get_snapshot_supported(self, obj):
        from tracer.services.derived_metrics.spec import snapshot_support

        return snapshot_support(obj.active_version)[0]

    @swagger_serializer_method(
        serializer_or_field=serializers.CharField(allow_blank=True)
    )
    def get_snapshot_unsupported_reason(self, obj):
        from tracer.services.derived_metrics.spec import snapshot_support

        return snapshot_support(obj.active_version)[1]

    @swagger_serializer_method(
        serializer_or_field=MetricCalculationPausedSerializer(allow_null=True)
    )
    def get_calculation_paused(self, obj):
        """Why the project's metrics stopped calculating, or null."""
        from tracer.models.derived_metric import MetricProjectRefresh
        from tracer.services.derived_metrics.size_cap import max_checkpoint_rows

        try:
            state = obj.project.metric_refresh
        except MetricProjectRefresh.DoesNotExist:
            return None
        if not state.paused_reason:
            return None
        return MetricCalculationPausedSerializer(
            {
                "reason": state.paused_reason,
                "since": state.paused_at,
                "rows": state.paused_rows,
                "limit": max_checkpoint_rows(),
                **(
                    {"work": state.busy_counts}
                    if state.paused_reason == "too_busy"
                    else {}
                ),
            }
        ).data

    @swagger_serializer_method(serializer_or_field=serializers.CharField())
    def get_value_mode(self, obj):
        return (
            "on_demand"
            if obj.active_version and obj.active_version.level == "user"
            else "materialized"
        )

    class Meta:
        model = DerivedMetric
        fields = [
            "id",
            "project_id",
            "name",
            "description",
            "current_version",
            "active_version",
            "draft_definition",
            "snapshot_supported",
            "snapshot_unsupported_reason",
            "value_mode",
            "calculation_paused",
            "definition",
            "created_at",
            "updated_at",
        ]

    @swagger_serializer_method(serializer_or_field=DerivedMetricVersionSerializer)
    def get_draft_definition(self, obj):
        version = next(
            (
                item
                for item in obj.versions.all()
                if item.version == obj.current_version
            ),
            None,
        )
        return DerivedMetricVersionSerializer(version).data if version else None


class DerivedMetricRunRequestSerializer(StrictInputSerializer):
    version = serializers.IntegerField(min_value=1, required=False)
    window_start = serializers.DateTimeField()
    window_end = serializers.DateTimeField()
    source_run_id = serializers.UUIDField(
        required=False,
        help_text="Persisted evaluation-task ID (the source eval_task_id), not an execution ID; snapshot and sampling_scope pin the observed evidence.",
    )
    filters = DerivedMetricFilterSerializer(many=True, required=False, default=list)
    idempotency_key = serializers.CharField(max_length=100)

    def validate(self, attrs):
        from datetime import timedelta

        from django.utils import timezone

        if not attrs["window_start"] < attrs["window_end"]:
            raise serializers.ValidationError("Window end must follow its start.")
        if attrs["window_end"] - attrs["window_start"] > timedelta(days=31):
            raise serializers.ValidationError("A run may cover at most 31 days.")
        if attrs["window_end"] > timezone.now():
            raise serializers.ValidationError("Only closed windows can be frozen.")
        if len(attrs["filters"]) > 10:
            raise serializers.ValidationError("At most 10 filters are allowed.")
        return attrs


class DerivedMetricManifestSerializer(serializers.Serializer):
    sampling_scope = serializers.CharField(allow_blank=True)
    schema_version = serializers.IntegerField()
    compiler_version = serializers.IntegerField()
    evaluator_table = serializers.CharField()
    definition_hash = serializers.CharField()
    captured_at = serializers.DateTimeField()
    semantics = serializers.CharField()

    def to_representation(self, instance):
        from tracer.services.derived_metrics.snapshots import SUBMISSION_PROTOCOL

        # Admission enrolls a run in the write barrier before it has captured
        # evidence. That internal marker is not a source manifest. Keep the
        # nullable API contract until the worker freezes actual provenance.
        if instance == {"write_protocol": SUBMISSION_PROTOCOL}:
            return None
        result = super().to_representation(instance)
        # Older records described an exemption that no longer applies.
        result["semantics"] = result["semantics"].replace(
            "retained until explicit deletion or erasure",
            "retained only while source policy permits, or until deletion or erasure",
        )
        return result

    class Meta:
        # Only referenced by the nullable DerivedMetricRun.source_manifest.
        swagger_schema_fields = {"x-nullable": True}


class DerivedMetricRunSerializer(serializers.ModelSerializer):
    # The run already selects this immutable FK, including on paginated reads.
    # Never derive historical context from the metric's current version.
    definition = DerivedMetricVersionSerializer(read_only=True)
    metric_id = serializers.UUIDField(source="definition.metric_id")
    metric_version = serializers.IntegerField(source="definition.version")
    source_manifest = DerivedMetricManifestSerializer(allow_null=True, required=False)
    filters = DerivedMetricFilterSerializer(many=True)

    class Meta:
        model = DerivedMetricRun
        fields = [
            "id",
            "metric_id",
            "metric_version",
            "definition",
            "window_start",
            "window_end",
            "as_of",
            "source_run_id",
            "filters",
            "snapshot_id",
            "result_id",
            "lease_expires_at",
            "source_manifest",
            "kind",
            "pinned_manifest",
            "pinned_generation",
            "captured_at",
            "supported_grain",
            "changed_at",
            "change_reason",
            "removed_unit_count",
            "deleted_at",
            "cleanup_status",
            "status",
            "error",
            "row_count",
            "created_at",
            "completed_at",
        ]


class MetricHistorySnapshotSerializer(DerivedMetricRunSerializer):
    class Meta(DerivedMetricRunSerializer.Meta):
        # Version and recalculation history entries carry no snapshot.
        swagger_schema_fields = {"x-nullable": True}


class DerivedMetricRecomputeSerializer(StrictInputSerializer):
    confirm = serializers.BooleanField(default=True)


class DerivedMetricResultQuerySerializer(StrictInputSerializer):
    rollup = serializers.ChoiceField(
        choices=["overall", "session", "user", "cohort", "day"], default="overall"
    )
    after = serializers.CharField(
        max_length=4096, required=False, allow_blank=True, default=""
    )
    limit = serializers.IntegerField(min_value=1, max_value=100, default=50)
    include_uncertainty = serializers.BooleanField(
        default=False,
        help_text="Add each row's design-based standard error and 95% interval as `uncertainty`.",
    )


class DerivedMetricUncertaintySerializer(serializers.Serializer):
    confidence_level = serializers.FloatField(help_text="Always 0.95 (two-sided).")
    status = serializers.ChoiceField(
        choices=list(CI_STATUSES),
        help_text="`not_estimable` means no interval exists; `reason` says why. Never a guess.",
    )
    reason = serializers.ChoiceField(
        choices=["", *CI_REASONS, NOT_COMPUTED], allow_blank=True
    )
    method = serializers.ChoiceField(
        choices=["", *CI_METHODS],
        allow_blank=True,
        help_text=(
            "stratified_wor_fpc: closed sampling cohorts (stratified, without replacement, "
            "finite-population corrected). poisson: live sampling policies. iid_units: "
            "unweighted ratios and means, a model-based interval treating units as "
            "independent draws. Covers sampling error only, not evaluator error."
        ),
    )
    interval = serializers.ChoiceField(
        choices=["", *CI_INTERVALS],
        allow_blank=True,
        help_text=(
            "wilson: rates (a 0/1 numerator within a 0/1 denominator), Wilson score "
            "interval at the design-effective sample size. wald_t: value +/- t * SE."
        ),
    )
    std_error = serializers.FloatField(allow_null=True)
    ci_low = serializers.FloatField(allow_null=True)
    ci_high = serializers.FloatField(allow_null=True)
    effective_n = serializers.FloatField(
        allow_null=True, help_text="Kish effective sample size of the valid units."
    )
    degrees_of_freedom = serializers.IntegerField(allow_null=True, min_value=1)


class DerivedMetricResultSerializer(serializers.Serializer):
    group_key = serializers.CharField(allow_blank=True)
    value = serializers.FloatField(allow_null=True)
    numerator = serializers.FloatField(allow_null=True)
    denominator = serializers.FloatField(allow_null=True)
    status = serializers.ChoiceField(
        choices=[
            "ok",
            "no_data",
            "missing_inputs",
            "invalid_probability",
            "zero_denominator",
            "non_finite",
        ]
    )
    unit_count = serializers.IntegerField(min_value=0)
    valid_count = serializers.IntegerField(min_value=0)
    missing_count = serializers.IntegerField(min_value=0)
    invalid_probability_count = serializers.IntegerField(min_value=0)
    uncertainty = DerivedMetricUncertaintySerializer(
        required=False,
        help_text="Present on previews, and on persisted results when include_uncertainty=true.",
    )


class DerivedMetricFrozenRecipeSerializer(DerivedMetricVersionSerializer):
    """Keep the legacy frozen-dashboard recipe contract unchanged.

    Direct definitions and snapshots use the complete v2 version contract. B19
    replaces the old run-backed dashboard surface with pinned live reads.
    """

    class Meta(DerivedMetricVersionSerializer.Meta):
        fields = [
            "version",
            "formula",
            "inputs",
            "selection_unit",
            "weighting",
            "cohort_key",
            "filters",
            "definition_hash",
            "created_at",
        ]


class DerivedMetricSavedRunContextSerializer(serializers.ModelSerializer):
    """Small read-only provenance for a dashboard's one committed observation."""

    metric_version = serializers.IntegerField(
        source="definition.version", read_only=True
    )
    definition = DerivedMetricFrozenRecipeSerializer(read_only=True)
    filters = DerivedMetricFilterSerializer(many=True, read_only=True)

    class Meta:
        model = DerivedMetricRun
        fields = [
            "id",
            "metric_version",
            "definition",
            "window_start",
            "window_end",
            "filters",
            "result_id",
            "status",
        ]
        read_only_fields = fields


class DerivedMetricDashboardContextSerializer(serializers.Serializer):
    run = DerivedMetricSavedRunContextSerializer(read_only=True)
    result = DerivedMetricResultSerializer(read_only=True, required=False)


class DerivedMetricResultPageSerializer(serializers.Serializer):
    results = DerivedMetricResultSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True, allow_blank=True)


class DerivedMetricCommittedResultPageSerializer(DerivedMetricResultPageSerializer):
    result_id = serializers.UUIDField(
        help_text="Committed result identity used to read every row and the next cursor."
    )


class DerivedMetricCompareQuerySerializer(StrictInputSerializer):
    baseline_run_id = serializers.UUIDField()


class DerivedMetricDeltaUncertaintySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "not_estimable"])
    reason = serializers.CharField(allow_blank=True)
    method = serializers.CharField(allow_blank=True)
    std_error = serializers.FloatField(allow_null=True)
    ci_low = serializers.FloatField(allow_null=True)
    ci_high = serializers.FloatField(allow_null=True)

    class Meta:
        # Only referenced by the nullable MetricKpi.delta_uncertainty.
        swagger_schema_fields = {"x-nullable": True}


class DerivedMetricComparisonSerializer(serializers.Serializer):
    baseline_run_id = serializers.UUIDField()
    candidate_run_id = serializers.UUIDField()
    baseline_result_id = serializers.UUIDField()
    candidate_result_id = serializers.UUIDField()
    baseline_version = serializers.IntegerField()
    candidate_version = serializers.IntegerField()
    baseline = DerivedMetricResultSerializer()
    candidate = DerivedMetricResultSerializer()
    delta = serializers.FloatField(allow_null=True)
    delta_uncertainty = DerivedMetricDeltaUncertaintySerializer()


class DerivedMetricContributorQuerySerializer(StrictInputSerializer):
    rollup = serializers.ChoiceField(
        choices=["overall", "session", "user", "cohort", "day"], default="overall"
    )
    group_key = serializers.CharField(
        max_length=2048,
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=False,
        help_text="The persisted rollup row to explain; empty for the overall row.",
    )
    limit = serializers.IntegerField(min_value=1, max_value=100, default=20)


class DerivedMetricContributorSerializer(serializers.Serializer):
    unit_id = serializers.CharField(
        help_text='Selection unit id; span units are ["trace_id","span_id"].'
    )
    trace_id = serializers.CharField(
        allow_null=True, help_text="Span units only: the unit's trace."
    )
    session_id = serializers.CharField(allow_blank=True)
    user_id = serializers.CharField(allow_blank=True)
    cohort = serializers.CharField(allow_blank=True)
    day = serializers.CharField()
    probability = serializers.FloatField()
    weight = serializers.FloatField(help_text="1 / inclusion probability.")
    numerator = serializers.FloatField()
    denominator = serializers.FloatField(allow_null=True)
    weighted_numerator = serializers.FloatField()
    weighted_denominator = serializers.FloatField(allow_null=True)
    influence = serializers.FloatField(
        allow_null=True,
        help_text=(
            "Exact leave-one-out change: the persisted value minus the group's value "
            "without this unit (a total's weighted contribution). Null when removing "
            "the unit leaves no denominator or the group has no value."
        ),
    )


class DerivedMetricContributorDiagnosticsSerializer(serializers.Serializer):
    unit_count = serializers.IntegerField(min_value=0)
    valid_count = serializers.IntegerField(min_value=0)
    missing_count = serializers.IntegerField(min_value=0)
    invalid_probability_count = serializers.IntegerField(min_value=0)
    outside_denominator_count = serializers.IntegerField(
        min_value=0, help_text="Valid units whose denominator is 0 (ratios only)."
    )
    undefined_arithmetic_count = serializers.IntegerField(
        min_value=0,
        help_text="Units with every input present whose row arithmetic is undefined.",
    )
    missing_by_input = serializers.DictField(
        child=serializers.IntegerField(min_value=0)
    )
    missing_examples = serializers.ListField(child=serializers.CharField())
    invalid_probability_examples = serializers.ListField(child=serializers.CharField())


class DerivedMetricContributorPageSerializer(serializers.Serializer):
    result_id = serializers.UUIDField(
        help_text="Committed result identity used for the value and contributor diagnostics."
    )
    rollup = serializers.CharField()
    group_key = serializers.CharField(allow_blank=True)
    kind = serializers.ChoiceField(choices=["ratio", "mean", "sum", "count_if"])
    selection_unit = serializers.ChoiceField(choices=["span", "session", "user"])
    status = serializers.CharField()
    value = serializers.FloatField(allow_null=True)
    numerator = serializers.FloatField(allow_null=True)
    denominator = serializers.FloatField(allow_null=True)
    results = DerivedMetricContributorSerializer(many=True)
    truncated = serializers.BooleanField(
        help_text="More valid units exist than were returned."
    )
    diagnostics = DerivedMetricContributorDiagnosticsSerializer()


class DerivedMetricValidationSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    kind = serializers.CharField()
    required_inputs = serializers.ListField(child=serializers.CharField())
    semantics = serializers.CharField()


class DerivedMetricListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=30)
    before = serializers.UUIDField(required=False)


class DerivedMetricListSerializer(serializers.Serializer):
    results = DerivedMetricSerializer(many=True)
    next_cursor = serializers.UUIDField(allow_null=True)


class DerivedMetricRunsSerializer(serializers.Serializer):
    results = DerivedMetricRunSerializer(many=True)
    next_cursor = serializers.UUIDField(allow_null=True)


class DerivedMetricEvaluatorQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    search = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    limit = serializers.IntegerField(min_value=1, max_value=100, default=50)


class DerivedMetricEvaluatorSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Evaluator config ID to bind (config_id).")
    name = serializers.CharField(allow_blank=True)
    config_hash = serializers.RegexField(
        r"^[a-f0-9]{64}$",
        help_text=(
            "Hash of the evaluator's CURRENT resolved definition: the config_hash that "
            "new evaluation results are stored under. Results produced under an earlier "
            "definition carry a different hash."
        ),
    )


class DerivedMetricEvaluatorPageSerializer(serializers.Serializer):
    results = DerivedMetricEvaluatorSerializer(many=True)
    truncated = serializers.BooleanField(
        help_text="True when more evaluators matched than were returned; narrow the search."
    )


# V2 routes are frozen here, before C01. Unimplemented reads return 503 rather
# than successful empty/zero observations. B13/B18 replace those adapters.
class MetricEmptySerializer(StrictInputSerializer):
    pass


class MetricUnavailableSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    retryable = serializers.BooleanField()
    value_mode = serializers.ChoiceField(choices=["on_demand"], required=False)
    calculated_through = serializers.DateTimeField(
        required=False,
        help_text=(
            "window_not_calculated_yet: the read cut; values are final only before it"
        ),
    )
    reason = serializers.ChoiceField(
        choices=["not_calculated", "settling", "too_large", "too_busy"],
        required=False,
        help_text=(
            "window_not_calculated_yet: not_calculated (past the calculated "
            "bound), settling (calculated, but holding open units), "
            "too_large (stored row limit), or too_busy (the pending work "
            "cannot complete within a pass)"
        ),
    )


class MetricWindowSerializer(StrictInputSerializer):
    window_start = serializers.DateTimeField()
    window_end = serializers.DateTimeField()

    def validate(self, attrs):
        from datetime import timedelta

        from django.utils import timezone

        if not attrs["window_start"] < attrs["window_end"]:
            raise serializers.ValidationError("Window end must follow its start.")
        if attrs["window_end"] - attrs["window_start"] > timedelta(days=90):
            raise serializers.ValidationError("A read may cover at most 90 days.")
        if attrs["window_end"] > timezone.now():
            raise serializers.ValidationError("Only closed windows can be read.")
        return attrs


class MetricPreviewRequestSerializer(MetricWindowSerializer):
    version = serializers.IntegerField(min_value=1, required=False)
    filters = DerivedMetricFilterSerializer(
        many=True, required=False, default=list, max_length=10
    )
    source_run_id = serializers.UUIDField(required=False)
    request_id = serializers.UUIDField(required=False)
    include_samples = serializers.BooleanField(default=False)
    # Accepted for old clients; not used for preview identity/admission.
    idempotency_key = serializers.CharField(max_length=100, required=False)

    def validate(self, attrs):
        from datetime import timedelta

        attrs = super().validate(attrs)
        if attrs["window_end"] - attrs["window_start"] > timedelta(days=7):
            raise serializers.ValidationError("A preview may cover at most 7 days.")
        return attrs


class MetricDraftDefinitionSerializer(DerivedMetricWriteSerializer):
    """An unsaved recipe needs neither a name nor a persistent metric identity."""

    project_id = None
    name = None
    description = None
    expected_version = None


class MetricDraftPreviewRequestSerializer(MetricPreviewRequestSerializer):
    version = None
    project_id = serializers.UUIDField()
    definition = MetricDraftDefinitionSerializer()
    include_samples = serializers.BooleanField(default=True)


class MetricPreviewCancelSerializer(StrictInputSerializer):
    request_id = serializers.UUIDField()


class MetricDraftPreviewCancelSerializer(MetricPreviewCancelSerializer):
    project_id = serializers.UUIDField()


class MetricPreviewCancelledSerializer(serializers.Serializer):
    cancelled = serializers.BooleanField()


class MetricReadProvenanceSerializer(serializers.Serializer):
    manifest_id = serializers.UUIDField()
    generation = serializers.IntegerField(min_value=0)
    version_id = serializers.UUIDField()
    version = serializers.IntegerField(min_value=1)
    definition_hash = serializers.CharField()
    updated_at = serializers.DateTimeField(allow_null=True)
    backfill_days_done = serializers.IntegerField(min_value=0)
    backfill_days_total = serializers.IntegerField(min_value=0)
    # Optional on the wire so a client never rejects a server that predates them.
    history_mode = serializers.ChoiceField(
        choices=["history", "forward"],
        allow_null=True,
        required=False,
        help_text="forward: started with no history; values exist from coverage_start",
    )
    coverage_start = serializers.DateTimeField(allow_null=True, required=False)
    retention_floor = serializers.DateTimeField(
        allow_null=True,
        required=False,
        help_text="Permanent floor of the published retained-only state; current source policy can be stricter.",
    )
    retention_policy = serializers.ChoiceField(
        choices=["follow_source"], required=False
    )
    calculated_through = serializers.DateTimeField(
        allow_null=True,
        required=False,
        help_text=(
            "Nothing starting at or after this 15-minute boundary is "
            "calculated yet (the consumed source cut, or a pending burst sweep's "
            "floor)"
        ),
    )


class MetricEffectiveWindowSerializer(serializers.Serializer):
    window_start = serializers.DateTimeField()
    window_end = serializers.DateTimeField()
    rounding = serializers.CharField(help_text="Rounded to 15 minutes")


class MetricPointSerializer(serializers.Serializer):
    value = serializers.FloatField(allow_null=True)
    point_status = serializers.ChoiceField(
        choices=[
            "ok",
            "unavailable",
            "no_data",
            "missing_inputs",
            "invalid_probability",
            "zero_denominator",
            "non_finite",
            "retention_expired",
            "sampled_inputs_not_supported",
            "not_calculated",
            "settling",
        ]
    )
    rate_eligible = serializers.BooleanField()
    weighted = serializers.BooleanField()
    unit = serializers.CharField(allow_blank=True)
    value_format = serializers.CharField(allow_blank=True)
    unit_noun = serializers.CharField()
    value_mode = serializers.ChoiceField(choices=["materialized", "on_demand"])
    eligible_count = serializers.IntegerField(min_value=0)
    evaluated_count = serializers.IntegerField(min_value=0)
    missing_count = serializers.IntegerField(min_value=0)
    counted_users = serializers.IntegerField(min_value=0, required=False)
    eligible_users = serializers.IntegerField(min_value=0, required=False)
    no_value_users = serializers.IntegerField(min_value=0, required=False)
    conversations_without_user = serializers.IntegerField(min_value=0, required=False)
    history_available_from = serializers.DateTimeField(allow_null=True, required=False)
    uncertainty = DerivedMetricUncertaintySerializer()


class MetricKpiPreviousPointSerializer(MetricPointSerializer):
    class Meta:
        # The previous period can be absent (no data or before retention).
        swagger_schema_fields = {"x-nullable": True}


_COMPLETE_THROUGH_HELP = (
    "End of the window's contiguous final prefix in both history and forward modes. Values are computed "
    "over the window's final buckets only (calculated, past the settle "
    "interval, holding no open unit)"
)
_PARTIAL_HELP = "true when any bucket of the window is not final"


class MetricKpiSerializer(serializers.Serializer):
    provenance = MetricReadProvenanceSerializer()
    effective_window = MetricEffectiveWindowSerializer()
    complete_through = serializers.DateTimeField(
        allow_null=True, required=False, help_text=_COMPLETE_THROUGH_HELP
    )
    partial = serializers.BooleanField(required=False, help_text=_PARTIAL_HELP)
    nonfinal_buckets = serializers.IntegerField(
        min_value=0,
        required=False,
        help_text="15-minute buckets of the window left out of the value",
    )
    open_units = serializers.IntegerField(
        min_value=0,
        required=False,
        help_text="units in the window refresh still keeps open",
    )
    current = MetricPointSerializer()
    previous = MetricKpiPreviousPointSerializer(allow_null=True)
    delta = serializers.FloatField(allow_null=True)
    delta_uncertainty = DerivedMetricDeltaUncertaintySerializer(allow_null=True)


class MetricTrendQuerySerializer(MetricWindowSerializer):
    points = serializers.IntegerField(min_value=1, max_value=90, default=30)
    group_by = serializers.CharField(max_length=160, required=False)


class MetricTrendPointSerializer(MetricPointSerializer):
    window_start = serializers.DateTimeField()
    window_end = serializers.DateTimeField()
    group_key = serializers.CharField(allow_blank=True)


class MetricTrendSerializer(serializers.Serializer):
    provenance = MetricReadProvenanceSerializer()
    effective_window = MetricEffectiveWindowSerializer()
    complete_through = serializers.DateTimeField(
        allow_null=True, required=False, help_text=_COMPLETE_THROUGH_HELP
    )
    nonfinal_buckets = serializers.IntegerField(min_value=0, required=False)
    open_units = serializers.IntegerField(min_value=0, required=False)
    partial = serializers.BooleanField(
        required=False, help_text="true when any point is not_calculated or settling"
    )
    results = MetricTrendPointSerializer(many=True)


class MetricDriversQuerySerializer(MetricWindowSerializer):
    after = serializers.CharField(max_length=4096, required=False, allow_blank=True)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=20)


class MetricUnitValueSerializer(serializers.Serializer):
    unit_id = serializers.CharField()
    status = serializers.ChoiceField(
        choices=["value", "in_progress", "not_calculated", "no_value"]
    )
    no_value_reason = serializers.CharField(allow_blank=True)
    value_number = serializers.FloatField(allow_null=True)
    value_bool = serializers.BooleanField(allow_null=True)
    value_text = serializers.CharField(allow_null=True, allow_blank=True)
    inputs = serializers.DictField(child=serializers.FloatField(allow_null=True))
    inputs_bool = serializers.DictField(child=serializers.BooleanField(allow_null=True))
    inputs_text = serializers.DictField(
        child=serializers.CharField(allow_null=True, allow_blank=True)
    )
    eligible_count = serializers.IntegerField(min_value=0)
    evaluated_count = serializers.IntegerField(min_value=0)
    missing_count = serializers.IntegerField(min_value=0)
    influence = serializers.FloatField(allow_null=True)


class MetricDriversSerializer(serializers.Serializer):
    provenance = MetricReadProvenanceSerializer()
    effective_window = MetricEffectiveWindowSerializer()
    complete_through = serializers.DateTimeField(
        allow_null=True, required=False, help_text=_COMPLETE_THROUGH_HELP
    )
    partial = serializers.BooleanField(required=False, help_text=_PARTIAL_HELP)
    nonfinal_buckets = serializers.IntegerField(
        min_value=0,
        required=False,
        help_text="15-minute buckets of the window left out of the value",
    )
    open_units = serializers.IntegerField(
        min_value=0,
        required=False,
        help_text="units in the window refresh still keeps open",
    )
    results = MetricUnitValueSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)


class MetricUnitIdsField(serializers.ListField):
    def get_value(self, dictionary):
        # validated_request read-POST keeps native JSON values in a QueryDict.
        # DRF's default getlist would turn that native list into [the_list].
        value = dictionary.get(self.field_name)
        return value if isinstance(value, list) else super().get_value(dictionary)


class MetricUnitValuesRequestSerializer(MetricWindowSerializer):
    unit_ids = MetricUnitIdsField(
        child=serializers.CharField(max_length=512), min_length=1, max_length=100
    )
    context = serializers.ChoiceField(choices=["sessions", "users", "spans", "traces"])


class MetricFieldsQuerySerializer(StrictInputSerializer):
    context = serializers.ChoiceField(
        choices=["sessions", "users", "spans", "traces"], default="sessions"
    )


class MetricFieldMetadataSerializer(serializers.Serializer):
    id = serializers.CharField()
    level = serializers.CharField()
    kind = serializers.CharField()
    value_type = serializers.ChoiceField(choices=["boolean", "number", "text"])
    value_format = serializers.CharField(allow_blank=True)
    unit = serializers.CharField(allow_blank=True)
    value_labels = serializers.DictField(child=serializers.CharField())
    operators = serializers.ListField(child=serializers.CharField())
    projection = serializers.BooleanField()


class MetricFieldsSerializer(serializers.Serializer):
    results = MetricFieldMetadataSerializer(many=True)


class MetricRefreshRequestSerializer(StrictInputSerializer):
    confirm = serializers.BooleanField(default=True)


class MetricRefreshSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["queued", "already_queued"])


class MetricHistoryEntrySerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["version", "recalculation", "snapshot"])
    created_at = serializers.DateTimeField()
    definition = MetricHistoryDefinitionSerializer(allow_null=True)
    snapshot = MetricHistorySnapshotSerializer(allow_null=True)
    status = serializers.CharField()


class MetricHistorySerializer(serializers.Serializer):
    results = MetricHistoryEntrySerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)


class MetricSnapshotRequestSerializer(MetricWindowSerializer):
    idempotency_key = serializers.CharField(max_length=100)


class MetricSnapshotDeleteSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["cleanup_pending", "deleted"])


class MetricActivationRequestSerializer(StrictInputSerializer):
    version = serializers.IntegerField(min_value=1)
    expected_active_id = serializers.UUIDField(allow_null=True)


class MetricPreviewSampleSerializer(serializers.Serializer):
    unit_id = serializers.CharField()
    status = serializers.ChoiceField(choices=["value", "no_value"])
    no_value_reason = serializers.CharField(allow_blank=True)
    value_number = serializers.FloatField(allow_null=True)
    value_bool = serializers.BooleanField(allow_null=True)
    value_text = serializers.CharField(allow_null=True, allow_blank=True)
    value_label = serializers.CharField(allow_blank=True)
    inputs = serializers.DictField(child=serializers.FloatField(allow_null=True))
    inputs_bool = serializers.DictField(child=serializers.BooleanField(allow_null=True))
    inputs_text = serializers.DictField(
        child=serializers.CharField(allow_null=True, allow_blank=True)
    )


class MetricPreviewSerializer(DerivedMetricResultPageSerializer):
    rate_eligible = serializers.BooleanField()
    weighted = serializers.BooleanField()
    unit = serializers.CharField(allow_blank=True)
    value_format = serializers.CharField(allow_blank=True)
    unit_noun = serializers.CharField()
    value_mode = serializers.ChoiceField(choices=["materialized", "on_demand"])
    counted_users = serializers.IntegerField(min_value=0, required=False)
    eligible_users = serializers.IntegerField(min_value=0, required=False)
    no_value_users = serializers.IntegerField(min_value=0, required=False)
    conversations_without_user = serializers.IntegerField(min_value=0, required=False)
    point_status = serializers.CharField()
    summary = serializers.CharField()
    sample_users = MetricUnitValueSerializer(many=True, required=False)
    sample_units = MetricPreviewSampleSerializer(
        many=True, required=False, max_length=20
    )


class MetricSourceCapabilitySerializer(serializers.Serializer):
    value_type = serializers.CharField()
    reducers = serializers.ListField(child=serializers.CharField())
    operators = serializers.ListField(child=serializers.CharField())
    observation_reducers = serializers.ListField(child=serializers.CharField())
    ordering = serializers.CharField()


class MetricCapabilitiesSerializer(serializers.Serializer):
    sources = MetricSourceCapabilitySerializer(many=True)
    levels = serializers.ListField(child=serializers.CharField())
    kinds = serializers.ListField(child=serializers.CharField())
    user_rules = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField())
    )
    max_group_dims = serializers.IntegerField()
    preview_max_days = serializers.IntegerField()
    preview_budget_seconds = serializers.IntegerField()
