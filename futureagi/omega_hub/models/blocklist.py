"""The public blocklist (SPEC §3.4). `note` is private and never leaves the maintainer API."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class BlockedDigest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    digest = models.CharField(max_length=80, null=True, blank=True)
    bundle_sha256 = models.CharField(max_length=64, null=True, blank=True)
    entry = models.ForeignKey(
        "omega_hub.Entry", null=True, blank=True, on_delete=models.SET_NULL, related_name="blocks"
    )
    version = models.ForeignKey(
        "omega_hub.Version", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    category = models.CharField(max_length=40)
    note = models.TextField(blank=True, default="")
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    added_at = models.DateTimeField(default=timezone.now)
    lifted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    lifted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "omega_hub_blocked_digest"
        ordering = ("-added_at",)
        constraints = [
            models.CheckConstraint(
                name="omega_hub_block_has_hash",
                condition=Q(digest__isnull=False) | Q(bundle_sha256__isnull=False),
            )
        ]
        indexes = [models.Index(fields=["lifted_at"], name="omega_hub_block_lifted_idx")]
