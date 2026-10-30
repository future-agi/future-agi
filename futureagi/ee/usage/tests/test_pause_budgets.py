"""Pause budgets against what a step is about to cost (with_pause_budgets).

Budget enforcement pauses a dimension only after usage has crossed a pause
budget, so a step that starts under it ran whole: on Cloud, a 2-credit pause
budget at 1.2 credits used let a 2.9-credit Instant eval run finish at 4.14.
These tests pin the precheck that refuses such a step before it starts.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from ee.usage.schemas.events import CheckResult

pytestmark = pytest.mark.requires_ee

ALLOWED = CheckResult(allowed=True, dimension="ai_credits")


def pause_budget(monkeypatch, organization, threshold, used, *, action="pause"):
    """A "Pause — block further usage" budget (or another ``action``) of
    ``threshold`` AI credits for ``organization`` with ``used`` credits
    counted this month, and billing's own check allowing every step, as on a
    paid plan. Returns the budget."""
    from ee.usage.models.usage import UsageBudget
    from ee.usage.schemas.billing_config import DimensionConfig
    from ee.usage.services.metering import _get_current_period

    ai_credits = DimensionConfig(
        display_name="AI Credits",
        native_unit="credits",
        display_unit="credits",
        native_to_display_divisor=1,
        aggregation="sum",
    )
    # Only the budgets' dimension: the credit conversion keeps the real
    # billing config.
    monkeypatch.setattr(
        "ee.usage.services.budgets.BillingConfig",
        SimpleNamespace(
            get=lambda: SimpleNamespace(get_dimension={"ai_credits": ai_credits}.get)
        ),
    )
    counters = {
        f"usage:{organization.id}:ai_credits:{_get_current_period()}": str(used)
    }
    monkeypatch.setattr(
        "ee.usage.services.budgets.get_redis",
        lambda: SimpleNamespace(get=counters.get),
    )
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda org_id, event_type, amount=0: ALLOWED,
    )
    return UsageBudget.objects.create(
        organization=organization,
        name="AI credits hard cap",
        scope="ai_credits",
        threshold_value=Decimal(str(threshold)),
        action=action,
    )


def _check(organization, amount, result=ALLOWED):
    from ee.usage.services.budgets import with_pause_budgets

    return with_pause_budgets(str(organization.id), result, amount)


@pytest.mark.django_db
class TestWithPauseBudgets:
    def test_a_step_that_would_cross_a_pause_budget_is_refused(
        self, monkeypatch, organization
    ):
        pause_budget(monkeypatch, organization, 2, 1.2)
        refused = _check(organization, 2.94)
        assert refused.allowed is False
        assert refused.error_code == "BUDGET_LIMIT"
        assert (refused.current_usage, refused.limit) == (1.2, 2.0)
        assert refused.dimension == "ai_credits"

    def test_a_step_that_reaches_the_budget_exactly_still_runs(
        self, monkeypatch, organization
    ):
        # Budget enforcement pauses once usage is past the budget, not at it.
        pause_budget(monkeypatch, organization, 2, 1.2)
        assert _check(organization, 0.8) is ALLOWED

    def test_usage_already_past_the_budget_refuses_any_step(
        self, monkeypatch, organization
    ):
        # Counted, but the consumer hasn't paused it yet.
        pause_budget(monkeypatch, organization, 2, 2.4)
        assert _check(organization, 0.06).error_code == "BUDGET_LIMIT"

    @pytest.mark.parametrize("action", ["notify", "warn"])
    def test_budgets_that_only_notify_or_warn_dont_refuse(
        self, monkeypatch, organization, action
    ):
        pause_budget(monkeypatch, organization, 2, 1.2, action=action)
        assert _check(organization, 2.94) is ALLOWED

    def test_inactive_deleted_and_other_budgets_dont_refuse(
        self, monkeypatch, organization
    ):
        from accounts.models.organization import Organization

        budget = pause_budget(monkeypatch, organization, 2, 1.2)
        budget.is_active = False
        budget.save()
        budget.pk = None
        budget.is_active, budget.deleted = True, True
        budget.save()
        budget.pk = None
        budget.deleted, budget.scope = False, "total_spend"
        budget.save()
        budget.pk = None
        budget.scope = "ai_credits"
        budget.organization = Organization.objects.create(name="Someone else")
        budget.save()
        assert _check(organization, 2.94) is ALLOWED

    def test_the_lowest_pause_budget_applies(self, monkeypatch, organization):
        budget = pause_budget(monkeypatch, organization, 10, 1.2)
        budget.pk = None
        budget.threshold_value = Decimal("3")
        budget.save()
        refused = _check(organization, 2.0)
        assert refused.limit == 3.0

    def test_nothing_is_read_for_a_step_already_refused(
        self, monkeypatch, organization
    ):
        pause_budget(monkeypatch, organization, 2, 1.2)
        paused = CheckResult(allowed=False, error_code="BUDGET_PAUSED")
        assert _check(organization, 2.94, paused) is paused

    def test_with_the_free_tier_the_tighter_cap_is_reported(
        self, monkeypatch, organization
    ):
        pause_budget(monkeypatch, organization, 2, 1.2)
        # 1.8 free credits left, 0.8 under the budget: the budget binds.
        free = CheckResult(
            allowed=False,
            error_code="FREE_TIER_LIMIT",
            dimension="ai_credits",
            current_usage=1.2,
            limit=3.0,
        )
        assert _check(organization, 5.0, free).error_code == "BUDGET_LIMIT"
        # 0.3 free credits left: the free tier binds.
        tighter = free.model_copy(update={"limit": 1.5})
        assert _check(organization, 5.0, tighter) is tighter

    def test_a_check_without_a_dimension_reads_no_budgets(self, organization):
        # Billing's answer for an event type it doesn't know: allowed.
        unknown = CheckResult(allowed=True)
        assert _check(organization, 2.94, unknown) is unknown
