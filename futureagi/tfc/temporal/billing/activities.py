"""Temporal activities for billing — invoice gen, monthly closing.

Dunning (payment retries, reminder emails, final unpaid/cancel) is owned
by Stripe Revenue Recovery; state lands here via webhooks only.
Stripe meter events fire at invoice-close (see invoice_generation.py);
no hourly catch-up. Errors re-raise so Temporal applies its retry policy.
"""

from datetime import datetime

from asgiref.sync import sync_to_async
from django.db import close_old_connections
from temporalio import activity
from tfc.temporal.billing.types import (
    MonthlyClosingInput,
    MonthlyClosingOutput,
    MonthlyInvoiceInput,
    MonthlyInvoiceOutput,
)

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
    try:
        from ee.cloud.billing.invoice_generation import InvoiceGenerationService
    except ImportError:
        InvoiceGenerationService = None

    if InvoiceGenerationService is None:
        # billing lives in the private cloud overlay (ee/cloud/); it is
        # absent from OSS and self-hosted EE images. Skip cleanly instead
        # of calling None.run_for_period(...) and crash-looping Temporal.
        activity.logger.info("invoice_generation_skipped_billing_is_cloud_only")
        return 0, 0, 0

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
            on_progress=activity.heartbeat,
        )
        return result.created, result.skipped, result.errors
    finally:
        close_old_connections()


# ── Monthly Closing (reset + invoice gen, chained) ─────────────────────────


def _run_monthly_reset_sync(period: str) -> None:
    try:
        from ee.usage.tasks.monthly_reset import run_monthly_reset
    except ImportError:
        run_monthly_reset = None

    if run_monthly_reset is None:
        # monthly_reset lives in the private cloud overlay (ee/cloud/); it is
        # absent from OSS and self-hosted EE images (ee/usage/tasks/ ships only
        # __init__.py + phone_home.py). Skip cleanly instead of raising
        # ImportError, which would fail monthly_closing_activity on the 1st of
        # every month on every self-hosted install — before the guarded invoice
        # step below even runs.
        activity.logger.info("monthly_reset_skipped_billing_is_cloud_only")
        return

    close_old_connections()
    try:
        run_monthly_reset(period=period)
    finally:
        close_old_connections()


def _parse_period(period: str) -> datetime:
    """Parse a ``YYYY-MM`` period string into a ``datetime`` (day=1)."""
    return datetime.strptime(period, "%Y-%m")


def _next_period_str(period: str) -> str:
    """``'2026-05' → '2026-06'``."""
    d = _parse_period(period)
    if d.month == 12:
        return f"{d.year + 1}-01"
    return f"{d.year}-{d.month + 1:02d}"


@activity.defn(name="monthly_closing_activity")
async def monthly_closing_activity(
    input: MonthlyClosingInput,
) -> MonthlyClosingOutput:
    """Reset then invoice. ``input.period`` is the period being closed
    (arrears); we derive the period being billed (advance) by adding one
    month. Reset must run first — BillingEngine reads the just-flushed
    UsageSummary for the closed period as the arrears half of the new
    invoice.
    """
    close_old_connections()
    try:
        period_closed = input.period
        try:
            _parse_period(period_closed or "")
        except (TypeError, ValueError):
            raise ValueError(
                f"monthly_closing_activity requires YYYY-MM period, got {period_closed!r}"
            )
        period_billed = _next_period_str(period_closed)
        activity.logger.info(
            f"monthly_closing_start closed={period_closed} billed={period_billed}"
        )

        await sync_to_async(_run_monthly_reset_sync, thread_sensitive=False)(
            period_closed
        )

        created, skipped, errors = await sync_to_async(
            _generate_monthly_invoices_sync, thread_sensitive=False
        )(period_billed, "")
        activity.logger.info(
            f"monthly_closing_done closed={period_closed} billed={period_billed} "
            f"created={created} skipped={skipped} errors={errors}"
        )

        return MonthlyClosingOutput(
            period=period_billed,
            closed_period=period_closed,
            invoices_created=created,
            invoices_skipped=skipped,
            errors=errors,
            status="COMPLETED",
        )
    finally:
        close_old_connections()
