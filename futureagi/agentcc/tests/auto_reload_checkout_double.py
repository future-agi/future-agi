"""Checkout-trigger adapter double only; no claim, payment, credit or protected imports.

The synthetic SQL table survives the request transaction and rolls back with it.
This exercises the gateway transaction contract, NOT the real outbox adapter.
This boundary must be replaced and the real durable outbox/consumer qualified.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from django.db import connection

from agentcc.models.auto_reload_consent import AgentccAutoReloadConsent


@dataclass(frozen=True)
class CheckoutTriggerResult:
    status: str
    reason: str = ""


class CheckoutDouble:
    def __init__(self, ledger, owned_methods, events):
        self.ledger = ledger
        self.owned_methods = owned_methods
        self.events = events
        self.calls = []
        self.after_insert = None

    def request_auto_reload_checkout(
        self,
        *,
        org_id,
        consent_receipt_id,
        intent_id,
        amount_micros,
        payment_method_id,
        consent_revision,
    ):
        assert connection.in_atomic_block, "outbox must share caller transaction"
        assert tuple(
            type(value)
            for value in (
                org_id,
                consent_receipt_id,
                intent_id,
                amount_micros,
                payment_method_id,
                consent_revision,
            )
        ) == (UUID, UUID, str, int, str, int), "checkout: exact primitive types"
        self.events.append("checkout")
        values = {
            "org_id": org_id,
            "consent_receipt_id": consent_receipt_id,
            "intent_id": intent_id,
            "amount_micros": amount_micros,
            "payment_method_id": payment_method_id,
            "consent_revision": consent_revision,
        }
        self.calls.append(values)
        account = self.ledger.ManagedCreditAccount.objects.filter(
            organization_id=org_id
        ).first()
        if account is None:
            return CheckoutTriggerResult("refused", "consent_missing")
        if not account.autoreload_enabled:
            return CheckoutTriggerResult("refused", "disabled")
        receipt = AgentccAutoReloadConsent.objects.filter(
            pk=consent_receipt_id, organization_id=org_id
        ).first()
        if receipt is None:
            return CheckoutTriggerResult("refused", "consent_missing")
        if (
            consent_revision != 1
            or receipt.consent_version != "auto-reload-v1"
            or account.autoreload_consent_version != receipt.consent_version
            or account.autoreload_consent_at != receipt.accepted_at
            or receipt.actor_id is None
            or receipt.actor_id != receipt.actor_identifier
            or account.autoreload_threshold_micros != receipt.threshold_micros
        ):
            return CheckoutTriggerResult("refused", "consent_stale")
        if (
            self.owned_methods.get(payment_method_id) != org_id
            or payment_method_id != account.autoreload_payment_method_id
            or payment_method_id != receipt.payment_method_id
        ):
            return CheckoutTriggerResult("refused", "payment_method_mismatch")
        if amount_micros > receipt.amount_micros:
            return CheckoutTriggerResult("refused", "amount_above_consent")
        if amount_micros != account.autoreload_amount_micros:
            return CheckoutTriggerResult("refused", "consent_stale")
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO test_gateway_checkout_outbox (intent_id, payload) "
                "VALUES (%s, %s) ON CONFLICT (intent_id) DO NOTHING",
                [intent_id, json.dumps(values, default=str)],
            )
            inserted = cursor.rowcount == 1
        if inserted and self.after_insert is not None:
            self.after_insert()
        return CheckoutTriggerResult("queued" if inserted else "duplicate")

    def rows(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM test_gateway_checkout_outbox")
            return [json.loads(row[0]) for row in cursor.fetchall()]
