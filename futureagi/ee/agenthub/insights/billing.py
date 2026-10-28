"""Billing for Insights, the way Falcon AI is billed: what the model calls
cost, times the platform markup, as AI credits.

A report is charged once, when it is written; a failed report is not. A
follow-up question, a drafted setup and a preview are charged when they
answer. Before any of them runs, the organization's credit cap is checked
against an estimate. Emits are no-ops where billing is off (OSS, self-hosted).
"""

from __future__ import annotations

import uuid

import structlog

from ee.agenthub.insights.llm import FAST, THINKING, call_cost, model_for

logger = structlog.get_logger(__name__)

EVENT = "insights_report"
# The billing consumer stores event ids as UUIDs; uuid5 keeps a report's id
# the same on a retried finish, so it is charged once.
_EVENT_NAMESPACE = uuid.UUID("5d0f3c9e-7f3a-4c1e-9b6a-2e8d4a1f6c70")

# Tokens each step sends and gets back, for estimates (the charge is always
# the actual cost). Upper-end figures for the built-in engine: a session or a
# user is read whole, up to 9,000 characters, and classified 20 to a call.
READ_TOKENS = (3_000, 450)
CLASSIFY_TOKENS = (3_000, 800)
CLASSIFY_BATCH = 20
REPORT_TOKENS = (30_000, 6_000)
ASK_TOKENS = (12_000, 1_600)
DRAFT_TOKENS = (4_000, 900)
PREVIEW_UNITS = 3

# The insights-agent/v2 reader reads a unit's whole span graph with tools over
# a few turns, each sending again what was read so far, inside its budgets
# (characters read plus the graph's structure shown). So a unit costs about
# its rendered size, up to those budgets, not a fixed 9,000 characters; one
# small enough is read whole in a single call. A typical unit's rendered
# size: measured on the project's last report when there is one, else these.
V2_UNIT_CHARS = {"traces": 24_000, "sessions": 60_000, "users": 120_000}
V2_INLINE_CHARS = 24_000
V2_BUDGET_CHARS = 120_000 + 60_000
V2_TURNS = 4
V2_TURN_TOKENS = (4_000, 1_000)
CHARS_PER_TOKEN = 4


def read_tokens(
    contract: str | None = None, unit: str | None = None, unit_chars=None
) -> tuple[int, int]:
    """Tokens reading one unit takes on ``contract``: a fixed upper-end figure
    for the built-in engine and v1, the unit's size for v2."""
    from ee.agenthub.insights.agent import CONTRACT_V2

    if contract != CONTRACT_V2:
        return READ_TOKENS
    chars = int(unit_chars or V2_UNIT_CHARS.get(unit or "", V2_UNIT_CHARS["sessions"]))
    if chars <= V2_INLINE_CHARS:
        return V2_TURN_TOKENS[0] + chars // CHARS_PER_TOKEN, V2_TURN_TOKENS[1]
    read = min(chars, V2_BUDGET_CHARS) // CHARS_PER_TOKEN
    # Each turn sends again what was read before it: on average half of it.
    return (
        V2_TURNS * V2_TURN_TOKENS[0] + read * (V2_TURNS + 1) // 2,
        V2_TURNS * V2_TURN_TOKENS[1],
    )


def _price(tier: str, tokens: tuple[int, int]) -> float:
    return call_cost(model_for(tier), 0.0, *tokens)


def _run_cost(sample_size: int, read: float, classify: float, write: float) -> float:
    batches = -(-max(0, sample_size) // CLASSIFY_BATCH)
    return sample_size * read + batches * classify + write


def _step_prices(**sizing) -> tuple[float, float, float]:
    return (
        _price(FAST, read_tokens(**sizing)),
        _price(FAST, CLASSIFY_TOKENS),
        _price(THINKING, REPORT_TOKENS),
    )


def run_cost(sample_size: int, **sizing) -> float:
    """About what a report over ``sample_size`` units costs, in USD.
    ``sizing`` is ``read_tokens``'s: the contract, unit and typical unit size."""
    return _run_cost(sample_size, *_step_prices(**sizing))


def largest_sample(
    credits_left: float, low: int, high: int, step: int = 10, **sizing
) -> int | None:
    """The largest sample from ``high`` down to ``low`` (in ``step``s) whose
    report costs no more than ``credits_left``, or None when none does."""
    prices = _step_prices(**sizing)
    for size in range(high, low - 1, -step):
        if credits(_run_cost(size, *prices)) <= credits_left + 1e-9:
            return size
    return None


def ask_cost() -> float:
    return _price(THINKING, ASK_TOKENS)


def draft_cost() -> float:
    return _price(THINKING, DRAFT_TOKENS)


def preview_cost(**sizing) -> float:
    """Preview reads its units the way the report would, on the same engine:
    ``sizing`` is ``read_tokens``'s, as for ``run_cost``."""
    return PREVIEW_UNITS * _price(FAST, read_tokens(**sizing))


def applies() -> bool:
    """Whether Insights is billed here: on Cloud. Elsewhere (OSS, a licensed
    self-host) nothing is estimated or checked in credits, as with Instant
    eval. A Cloud whose billing config doesn't load is still billed: its
    credit checks can't answer, so they refuse (CreditCheckUnavailable)."""
    try:
        from ee.usage.deployment import DeploymentMode
    except ImportError:
        return False
    return DeploymentMode.is_cloud()


def _to_credits(cost_usd: float) -> float:
    """AI credits for a model cost, by billing's conversion. Raises when the
    billing config doesn't load."""
    if not cost_usd or cost_usd <= 0:
        return 0.0
    from ee.usage.services.config import BillingConfig

    return round(BillingConfig.get().calculate_ai_credits(cost_usd), 4)


def credits(cost_usd: float) -> float:
    """AI credits for a model cost; 0 for nothing spent (billing's own
    conversion charges 1 credit for an unknown cost), or when the billing
    config doesn't load."""
    try:
        return _to_credits(cost_usd)
    except Exception:
        logger.warning("insight_credit_conversion_unavailable", exc_info=True)
        return 0.0


class CreditCheckUnavailable(Exception):
    """Billing couldn't be asked (its Redis or config is down). Reports run
    on Future AGI's own model keys, so a step is refused rather than run
    unchecked: an outage must not mean unlimited free reports."""


def check(org, cost_usd: float):
    """The organization's credit cap and pause budgets against a step
    costing about ``cost_usd``: None when it may run (or this deployment has
    no billing), else billing's CheckResult. Read-only. Raises
    CreditCheckUnavailable when billing can't answer, its config included."""
    if org is None:
        return None
    try:
        from ee.usage.services.budgets import with_pause_budgets
        from ee.usage.services.metering import check_usage
    except ImportError:
        # No billing module: this deployment doesn't bill.
        return None
    try:
        amount = _to_credits(cost_usd)
        if amount <= 0:
            return None
        result = with_pause_budgets(
            str(org.id), check_usage(str(org.id), EVENT, amount=amount), amount
        )
    except Exception as exc:
        logger.warning("insight_credit_check_unavailable", exc_info=True)
        raise CreditCheckUnavailable() from exc
    return None if result.allowed else result


def charge(
    org_id,
    cost_usd: float,
    *,
    source: str,
    source_id: str,
    spend=None,
    event_id: str | None = None,
    unpriced: bool = False,
) -> float:
    """Emit the usage event for model calls that cost ``cost_usd``. Returns
    the credits charged (0 when nothing was spent). Never raises: the user's
    report must not fail because metering did. ``unpriced`` marks a cost
    partly at the ceiling price of a model nothing priced."""
    amount = credits(cost_usd)
    if not org_id or amount <= 0:
        return 0.0
    try:
        from ee.usage.schemas.events import UsageEvent
        from ee.usage.services.emitter import emit

        properties = {
            "source": source,
            "source_id": str(source_id),
            "raw_cost_usd": str(cost_usd),
            "fast_model": model_for(FAST),
            "thinking_model": model_for(THINKING),
        }
        if spend is not None:
            properties.update(
                {
                    "model_calls": spend.calls,
                    "prompt_tokens": spend.prompt_tokens,
                    "completion_tokens": spend.completion_tokens,
                }
            )
        if unpriced or getattr(spend, "unpriced", False):
            properties["unpriced"] = True
        event = UsageEvent(
            org_id=str(org_id),
            event_type=EVENT,
            amount=amount,
            properties=properties,
            **({"event_id": event_id} if event_id else {}),
        )
        emit(event)
    except Exception:
        logger.exception("insight_usage_emit_failed", org_id=str(org_id), source=source)
        return 0.0
    return amount


def charge_run(run) -> float:
    """Charge a written report its model cost so far, once per run."""
    flags = (getattr(run, "settings", None) or {}).get("spend_flags") or {}
    return charge(
        run.organization_id,
        float(run.cost_usd or 0),
        source="insights_report",
        source_id=str(run.id),
        event_id=str(uuid.uuid5(_EVENT_NAMESPACE, f"run:{run.id}")),
        unpriced=bool(flags.get("unpriced")),
    )
