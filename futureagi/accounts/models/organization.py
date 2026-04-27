import uuid

from django.db import models


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255, blank=True)
    is_new = models.BooleanField(default=True)
    # Backfilled to True for all existing orgs in 0015_backfill_rbac_data.py
    ws_enabled = models.BooleanField(default=True)
    region = models.CharField(max_length=16, default="us")

    # 2FA enforcement
    require_2fa = models.BooleanField(default=False)
    require_2fa_grace_period_days = models.PositiveSmallIntegerField(default=7)
    require_2fa_enforced_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # If display_name is not provided, default to the value of name
        if not self.display_name:
            self.display_name = self.name
        super().save(*args, **kwargs)
