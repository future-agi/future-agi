import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class AgentccSession(BaseModel):
    """Explicit session for grouping related requests."""

    ACTIVE = "active"
    CLOSED = "closed"

    STATUS_CHOICES = [
        (ACTIVE, "Active"),
        (CLOSED, "Closed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_sessions",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="agentcc_sessions",
        null=True,
        blank=True,
    )
    session_id = models.CharField(max_length=255)
    name = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=ACTIVE,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "agentcc_session"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "session_id"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_session_id",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["session_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Session {self.session_id} ({self.status})"
