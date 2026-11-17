"""Moves keep their old references forever (SPEC §3.4)."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class EntryRedirect(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    from_publisher_handle = models.CharField(max_length=40)
    from_name = models.CharField(max_length=40)
    entry = models.ForeignKey("omega_hub.Entry", on_delete=models.CASCADE, related_name="redirects")
    moved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    moved_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "omega_hub_entry_redirect"
        constraints = [
            models.UniqueConstraint(
                fields=["from_publisher_handle", "from_name"], name="omega_hub_entry_redirect_unique"
            )
        ]


class PublisherRedirect(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    from_handle = models.CharField(max_length=40, unique=True)
    publisher = models.ForeignKey(
        "omega_hub.Publisher", on_delete=models.CASCADE, related_name="redirects"
    )
    moved_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "omega_hub_publisher_redirect"
