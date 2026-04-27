import uuid

from django.conf import settings as django_settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class IntegrationPlatform(models.TextChoices):
    LANGFUSE = "langfuse", "Langfuse"
    DATADOG = "datadog", "Datadog"
    POSTHOG = "posthog", "PostHog"
    PAGERDUTY = "pagerduty", "PagerDuty"
    MIXPANEL = "mixpanel", "Mixpanel"
    CLOUD_STORAGE = "cloud_storage", "Cloud Storage"
    MESSAGE_QUEUE = "message_queue", "Message Queue"
    LINEAR = "linear", "Linear"


class ConnectionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    ERROR = "error", "Error"
    SYNCING = "syncing", "Syncing"
    BACKFILLING = "backfilling", "Backfilling"


class IntegrationConnection(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Ownership
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="integration_connections",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="integration_connections",
    )
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_integrations",
    )

    # Platform configuration
    platform = models.CharField(
        max_length=50,
        choices=IntegrationPlatform.choices,
    )
    display_name = models.CharField(max_length=255)
    host_url = models.URLField(max_length=500)
    encrypted_credentials = models.BinaryField()
    ca_certificate = models.TextField(null=True, blank=True)

    # Project mapping
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="integration_connections",
    )
    external_project_name = models.CharField(max_length=255)

    # Sync state machine
    status = models.CharField(
        max_length=20,
        choices=ConnectionStatus.choices,
        default=ConnectionStatus.ACTIVE,
    )
    status_message = models.TextField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_cursor = models.JSONField(default=dict, blank=True)
    sync_interval_seconds = models.PositiveIntegerField(
        default=300,
        validators=[MinValueValidator(60), MaxValueValidator(1800)],
    )
    last_error_notified_at = models.DateTimeField(null=True, blank=True)

    # Backfill state
    backfill_from = models.DateTimeField(null=True, blank=True)
    backfill_completed = models.BooleanField(default=False)
    backfill_progress = models.JSONField(default=dict, blank=True)

    # Counters
    total_traces_synced = models.PositiveIntegerField(default=0)
    total_spans_synced = models.PositiveIntegerField(default=0)
    total_scores_synced = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "integrations_connection"
        indexes = [
            models.Index(
                fields=["organization", "platform", "status"],
                name="idx_intconn_org_plat_status",
            ),
            models.Index(
                fields=["status", "last_synced_at"],
                name="idx_intconn_status_lastsync",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "workspace",
                    "platform",
                    "external_project_name",
                ],
                condition=models.Q(deleted=False),
                name="uq_intconn_org_ws_plat_extproj",
            ),
        ]

    def __str__(self):
        return f"{self.get_platform_display()}: {self.display_name} ({self.status})"
