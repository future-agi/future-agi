import uuid

from django.db import models  # type: ignore[import-not-found]

from tfc.utils.base_model import BaseModel


class CallLogEntry(BaseModel):
    class LogSource(models.TextChoices):
        AGENT = "agent", "Agent"
        CUSTOMER = "customer", "Customer"

    class Provider(models.TextChoices):
        VAPI = "vapi", "Vapi"
        RETELL = "retell", "Retell"

    """
    Stores a single structured log record for a call execution.
    The entire payload from Vapi is persisted in `payload` for traceability,
    while key fields are duplicated for efficient querying.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    call_execution = models.ForeignKey(
        "simulate.CallExecution",
        on_delete=models.CASCADE,
        related_name="log_entries",
        help_text="Call execution the log entry belongs to",
    )
    source = models.CharField(
        max_length=16,
        choices=LogSource.choices,
        default=LogSource.AGENT,
        help_text="Source of the log entry (agent or customer)",
    )
    provider = models.CharField(
        max_length=16,
        choices=Provider.choices,
        default=Provider.VAPI,
        help_text="Provider of the log entry (vapi or retell)",
    )
    logged_at = models.DateTimeField(
        help_text="Timestamp of the log entry (UTC)", db_index=True
    )
    level = models.IntegerField(
        help_text="Numeric severity level of the log entry (e.g., 30=INFO)"
    )
    severity_text = models.CharField(
        max_length=32,
        blank=True,
        help_text="Human-readable severity text (e.g., INFO, WARN)",
    )
    category = models.CharField(
        max_length=128, blank=True, help_text="Category of the log entry"
    )
    body = models.CharField(
        max_length=1024,
        blank=True,
        help_text="Short message body for the log entry",
    )
    attributes = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured attributes parsed from the log entry",
    )
    payload = models.JSONField(
        help_text="Full log entry payload as returned by Vapi for auditing"
    )

    class Meta:
        db_table = "simulate_call_log_entry"
        verbose_name = "Call Log Entry"
        verbose_name_plural = "Call Log Entries"
        ordering = ["-logged_at"]
        indexes = [
            models.Index(
                fields=["call_execution", "logged_at"],
                name="idx_calllog_exec_time",
            ),
            models.Index(
                fields=["call_execution", "source", "logged_at"],
                name="idx_calllog_exec_src",
            ),
            models.Index(fields=["category"], name="idx_calllogentry_category"),
            models.Index(fields=["body"], name="idx_calllogentry_body"),
        ]

    def __str__(self) -> str:
        return (
            f"{self.call_execution_id} [{self.source}] @ "
            f"{self.logged_at.isoformat()} - {self.body}"
        )
