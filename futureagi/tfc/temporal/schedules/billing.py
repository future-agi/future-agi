"""Billing Temporal activities and schedules.

Schedules: budget catch-up (15 min), monthly closing (``0 0 1 * *``).
Dunning is owned by Stripe Revenue Recovery — no scheduled checks here.
Meter events fire at invoice-close inside the monthly closing activity —
no hourly catch-up. The usage consumer is a separate long-lived
workflow, not a schedule.
"""

from datetime import UTC, datetime
from typing import Any

import structlog

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

logger = structlog.get_logger(__name__)


def _monthly_closing_workflow() -> Any:
    """Lazy import — workflow module pulls in temporalio's sandbox guards."""
    from tfc.temporal.billing.workflows import MonthlyClosingWorkflow

    return MonthlyClosingWorkflow


def _total_spend_budgets_enforced() -> bool:
    """Whether Billing says total-spend budgets are enforced (the same
    per-process check of the installed billing package)."""
    # Lazy import — the view module pulls in DRF serializers.
    from tfc.views.deployment import budget_support

    return budget_support()["total_spend_budgets_enforced"]


@temporal_activity(time_limit=300, queue="default")
def evaluate_budgets_catchup_activity():
    """Evaluate unfired budgets and every active total-spend budget.

    Called every 15 minutes by Temporal schedule. Catches budgets missed by consumer:
    - Consumer was down when threshold crossed
    - Budget created after usage already exceeded threshold
    - total_spend scope budgets (require BillingEngine cost calculation)

    Total-spend budgets are evaluated only when Billing says they are
    enforced: the billing package compares the threshold with this billing
    period's usage charges. Otherwise they are skipped with an error, so a
    budget Billing calls not enforced never pauses usage.
    """

    try:
        from ee.usage.models.usage import UsageBudget
    except ImportError:
        UsageBudget = None
    try:
        from ee.cloud.billing.budget_enforcement import evaluate_budgets_catchup
    except ImportError:
        evaluate_budgets_catchup = None
    try:
        from ee.cloud.billing.budget_enforcement import (
            evaluate_total_spend_budgets_catchup,
        )
    except ImportError:
        evaluate_total_spend_budgets_catchup = None

    if (
        evaluate_budgets_catchup is None
        and evaluate_total_spend_budgets_catchup is None
    ):
        # Budget enforcement lives in the private cloud overlay (ee/cloud/),
        # absent from OSS/self-hosted images. Skip instead of calling None per
        # org every 15 min and spamming the failure log.
        logger.info("budget_catchup_skipped_billing_is_cloud_only")
        return

    period = datetime.now(tz=UTC).strftime("%Y-%m")

    # EE owns bounded cap discovery, including lazy query failures. Run it
    # before dimension discovery so an outage still alerts and protects known caps.
    evaluated = 0
    if evaluate_total_spend_budgets_catchup is None:
        # Keep dimension catch-up, but alert on total-spend version skew:
        # Sentry captures ERROR+ events, not WARNING breadcrumbs.
        logger.error(
            "budget_catchup_symbol_unavailable",
            symbol="evaluate_total_spend_budgets_catchup",
        )
    elif not _total_spend_budgets_enforced():
        # The package has the catch-up but does not measure total spend as
        # this billing period's usage charges, so Billing says total-spend
        # budgets are not enforced. Skip it so it cannot pause usage, keep
        # dimension catch-up, and alert on the skew at ERROR as above.
        logger.error(
            "budget_catchup_total_spend_skipped",
            symbol="evaluate_total_spend_budgets_catchup",
            reason="total_spend_measure_not_period_usage_charges",
        )
    else:
        evaluated = evaluate_total_spend_budgets_catchup(period)["evaluated"]

    if evaluate_budgets_catchup is None or UsageBudget is None:
        logger.warning(
            "budget_catchup_symbol_unavailable",
            symbol=(
                "evaluate_budgets_catchup"
                if evaluate_budgets_catchup is None
                else "UsageBudget"
            ),
        )
        return {"evaluated": evaluated, "period": period}

    active_budgets = (
        UsageBudget.objects.filter(
            is_active=True,
            deleted=False,
        )
        .exclude(scope="total_spend")
        .exclude(last_triggered_period=period)
        .values_list("organization_id", "scope")
        .distinct()
    )

    for org_id, scope in active_budgets.iterator(chunk_size=500):
        try:
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


BILLING_SCHEDULES: list[ScheduleConfig] = [
    # sync-usage-to-db: REMOVED — now runs inside UsageConsumerWorkflow (every 60s)
    ScheduleConfig(
        schedule_id="budget-catchup",
        activity_name="evaluate_budgets_catchup_activity",
        interval_seconds=900,
        queue="default",
        description="Evaluate budgets missed by consumer (every 15 min)",
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
