"""Submissions and their check jobs (SPEC §3.1, §4.1, §8). A submission is never public."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from tfc.utils.base_model import BaseModel


class Submission(BaseModel):
    class Channel(models.TextChoices):
        CLI = "cli", "CLI"
        WEB = "web", "Web upload"
        GITHUB = "github", "GitHub (Official)"

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        CHECKING = "checking", "Checking"
        CHECKS_FAILED = "checks_failed", "Checks failed"
        CHECK_ERROR = "check_error", "Check error"
        IN_REVIEW = "in_review", "In review"
        APPROVED = "approved", "Approved"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        SUPERSEDED = "superseded", "Superseded"

    class Decision(models.TextChoices):
        APPROVE = "approve", "Approve"
        REJECT = "reject", "Reject"
        REQUEST_CHANGES = "request_changes", "Request changes"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publisher = models.ForeignKey(
        "omega_hub.Publisher", on_delete=models.PROTECT, related_name="submissions"
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="omega_hub_submissions"
    )
    channel = models.CharField(max_length=8, choices=Channel.choices)
    github_commit = models.CharField(max_length=40, null=True, blank=True)
    raw_key = models.CharField(max_length=200, blank=True, default="")
    raw_sha256 = models.CharField(max_length=64)
    raw_bytes = models.PositiveIntegerField()
    hint_name = models.CharField(max_length=40, null=True, blank=True)
    hint_version = models.CharField(max_length=32, null=True, blank=True)
    name = models.CharField(max_length=40, null=True, blank=True)
    version = models.CharField(max_length=32, null=True, blank=True)
    manifest = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.RECEIVED)
    status_reason = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    previous_version = models.ForeignKey(
        "omega_hub.Version", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    review_started_at = models.DateTimeField(null=True, blank=True)
    decision = models.CharField(max_length=24, choices=Decision.choices, null=True, blank=True)
    decision_reason = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    checklist = models.JSONField(null=True, blank=True)
    acknowledged_flags = models.JSONField(null=True, blank=True)
    confirmed_disclosure = models.JSONField(null=True, blank=True)
    change_notes = models.JSONField(null=True, blank=True)
    rules_version = models.PositiveIntegerField(null=True, blank=True)
    superseded_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "omega_hub_submission"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "created_at"], name="omega_hub_sub_status_idx"),
            models.Index(fields=["publisher", "created_at"], name="omega_hub_sub_pub_idx"),
        ]
        permissions = [("hub_maintainer", "Can review and moderate the Omega Hub")]

    def __str__(self):
        return f"submission {self.id} ({self.status})"


class CheckJob(BaseModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        ERRORED = "errored", "Errored"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(
        "omega_hub.Submission", on_delete=models.CASCADE, related_name="check_jobs"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    attempt = models.PositiveSmallIntegerField(default=0)
    leased_by = models.CharField(max_length=120, blank=True, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    # Updated on every lease claim, including retries, for the operations stale-checker alert.
    claimed_at = models.DateTimeField(null=True, blank=True)
    # When the job last entered QUEUED: at creation and again on every requeue (an expired lease, a
    # failed attempt). The stall alert ages the queue from this, so a job the reaper has just requeued
    # is not counted as unclaimed since its submission arrived (created_at survives a requeue).
    queued_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    report = models.JSONField(null=True, blank=True)
    log_key = models.CharField(max_length=200, blank=True, default="")
    cast_key = models.CharField(max_length=200, blank=True, default="")
    canonical_bundle_key = models.CharField(max_length=200, blank=True, default="")
    checker_version = models.CharField(max_length=40, blank=True, default="")
    omega_commit = models.CharField(max_length=40, blank=True, default="")
    runner_image = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        db_table = "omega_hub_check_job"
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=["status", "created_at"], name="omega_hub_job_status_idx"),
            models.Index(fields=["status", "queued_at"], name="omega_hub_job_queued_idx"),
        ]
