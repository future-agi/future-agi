"""Durable demo work; rows survive dispatch outages and worker retries."""

from django.conf import settings
from django.db import models


class DemoSeedRequest(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    organization = models.OneToOneField(
        "accounts.Organization", on_delete=models.CASCADE, primary_key=True
    )
    traces_seeded = models.BooleanField(default=False)
    datasets_seeded = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)


class DemoTracePublication(models.Model):
    trace = models.OneToOneField(
        "tracer.Trace", on_delete=models.CASCADE, primary_key=True
    )
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
