"""When each Hub checker last called the Hub (services/checker_presence.py)."""

from __future__ import annotations

from django.db import models


class CheckerPresence(models.Model):
    """One row per checker id (the `X-Omega-Hub-Checker-Id` header): the last time that
    checker made an authenticated call. Written at most every 30 seconds per checker."""

    checker_id = models.CharField(max_length=120, primary_key=True)
    last_seen_at = models.DateTimeField()

    class Meta:
        db_table = "omega_hub_checker_presence"

    def __str__(self) -> str:
        return f"checker {self.checker_id}"
