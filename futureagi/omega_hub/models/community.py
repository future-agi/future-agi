"""Community counters and maintainer signals (SPEC §6.1-§6.6). There is no conversation model here."""

import uuid

from django.conf import settings
from django.db import models

from tfc.utils.base_model import BaseModel


class Like(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="omega_hub_likes"
    )
    entry = models.ForeignKey("omega_hub.Entry", on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "omega_hub_like"
        constraints = [
            models.UniqueConstraint(fields=["user", "entry"], name="omega_hub_like_once")
        ]
        indexes = [models.Index(fields=["entry", "created_at"], name="omega_hub_like_entry_idx")]


class DownloadDaily(models.Model):
    class Source(models.TextChoices):
        CLI = "cli", "Omega CLI"
        STUDIO = "studio", "Studio"
        DEPLOY = "deploy", "Deploy"
        CONSOLE = "console", "Omega Console"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(
        "omega_hub.Version", on_delete=models.CASCADE, related_name="downloads"
    )
    day = models.DateField()
    source = models.CharField(max_length=8, choices=Source.choices)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "omega_hub_download_daily"
        constraints = [
            models.UniqueConstraint(
                fields=["version", "day", "source"], name="omega_hub_download_daily_unique"
            )
        ]


class Pick(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey("omega_hub.Entry", on_delete=models.CASCADE, related_name="picks")
    week = models.DateField()
    why = models.CharField(max_length=140)
    picked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "omega_hub_pick"
        ordering = ("-week", "created_at")
        constraints = [
            models.UniqueConstraint(fields=["entry", "week"], name="omega_hub_pick_unique")
        ]


class Report(BaseModel):
    class Reason(models.TextChoices):
        SECURITY = "security", "Security"
        LICENCE = "licence", "Licence"
        IMPERSONATION = "impersonation", "Impersonation"
        BROKEN = "broken", "Broken"
        SPAM = "spam", "Spam"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACTIONED = "actioned", "Actioned"
        DISMISSED = "dismissed", "Dismissed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey("omega_hub.Entry", on_delete=models.CASCADE, related_name="reports")
    version = models.ForeignKey(
        "omega_hub.Version", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # SET_NULL, not CASCADE: deleting a reporter must not delete their report against an entry
    # (HUB-09). `reporter_label` is the reporter's pseudonym, stored when the report is filed.
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="omega_hub_reports"
    )
    reporter_label = models.CharField(max_length=40, blank=True, default="")
    reason = models.CharField(max_length=16, choices=Reason.choices)
    details = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    resolution = models.CharField(max_length=24, blank=True, default="")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "omega_hub_report"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["status", "created_at"], name="omega_hub_report_status_idx")]
