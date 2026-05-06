"""Billing Temporal activities and schedules.

Budget catch-up workflow runs every 15 minutes to evaluate budgets
that were missed by the consumer (e.g., consumer was down, budget created after threshold crossed).
Hourly Stripe usage reporting + daily dunning checks. The consumer workflow runs as a long-lived workflow (not a schedule).
"""

from datetime import datetime, timezone
from typing import Any, List

import structlog

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

logger = structlog.get_logger(__name__)


def _monthly_closing_workflow() -> Any:
    """Lazy import — workflow module pulls in temporalio's sandbox guards."""
    from tfc.temporal.billing.workflows import MonthlyClosingWorkflow

    return MonthlyClosingWorkflow


@temporal_activity(time_limit=300, queue="default")
def evaluate_budgets_catchup_activity():
    """Evaluate all active budgets that haven't fired this period.

    Called every 15 minutes by Temporal schedule. Catches budgets missed by consumer:
    - Consumer was down when threshold crossed
    - Budget created after usage already exceeded threshold
    - total_spend scope budgets (require BillingEngine cost calculation)
    """
    from accounts.models.organization import Organization
    try:
        from ee.usage.models.usage import UsageBudget, UsageSummary
    except ImportError:
        UsageBudget = None
        UsageSummary = None
    try:
        from ee.usage.services.budget_enforcement import evaluate_budgets_catchup, evaluate_total_spend_budget
    except ImportError:
        evaluate_budgets_catchup = None
        evaluate_total_spend_budget = None

    period = datetime.now(tz=timezone.utc).strftime("%Y-%m")

    active_budgets = (
        UsageBudget.objects.filter(
            is_active=True,
            deleted=False,
        )
        .exclude(last_triggered_period=period)
        .values_list("organization_id", "scope")
        .distinct()
    )

    evaluated = 0
    for org_id, scope in active_budgets.iterator(chunk_size=500):
        try:
            if scope == "total_spend":
                evaluate_total_spend_budget(str(org_id), period)
            else:
                evaluate_budgets_catchup(str(org_id), scope, period)
            evaluated += 1
        except Exception:
            logger.exception(
                "budget_catchup_evaluation_failed",
                org_id=str(org_id),
                scope=scope,
                period=period,
            )

    return {"evaluated": evaluated, "period": period}


BILLING_SCHEDULES: List[ScheduleConfig] = [
    # sync-usage-to-db: REMOVED — now runs inside UsageConsumerWorkflow (every 60s)
    ScheduleConfig(
        schedule_id="budget-catchup",
        activity_name="evaluate_budgets_catchup_activity",
        interval_seconds=900,
        queue="default",
        description="Evaluate budgets missed by consumer (every 15 min)",
    ),
    ScheduleConfig(
        schedule_id="stripe-usage-hourly",
        activity_name="report_stripe_usage_activity",
        interval_seconds=3600,
        queue="default",
        description="Report pre-calculated usage costs to Stripe (hourly)",
    ),
    ScheduleConfig(
        schedule_id="dunning-checks-daily",
        activity_name="run_dunning_checks_activity",
        interval_seconds=86400,
        queue="tasks_l",
        description="Process dunning steps for past_due orgs (daily)",
    ),
    ScheduleConfig(
        schedule_id="monthly-closing",
        activity_name="monthly_closing_activity",
        cron_expression="0 0 1 * *",
        catchup_window_seconds=7 * 86400,
        queue="tasks_s",
        description=(
            "Flush closing-period Redis usage to UsageSummary, then generate "
            "invoices for all paid orgs."
        ),
        workflow_class=_monthly_closing_workflow(),
    ),
]
