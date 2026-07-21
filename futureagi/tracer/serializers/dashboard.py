import re

from rest_framework import serializers

from accounts.serializers.user import UserSerializer
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.serializers.filters import (
    JsonValueField,
    StrictInputSerializer,
    filter_list_field,
)

# Metric identifiers that reach the ClickHouse query builder must be valid map
# keys. Kept identical to `_SAFE_ATTR_KEY_RE` / `_sanitize_attr_key` in
# tracer/services/clickhouse/query_builders/dashboard.py so the serializer never
# rejects anything the builder would accept.
_SAFE_METRIC_KEY_RE = re.compile(r"^[a-zA-Z0-9._\-]+$")


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

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # Reject metric identifiers that could not be valid ClickHouse map keys
        # (injection / markup payloads) with a static, non-reflecting message so
        # they never reach the query layer or get echoed back in the response.
        # Display-only fields (display_name, unit, eval keys, ...) stay free-form.
        attr_key = attrs.get("attribute_key")
        if attr_key and not _SAFE_METRIC_KEY_RE.fullmatch(attr_key):
            raise serializers.ValidationError(
                {"attribute_key": "Attribute key contains unsupported characters."}
            )
        if attrs.get("type") == "system_metric":
            for field in ("name", "id"):
                value = attrs.get(field)
                if value and not _SAFE_METRIC_KEY_RE.fullmatch(value):
                    raise serializers.ValidationError(
                        {field: "Metric identifier contains unsupported characters."}
                    )
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


class DashboardPreviewQuerySerializer(StrictInputSerializer):
    query_config = DashboardQuerySerializer(required=True)

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
