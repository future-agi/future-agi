"""Temporal activities for billing operations.

Two scheduled activities:
- report_stripe_usage_activity: hourly, reports pre-calculated usage costs to Stripe
- run_dunning_checks_activity: daily, processes dunning steps for past_due orgs

Pattern: @activity.defn (async) with close_old_connections + sync_to_async.
Errors re-raised for Temporal retry.
"""

from asgiref.sync import sync_to_async
from django.db import close_old_connections
from temporalio import activity

from tfc.temporal.billing.types import (
    DunningCheckInput,
    DunningCheckOutput,
    MonthlyClosingInput,
    MonthlyClosingOutput,
    MonthlyInvoiceInput,
    MonthlyInvoiceOutput,
    StripeUsageReportInput,
    StripeUsageReportOutput,
)

# ── Stripe Usage Reporting (hourly) ───────────────────────────────────────


@activity.defn(name="report_stripe_usage_activity")
async def report_stripe_usage_activity(
    input: StripeUsageReportInput,
) -> StripeUsageReportOutput:
    """Report metered usage to Stripe for all paid orgs.

    Re-raises on failure so Temporal applies retry policy.
    """
    close_old_connections()
    try:
        count = await sync_to_async(_report_stripe_usage_sync, thread_sensitive=False)(
            input.period
        )
        activity.logger.info(f"Stripe usage reported: {count} records")
        return StripeUsageReportOutput(records_reported=count, status="COMPLETED")
    finally:
        close_old_connections()


def _report_stripe_usage_sync(period: str) -> int:
    """Sync wrapper — calls existing report_all_usage_to_stripe."""
    close_old_connections()
    try:
        try:
            from ee.usage.tasks.stripe_reporting import report_all_usage_to_stripe
        except ImportError:
            report_all_usage_to_stripe = None

        return report_all_usage_to_stripe()
    finally:
        close_old_connections()


# ── Dunning Checks (daily) ────────────────────────────────────────────────


@activity.defn(name="run_dunning_checks_activity")
async def run_dunning_checks_activity(
    input: DunningCheckInput,
) -> DunningCheckOutput:
    """Process dunning steps for all past_due orgs.

    Queries orgs with status=past_due, calculates days_overdue,
    and runs the appropriate dunning step (Day 3: retry, Day 7: warn, Day 14: downgrade).
    Re-raises on failure so Temporal applies retry policy.
    """
    close_old_connections()
    try:
        count = await sync_to_async(_run_dunning_checks_sync, thread_sensitive=False)()
        activity.logger.info(f"Dunning checks processed: {count} orgs")
        return DunningCheckOutput(orgs_processed=count, status="COMPLETED")
    finally:
        close_old_connections()


def _run_dunning_checks_sync() -> int:
    """Sync wrapper — processes all past_due orgs."""
    close_old_connections()
    try:
        from datetime import datetime

        try:
            from ee.usage.models.usage import OrganizationSubscription
        except ImportError:
            OrganizationSubscription = None
        try:
            from ee.usage.services.dunning import DunningService
        except ImportError:
            DunningService = None

        past_due_subs = OrganizationSubscription.objects.filter(
            status="past_due", deleted=False
        )

        count = 0
        for sub in past_due_subs:
            # Calculate days overdue from billing_period_end or status change
            if sub.billing_period_end:
                days_overdue = (datetime.utcnow().date() - sub.billing_period_end).days
            else:
                days_overdue = 0

            try:
                DunningService.process_dunning_step(
                    str(sub.organization_id), days_overdue
                )
                count += 1
            except Exception:
                activity.logger.exception(
                    f"Dunning failed for org {sub.organization_id}"
                )
                # Continue processing other orgs; don't fail the entire batch
        return count
    finally:
        close_old_connections()


# ── Monthly Invoice Generation ─────────────────────────────────────────────


@activity.defn(name="generate_monthly_invoices_activity")
async def generate_monthly_invoices_activity(
    input: MonthlyInvoiceInput,
) -> MonthlyInvoiceOutput:
    """Generate invoices for all paid orgs for a billing period.

    Runs monthly (1st of each month). Generates invoices for the previous month.
    Re-raises on failure so Temporal applies retry policy.
    """
    close_old_connections()
    try:
        created, skipped, errors = await sync_to_async(
            _generate_monthly_invoices_sync, thread_sensitive=False
        )(input.period, input.org_id)
        activity.logger.info(
            f"Monthly invoices: created={created}, skipped={skipped}, errors={errors}"
        )
        return MonthlyInvoiceOutput(
            invoices_created=created,
            invoices_skipped=skipped,
            errors=errors,
            status="COMPLETED",
        )
    finally:
        close_old_connections()


def _generate_monthly_invoices_sync(
    period: str, org_id: str = ""
) -> tuple[int, int, int]:
    """Sync wrapper — generates invoices for paid orgs (or a single org).

    Delegates to ``InvoiceGenerationService`` so the CLI, Temporal schedule,
    and admin "Generate Invoice" page all share identical logic.
    """
    from datetime import datetime

    try:
        from ee.usage.services.invoice_generation import InvoiceGenerationService
    except ImportError:
        InvoiceGenerationService = None

    close_old_connections()
    try:
        # Default to previous month if not specified
        if not period:
            now = datetime.utcnow()
            if now.month == 1:
                period = f"{now.year - 1}-12"
            else:
                period = f"{now.year}-{now.month - 1:02d}"

        result = InvoiceGenerationService.run_for_period(
            period=period,
            org_id=org_id or None,
            dry_run=False,
            skip_stripe=False,
            skip_email=False,
            stdout=lambda msg: activity.logger.info(msg),
        )
        return result.created, result.skipped, result.errors
    finally:
        close_old_connections()


# ── Monthly Closing (reset + invoice gen, chained) ─────────────────────────


def _run_monthly_reset_sync(period: str) -> None:
    close_old_connections()
    try:
        from ee.usage.tasks.monthly_reset import run_monthly_reset

        run_monthly_reset(period=period)
    finally:
        close_old_connections()


@activity.defn(name="monthly_closing_activity")
async def monthly_closing_activity(
    input: MonthlyClosingInput,
) -> MonthlyClosingOutput:
    """Flush Redis usage to DB, then generate invoices for the same period.

    Reset must precede invoice generation: the invoice-gen idempotency gate
    skips orgs that already have an Invoice row for the period, so a retry
    can't recover events still buffered in Redis at fire time.

    ``input.period`` MUST be a non-empty ``YYYY-MM`` string, derived from
    ``workflow.now()`` in ``MonthlyClosingWorkflow``. No wall-clock
    fallback — see the workflow docstring for why.
    """
    close_old_connections()
    try:
        period = input.period
        if not period or len(period) != 7 or period[4] != "-":
            raise ValueError(
                f"monthly_closing_activity requires YYYY-MM period, got {period!r}"
            )
        activity.logger.info(f"monthly_closing_start period={period}")

        await sync_to_async(_run_monthly_reset_sync, thread_sensitive=False)(period)

        created, skipped, errors = await sync_to_async(
            _generate_monthly_invoices_sync, thread_sensitive=False
        )(period, "")
        activity.logger.info(
            f"monthly_closing_done period={period} "
            f"created={created} skipped={skipped} errors={errors}"
        )

        return MonthlyClosingOutput(
            period=period,
            invoices_created=created,
            invoices_skipped=skipped,
            errors=errors,
            status="COMPLETED",
        )
    finally:
        close_old_connections()
