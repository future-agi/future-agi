import uuid

from django.contrib.postgres.fields import ArrayField
from django.core.validators import MinValueValidator
from django.db import models

from accounts.models.organization import Organization
from accounts.models.user import User
from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class ComparisonOperatorChoices(models.TextChoices):
    """Choices for comparison operators used in threshold conditions."""

    GREATER_THAN = "greater_than", "Greater than"
    LESS_THAN = "less_than", "Less than"
    # GREATER_OR_EQUAL = "greater_or_equal_to", "Greater or equal to"
    # LESS_OR_EQUAL = "less_than_or_equal_to", "Less than or equal to"


class MonitorMetricTypeChoices(models.TextChoices):
    """Choices for different types of metrics that can be monitored."""

    # Error-related metrics
    COUNT_OF_ERRORS = "count_of_errors", "Count of errors"
    ERROR_RATES_FOR_FUNCTION_CALLING = (
        "error_rates_for_function_calling",
        "Error rates for function calling",
    )
    ERROR_FREE_SESSION_RATES = "error_free_session_rates", "Error free session rates"
    # USER_EXPERIENCE_ERROR = "user_experience_error", "User experience error" # TODO: need to add a sep col for this in trace similar to session_id
    SERVICE_PROVIDER_ERROR_RATES = (
        "service_provider_error_rates",
        "Service provider error rates",
    )
    LLM_API_FAILURE_RATES = "llm_api_failure_rates", "LLM API failure rates"

    # Performance metrics
    SPAN_RESPONSE_TIME = "span_response_time", "Span response time"
    LLM_RESPONSE_TIME = "llm_response_time", "LLM response time"

    # Usage and cost metrics
    TOKEN_USAGE = "token_usage", "Token usage"
    DAILY_TOKENS_SPENT = "daily_tokens_spent", "Daily tokens spent"
    MONTHLY_TOKENS_SPENT = "monthly_tokens_spent", "Monthly tokens spent"
    # CREDIT_EXHAUSTION = "credit_exhaustion", "Credit exhaustion" # we don't have this data in the db yet, removed for now

    # Other metrics
    EVALUATION_METRICS = "evaluation_metrics", "Evaluation metrics"
    # RATE_LIMIT_ALERT = "rate_limit_alert", "Rate limit alert" # we don't have this data in the db yet, removed for now


class ThresholdCalculationMethodChoices(models.TextChoices):
    """Choices for how threshold values are calculated or determined."""

    STATIC = "static", "Static"
    PERCENTAGE_CHANGE = "percentage_change", "Percentage Change"
    # ANOMALY_DETECTION = "anomaly_detection", "Anomaly Detection"


class UserAlertMonitor(BaseModel):
    """Model for user-defined alert monitors that track various metrics."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)

    # Metric configuration
    metric_type = models.CharField(max_length=100, choices=MonitorMetricTypeChoices)
    metric = models.CharField(
        max_length=2556,
        help_text="Id of the evaluation template.",
        null=True,
        blank=True,
    )

    # Organization and project
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="user_alert_monitors",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, null=True, blank=True
    )

    # Threshold configuration
    threshold_operator = models.CharField(
        max_length=100, choices=ComparisonOperatorChoices
    )
    threshold_type = models.CharField(
        max_length=50,
        choices=ThresholdCalculationMethodChoices,
        default=ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE.value,
        help_text="Method to set the threshold for the monitor (Static or Percentage change).",
    )
    threshold_metric_value = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="For choice and pass/fail evals, the specific metric value to monitor.",
    )
    critical_threshold_value = models.FloatField(
        validators=[MinValueValidator(0.0)], null=True, blank=True
    )  # will be null for anomaly detection , for percentage change it will be the percentage value, for static it will be the value
    warning_threshold_value = models.FloatField(
        validators=[MinValueValidator(0.0)], null=True, blank=True
    )  # will be null for anomaly detection , for percentage change it will be the percentage value, for static it will be the value

    # Alert frequency and timing
    alert_frequency = models.IntegerField(
        validators=[MinValueValidator(5)],
        default=60,
        help_text="Frequency of alert checks in minutes.",
    )
    auto_threshold_time_window = models.PositiveIntegerField(
        default=60 * 24 * 7,  # 1 week
        help_text="For auto-thresholding. The time window in minutes to calculate the historical mean",
    )
    last_checked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The last time the monitor was checked for alerts.",
    )

    # Notification settings
    notification_emails: ArrayField = ArrayField(models.EmailField(), default=list)
    slack_webhook_url = models.URLField(null=True, blank=True)
    slack_notes = models.TextField(null=True, blank=True)

    # Monitor state and configuration
    is_mute = models.BooleanField(default=False)
    filters = models.JSONField(default=dict, blank=True, null=True)
    logs: ArrayField = ArrayField(
        models.JSONField(default=dict), default=list, blank=True, null=True
    )

    def __str__(self):
        return self.name


class AlertTypeChoices(models.TextChoices):
    """Choices for different types of alerts."""

    CRITICAL = "critical", "Critical"
    WARNING = "warning", "Warning"


class UserAlertMonitorLog(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    alert = models.ForeignKey(UserAlertMonitor, on_delete=models.CASCADE)
    type = models.CharField(max_length=100, choices=AlertTypeChoices)
    message = models.TextField()
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True
    )
    link = models.URLField(null=True, blank=True)
    time_window_start = models.DateTimeField(null=True, blank=True)
    time_window_end = models.DateTimeField(null=True, blank=True)
