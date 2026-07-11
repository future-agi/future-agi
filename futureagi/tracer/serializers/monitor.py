import json

from rest_framework import serializers

from accounts.serializers.user import UserSerializer
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.monitor import (
    AlertTypeChoices,
    MonitorMetricTypeChoices,
    ThresholdCalculationMethodChoices,
    UserAlertMonitor,
    UserAlertMonitorLog,
)
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.serializers.filters import StrictInputSerializer, filter_list_field

OBSERVATION_SPAN_TYPES = [t[0] for t in ObservationSpan.OBSERVATION_SPAN_TYPES]


class UserAlertMonitorSerializer(serializers.ModelSerializer):
    """An alert monitor that watches one telemetry metric on an observe trace project
    and fires critical/warning alerts (email + Slack) when it crosses a threshold.
    Listed/read via list_alert_monitors / get_alert_monitor and created/edited via
    create_alert_monitor / update_alert_monitor. Configure it with a `metric_type`,
    a `threshold_type` (static or percentage_change), threshold values, and a
    `threshold_operator`; for `evaluation_metrics`, point `metric` at a CustomEvalConfig."""

    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.filter(trace_type="observe"),
        required=True,
        help_text="UUID of the observe trace project to monitor (from list_projects).",
    )
    name = serializers.CharField(
        required=True,
        help_text="Human-readable name for this monitor; must be unique within the project.",
    )
    metric_name = serializers.SerializerMethodField()

    class Meta:
        model = UserAlertMonitor
        fields = "__all__"
        extra_kwargs = {
            "threshold_operator": {
                "help_text": (
                    "How the metric is compared to the threshold: 'greater_than' or "
                    "'less_than'."
                ),
            },
            "critical_threshold_value": {
                "help_text": (
                    "Value (or percentage, for percentage_change thresholds) that "
                    "triggers a critical alert. Required for static/percentage_change."
                ),
            },
            "warning_threshold_value": {
                "help_text": (
                    "Optional value (or percentage) that triggers a warning alert; "
                    "must be on the non-critical side of critical_threshold_value."
                ),
            },
            "notification_emails": {
                "help_text": "Up to 5 email addresses to notify when the monitor fires.",
            },
            "slack_webhook_url": {
                "help_text": "Optional Slack incoming-webhook URL to post alerts to.",
            },
            "slack_notes": {
                "help_text": "Optional note included in the Slack alert message.",
            },
            "is_mute": {
                "help_text": "If true, the monitor is muted and will not send notifications.",
            },
            "filters": {
                "help_text": (
                    "Optional JSON filters scoping which spans the metric is computed "
                    "over (e.g. observation_type, span_attributes_filters)."
                ),
            },
            "metric_type": {
                "help_text": (
                    "What this alert monitor watches. Allowed values: "
                    "count_of_errors (total error count); "
                    "error_rates_for_function_calling (tool/function-call error rate); "
                    "error_free_session_rates (percent of sessions with no errors); "
                    "service_provider_error_rates (upstream provider error rate); "
                    "llm_api_failure_rates (LLM API failure rate); "
                    "span_response_time (span latency); "
                    "llm_response_time (LLM latency); "
                    "token_usage (tokens per window); "
                    "daily_tokens_spent / monthly_tokens_spent (token spend); "
                    "evaluation_metrics (alert on an eval score — set `metric` to "
                    "the CustomEvalConfig id to monitor)."
                ),
            },
        }

    def get_metric_name(self, obj):
        if obj.metric_type == MonitorMetricTypeChoices.EVALUATION_METRICS.value:
            if obj.metric:
                eval_config = (
                    CustomEvalConfig.objects.filter(
                        id=obj.metric,
                        project=obj.project,
                        deleted=False,
                    )
                    .select_related("eval_template")
                    .first()
                )
                if eval_config:
                    metric_name = eval_config.name
                    if obj.threshold_metric_value:
                        metric_name += f" ({obj.threshold_metric_value})"
                    return metric_name
                return "Invalid Eval"
        return obj.get_metric_type_display()

    def validate_notification_emails(self, value):
        if value and len(value) > 5:
            raise serializers.ValidationError(
                "You can specify at most 5 notification emails."
            )
        return value

    def _validate_project_organization(self, project, organization):
        if project and organization and project.organization != organization:
            raise serializers.ValidationError(
                "This project does not belong to the provided organization."
            )

    def _validate_unique_name(self, project, name):
        if project and name:
            queryset = UserAlertMonitor.objects.filter(project=project, name=name)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError(
                    f"An alert with the name '{name}' already exists in this project."
                )

    def _validate_metric_type(self, data):
        metric_type = data.get("metric_type")
        metric = data.get("metric")
        project = data.get("project")
        threshold_metric_value = data.get("threshold_metric_value")

        if metric_type == MonitorMetricTypeChoices.EVALUATION_METRICS.value:
            if not metric:
                raise serializers.ValidationError(
                    {"metric": "Metric is required for evaluation metrics."}
                )
            try:
                custom_eval_config = CustomEvalConfig.objects.get(
                    id=metric, project=project
                )
            except CustomEvalConfig.DoesNotExist:
                raise serializers.ValidationError(  # noqa: B904
                    {"metric": f"Invalid metric format for '{metric}'."}
                )

            choices = (
                custom_eval_config.eval_template.choices
                if custom_eval_config.eval_template
                and custom_eval_config.eval_template.choices
                else None
            )
            if choices:
                if threshold_metric_value is None:
                    raise serializers.ValidationError(
                        {
                            "threshold_metric_value": "This field is required for evals with predefined choices."
                        }
                    )
                if str(threshold_metric_value) not in choices:
                    raise serializers.ValidationError(
                        {
                            "threshold_metric_value": f"'{threshold_metric_value}' is not valid. Available choices are: {', '.join(map(str, choices))}"
                        }
                    )
            elif threshold_metric_value is not None:
                raise serializers.ValidationError(
                    {
                        "threshold_metric_value": "This field must be empty for evals without predefined choices."
                    }
                )
        else:
            if metric or threshold_metric_value:
                raise serializers.ValidationError(
                    f"Metric and threshold_metric_value are not allowed for metric type {metric_type}"
                )

    def _validate_threshold_type(self, data):
        threshold_type = data.get("threshold_type")
        critical_threshold_value = data.get("critical_threshold_value")
        warning_threshold_value = data.get("warning_threshold_value")
        threshold_operator = data.get("threshold_operator")

        if threshold_type in [
            ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE.value,
            ThresholdCalculationMethodChoices.STATIC.value,
        ]:
            if critical_threshold_value is None:
                raise serializers.ValidationError(
                    "Critical threshold is required for percentage change and static threshold."
                )

            if (
                threshold_operator in ["greater_than", "less_than"]
                and warning_threshold_value is not None
            ):
                if threshold_operator == "greater_than" and not (
                    critical_threshold_value > warning_threshold_value
                ):
                    raise serializers.ValidationError(
                        "Critical threshold must be greater than warning threshold for 'greater_than' operator."
                    )
                if threshold_operator == "less_than" and not (
                    critical_threshold_value < warning_threshold_value
                ):
                    raise serializers.ValidationError(
                        "Critical threshold must be less than warning threshold for 'less_than' operator."
                    )
        # elif threshold_type == ThresholdCalculationMethodChoices.ANOMALY_DETECTION.value:
        #     if critical_threshold_value is not None or warning_threshold_value is not None:
        #         raise serializers.ValidationError("Critical and warning threshold values are not allowed for anomaly detection.")

    def validate_filters(self, filters):
        if filters:
            if not isinstance(filters, dict):
                raise serializers.ValidationError("Filters must be a dictionary.")

            observation_type = filters.get("observation_type")
            span_attributes_filters = filters.get("span_attributes_filters")

            if observation_type:
                if not isinstance(observation_type, list):
                    raise serializers.ValidationError(
                        {
                            "observation_type": "observation_type filter must be a list of strings."
                        }
                    )

                invalid_types = [
                    t for t in observation_type if t not in OBSERVATION_SPAN_TYPES
                ]
                if invalid_types:
                    raise serializers.ValidationError(
                        {
                            "observation_type": f"Invalid values: {', '.join(invalid_types)}. Allowed values are: {', '.join(OBSERVATION_SPAN_TYPES)}"
                        }
                    )
            if span_attributes_filters:
                filters["span_attributes_filters"] = filter_list_field().run_validation(
                    span_attributes_filters
                )
        return filters

    def validate(self, data):
        validated_data = super().validate(data)

        # For partial updates, we need to combine the instance data with the incoming data
        if self.instance:
            full_data = self.instance.__dict__.copy()
            full_data.update(validated_data)
            # Ensure related fields are objects for validation
            if "project" in validated_data:
                full_data["project"] = validated_data["project"]
            else:
                full_data["project"] = self.instance.project
            if "organization" in validated_data:
                full_data["organization"] = validated_data["organization"]
            else:
                full_data["organization"] = self.instance.organization
        else:
            full_data = validated_data

        project = full_data.get("project")
        organization = full_data.get("organization")
        name = full_data.get("name")
        filters = full_data.get("filters")

        self._validate_project_organization(project, organization)
        self._validate_unique_name(project, name)
        self._validate_metric_type(full_data)
        self._validate_threshold_type(full_data)
        if "filters" in validated_data and filters:
            validated_data["filters"] = self.validate_filters(filters)

        return validated_data


class UserAlertMonitorBulkMuteRequestSerializer(StrictInputSerializer):
    ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    is_mute = serializers.BooleanField(required=False, default=True)
    select_all = serializers.BooleanField(required=False, default=False)
    exclude_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )


class UserAlertMonitorPreviewGraphSerializer(UserAlertMonitorSerializer):
    name = serializers.CharField(required=False, allow_blank=True)


class UserAlertMonitorLogSerializer(serializers.ModelSerializer):
    """A single firing/log entry for an alert monitor — recorded each time a monitor's threshold is breached. It captures the severity, message, the time window evaluated, and whether it has been resolved. List these via list_alert_monitor_logs (or filter to one monitor) and read one with get_alert_monitor_log to triage what tripped the alert."""

    resolved_by = UserSerializer(
        read_only=True,
        help_text="The user who marked this alert log as resolved, if any.",
    )

    class Meta:
        model = UserAlertMonitorLog
        exclude = ["deleted_at", "deleted", "alert", "updated_at"]
        extra_kwargs = {
            "id": {
                "help_text": "UUID of this alert log entry; pass it to get_alert_monitor_log."
            },
            "type": {
                "help_text": "Severity of the alert: 'critical' or 'warning'."
            },
            "message": {
                "help_text": "Human-readable description of what tripped the monitor."
            },
            "resolved": {
                "help_text": "Whether this alert log has been marked resolved."
            },
            "resolved_at": {
                "help_text": "Timestamp when the alert log was resolved, if resolved."
            },
            "link": {
                "help_text": "Deep link to the relevant view (e.g. the monitor or affected traces), if set."
            },
            "time_window_start": {
                "help_text": "Start of the time window the monitor evaluated when it fired."
            },
            "time_window_end": {
                "help_text": "End of the time window the monitor evaluated when it fired."
            },
            "created_at": {
                "help_text": "When this alert fired (the log entry was created)."
            },
        }


class UserAlertMonitorLogWriteSerializer(serializers.ModelSerializer):
    alert = serializers.PrimaryKeyRelatedField(queryset=UserAlertMonitor.objects.none())
    resolved_by = UserSerializer(read_only=True)

    class Meta:
        model = UserAlertMonitorLog
        fields = [
            "id",
            "alert",
            "type",
            "message",
            "resolved",
            "resolved_at",
            "resolved_by",
            "link",
            "time_window_start",
            "time_window_end",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "resolved_by"]


class UserAlertMonitorLogWriteRequestSerializer(StrictInputSerializer):
    alert = serializers.UUIDField()
    type = serializers.ChoiceField(choices=AlertTypeChoices.choices)
    message = serializers.CharField()
    resolved = serializers.BooleanField(required=False, default=False)
    resolved_at = serializers.DateTimeField(required=False, allow_null=True)
    link = serializers.URLField(required=False, allow_blank=True, allow_null=True)
    time_window_start = serializers.DateTimeField(required=False, allow_null=True)
    time_window_end = serializers.DateTimeField(required=False, allow_null=True)


class UserAlertMonitorLogWriteResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    alert = serializers.UUIDField()
    type = serializers.ChoiceField(choices=AlertTypeChoices.choices)
    message = serializers.CharField()
    resolved = serializers.BooleanField()
    resolved_at = serializers.DateTimeField(required=False, allow_null=True)
    resolved_by = UserSerializer(required=False, allow_null=True)
    link = serializers.URLField(required=False, allow_blank=True, allow_null=True)
    time_window_start = serializers.DateTimeField(required=False, allow_null=True)
    time_window_end = serializers.DateTimeField(required=False, allow_null=True)
    created_at = serializers.DateTimeField()


class UserAlertMonitorLogResolveRequestSerializer(StrictInputSerializer):
    log_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    select_all = serializers.BooleanField(required=False, default=False)
    exclude_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )


class UserAlertMonitorLogResolveResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.CharField()


class UserAlertMonitorDuplicateSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField(max_length=255)


class UserAlertMonitorDuplicateResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    message = serializers.CharField()


class UserAlertMonitorDuplicateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = UserAlertMonitorDuplicateResultSerializer()


class UserAlertMonitorMetricOptionSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    metric_type = serializers.CharField(read_only=True)
    output_type = serializers.CharField(read_only=True, allow_blank=True)


class UserAlertMonitorMetricOptionsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = UserAlertMonitorMetricOptionSerializer(many=True, read_only=True)


class UserAlertMonitorDetailSerializer(serializers.ModelSerializer):
    metric_name = serializers.SerializerMethodField()
    created_by = UserSerializer(read_only=True)

    class Meta:
        model = UserAlertMonitor
        # fields = "__all__"
        exclude = ["deleted_at", "deleted", "organization", "logs"]

    def get_metric_name(self, obj):
        if obj.metric_type == MonitorMetricTypeChoices.EVALUATION_METRICS.value:
            if obj.metric:
                eval_config = (
                    CustomEvalConfig.objects.filter(
                        id=obj.metric,
                        project=obj.project,
                        deleted=False,
                    )
                    .select_related("eval_template")
                    .first()
                )
                if eval_config:
                    metric_name = eval_config.name
                    if obj.threshold_metric_value:
                        metric_name += f" ({obj.threshold_metric_value})"
                    return metric_name
                return "Invalid Eval"
        return obj.get_metric_type_display()


class MetricDetailSerializer(serializers.ModelSerializer):
    output_type = serializers.SerializerMethodField()

    class Meta:
        model = CustomEvalConfig
        fields = ["id", "name", "output_type"]

    def get_output_type(self, obj):
        return obj.eval_template.config.get("output")


class FetchGraphMetricConfigField(serializers.Field):
    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError(
                    "req_data_config must be valid JSON."
                ) from exc
        if not isinstance(data, dict):
            raise serializers.ValidationError("req_data_config must be an object.")
        if "type" not in data:
            raise serializers.ValidationError("req_data_config.type is required.")
        if data["type"] not in ("EVAL", "SYSTEM_METRIC", "SYSTEM_METRICS"):
            raise serializers.ValidationError(
                "req_data_config.type must be EVAL, SYSTEM_METRIC, or SYSTEM_METRICS."
            )
        return data

    def to_representation(self, value):
        return value


class FetchGraphSerializer(StrictInputSerializer):
    interval = serializers.CharField()
    filters = filter_list_field(required=False, default=list)
    property = serializers.CharField(
        required=False, allow_blank=True, default="average"
    )
    req_data_config = FetchGraphMetricConfigField()
    project_id = serializers.UUIDField()
