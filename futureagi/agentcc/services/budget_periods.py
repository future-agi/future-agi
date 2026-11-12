"""Pure effective-period comparison, using the same budget transform as push."""

from agentcc.contracts.gateway_admin import BudgetsConfig
from agentcc.services.config_push import _transform_budgets


def effective_budget_periods(budgets):
    """Map gateway counter identities to periods, including disabled/zero levels.

    All organization entry spellings share one counter. In particular, saved_at
    can change its period without changing either entry's explicit period. Reuse
    the push transform and contract aliases rather than reimplement precedence.
    Hierarchy levels inherit default_period, not org_period (budget.go).
    Admission caps have a separate, fixed daily period and revision contract.
    """
    if not isinstance(budgets, dict) or not budgets:
        return {}
    pushed = BudgetsConfig.model_validate(_transform_budgets(budgets))
    default_period = pushed.default_period or "monthly"
    periods = {}
    if pushed.org_limit is not None or any(
        isinstance(budgets.get(key), dict)
        for key in ("org_limit", "hard_limit", "organization", "org")
    ):
        periods[("org", "")] = pushed.org_period or default_period
    for group in ("teams", "users", "keys", "tags"):
        for name, entry in (getattr(pushed, group) or {}).items():
            periods[(group, name)] = entry.period or default_period
    return periods


def changes_existing_budget_period(active_budgets, next_budgets):
    before = effective_budget_periods(active_budgets)
    after = effective_budget_periods(next_budgets)
    return any(before[level] != after[level] for level in before.keys() & after.keys())
