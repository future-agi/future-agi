import uuid

from django.db import models

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class CekuraIntegration(BaseModel):
    """Per-project Cekura webhook configuration.

    Follows the ``ObservabilityProvider`` shape (one row per project, a
    secret plus an enabled toggle) minus its polling fields: Cekura pushes
    completed runs to us, nothing here ever fetches from Cekura.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="cekura_integration",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="cekura_integrations",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cekura_integrations",
    )
    # Shared secret Cekura sends back in the X-Webhook-Secret header. Stored
    # in plain text to match the existing per-project provider rows
    # (ObservabilityProvider.metadata, ExternalEvalConfig.credentials); moving
    # all three behind field-level encryption is a separate change.
    signing_secret = models.CharField(max_length=255)
    enabled = models.BooleanField(default=True)

    class Meta:
        db_table = "tracer_cekura_integration"
        indexes = [
            models.Index(fields=["project", "enabled"]),
        ]

    def __str__(self):
        return f"Cekura integration for {self.project.name}"
