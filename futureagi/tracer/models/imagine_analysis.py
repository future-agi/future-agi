import uuid

from django.db import models

from accounts.models.user import User
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel
from tracer.models.project import Project
from tracer.models.saved_view import SavedView


class ImagineAnalysis(BaseModel):
    """Stores dynamic analysis results per trace + widget.

    Each record represents one LLM analysis for a specific widget
    in a saved Imagine view, run against a specific trace.
    """

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    saved_view = models.ForeignKey(
        SavedView,
        on_delete=models.CASCADE,
        related_name="imagine_analyses",
    )
    widget_id = models.CharField(max_length=100)
    trace_id = models.CharField(max_length=255)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="imagine_analyses",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="imagine_analyses",
    )

    prompt = models.TextField()
    content = models.TextField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    error = models.TextField(null=True, blank=True)
    workflow_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "tracer_imagine_analysis"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["saved_view", "widget_id", "trace_id"],
                condition=models.Q(deleted=False),
                name="unique_imagine_analysis_per_widget_trace",
            ),
        ]
        indexes = [
            models.Index(fields=["saved_view", "trace_id"]),
            models.Index(fields=["trace_id", "status"]),
        ]

    def __str__(self):
        return f"Analysis {self.widget_id} for trace {self.trace_id[:8]}"
