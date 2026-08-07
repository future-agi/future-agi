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
DASHBOARD_QUERY_MODES = ("time_series", "distribution")
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
        default="string",
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
            "distribution",
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
    query_mode = serializers.ChoiceField(
        choices=DASHBOARD_QUERY_MODES, required=False, default="time_series"
    )
    metrics = DashboardMetricSerializer(many=True)
    filters = filter_list_field(required=False, default=list)
    breakdowns = DashboardBreakdownSerializer(
        many=True, required=False, default=list
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    def validate_metrics(self, value):
        if not value:
            raise serializers.ValidationError("At least one metric is required.")
        if len(value) > 5:
            raise serializers.ValidationError("At most 5 metrics are allowed.")
        return value

    def validate(self, attrs):
        if attrs.get("query_mode") != "distribution":
            return attrs

        metrics = attrs["metrics"]
        if len(metrics) != 1:
            raise serializers.ValidationError(
                {
                    "metrics": (
                        "Distribution queries require exactly one numeric eval metric."
                    )
                }
            )

        metric = metrics[0]
        if metric.get("type") != "eval_metric":
            raise serializers.ValidationError(
                {"metrics": "Distribution queries only support eval metrics."}
            )
        if metric.get("source", "traces") not in ("traces", "both", "all"):
            raise serializers.ValidationError(
                {
                    "metrics": (
                        "Distribution queries only support trace-sourced eval metrics."
                    )
                }
            )
        output_type = str(
            metric.get("output_type") or metric.get("outputType") or "SCORE"
        ).upper()
        if output_type not in ("SCORE", "NUMERIC"):
            raise serializers.ValidationError(
                {"metrics": "Distribution queries require a numeric score eval metric."}
            )
        if attrs.get("breakdowns"):
            raise serializers.ValidationError(
                {"breakdowns": "Distribution queries do not support breakdowns."}
            )
        if metric.get("aggregation") != "count":
            raise serializers.ValidationError(
                {"metrics": "Distribution queries use count aggregation."}
            )

        return attrs


class DashboardPreviewQuerySerializer(StrictInputSerializer):
    query_config = DashboardQuerySerializer(required=True)

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class DashboardQuerySeriesPointSerializer(serializers.Serializer):
    timestamp = serializers.CharField(required=False)
    bucket_start = serializers.FloatField(required=False)
    bucket_end = serializers.FloatField(required=False)
    value = serializers.FloatField(allow_null=True)

    def validate(self, attrs):
        has_timestamp = "timestamp" in attrs
        has_bucket_start = "bucket_start" in attrs
        has_bucket_end = "bucket_end" in attrs

        if has_bucket_start != has_bucket_end:
            raise serializers.ValidationError(
                "bucket_start and bucket_end must be provided together."
            )
        if has_timestamp == has_bucket_start:
            raise serializers.ValidationError(
                "Provide either timestamp or both bucket_start and bucket_end."
            )
        return attrs


class DashboardQuerySeriesSerializer(serializers.Serializer):
    name = serializers.CharField()
    data = DashboardQuerySeriesPointSerializer(many=True)


class DashboardQueryMetricResultSerializer(serializers.Serializer):
    id = serializers.CharField(allow_blank=True)
    name = serializers.CharField(allow_blank=True)
    aggregation = serializers.ChoiceField(choices=DASHBOARD_AGGREGATIONS)
    unit = serializers.CharField(allow_blank=True)
    series = DashboardQuerySeriesSerializer(many=True)


class DashboardQueryTimeRangeResultSerializer(serializers.Serializer):
    start = serializers.CharField()
    end = serializers.CharField()


class DashboardQueryResultSerializer(serializers.Serializer):
    metrics = DashboardQueryMetricResultSerializer(many=True)
    time_range = DashboardQueryTimeRangeResultSerializer()
    granularity = serializers.ChoiceField(choices=DASHBOARD_GRANULARITIES)


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

    def to_internal_value(self, data):
        if data in (None, ""):
            return []
        if isinstance(data, (list, tuple)):
            items = data
        else:
            items = str(data).split(",")
        return [str(item).strip() for item in items if str(item).strip()]

    def to_representation(self, value):
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
    project_ids = CommaSeparatedListField(required=False, default=list)
    dataset_id = serializers.UUIDField(required=False)
    search = serializers.CharField(required=False, allow_blank=True, default="")
