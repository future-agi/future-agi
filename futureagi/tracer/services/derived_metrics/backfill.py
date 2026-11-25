"""Bounded version history and full-history repair inside the project writer.

Progress is a frozen publication intent, not a successful-SELECT acknowledgement.
B10 may give a project at most three pages per admission; pending_backfill remains
runnable on the next dispatch, without moving incremental_last_success_at merely
because a history chunk completed. No queue or second writer is introduced.
The version cursor stores coverage and the current private keyset, not a dirty
set or source payload. Completed phases clear their keyset. Expiry drops only
keysets older than the horizon and erasure drops every keyset; both keep the
non-identifying progress (phase, day bounds, counts) and move the CAS token.
"""

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from time import monotonic

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricVersion,
    MetricGenerationPin,
    MetricProjectRefresh,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.derived_metrics import changes
from tracer.services.derived_metrics.bounded_reads import (
    committed_branches,
    committed_key_pages,
    merge_bindings,
)
from tracer.services.derived_metrics.changes import ReconciliationCheckpoint
from tracer.services.derived_metrics.deletion_protocol import serialized_project
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_VALUES,
    SOURCE_DEPENDENCIES,
)

MAX_HISTORY_DAYS = 90
MAX_CHUNKS_PER_PASS = 3
# Start no further chunk once a pass has run this long (reservation liveness).
PASS_SOFT_SECONDS = 20
PAGE_SIZE = 1000
DEFAULT_MAX_BACKFILL_SPANS = 150_000
# A burst sweep commits one source page per publication. One page per pass
# could never catch a sustained burst up (10M: 1,000 span keys per ~3.7 min
# pass against 275k spans of backlog), so a pass keeps sweeping pages while
# they finish within this soft budget, each page published (committed) on its
# own so a killed pass keeps its progress. Configurable: METRICS_BURST_*.
DEFAULT_BURST_PASS_SECONDS = 20
DEFAULT_BURST_MAX_PAGES = 19  # below MAX_MANIFEST_DELTAS: one compaction a pass
DEFAULT_BURST_PAGE_KEYS = 5000
HISTORY = DerivedMetricVersion.HistoryMode.HISTORY
FORWARD = DerivedMetricVersion.HistoryMode.FORWARD


def _positive_setting(name, default, *, maximum):
    value = getattr(settings, name, default)
    if type(value) is not int or not 1 <= value <= maximum:
        raise ImproperlyConfigured(f"{name} must be an integer from 1 to {maximum}")
    return value


def burst_pass_seconds():
    """Soft wall budget of one pass's burst pages (no page starts after it)."""
    return _positive_setting(
        "METRICS_BURST_PASS_SECONDS", DEFAULT_BURST_PASS_SECONDS, maximum=300
    )


def burst_max_pages():
    """Most burst pages one pass publishes."""
    return _positive_setting(
        "METRICS_BURST_MAX_PAGES", DEFAULT_BURST_MAX_PAGES, maximum=200
    )


def burst_page_keys():
    """Source keys (span rows) per burst page, up to the discovery key cap.

    Every capture statement stays bounded by its own 500-unit batches, so a
    page's size sets how many statements it runs, not their memory. A larger
    page amortizes each page's fixed statements (~84, ~3 s at 10M).
    """
    return _positive_setting(
        "METRICS_BURST_PAGE_KEYS", DEFAULT_BURST_PAGE_KEYS, maximum=changes.MAX_KEYS
    )


def burst_pending(project_id):
    state = MetricProjectRefresh.for_project(project_id)
    cursor = state.reconciliation_cursor or {}
    return bool(
        state.pending_repair
        and cursor.get("floor")
        and cursor.get("phase") != "complete"
    )


def max_backfill_spans():
    """Largest project (spans in the history window) that still backfills."""
    value = getattr(settings, "METRICS_BACKFILL_MAX_SPANS", DEFAULT_MAX_BACKFILL_SPANS)
    if type(value) is not int or value < 1:
        raise ImproperlyConfigured("METRICS_BACKFILL_MAX_SPANS must be positive")
    return value


def history_days(retention_days):
    """The bounded history window: 90 days, or less under a shorter retention."""
    return (
        min(MAX_HISTORY_DAYS, retention_days)
        if retention_days > 0
        else MAX_HISTORY_DAYS
    )


def history_window_spans(analytics, project_id, *, now, retention_days, limit):
    """Physical span rows in [now - window, now), counted only up to limit + 1.

    The LIMIT stops the read once the answer (above the limit or not) is known,
    so the cost does not grow with the project. Only the project and partition
    keys are filtered: no GROUP BY, no collapse and no full scan. Unmerged
    revisions can count twice; that only errs towards starting with no history.
    """
    end = changes.utc(now)
    start = end - timedelta(days=history_days(retention_days))
    rows = changes.read(
        analytics,
        """SELECT count() AS spans FROM (
            SELECT 1 FROM spans
            PREWHERE project_id = toUUID(%(size_project)s)
                AND start_time >= toDateTime64(%(size_start)s, 6, 'UTC')
                AND start_time < toDateTime64(%(size_end)s, 6, 'UTC')
            LIMIT %(size_cap)s
        )""",
        {
            "size_project": str(project_id),
            "size_start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "size_end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "size_cap": limit + 1,
        },
        changes.MetricReadBudget(),
        settings=changes._block_settings(limit + 1),
    )
    return int(rows[0]["spans"])


def forward_coverage_start(definition):
    """coverage_start of a forward-only version; None means full history."""
    if getattr(definition, "history_mode", None) != FORWARD:
        return None
    coverage = getattr(definition, "coverage_start", None)
    if coverage is None:
        raise MetricStateConflict("forward-only version has no coverage start")
    return changes.utc(coverage)


def forward_floor(definitions):
    """Earliest coverage start when EVERY working version is forward-only.

    Source reconciliation for such a project need not visit spans starting
    earlier: no version computes a unit there. Any history (or legacy,
    undecided) version keeps the full retained-history source sweep. A bounded
    overflow burst additionally skips committed phases; a pre-existing full
    repair continues to preserve its older dependency/lifecycle debt.
    """
    starts = [forward_coverage_start(d) for d in definitions]
    if not starts or any(start is None for start in starts):
        return None
    return min(starts)


def _undecided(project_id):
    """Pending versions with no progress yet: they are about to enter backfill."""
    return pending_versions(project_id).filter(
        history_mode__isnull=True,
        backfill_status=DerivedMetricVersion.BackfillStatus.PENDING,
        backfill_cursor__isnull=True,
    )


def enter_backfill(lease, analytics, *, retention_days, limit=None):
    """Choose history or "start with no history" for versions entering backfill.

    Runs inside the writer's fenced pass, for the first enrollment and for every
    later version. The project size is measured once, cheaply and bounded; at
    most ``limit`` spans in the history window keeps today's backfill. Above it
    each version is forward-only: it records coverage_start and skips the
    history backfill. coverage_start is its enrollment time, never after this
    pass and never before arrivals the project already consumed without it
    (forward_coverage_start_for). It stays unserved (status running) until the
    first publication that computes it consumes arrivals: apply_publication
    activates it in that head switch, so a read is never pinned to a head that
    lacks it. A fresh forward-only project's change watermarks start at that
    time instead of the epoch, so the first discovery reads new arrivals only.
    Returns {version id: history mode}.
    """
    project_id = lease.project_id
    if not _undecided(project_id).exists():
        return {}
    limit = max_backfill_spans() if limit is None else limit
    now = lease.clock()
    size = history_window_spans(
        analytics,
        project_id,
        now=now,
        retention_days=retention_days(),
        limit=limit,
    )
    mode = FORWARD if size > limit else HISTORY
    from tracer.services.derived_metrics.spec import require_computable

    decided = {}
    with transaction.atomic():
        state = MetricProjectRefresh._locked(project_id, **lease.check())
        sweeping = _sweep_in_progress(state, project_id)
        for definition in _undecided(project_id).select_for_update(of=("self",)):
            try:
                require_computable(definition)
            except MetricInputError:
                # A legacy recipe that cannot be calculated stays undecided and
                # fails in the existing backfill path, exactly as before.
                continue
            if mode == HISTORY:
                # Today's history backfill, unchanged: no progress token moves.
                DerivedMetricVersion.objects.filter(id=definition.id).update(
                    history_mode=HISTORY
                )
                decided[str(definition.id)] = HISTORY
                continue
            coverage = forward_coverage_start_for(
                definition, state, now=now, sweeping=sweeping
            )
            # Moving the progress token fences any sealed initial intent that
            # still expects this version's empty, pending progress.
            DerivedMetricVersion.objects.filter(id=definition.id).update(
                history_mode=FORWARD,
                coverage_start=coverage,
                backfill_status=DerivedMetricVersion.BackfillStatus.RUNNING,
                backfill_cursor=None,
                backfill_days_done=0,
                backfill_days_total=0,
                backfill_updated_at=_progress_time(definition.backfill_updated_at, now),
            )
            decided[str(definition.id)] = FORWARD
        working = working_definitions(project_id)
        floor = forward_floor(working)
        if (
            floor is not None
            and state.head_manifest_id is None
            and not any(
                getattr(state, field) for field in MetricProjectRefresh.MONOTONE_FIELDS
            )
        ):
            # Nothing was ever published or consumed, and no version needs
            # history: discovery starts at enrollment, never at the epoch.
            start = changes.ns(floor)
            MetricProjectRefresh.objects.filter(pk=state.pk).update(
                **dict.fromkeys(MetricProjectRefresh.MONOTONE_FIELDS, start)
            )
        MetricProjectRefresh.objects.filter(pk=state.pk).update(
            pending_backfill=pending_versions(project_id).exists()
        )
    return decided


def _sweep_in_progress(state, project_id):
    """Whether a paged sweep that started before now has yet to complete."""
    cursor = state.reconciliation_cursor
    return (
        state.pending_repair
        and isinstance(cursor, dict)
        and cursor.get("phase") != "complete"
    ) or sweep_participants(project_id) is not None


def forward_coverage_start_for(definition, state, *, now, sweeping):
    """The first instant a forward-only version can claim every unit from.

    Its enrollment time, never after this pass. Every publication from this
    pass on computes the version, but arrivals already consumed were discovered
    without it: a unit starting before the consumed cursors would never be
    computed, so coverage starts no earlier than they do. A sweep that started
    before this pass consumes arrivals up to its own start when it completes,
    although its earlier pages never computed this version: coverage then
    starts at this pass, whose later arrivals every discovery still reads.
    """
    if sweeping:
        return changes.utc(now)
    coverage = min(changes.utc(definition.created_at), changes.utc(now))
    consumed = max(getattr(state, f) for f in MetricProjectRefresh.MONOTONE_FIELDS)
    return max(coverage, changes.from_ns(consumed)) if consumed > 0 else coverage


def pending_versions(project_id):
    """Only the newest draft can activate; superseded drafts are not work.

    A forward-only version has no history to backfill, so it is never pending
    here (see awaiting_forward_versions): no history chunk or sweep may start
    for it.
    """
    return (
        DerivedMetricVersion.objects.filter(
            metric__project_id=project_id,
            metric__deleted=False,
            metric__current_version=F("version"),
            pins_revoked_at__isnull=True,
            backfill_status__in=["pending", "running"],
        )
        .exclude(history_mode=FORWARD)
        .exclude(
            Q(metric__active_version_id__isnull=False)
            & Q(id=F("metric__active_version_id"))
        )
        .select_related("metric")
        .order_by(F("backfill_updated_at").asc(nulls_first=True), "created_at", "id")
    )


def awaiting_forward_versions(project_id):
    """Forward-only versions decided but not yet served: they activate with the
    first publication that computes them (apply_publication), exactly like a
    history version activates in its completing head switch. Until then reads
    keep the previous active version, or say "not calculated yet"."""
    return (
        DerivedMetricVersion.objects.filter(
            metric__project_id=project_id,
            metric__deleted=False,
            metric__current_version=F("version"),
            pins_revoked_at__isnull=True,
            history_mode=FORWARD,
            backfill_status=DerivedMetricVersion.BackfillStatus.RUNNING,
            activated_at__isnull=True,
        )
        .select_related("metric")
        .order_by("created_at", "id")
    )


def serving_versions(project_id):
    """Active, unrevoked versions: what reads can serve right now."""
    return DerivedMetricVersion.objects.filter(
        metric__project_id=project_id,
        metric__deleted=False,
        id=F("metric__active_version_id"),
        pins_revoked_at__isnull=True,
    )


SWEEP_PHASE = "sweep"


def _sweep_position(cursor):
    """Identity + nonidentifying position shared by every participant cursor."""
    return (
        (cursor.get("sweep") or {}).get("id"),
        cursor.get("start"),
        cursor.get("total"),
        cursor.get("done"),
        repr(sorted((cursor.get("coverage") or {}).items())),
    )


def sweep_participants(project_id):
    """(participants, shared cursor) of the in-progress initial sweep, else None.

    One publication writes every participant's cursor, and scrubs strip the same
    keyset from all of them, so participants never legitimately diverge. If they
    somehow do, conservatively restart one sweep over the current pending set.
    """
    participants = [
        d
        for d in pending_versions(project_id)
        if isinstance(d.backfill_cursor, dict)
        and d.backfill_cursor.get("phase") == SWEEP_PHASE
    ]
    if not participants:
        return None
    if len({_sweep_position(d.backfill_cursor) for d in participants}) != 1:
        return start_sweep(project_id)
    return participants, deepcopy(participants[0].backfill_cursor)


def start_sweep(project_id):
    """Every pending version joins; later drafts wait and backfill afterwards."""
    return list(pending_versions(project_id)), None


def _sweep_done(cursor, checkpoint):
    """Days of the window the sweep has passed (monotone, never complete early)."""
    total = cursor["total"]
    if checkpoint.phase == "complete":
        return total
    if checkpoint.phase != "sources":
        return max(0, total - 1)
    if checkpoint.day is None:
        return 0
    first = datetime.fromisoformat(cursor["start"]).date()
    return max(0, min(total - 1, (checkpoint.day - first).days))


def working_definitions(project_id, definitions=()):
    """Each touched owner uses EACH version's own immutable recipe/metadata."""
    versions = {str(d.id): d for d in definitions}
    active = DerivedMetricVersion.objects.filter(
        metric__project_id=project_id,
        metric__deleted=False,
        id=F("metric__active_version_id"),
        pins_revoked_at__isnull=True,
    )
    pending = (
        pending_versions(project_id)
        if MetricProjectRefresh.for_project(project_id).pending_backfill
        else ()
    )
    awaiting = awaiting_forward_versions(project_id)
    for definition in (*active, *awaiting, *pending):
        versions[str(definition.id)] = definition
    return list(versions.values())


def retirement_definitions(writer, head, definitions):
    """Pinned old recipes may still own rows that privacy expiry must retire.

    They are NOT added to normal touched-unit computation, which would otherwise
    resurrect old versions after cleanup. Only the expiry planner uses them.
    """
    versions = {(str(d.metric_id), d.version): d for d in definitions}
    needed: set[tuple[str, int]] = set()
    for table, prefix in ((FIELD_VALUES, "field"), (BUCKET_TOTALS, "metric")):
        relation, params = committed_rows_select(
            table, head, org_id=writer.org_id, project_id=writer.lease.project_id
        )
        needed.update(
            (str(metric), version)
            for metric, version in writer.store.execute(
                f"SELECT DISTINCT {prefix}_id, {prefix}_version FROM ({relation})",
                params,
            )
        )
    if not needed - versions.keys():
        return list(versions.values())
    for definition in DerivedMetricVersion.objects.filter(
        metric__project_id=writer.lease.project_id,
        metric_id__in={metric for metric, _ in needed},
        pins_revoked_at__isnull=True,
    ):
        key = str(definition.metric_id), definition.version
        if key in needed:
            versions[key] = definition
    if needed - versions.keys():
        raise MetricStateConflict("retirement definition is unavailable")
    return list(versions.values())


def has_private_keyset(cursor):
    """Both daily and nested coverage positions can contain private identities."""
    return isinstance(cursor, dict) and (
        cursor.get("after") is not None or has_private_keyset(cursor.get("coverage"))
    )


def _progress_time(previous, now):
    # The existing progress timestamp is also an invalidation token. Never reuse
    # it, including with a frozen clock or a None -> cursor -> None reset (ABA).
    return max(now, previous + timedelta(microseconds=1)) if previous else now


def _moment(value):
    return changes.utc(
        value if isinstance(value, datetime) else datetime.fromisoformat(value)
    )


def retained_cutoff(writer, now):
    """The cutoff a writer enforces physically (batched; reads stay exact)."""
    from tracer.services.derived_metrics.retention import physical_cutoff

    return physical_cutoff(writer, now=now)


def expiry_floor(attempt):
    """The horizon a logical-expiry checkpoint enforced; None means strip all."""
    floor = attempt.source_ceilings.get("retention_floor")
    return _moment(floor) if floor else None


def publication_horizon(writer, attempt):
    """(strip?, cutoff) that a published private position must still satisfy.

    A sealed intent may have been planned before its keyset aged out, so every
    publication re-checks the CURRENT horizon (and an expiry checkpoint's own
    floor). An expiry checkpoint without a recorded floor strips everything.
    """
    floors = []
    if attempt.source_ceilings.get("logical_expiry"):
        floor = expiry_floor(attempt)
        if floor is None:
            return True, None
        floors.append(floor)
    if writer.retention_days is not None:
        current = retained_cutoff(writer, writer.lease.clock())
        if current is not None:
            floors.append(current)
    return (True, max(floors)) if floors else (False, None)


def _position_expired(position, cutoff, *, fallback=None):
    """Whether ONE level's private keyset must go under the given horizon.

    cutoff=None is erasure or abandonment: every private position goes. Else an
    exact keyset date decides; a source key falls back to its sorting hour (a
    lower bound); a nested coverage key falls back to its own pass start. Any
    other key that cannot be dated is conservatively treated as expired.
    """
    if not isinstance(position, dict) or position.get("after") is None:
        return False
    if cutoff is None:
        return True
    if position.get("keyset_start"):
        return _moment(position["keyset_start"]) < cutoff
    after = position["after"]
    if position.get("phase") == "sources" and len(after) == len(changes.SOURCE_ORDER):
        return _moment(after[2]) < cutoff
    return fallback is None or _moment(fallback) <= cutoff


def strip_private_positions(cursor, *, cutoff=None):
    """Return (copy without private positions, whether any position went).

    Only paging keysets (after/keyset_start, also nested in coverage) identify
    sources. Phase, day bounds, counts, schema and pass start are progress and
    survive, so the next page re-reads just that one day or phase from its start.
    """
    if not isinstance(cursor, dict):
        return cursor, False
    result, removed = dict(cursor), False
    if _position_expired(result, cutoff):
        result.update(after=None, keyset_start=None)
        removed = True
    coverage = result.get("coverage")
    if isinstance(coverage, dict) and _position_expired(
        coverage,
        cutoff,
        fallback=coverage.get("started_at") or result.get("end"),
    ):
        result["coverage"] = {**coverage, "after": None, "keyset_start": None}
        removed = True
    return result, removed


def _scrub_versions(selected, *, now, cutoff, clear, always, using):
    for definition in selected:
        cursor, removed = strip_private_positions(
            definition.backfill_cursor, cutoff=cutoff
        )
        if clear and removed:
            cursor = None  # abandoned: no progress worth keeping, only counters
        if removed or always:
            DerivedMetricVersion.objects.using(using).filter(id=definition.id).update(
                backfill_cursor=cursor,
                backfill_updated_at=_progress_time(definition.backfill_updated_at, now),
            )


def _scrub_repair(project_id, *, now, cutoff, always, using):
    state = (
        MetricProjectRefresh.objects.using(using).filter(project_id=project_id).first()
    )
    if state is None:
        return
    cursor, removed = strip_private_positions(
        state.reconciliation_cursor, cutoff=cutoff
    )
    if removed or always:
        # The phase/day survive, so pending_repair and the completion time stay
        # truthful; only the token moves, which makes sealed intents stale.
        MetricProjectRefresh.objects.using(using).filter(project_id=project_id).update(
            reconciliation_cursor=cursor,
            reconciliation_started_at=_progress_time(
                state.reconciliation_started_at, now
            ),
        )


def scrub_private_progress(
    project_id,
    *,
    now,
    versions=None,
    reset_repair=True,
    invalidate_empty=True,
    clear=False,
    using="default",
):
    """Serialized erasure scrub of private paging positions, never of progress.

    Erasure cannot know whose identity a keyset holds, so every selected private
    position goes while non-identifying progress stays. The token makes even
    sealed initial (expected=None) intents stale. clear=True is only for
    abandoned versions: a cursor holding a keyset is dropped whole (row-level
    counters remain). Ordinary history GC must NOT call this: safe pins remain
    GC roots.
    """
    with serialized_project(project_id, using=using):
        MetricRefreshAttempt.scrub_private_cursor_metadata(project_id, using=using)
        selected = DerivedMetricVersion.objects.using(using).filter(
            metric__project_id=project_id,
        )
        if versions is not None:
            selected = selected.filter(id__in=versions)
        _scrub_versions(
            selected,
            now=now,
            cutoff=None,
            clear=clear,
            always=invalidate_empty,
            using=using,
        )
        if reset_repair:
            _scrub_repair(
                project_id, now=now, cutoff=None, always=invalidate_empty, using=using
            )


def scrub_superseded_progress(project_id, *, using="default"):
    abandoned = (
        DerivedMetricVersion.objects.using(using)
        .filter(
            metric__project_id=project_id,
            version__lt=F("metric__current_version"),
        )
        .exclude(
            Q(metric__active_version_id__isnull=False)
            & Q(id=F("metric__active_version_id"))
        )
    )
    scrub_private_progress(
        project_id,
        now=timezone.now(),
        versions=abandoned.values("id"),
        reset_repair=False,
        invalidate_empty=False,
        clear=True,
        using=using,
    )


def _private_keyset_start(batch, phase, after, *, fallback=None):
    """Date the actual retained key, not its coarse physical sorting hour."""
    if after is None:
        return None
    if phase in {"sources", "dependencies"}:
        key = changes.SourceKey(*after[-2:])
        positions = {d.key: d for d in (*batch.prior_dependencies, *batch.dependencies)}
        if key in positions:
            fallback = positions[key].unit_start
    return fallback.isoformat() if isinstance(fallback, datetime) else fallback


def scrub_expired_progress(project_id, *, cutoff, now, using="default"):
    """Selective, serialized expiry of private positions, never of progress.

    Also covers stranded cursors with no remaining field rows, superseded drafts
    and a shrunk policy: each keyset is judged by its own date against the
    current horizon. Retained keysets and their tokens are left untouched, so an
    in-flight chunk is not invalidated merely because time moved. Whole expired
    days are skipped by the readers, not by resetting progress here.
    """
    with serialized_project(project_id, using=using):
        MetricRefreshAttempt.scrub_private_cursor_metadata(project_id, using=using)
        _scrub_versions(
            DerivedMetricVersion.objects.using(using)
            .filter(metric__project_id=project_id)
            .exclude(backfill_cursor=None),
            now=now,
            cutoff=cutoff,
            clear=False,
            always=False,
            using=using,
        )
        _scrub_repair(project_id, now=now, cutoff=cutoff, always=False, using=using)


def obsolete_versions(project_id, *, now, exclude_attempt_id=None):
    """Checkpoint omission only; old pinned manifests remain GC roots.

    A definition-less request pin is deliberately conservative. Cleanup waits
    for it too, rather than guessing which semantic version the request used.
    """
    pins = MetricGenerationPin.objects.filter(
        project_id=project_id, expires_at__gt=now, invalidated_at__isnull=True
    )
    if pins.filter(definition_version_id=None).exists():
        return ()
    versions = (
        DerivedMetricVersion.objects.filter(metric__project_id=project_id)
        .filter(
            Q(activated_at__isnull=False) | Q(version__lt=F("metric__current_version"))
        )
        .exclude(
            Q(metric__active_version_id__isnull=False)
            & Q(id=F("metric__active_version_id"))
        )
        .exclude(version=F("metric__current_version"))
        .exclude(id__in=pins.values("definition_version_id"))
        .order_by("metric_id", "version")
    )
    attempts = MetricRefreshAttempt.objects.filter(
        project_id=project_id, state__in=MetricRefreshAttempt.OPEN_STATES
    ).exclude(id=exclude_attempt_id)
    open_ids = {str(v["id"]) for attempt in attempts for v in attempt.metric_versions}
    return tuple(
        (definition.metric_id, definition.version)
        for definition in versions
        if str(definition.id) not in open_ids
    )


def pending_omissions(project_id, *, now):
    """Eligible obsolete versions that no checkpoint has retired yet.

    Retention checkpoints copy every version forward and reset the delta chain,
    so the writer must not wait for a long delta chain to omit them. Retired
    versions stay in obsolete_versions (a harmless NOT IN) but are not new work.
    """
    obsolete = obsolete_versions(project_id, now=now)
    if not obsolete:
        return ()
    retired = set(
        DerivedMetricVersion.objects.filter(
            metric__project_id=project_id, pins_revoked_at__isnull=False
        ).values_list("metric_id", "version")
    )
    return tuple(item for item in obsolete if item not in retired)


def check_retirement_publication(writer, attempt, control):
    """Final eligibility + permanent capture/activation fence with head switch.

    Planning holds the project lock, but staging does not. Recompute eligibility
    for EXACTLY the frozen omissions; a reactivation, new live pin, current draft
    or recoverable attempt makes the whole publication fail, not the pin expire.
    """
    intended = {tuple(item) for item in control.get("retire_versions", ())}
    if not intended:
        return
    if attempt.purpose != "compaction":
        raise MetricStateConflict("only a checkpoint may retire version payload")
    eligible = {
        (str(metric), version)
        for metric, version in obsolete_versions(
            writer.lease.project_id,
            now=writer.lease.clock(),
            exclude_attempt_id=attempt.id,
        )
    }
    if not intended <= eligible:
        raise MetricStateConflict("version retirement eligibility changed")
    return intended


def retire_omitted_versions(writer, attempt, control):
    """Retire only inside the transaction that publishes the frozen omission."""
    intended = check_retirement_publication(writer, attempt, control)
    now = writer.lease.clock()
    newly_retired = False
    for metric, version in intended or ():
        definition = DerivedMetricVersion.objects.get(
            metric_id=metric,
            version=version,
            metric__project_id=writer.lease.project_id,
        )
        newly_retired |= definition.pins_revoked_at is None
        DerivedMetricVersion.objects.filter(id=definition.id).update(
            pins_revoked_at=definition.pins_revoked_at or now,
            backfill_cursor=None,
            backfill_updated_at=_progress_time(definition.backfill_updated_at, now),
        )
    if newly_retired:
        from tracer.services.derived_metrics.deletion_protocol import _intent

        # Omission removes the head's nomination, not its privacy obligation.
        # Persist only namespace bounds; policy expiry must still visit all old
        # physical revisions even while their ordinary history TTL is live.
        _intent(
            organization_id=writer.org_id,
            project_id=writer.lease.project_id,
            scope="history",
            deletion_key=f"omission:{attempt.id}",
            store_payload={
                "protocol": "metric_omission",
                "through_generation": attempt.generation,
            },
        )


def discharge_omission_debts(project_id, *, now):
    """Close omission receipts once ordinary history GC removed their namespaces.

    Call only after the external store dropped unregistered attempts' rows
    (collect_garbage after scrub_orphans). Omitted payload lives only in attempts
    older than the omitting checkpoint, which itself froze NOT IN those versions.
    This is retention-independent: unlimited policies never run the expiry
    scrub, which remains a second, earlier discharge path for finite policies.
    """
    from tracer.models.derived_metric import MetricErasureRequest

    with serialized_project(project_id):
        debts = MetricErasureRequest.objects.filter(
            project_key=project_id, store_payload__protocol="metric_omission"
        ).exclude(status="done")
        if not debts.exists():
            return 0
        head = MetricProjectRefresh.for_project(project_id).head_manifest
        reachable = {a for _, a in head.allowed_attempts()} if head else set()
        registered = MetricRefreshAttempt.objects.filter(project_id=project_id).exclude(
            id__in=reachable
        )
        done = 0
        for debt in debts:
            through = debt.store_payload["through_generation"]
            if registered.filter(generation__lt=through).exists():
                continue
            done += MetricErasureRequest.objects.filter(id=debt.id).update(
                status="done",
                completed_at=now,
                receipts={"history_collected": {"through_generation": through}},
            )
        return done


def _initial_cursor(definition, *, now, retention_days):
    days = history_days(retention_days)
    return {
        "schema": 1,
        "start": (now - timedelta(days=days)).isoformat(),
        "end": now.isoformat(),
        "day_end": now.isoformat(),
        "phase": "sources",
        "after": None,
        "done": 0,
        "total": days,
        "expected_active": str(definition.metric.active_version_id)
        if definition.metric.active_version_id
        else None,
        "coverage": None,
    }


def _bound_after(columns, after, prefix):
    if after is None:
        return "1", {}
    return changes.keyset_after(columns, tuple(after), prefix)


def _grouped_committed_page(
    analytics, writer, head, table, columns, after, start, end, limit, budget
):
    """Find the globally first groups, not the first groups in storage order.

    A field's physical key starts with its definition, while backfill groups
    by owner. Every full-key page must be collapsed and inspected before a
    grouped cursor can be advanced. A read-budget failure leaves that cursor
    unchanged; it is never evidence that the final group has been visited.
    """
    if limit <= 0:
        return []
    winners: dict[tuple[str, ...], datetime] = {}
    used_bytes = 0
    for keys in committed_key_pages(
        writer.store,
        table,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
        target_rows=500,
    ):
        physical_keys = tuple(
            c for c in table.logical_key if c not in {"org_id", "project_id"}
        )
        relation, bound = committed_rows_select(
            table,
            head,
            org_id=writer.org_id,
            project_id=writer.lease.project_id,
            key_in={physical_keys: keys},
            param_prefix="history_group",
        )
        predicate, cursor_bound = _bound_after(columns, after, "history_group_after")
        group = ", ".join(columns)
        rows = changes.read(
            analytics,
            f"SELECT {group}, min(unit_start) AS keyset_start FROM ({relation}) "
            "WHERE unit_start >= toDateTime64(%(history_group_start)s, 6, 'UTC') "
            "AND unit_start < toDateTime64(%(history_group_end)s, 6, 'UTC') "
            f"AND {predicate} GROUP BY {group} ORDER BY {group}",
            merge_bindings(
                bound,
                cursor_bound,
                {
                    "history_group_start": start,
                    "history_group_end": end,
                },
            ),
            budget,
            settings=changes._block_settings(500),
        )
        for row in rows:
            key = tuple(str(row[column]) for column in columns)
            if key in winners:
                winners[key] = min(winners[key], row["keyset_start"])
                continue
            size = sum(len(part.encode("utf-8")) for part in key)
            if len(winners) == limit and key >= max(winners):
                continue
            if len(winners) == limit:
                discarded = max(winners)
                used_bytes -= sum(len(part.encode("utf-8")) for part in discarded)
                del winners[discarded]
            if used_bytes + size > 1024 * 1024:
                raise changes.MetricChangeReadError(
                    "backfill grouped key budget exceeded"
                )
            winners[key] = row["keyset_start"]
            used_bytes += size
    return [
        {**dict(zip(columns, key, strict=True)), "keyset_start": winners[key]}
        for key in sorted(winners)
    ]


def _older_history_end(analytics, writer, head, *, floor, end, budget):
    """Conservative physical nominees, including losers/deletes and old owners.

    A raw maximum may nominate an unnecessary day; it cannot skip a committed
    vanished field or dependency. No private identity is stored in this hint.
    """
    params = {
        "history_project": str(writer.lease.project_id),
        "history_floor": floor,
        "history_end": end,
    }
    branches = [
        "SELECT start_time AS moment FROM spans PREWHERE project_id = toUUID(%(history_project)s) "
        "AND start_time >= %(history_floor)s AND start_time < %(history_end)s"
    ]
    for table in (SOURCE_DEPENDENCIES, FIELD_VALUES):
        raw, bound = committed_branches(
            table,
            head,
            org_id=writer.org_id,
            project_id=writer.lease.project_id,
            columns=("unit_start",),
            param_prefix="history_" + table.name,
        )
        params = merge_bindings(params, bound)
        branches.extend(
            f"SELECT unit_start AS moment FROM ({sql}) WHERE unit_start >= %(history_floor)s AND unit_start < %(history_end)s"
            for sql in raw
        )
    rows = changes.read(
        analytics,
        "SELECT maxOrNull(moment) AS latest FROM ("
        + " UNION ALL ".join(branches)
        + ")",
        params,
        budget,
    )
    return changes.utc(rows[0]["latest"]) if rows[0]["latest"] is not None else None


def day_page(analytics, writer, head, cursor, *, page_size, cutoff=None):
    """Nominate one day's bounded source/dependency/field keys, newest first.

    Date predicates here nominate a superset only. Recompute has NO cached
    unit-start lower bound: a nominated conversation is always read in full.
    Committed preimages and fields also visit vanished/moved physical sources.
    Reads never go below the CURRENT retained horizon, so a fresh keyset is
    retained when proposed; days wholly beyond it are complete, not restarted.
    """
    proposed = deepcopy(cursor)
    end = datetime.fromisoformat(cursor["day_end"])
    floor = datetime.fromisoformat(cursor["start"])
    if cutoff is not None:
        floor = max(floor, cutoff)
    if proposed["phase"] != "coverage" and end <= floor:
        # Every remaining day is older than the retained horizon: nothing in it
        # may be nominated, so it is done. Counts stay honest about the window.
        proposed.update(
            phase="coverage",
            after=None,
            done=proposed["total"],
            day_end=cursor["start"],
        )
    start = max(end - timedelta(days=1), floor)
    attempts = [str(a) for _, a in head.allowed_attempts()] if head else []
    params = changes._scope(writer.org_id, writer.lease.project_id, attempts)
    params.update(day_start=start, day_end=end)
    budget = changes.MetricReadBudget()
    keys: set[changes.SourceKey] = set()
    units: set[changes.UnitKey] = set()
    scanned = 0
    keyset_start = None
    while proposed["phase"] != "coverage" and scanned < page_size:
        phase, after = proposed["phase"], proposed["after"]
        remaining = page_size - scanned
        if phase == "sources":
            predicate, bound = _bound_after(
                changes.SOURCE_ORDER, after, "history_after"
            )
            predicate = predicate.replace(
                "%(history_after_2)s", "toDateTime(%(history_after_2)s, 'UTC')"
            )
            query = f"""SELECT observation_type, service_name,
                toStartOfHour(start_time) AS hour, trace_id, id AS span_id
                FROM spans PREWHERE project_id = toUUID(%(project_id)s)
                AND start_time >= toDateTime64(%(day_start)s, 6, 'UTC')
                AND start_time < toDateTime64(%(day_end)s, 6, 'UTC')
                WHERE {predicate} ORDER BY {", ".join(changes.SOURCE_ORDER)}
                LIMIT %(history_limit)s"""
            columns: tuple[str, ...] = changes.SOURCE_ORDER
        else:
            table = SOURCE_DEPENDENCIES if phase == "dependencies" else FIELD_VALUES
            columns = (
                ("trace_id", "span_id") if phase == "dependencies" else ("unit_id",)
            )
            rows = _grouped_committed_page(
                analytics,
                writer,
                head,
                table,
                columns,
                after,
                start,
                end,
                remaining,
                budget,
            )
        if phase == "sources":
            rows = changes.read(
                analytics,
                query,
                {**params, **bound, "history_limit": remaining},
                budget,
            )
        scanned += len(rows)
        if phase in {"sources", "dependencies"}:
            keys.update(
                changes.SourceKey(str(r["trace_id"]), str(r["span_id"])) for r in rows
            )
        else:
            units.update(
                changes.UnitKey("conversation", str(r["unit_id"])) for r in rows
            )
        if len(rows) == remaining:
            last = (
                changes._source_key_values(rows[-1])
                if phase == "sources"
                else tuple(str(rows[-1][c]) for c in columns)
            )
            if after is not None and last <= tuple(after):
                raise changes.MetricChangeReadError("backfill keyset did not advance")
            proposed["after"] = list(last)
            keyset_start = rows[-1].get("keyset_start")
            break
        proposed["after"] = None
        if phase != "fields":
            proposed["phase"] = "dependencies" if phase == "sources" else "fields"
        else:
            proposed["done"] += 1
            proposed["day_end"] = start.isoformat()
            if start <= floor:
                # The retained horizon, not the original window start, bounded
                # this day: every older day is already beyond retention.
                proposed["done"] = proposed["total"]
            proposed["phase"] = (
                "coverage" if proposed["done"] == proposed["total"] else "sources"
            )
            if proposed["phase"] != "coverage" and not keys and not units:
                latest = _older_history_end(
                    analytics, writer, head, floor=floor, end=start, budget=budget
                )
                if latest is None:
                    proposed.update(
                        done=proposed["total"],
                        phase="coverage",
                        day_end=floor.isoformat(),
                    )
                else:
                    # Keep the original daily grid and coverage count. Skipped
                    # ranges provably contain no raw or committed nominees.
                    # day_end is exclusive: a nominee exactly on a daily
                    # boundary belongs to the following page, not a skipped day.
                    skipped = max(0, (start - latest - timedelta(microseconds=1)).days)
                    proposed["done"] += skipped
                    proposed["day_end"] = (start - timedelta(days=skipped)).isoformat()
            break
    batch = changes._batch(
        analytics, tuple(sorted(keys)), params, budget, {}, extra_units=units
    )
    proposed["keyset_start"] = _private_keyset_start(
        batch, proposed["phase"], proposed["after"], fallback=keyset_start
    )
    return batch, proposed


@dataclass(frozen=True)
class VersionCoveragePage:
    changes: changes.ChangeBatch
    checkpoint: ReconciliationCheckpoint
    keyset_start: str | None


def version_coverage_page(
    analytics, writer, head, cursor, *, now, page_size, cutoff=None
):
    """Catch up only the version's approved nomination window.

    Project repair is a separate full-retained-history sweep. These predicates
    nominate owners, never bound their recomputation: a conversation spanning
    the boundary still contributes its complete membership (T6). Nominations
    stay inside the current retained horizon, like the daily pages.
    """
    cp = (
        changes.ReconciliationCheckpoint.from_dict(cursor["coverage"])
        if cursor.get("coverage")
        else changes.ReconciliationCheckpoint(now)
    )
    start, end = datetime.fromisoformat(cursor["start"]), now
    if cutoff is not None:
        start = max(start, cutoff)
    phase, after, day, last_day = cp.phase, cp.after, cp.day, cp.last_day
    if phase == "sources" and day is not None and day < start.date():
        # Whole coverage days already beyond retention are done, not repeated.
        day, after = start.date(), None
    params = changes._scope(
        writer.org_id,
        writer.lease.project_id,
        [str(a) for _, a in head.allowed_attempts()] if head else [],
    )
    params.update(coverage_start=start, coverage_end=end)
    budget = changes.MetricReadBudget()
    keys: set[changes.SourceKey] = set()
    units: set[changes.UnitKey] = set()
    scanned = 0
    keyset_start = None
    if phase == "sources":
        day = day or start.date()
        last_day = last_day or end.date()
        for _ in range(changes.MAX_DAYS_PER_PAGE):
            lower = max(datetime.combine(day, datetime.min.time(), UTC), start)
            upper = min(
                datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC),
                end,
            )
            predicate, bound = _bound_after(
                changes.SOURCE_ORDER, after, "catchup_after"
            )
            predicate = predicate.replace(
                "%(catchup_after_2)s", "toDateTime(%(catchup_after_2)s, 'UTC')"
            )
            remaining = page_size - scanned
            rows = changes.read(
                analytics,
                f"""SELECT observation_type, service_name,
                    toStartOfHour(start_time) AS hour, trace_id, id AS span_id
                    FROM spans PREWHERE project_id = toUUID(%(project_id)s)
                    AND start_time >= toDateTime64(%(coverage_day_start)s, 6, 'UTC')
                    AND start_time < toDateTime64(%(coverage_day_end)s, 6, 'UTC')
                    WHERE {predicate} ORDER BY {", ".join(changes.SOURCE_ORDER)}
                    LIMIT %(coverage_limit)s""",
                {
                    **params,
                    **bound,
                    "coverage_day_start": lower,
                    "coverage_day_end": upper,
                    "coverage_limit": remaining,
                },
                budget,
                settings=changes._block_settings(remaining),
            )
            keys.update(
                changes.SourceKey(str(r["trace_id"]), str(r["span_id"])) for r in rows
            )
            scanned += len(rows)
            if len(rows) == remaining:
                next_after = changes._source_key_values(rows[-1])
                if after is not None and next_after <= tuple(after):
                    raise changes.MetricChangeReadError(
                        "version coverage did not advance"
                    )
                after = next_after
                break
            after = None
            if day >= last_day:
                phase, day, last_day = "dependencies", None, None
                break
            day += timedelta(days=1)
    elif phase in {"dependencies", "units"}:
        table, columns = (
            (SOURCE_DEPENDENCIES, ("trace_id", "span_id"))
            if phase == "dependencies"
            # Lifecycle first_seen is discovery time, not the unit anchor.
            # Bound orphan/vanished-owner nomination by committed fields; source
            # dependencies above independently nominate moved/deleted members.
            else (FIELD_VALUES, ("level", "unit_id"))
        )
        rows = _grouped_committed_page(
            analytics,
            writer,
            head,
            table,
            columns,
            after,
            start,
            end,
            page_size,
            budget,
        )
        scanned = len(rows)
        values = [tuple(str(r[c]) for c in columns) for r in rows]
        if phase == "dependencies":
            keys.update(changes.SourceKey(*v) for v in values)
        else:
            units.update(changes.UnitKey(*v) for v in values)
        if scanned == page_size:
            next_after = values[-1]
            if after is not None and next_after <= tuple(after):
                raise changes.MetricChangeReadError("version coverage did not advance")
            after = next_after
            keyset_start = rows[-1]["keyset_start"]
        else:
            phase, after = ("units" if phase == "dependencies" else "complete"), None
    complete = phase == "complete"
    proposed = changes.ReconciliationCheckpoint(
        started_at=cp.started_at,
        completed_at=now if complete else None,
        phase=phase,
        after=after,
        scanned=cp.scanned + scanned,
        completed_passes=int(complete),
        day=day,
        last_day=last_day,
        covered_through=cp.started_at if complete else None,
    )
    batch = changes._batch(
        analytics, tuple(sorted(keys)), params, budget, {}, extra_units=units
    )
    return VersionCoveragePage(
        batch,
        proposed,
        _private_keyset_start(batch, phase, after, fallback=keyset_start),
    )


def pass_unit_allowance(definitions, state):
    """Conversation units one pass may capture inside its capture budget.

    None when no definition captures conversations. Always at least one
    500-unit batch, so a pass makes progress however many fields there are.
    """
    from tracer.services.derived_metrics.publication import MAX_BATCH_ROWS
    from tracer.services.derived_metrics.refresh import capture_budget

    fields = sum(1 for d in definitions if getattr(d, "level", "") != "span")
    if not fields:
        return None
    return max(1, capture_budget(state) // fields) * MAX_BATCH_ROWS


def _conversation_units(batch):
    return sum(1 for unit in batch.units if unit.level == "conversation")


def merge_changes(*batches):
    """Discovery is conservative; include old AND new memberships from each path."""
    return changes.ChangeBatch(
        tuple(sorted({v for b in batches for v in b.source_keys})),
        tuple(sorted({v for b in batches for v in b.units})),
        tuple(sorted({v for b in batches for v in b.user_ids})),
        tuple({d.key: d for b in batches for d in b.dependencies}.values()),
        tuple({d.key: d for b in batches for d in b.prior_dependencies}.values()),
        {k: v for b in batches for k, v in b.cursor_updates.items()},
    )


def _eval_config_ids(definitions):
    return sorted(
        {
            str(item["config_id"])
            for d in definitions
            for item in d.inputs
            if item.get("source") == "eval" and item.get("config_id")
        }
    )


def incremental_changes(analytics, writer, head, definitions, *, now):
    definitions = list(definitions)
    state = MetricProjectRefresh.for_project(writer.lease.project_id)
    try:
        batch = changes.discover_changes(
            analytics,
            org_id=writer.org_id,
            project_id=writer.lease.project_id,
            committed_attempts=[str(a) for _, a in head.allowed_attempts()]
            if head
            else [],
            watermark_ns=state.watermark_ns,
            eval_watermark_ns=state.eval_watermark_ns,
            remap_cursor_ns=state.remap_cursor_ns,
            ceiling=now,
            eval_config_ids=_eval_config_ids(definitions),
        )
    except changes.arrivals.ContinuousCandidateOverflow:
        raise
    except changes.arrivals.ContinuousCandidateReadError as exc:
        if not changes.memory_limit_exceeded(exc):
            raise
        # Too many changed keys to buffer is the same condition as the key cap:
        # take the durable, paged fallback instead of failing this window on
        # every retry. Discovery ran once; no fallback re-runs it in this pass.
        raise changes.arrivals.ContinuousCandidateOverflow(
            "metric discovery exceeded its memory budget"
        ) from exc
    work_units = len(batch.units)
    if any(getattr(d, "level", None) == "span" for d in definitions):
        # Span refreshes materialize source identities, not just their owners.
        work_units = max(work_units, len(batch.source_keys))
    if work_units > getattr(writer, "incremental_unit_limit", changes.MAX_KEYS):
        # Do not truncate or consume any watermark. Existing overflow handling
        # switches to fenced, durably checkpointed repair pages instead.
        raise changes.arrivals.ContinuousCandidateOverflow(
            "metric native write page exceeds admission cap"
        )
    return batch


def _backfill_intents(control):
    return control.get("backfills", ()) or (
        [control["backfill"]] if control.get("backfill") else ()
    )


def check_backfill_publication(writer, control):
    if (
        control.get("initial_empty_head")
        and MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest_id
        is not None
    ):
        raise MetricStateConflict("initial history proof no longer has an empty parent")
    for intent in _backfill_intents(control):
        _check_backfill_intent(writer, intent)


def _check_backfill_intent(writer, intent):
    definition = DerivedMetricVersion.objects.select_related("metric").get(
        id=intent["version_id"], metric__project_id=writer.lease.project_id
    )
    updated = definition.backfill_updated_at
    if (
        definition.backfill_cursor != intent["expected"]
        or (updated.isoformat() if updated else None)
        != intent.get("expected_updated_at")
        or definition.pins_revoked_at
        or (
            definition.metric.current_version != definition.version
            and has_private_keyset(intent["next"])
        )
    ):
        raise MetricStateConflict("backfill progress changed or expired")


def apply_publication(writer, attempt, control):
    """Called ONLY after validation inside the fenced head-switch transaction."""
    for intent in _backfill_intents(control):
        definition = DerivedMetricVersion.objects.select_related("metric").get(
            id=intent["version_id"], metric__project_id=writer.lease.project_id
        )
        cursor = intent["next"]
        complete = cursor["phase"] == "complete"
        now = writer.lease.clock()
        stored = cursor
        strip, cutoff = publication_horizon(writer, attempt)
        if definition.metric.current_version != definition.version:
            stored = None  # superseded progress is unscheduled; keep no keyset
        elif strip:
            # The horizon may have moved after this page was planned (or this
            # is an expiry checkpoint): keep progress, drop only an aged keyset.
            stored, _ = strip_private_positions(cursor, cutoff=cutoff)
        DerivedMetricVersion.objects.filter(id=definition.id).update(
            backfill_cursor=stored,
            backfill_status="complete" if complete else "running",
            backfill_days_done=cursor["done"],
            backfill_days_total=cursor["total"],
            backfill_updated_at=_progress_time(definition.backfill_updated_at, now),
        )
        # A new edit can supersede a staging draft. Commit its data, but never
        # expose it or clear the newer draft's pending flag.
        if complete and definition.metric.current_version == definition.version:
            DerivedMetric.activate_version(
                definition.metric_id,
                definition.id,
                expected_active_id=cursor["expected_active"],
                now=now,
            )
        MetricProjectRefresh.objects.filter(project_id=writer.lease.project_id).update(
            pending_backfill=pending_versions(writer.lease.project_id).exists(),
            backfill_last_progress_at=now,
        )
    activate_forward_versions(writer, attempt, control)


def activate_forward_versions(writer, attempt, control):
    """Serve forward-only versions in the head switch that first computes them.

    Only a publication that consumed arrivals (its frozen cursors move the
    watermark) proves [coverage_start, its ceiling) was computed; a partial
    sweep page or a compaction does not. Only versions this very attempt was
    prepared with, and that are still the current version of a live metric, are
    activated: anything else is skipped, never raised, because a failure here
    would roll back every publication of the project.
    """
    if "watermark_ns" not in (control.get("cursors") or {}):
        return []
    prepared = {str(item["id"]) for item in attempt.metric_versions}
    activated = []
    for definition in awaiting_forward_versions(writer.lease.project_id).filter(
        id__in=prepared
    ):
        now = writer.lease.clock()
        try:
            with transaction.atomic():
                DerivedMetricVersion.objects.filter(id=definition.id).update(
                    backfill_status=DerivedMetricVersion.BackfillStatus.COMPLETE,
                    backfill_updated_at=_progress_time(
                        definition.backfill_updated_at, now
                    ),
                )
                DerivedMetric.activate_version(
                    definition.metric_id,
                    definition.id,
                    expected_active_id=definition.metric.active_version_id,
                    now=now,
                )
        except (MetricStateConflict, MetricInputError):
            continue
        activated.append(definition.id)
    return activated


def queue_bulk_change(project_id, *, now, burst=None):
    """Reroute large invalidations to a persisted FULL coverage sweep.

    Resetting its cursor is explicit, serialized invalidation, never an implicit
    yesterday-only repair. A publication planned before this reset fails its CAS.
    ``burst`` (a burst ReconciliationCheckpoint) queues that sweep instead.
    """
    with serialized_project(project_id):
        state = MetricProjectRefresh.for_project(project_id)
        MetricProjectRefresh.objects.filter(project_id=project_id).update(
            pending_repair=True,
            reconciliation_cursor=burst.to_dict() if burst else None,
            reconciliation_started_at=(
                burst.started_at
                if burst
                else _progress_time(state.reconciliation_started_at, now)
            ),
            reconciliation_completed_at=burst.completed_at if burst else None,
        )


def _burst_floor(analytics, writer, head, definitions, state, *, now):
    """Where a forward-only burst sweep starts, or None to keep a full sweep."""
    coverage = forward_floor(definitions)
    if coverage is None:
        return None
    try:
        window = changes.arrival_window(
            analytics,
            org_id=writer.org_id,
            project_id=writer.lease.project_id,
            committed_attempts=[str(a) for _, a in head.allowed_attempts()]
            if head
            else [],
            watermark_ns=state.watermark_ns,
            eval_watermark_ns=state.eval_watermark_ns,
            remap_cursor_ns=state.remap_cursor_ns,
            ceiling=now,
            eval_config_ids=_eval_config_ids(definitions),
        )
    except changes.arrivals.ContinuousCandidateReadError:
        # Too many eval/remap keys, a key without a physical row, a memory
        # limit, a timeout or an exhausted budget: the burst cannot be dated,
        # and the full sweep (with its committed phases) still covers it.
        return None
    # Nothing arrived: an empty frozen window completes without visiting history.
    first = changes.utc(now) if window.start_floor is None else window.start_floor
    return changes.ArrivalWindow(
        # A newly changed key can move BEFORE coverage and must still retract
        # its previously eligible value. Version-bounded nomination is safe
        # there: capture/publication still applies coverage and retention.
        first if window.source_floor_ns is not None else max(coverage, first),
        window.source_floor_ns,
    )


def queue_overflow_sweep(analytics, writer, head, definitions, *, now):
    """Send an arrival window too large for discovery to the durable sweep.

    A sweep already pending continues: completing it consumes arrivals up to
    its start, and later arrivals are discovered afterwards. With none pending,
    a project with any history version queues today's full sweep. A project
    whose every working version is forward-only sweeps only the burst instead:
    from the earliest start its unconsumed arrivals touch, source phase only.
    A source-only burst is also version-bounded and may nominate backdated
    corrections before coverage; publication still enforces eligibility. A
    relation-driven burst keeps the coverage floor and older source versions.
    A pending full sweep (a routine repair, which
    is due again after every burst, or an invalidation) is replaced by the
    burst: watermarks have not moved past its arrivals, so the burst covers
    them too, and the burst keeps the last complete pass, so the routine repair
    restarts once the burst completes. Under sustained overflow that routine
    full repair can therefore keep restarting; freshness comes first. If the
    burst cannot be dated, the pending full sweep continues (or one is queued).
    """
    project_id = writer.lease.project_id
    state = MetricProjectRefresh.for_project(project_id)
    current = (
        changes.ReconciliationCheckpoint.from_dict(state.reconciliation_cursor)
        if state.reconciliation_cursor
        else None
    )
    bursting = (
        state.pending_repair
        and current is not None
        and current.floor is not None
        and current.phase != "complete"
    )
    if bursting:
        return
    window = _burst_floor(analytics, writer, head, definitions, state, now=now)
    with transaction.atomic():
        locked = MetricProjectRefresh._locked(project_id, **writer.lease.check())
        if window is None:
            if not locked.pending_repair:
                queue_bulk_change(project_id, now=now)
            return
        sweep_started = _progress_time(state.reconciliation_started_at, now)
        queue_bulk_change(
            project_id,
            now=sweep_started,
            burst=changes.ReconciliationCheckpoint(
                started_at=sweep_started,
                completed_at=current.completed_at if current else None,
                completed_passes=current.completed_passes if current else 0,
                covered_through=current.covered_through if current else None,
                floor=window.start_floor,
                arrival_floor_ns=window.source_floor_ns,
            ),
        )


def check_repair_publication(writer, control):
    intent = control.get("repair")
    if intent:
        state = MetricProjectRefresh.for_project(writer.lease.project_id)
        started = state.reconciliation_started_at
        if (state.reconciliation_cursor, started.isoformat() if started else None) != (
            intent["cursor"],
            intent["started_at"],
        ):
            raise MetricStateConflict("repair coverage was invalidated")


def _retained_repair_checkpoint(cp, *, cutoff):
    """Continue the repair's source days from the retained horizon's day.

    Spans older than the cutoff belong only to expired conversations, so whole
    earlier days are skipped instead of being re-read after every expiry. The
    cutoff day itself is still read in full (reconcile_page has no time bound).
    """
    first = cutoff.date() if cutoff is not None else None
    if first is None or cp is None or cp.phase != "sources" or cp.day is None:
        return cp
    if cp.day >= first:
        return cp
    if cp.last_day < first:
        if cp.floor is not None:
            # A burst has no committed phases. Visit the now-empty retained
            # day so reconcile_page publishes its normal completion/cursors.
            return replace(cp, day=first, last_day=first, after=None)
        return replace(cp, phase="dependencies", after=None, day=None, last_day=None)
    return replace(cp, day=first, after=None)


def _committed_keyset_start(writer, committed, phase, after):
    """Date a committed-table repair keyset through sorting-key point lookups.

    The divergent-only dependency filter and the lifecycle table cannot date
    the boundary key from the page itself. None leaves it conservatively
    undated (expired at the next horizon check).
    """
    if after is None or not committed or phase not in {"dependencies", "units"}:
        return None
    params = {
        "keyset_org": str(writer.org_id),
        "keyset_project": str(writer.lease.project_id),
        "keyset_attempts": tuple(committed),
        "keyset_a": after[0],
        "keyset_b": after[1],
    }
    scope = (
        "org_id = toUUID(%(keyset_org)s) AND project_id = toUUID(%(keyset_project)s)"
    )
    if phase == "dependencies":
        tables: tuple[str, ...] = (SOURCE_DEPENDENCIES.name,)
        key = "trace_id = %(keyset_a)s AND span_id = %(keyset_b)s"
    else:
        versions = tuple(
            (d.metric_id, d.version)
            for d in working_definitions(writer.lease.project_id)
        )
        if not versions:
            return None
        params["keyset_versions"] = versions
        tables = (FIELD_VALUES.name, FIELD_VALUES.name + "_checkpoint")
        key = (
            "(field_id, field_version) IN %(keyset_versions)s "
            "AND level = %(keyset_a)s AND unit_id = %(keyset_b)s"
        )
    starts = []
    for table in tables:
        rows = writer.store.execute(
            f"SELECT min(unit_start), count() FROM {table} PREWHERE {scope} AND {key} "
            "WHERE attempt_id IN %(keyset_attempts)s",
            params,
        )
        if rows and rows[0][1]:
            starts.append(changes.utc(rows[0][0]))
    return min(starts).isoformat() if starts else None


class BackfillRunner:
    """One bounded admission. Fleet admission/fairness remains B10's concern."""

    def __init__(
        self, writer, analytics, scope, *, page_size=PAGE_SIZE, exhaustive_initial=False
    ):
        if not 1 <= page_size <= PAGE_SIZE:
            raise ValueError("invalid backfill page size")
        if (
            str(scope.project) != str(writer.lease.project_id)
            or str(scope.organization) != str(writer.org_id)
            or str(scope.workspace) != str(writer.workspace_id)
        ):
            raise MetricStateConflict("backfill scope mismatch")
        if writer.retention_days is None:
            raise MetricStateConflict("backfill requires current retention policy")
        self.writer, self.analytics, self.scope = writer, analytics, scope
        self.page_size = page_size
        self.exhaustive_initial = exhaustive_initial

    def run_pass(self, *, max_chunks=MAX_CHUNKS_PER_PASS):
        if not 1 <= max_chunks <= MAX_CHUNKS_PER_PASS:
            raise ValueError("backfill pass must contain one to three chunks")
        manifests = []
        started = monotonic()
        for number in range(max_chunks):
            if number and monotonic() - started >= PASS_SOFT_SECONDS:
                # Each published chunk is durable; the next reservation resumes.
                # Starting another page this late would only lengthen this one
                # admission past its liveness budget, never add correctness.
                break
            self.writer.lease.keepalive()
            definition = pending_versions(self.writer.lease.project_id).first()
            if definition is None:
                break
            manifests.append(self.run_chunk(definition))
        return manifests

    def run_chunk(self, definition):
        writer = self.writer
        now = writer.lease.clock()
        cutoff = retained_cutoff(writer, now)
        if cutoff is not None:
            # Plan from already-scrubbed progress: a keyset that aged out since
            # its publication goes now, rather than failing this chunk's CAS.
            scrub_expired_progress(writer.lease.project_id, cutoff=cutoff, now=now)
        # Same-epoch sealed plans resume exact bytes, including progress intent.
        # A takeover is replanned by RefreshWriter from unchanged durable cursors.
        open_attempt = MetricRefreshAttempt.objects.filter(
            project_id=writer.lease.project_id,
            state__in=MetricRefreshAttempt.OPEN_STATES,
            fencing_epoch=writer.lease.grant.fencing_epoch,
            lease_token=writer.lease.grant.lease_token,
            ledger_sealed_at__isnull=False,
            expires_at__gt=now,
        ).first()
        if open_attempt:
            if open_attempt.purpose == "repair":
                # Bulk invalidation may have reset coverage after staging. Repair
                # can safely replan from its persisted cursor/preimages; replaying
                # an invalidated coverage CAS forever cannot make progress.
                return self.run_repair_page(skip_incremental=True)
            controls = [
                entry
                for chunk in open_attempt.ledger_chunks.filter(
                    part="cleanup_intent", payload__0__kind="publication"
                )
                for entry in chunk.payload
                if entry.get("kind") == "publication"
            ]
            resumable = False
            with serialized_project(writer.lease.project_id):
                try:
                    if len(controls) != 1:
                        raise MetricStateConflict("missing backfill publication intent")
                    check_backfill_publication(writer, controls[0])
                    check_retirement_publication(writer, open_attempt, controls[0])
                except MetricStateConflict:
                    # The normal preparation path transfers the original recovery
                    # ledger before aborting, rather than losing dirty preimages.
                    pass
                else:
                    resumable = True
            if resumable:
                return writer.stage_and_publish(open_attempt)
        sweep = sweep_participants(writer.lease.project_id)
        if sweep:
            # One shared initial-history sweep is in progress. Continue it for
            # all of its participants, whichever pending version was picked.
            return self.run_initial_sweep_page(sweep, now=now, cutoff=cutoff)
        definition.refresh_from_db()
        cursor = deepcopy(definition.backfill_cursor) or _initial_cursor(
            definition, now=now, retention_days=writer.retention_days()
        )
        head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
        definitions = working_definitions(
            writer.lease.project_id, pending_versions(writer.lease.project_id)
        )
        if str(definition.id) not in {str(d.id) for d in definitions}:
            raise MetricStateConflict("backfill definition is no longer pending")
        # History publication also serves incremental work. This prevents a bulk
        # backfill from starving arrivals, and closes the final catch-up window.
        # Discover first: an overflowing discovery must not waste a history read.
        overflowed = False
        try:
            incremental = incremental_changes(
                self.analytics, writer, head, definitions, now=now
            )
        except changes.arrivals.ContinuousCandidateOverflow:
            # A large re-evaluation must make bounded progress, not retry the
            # same overflowing arrival interval forever. Do not consume ANY
            # arrival cursor until a complete retained-history pass publishes.
            if not serving_versions(writer.lease.project_id).exists():
                # Initial history: no version serves reads yet. One paged,
                # project-wide sweep IS every pending version's backfill. Its
                # pages publish definition progress and its completion
                # activates them; repair never substitutes for that history.
                return self.run_initial_sweep_page(
                    start_sweep(writer.lease.project_id), now=now, cutoff=cutoff
                )
            # Serving versions exist: arrivals go to the durable repair sweep
            # (other lanes), while THIS lane still publishes bounded history.
            # Repair must not starve a new version's initial history.
            if not MetricProjectRefresh.for_project(
                writer.lease.project_id
            ).pending_repair:
                queue_bulk_change(writer.lease.project_id, now=now)
            incremental = changes.ChangeBatch((), (), (), (), (), {})
            overflowed = True
        # Bound the chunk to what one pass can capture (bug 6): its history
        # page by the allowance, and arrivals too many to fit beside it are
        # left unconsumed for the incremental lane, which pages them.
        allowance = pass_unit_allowance(
            definitions, MetricProjectRefresh.for_project(writer.lease.project_id)
        )
        page_size = self.page_size
        if allowance is not None:
            page_size = max(1, min(page_size, allowance))
            if _conversation_units(incremental) > allowance - page_size and (
                serving_versions(writer.lease.project_id).exists()
            ):
                incremental = changes.ChangeBatch((), (), (), (), (), {})
        if cursor["phase"] == "coverage":
            coverage = version_coverage_page(
                self.analytics,
                writer,
                head,
                cursor,
                now=now,
                page_size=page_size,
                cutoff=cutoff,
            )
            batch = replace(coverage.changes, cursor_updates={})
            proposed = {
                **cursor,
                "coverage": {
                    **coverage.checkpoint.to_dict(),
                    "keyset_start": getattr(coverage, "keyset_start", None),
                },
            }
            if coverage.checkpoint.phase == "complete":
                changes.require_initial_dependencies(coverage.checkpoint)
                proposed["phase"] = "complete"
        else:
            batch, proposed = day_page(
                self.analytics,
                writer,
                head,
                cursor,
                page_size=page_size,
                cutoff=cutoff,
            )
        batch = merge_changes(batch, incremental)
        initial_publication = (
            None
            if overflowed
            else self._initial_publication(definitions, head, incremental, now=now)
        )
        return writer.refresh(
            batch,
            definitions,
            self.scope,
            start=datetime.fromisoformat(cursor["start"]),
            end=now,
            as_of=now,
            purpose="backfill",
            publication=initial_publication
            or {
                "backfill": {
                    "version_id": str(definition.id),
                    "expected": definition.backfill_cursor,
                    "expected_updated_at": definition.backfill_updated_at.isoformat()
                    if definition.backfill_updated_at
                    else None,
                    "next": proposed,
                }
            },
        )

    def _initial_publication(self, definitions, head, incremental, *, now):
        """A complete Epoch-to-now arrival scan is already initial coverage.

        Only a brand-new, empty project can use this proof. There are no committed
        vanished dependencies/fields to reconcile, and zero watermarks mean the
        bounded discovery just exhaustively visited source/eval/remap history.
        Overflow goes to the existing paged repair path, never this shortcut.
        All pending recipes are recomputed by the SAME frozen publication; every
        activation still validates its original progress/active-version CAS.
        """
        if not self.exhaustive_initial or head is not None or not definitions:
            return None
        state = MetricProjectRefresh.for_project(self.writer.lease.project_id)
        if state.head_manifest_id is not None or any(
            getattr(state, field) != 0 for field in MetricProjectRefresh.MONOTONE_FIELDS
        ):
            return None
        if any(
            d.backfill_status != "pending"
            or d.backfill_cursor is not None
            or d.backfill_updated_at is not None
            or d.metric.active_version_id is not None
            for d in definitions
        ):
            return None
        coverage = changes.ReconciliationCheckpoint(
            started_at=now,
            completed_at=now,
            phase="complete",
            scanned=len(incremental.source_keys),
            completed_passes=1,
            covered_through=now,
        )
        intents = []
        for definition in definitions:
            cursor = _initial_cursor(
                definition, now=now, retention_days=self.writer.retention_days()
            )
            cursor.update(
                phase="complete",
                done=cursor["total"],
                day_end=cursor["start"],
                coverage=coverage.to_dict(),
            )
            intents.append(
                {
                    "version_id": str(definition.id),
                    "expected": None,
                    "expected_updated_at": None,
                    "next": cursor,
                }
            )
        return {"initial_empty_head": True, "backfills": intents}

    def run_initial_sweep_page(self, sweep, *, now, cutoff):
        """One bounded page of the paged form of the exhaustive initial proof.

        While no version serves reads, every pending version is a working
        definition of EVERY publication (pending_backfill stays set), so a full
        retained-history pass visiting every physical source key is initial
        history for all participants. Each page publishes their progress in
        their own backfill cursors (CAS on each); the completing page activates
        all of them in the same head-switch transaction and consumes arrival
        cursors only up to the pass START, like a complete repair. A pass that
        began on an empty head has no older committed rows to revisit, so its
        source phase alone is complete. The project's repair cursor is untouched.
        """
        writer = self.writer
        project_id = writer.lease.project_id
        participants, cursor = sweep
        if not participants:
            raise MetricStateConflict("initial sweep has no pending participant")
        state = MetricProjectRefresh.for_project(project_id)
        head = state.head_manifest
        if cursor is None:
            cursor = _initial_cursor(
                participants[0], now=now, retention_days=writer.retention_days()
            )
            cursor.update(
                phase=SWEEP_PHASE,
                day_end=cursor["start"],
                sweep={"id": now.isoformat(), "empty_head": head is None},
            )
        committed = [str(a) for _, a in head.allowed_attempts()] if head else []
        # A new pass starts at this page's `now` (its id), like a first repair.
        checkpoint = (
            changes.ReconciliationCheckpoint.from_dict(cursor["coverage"])
            if cursor.get("coverage")
            else None
        )
        page = changes.reconcile_page(
            self.analytics,
            org_id=writer.org_id,
            project_id=project_id,
            committed_attempts=committed,
            now=now,
            checkpoint=_retained_repair_checkpoint(checkpoint, cutoff=cutoff),
            page_size=self.page_size,
            # The history this sweep promises is the persisted bounded window
            # (the same one the size gate counted and reads treat as
            # calculated), never every retained day of the project.
            floor=_moment(cursor["start"]),
        )
        proposed = page.checkpoint
        if cursor["sweep"]["empty_head"] and proposed.phase in {
            "dependencies",
            "units",
        }:
            # Every committed dependency/lifecycle row was written by this very
            # pass (or later arrivals beyond its start ceiling): nothing older
            # can be vanished or divergent, so the source phase is the proof.
            proposed = replace(
                proposed,
                phase="complete",
                after=None,
                completed_at=now,
                completed_passes=proposed.completed_passes + 1,
                covered_through=proposed.started_at,
            )
        complete = proposed.phase == "complete"
        # Never the project's repair cursor/pending flag: those stay owned by
        # repair. Arrivals are consumed only by the completing page.
        batch = replace(page.changes, cursor_updates={})
        if complete:
            ceiling = changes.ns(proposed.started_at)
            batch = replace(
                batch,
                cursor_updates={
                    field: max(getattr(state, field), ceiling)
                    for field in MetricProjectRefresh.MONOTONE_FIELDS
                },
            )
        coverage = {
            **proposed.to_dict(),
            "keyset_start": _private_keyset_start(batch, proposed.phase, proposed.after)
            or _committed_keyset_start(
                writer, committed, proposed.phase, proposed.after
            ),
        }
        done = _sweep_done(cursor, proposed)
        intents = []
        for definition in participants:
            nxt = {
                **cursor,
                "coverage": coverage,
                "done": done,
                "expected_active": str(definition.metric.active_version_id)
                if definition.metric.active_version_id
                else None,
            }
            if complete:
                nxt.update(phase="complete", day_end=cursor["start"])
            intents.append(
                {
                    "version_id": str(definition.id),
                    "expected": definition.backfill_cursor,
                    "expected_updated_at": definition.backfill_updated_at.isoformat()
                    if definition.backfill_updated_at
                    else None,
                    "next": nxt,
                }
            )
        return writer.refresh(
            batch,
            working_definitions(project_id, pending_versions(project_id)),
            self.scope,
            start=changes.EPOCH,
            end=now,
            as_of=now,
            purpose="backfill",
            publication={"backfills": intents},
        )

    def run_repair_pages(self, *, skip_incremental=False):
        """Repair pages: one page, then more of a burst sweep within the budget.

        Each page is its own publication, so progress is durable page by page
        and a pass stopped by its deadline keeps every page it published. Only
        a forward-only burst sweep continues: a full repair stays one page per
        pass, as before. Later pages skip discovery (the first page did it; new
        arrivals are the next pass's).
        """
        from tracer.models.derived_metric import MAX_MANIFEST_DELTAS

        started = monotonic()
        head = self.run_repair_page(skip_incremental=skip_incremental)
        # Compact at most once per pass, not per page: only the first page
        # uses the configured threshold; later pages publish deltas on top of
        # it until the chain's own limit (10M: the checkpoint rewrite grew from
        # 1.4 s to 6.4 s per page).
        previous = getattr(self.writer, "deltas_before_compaction", None)
        self.writer.deltas_before_compaction = MAX_MANIFEST_DELTAS - 1
        try:
            for _ in range(min(burst_max_pages(), MAX_MANIFEST_DELTAS - 1) - 1):
                if monotonic() - started >= burst_pass_seconds():
                    break
                if not burst_pending(self.writer.lease.project_id):
                    break
                self.writer.lease.check()
                head = self.run_repair_page(skip_incremental=True)
        finally:
            if previous is None:
                del self.writer.deltas_before_compaction
            else:
                self.writer.deltas_before_compaction = previous
        return head

    def run_repair_page(self, *, skip_incremental=False):
        writer = self.writer
        now = writer.lease.clock()
        cutoff = retained_cutoff(writer, now)
        if cutoff is not None:
            scrub_expired_progress(writer.lease.project_id, cutoff=cutoff, now=now)
        state = MetricProjectRefresh.for_project(writer.lease.project_id)
        head = state.head_manifest
        committed = [str(a) for _, a in head.allowed_attempts()] if head else []
        definitions = working_definitions(writer.lease.project_id)
        # Bound normal source repair to the earliest forward coverage. Overflow
        # burst cursors carry a narrower floor and reconcile_page skips their
        # committed phases.
        floor = forward_floor(definitions)
        incremental = changes.ChangeBatch((), (), (), (), (), {})
        discovered = skip_incremental
        if floor is not None and not skip_incremental:
            # A forward-only project discovers first: an overflow then sweeps
            # only the burst, instead of a page of a sweep since enrollment.
            discovered = True
            try:
                incremental = incremental_changes(
                    self.analytics, writer, head, definitions, now=now
                )
            except changes.arrivals.ContinuousCandidateOverflow:
                queue_overflow_sweep(self.analytics, writer, head, definitions, now=now)
                state = MetricProjectRefresh.for_project(writer.lease.project_id)
                skip_incremental = True
        # A burst page is sized by METRICS_BURST_PAGE_KEYS unless the runner
        # was given a smaller page explicitly.
        page_size = (
            burst_page_keys()
            if self.page_size == PAGE_SIZE
            and (state.reconciliation_cursor or {}).get("floor")
            else self.page_size
        )
        allowance = pass_unit_allowance(definitions, state)

        def plan(size):
            checkpoint = (
                changes.ReconciliationCheckpoint.from_dict(state.reconciliation_cursor)
                if state.reconciliation_cursor
                else None
            )
            nomination_floor = (
                None
                if checkpoint and checkpoint.arrival_floor_ns is not None
                else floor
            )
            return changes.reconcile_page(
                self.analytics,
                org_id=writer.org_id,
                project_id=writer.lease.project_id,
                committed_attempts=committed,
                now=now,
                checkpoint=_retained_repair_checkpoint(
                    checkpoint,
                    cutoff=max(v for v in (cutoff, nomination_floor) if v is not None)
                    if cutoff or nomination_floor
                    else None,
                ),
                page_size=size,
                floor=floor,
            )

        page = plan(page_size)
        if not discovered:
            try:
                incremental = incremental_changes(
                    self.analytics, writer, head, definitions, now=now
                )
            except changes.arrivals.ContinuousCandidateOverflow:
                skip_incremental = True
        batch = merge_changes(page.changes, incremental)
        # Bound the page to what one pass can capture (bug 6: a pass over its
        # lease published nothing and the next pass repeated it). Arrivals
        # discovered here are left for a later pass first, then the page
        # halves; a one-key page always runs.
        while allowance is not None and _conversation_units(batch) > allowance:
            if incremental.units or incremental.source_keys:
                incremental = changes.ChangeBatch((), (), (), (), (), {})
                skip_incremental = True
            elif page_size > 1:
                page_size = max(1, page_size // 2)
                page = plan(page_size)
            else:
                break
            batch = merge_changes(page.changes, incremental)
        if batch.cursor_updates.get("reconciliation_cursor") is not None:
            # Date the private keyset, so expiry drops it only once its own
            # source ages out. Policy metadata is nonidentifying audit context.
            batch = replace(
                batch,
                cursor_updates={
                    **batch.cursor_updates,
                    "reconciliation_cursor": {
                        **batch.cursor_updates["reconciliation_cursor"],
                        "retention_days": writer.retention_days(),
                        "keyset_start": _private_keyset_start(
                            batch, page.checkpoint.phase, page.checkpoint.after
                        )
                        or _committed_keyset_start(
                            writer,
                            committed,
                            page.checkpoint.phase,
                            page.checkpoint.after,
                        ),
                    },
                },
            )
        if skip_incremental and page.checkpoint.phase == "complete":
            # Every retained key/owner/eval was recomputed. Consume only the
            # sweep START ceiling, never arrivals made while coverage ran.
            ceiling = changes.ns(page.checkpoint.started_at)
            batch = replace(
                batch,
                cursor_updates={
                    **batch.cursor_updates,
                    **{
                        field: max(getattr(state, field), ceiling)
                        for field in MetricProjectRefresh.MONOTONE_FIELDS
                    },
                },
            )
        return writer.refresh(
            batch,
            definitions,
            self.scope,
            start=changes.EPOCH if floor is None else floor,
            end=now,
            as_of=now,
            purpose="repair",
            publication={
                "repair": {
                    "cursor": state.reconciliation_cursor,
                    "started_at": state.reconciliation_started_at.isoformat()
                    if state.reconciliation_started_at
                    else None,
                }
            },
        )
