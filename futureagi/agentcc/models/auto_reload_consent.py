"""An authorization receipt for one enabled credit-settings save.

A dedicated gateway-owned table avoids changing the main-owned ledger model
and retains earlier acknowledgements instead of overwriting the last actor.
The accepted amount, threshold and saved payment method prevent an unrelated
settings writer from reusing a receipt for different charges or a new card. No receipts are backfilled: an old
boolean is not consent. Actor UUIDs survive user deletion for audit; losing the
live actor makes the charge guard fail closed until an admin consents again.
"""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization


class AgentccAutoReloadConsent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_auto_reload_consents",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agentcc_auto_reload_consents",
    )
    actor_identifier = models.UUIDField(editable=False)
    consent_version = models.CharField(max_length=64)
    accepted_at = models.DateTimeField()
    threshold_micros = models.BigIntegerField()
    amount_micros = models.BigIntegerField()
    # Empty legacy bindings are intentionally not charge authorization.
    payment_method_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "agentcc_auto_reload_consent"
        indexes = [
            models.Index(
                fields=["organization", "accepted_at"],
                name="agentcc_ar_consent_org_at",
            ),
        ]
