"""Explicit trusted-hook qualification only; NOT main writer integration proof."""

from uuid import uuid4

from django.db import transaction


def complete(ports, outcome, *, intent_id=None):
    from agentcc.services.auto_reload_events import record_locked_auto_reload_outcome

    if intent_id is None:
        intent_id = ports.checkout.rows()[-1]["intent_id"]
    with transaction.atomic():
        account = (
            type(ports.account).objects.select_for_update().get(pk=ports.account.pk)
        )
        return record_locked_auto_reload_outcome(
            account, intent_id=intent_id, outcome=outcome
        )


def credit(ports, ledger, organization, target, *, entry_id=None):
    """Real synthetic ledger credit plus explicit gateway hook in one transaction.

    The immutable synthetic ledger idempotency key identifies this append-only
    test credit. A real ledger writer must bind its own authoritative ledger entry identity.
    """
    from agentcc.services.auto_reload_events import record_locked_auto_reload_credit

    entry_id = entry_id or f"reload-credit-test:{uuid4()}"
    with transaction.atomic():
        account = (
            type(ports.account).objects.select_for_update().get(pk=ports.account.pk)
        )
        before = ledger.ledger_balance(organization).balance_micros
        assert target > before
        ledger.adjust(
            organization,
            target - before,
            idempotency_key=entry_id,
            actor=None,
            reason="synthetic append-only credit",
        )
        after = ledger.ledger_balance(organization).balance_micros
        assert after == target
        state = record_locked_auto_reload_credit(
            account,
            ledger_entry_id=entry_id,
            before_micros=before,
            after_micros=after,
        )
    return entry_id, before, after, state
