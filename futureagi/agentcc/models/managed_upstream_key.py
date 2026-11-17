import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import Organization
from agentcc.services.credential_manager import ENCRYPTED_PREFIX

UNENCRYPTED_KEY_MESSAGE = (
    "encrypted_key must be written through CredentialManager "
    "(agentcc.services.credential_manager.encrypt_token)."
)


class AgentccManagedUpstreamKey(models.Model):
    """A per-org upstream API key for managed traffic.

    The key is stored encrypted (``agentcc.services.credential_manager``, the
    same ``CredentialManager`` provider credentials use), assembled into the
    gateway push only, and never returned by a read. ``key_hash`` is the
    upstream's own id for the key, not a secret.
    """

    class Status(models.TextChoices):
        ACTIVE = "active"
        DISABLED = "disabled"
        REVOKED = "revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_managed_upstream_keys",
    )
    provider_id = models.CharField(max_length=64)
    encrypted_key = models.TextField()
    key_hash = models.CharField(max_length=128)
    label = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    limit_usd = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_managed_upstream_key"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "provider_id"],
                condition=models.Q(status="active"),
                name="agentcc_one_active_upstream_key",
            ),
        ]

    def __str__(self):
        return f"{self.provider_id} {self.key_hash} ({self.status})"

    def clean(self):
        super().clean()
        self._refuse_unencrypted_key()

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is None or "encrypted_key" in update_fields:
            self._refuse_unencrypted_key()
        super().save(*args, **kwargs)

    def _refuse_unencrypted_key(self):
        # The message never carries the value: it may be a clear-text key.
        value = self.encrypted_key
        if (
            not isinstance(value, str)
            or not value.startswith(ENCRYPTED_PREFIX)
            or len(value) == len(ENCRYPTED_PREFIX)
        ):
            raise ValidationError({"encrypted_key": UNENCRYPTED_KEY_MESSAGE})

    def __repr__(self):
        return (
            f"<AgentccManagedUpstreamKey {self.pk} org={self.organization_id} {self}>"
        )
