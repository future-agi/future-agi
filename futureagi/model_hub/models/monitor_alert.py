from django.db import models

from model_hub.models.monitors import Monitor


class MonitorAlert(models.Model):
    triggered_value = models.FloatField(
        help_text="Value at which the alert is triggered"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    monitor = models.ForeignKey(
        Monitor, on_delete=models.CASCADE, related_name="monitor_alerts"
    )

    def __str__(self):
        return self.triggered_value
