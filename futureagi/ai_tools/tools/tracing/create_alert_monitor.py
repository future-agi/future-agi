from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class CreateAlertMonitorInput(PydanticBaseModel):
    name: str = Field(description="Name of the alert monitor")
    metric_type: str = Field(
        description=(
            "Metric type to monitor. Options: count_of_errors, "
            "error_rates_for_function_calling, error_free_session_rates, "
            "service_provider_error_rates, llm_api_failure_rates, "
            "span_response_time, llm_response_time, token_usage, "
            "daily_tokens_spent, monthly_tokens_spent, evaluation_metrics"
        )
    )
    threshold_operator: str = Field(
        description="Comparison operator: 'greater_than' or 'less_than'"
    )
    critical_threshold_value: Optional[float] = Field(
        default=None,
        description="Critical threshold value that triggers a critical alert",
    )
    warning_threshold_value: Optional[float] = Field(
        default=None,
        description="Warning threshold value that triggers a warning alert",
    )
    threshold_type: str = Field(
        default="percentage_change",
        description="Threshold calculation method: 'static' or 'percentage_change'",
    )
    alert_frequency: int = Field(
        default=60,
        ge=5,
        description="How often to check the monitor, in minutes (minimum 5)",
    )
    project_id: Optional[UUID] = Field(
        default=None, description="Optional project ID to scope the monitor to"
    )
    notification_emails: Optional[list[str]] = Field(
        default=None, description="List of email addresses for alert notifications"
    )
    metric: Optional[str] = Field(
        default=None,
        description="Metric ID for evaluation_metrics type (CustomEvalConfig UUID)",
    )
    threshold_metric_value: Optional[str] = Field(
        default=None,
        description="Threshold metric value for evaluation metrics with predefined choices",
    )
    filters: Optional[dict] = Field(
        default=None,
        description="Optional filters dict with observation_type (list) and span_attributes_filters (list of dicts)",
    )


@register_tool
class CreateAlertMonitorTool(BaseTool):
    name = "create_alert_monitor"
    description = (
        "Creates a new alert monitor that tracks a metric and triggers alerts "
        "when thresholds are exceeded. Supports error rates, response times, "
        "token usage, and evaluation metrics."
    )
    category = "tracing"
    input_model = CreateAlertMonitorInput

    def execute(
        self, params: CreateAlertMonitorInput, context: ToolContext
    ) -> ToolResult:

        from tracer.models.monitor import (
            ComparisonOperatorChoices,
            MonitorMetricTypeChoices,
            ThresholdCalculationMethodChoices,
            UserAlertMonitor,
        )

        # Validate metric_type
        valid_metrics = [c[0] for c in MonitorMetricTypeChoices.choices]
        if params.metric_type not in valid_metrics:
            return ToolResult.error(
                f"Invalid metric_type '{params.metric_type}'. "
                f"Valid options: {', '.join(valid_metrics)}",
                error_code="VALIDATION_ERROR",
            )

        # Validate threshold_operator
        valid_ops = [c[0] for c in ComparisonOperatorChoices.choices]
        if params.threshold_operator not in valid_ops:
            return ToolResult.error(
                f"Invalid threshold_operator '{params.threshold_operator}'. "
                f"Valid options: {', '.join(valid_ops)}",
                error_code="VALIDATION_ERROR",
            )

        # Validate threshold_type
        valid_types = [c[0] for c in ThresholdCalculationMethodChoices.choices]
        if params.threshold_type not in valid_types:
            return ToolResult.error(
                f"Invalid threshold_type '{params.threshold_type}'. "
                f"Valid options: {', '.join(valid_types)}",
                error_code="VALIDATION_ERROR",
            )

        # Ensure at least one threshold is provided
        if (
            params.critical_threshold_value is None
            and params.warning_threshold_value is None
        ):
            return ToolResult.error(
                "Provide at least one of: critical_threshold_value or warning_threshold_value.",
                error_code="VALIDATION_ERROR",
            )

        # Validate max 5 notification emails
        if params.notification_emails and len(params.notification_emails) > 5:
            return ToolResult.error(
                "You can specify at most 5 notification emails.",
                error_code="VALIDATION_ERROR",
            )

        # Validate project if provided
        project = None
        if params.project_id:
            from tracer.models.project import Project

            try:
                project = Project.objects.get(id=params.project_id)
            except Project.DoesNotExist:
                return ToolResult.not_found("Project", str(params.project_id))

            # Validate project belongs to user's organization
            if project.organization_id != context.organization.id:
                return ToolResult.error(
                    "This project does not belong to your organization.",
                    error_code="VALIDATION_ERROR",
                )

        # Validate unique name per project
        if project:
            if UserAlertMonitor.objects.filter(
                project=project, name=params.name
            ).exists():
                return ToolResult.error(
                    f"An alert with the name '{params.name}' already exists in this project.",
                    error_code="VALIDATION_ERROR",
                )

        # Validate threshold relationships
        if params.threshold_type in [
            ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE.value,
            ThresholdCalculationMethodChoices.STATIC.value,
        ]:
            if params.critical_threshold_value is None:
                return ToolResult.error(
                    "Critical threshold is required for percentage change and static threshold.",
                    error_code="VALIDATION_ERROR",
                )
            if (
                params.threshold_operator in ["greater_than", "less_than"]
                and params.warning_threshold_value is not None
            ):
                if params.threshold_operator == "greater_than" and not (
                    params.critical_threshold_value > params.warning_threshold_value
                ):
                    return ToolResult.error(
                        "Critical threshold must be greater than warning threshold for 'greater_than' operator.",
                        error_code="VALIDATION_ERROR",
                    )
                if params.threshold_operator == "less_than" and not (
                    params.critical_threshold_value < params.warning_threshold_value
                ):
                    return ToolResult.error(
                        "Critical threshold must be less than warning threshold for 'less_than' operator.",
                        error_code="VALIDATION_ERROR",
                    )

        # Validate evaluation metrics
        if params.metric_type == MonitorMetricTypeChoices.EVALUATION_METRICS.value:
            if not params.metric:
                return ToolResult.error(
                    "Metric is required for evaluation metrics.",
                    error_code="VALIDATION_ERROR",
                )
            from tracer.models.custom_eval_config import CustomEvalConfig

            try:
                custom_eval_config = CustomEvalConfig.objects.get(
                    id=params.metric, project=project
                )
            except CustomEvalConfig.DoesNotExist:
                return ToolResult.error(
                    f"Invalid metric '{params.metric}' for this project.",
                    error_code="VALIDATION_ERROR",
                )

            choices = (
                custom_eval_config.eval_template.choices
                if custom_eval_config.eval_template
                and custom_eval_config.eval_template.choices
                else None
            )
            if choices:
                if params.threshold_metric_value is None:
                    return ToolResult.error(
                        "threshold_metric_value is required for evals with predefined choices.",
                        error_code="VALIDATION_ERROR",
                    )
                if str(params.threshold_metric_value) not in choices:
                    return ToolResult.error(
                        f"'{params.threshold_metric_value}' is not valid. Available choices are: {', '.join(map(str, choices))}",
                        error_code="VALIDATION_ERROR",
                    )
            elif params.threshold_metric_value is not None:
                return ToolResult.error(
                    "threshold_metric_value must be empty for evals without predefined choices.",
                    error_code="VALIDATION_ERROR",
                )
        else:
            if params.metric or params.threshold_metric_value:
                return ToolResult.error(
                    f"Metric and threshold_metric_value are not allowed for metric type {params.metric_type}.",
                    error_code="VALIDATION_ERROR",
                )

        # Validate filters
        if params.filters:
            if not isinstance(params.filters, dict):
                return ToolResult.error(
                    "Filters must be a dictionary.",
                    error_code="VALIDATION_ERROR",
                )
            from tracer.models.observation_span import ObservationSpan

            valid_span_types = [t[0] for t in ObservationSpan.OBSERVATION_SPAN_TYPES]

            observation_type = params.filters.get("observation_type")
            if observation_type:
                if not isinstance(observation_type, list):
                    return ToolResult.error(
                        "observation_type filter must be a list of strings.",
                        error_code="VALIDATION_ERROR",
                    )
                invalid_types = [
                    t for t in observation_type if t not in valid_span_types
                ]
                if invalid_types:
                    return ToolResult.error(
                        f"Invalid observation types: {', '.join(invalid_types)}. "
                        f"Allowed values: {', '.join(valid_span_types)}",
                        error_code="VALIDATION_ERROR",
                    )

            span_attributes_filters = params.filters.get("span_attributes_filters")
            if span_attributes_filters and not isinstance(
                span_attributes_filters, list
            ):
                return ToolResult.error(
                    "span_attributes_filters must be a list of dictionaries.",
                    error_code="VALIDATION_ERROR",
                )

        monitor = UserAlertMonitor(
            name=params.name,
            metric_type=params.metric_type,
            threshold_operator=params.threshold_operator,
            threshold_type=params.threshold_type,
            critical_threshold_value=params.critical_threshold_value,
            warning_threshold_value=params.warning_threshold_value,
            alert_frequency=params.alert_frequency,
            project=project,
            organization=context.organization,
            workspace=context.workspace,
            created_by=context.user,
            notification_emails=params.notification_emails or [],
            metric=params.metric,
            threshold_metric_value=params.threshold_metric_value,
            filters=params.filters,
        )
        monitor.save()

        info = key_value_block(
            [
                ("Monitor ID", f"`{monitor.id}`"),
                ("Name", monitor.name),
                ("Metric Type", monitor.metric_type),
                ("Operator", monitor.threshold_operator),
                ("Threshold Type", monitor.threshold_type),
                (
                    "Critical Threshold",
                    (
                        str(monitor.critical_threshold_value)
                        if monitor.critical_threshold_value is not None
                        else "—"
                    ),
                ),
                (
                    "Warning Threshold",
                    (
                        str(monitor.warning_threshold_value)
                        if monitor.warning_threshold_value is not None
                        else "—"
                    ),
                ),
                ("Alert Frequency", f"{monitor.alert_frequency} min"),
                ("Project", project.name if project else "All projects"),
                (
                    "Emails",
                    (
                        ", ".join(monitor.notification_emails)
                        if monitor.notification_emails
                        else "—"
                    ),
                ),
            ]
        )

        content = section("Alert Monitor Created", info)

        return ToolResult(
            content=content,
            data={
                "monitor_id": str(monitor.id),
                "name": monitor.name,
                "metric_type": monitor.metric_type,
            },
        )
