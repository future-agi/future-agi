import uuid

from django.db import models

from accounts.models import Organization


class EvalSummaryTemplate(models.Model):
    """Reusable summary template for eval output formatting."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    criteria = models.TextField(
        help_text="The summary instructions to inject into the eval prompt",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="eval_summary_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "model_hub"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name
