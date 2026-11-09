"""The Hub's own append-only audit log (SPEC §8: why not accounts.AuditLog)."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditEventImmutable(Exception):
    pass


class AuditEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AuditEventImmutable("AuditEvent rows are append-only")

    def delete(self):
        raise AuditEventImmutable("AuditEvent rows are append-only")


class AuditEvent(models.Model):
    class ActorKind(models.TextChoices):
        USER = "user", "User"
        CHECKER = "checker", "Checker"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    at = models.DateTimeField(default=timezone.now)
    # SET_NULL: the platform hard-deletes users (accounts workspace_management). The collector's
    # SET_NULL is a raw UpdateQuery, so it does not go through the refusing QuerySet.update().
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # The actor's pseudonym (pseudonym.py), written with the row, so the row still names the same
    # account after the account is deleted and `actor` becomes NULL (HUB-09).
    actor_label = models.CharField(max_length=40, blank=True, default="")
    actor_kind = models.CharField(max_length=8, choices=ActorKind.choices)
    action = models.CharField(max_length=40)
    target_type = models.CharField(max_length=24)
    target_id = models.CharField(max_length=80)
    publisher = models.ForeignKey(
        "omega_hub.Publisher", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    data = models.JSONField(default=dict)

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        db_table = "omega_hub_audit_event"
        ordering = ("-at",)
        indexes = [
            models.Index(fields=["target_type", "target_id", "at"], name="omega_hub_audit_target_idx"),
            models.Index(fields=["action", "at"], name="omega_hub_audit_action_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditEventImmutable("AuditEvent rows are append-only")
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditEventImmutable("AuditEvent rows are append-only")
