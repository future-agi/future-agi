from django.db import models

from model_hub.models import AIModel


class Monitor(models.Model):
    class MonitorTypes(models.TextChoices):
        ANALYTICS = "Analytics", "Analytics"
        DATA_DRIFT = "DataDrift", "Data Drift"
        PERFORMANCE = "Performance", "Performance"

    status = models.BooleanField(
        default=False, help_text="Indicates if the alert is executed"
    )
    name = models.CharField(max_length=255, help_text="Name of the monitor")
    monitor_type = models.CharField(max_length=100, choices=MonitorTypes.choices)
    dimension = models.CharField(max_length=255, help_text="Dimension of the monitor")
    metric = models.CharField(max_length=255, help_text="Metric used by the monitor")
    current_value = models.FloatField(help_text="Current value of the metric")
    trigger_value = models.FloatField(help_text="Value at which the alert is triggered")
    is_mute = models.BooleanField(
        default=False, help_text="Indicates if the monitor is muted"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    ai_model = models.ForeignKey(
        AIModel, on_delete=models.CASCADE, related_name="monitors"
    )

    def __str__(self):
        return self.name
