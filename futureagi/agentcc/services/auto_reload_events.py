"""Trusted internal joins, NOT HTTP APIs or changes to the frozen checkout trigger.

J2 calls the outcome hook in the same account-locked transaction as its durable
outbox outcome. Only credited (verified success plus credit), definitively failed
or refused are terminal. Unknown payment outcomes remain pending_reconcile.
J7 calls the credit hook immediately after an append-only ledger credit in that
same account-locked transaction, with its actual immutable ID and before/after
balance. Do not call it from settings, a GET, an enqueue response or an estimate.
These functions do not verify payment/ledger provenance themselves; the trusted
writers own that evidence. Hook tests do not certify those main-owned joins.
"""

from agentcc.models.auto_reload_intent import (
    TERMINAL_STATES,
    AgentccAutoReloadCreditEvent,
    AgentccAutoReloadIntent,
)
from agentcc.services.auto_reload_arming import (
    get_locked_auto_reload_inflight,
    locked_arming,
    require_locked_account_transaction,
)


def record_locked_auto_reload_outcome(account, *, intent_id: str, outcome: str):
    """Record an authoritative outcome once; never reopen a terminal intent."""
    require_locked_account_transaction(account)
    if type(intent_id) is not str or type(outcome) is not str:
        raise ValueError("auto-reload outcome requires string identity and state")
    if outcome not in (*TERMINAL_STATES, "pending_reconcile"):
        raise ValueError("auto-reload requires a definitive or pending outcome")
    arming = locked_arming(account)
    intent = (
        AgentccAutoReloadIntent.objects.using("default")
        .select_for_update()
        .get(organization_id=account.organization_id, intent_id=intent_id)
    )
    if intent.state in TERMINAL_STATES:
        if intent.state != outcome:
            raise ValueError("auto-reload terminal outcome cannot be changed")
        return intent
    if intent.state == outcome:
        return intent
    intent.state = outcome
    intent.save(using="default", update_fields=["state"])
    if outcome in TERMINAL_STATES:
        # A credit crossing may already have advanced beyond this intent. Do not
        # count terminal delivery/replay as another epoch in that case.
        arming.epoch = max(arming.epoch, intent.epoch + 1)
        if arming.intent_id == intent.intent_id:
            arming.intent_id = ""
        arming.save(using="default", update_fields=["epoch", "intent_id"])
    return intent


def record_locked_auto_reload_credit(
    account, *, ledger_entry_id: str, before_micros: int, after_micros: int
):
    """Consume an actual append-only credit under the writer's account lock.

    Positive credits that do NOT cross are also receipted: replaying one after
    changing the threshold cannot turn it into a new crossing. Replayed IDs with
    different balances fail closed. A crossing advances arming but never removes
    the active intent: the database exclusion wins until its terminal outcome.
    An absent threshold is receipted as NULL and is never a crossing. Disabling
    auto-reload after claim cannot require renewed consent to credit a successful
    payment; this hook leaves the epoch, eligibility and active intent unchanged.
    """
    require_locked_account_transaction(account)
    threshold = account.autoreload_threshold_micros
    if (
        type(ledger_entry_id) is not str
        or not ledger_entry_id
        or ledger_entry_id != ledger_entry_id.strip()
        or len(ledger_entry_id) > 255
    ):
        raise ValueError("auto-reload credit requires an immutable ledger entry ID")
    if any(type(value) is not int for value in (before_micros, after_micros)) or (
        threshold is not None and type(threshold) is not int
    ):
        raise ValueError("auto-reload credit requires integer micros")
    if after_micros <= before_micros:
        raise ValueError("auto-reload event must be a positive ledger credit")
    arming = locked_arming(account)
    event, created = AgentccAutoReloadCreditEvent.objects.using(
        "default"
    ).get_or_create(
        organization_id=account.organization_id,
        ledger_entry_id=ledger_entry_id,
        defaults={
            "before_micros": before_micros,
            "after_micros": after_micros,
            "threshold_micros": threshold,
        },
    )
    if not created:
        if (event.before_micros, event.after_micros) != (before_micros, after_micros):
            raise ValueError("auto-reload ledger event replay differs")
        return arming
    if threshold is not None and before_micros <= threshold < after_micros:
        active = get_locked_auto_reload_inflight(account)
        arming.epoch = (
            max(arming.epoch, active.epoch + 1) if active else arming.epoch + 1
        )
        # Never clear a nonterminal intent, even after an unrelated credit.
        # Only the authoritative terminal hook may clear its latch.
        arming.below_threshold = False
        arming.save(using="default", update_fields=["epoch", "below_threshold"])
    return arming
