"""Persistent, payload-free evidence that an explicit dispatch was claimed."""

from django.db import models

DISPATCH_CLAIM_KEY_MAX_LENGTH = 255


class AgentccDispatchClaim(models.Model):
    """Global replay barrier, not a lease or a request log.

    Retain claims for at least 30 days. There is deliberately no expiry or
    deletion path: removing a row would permit its key to dispatch again.
    Keys must be opaque idempotency identifiers, never secrets or payloads.
    """

    idempotency_key = models.CharField(
        max_length=DISPATCH_CLAIM_KEY_MAX_LENGTH, unique=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_dispatch_claim"
