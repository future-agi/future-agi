"""Explicit contracts for saved formulas, frozen runs and persisted rollups."""

from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers

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
        ],
        required=False,
    )
    attribute_key = serializers.CharField(
        max_length=160, required=False, allow_blank=True, default=""
    )
    reducer = serializers.ChoiceField(
        choices=["mean", "min", "max", "latest"], default="latest"
    )

    def validate(self, attrs):
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


class DerivedMetricWriteSerializer(StrictInputSerializer):
    project_id = serializers.UUIDField()
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, default=""
    )
    formula = serializers.CharField(max_length=2048)
    inputs = DerivedMetricInputSerializer(many=True, allow_empty=False)
    selection_unit = serializers.ChoiceField(
        choices=["span", "session", "user"], default="session"
    )
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
            "created_at",
        ]


class DerivedMetricSerializer(serializers.ModelSerializer):
    definition = serializers.SerializerMethodField()

    class Meta:
        model = DerivedMetric
        fields = [
            "id",
            "project_id",
            "name",
            "description",
            "current_version",
            "definition",
            "created_at",
            "updated_at",
        ]

    @swagger_serializer_method(serializer_or_field=DerivedMetricVersionSerializer)
    def get_definition(self, obj):
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


class DerivedMetricRunSerializer(serializers.ModelSerializer):
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
            "window_start",
            "window_end",
            "as_of",
            "source_run_id",
            "filters",
            "snapshot_id",
            "result_id",
            "lease_expires_at",
            "source_manifest",
            "status",
            "error",
            "row_count",
            "created_at",
            "completed_at",
        ]


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


class DerivedMetricResultPageSerializer(serializers.Serializer):
    results = DerivedMetricResultSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True, allow_blank=True)


class DerivedMetricCompareQuerySerializer(StrictInputSerializer):
    baseline_run_id = serializers.UUIDField()


class DerivedMetricDeltaUncertaintySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "not_estimable"])
    reason = serializers.CharField(allow_blank=True)
    method = serializers.CharField(allow_blank=True)
    std_error = serializers.FloatField(allow_null=True)
    ci_low = serializers.FloatField(allow_null=True)
    ci_high = serializers.FloatField(allow_null=True)


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
