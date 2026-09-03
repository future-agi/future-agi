from django.conf import settings
from rest_framework import serializers

from accounts.serializers.user import UserSerializer
from tracer.constants.dashboard import (
    DASHBOARD_AGGREGATIONS,
    DASHBOARD_NUMERIC_ONLY_AGGREGATIONS,
)
from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.serializers.filters import (
    JsonValueField,
    StrictInputSerializer,
    filter_list_field,
)
from tracer.services.clickhouse.query_builders.dataset_dashboard import (
    DATASET_BREAKDOWN_COLUMNS,
    DATASET_FILTER_COLUMNS,
)
from tracer.services.clickhouse.query_builders.simulation_dashboard import (
    SIMULATION_BREAKDOWN_COLUMNS,
    SIMULATION_FILTER_COLUMNS,
)
from tracer.utils.property_registry import (
    normalize_custom_attribute_source,
    parse_property_registry_id,
    property_value_transport_source,
    validate_property_metric_binding,
)

DASHBOARD_METRIC_TYPES = (
    "system_metric",
    "eval_metric",
    "annotation_metric",
    "custom_attribute",
    "custom_column",
)
DASHBOARD_METRIC_SOURCES = ("traces", "datasets", "simulation", "both", "all")

# Cursor-mode discovery publishes logical definition namespaces instead of
# forcing every surface through its physical trace/session transport.  Keep
# the legacy page-number contract narrow below and admit these additional
# values only when ``cursor_mode=true``.
PROPERTY_CATALOG_METRIC_SOURCES = (
    "traces",
    "spans",
    "sessions",
    "users",
    "voice_calls",
    "prompts",
    "datasets",
    "simulation",
    "both",
    "all",
)
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

_DATASET_DIMENSION_SOURCES = frozenset({"datasets", "all", "both"})
_DATASET_FILTER_DIMENSIONS = frozenset(DATASET_FILTER_COLUMNS)
_DATASET_BREAKDOWN_DIMENSIONS = frozenset(DATASET_BREAKDOWN_COLUMNS)
_SIMULATION_FILTER_DIMENSIONS = frozenset(SIMULATION_FILTER_COLUMNS)
_SIMULATION_BREAKDOWN_DIMENSIONS = frozenset(SIMULATION_BREAKDOWN_COLUMNS)
_DASHBOARD_CATALOG_MAX_PAGE_SIZE = settings.DASHBOARD_METRICS_CATALOG_MAX_PAGE_SIZE
_DASHBOARD_CATALOG_SEARCH_MAX_CHARS = (
    settings.DASHBOARD_METRICS_CATALOG_SEARCH_MAX_CHARS
)
_PROPERTY_CATALOG_MAX_PROJECTS = settings.PROPERTY_CATALOG_MAX_PROJECTS
_PROPERTY_CATALOG_MAX_PAGE_SIZE = settings.PROPERTY_CATALOG_MAX_PAGE_SIZE
_PROPERTY_CATALOG_MAX_SEARCH_BYTES = settings.PROPERTY_CATALOG_MAX_SEARCH_BYTES
_PROPERTY_CATALOG_CURSOR_MAX_BYTES = settings.PROPERTY_CATALOG_CURSOR_MAX_BYTES
_DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE = settings.DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE
_FILTER_VALUE_MAX_PAGE_SIZE = min(
    _DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE,
    _PROPERTY_CATALOG_MAX_PAGE_SIZE,
)


def _validate_property_catalog_project_ids(value):
    if len(value) > _PROPERTY_CATALOG_MAX_PROJECTS:
        raise serializers.ValidationError(
            "At most "
            f"{_PROPERTY_CATALOG_MAX_PROJECTS} project_ids may be searched at once"
        )
    uuid_field = serializers.UUIDField()
    validated = []
    seen = set()
    for raw_value in value:
        try:
            project_id = str(uuid_field.run_validation(raw_value))
        except serializers.ValidationError as exc:
            raise serializers.ValidationError(
                f"Invalid project id: {raw_value}"
            ) from exc
        if project_id not in seen:
            seen.add(project_id)
            validated.append(project_id)
    return validated


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
    property_id = serializers.CharField(required=False, allow_blank=False)
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

        property_id = attrs.get("property_id")
        if property_id:
            metric_type = attrs.get("type")
            metric_identity = {
                "annotation_metric": attrs.get("label_id"),
                "custom_attribute": attrs.get("attribute_key"),
                "custom_column": attrs.get("column_id"),
            }.get(metric_type) or attrs.get("name")
            try:
                validate_property_metric_binding(
                    property_id,
                    metric_name=metric_identity,
                    metric_type=metric_type,
                    source=attrs.get("source"),
                )
            except ValueError as exc:
                raise serializers.ValidationError({"property_id": str(exc)}) from exc

        if not attrs.get("attribute_type"):
            if attrs.get("type") == "custom_attribute":
                attrs["attribute_type"] = (
                    "number"
                    if attrs.get("aggregation", "avg")
                    in DASHBOARD_NUMERIC_ONLY_AGGREGATIONS
                    else "string"
                )
            else:
                # Preserve the historical normalized payload/cache identity for
                # metric kinds that do not consume this field.
                attrs["attribute_type"] = "string"
        return attrs


class DashboardBreakdownSerializer(StrictInputSerializer):
    name = serializers.CharField(required=True, allow_blank=False)
    property_id = serializers.CharField(required=False, allow_blank=False)
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

    def validate(self, attrs):
        property_id = attrs.get("property_id")
        if not property_id:
            return attrs
        metric_type = attrs.get("type") or "system_metric"
        metric_identity = {
            "annotation_metric": attrs.get("label_id"),
            "custom_attribute": attrs.get("attribute_key"),
            "custom_column": attrs.get("column_id"),
        }.get(metric_type) or attrs.get("name")
        try:
            validate_property_metric_binding(
                property_id,
                metric_name=metric_identity,
                metric_type=metric_type,
                source=attrs.get("source"),
            )
        except ValueError as exc:
            raise serializers.ValidationError({"property_id": str(exc)}) from exc
        return attrs


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
            "The response explicitly labels exact, rollup, or unavailable provenance."
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

    @staticmethod
    def _validate_dataset_filter(filter_item):
        config = filter_item.get("filter_config") or {}
        if (
            config.get("col_type") != "SYSTEM_METRIC"
            or filter_item.get("column_id") not in _DATASET_FILTER_DIMENSIONS
        ):
            raise serializers.ValidationError(
                "Dataset filters currently support only Dataset, Eval Template, "
                "Column Name, Column Source, and Cell Status dimensions."
            )

    @staticmethod
    def _validate_simulation_filter(filter_item):
        config = filter_item.get("filter_config") or {}
        if (
            config.get("col_type") != "SYSTEM_METRIC"
            or filter_item.get("column_id") not in _SIMULATION_FILTER_DIMENSIONS
        ):
            raise serializers.ValidationError(
                "Simulation filters support only cataloged system dimensions."
            )

    def validate(self, attrs):
        metrics = attrs.get("metrics") or []
        dataset_metrics = [
            metric for metric in metrics if metric.get("source") == "datasets"
        ]
        simulation_metrics = [
            metric for metric in metrics if metric.get("source") == "simulation"
        ]
        dataset_workflow = attrs.get("workflow") == "dataset"
        simulation_workflow = attrs.get("workflow") == "simulation"
        if (
            not dataset_metrics
            and not dataset_workflow
            and not simulation_metrics
            and not simulation_workflow
        ):
            return attrs

        for filter_item in attrs.get("filters") or []:
            if (
                dataset_workflow
                or filter_item.get("source") in _DATASET_DIMENSION_SOURCES
            ):
                self._validate_dataset_filter(filter_item)

        for metric in dataset_metrics:
            for filter_item in metric.get("filters") or []:
                self._validate_dataset_filter(filter_item)

        for filter_item in attrs.get("filters") or []:
            source = filter_item.get("source")
            if source == "simulation" or (simulation_workflow and not source):
                self._validate_simulation_filter(filter_item)

        for metric in simulation_metrics:
            for filter_item in metric.get("filters") or []:
                self._validate_simulation_filter(filter_item)

        for breakdown in attrs.get("breakdowns") or []:
            if (
                dataset_workflow
                or breakdown.get("source") in _DATASET_DIMENSION_SOURCES
            ) and (
                breakdown.get("type") != "system_metric"
                or breakdown.get("name") not in _DATASET_BREAKDOWN_DIMENSIONS
            ):
                raise serializers.ValidationError(
                    {
                        "breakdowns": (
                            "Dataset breakdowns currently support only Dataset, "
                            "Eval Template, Column Name, Column Source, and Cell "
                            "Status dimensions."
                        )
                    }
                )
            source = breakdown.get("source")
            if (source == "simulation" or (simulation_workflow and not source)) and (
                breakdown.get("type") != "system_metric"
                or breakdown.get("name") not in _SIMULATION_BREAKDOWN_DIMENSIONS
            ):
                raise serializers.ValidationError(
                    {
                        "breakdowns": (
                            "Simulation breakdowns support only cataloged system "
                            "dimensions."
                        )
                    }
                )
        return attrs


class DashboardPreviewQuerySerializer(StrictInputSerializer):
    query_config = DashboardQuerySerializer(required=True)
    allow_sampled = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "Deprecated compatibility parameter; accepted but ignored. "
            "The response explicitly labels exact, rollup, or unavailable provenance."
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
            "The response explicitly labels exact, rollup, or unavailable provenance."
        ),
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class DashboardRefreshQuerySerializer(StrictInputSerializer):
    """Query parameters shared by exact dashboard execution endpoints."""

    refresh = serializers.BooleanField(required=False, default=False)

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class DashboardQuerySeriesPointSerializer(serializers.Serializer):
    timestamp = serializers.CharField()
    value = serializers.FloatField(allow_null=True)


class DashboardQuerySeriesSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True)
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
        choices=["complete", "degraded"], required=False
    )
    query_error_code = serializers.ChoiceField(
        choices=[
            "sample_limit",
            "read_budget_exceeded",
            "query_failed",
            "bounded_shape_unavailable",
            "invalid_window",
            "malformed_result",
        ],
        required=False,
    )
    query_exact = serializers.BooleanField(required=False)
    query_provenance = serializers.ChoiceField(
        choices=[
            "exact_snapshot",
            "materialized_rollup",
            "authorized_empty_scope",
            "bounded_unavailable",
        ],
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
    query_exact = serializers.BooleanField(required=False)
    query_provenance = serializers.ChoiceField(
        choices=[
            "exact_snapshot",
            "materialized_rollup",
            "authorized_empty_scope",
            "bounded_unavailable",
        ],
        required=False,
    )
    query_error_code = serializers.ChoiceField(
        choices=[
            "sample_limit",
            "read_budget_exceeded",
            "query_failed",
            "bounded_shape_unavailable",
            "invalid_window",
            "malformed_result",
        ],
        required=False,
    )
    query_sampling_strategy = serializers.CharField(required=False)
    query_count = serializers.IntegerField(min_value=0, max_value=256, required=False)
    query_rows_returned = serializers.IntegerField(min_value=0, required=False)
    query_elapsed_ms = serializers.FloatField(min_value=0, required=False)
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
    property_id = serializers.CharField()
    property_kind = serializers.ChoiceField(
        choices=[
            "system_attribute",
            "custom_attribute",
            "eval_config",
            "eval_template",
            "annotation",
            "dataset_column",
        ],
    )
    display_name = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(required=False, allow_blank=True)
    sources = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True
    )
    type = serializers.CharField(required=False, allow_blank=True)
    unit = serializers.CharField(required=False, allow_blank=True)
    output_type = serializers.CharField(required=False, allow_blank=True)
    eval_template_id = serializers.UUIDField(required=False)
    role = serializers.ChoiceField(choices=["metric", "dimension"], required=False)
    choices = serializers.ListField(
        child=JsonValueField(), required=False, allow_empty=True
    )
    choice_options = serializers.ListField(
        child=JsonValueField(), required=False, allow_empty=True
    )
    allowed_aggregations = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True
    )
    data_type = serializers.CharField(required=False, allow_blank=True)
    attribute_types = serializers.ListField(
        child=serializers.ChoiceField(
            choices=["string", "number", "boolean", "array", "map", "json"]
        ),
        required=False,
        allow_empty=False,
    )
    attribute_types_exact = serializers.BooleanField(required=False)


class DashboardMetricsCatalogResultSerializer(serializers.Serializer):
    metrics = DashboardMetricCatalogItemSerializer(many=True)
    # Optional for the legacy unpaginated response; always present when any
    # catalog filter or pagination query parameter selects the bounded shape.
    total = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    total_is_exact = serializers.BooleanField(required=False)
    category_counts = serializers.DictField(
        child=serializers.IntegerField(min_value=0),
        required=False,
    )
    category_counts_exact = serializers.BooleanField(required=False)
    page = serializers.IntegerField(min_value=1, required=False)
    page_size = serializers.IntegerField(
        min_value=1,
        max_value=_DASHBOARD_CATALOG_MAX_PAGE_SIZE,
        required=False,
    )
    has_more = serializers.BooleanField(required=False)
    next_cursor = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=False,
        max_length=_PROPERTY_CATALOG_CURSOR_MAX_BYTES,
    )
    catalog_epoch = serializers.IntegerField(
        min_value=1, max_value=65_535, required=False
    )
    catalog_revision = serializers.IntegerField(min_value=1, required=False)
    activation_fingerprint = serializers.RegexField(r"^[0-9a-f]{64}$", required=False)
    query_complete = serializers.BooleanField(required=False)
    query_exact = serializers.BooleanField(required=False)
    query_status = serializers.ChoiceField(choices=["complete"], required=False)
    query_provenance = serializers.ChoiceField(
        choices=["activated_property_catalog"], required=False
    )


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


class DashboardMetricsCatalogQuerySerializer(StrictInputSerializer):
    """Strict query contract for the unified dashboard property catalog."""

    workflow = serializers.ChoiceField(
        choices=("observability", "dataset", "simulation"),
        required=False,
    )
    project_ids = CommaSeparatedListField(required=False, default=list)
    agent_definition_id = serializers.UUIDField(required=False)
    per_eval_config = serializers.BooleanField(required=False, default=False)
    exclude_custom_attributes = serializers.BooleanField(
        required=False,
        default=False,
    )
    search = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=_DASHBOARD_CATALOG_SEARCH_MAX_CHARS,
        trim_whitespace=True,
    )
    category = serializers.ChoiceField(
        choices=DASHBOARD_METRIC_TYPES,
        required=False,
        allow_blank=True,
        default="",
    )
    role = serializers.ChoiceField(
        choices=("metric", "dimension"),
        required=False,
        allow_blank=True,
        default="",
    )
    source = serializers.ChoiceField(
        choices=PROPERTY_CATALOG_METRIC_SOURCES,
        required=False,
        allow_blank=True,
        default="",
    )
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=_DASHBOARD_CATALOG_MAX_PAGE_SIZE,
    )
    cursor_mode = serializers.BooleanField(required=False, default=False)
    cursor = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=_PROPERTY_CATALOG_CURSOR_MAX_BYTES,
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}

    def validate_project_ids(self, value):
        return _validate_property_catalog_project_ids(value)

    def validate_search(self, value):
        if len(value.encode("utf-8")) > _PROPERTY_CATALOG_MAX_SEARCH_BYTES:
            raise serializers.ValidationError(
                f"Search exceeds {_PROPERTY_CATALOG_MAX_SEARCH_BYTES} UTF-8 bytes"
            )
        return value

    def validate(self, attrs):
        cursor = attrs.get("cursor")
        cursor_mode = attrs.get("cursor_mode", False)
        if cursor and not cursor_mode:
            raise serializers.ValidationError(
                {"cursor_mode": "cursor_mode=true is required with cursor"}
            )
        if cursor_mode and "page_size" not in attrs:
            raise serializers.ValidationError(
                {"page_size": "page_size is required in cursor mode"}
            )
        if cursor_mode and attrs.get("page_size", 0) > _PROPERTY_CATALOG_MAX_PAGE_SIZE:
            raise serializers.ValidationError(
                {
                    "page_size": (
                        "page_size must be at most "
                        f"{_PROPERTY_CATALOG_MAX_PAGE_SIZE} in cursor mode"
                    )
                }
            )
        if cursor_mode and "page" in attrs:
            raise serializers.ValidationError(
                {"page": "page is not supported in cursor mode"}
            )
        if cursor_mode and attrs.get("exclude_custom_attributes"):
            raise serializers.ValidationError(
                {
                    "exclude_custom_attributes": (
                        "cursor mode is the unified all-property catalog"
                    )
                }
            )
        if cursor_mode and attrs.get("workflow"):
            raise serializers.ValidationError(
                {"workflow": "workflow is not supported in unified cursor mode"}
            )
        if (
            cursor_mode
            and attrs.get("category") == "custom_attribute"
            and attrs.get("source")
        ):
            try:
                attrs["source"] = normalize_custom_attribute_source(attrs["source"])
            except ValueError as exc:
                raise serializers.ValidationError({"source": str(exc)}) from exc
        if not cursor_mode and attrs.get("role"):
            raise serializers.ValidationError(
                {"role": "role requires cursor_mode=true"}
            )
        if (
            not cursor_mode
            and attrs.get("source")
            and attrs["source"] not in DASHBOARD_METRIC_SOURCES
        ):
            raise serializers.ValidationError(
                {"source": ("Logical property sources require cursor_mode=true")}
            )
        return attrs


class DashboardFilterValuesQuerySerializer(serializers.Serializer):
    property_id = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=1_024,
        help_text=(
            "Stable namespaced property identity returned by the metrics catalog. "
            "Legacy metric_name/metric_type remain accepted during migration."
        ),
    )
    metric_name = serializers.CharField(required=False, allow_blank=False)
    metric_type = serializers.ChoiceField(
        choices=[
            "system_metric",
            "eval_metric",
            "annotation_metric",
            "custom_attribute",
            "custom_column",
        ],
        required=False,
    )
    source = serializers.ChoiceField(
        choices=[
            "traces",
            "spans",
            "sessions",
            "users",
            "voice_calls",
            "prompts",
            "datasets",
            "dataset_column",
            "simulation",
            "both",
            "all",
        ],
        required=False,
        default="traces",
    )
    # Keep the established comma-separated query-string/OpenAPI default while
    # ``CommaSeparatedListField.run_validation`` normalizes an omitted value to
    # the runtime list contract consumed by the scoped readers.
    project_ids = CommaSeparatedListField(required=False, default="")
    dataset_id = serializers.UUIDField(required=False)
    search = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        # The selector enforces the same limit in encoded UTF-8 bytes.  This
        # character cap keeps obviously oversized requests out of every
        # source-specific branch before any database work.
        max_length=_PROPERTY_CATALOG_MAX_SEARCH_BYTES,
    )
    page_size = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=_FILTER_VALUE_MAX_PAGE_SIZE,
    )
    cursor = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=_PROPERTY_CATALOG_CURSOR_MAX_BYTES,
    )
    attribute_type = serializers.ChoiceField(
        choices=["string", "number", "boolean", "array", "map", "json"],
        required=False,
    )

    def validate_page_size(self, value):
        max_page_size = min(
            settings.DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE,
            settings.PROPERTY_CATALOG_MAX_PAGE_SIZE,
        )
        if value > max_page_size:
            raise serializers.ValidationError(
                f"page_size must be at most {max_page_size}"
            )
        return value

    def validate_project_ids(self, value):
        return _validate_property_catalog_project_ids(value)

    def validate(self, attrs):
        property_id = attrs.get("property_id")
        if property_id:
            try:
                decoded = parse_property_registry_id(property_id)
            except ValueError as exc:
                raise serializers.ValidationError({"property_id": str(exc)}) from exc
            supplied_name = attrs.get("metric_name")
            supplied_type = attrs.get("metric_type")
            if supplied_name is not None and supplied_name != decoded["metric_name"]:
                raise serializers.ValidationError(
                    {"metric_name": "metric_name does not match property_id"}
                )
            if supplied_type is not None and supplied_type != decoded["metric_type"]:
                raise serializers.ValidationError(
                    {"metric_type": "metric_type does not match property_id"}
                )
            try:
                validate_property_metric_binding(
                    property_id,
                    metric_name=decoded["metric_name"],
                    metric_type=decoded["metric_type"],
                    source=attrs.get("source"),
                )
            except ValueError as exc:
                raise serializers.ValidationError({"property_id": str(exc)}) from exc
            attrs["metric_name"] = decoded["metric_name"]
            attrs["metric_type"] = decoded["metric_type"]
            # Keep the decoded definition kind in the internal request
            # contract. Eval config and eval template UUIDs share the same
            # native metric family, so downstream adapters must not guess the
            # definition type from UUID lookup order.
            attrs["_property_kind"] = decoded["property_kind"]
        elif not attrs.get("metric_name"):
            raise serializers.ValidationError(
                {"metric_name": "metric_name or property_id is required"}
            )
        else:
            attrs.setdefault("metric_type", "system_metric")
        if attrs["metric_type"] == "custom_attribute":
            try:
                attrs["source"] = normalize_custom_attribute_source(
                    attrs.get("source", "traces")
                )
            except ValueError as exc:
                raise serializers.ValidationError({"source": str(exc)}) from exc
        else:
            attrs["source"] = property_value_transport_source(
                attrs.get("source", "traces")
            )
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
    query_window_mode = serializers.ChoiceField(
        choices=["frozen_snapshot"],
        required=False,
    )
    query_count = serializers.IntegerField(required=False, min_value=0)
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
    attribute_types = serializers.ListField(
        child=serializers.ChoiceField(
            choices=["string", "number", "boolean", "array", "map", "json"]
        ),
        required=False,
        allow_empty=False,
    )
    attribute_types_exact = serializers.BooleanField(required=False)
    catalog_epoch = serializers.IntegerField(
        min_value=1, max_value=65_535, required=False
    )
    catalog_revision = serializers.IntegerField(min_value=1, required=False)
    activation_fingerprint = serializers.RegexField(r"^[0-9a-f]{64}$", required=False)
    query_provenance = serializers.ChoiceField(
        choices=["activated_property_catalog"], required=False
    )


class DashboardFilterValuesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = DashboardFilterValuesResultSerializer()
