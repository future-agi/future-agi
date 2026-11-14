"""Pause budgets against what a step is about to cost.

A pause budget blocks usage once it is crossed: the billing consumer counts
an event, sees the dimension's usage past the budget and sets the pause flag
that ``check_usage`` refuses on (budget enforcement, in the Cloud overlay's
ee/cloud/billing/budget_enforcement.py). That happens after the usage is
counted, so one step that starts under the budget runs whole, however far
past it that takes the usage. A caller that knows what a step will cost
also asks ``with_pause_budgets``, and a step that would take the usage past
an active pause budget is refused before it starts.

A precheck, not a reservation: steps started together can cross a budget
together. ``total_spend`` budgets (priced by the Cloud overlay's billing
engine) are left to budget enforcement.
"""

from __future__ import annotations

from ee.usage.schemas.events import CheckResult
from ee.usage.services.config import BillingConfig
from ee.usage.services.emitter import get_redis
from ee.usage.services.metering import _get_current_period

# A step that would take usage past an active pause budget. ``limit`` is the
# budget and ``current_usage`` the usage so far, in native units, so what is
# left reads as for FREE_TIER_LIMIT.
BUDGET_LIMIT = "BUDGET_LIMIT"
FREE_TIER_LIMIT = "FREE_TIER_LIMIT"


def _left(result: CheckResult) -> float:
    return max(0.0, float(result.limit or 0) - float(result.current_usage or 0))


def pause_budget_refusal(
    org_id: str, dimension: str, amount: float
) -> CheckResult | None:
    """BUDGET_LIMIT when ``amount`` (native units of ``dimension``) would take
    this period's usage past the lowest active pause budget on it, else None.
    Reads the budgets from the database and the usage from the counter
    ``check_usage`` reads. Raises when either can't be read."""
    if not dimension or not amount or amount <= 0:
        return None
    from ee.usage.models.usage import UsageBudget

    thresholds = list(
        UsageBudget.objects.filter(
            organization_id=org_id,
            scope=dimension,
            action="pause",
            is_active=True,
            deleted=False,
        ).values_list("threshold_value", flat=True)
    )
    if not thresholds:
        return None
    dim_config = BillingConfig.get().get_dimension(dimension)
    # Budgets are set in display units; the counter is in native ones.
    limit = float(min(thresholds)) * dim_config.native_to_display_divisor
    usage_key = f"usage:{org_id}:{dimension}:{_get_current_period()}"
    current = float(get_redis().get(usage_key) or 0)
    # Budget enforcement pauses once usage is past the budget, not at it.
    if current + amount <= limit + 1e-9:
        return None
    return CheckResult(
        allowed=False,
        error_code=BUDGET_LIMIT,
        reason=(
            f"Your budget limit for {dim_config.display_name} doesn't cover "
            "this. Raise or remove it to continue."
        ),
        dimension=dimension,
        current_usage=current,
        limit=limit,
    )


def with_pause_budgets(org_id: str, result: CheckResult, amount: float):
    """``check_usage``'s ``result`` for ``amount``, with the organization's
    pause budgets applied: a step it allows that would cross a pause budget
    is refused with BUDGET_LIMIT. Where the free tier refuses too, the
    refusal that leaves less is the one reported, so what is left, and the
    largest step that fits, are right. Raises when the budgets can't be
    read."""
    if not (result.allowed or result.error_code == FREE_TIER_LIMIT):
        return result
    budget = pause_budget_refusal(org_id, getattr(result, "dimension", ""), amount)
    if budget is None:
        return result
    if result.allowed or _left(budget) < _left(result):
        return budget
    return result
