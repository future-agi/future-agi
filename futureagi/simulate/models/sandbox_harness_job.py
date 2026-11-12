"""Tenant ownership for the installation-level sandbox transport only."""

from django.db import models


class SandboxHarnessJob(models.Model):
    """Bind the provider's opaque ID without manufacturing a hosted execution.

    A dedicated table keeps sandbox identifiers disjoint from hosted job UUIDs.
    Do not inherit BaseModel: NULL workspace is an exact submission scope, not
    permission to fill an ambient/default workspace during save.
    """

    job_id = models.TextField(primary_key=True)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="sandbox_harness_jobs",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="sandbox_harness_jobs",
        null=True,
        blank=True,
    )
    deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "simulate_sandbox_harness_job"
        indexes = [
            models.Index(
                fields=["organization", "workspace", "deleted"],
                name="sandbox_job_tenant_idx",
            ),
        ]
