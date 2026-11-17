"""Account-locked intent identity, independent of mutable settings.

Lock order: current default-DB ledger account FOR UPDATE, arming, intent. HTTP
observations update eligibility ONLY. They never advance epoch or clear a latch.
The database excludes a second nonterminal intent even if a caller bypasses the
lock. Trusted terminal/append-only credit hooks live in auto_reload_events;
they must still be integrated with the real writers (J2/J7). No money is written here.
"""

from django.db import transaction

from agentcc.models.auto_reload_arming import AgentccAutoReloadArming
from agentcc.models.auto_reload_intent import TERMINAL_STATES, AgentccAutoReloadIntent


def require_locked_account_transaction(account):
    """Check DB/transaction lifetime; actual row-lock ownership is a precondition."""
    if (
        not transaction.get_connection("default").in_atomic_block
        or account._state.db != "default"
    ):
        raise RuntimeError("auto-reload requires a locked account transaction")


def locked_arming(account):
    require_locked_account_transaction(account)
    states = AgentccAutoReloadArming.objects.using("default")
    states.get_or_create(organization_id=account.organization_id)
    return states.select_for_update().get(organization_id=account.organization_id)


def get_locked_auto_reload_inflight(account):
    """Read exclusion independently of current threshold, receipt or latch."""
    require_locked_account_transaction(account)
    return (
        AgentccAutoReloadIntent.objects.using("default")
        .select_for_update()
        .filter(organization_id=account.organization_id)
        .exclude(state__in=TERMINAL_STATES)
        .first()
    )


def observe_locked_auto_reload_balance(account, *, balance_micros: int):
    """Snapshot eligibility, not event authority. Settings cannot re-arm."""
    require_locked_account_transaction(account)
    threshold = account.autoreload_threshold_micros
    if type(balance_micros) is not int or type(threshold) is not int:
        raise ValueError("auto-reload requires integer balance and threshold micros")
    state = locked_arming(account)
    below = balance_micros <= threshold
    if state.below_threshold != below:
        state.below_threshold = below
        state.save(using="default", update_fields=["below_threshold"])
    return state


def get_locked_auto_reload_intent(account):
    """Reserve/reuse org:epoch after current consent and balance checks.

    Current consent must be validated even for duplicates. Caller invokes the frozen
    checkout in this SAME transaction with current receipt arguments; any refusal or
    malformed result must abort reservation and outbox together. The original
    outbox payload is immutable. IntegrityError is never swallowed: if a writer
    violates the lock protocol, the partial unique constraint aborts its insert.
    """
    state = locked_arming(account)
    active = get_locked_auto_reload_inflight(account)
    if active is not None:
        return active.intent_id
    if not state.below_threshold:
        raise ValueError("auto-reload intent requires a below-threshold observation")
    if state.intent_id:
        # Migration 0077 carries old nonempty latches forward conservatively.
        # Missing intent metadata must not silently manufacture a new attempt.
        raise RuntimeError("auto-reload intent state requires reconciliation")
    intent_id = f"{account.organization_id}:{state.epoch}"
    AgentccAutoReloadIntent.objects.using("default").create(
        organization_id=account.organization_id,
        intent_id=intent_id,
        epoch=state.epoch,
        state="queued",
    )
    state.intent_id = intent_id
    state.save(using="default", update_fields=["intent_id"])
    return intent_id
