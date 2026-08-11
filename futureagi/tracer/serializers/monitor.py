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
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.filter(trace_type="observe"), required=True
    )
    name = serializers.CharField(required=True)
    metric_name = serializers.SerializerMethodField()

    class Meta:
        model = UserAlertMonitor
        fields = "__all__"

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
    resolved_by = UserSerializer(read_only=True)

    class Meta:
        model = UserAlertMonitorLog
        exclude = ["deleted_at", "deleted", "alert", "updated_at"]


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
