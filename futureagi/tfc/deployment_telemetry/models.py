from __future__ import annotations

import uuid

from django.db import models


class DeploymentTelemetryState(models.Model):
    class RegistrationKind(models.TextChoices):
        MINIMAL_DISABLED = "minimal_disabled", "Minimal disabled"
        FULL = "full", "Full"

    class RegistrationStatus(models.TextChoices):
        IDLE = "idle", "Idle"
        IN_PROGRESS = "in_progress", "In progress"

    singleton_key = models.IntegerField(default=1, unique=True)
    instance_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # Per-instance HMAC signing secret issued by the receiver on first
    # registration (TOFU). Persisted here for the instance's lifetime and
    # used to sign heartbeat bodies so the receiver can authenticate them.
    instance_secret = models.CharField(max_length=64, blank=True, default="")
    telemetry_disabled = models.BooleanField(default=False)
    registered_at = models.DateTimeField(null=True, blank=True)
    registration_kind = models.CharField(
        max_length=32,
        choices=RegistrationKind.choices,
        blank=True,
        default="",
    )
    registration_status = models.CharField(
        max_length=20,
        choices=RegistrationStatus.choices,
        default=RegistrationStatus.IDLE,
    )
    registration_claimed_at = models.DateTimeField(null=True, blank=True)
    # Local copy of the last registration payload that was sent. No production
    # code reads it — it exists as an operator diagnostic surface: a person
    # debugging "what did this instance phone home with last?" can inspect it
    # directly in the singleton row without rebuilding the payload from state.
    registration_metadata = models.JSONField(default=dict, blank=True)
    last_registration_attempt_at = models.DateTimeField(null=True, blank=True)
    # ``TextField`` because the value is a diagnostic exception/HTTP message
    # (often longer than 100 chars — ``HTTPSConnectionPool``, ``ReadTimeout``,
    # full Sentry-style messages). Truncating mid-stacktrace defeats the point.
    last_registration_error = models.TextField(blank=True, default="")
    last_heartbeat_attempt_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_window_start = models.DateTimeField(null=True, blank=True)
    last_heartbeat_window_end = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "deployment_telemetry_state"
        verbose_name = "deployment telemetry state"
        verbose_name_plural = "deployment telemetry state"

    def __str__(self) -> str:
        return str(self.instance_id)
