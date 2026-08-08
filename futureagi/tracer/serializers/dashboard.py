from rest_framework import serializers

from accounts.serializers.user import UserSerializer
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.serializers.filters import (
    JsonValueField,
    StrictInputSerializer,
    filter_list_field,
)

DASHBOARD_METRIC_TYPES = (
    "system_metric",
    "eval_metric",
    "annotation_metric",
    "custom_attribute",
    "custom_column",
)
DASHBOARD_METRIC_SOURCES = ("traces", "datasets", "simulation", "both", "all")
DASHBOARD_GRANULARITIES = ("minute", "hour", "day", "week", "month")
DASHBOARD_TIME_RANGE_PRESETS = (
    "30m",
    "6h",
    "today",
    "yesterday",
    "7D",
    "30D",
    "3M",
    "6M",
    "12M",
)
DASHBOARD_AGGREGATIONS = (
    "avg",
    "median",
    "max",
    "min",
    "p25",
    "p50",
    "p75",
    "p90",
    "p95",
    "p99",
    "count",
    "count_distinct",
    "sum",
    "pass_rate",
    "fail_rate",
    "pass_count",
    "fail_count",
    "true_rate",
)
DASHBOARD_DATA_TYPES = (
    "string",
    "text",
    "number",
    "float",
    "integer",
    "boolean",
    "datetime",
    "date",
)


class DashboardTimeRangeSerializer(StrictInputSerializer):
    preset = serializers.ChoiceField(
        choices=DASHBOARD_TIME_RANGE_PRESETS, required=False
    )
    custom_start = serializers.DateTimeField(required=False)
    custom_end = serializers.DateTimeField(required=False)

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    def validate(self, attrs):
        has_custom_start = "custom_start" in attrs
        has_custom_end = "custom_end" in attrs
        if has_custom_start != has_custom_end:
            raise serializers.ValidationError(
                "custom_start and custom_end must be provided together."
            )
        if not attrs.get("preset") and not (has_custom_start and has_custom_end):
            raise serializers.ValidationError(
                "Provide either preset or custom_start/custom_end."
            )
        return attrs


class DashboardMetricSerializer(StrictInputSerializer):
    id = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=True, allow_blank=False)
    display_name = serializers.CharField(required=False, allow_blank=True)
    type = serializers.ChoiceField(choices=DASHBOARD_METRIC_TYPES)
    source = serializers.ChoiceField(
        choices=DASHBOARD_METRIC_SOURCES, required=False, default="traces"
    )
    aggregation = serializers.ChoiceField(
        choices=DASHBOARD_AGGREGATIONS, required=False, default="avg"
    )
    unit = serializers.CharField(required=False, allow_blank=True)
    output_type = serializers.CharField(required=False, allow_blank=True)
    eval_key = serializers.CharField(required=False, allow_blank=True)
    config_id = serializers.CharField(required=False, allow_blank=True)
    label_id = serializers.CharField(required=False, allow_blank=True)
    attribute_key = serializers.CharField(required=False, allow_blank=True)
    attribute_type = serializers.ChoiceField(
        choices=DASHBOARD_DATA_TYPES,
        required=False,
    )
    column_id = serializers.CharField(required=False, allow_blank=True)
    data_type = serializers.ChoiceField(
        choices=DASHBOARD_DATA_TYPES,
        required=False,
        default="string",
    )
    filters = filter_list_field(required=False, default=list)

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    def validate(self, attrs):
        """Infer the value-map type for legacy custom-metric payloads.

        The dashboard metric picker historically omitted ``attribute_type``.
        Defaulting that omission to ``string`` makes every numeric aggregation
        (avg, percentile, sum, and so on) fail before ClickHouse is queried.
        Dashboard Y-axis aggregations are numeric unless the caller explicitly
        requests a text-safe count operation; explicit types always win.
        """

        if not attrs.get("attribute_type"):
            if attrs.get("type") == "custom_attribute":
                attrs["attribute_type"] = (
                    "number"
                    if attrs.get("aggregation", "avg")
                    in {
                        "avg",
                        "sum",
                        "median",
                        "p25",
                        "p50",
                        "p75",
                        "p90",
                        "p95",
                        "p99",
                    }
                    else "string"
                )
            else:
                # Preserve the historical normalized payload/cache identity for
                # metric kinds that do not consume this field.
                attrs["attribute_type"] = "string"
        return attrs


class DashboardBreakdownSerializer(StrictInputSerializer):
    name = serializers.CharField(required=True, allow_blank=False)
    display_name = serializers.CharField(required=False, allow_blank=True)
    type = serializers.ChoiceField(
        choices=DASHBOARD_METRIC_TYPES, required=False, default="system_metric"
    )
    source = serializers.ChoiceField(
        choices=DASHBOARD_METRIC_SOURCES, required=False, default="traces"
    )
    output_type = serializers.CharField(required=False, allow_blank=True)
    label_id = serializers.CharField(required=False, allow_blank=True)
    config_id = serializers.CharField(required=False, allow_blank=True)
    eval_key = serializers.CharField(required=False, allow_blank=True)
    attribute_key = serializers.CharField(required=False, allow_blank=True)
    attribute_type = serializers.ChoiceField(
        choices=DASHBOARD_DATA_TYPES,
        required=False,
        default="string",
    )
    column_id = serializers.CharField(required=False, allow_blank=True)
    data_type = serializers.ChoiceField(
        choices=DASHBOARD_DATA_TYPES,
        required=False,
        default="string",
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class DashboardWidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = DashboardWidget
        fields = [
            "id",
            "name",
            "description",
            "position",
            "width",
            "height",
            "query_config",
            "chart_config",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def validate_width(self, value):
        if value < 1 or value > 12:
            raise serializers.ValidationError("Width must be between 1 and 12.")
        return value

    def validate_height(self, value):
        if value < 1:
            raise serializers.ValidationError("Height must be at least 1.")
        return value

    def validate_query_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("query_config must be a JSON object.")
        if value.get("metrics"):
            serializer = DashboardQuerySerializer(data=value)
            if not serializer.is_valid():
                raise serializers.ValidationError(serializer.errors)
        return value

    def validate_chart_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("chart_config must be a JSON object.")
        valid_types = (
            "line",
            "stacked_line",
            "column",
            "stacked_column",
            "bar",
            "stacked_bar",
            "pie",
            "table",
            "metric",
        )
        if "chart_type" in value and value["chart_type"] not in valid_types:
            raise serializers.ValidationError(
                f"chart_type must be one of: {', '.join(valid_types)}"
            )
        return value


class DashboardSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    updated_by = UserSerializer(read_only=True)
    widget_count = serializers.SerializerMethodField()

    class Meta:
        model = Dashboard
        fields = [
            "id",
            "name",
            "description",
            "workspace",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
            "widget_count",
        ]
        read_only_fields = [
            "id",
            "workspace",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]

    def get_widget_count(self, obj):
        return obj.widgets.filter(deleted=False).count()


class DashboardDetailSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    updated_by = UserSerializer(read_only=True)
    widgets = serializers.SerializerMethodField()

    class Meta:
        model = Dashboard
        fields = [
            "id",
            "name",
            "description",
            "workspace",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
            "widgets",
        ]
        read_only_fields = [
            "id",
            "workspace",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        ]

    def get_widgets(self, obj):
        widgets = obj.widgets.filter(deleted=False).order_by("position", "created_at")
        return DashboardWidgetSerializer(widgets, many=True).data


class DashboardCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dashboard
        fields = ["name", "description"]

    def validate_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Dashboard name cannot be empty.")
        return value.strip()


class DashboardQuerySerializer(StrictInputSerializer):
    workflow = serializers.ChoiceField(
        choices=("observability", "dataset", "simulation"),
        required=False,
        default="observability",
    )
    project_ids = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    time_range = DashboardTimeRangeSerializer(required=True)
    granularity = serializers.ChoiceField(
        choices=DASHBOARD_GRANULARITIES, required=False, default="day"
    )
    metrics = DashboardMetricSerializer(many=True)
    filters = filter_list_field(required=False, default=list)
    breakdowns = DashboardBreakdownSerializer(many=True, required=False, default=list)
    allow_sampled = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "Deprecated compatibility parameter; accepted but ignored. "
            "Dashboard aggregates are always exact."
        ),
    )

    class Meta:
        # Keep the established OpenAPI component identity explicit so runtime
        # read-compatibility subclasses can share the same unchanged contract.
        ref_name = "DashboardQuery"
        swagger_schema_fields = {"additionalProperties": False}

    def validate_metrics(self, value):
        if not value:
            raise serializers.ValidationError("At least one metric is required.")
        if len(value) > 5:
            raise serializers.ValidationError("At most 5 metrics are allowed.")
        return value


class DashboardPreviewQuerySerializer(StrictInputSerializer):
    query_config = DashboardQuerySerializer(required=True)
    allow_sampled = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "Deprecated compatibility parameter; accepted but ignored. "
            "Dashboard aggregates are always exact."
        ),
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class DashboardSampleOptInSerializer(StrictInputSerializer):
    allow_sampled = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "Deprecated compatibility parameter; accepted but ignored. "
            "Dashboard aggregates are always exact."
        ),
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class DashboardQuerySeriesPointSerializer(serializers.Serializer):
    timestamp = serializers.CharField()
    value = serializers.FloatField(allow_null=True)


class DashboardQuerySeriesSerializer(serializers.Serializer):
    name = serializers.CharField()
    data = DashboardQuerySeriesPointSerializer(many=True)


class DashboardQueryMetricResultSerializer(serializers.Serializer):
    id = serializers.CharField(allow_blank=True)
    name = serializers.CharField(allow_blank=True)
    aggregation = serializers.ChoiceField(choices=DASHBOARD_AGGREGATIONS)
    unit = serializers.CharField(allow_blank=True)
    series = DashboardQuerySeriesSerializer(many=True)
    query_complete = serializers.BooleanField(required=False)
    query_sampled = serializers.BooleanField(required=False)
    query_status = serializers.ChoiceField(
        choices=["complete", "sampled", "degraded"], required=False
    )
    query_error_code = serializers.ChoiceField(
        choices=["sample_limit", "read_budget_exceeded", "query_failed"],
        required=False,
    )
    query_sampling_strategy = serializers.CharField(required=False)
    query_sampling_interval_seconds = serializers.IntegerField(
        min_value=1, required=False
    )
    query_sample_limit = serializers.IntegerField(min_value=1, required=False)
    query_sample_per_bucket = serializers.IntegerField(min_value=1, required=False)


class DashboardQueryTimeRangeResultSerializer(serializers.Serializer):
    start = serializers.CharField()
    end = serializers.CharField()


class DashboardQueryResultSerializer(serializers.Serializer):
    metrics = DashboardQueryMetricResultSerializer(many=True)
    time_range = DashboardQueryTimeRangeResultSerializer()
    granularity = serializers.ChoiceField(choices=DASHBOARD_GRANULARITIES)
    query_complete = serializers.BooleanField(required=False)
    query_status = serializers.ChoiceField(
        choices=["complete", "degraded", "pending"], required=False
    )
    query_sampled = serializers.BooleanField(required=False)
    query_completed_at = serializers.DateTimeField(required=False)
    query_cached = serializers.BooleanField(required=False)
    query_refresh_failed = serializers.BooleanField(required=False)
    query_refreshing = serializers.BooleanField(required=False)
    query_snapshot_version_ceiling = serializers.IntegerField(
        min_value=1, required=False
    )
    query_snapshot_capture_count = serializers.IntegerField(min_value=0, required=False)
    query_snapshot_relation_count = serializers.IntegerField(
        min_value=0, required=False
    )


class DashboardQueryApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = DashboardQueryResultSerializer()


class DashboardMetricCatalogItemSerializer(serializers.Serializer):
    name = serializers.CharField()
    display_name = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(required=False, allow_blank=True)
    sources = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True
    )
    type = serializers.CharField(required=False, allow_blank=True)
    unit = serializers.CharField(required=False, allow_blank=True)
    output_type = serializers.CharField(required=False, allow_blank=True)
    choices = serializers.ListField(
        child=JsonValueField(), required=False, allow_empty=True
    )
    allowed_aggregations = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True
    )
    data_type = serializers.CharField(required=False, allow_blank=True)


class DashboardMetricsCatalogResultSerializer(serializers.Serializer):
    metrics = DashboardMetricCatalogItemSerializer(many=True)


class DashboardMetricsCatalogResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = DashboardMetricsCatalogResultSerializer()


class CommaSeparatedListField(serializers.Field):
    """Query-param helper for explicit comma-separated lists."""

    class Meta:
        swagger_schema_fields = {
            "type": "string",
            "default": "",
        }

    def run_validation(self, data=serializers.empty):
        value = super().run_validation(data)
        if data is serializers.empty:
            return self.to_internal_value(value)
        return value

    def to_internal_value(self, data):
        if data in (None, ""):
            return []
        if isinstance(data, (list, tuple)):
            items = data
        else:
            items = str(data).split(",")
        return [str(item).strip() for item in items if str(item).strip()]

    def to_representation(self, value):
        if isinstance(value, str):
            return value
        return value or []


class DashboardFilterValuesQuerySerializer(serializers.Serializer):
    metric_name = serializers.CharField(required=True, allow_blank=False)
    metric_type = serializers.ChoiceField(
        choices=[
            "system_metric",
            "eval_metric",
            "annotation_metric",
            "custom_attribute",
            "custom_column",
        ],
        required=False,
        default="system_metric",
    )
    source = serializers.ChoiceField(
        choices=[
            "traces",
            "sessions",
            "datasets",
            "dataset_column",
            "simulation",
        ],
        required=False,
        default="traces",
    )
    project_ids = CommaSeparatedListField(required=False, default="")
    dataset_id = serializers.UUIDField(required=False)
    search = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        # The selector enforces the same limit in encoded UTF-8 bytes.  This
        # character cap keeps obviously oversized requests out of every
        # source-specific branch before any database work.
        max_length=512,
    )
    page_size = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=50,
    )
    cursor = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=16_384,
    )
    attribute_type = serializers.ChoiceField(
        choices=["string", "number", "boolean", "array", "map", "json"],
        required=False,
    )

    def validate(self, attrs):
        if attrs.get("cursor") and "page_size" not in attrs:
            raise serializers.ValidationError(
                {"page_size": "page_size is required with cursor"}
            )
        return attrs

    def validate_search(self, value):
        # Import lazily so serializer/OpenAPI discovery does not initialize the
        # ClickHouse client package.  The shared validator also catches a
        # 512-character non-ASCII value whose UTF-8 representation exceeds the
        # actual 512-byte query contract.
        from tracer.services.clickhouse.attribute_reads import (
            InvalidAttributeSearch,
            validate_attribute_search,
        )

        try:
            return validate_attribute_search(value)
        except InvalidAttributeSearch as exc:
            raise serializers.ValidationError(str(exc)) from exc


class DashboardFilterValueOptionSerializer(serializers.Serializer):
    """One filter-picker option with optional custom-attribute provenance.

    ``type`` is additive so existing system/eval/annotation/dataset options
    keep their established ``value``/``label`` shape.  Custom-attribute
    options populate it from ``AttributeValueRow.type`` so an overflow-array
    member cannot be mistaken for a typed-Map text value by API consumers.
    """

    value = JsonValueField(allow_null=True)
    label = serializers.CharField()
    type = serializers.ChoiceField(
        choices=["string", "number", "boolean", "array", "map", "json"],
        required=False,
    )
    # Annotator options retain these established optional presentation fields.
    name = serializers.CharField(required=False)
    email = serializers.CharField(required=False)
    description = serializers.CharField(required=False)


class DashboardFilterValuesResultSerializer(serializers.Serializer):
    values = DashboardFilterValueOptionSerializer(many=True)
    query_complete = serializers.BooleanField(required=False)
    query_status = serializers.ChoiceField(
        choices=["complete", "sampled", "degraded"],
        required=False,
    )
    query_error_code = serializers.ChoiceField(
        choices=["sample_limit", "read_budget_exceeded", "query_failed"],
        required=False,
    )
    query_window_start = serializers.DateTimeField(required=False)
    query_window_end = serializers.DateTimeField(required=False)
    has_more = serializers.BooleanField(required=False)
    browse_status = serializers.ChoiceField(
        choices=["continuation", "exhausted", "limit_reached"],
        required=False,
    )
    next_cursor = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=False,
    )
    attribute_type = serializers.ChoiceField(
        choices=["string", "number", "boolean", "array", "map", "json"],
        required=False,
    )


class DashboardFilterValuesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = DashboardFilterValuesResultSerializer()
