import uuid

from django.db import models


class InsightStatus(models.Model):
    class StatusTypes(models.TextChoices):
        STEP_1 = "Step1", "Step 1"
        STEP_2 = "Step2", "Step 2"
        STEP_3 = "Step3", "Step 3"
        COMPLETED = "Completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now_add=True)
    insight = models.ForeignKey(
        "Insight", on_delete=models.CASCADE, related_name="insight_status"
    )
    status = models.CharField(max_length=100, choices=StatusTypes.choices)
    message = models.TextField()

    def __str__(self):
        return str(self.id)
