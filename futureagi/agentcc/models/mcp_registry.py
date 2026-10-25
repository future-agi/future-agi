from django.conf import settings
from django.db import models

from accounts.models import Organization
from tfc.middleware.workspace_context import (
    get_current_organization,
    set_workspace_context,
)


class AgentccMCPRegistryEntry(models.Model):
    """One ``server.json`` version in the MCP registry.

    A null organization is the FutureAGI mirror of the official registry,
    invisible to orgs until a FutureAGI maintainer approves it; an org's own
    entries are approved by the org's admin.
    """

    class Source(models.TextChoices):
        OFFICIAL = "official"
        ORG = "org"
        FUTUREAGI = "futureagi"

    class ReviewStatus(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_mcp_registry_entries",
        null=True,
        blank=True,
        db_index=False,  # covered by the unique constraint below
    )
    # Reverse-DNS, as in the official registry (io.github.owner/server).
    name = models.CharField(max_length=255)
    version = models.CharField(max_length=64)
    server_json = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=16, choices=Source.choices)
    review_status = models.CharField(
        max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.PENDING
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # {ok, era, tool_count, tools_hash, latency_ms, probed_at, error}
    probe = models.JSONField(null=True, blank=True)
    upstream_updated_at = models.DateTimeField(null=True, blank=True)
    deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agentcc_mcp_registry_entry"
        ordering = ["name", "version"]
        constraints = [
            # nulls_distinct=False so mirror entries (organization NULL) collide.
            models.UniqueConstraint(
                fields=["organization", "name", "version"],
                name="agentcc_mcp_registry_unique",
                nulls_distinct=False,
            ),
        ]

    def __str__(self):
        return f"{self.name}@{self.version} {self.source} ({self.review_status})"

    def save(self, *args, **kwargs):
        if self.organization_id is not None:
            super().save(*args, **kwargs)
            return
        # A mirror entry keeps its null organization: it is saved outside the
        # request's org context, so the global pre_save and post_save handlers in
        # tfc/utils/signals.py leave it null.
        previous = get_current_organization()
        set_workspace_context(organization=None)
        try:
            super().save(*args, **kwargs)
        finally:
            set_workspace_context(organization=previous)
