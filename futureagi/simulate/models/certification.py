from __future__ import annotations

import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel, NoWorkspaceFilterManager

# The certification plan caps in-flight cases at four; stored per campaign so a
# later policy change does not rewrite history.
DEFAULT_MAX_ACTIVE_CASES = 4


class CertificationCampaign(BaseModel):
    """One post-deployment certification run against a verified deployment."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        COMPLETED_WITH_FAILURES = "completed_with_failures", "Completed with failures"
        CANCELING = "canceling", "Canceling"
        CANCELED = "canceled", "Canceled"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="certification_campaigns",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="certification_campaigns",
        null=True,
        blank=True,
    )

    # Deployment identity discovered by the zero-input deployment workflow and
    # echoed back on every campaign read: helm revision, deployment
    # generations, image digests, replica counts, and the ALK snapshot.
    deployment_id = models.CharField(max_length=255)
    deployment_identity = models.JSONField(default=dict, blank=True)

    suite_key = models.CharField(max_length=255)
    suite_digest = models.CharField(max_length=71)
    # Normalized suite snapshot recorded at creation so replays, audit, and
    # case materialization read exactly what admission approved.
    normalized_suite = models.JSONField(default=dict, blank=True)

    # Exact-once submission key: "certification:{deployment_id}:{suite_digest}".
    idempotency_key = models.CharField(max_length=512)

    state = models.CharField(
        max_length=32, choices=State.choices, default=State.PENDING
    )
    max_active_cases = models.PositiveSmallIntegerField(
        default=DEFAULT_MAX_ACTIVE_CASES
    )
    cases_total = models.PositiveIntegerField(default=0)
    cases_completed = models.PositiveIntegerField(default=0)
    cases_failed = models.PositiveIntegerField(default=0)

    requested_by = models.CharField(max_length=255, blank=True, default="")
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # Secret-safe terminal summary: counts, failure classes, and human/machine
    # report links. Never stores credential material or endpoint secrets.
    result = models.JSONField(default=dict, blank=True)
    failure_detail = models.JSONField(default=dict, blank=True)

    no_workspace_objects = NoWorkspaceFilterManager()

    class Meta:
        db_table = "simulate_certification_campaign"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "deployment_id", "suite_digest"],
                name="uniq_cert_campaign_org_deploy_suite",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="uniq_cert_campaign_org_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "state"]),
            models.Index(fields=["suite_key", "suite_digest"]),
        ]


class CertificationCase(BaseModel):
    """One manifest case inside a campaign — exactly one HostedHarnessJob."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        DISPATCHED = "dispatched", "Dispatched"
        RUNNING = "running", "Running"
        RECONCILING = "reconciling", "Reconciling"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    class FailureClass(models.TextChoices):
        CONFIGURATION = "configuration", "Configuration"
        SOURCE = "source", "Source"
        AUTHORING = "authoring", "Authoring"
        ENVIRONMENT = "environment", "Environment"
        TARGET = "target", "Target"
        TRANSPORT = "transport", "Transport"
        EVIDENCE = "evidence", "Evidence"
        CLEANUP = "cleanup", "Cleanup"
        STABLE_BEHAVIOR = "stable_behavior", "Stable behavior"
        EXPLORATORY_EVALUATION = "exploratory_evaluation", "Exploratory evaluation"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        CertificationCampaign,
        on_delete=models.CASCADE,
        related_name="cases",
    )
    case_key = models.CharField(max_length=255)
    sequence = models.PositiveIntegerField()

    # Exact-once job key:
    # "certification:{deployment_id}:{suite_digest}:{case_key}".
    idempotency_key = models.CharField(max_length=768)
    hosted_harness_job = models.ForeignKey(
        "simulate.HostedHarnessJob",
        on_delete=models.SET_NULL,
        related_name="certification_cases",
        null=True,
        blank=True,
    )

    # The expanded manifest case spec (agent, target, interactions, seeds,
    # authorization) used to build the HostedHarnessJob payload.
    spec = models.JSONField(default=dict, blank=True)

    state = models.CharField(
        max_length=32, choices=State.choices, default=State.PENDING
    )
    failure_class = models.CharField(
        max_length=32, choices=FailureClass.choices, null=True, blank=True
    )
    failure_detail = models.JSONField(default=dict, blank=True)

    # Per-item reconciliation checklist results recorded by the workflow;
    # job state alone is never treated as success.
    reconciliation = models.JSONField(default=dict, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    no_workspace_objects = NoWorkspaceFilterManager()

    class Meta:
        db_table = "simulate_certification_case"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "case_key"],
                name="uniq_cert_case_campaign_key",
            ),
        ]
        indexes = [
            models.Index(fields=["campaign", "state"]),
            models.Index(fields=["campaign", "sequence"]),
        ]


class CertificationNotification(BaseModel):
    """Outbox row for campaign notifications; delivery must not change results."""

    class EventKind(models.TextChoices):
        CAMPAIGN_STARTED = "campaign_started", "Campaign started"
        BLOCKING_FAILURE = "blocking_failure", "Blocking failure"
        STABLE_ASSERTION_FAILURE = (
            "stable_assertion_failure",
            "Stable assertion failure",
        )
        CAMPAIGN_COMPLETED = "campaign_completed", "Campaign completed"

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        CertificationCampaign,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    case = models.ForeignKey(
        CertificationCase,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    event_kind = models.CharField(max_length=48, choices=EventKind.choices)
    payload = models.JSONField(default=dict, blank=True)
    payload_digest = models.CharField(max_length=71)

    state = models.CharField(
        max_length=16, choices=State.choices, default=State.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    delivered_at = models.DateTimeField(null=True, blank=True)

    no_workspace_objects = NoWorkspaceFilterManager()

    class Meta:
        db_table = "simulate_certification_notification"
        constraints = [
            # Postgres treats NULL case_id as distinct, so dedupe needs two
            # partial constraints to cover case-scoped and campaign-scoped rows.
            models.UniqueConstraint(
                fields=["campaign", "case", "event_kind", "payload_digest"],
                condition=~models.Q(case__isnull=True),
                name="uniq_cert_notification_dedupe_case",
            ),
            models.UniqueConstraint(
                fields=["campaign", "event_kind", "payload_digest"],
                condition=models.Q(case__isnull=True),
                name="uniq_cert_notification_dedupe_campaign",
            ),
        ]
        indexes = [
            models.Index(fields=["state", "created_at"]),
        ]
