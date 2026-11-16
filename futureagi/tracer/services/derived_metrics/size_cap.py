"""Pause a project whose stored metric rows outgrow METRICS_MAX_CHECKPOINT_ROWS.

Every pass, compaction and retention checkpoint rewrites a project's stored
rows, so their count bounds what a pass costs. A project measured above the
limit, at enrollment (spans in its history window, a lower bound on its rows:
every span keeps a dependency row) or after any pass (the rows its head
reads), is paused as ``too_large``: no incremental, backfill or repair pass
runs, so nothing new is calculated and nothing partial is published. Privacy
work never pauses: physical expiry still runs once per expiry interval (an
expiry-only pass) and erasure is processed as for every project. A paused
project's head stops growing, so those rewrites stay at about the limit.
Reads keep serving the buckets that were final before the pause and report
the rest as not calculated, reason too_large. Raising the limit to at least
the measured rows resumes the project at the next dispatch tick; its first
pass finds the backlog larger than a pass and pages it through the burst
sweep. A project over the limit at enrollment never starts.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Sum

from tracer.models.derived_metric import (
    MetricAttemptBatch,
    MetricCompactionRange,
    MetricProjectRefresh,
    MetricRefreshAttempt,
    MetricStateConflict,
)

TOO_LARGE = "too_large"
# Provisional, from in-repo costs: one legacy retention rewrite (about 330 us a
# row) of 800k rows is about 265 s, half of a 540 s pass. Replace with the
# 10M measurement (one daily expiry rewrite and one erasure rewrite each within
# half of their deadlines).
DEFAULT_MAX_CHECKPOINT_ROWS = 800_000
MAX_CHECKPOINT_ROWS_LIMIT = 10_000_000_000


def max_checkpoint_rows():
    """Most stored metric rows a project may hold before it pauses."""
    value = getattr(
        settings, "METRICS_MAX_CHECKPOINT_ROWS", DEFAULT_MAX_CHECKPOINT_ROWS
    )
    if type(value) is not int or not 1 <= value <= MAX_CHECKPOINT_ROWS_LIMIT:
        raise ImproperlyConfigured(
            "METRICS_MAX_CHECKPOINT_ROWS must be an integer from 1 to "
            f"{MAX_CHECKPOINT_ROWS_LIMIT}"
        )
    return value


def head_rows(head):
    """Rows the head's allowlist stores, across every metric table.

    From PG receipts only; no ClickHouse read. A key rewritten by a delta
    counts once per copy the head reads, which is what a rewrite costs. Range
    output totals survive private-bound cleanup in the manifest envelope.
    Older envelopes can use still-present ranges, but missing proof is never
    a zero count: rebuild such a checkpoint before admitting fresh work.
    """
    if head is None:
        return 0
    attempts = {str(attempt) for _, attempt in head.allowed_attempts()}
    batches = MetricAttemptBatch.objects.filter(attempt_id__in=attempts).aggregate(
        rows=Sum("row_count")
    )["rows"]
    counted = {}
    for receipt in (getattr(head, "visibility_receipts", None) or {}).get("ranges", []):
        attempt, rows = receipt.get("attempt"), receipt.get("row_count")
        if attempt not in attempts or rows is None:
            continue
        if type(rows) is not int or rows < 0 or attempt in counted:
            raise MetricStateConflict("invalid checkpoint row-count receipt")
        counted[attempt] = rows
    remaining = attempts - counted.keys()
    # Compatibility with checkpoints published before totals were retained.
    # Do not count both the durable total and its still-present private ranges.
    for row in (
        MetricCompactionRange.objects.filter(attempt_id__in=remaining)
        .values("attempt_id")
        .annotate(rows=Sum("row_count"))
    ):
        counted[str(row["attempt_id"])] = row["rows"]
    if MetricRefreshAttempt.objects.filter(
        id__in=attempts - counted.keys(), source_ceilings__range_compaction=True
    ).exists():
        raise MetricStateConflict(
            "checkpoint row-count receipt unavailable; rebuild checkpoint before fresh work"
        )
    return (batches or 0) + sum(counted.values())


def pause_if_too_large(project_id, rows, *, now):
    """Pause the project when ``rows`` exceed the limit. Returns whether it did."""
    if rows <= max_checkpoint_rows():
        return False
    MetricProjectRefresh.objects.filter(project_id=project_id).update(
        paused_reason=TOO_LARGE, paused_at=now, paused_rows=rows, updated_at=now
    )
    return True


def pause_at_enrollment(lease, analytics, *, retention_days):
    """Before a project's first publication: pause it if it is already too large.

    Spans in the history window bound its rows from below (every span keeps a
    dependency row), counted only up to the limit + 1.
    """
    from tracer.services.derived_metrics.backfill import history_window_spans

    # A history decision is not an admission receipt. Older workers may have
    # decided every version yet repeatedly yielded before publishing a head.
    # Recheck that no-head state too; otherwise it bypasses the capped release
    # forever. Once a head exists, its actual stored receipts own this guard.
    if MetricProjectRefresh.for_project(lease.project_id).head_manifest_id is not None:
        return False
    spans = history_window_spans(
        analytics,
        lease.project_id,
        now=lease.clock(),
        retention_days=retention_days,
        limit=max_checkpoint_rows(),
    )
    return pause_if_too_large(lease.project_id, spans, now=lease.clock())


def resume_within_limit(*, now):
    """Resume every too_large project the current limit admits again."""
    return MetricProjectRefresh.objects.filter(
        paused_reason=TOO_LARGE, paused_rows__lte=max_checkpoint_rows()
    ).update(
        paused_reason="",
        paused_at=None,
        paused_rows=None,
        next_due_at=now,
        updated_at=now,
    )
