import uuid

from django.db import models


class HarnessSourceReview(models.Model):
    class Owner(models.TextChoices):
        ENVIRONMENT = "environment", "Existing environment"
        DESIGN = "design", "Design checkpoint"
        BUILD_JOB = "build_job", "Hosted build"

    class State(models.TextChoices):
        READING = "reading", "Reading"
        NEEDS_REPAIR = "needs_repair", "Needs repair"
        READY = "ready", "Ready"
        ACCEPTED = "accepted", "Accepted"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        "accounts.Workspace", null=True, on_delete=models.CASCADE
    )
    owner = models.ForeignKey("simulate.HostedHarnessJob", on_delete=models.CASCADE)
    owner_kind = models.CharField(max_length=16, choices=Owner.choices)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.READING
    )
    revision = models.PositiveIntegerField(default=1)
    basis = models.JSONField()
    expires_at = models.DateTimeField()
    staged_digest = models.CharField(max_length=64, blank=True)
    draft_digest = models.CharField(max_length=128, blank=True)
    preview = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=128, null=True)
    request_digest = models.CharField(max_length=64, blank=True)
    acceptance_key = models.CharField(max_length=128, blank=True)
    acceptance_digest = models.CharField(max_length=64, blank=True)
    accepted_artifact_digest = models.CharField(max_length=64, blank=True)
    snapshot_digest = models.CharField(max_length=71, blank=True)
    receipt = models.JSONField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "owner_kind", "idempotency_key"],
                name="harness_source_review_idempotency",
            ),
        ]
