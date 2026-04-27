from django.db import models

from model_hub.models import AIModel


class AutoGenerateEventsRun(models.Model):
    ai_model = models.ForeignKey(
        AIModel, on_delete=models.CASCADE, related_name="auto_generate_runs"
    )
    last_run_at = models.DateTimeField(auto_now=True)
    total_events = models.IntegerField(default=0)

    class Meta:
        unique_together = ("ai_model",)
