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
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models.gcp_marketplace import (
    IN_SERVICE_STATES,
    GCPMarketplaceEntitlement,
    GCPMarketplaceEntitlementState,
    GCPMarketplaceUsageCheckpoint,
    GCPUsageReportStatus,
)
from accounts.services.gcp_procurement import metric_id_for, resolve_plan
from accounts.services.gcp_service_control import gcp_service_control
from ee.usage.services.config import BillingConfig
from tfc.logging.sentry import capture_message

logger = structlog.get_logger(__name__)

# Longer than the hourly cadence, so a window in flight is never flagged.
STUCK_PENDING_HOURS = 6

try:
    from ee.usage.models.usage import UsageSummary
except ImportError:
    UsageSummary = None


def _period_of(moment) -> str:
    return moment.strftime("%Y-%m")


def _period_start(moment):
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _period_bounds(period: str):
    """[start, end) of a YYYY-MM period in UTC."""
    year, month = int(period[:4]), int(period[5:7])
    start = datetime(year, month, 1, tzinfo=UTC)
    end = (
        datetime(year + 1, 1, 1, tzinfo=UTC)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=UTC)
    )
    return start, end


def _previous_period(moment) -> str:
    return _period_of(_period_start(moment) - timedelta(seconds=1))


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


# Reserved Marketplace label key. Free-form keys are accepted but ignored: only
# the reserved ones reach the customer's Cloud Billing cost breakdown. The other
# is cloudmarketplace.googleapis.com/resource_name, unused for now.
CONTAINER_LABEL = "cloudmarketplace.googleapis.com/container_name"


def _cost_attribution(entitlement) -> dict[str, str]:
    """Labels letting the customer attribute this charge inside their own org.

    Only the container is sent. UsageSummary is keyed on organization, so there
    is no finer resource to name until usage is tracked per workspace.
    """
    return {CONTAINER_LABEL: _org_label(entitlement).lower()[:63]}


def _already_reported(entitlement, dimension: str, period_start) -> Decimal:
    """Sum of what we have already sent for this metric in this period.

    Scoped to the period because UsageSummary resets on the 1st. Without that
    scoping the first report of a month computes a negative delta.
    """
    total = GCPMarketplaceUsageCheckpoint.objects.filter(
        entitlement=entitlement,
        metric=dimension,
        report_status=GCPUsageReportStatus.REPORTED,
        window_start__gte=period_start,
    ).aggregate(total=Sum("quantity_reported"))["total"]
    return Decimal(total or 0)


def _last_window_end(entitlement, dimension: str):
    """Where the last successful window ended, or None if never reported."""
    last = (
        GCPMarketplaceUsageCheckpoint.objects.filter(
            entitlement=entitlement,
            metric=dimension,
            report_status=GCPUsageReportStatus.REPORTED,
        )
        .order_by("-window_end")
        .first()
    )
    return last.window_end if last else None


def _plan_of(entitlement) -> str | None:
    """Internal plan, or None if the portal plan is unmapped. Resolved once."""
    try:
        plan, _interval = resolve_plan(entitlement.plan_id)
    except ValueError:
        logger.error(
            "gcp_marketplace_unmapped_plan",
            entitlement_id=entitlement.entitlement_id,
            plan_id=entitlement.plan_id,
        )
        return None
    return plan


def _free_allowance(dimension: str, plan: str) -> Decimal:
    """Monthly allowance for this dimension, in display units.

    Marketplace plans carry a flat per-unit rate with no allowance configured on
    Google's side, so the allowance has to be applied before reporting. Without
    this a Marketplace customer is charged from the first unit while a direct
    customer on the same plan gets the same allowance free.
    """
    return BillingConfig.get().get_free_allowance(dimension, plan)


def _billable_total(
    entitlement, dimension: str, period: str, plan: str
) -> Decimal | None:
    """Month-to-date usage above the free allowance, or None if not metered.

    No ledger row means no usage yet, which is zero, not unknown: Google
    expects a report every hour, zero included.
    """
    if UsageSummary is None:
        return None

    summary = UsageSummary.objects.filter(
        organization_id=entitlement.organization_id,
        dimension=dimension,
        period=period,
    ).first()
    if summary is None:
        return Decimal(0)

    total = Decimal(summary.total_usage or 0)
    billable = total - _free_allowance(dimension, plan)
    return billable if billable > 0 else Decimal(0)


def _quantity_for(
    entitlement, dimension: str, plan: str, period: str, period_start
) -> Decimal | None:
    """Usage in this window: the rise in the month-to-date total since the last.

    Every dimension is a running total, storage included, so these deltas sum
    to the figure the Stripe invoice run bills from the same ledger. Zero is a
    real answer and is reported: Google wants every hour accounted for.

    The period is passed in rather than read off the window end: a window
    that closes a month ends exactly at the next month's first instant, and
    belongs to the month it closes.
    """
    billable_total = _billable_total(entitlement, dimension, period, plan)
    if billable_total is None:
        return None
    if billable_total <= 0:
        return Decimal(0)

    # Subtracting the allowance from the cumulative total handles the crossover
    # on its own: nothing is reported until usage passes it, then only the excess.
    delta = billable_total - _already_reported(entitlement, dimension, period_start)
    return delta if delta > 0 else Decimal(0)


def report_entitlement_usage(
    entitlement: GCPMarketplaceEntitlement,
    _skip_check: bool = False,
    _window_end=None,
) -> int:
    """Report one window of usage for one entitlement. Returns metrics sent.

    `_skip_check` and `_window_end` are private and belong to
    report_final_window alone.
    """
    if not entitlement.usage_reporting_id:
        logger.warning(
            "gcp_marketplace_usage_skipped_no_consumer_id",
            entitlement_id=entitlement.entitlement_id,
        )
        return 0

    plan = _plan_of(entitlement)
    if plan is None:
        return 0

    # The final report passes the cancellation moment, since the part-hour
    # since the last boundary is the whole reason it runs.
    now = _window_end or _floor_hour(timezone.now())
    period_start = _period_start(now)

    # One operation per metric: Google reports errors per operation, so a bad
    # metric id fails alone rather than blocking every dimension.
    operations: list[dict] = []
    by_operation: dict[str, GCPMarketplaceUsageCheckpoint] = {}

    try:
        _collect_operations(
            entitlement, plan, now, period_start, operations, by_operation
        )
    except Exception as exc:
        # Nothing was sent, so anything written is FAILED. Left PENDING it
        # would read as "sent, outcome unknown" and be skipped for ever.
        _mark(list(by_operation.values()), GCPUsageReportStatus.FAILED, str(exc))
        raise

    if not operations:
        return 0

    checkpoints = list(by_operation.values())

    if _skip_check:
        return _send(entitlement, operations, by_operation, [])

    try:
        # Once: check answers for the consumer, identical on every operation.
        check_errors = gcp_service_control.check(operations[0])
    except Exception as exc:
        _mark(checkpoints, GCPUsageReportStatus.FAILED, str(exc))
        raise

    return _send(entitlement, operations, by_operation, check_errors)


def report_final_window(entitlement: GCPMarketplaceEntitlement) -> int:
    """Bill the part-hour between the last report and a cancellation.

    The only caller that may skip check. Google has already cancelled the
    entitlement by the time it tells us, so check would answer "not entitled"
    and we would drop usage the customer really did incur. Nothing else may
    use this: check is what stops a cancelled consumer being billed at all.

    The window ends just before Google's cancellation time, not at ours.
    Google's rule for usage reported after a cancellation: "The timestamp
    must be before the entitlement was canceled." The handler always runs
    some time after the event, so an endTime of "now" would be rejected
    every hour for ever. Usage between the two moments is not billable.
    """
    now = timezone.now()
    ended_at = entitlement.google_update_time
    window_end = min(now, ended_at - timedelta(seconds=1)) if ended_at else now
    return report_entitlement_usage(
        entitlement, _skip_check=True, _window_end=window_end
    )


def _collect_operations(
    entitlement, plan, now, period_start, operations, by_operation
) -> None:
    """Build one operation per metric, each with a PENDING checkpoint written.

    Accumulators are arguments, not return values, so a caller can still mark
    what was written if this raises partway through.
    """
    for dimension in settings.GCP_MARKETPLACE_DIMENSIONS:
        metric_id = metric_id_for(entitlement.plan_id, dimension)
        if not metric_id:
            logger.warning(
                "gcp_marketplace_no_metric_for_plan",
                plan_id=entitlement.plan_id,
                dimension=dimension,
            )
            continue
        is_float = dimension in settings.GCP_MARKETPLACE_FLOAT_DIMENSIONS
        last_end = _last_window_end(entitlement, dimension)

        # The first run of a month first closes the previous one: the hours
        # between the last report and midnight belong to that month's ledger
        # and would otherwise never be billed. Google accepts them until
        # 1 AM Pacific on the 1st, and this runs at 00:05 UTC.
        if last_end is not None and last_end < period_start:
            _add_window(
                entitlement,
                plan,
                dimension,
                metric_id,
                is_float,
                window_start=last_end,
                window_end=period_start,
                period=_period_of(last_end),
                operations=operations,
                by_operation=by_operation,
            )

        window_start = period_start if last_end is None else max(last_end, period_start)
        if window_start >= now:
            continue
        _add_window(
            entitlement,
            plan,
            dimension,
            metric_id,
            is_float,
            window_start=window_start,
            window_end=now,
            period=_period_of(now),
            operations=operations,
            by_operation=by_operation,
        )


def _add_window(
    entitlement,
    plan,
    dimension,
    metric_id,
    is_float,
    *,
    window_start,
    window_end,
    period,
    operations,
    by_operation,
) -> None:
    """Write one PENDING checkpoint for a window and queue its operation."""
    quantity = _quantity_for(
        entitlement, dimension, plan, period, _period_bounds(period)[0]
    )
    if quantity is None:
        return

    if not is_float:
        # Google takes an int64, so record what goes out. Storing the
        # fraction would count it reported and drop it.
        quantity = quantity.to_integral_value(rounding=ROUND_FLOOR)

    existing = GCPMarketplaceUsageCheckpoint.objects.filter(
        entitlement=entitlement, metric=dimension, window_start=window_start
    ).first()
    if existing and existing.report_status == GCPUsageReportStatus.PENDING:
        # Outcome unknown. Resending risks double billing, so leave it
        # for reconcile_usage to surface.
        logger.warning(
            "gcp_marketplace_usage_window_unresolved",
            entitlement_id=entitlement.entitlement_id,
            metric=dimension,
        )
        return
    if existing and existing.report_status == GCPUsageReportStatus.REPORTED:
        # update_or_create below would reset a billed row to PENDING and lose
        # the record of the charge.
        logger.error(
            "gcp_marketplace_usage_window_already_reported",
            entitlement_id=entitlement.entitlement_id,
            metric=dimension,
            window_start=_rfc3339(window_start),
        )
        return

    operation_id = str(_uuid.uuid4())

    # Reused, not inserted: the window is unique per metric, so a failed
    # attempt would collide here every hour after.
    checkpoint, _ = GCPMarketplaceUsageCheckpoint.objects.update_or_create(
        entitlement=entitlement,
        metric=dimension,
        window_start=window_start,
        defaults={
            "organization_id": entitlement.organization_id,
            "window_end": window_end,
            "quantity_reported": quantity,
            "operation_id": operation_id,
            "report_status": GCPUsageReportStatus.PENDING,
            "reported_at": None,
            "error_detail": "",
        },
    )
    by_operation[operation_id] = checkpoint
    operations.append(_operation_for(entitlement, checkpoint, metric_id, is_float))


def _operation_for(entitlement, checkpoint, metric_id: str, is_float: bool) -> dict:
    """The Service Control operation for one checkpoint, exactly as stored."""
    start = _rfc3339(checkpoint.window_start)
    end = _rfc3339(checkpoint.window_end)
    return gcp_service_control.build_operation(
        consumer_id=entitlement.usage_reporting_id,
        operation_id=checkpoint.operation_id,
        start_time=start,
        end_time=end,
        metric_values={metric_id: (float(checkpoint.quantity_reported), is_float)},
        operation_name=(
            f"usage_report_{_org_label(entitlement)}_{checkpoint.metric}_{start}_{end}"
        ),
    )


UNRESOLVED_ALARM = "gcp_marketplace_usage_unresolved"


def _alert_unresolved(event: str, entitlement, checkpoints, detail: str) -> None:
    """Page: these metrics are frozen until someone settles the row.

    An unknown window is never resent, and _last_window_end advances on
    REPORTED only, so the pair stops billing until then. A log line alone
    leaves that costing revenue for as long as nobody reads it.
    """
    metrics = sorted({checkpoint.metric for checkpoint in checkpoints})
    logger.error(
        event,
        entitlement_id=entitlement.entitlement_id,
        organization_id=str(entitlement.organization_id),
        metrics=metrics,
        windows=len(checkpoints),
        error=detail,
    )
    capture_message(
        event,
        level="error",
        tags={"alarm": UNRESOLVED_ALARM, "entitlement_id": entitlement.entitlement_id},
        context={
            "marketplace": {
                "organization_id": str(entitlement.organization_id),
                "metrics": metrics,
                "windows": len(checkpoints),
                "detail": detail,
            }
        },
    )


def _send(entitlement, operations, by_operation, check_errors) -> int:
    """Report the checked operations and record each outcome on its own row."""
    checkpoints = list(by_operation.values())

    if check_errors:
        # Nothing is lost by stopping here. The delta is computed from REPORTED
        # checkpoints only, so this window folds into the next successful one.
        _mark(checkpoints, GCPUsageReportStatus.FAILED, str(check_errors))
        logger.warning(
            "gcp_marketplace_usage_skipped_check_failed",
            entitlement_id=entitlement.entitlement_id,
        )
        return 0

    try:
        outcome = gcp_service_control.report(
            operations, user_labels=_cost_attribution(entitlement)
        )
    except Exception as exc:
        if gcp_service_control.is_definitive_failure(exc):
            # Google refused it or never received it, so nothing was billed
            # and the next run can safely send this window again.
            _mark(checkpoints, GCPUsageReportStatus.FAILED, str(exc))
            raise

        # A lost response is not a failure. Google may have billed this window
        # before the timeout, and it does not dedupe on operation id, so a
        # resend would charge it twice. Left PENDING: the next run skips it and
        # reconcile_usage surfaces it for a human to settle.
        _mark(checkpoints, GCPUsageReportStatus.PENDING, str(exc))
        _alert_unresolved(
            "gcp_marketplace_usage_report_outcome_unknown",
            entitlement,
            checkpoints,
            str(exc)[:500],
        )
        raise

    failed = [by_operation[oid] for oid in outcome.rejected if oid in by_operation]
    rest = [c for oid, c in by_operation.items() if oid not in outcome.rejected]

    if failed:
        _mark(failed, GCPUsageReportStatus.FAILED, "rejected by Service Control")

    if outcome.outcome_known:
        reported, unknown = rest, []
    else:
        # Google may have billed these inside the same 200, and does not dedupe,
        # so FAILED would resend and charge twice. Unknown, as a lost response.
        reported, unknown = [], rest

    if reported:
        _mark(reported, GCPUsageReportStatus.REPORTED, "")
    if unknown:
        detail = f"unattributed report errors: {outcome.unattributed}"
        _mark(unknown, GCPUsageReportStatus.PENDING, detail)
        _alert_unresolved(
            "gcp_marketplace_usage_report_outcome_unknown",
            entitlement,
            unknown,
            detail,
        )

    logger.info(
        "gcp_marketplace_usage_reported",
        entitlement_id=entitlement.entitlement_id,
        metrics=len(reported),
        rejected=len(failed),
        unknown=len(unknown),
    )
    return len(reported)


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


def billable_entitlements(require_consumer_id: bool = True):
    """One in-service entitlement per organization, oldest first.

    Usage is metered per organization, so two entitlements on one
    organization would each report the whole delta and Google would bill it
    twice. Google can send several ENTITLEMENT_ACTIVE events for one account
    when the listing allows multiple orders. Only the first purchase bills;
    the rest are logged as an error every run until someone resolves them.
    """
    active = GCPMarketplaceEntitlement.objects.filter(
        status__in=IN_SERVICE_STATES, organization__isnull=False
    ).select_related("organization")
    if require_consumer_id:
        active = active.exclude(usage_reporting_id="")

    chosen: dict = {}
    for entitlement in active.order_by("effective_at", "created_at"):
        first = chosen.get(entitlement.organization_id)
        if first is None:
            chosen[entitlement.organization_id] = entitlement
            continue
        logger.error(
            "gcp_marketplace_multiple_entitlements_for_org",
            organization_id=str(entitlement.organization_id),
            billing_entitlement_id=first.entitlement_id,
            ignored_entitlement_id=entitlement.entitlement_id,
        )
    return list(chosen.values())


def report_all_usage() -> dict:
    """Report the current window for every in-service entitlement.

    Then resend any cancellation tail that was refused: a cancelled entitlement
    has left this sweep, so nothing else would revisit it.
    """
    entitlements = 0
    metrics = 0
    failures = 0

    for entitlement in billable_entitlements():
        try:
            metrics += report_entitlement_usage(entitlement)
            entitlements += 1
        except Exception:
            failures += 1
            logger.exception(
                "gcp_marketplace_usage_report_failed",
                entitlement_id=entitlement.entitlement_id,
            )

    retried = _retry_failed_final_windows()

    return {
        "entitlements": entitlements,
        "metrics": metrics,
        "failures": failures,
        "final_windows_resent": retried["resent"],
        "final_window_failures": retried["failures"],
    }


def _retry_failed_final_windows() -> dict:
    """Resend a cancelled entitlement's tail if Google refused it.

    Resent as stored, not recomputed: the quantity was snapshotted at
    cancellation, and the ledger has kept growing since with usage the customer
    incurred on the free plan, which is not theirs to pay Google for.

    Only FAILED rows. PENDING means the outcome is unknown and stays with
    reconcile_usage; check is skipped for the same reason report_final_window
    skips it.
    """
    failed = (
        GCPMarketplaceUsageCheckpoint.objects.filter(
            report_status=GCPUsageReportStatus.FAILED,
            entitlement__status=GCPMarketplaceEntitlementState.CANCELLED,
            entitlement__organization__isnull=False,
        )
        .exclude(entitlement__usage_reporting_id="")
        .select_related("entitlement", "entitlement__organization")
        .order_by("entitlement_id", "metric")
    )

    by_entitlement: dict[str, tuple[GCPMarketplaceEntitlement, list]] = {}
    for checkpoint in failed:
        entry = by_entitlement.setdefault(
            checkpoint.entitlement_id, (checkpoint.entitlement, [])
        )
        entry[1].append(checkpoint)

    resent = 0
    failures = 0
    for entitlement, checkpoints in by_entitlement.values():
        try:
            resent += _resend_checkpoints(entitlement, checkpoints)
        except Exception:
            failures += 1
            logger.exception(
                "gcp_marketplace_final_usage_retry_failed",
                entitlement_id=entitlement.entitlement_id,
            )

    if resent or failures:
        logger.info(
            "gcp_marketplace_final_usage_retried", resent=resent, failures=failures
        )
    return {"resent": resent, "failures": failures}


def _resend_checkpoints(entitlement, checkpoints) -> int:
    operations: list[dict] = []
    by_operation: dict[str, GCPMarketplaceUsageCheckpoint] = {}

    for checkpoint in checkpoints:
        metric_id = metric_id_for(entitlement.plan_id, checkpoint.metric)
        if not metric_id:
            logger.warning(
                "gcp_marketplace_no_metric_for_plan",
                plan_id=entitlement.plan_id,
                dimension=checkpoint.metric,
            )
            continue

        # A fresh id per attempt, as the hourly path does. Google does not
        # dedupe on it, so reusing one buys nothing and muddles its error log.
        checkpoint.operation_id = str(_uuid.uuid4())
        checkpoint.report_status = GCPUsageReportStatus.PENDING
        checkpoint.error_detail = ""
        checkpoint.save(
            update_fields=[
                "operation_id",
                "report_status",
                "error_detail",
                "updated_at",
            ]
        )
        by_operation[checkpoint.operation_id] = checkpoint
        operations.append(
            _operation_for(
                entitlement,
                checkpoint,
                metric_id,
                checkpoint.metric in settings.GCP_MARKETPLACE_FLOAT_DIMENSIONS,
            )
        )

    if not operations:
        return 0
    return _send(entitlement, operations, by_operation, [])


def reconcile_usage(period: str | None = None) -> list[dict]:
    """Compare the ledger against what we recorded as reported.

    Under-reporting is silent: no customer complains about being charged too
    little, so nothing else surfaces it. Over-reporting reaches them as a wrong
    invoice. Neither shows up without this comparison.
    """
    if UsageSummary is None:
        return []

    now = timezone.now()
    # On the first two days the previous month is reconciled too: its last
    # hours were reported after the last daily run of that month.
    periods = [period] if period else [_period_of(now)]
    if not period and now.day <= 2:
        periods.append(_previous_period(now))

    discrepancies = []
    failures = 0

    # In-service only. A cancelled org keeps consuming on the free plan, so its
    # ledger keeps rising after the last report and would flag every day.
    # Entitlements with no consumer id are kept: nothing reports for them, so
    # this is the one place their unbilled usage shows up.
    for entitlement in billable_entitlements(require_consumer_id=False):
        try:
            for one_period in periods:
                discrepancies.extend(_reconcile_entitlement(entitlement, one_period))
        except Exception:
            # One org must not take the rest of the run, or the stuck-PENDING
            # count below, with it.
            failures += 1
            logger.exception(
                "gcp_marketplace_usage_reconcile_failed",
                entitlement_id=entitlement.entitlement_id,
            )

    stale = GCPMarketplaceUsageCheckpoint.objects.filter(
        report_status=GCPUsageReportStatus.PENDING,
        updated_at__lt=now - timedelta(hours=STUCK_PENDING_HOURS),
    )
    stale_count = stale.count()
    if stale_count:
        # Pending means we called Google and never learned the outcome. Retrying
        # risks double billing and skipping risks losing revenue, so these are
        # surfaced for a human rather than resolved automatically. Each row
        # freezes its (entitlement, metric) until then, so this is unbilled
        # revenue accruing, not a backlog that drains on its own.
        rows = list(stale.select_related("entitlement")[:20])
        frozen = [
            {
                "entitlement_id": row.entitlement.entitlement_id,
                "metric": row.metric,
                "window_start": _rfc3339(row.window_start),
                "stalled_hours": int((now - row.updated_at).total_seconds() // 3600),
                "error": row.error_detail[:200],
            }
            for row in rows
        ]
        logger.error(
            "gcp_marketplace_usage_checkpoints_stuck_pending",
            count=stale_count,
            checkpoints=frozen,
        )
        capture_message(
            "gcp_marketplace_usage_checkpoints_stuck_pending",
            level="error",
            tags={"alarm": UNRESOLVED_ALARM},
            context={
                "marketplace": {
                    "count": stale_count,
                    "oldest_hours": max(
                        (f["stalled_hours"] for f in frozen), default=0
                    ),
                    "frozen": frozen,
                }
            },
        )

    if failures:
        logger.error("gcp_marketplace_usage_reconcile_incomplete", failures=failures)

    return discrepancies


def _reconcile_entitlement(entitlement, period: str) -> list[dict]:
    plan = _plan_of(entitlement)
    if plan is None:
        return []

    period_start, period_end = _period_bounds(period)
    discrepancies = []
    for dimension in settings.GCP_MARKETPLACE_DIMENSIONS:
        # After the allowance, because that is what we report: the raw
        # total would flag every org by its own allowance. None means no
        # usage row, so zero, not skip -- skipping hides an over-report.
        ledger_total = _billable_total(entitlement, dimension, period, plan)
        ledger_total = (ledger_total or Decimal(0)).to_integral_value(
            rounding=ROUND_FLOOR
        )

        reported = GCPMarketplaceUsageCheckpoint.objects.filter(
            entitlement=entitlement,
            metric=dimension,
            report_status=GCPUsageReportStatus.REPORTED,
            window_start__gte=period_start,
            window_start__lt=period_end,
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

    return discrepancies
