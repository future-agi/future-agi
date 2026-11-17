"""EE's purge receipts for deleted workspaces and organizations (READINESS 3.22, E02).

One row per lifecycle event, written when EE reports the purge done. Ids and counts only, no
names or content, so it is kept after the data it describes is gone. It has no foreign key on
purpose: an organization's deletion cascades to its ``audit_log`` rows, and the receipt of that
deletion must outlive it. A workspace deletion's receipt is also written to ``audit_log``.
"""

from django.db import models


class OmegaPurgeOutcome(models.TextChoices):
    # EE erased what it held for the scope and returned its receipt.
    PURGED = "purged", "Purged"
    # EE runs without just-in-time host identity, so it held nothing for the scope.
    IGNORED = "ignored", "Ignored"


class OmegaPurgeReceipt(models.Model):
    id = models.BigAutoField(primary_key=True)
    # The outbox row's event id: one receipt per deletion, however often EE was asked.
    event_id = models.UUIDField(unique=True)
    # workspace.deleted | org.deleted
    kind = models.CharField(max_length=32)
    organization_id = models.UUIDField(db_index=True)
    workspace_id = models.UUIDField(null=True, blank=True)
    outcome = models.CharField(max_length=16, choices=OmegaPurgeOutcome.choices)
    # When Future AGI deleted it, and when EE finished (null when ignored).
    requested_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    # EE's receipt as validated (omega-host-purge-receipt/v1), or {} when ignored.
    receipt = models.JSONField(default=dict)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_omega_purge_receipt"

    def __str__(self):
        return (
            f"{self.kind} {self.organization_id}/{self.workspace_id} ({self.outcome})"
        )
