"""Hub tokens (SPEC §8, §9.1): one publisher, `publish` scope, stored as a SHA-256 hash only."""

import uuid

from django.conf import settings
from django.db import models

from tfc.utils.base_model import BaseModel


class HubToken(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="omega_hub_tokens"
    )
    publisher = models.ForeignKey(
        "omega_hub.Publisher", on_delete=models.CASCADE, related_name="tokens"
    )
    name = models.CharField(max_length=60)
    prefix = models.CharField(max_length=8)
    token_hash = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "omega_hub_token"
        ordering = ("-created_at",)

    # DRF's IsAuthenticated reads request.user; a token request's user is the token's owner.
    @property
    def is_authenticated(self) -> bool:  # pragma: no cover - DRF reads request.user, not this
        return True
