from django.db import models


class DatadogExportReceipt(models.Model):
    """Durable per-connection membership in an export snapshot.

    Keep receipts as long as their source logs exist: created_at is assigned
    before commit, so no timestamp cutoff can safely replace this dedup ledger.
    """

    id = models.BigAutoField(primary_key=True)
    connection = models.ForeignKey(
        "integrations.IntegrationConnection",
        on_delete=models.CASCADE,
        related_name="datadog_receipts",
    )
    request_log = models.ForeignKey(
        "prism.AgentccRequestLog",
        on_delete=models.CASCADE,
        related_name="datadog_receipts",
    )
    export_id = models.UUIDField(db_index=True)

    class Meta:
        db_table = "integrations_datadog_export_receipt"
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "request_log"],
                name="uq_datadog_conn_request",
            ),
        ]
