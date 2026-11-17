"""Operational backfill state; never archive contents or error messages."""

import uuid

from django.db import models


class BackfillState(models.TextChoices):
    NOT_STARTED = "not_started", "Not started"
    RUNNING = "running", "Running"
    COMPLETE = "complete", "Complete"
    FAILED = "failed", "Failed"
    OFF = "off", "Off"


class HarnessVersioningBackfillProgress(models.Model):
    """One organization-scoped claim and progress snapshot per installation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    state = models.CharField(
        max_length=16, choices=BackfillState.choices, default=BackfillState.NOT_STARTED
    )
    run_token = models.UUIDField(null=True, blank=True, editable=False)
    attempt = models.PositiveIntegerField(default=0)
    total = models.PositiveBigIntegerField(default=0)
    processed = models.PositiveBigIntegerField(default=0)
    succeeded = models.PositiveBigIntegerField(default=0)
    failed = models.PositiveBigIntegerField(default=0)
    skipped = models.PositiveBigIntegerField(default=0)
    outcomes = models.JSONField(default=dict)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class HarnessVersioningBackfillOutcome(models.Model):
    """The last attempt for one environment, separate from organization totals."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    environment = models.OneToOneField(
        "simulate.HostedHarnessJob", on_delete=models.CASCADE, related_name="+"
    )
    run_token = models.UUIDField(editable=False)
    attempt = models.PositiveIntegerField(default=1)
    state = models.CharField(
        max_length=16, choices=BackfillState.choices, default=BackfillState.NOT_STARTED
    )
    outcome_code = models.CharField(max_length=64, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
