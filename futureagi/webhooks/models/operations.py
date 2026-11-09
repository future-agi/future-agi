"""Operations tables (WH-1b, migration 0002_operations; DESIGN.md sections 13.1-13.3).

Install-wide, never tenant data: no row names an organization's endpoint, URL, secret or body.

- ``webhooks_dispatcher_beat``: one row per dispatcher loop, written as it heartbeats, so the health pass can tell a
  loop that is down (WA2) and report the busy slots per loop and lane.
- ``webhooks_ops_alert``: the state of each ops alert of section 13.3 (condition held since, fired, cleared), so an
  alert with a duration ("for 5 min") survives from one health pass to the next.
- ``webhooks_ops_snapshot``: the last gauges the health pass computed, served to staff at ``/webhooks/ops/stats/``.
"""

from __future__ import annotations

from django.db import models


class DispatcherBeat(models.Model):
    loop = models.CharField(max_length=40, primary_key=True)
    """``webhooks-dispatcher-<n>``."""
    beat_at = models.DateTimeField()
    busy_normal = models.IntegerField(default=0)
    busy_slow = models.IntegerField(default=0)
    worker = models.CharField(max_length=200, blank=True, default="")
    """The worker host and pid that ran the loop last."""

    class Meta:
        db_table = "webhooks_dispatcher_beat"


class OpsAlert(models.Model):
    id = models.CharField(max_length=8, primary_key=True)
    """``WA1`` ... ``WA9``."""
    active = models.BooleanField(default=False)
    since = models.DateTimeField(null=True)
    fired_at = models.DateTimeField(null=True)
    cleared_at = models.DateTimeField(null=True)
    detail = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "webhooks_ops_alert"


class OpsSnapshot(models.Model):
    id = models.CharField(max_length=20, primary_key=True)
    computed_at = models.DateTimeField()
    data = models.JSONField(default=dict)

    class Meta:
        db_table = "webhooks_ops_snapshot"
