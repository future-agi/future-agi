"""Stop repeated nonpublishing passes without changing the publication protocol.

The pending frontier is the existing head/cursors/work flags, not a new private
work fingerprint. A publication resets the streak in its own transaction. New
arrivals may add debt behind that frontier; they cannot hide a stuck frontier.
Only counts are persisted here. Source identities stay in the existing purgeable
attempt ledger, which a pause neither consumes nor deletes.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q

from tracer.models.derived_metric import MetricProjectRefresh
from tracer.services.derived_metrics.publication import MAX_BATCH_ROWS

TOO_BUSY = "too_busy"
# Three gives the adaptive planner two chances to reduce its allowance (48 ->
# 24 -> 12). Two consecutive failed adaptations are enough; further identical
# passes burn fleet turns without consuming any work.
DEFAULT_YIELDS = 3
# Operator capacity hint for diagnostics and a one-time retry after settings
# improve. Never an admission threshold: hardware and project capture costs
# vary too much for a fixed estimate to establish non-progress.
DEFAULT_CAPTURE_MS = 2500
FRONTIER_FIELDS = (
    "head_manifest_id",
    "watermark_ns",
    "eval_watermark_ns",
    "remap_cursor_ns",
    "reconciliation_cursor",
    "reconciliation_started_at",
    "reconciliation_completed_at",
    "pending_backfill",
    "pending_repair",
    "pending_expiry",
    "pending_erasure",
)


def _integer(name, default, low, high):
    value = getattr(settings, name, default)
    if type(value) is not int or not low <= value <= high:
        raise ImproperlyConfigured(f"{name} must be an integer from {low} to {high}")
    return value


def max_yields():
    return _integer("METRICS_BUSY_MAX_YIELDS", DEFAULT_YIELDS, 2, 100)


def capture_capacity():
    from tracer.services.derived_metrics.admission import PASS_SECONDS
    from tracer.services.derived_metrics.refresh import pass_soft_margin_seconds

    cost = _integer("METRICS_BUSY_CAPTURE_MS", DEFAULT_CAPTURE_MS, 1, 60_000)
    return max(1, (PASS_SECONDS - pass_soft_margin_seconds()) * 1000 // cost)


def frontier(state):
    return tuple(getattr(state, field) for field in FRONTIER_FIELDS)


def counts(writer, *, owners=None, definitions=None):
    """Fixed, non-identifying fields only; never serialize arbitrary metadata."""
    previous = getattr(writer.lease, "metric_work_counts", {})
    result = {
        key: previous.get(key, 0)
        for key in ("owners", "conversation_definitions", "span_definitions")
    }
    if owners is not None:
        result["owners"] = owners
    if definitions is not None:
        result["conversation_definitions"] = sum(d.level != "span" for d in definitions)
        result["span_definitions"] = sum(d.level == "span" for d in definitions)
    result["captures"] = getattr(writer, "pass_captures", 0) + getattr(
        writer, "pass_span_captures", 0
    )
    result["estimated_captures"] = (
        result["conversation_definitions"] * -(-result["owners"] // MAX_BATCH_ROWS)
        + getattr(writer, "pass_span_captures", 0)
        - getattr(writer, "_busy_span_start", 0)
    )
    result["capture_capacity"] = capture_capacity()
    writer.lease.metric_work_counts = result
    return result


def record_mixed_closure(writer, attempt, definitions, owners):
    """Measure the expanded work, but always let a real pass try it.

    A fixed capture estimate cannot distinguish fast from slow hardware. Only
    record_yield may pause non-progressing work, after max_yields real attempts.
    Preserve every expanded owner BEFORE capture: a natural yield must not lose
    owners whose conversation capture has not started yet.
    """
    counts(writer, owners=len(owners), definitions=definitions)
    writer.append(
        attempt, "dirty_after", [{"unit_id": owner} for owner in sorted(owners)]
    )


def record_yield(lease, before):
    """All levels, including deadline cancellation before a capture starts.

    The deadline may already have elapsed. A nonblocking row lock + epoch/token
    CAS allows bookkeeping only; it grants no right to publish or move cursors.
    Progress in this pass (including maintenance) always wins over a pause.
    """
    if before is None:
        return
    from tracer.services.derived_metrics.deletion_protocol import try_control_update

    owned = MetricProjectRefresh.objects.filter(
        project_id=lease.project_id,
        fencing_epoch=lease.grant.fencing_epoch,
        lease_token=lease.grant.lease_token,
        paused_reason="",
    )
    state = owned.select_related("head_manifest").first()
    if state is None:
        return
    # Read without a row lock: lifecycle_lock_budget refuses ordinary locking
    # SQL after the deadline. The existing control-update seam temporarily
    # removes that budget, takes a nonblocking lock, and rechecks this CAS.
    expected = {}
    for field in FRONTIER_FIELDS:
        value = getattr(state, field)
        # JSONField exact=None means JSON null, not the SQL NULL default.
        expected[field + "__isnull" if value is None else field] = (
            True if value is None else value
        )
    unchanged = owned.filter(
        busy_yields=state.busy_yields,
        busy_counts=state.busy_counts,
        **expected,
    )
    if frontier(state) != before:
        try_control_update(unchanged, busy_yields=0)
        return
    # Keep a non-identifying generation counter as well as resetting on
    # publication. Older workers during a rolling update do not know the
    # new streak column, but their head advance must still break the streak.
    head = state.head_manifest  # select_related above: no extra query
    generation = head.generation if head is not None else 0
    streak = (
        state.busy_yields + 1
        if state.busy_counts.get("head_generation") == generation
        else 1
    )
    observed = getattr(lease, "metric_work_counts", {})
    defaults = {
        "owners": 0,
        "conversation_definitions": 0,
        "span_definitions": 0,
        "captures": 0,
        "estimated_captures": 0,
        "capture_capacity": capture_capacity(),
    }
    # Never let an instrumentation extension persist private keys or payloads.
    work = {
        key: observed[key]
        if type(observed.get(key)) is int and observed[key] >= 0
        else default
        for key, default in defaults.items()
    }
    work["head_generation"] = generation
    updates = {"busy_yields": streak, "busy_counts": work, "updated_at": lease.clock()}
    if streak >= max_yields():
        updates.update(
            paused_reason=TOO_BUSY, paused_at=lease.clock(), paused_rows=None
        )
    try_control_update(unchanged, **updates)


def resume_updates(now):
    return {
        "paused_reason": "",
        "paused_at": None,
        "paused_rows": None,
        "busy_yields": 0,
        "busy_counts": {},
        "pass_capture_budget": None,
        "next_due_at": now,
        "updated_at": now,
    }


def resume_within_budget(*, now):
    """Only a larger capacity that fits the recorded work can auto-resume.

    Requiring improvement prevents a backstop pause whose optimistic estimate
    already fitted from automatically retrying forever on every dispatch tick.
    Explicit Run now may retry unchanged settings, but will re-pause if stuck.
    """
    from tracer.services.derived_metrics.deletion_protocol import try_control_update

    capacity = capture_capacity()
    # A locked paused project must not hold the dispatch lock away from peers.
    return try_control_update(
        MetricProjectRefresh.objects.filter(
            Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now),
            paused_reason=TOO_BUSY,
            busy_counts__capture_capacity__lt=capacity,
            busy_counts__estimated_captures__lte=capacity,
        ),
        **resume_updates(now),
    )
