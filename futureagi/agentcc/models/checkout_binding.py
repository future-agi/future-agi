"""Which org opened a managed-credit Checkout session (MUST-FIX G).

POST /agentcc/managed-credits/checkout/ records one row when cloud billing
returns a session, before the checkout URL is answered, so nobody can pay a
session FA could not bind. The landing page fulfils only a session bound to
the calling org, in the bound mode, for at least the bound amount
(agentcc.services.checkout_verification).

session_digest is an unkeyed sha256 of a Stripe Checkout Session id (a
high-entropy object id, not a credential), used only as a lookup key so the
raw id is never stored (tfc.logging.checkout_format redacts it from output
too). It is not a platform-key fingerprint, so the keyed-HMAC fingerprint rule
for keys (rating group I) does not apply, and no API ever returns it. Its name
puts the table under the hard-erase raw-SQL guard
(accounts.services.hard_erase_sql_policy); it is written through the ORM only.
"""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization


class AgentccCheckoutBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_checkout_bindings",
    )
    session_digest = models.CharField(max_length=64, unique=True)
    amount_micros = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="usd")
    livemode = models.BooleanField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agentcc_checkout_bindings",
    )
    # Authorization identity is write-once in record_binding and survives
    # SET_NULL on user deletion. Legacy/anonymous bindings remain unknown.
    actor_identifier = models.UUIDField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_checkout_binding"
        indexes = [
            models.Index(
                fields=["organization", "created_at"],
                name="agentcc_ckout_bind_org_at",
            ),
        ]
