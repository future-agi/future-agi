"""Gateway intent exclusion and ledger-event receipts; not a payment outbox.

Checkout remains the sole queued payload/charge authority. Intent reservation shares
its transaction. Only the trusted outcome hook changes state; pending_reconcile
is NONTERMINAL. No settings writer touches these rows.
"""

from django.db import models

from accounts.models import Organization

TERMINAL_STATES = ("credited", "failed", "refused")
INTENT_STATES = ("queued", "pending_reconcile", *TERMINAL_STATES)


class AgentccAutoReloadIntent(models.Model):
    intent_id = models.CharField(max_length=64, primary_key=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    epoch = models.PositiveBigIntegerField()
    state = models.CharField(
        max_length=24,
        choices=[(value, value) for value in INTENT_STATES],
        default="queued",
    )

    class Meta:
        db_table = "agentcc_auto_reload_intent"
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=~models.Q(state__in=TERMINAL_STATES),
                name="agentcc_reload_one_nonterminal",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=INTENT_STATES),
                name="agentcc_reload_known_state",
            ),
        ]


class AgentccAutoReloadCreditEvent(models.Model):
    """Deduplicate trusted append-only credit callbacks, including noncrossings.

    Balance values are event evidence, not a wallet or debit estimate. The writer
    supplies the actual before/after values while holding the account lock.
    Threshold is captured at that credit, never recomputed on a later replay.
    NULL means no threshold was configured, so this event cannot re-arm.
    """

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    ledger_entry_id = models.CharField(max_length=255)
    before_micros = models.BigIntegerField()
    after_micros = models.BigIntegerField()
    threshold_micros = models.BigIntegerField(null=True)

    class Meta:
        db_table = "agentcc_auto_reload_credit_event"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "ledger_entry_id"],
                name="agentcc_reload_credit_once",
            ),
            models.CheckConstraint(
                condition=models.Q(after_micros__gt=models.F("before_micros")),
                name="agentcc_reload_positive_credit",
            ),
        ]
