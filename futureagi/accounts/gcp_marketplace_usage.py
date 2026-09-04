"""Report Marketplace usage from the existing usage ledger to Google.

An adapter, not a second metering system. Quantities come from UsageSummary,
which is already the source for Stripe reporting, and go out unchanged. No GCP
specific rates, no recalculation, no discounts applied here: a private offer's
economics live on the Marketplace offer and applying them twice would undercharge.

UsageSummary holds a cumulative month-to-date total per organization, dimension
and period. Google wants what was consumed during a window, so the delta is the
cumulative total minus everything already reported for that period.
"""

import re
import uuid as _uuid
from datetime import timedelta
from decimal import Decimal

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models.gcp_marketplace import (
    GCPMarketplaceEntitlement,
    GCPMarketplaceEntitlementState,
    GCPMarketplaceUsageCheckpoint,
    GCPUsageReportStatus,
)
from accounts.services.gcp_procurement import metric_id_for, resolve_plan
from accounts.services.gcp_service_control import gcp_service_control
from ee.usage.services.config import BillingConfig

logger = structlog.get_logger(__name__)

try:
    from ee.usage.models.usage import UsageSummary
except ImportError:
    UsageSummary = None


def _period_of(moment) -> str:
    return moment.strftime("%Y-%m")


def _period_start(moment):
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _floor_hour(moment):
    return moment.replace(minute=0, second=0, microsecond=0)


def _rfc3339(moment) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _org_label(entitlement) -> str:
    """Readable organization tag for the operation name, or the id if unusable."""
    organization = entitlement.organization if entitlement.organization_id else None
    name = (organization.name or "").strip() if organization else ""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:64]
    return slug or str(entitlement.organization_id)


def _already_reported(entitlement, dimension: str, period: str) -> Decimal:
    """Sum of what we have already sent for this metric in this period.

    Scoped to the period because UsageSummary resets on the 1st. Without that
    scoping the first report of a month computes a negative delta.
    """
    start = _period_start(timezone.now().replace(day=1))
    total = GCPMarketplaceUsageCheckpoint.objects.filter(
        entitlement=entitlement,
        metric=dimension,
        report_status=GCPUsageReportStatus.REPORTED,
        window_start__gte=start,
    ).aggregate(total=Sum("quantity_reported"))["total"]
    return Decimal(total or 0)


def _window_start_for(entitlement, dimension: str, period_start):
    """Contiguous windows: this one starts where the last one ended."""
    last = (
        GCPMarketplaceUsageCheckpoint.objects.filter(
            entitlement=entitlement,
            metric=dimension,
            report_status=GCPUsageReportStatus.REPORTED,
        )
        .order_by("-window_end")
        .first()
    )
    return last.window_end if last else period_start


def _free_allowance(dimension: str, plan: str) -> Decimal:
    """Monthly allowance for this dimension, in display units.

    Marketplace plans carry a flat per-unit rate with no allowance configured on
    Google's side, so the allowance has to be applied before reporting. Without
    this a Marketplace customer is charged from the first unit while a direct
    customer on the same plan gets the same allowance free.
    """
    return BillingConfig.get().get_free_allowance(dimension, plan)


def _quantity_for(entitlement, dimension: str, period: str) -> Decimal | None:
    """Billable delta for a counter, or the billable level for a gauge.

    Storage is held rather than consumed, so a delta is meaningless: report the
    point-in-time value and let Google accumulate it over the billing period.
    """
    if UsageSummary is None:
        return None

    summary = UsageSummary.objects.filter(
        organization_id=entitlement.organization_id,
        dimension=dimension,
        period=period,
    ).first()
    if summary is None:
        return None

    total = Decimal(summary.total_usage or 0)

    plan, _interval = resolve_plan(entitlement.plan_id)
    billable_total = total - _free_allowance(dimension, plan)
    if billable_total <= 0:
        return Decimal(0)

    if dimension in settings.GCP_MARKETPLACE_GAUGE_DIMENSIONS:
        return billable_total

    # Subtracting the allowance from the cumulative total handles the crossover
    # on its own: nothing is reported until usage passes it, then only the excess.
    delta = billable_total - _already_reported(entitlement, dimension, period)
    return delta if delta > 0 else Decimal(0)


def report_entitlement_usage(entitlement: GCPMarketplaceEntitlement) -> int:
    """Report one window of usage for one entitlement. Returns metrics sent."""
    if not entitlement.usage_reporting_id:
        logger.warning(
            "gcp_marketplace_usage_skipped_no_consumer_id",
            entitlement_id=entitlement.entitlement_id,
        )
        return 0

    now = _floor_hour(timezone.now())
    period = _period_of(now)
    period_start = _period_start(now)

    metric_values: dict[str, tuple[float, bool]] = {}
    checkpoints: list[GCPMarketplaceUsageCheckpoint] = []
    operation_id = str(_uuid.uuid4())

    for dimension in settings.GCP_MARKETPLACE_DIMENSIONS:
        metric_id = metric_id_for(entitlement.plan_id, dimension)
        if not metric_id:
            logger.warning(
                "gcp_marketplace_no_metric_for_plan",
                plan_id=entitlement.plan_id,
                dimension=dimension,
            )
            continue
        window_start = _window_start_for(entitlement, dimension, period_start)
        if window_start >= now:
            continue

        quantity = _quantity_for(entitlement, dimension, period)
        if quantity is None or quantity <= 0:
            continue

        checkpoint = GCPMarketplaceUsageCheckpoint.objects.create(
            organization_id=entitlement.organization_id,
            entitlement=entitlement,
            metric=dimension,
            window_start=window_start,
            window_end=now,
            quantity_reported=quantity,
            operation_id=operation_id,
            report_status=GCPUsageReportStatus.PENDING,
        )
        checkpoints.append(checkpoint)
        is_float = dimension in settings.GCP_MARKETPLACE_FLOAT_DIMENSIONS
        metric_values[metric_id] = (float(quantity), is_float)

    if not metric_values:
        return 0

    window_start = min(c.window_start for c in checkpoints)
    operation_name = (
        f"usage_report_{_org_label(entitlement)}"
        f"_{_rfc3339(window_start)}_{_rfc3339(now)}"
    )

    try:
        errors = gcp_service_control.report(
            consumer_id=entitlement.usage_reporting_id,
            operation_id=operation_id,
            start_time=_rfc3339(window_start),
            end_time=_rfc3339(now),
            metric_values=metric_values,
            operation_name=operation_name,
            user_labels={
                "environment": settings.ENV_TYPE,
                "region": settings.REGION,
            },
        )
    except Exception as exc:
        _mark(checkpoints, GCPUsageReportStatus.FAILED, str(exc))
        raise

    if errors:
        _mark(checkpoints, GCPUsageReportStatus.FAILED, str(errors))
        return 0

    _mark(checkpoints, GCPUsageReportStatus.REPORTED, "")
    logger.info(
        "gcp_marketplace_usage_reported",
        entitlement_id=entitlement.entitlement_id,
        metrics=len(metric_values),
    )
    return len(metric_values)


def _mark(checkpoints, status, error_detail) -> None:
    reported_at = timezone.now() if status == GCPUsageReportStatus.REPORTED else None
    with transaction.atomic():
        for checkpoint in checkpoints:
            checkpoint.report_status = status
            checkpoint.reported_at = reported_at
            checkpoint.error_detail = error_detail[:2000]
            checkpoint.save(
                update_fields=[
                    "report_status",
                    "reported_at",
                    "error_detail",
                    "updated_at",
                ]
            )


def report_all_usage() -> dict:
    """Report the current window for every active entitlement."""
    active = GCPMarketplaceEntitlement.objects.filter(
        status=GCPMarketplaceEntitlementState.ACTIVE,
        organization__isnull=False,
    ).select_related("organization").exclude(usage_reporting_id="")

    entitlements = 0
    metrics = 0
    failures = 0

    for entitlement in active.iterator(chunk_size=200):
        try:
            metrics += report_entitlement_usage(entitlement)
            entitlements += 1
        except Exception:
            failures += 1
            logger.exception(
                "gcp_marketplace_usage_report_failed",
                entitlement_id=entitlement.entitlement_id,
            )

    return {
        "entitlements": entitlements,
        "metrics": metrics,
        "failures": failures,
    }


def reconcile_usage(period: str | None = None) -> list[dict]:
    """Compare the ledger against what we recorded as reported.

    Under-reporting is silent: no customer complains about being charged too
    little, so nothing else surfaces it. Over-reporting reaches them as a wrong
    invoice. Neither shows up without this comparison.
    """
    if UsageSummary is None:
        return []

    now = timezone.now()
    period = period or _period_of(now)
    period_start = _period_start(now)

    discrepancies = []
    active = GCPMarketplaceEntitlement.objects.filter(
        organization__isnull=False
    ).exclude(usage_reporting_id="")

    for entitlement in active.iterator(chunk_size=200):
        for dimension in settings.GCP_MARKETPLACE_DIMENSIONS:
            if dimension in settings.GCP_MARKETPLACE_GAUGE_DIMENSIONS:
                continue

            summary = UsageSummary.objects.filter(
                organization_id=entitlement.organization_id,
                dimension=dimension,
                period=period,
            ).first()
            ledger_total = Decimal(summary.total_usage if summary else 0)

            reported = GCPMarketplaceUsageCheckpoint.objects.filter(
                entitlement=entitlement,
                metric=dimension,
                report_status=GCPUsageReportStatus.REPORTED,
                window_start__gte=period_start,
            ).aggregate(total=Sum("quantity_reported"))["total"]
            reported_total = Decimal(reported or 0)

            if ledger_total == reported_total:
                continue

            discrepancy = {
                "entitlement_id": entitlement.entitlement_id,
                "organization_id": str(entitlement.organization_id),
                "metric": dimension,
                "period": period,
                "ledger": str(ledger_total),
                "reported": str(reported_total),
                "difference": str(ledger_total - reported_total),
            }
            discrepancies.append(discrepancy)
            logger.warning("gcp_marketplace_usage_discrepancy", **discrepancy)

    stale = GCPMarketplaceUsageCheckpoint.objects.filter(
        report_status=GCPUsageReportStatus.PENDING,
        created_at__lt=now - timedelta(hours=6),
    ).count()
    if stale:
        # Pending means we called Google and never learned the outcome. Retrying
        # risks double billing and skipping risks losing revenue, so these are
        # surfaced for a human rather than resolved automatically.
        logger.warning("gcp_marketplace_usage_checkpoints_stuck_pending", count=stale)

    return discrepancies
