import copy
import json
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests
import structlog
from django.db.models import Q
from django.utils import timezone

from accounts.models.organization import Organization
from tfc.temporal import temporal_activity
from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import ProjectSourceChoices
from tracer.models.trace import Trace
from tracer.serializers.observability_provider import ObservabilityProviderSerializer
from tracer.services.observability_providers import (
    RETELL_LIST_PAGE_LIMIT,
    ObservabilityService,
    RetellConfigurationError,
    RetellCursorRejected,
)
from tracer.utils.bland import normalize_bland_data
from tracer.utils.eleven_labs import normalize_eleven_labs_data
from tracer.utils.otel import ResourceLimitError, get_or_create_project
from tracer.utils.retell import normalize_retell_data
from tracer.utils.twilio_calls import normalize_twilio_data
from tracer.utils.usage_emit import emit_span_ingestion_usage
from tracer.utils.vapi import normalize_vapi_data

logger = structlog.get_logger(__name__)

RETELL_VISIBILITY_LAG = timedelta(seconds=60)
RETELL_FUTURE_WATERMARK_LOOKBACK = timedelta(hours=1)
RETELL_MIN_WINDOW = timedelta(seconds=1)
RETELL_WINDOW_HINT_MAX = timedelta(hours=6)
RETELL_WINDOW_GROW_AFTER = 3
RETELL_DIGEST_HISTORY = 8
RETELL_MAX_PAGES_PER_WINDOW = 50
RETELL_BOOTSTRAP_MAX_PAGES = 10
RETELL_BEHIND_WARN = timedelta(minutes=20)
RETELL_BEHIND_ERROR = timedelta(hours=6)
RETELL_BACKOFF_BASE = timedelta(minutes=10)
RETELL_BACKOFF_MAX = timedelta(hours=6)
RETELL_MAX_BACKOFF_EXPONENT = 6
RETELL_MAX_FAILED_RUNS = 3
RETELL_MAX_WINDOW_RESTARTS = 3
RETELL_MANUAL_RUN_MAX_PAGES = 5
RETELL_RUN_BUDGET = timedelta(minutes=20)
# v1.15 D. The deadline is honoured inside hydration and between stored calls,
# so an activity overruns it by roughly: the detail requests in flight when it
# passes (they run concurrently, so ~ one request of 3 x 30s + 2 x 30.5s, i.e.
# ~2.5 min), plus one list request, plus the one call still being stored. That
# last term is an estimate and NOT a ceiling: the recording download's 200s in
# the shared converter is a per-read timeout, so a server that trickles bytes
# is bounded only by the audio size cap, and the S3 upload names no timeout at
# all. Bounding a download belongs in that converter (with the async rehost)
# and is a follow-up. Only the first two terms are enforced; they cost ~5 min,
# so the 3h time_limit leaves that unbounded one most of an hour to finish in.
RETELL_ACTIVITY_BUDGET = timedelta(hours=2)


@temporal_activity(
    max_retries=0,
    time_limit=3600 * 3,
    queue="tasks_s",
)
def fetch_observability_logs(
    start_time: str | None = None,  # ISO format string
    end_time: str | None = None,  # ISO format string
    provider_id: str | None = None,
) -> None:
    """
    Fetches observability logs.

    A scheduled firing (no bounds) polls every enabled provider, or just
    ``provider_id`` when one is given. Any bound given makes this a manual,
    single-provider run that requires ``provider_id`` and ignores ``enabled``.
    """
    scheduled = start_time is None and end_time is None

    start_dt = None
    if start_time is not None:
        start_dt = datetime.fromisoformat(start_time)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=UTC)
    end_dt = None
    if end_time is not None:
        end_dt = datetime.fromisoformat(end_time)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=UTC)

    # F1/N1: one activity-level deadline, computed once, threaded down to
    # every Retell provider's run loop so the budget bounds the whole
    # activity (and its 3h time_limit) rather than resetting per provider.
    activity_started = timezone.now()
    deadline = activity_started + RETELL_ACTIVITY_BUDGET

    if scheduled:
        if provider_id is not None:
            provider_ids = [
                provider_id
            ]  # single-provider dispatch: unchanged, no shuffle
        else:
            provider_ids = list(
                ObservabilityProvider.objects.filter(enabled=True).values_list(
                    "id", flat=True
                )
            )
            random.shuffle(
                provider_ids
            )  # F1: a starved tail rotates between firings instead of always being last
    else:
        if not provider_id:
            logger.error("provider_manual_run_rejected", reason="provider_id_required")
            return
        provider_ids = [provider_id]

    success_count = 0
    failure_count = 0
    skipped_providers = 0

    for pid in provider_ids:
        if scheduled and timezone.now() >= deadline:
            skipped_providers += 1
            continue
        try:
            result = fetch_logs_for_provider(
                pid,
                scheduled=scheduled,
                start_time=start_dt,
                end_time=end_dt,
                deadline=deadline,
            )
        except Exception as exc:
            failure_count += 1
            logger.error(
                "provider_log_fetch_failed",
                provider_id=str(pid),
                error_type=type(exc).__name__,
            )
            continue
        if result is not None:
            success_count += 1
        else:
            failure_count += 1

    if skipped_providers:
        logger.warning(
            "retell_activity_budget_exhausted", skipped_providers=skipped_providers
        )

    logger.info(
        "Completed fetching observability logs",
        success_count=success_count,
        failure_count=failure_count,
    )


@dataclass(frozen=True)
class StoreOutcome:
    stored: int  # spans the collector's gRPC Export RPC acknowledged
    malformed: int  # per-call permanent failures — counted, never retried
    export_failed: int  # spans built but not acknowledged


def fetch_logs_for_provider(
    provider_id,
    *,
    scheduled: bool,
    start_time: datetime | None,
    end_time: datetime | None,
    deadline: datetime | None = None,
) -> StoreOutcome | None:
    try:
        provider = ObservabilityProvider.objects.get(id=provider_id)
    except ObservabilityProvider.DoesNotExist:
        if scheduled:
            logger.error(
                "provider_log_fetch_failed",
                provider_id=str(provider_id),
                error_type="DoesNotExist",
            )
        else:
            logger.error(
                "provider_manual_run_rejected",
                provider_id=str(provider_id),
                reason="not_found",
            )
        return None
    try:
        if provider.provider == ProviderChoices.RETELL:
            return (
                _poll_retell_provider(provider, deadline=deadline)
                if scheduled
                else _manual_retell_run(
                    provider, start_time=start_time, end_time=end_time
                )
            )
        # F1: the activity deadline only bounds Retell's own page loop; other
        # providers' single-request polls ignore it.
        return _poll_other_provider(provider, start_time=start_time, end_time=end_time)
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if provider.provider == ProviderChoices.RETELL and status in (401, 403):
            logger.error(
                "retell_auth_failed", provider_id=str(provider_id), status_code=status
            )
        elif provider.provider != ProviderChoices.RETELL and status in (401, 403):
            logger.error(
                "authentication_failed_for_provider",
                provider_type=provider.provider,
                status_code=status,
            )
        else:
            logger.error(
                "provider_log_fetch_failed",
                provider_id=str(provider_id),
                provider_type=provider.provider,
                status_code=status,
                error_type=type(exc).__name__,
            )
        return None
    except RetellConfigurationError as exc:
        logger.error(
            "retell_configuration_error",
            provider_id=str(provider_id),
            error_type=type(exc).__name__,
        )
        return None
    except Exception as exc:
        logger.error(
            "provider_log_fetch_failed",
            provider_id=str(provider_id),
            provider_type=provider.provider,
            error_type=type(exc).__name__,
        )
        return None


def _read_retell_state(provider) -> dict:
    raw = provider.poll_state if isinstance(provider.poll_state, dict) else {}
    retell = raw.get("retell")
    return copy.deepcopy(retell) if isinstance(retell, dict) else {}


def _write_retell_state(provider_id, retell_state: dict) -> bool:
    row = (
        ObservabilityProvider.all_objects.filter(id=provider_id)
        .values_list("poll_state", flat=True)
        .first()
    )
    merged = dict(row) if isinstance(row, dict) else {}
    merged["retell"] = retell_state
    n = ObservabilityProvider.all_objects.filter(id=provider_id).update(
        poll_state=merged
    )
    if n == 0:
        logger.error("provider_poll_state_write_skipped", provider_id=str(provider_id))
    return n == 1


def _advance_watermark(provider_id, new: datetime) -> int:  # monotonic
    n = ObservabilityProvider.all_objects.filter(
        Q(last_fetched_at__lt=new) | Q(last_fetched_at__isnull=True), id=provider_id
    ).update(last_fetched_at=new)
    if n == 0:
        logger.warning(
            "provider_watermark_write_skipped",
            provider_id=str(provider_id),
            attempted=new.isoformat(),
        )
    return n


def _repair_future_watermark(
    provider_id, now: datetime, new: datetime
) -> int:  # the single exception to monotonicity
    n = ObservabilityProvider.all_objects.filter(
        id=provider_id, last_fetched_at__gt=now
    ).update(last_fetched_at=new)
    if n:
        logger.warning(
            "provider_watermark_repaired",
            provider_id=str(provider_id),
            new=new.isoformat(),
        )
    return n


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(
        iso
    )  # values were written by .isoformat() on aware UTC datetimes


def _backoff_delay(total_failures: int) -> timedelta:
    return min(
        RETELL_BACKOFF_BASE
        * (2 ** min(total_failures - 1, RETELL_MAX_BACKOFF_EXPONENT)),
        RETELL_BACKOFF_MAX,
    )


def _hint_for(state) -> timedelta:
    return timedelta(
        seconds=state.get(
            "window_hint_seconds", int(RETELL_WINDOW_HINT_MAX.total_seconds())
        )
    )


_WINDOW_TYPES = {
    "start": (str, type(None)),  # None = bootstrap window
    "end": str,
    "opened_at_hint": bool,
    "narrowed": bool,
    "key": (str, type(None)),
    "skip": (int, type(None)),
    "pages_stored": int,
    "digests": list,
    "restarts": int,
    # v1.15 B1: how far into the CURRENT page this window has got, so a run
    # that stops mid-page resumes there instead of re-storing it.
    "progress": int,
    "page_digest": (str, type(None)),
    # [stored, malformed, export_failed, dropped_no_end, dropped_missing,
    # dropped_failed] for the current page, accumulated across runs
    "page_counts": list,
}
_PAGE_COUNT_SLOTS = 6


def _reset_page_progress(window) -> None:
    """v1.15 B1: the three page-progress keys only ever mean something about
    the page the window is pointing AT, so they are cleared together — on every
    cursor/offset advance, every restart, and every retry of the same page."""
    window["progress"] = 0
    window["page_digest"] = None
    window["page_counts"] = [0] * _PAGE_COUNT_SLOTS


def _valid_window(window) -> bool:
    if not isinstance(window, dict):
        return False
    try:
        if not all(isinstance(window[k], t) for k, t in _WINDOW_TYPES.items()):
            return False
        counts = window["page_counts"]
        # The only key whose type does not pin its shape, and the only one the
        # store loop indexes: a wrong-length list would raise on every run for
        # this provider for ever, where a discarded window just starts over.
        if len(counts) != _PAGE_COUNT_SLOTS or not all(
            isinstance(slot, int) for slot in counts
        ):
            return False
        # Same exposure: `progress` is a position in the listed page, so a
        # negative one would ask the fetcher to hydrate from behind the page's
        # end on every run until a human noticed.
        if window["progress"] < 0:
            return False
        if window["start"] is not None and _parse(window["start"]).tzinfo is None:
            return False  # bootstrap window: "start" is None, nothing to parse
        return _parse(window["end"]).tzinfo is not None
    except (KeyError, ValueError, TypeError):
        return False


def _new_window(
    start: datetime | None, end: datetime, *, opened_at_hint: bool, narrowed: bool
) -> dict:
    """F6/N6: the one place that builds a fresh window dict — bootstrap
    (``start=None``) and ordinary windows differ only in ``start``/``end``/
    ``opened_at_hint``; every other key is a shared, unconditional default."""
    return {
        "start": None if start is None else start.isoformat(),
        "end": end.isoformat(),
        "opened_at_hint": opened_at_hint,
        "narrowed": narrowed,
        "key": None,
        "skip": None,
        "pages_stored": 0,
        "digests": [],
        "restarts": 0,
        "progress": 0,
        "page_digest": None,
        "page_counts": [0] * _PAGE_COUNT_SLOTS,
    }


def _classify(counts) -> str:  # "ok" | "total" | "partial"
    """Verdict on a COMPLETE page, from its accumulated `page_counts`. It reads
    the window's slots rather than the last response because a page stored over
    several runs has its drops spread across all of them; the last response
    only covers the slice it hydrated."""
    stored, malformed, export_failed = counts[:3]
    problems = export_failed + counts[5]  # infra-shaped failures (retryable)
    if problems == 0:
        return "ok"  # malformed-only pages are "ok": permanent, counted, never retried
    if stored == 0 and problems >= malformed:
        return "total"  # nothing worked, mostly for infra reasons: wait with backoff
    return "partial"  # retry the same page up to RETELL_MAX_FAILED_RUNS, then abandon the problems


def _poll_retell_provider(
    provider, *, deadline: datetime | None = None
) -> StoreOutcome | None:
    # Every returned outcome is the CURRENT page's counts so far — one meaning
    # for the checkpoint and the completion paths, because the fan-out reads
    # only None (this run failed) versus not None.
    provider_id = provider.id
    now = timezone.now()
    run_started = now  # same call as `now`: F2's per-run budget is measured from here
    # F1: an activity-level deadline (threaded from the scheduled fan-out) caps
    # the run budget from outside; None (manual runs, direct callers) leaves
    # only the run budget in effect.
    run_deadline = run_started + RETELL_RUN_BUDGET
    if deadline is not None:
        run_deadline = min(run_deadline, deadline)
    end = now - RETELL_VISIBILITY_LAG
    state = _read_retell_state(provider)

    try:
        backoff_until = (
            _parse(state["backoff_until"])
            if isinstance(state.get("backoff_until"), str)
            else None
        )
    except ValueError:
        backoff_until = None
    if backoff_until is not None and backoff_until > now:
        logger.warning(
            "retell_poll_backoff",
            provider_id=str(provider_id),
            until=state["backoff_until"],
        )
        return StoreOutcome(0, 0, 0)

    if not state.get(
        "bootstrapped"
    ):  # first run under this code, whatever last_fetched_at says (D10)
        window = state.get("window")
        # v1.13 §7: a stale window is discarded when bootstrapped is falsy —
        # UNLESS it is itself a bootstrap window (start is None), which is
        # resumed rather than restarted from scratch.
        if not _valid_window(window) or window["start"] is not None:
            window = (
                None  # anything malformed, or a stale ordinary window, is discarded
            )
        if window is None:
            # A bootstrap window pages the same as an ordinary one, just with
            # "start": None; "end" is frozen here and reused by every later
            # page of this bootstrap, however many runs it takes.
            window = _new_window(None, end, opened_at_hint=False, narrowed=False)
            state["window"] = window
    else:
        wm = provider.last_fetched_at
        if wm is None:  # cannot happen after a successful bootstrap; treat like poison
            logger.warning("provider_watermark_missing", provider_id=str(provider_id))
            start = end - RETELL_FUTURE_WATERMARK_LOOKBACK
        elif wm > now:
            logger.warning("provider_watermark_in_future", provider_id=str(provider_id))
            start = end - RETELL_FUTURE_WATERMARK_LOOKBACK
            _repair_future_watermark(provider_id, now, start)
        else:
            start = wm

        window = state.get("window")
        if not _valid_window(window):
            window = None  # anything malformed is treated as "no window in progress"
        if window is None:
            if start >= end:
                return StoreOutcome(0, 0, 0)
            hint = _hint_for(state)
            opened_at_hint = (end - start) >= hint
            end = min(end, start + hint)  # a new window is never wider than the hint
            window = _new_window(
                start, end, opened_at_hint=opened_at_hint, narrowed=False
            )
            state["window"] = window

    bootstrap = window["start"] is None
    window_start = None if bootstrap else _parse(window["start"])
    window_end = _parse(window["end"])

    while True:
        try:
            page = ObservabilityService.fetch_retell_page(
                provider,
                window_start,
                window_end,
                pagination_key=window["key"],
                skip=window["skip"],
                resume_from=window["progress"],
                resume_digest=window["page_digest"],
                deadline=run_deadline,
            )
        except RetellCursorRejected as exc:
            return _restart_window(provider_id, state, cause=exc.cause)
        # An empty listing is a paging fault, not page instability: judge it
        # before the digest comparison so it is never scored as a mismatch. A
        # resumed first page has no completed page yet but does have progress;
        # it is just as much "already paging" as a completed one.
        if (
            (window["pages_stored"] > 0 or window["progress"] > 0)
            and page.has_more
            and page.digest is None
        ):
            return _restart_window(provider_id, state, cause="empty_page")
        # v1.15 B3: a resumed page that no longer hashes the same is a
        # different page — the fetcher already re-hydrated it from 0, so drop
        # the progress and re-store it (re-emits are idempotent: deterministic
        # trace ids, collector versions by receive time, usage metered once).
        page_changed = window["progress"] > 0 and page.digest != window["page_digest"]
        if page_changed:
            state["page_changed"] = state.get("page_changed", 0) + 1
            if state["page_changed"] >= RETELL_MAX_FAILED_RUNS:
                # A page that keeps coming back re-ordered, on a window whose
                # stores need more than one run, re-stores its prefix and
                # checkpoints again for ever: the cursor never moves, so the
                # frontier never ages into `retell_poll_behind` either and the
                # only signal is one warning a run. The count is per page (a
                # completed page clears it), so the third mismatch on one page
                # is treated like an outage — stop spinning the provider, and
                # say so where an operator is already listening.
                state["total_failures"] = state.get("total_failures", 0) + 1
                state["backoff_until"] = (
                    timezone.now() + _backoff_delay(state["total_failures"])
                ).isoformat()
                logger.error(
                    "retell_page_unstable",
                    provider_id=str(provider_id),
                    page_changed=state["page_changed"],
                    backoff_until=state["backoff_until"],
                )
                _reset_page_progress(window)
                _write_retell_state(provider_id, state)
                return None
            logger.warning(
                "retell_page_changed",
                provider_id=str(provider_id),
                progress=window["progress"],
            )
            _reset_page_progress(window)
        if page.digest is not None and page.digest in window["digests"]:
            return _restart_window(provider_id, state, cause="page_repeated")
        if (
            not bootstrap
            and page.has_more
            and window["pages_stored"] + 1 >= RETELL_MAX_PAGES_PER_WINDOW
        ):  # a window that needs more than 50 pages is not being paged honestly: restart → narrow
            return _restart_window(provider_id, state, cause="page_cap")

        # v1.15 B5: store call by call so the deadline can be honoured INSIDE a
        # page. `counts` accumulates the page's whole fate in the window across
        # runs — the drops as well as the stores, because a resumed response
        # describes only the slice it hydrated, so drops decided before a
        # checkpoint are in no later response and a lossy page would otherwise
        # complete as "ok".
        counts = window["page_counts"]
        checkpoint = False
        for index, call in zip(page.indices, page.calls, strict=True):
            o = process_and_store_logs([call], provider)
            counts[0] += o.stored
            counts[1] += o.malformed
            counts[2] += o.export_failed
            window["progress"] = index + 1
            if timezone.now() >= run_deadline and index + 1 < page.listed:
                checkpoint = True
                break
        if not checkpoint:
            # Trailing drops are consumed too; a short `consumed` means
            # hydration itself stopped at the deadline (v1.15 A3).
            window["progress"] = page.consumed
            checkpoint = page.consumed < page.listed
        # The drops go in only now that `progress` is final, and only the ones
        # it covers: the next run re-lists from `progress` and the fetcher
        # decides the fate of every item after it again, so a drop counted here
        # while sitting past the checkpoint would be counted a second time then
        # — enough to read a merely lossy page as a total outage and back the
        # provider off for hours.
        for index, slot in page.drops:
            if index < window["progress"]:
                counts[slot] += 1
        if checkpoint:
            if (
                window["progress"] == 0
                and window["page_digest"] is None
                and not page_changed
            ):
                # The deadline went during the list request: nothing hydrated,
                # nothing stored, no page identity to remember. A checkpoint
                # here would persist state and announce progress for a run that
                # learned nothing, and the next run starts from 0 regardless.
                # A mismatch IS something learned: its count must reach the
                # store, or an unstable page polled on a spent deadline could
                # never reach the backoff threshold.
                return StoreOutcome(0, 0, 0)
            # Whatever this response hydrated past `progress` is discarded with
            # it, so those detail requests are made again next run — at most one
            # page of Get Calls. Paying that keeps the checkpoint at the call the
            # deadline actually landed on, instead of storing on to `consumed`
            # and blowing the very bound the checkpoint exists to hold.
            window["page_digest"] = page.digest
            if not _write_retell_state(provider_id, state):
                return None
            logger.info(
                "retell_page_checkpointed",
                provider_id=str(provider_id),
                progress=window["progress"],
                listed=page.listed,
            )
            # A slow page is the one case where the frontier ages while the
            # cursor stands still, so the behind/stalled alarm must fire from
            # here too, not only on a cursor advance. Gated on the warn
            # threshold: a checkpoint forced by the shared activity deadline
            # can land on a frontier only a minute old, and that is not news.
            behind_now = timezone.now()
            if not bootstrap and (behind_now - window_end) > RETELL_BEHIND_WARN:
                _log_behind(provider_id, behind_now, window_end, window["pages_stored"])
            # no verdict, no cursor move: the same page resumes next run
            return StoreOutcome(*counts[:3])

        outcome = StoreOutcome(*counts[:3])
        _log_counts(
            provider_id,
            "bootstrap" if bootstrap else "window",
            window["pages_stored"],
            page,
            counts,
        )
        verdict = _classify(counts)
        if verdict == "total":
            # The whole page is retried next run, so its part-done progress and
            # its (all-failed) counts must go with it — kept, they would make
            # every later run re-classify the same stale totals and never
            # re-store a thing. Not spelled out in v1.15 B5, which only says
            # this for `partial`; the same reason applies here.
            _reset_page_progress(window)  # rebinds, so `counts` keeps the totals
            return _on_total_failure(provider_id, state, counts)
        state.pop("total_failures", None)
        state.pop("backoff_until", None)
        if verdict == "partial":
            state["failed_runs"] = state.get("failed_runs", 0) + 1
            if state["failed_runs"] < RETELL_MAX_FAILED_RUNS:
                _log_incomplete(provider_id, counts, state["failed_runs"])
                _reset_page_progress(window)  # v1.15 B5: the retry re-stores from 0
                _write_retell_state(provider_id, state)
                return None  # same page is retried next run (bootstrap: marker not set until it completes)
            logger.error(
                "retell_page_abandoned",
                provider_id=str(provider_id),
                abandoned=counts[2] + counts[5],
                failed_runs=state["failed_runs"],
            )
        state.pop("failed_runs", None)
        # The page is done, so whatever instability its resumes saw is behind
        # us: only repeated mismatches on one page say the listing is unusable.
        state.pop("page_changed", None)
        if outcome.stored == 0 and outcome.malformed > 0:
            logger.warning(
                "retell_page_all_malformed",
                provider_id=str(provider_id),
                malformed=outcome.malformed,
            )
        window["pages_stored"] += 1
        if page.digest is not None:
            window["digests"] = (window["digests"] + [page.digest])[
                -RETELL_DIGEST_HISTORY:
            ]

        # A page cap caught here (post-store) completes the bootstrap instead of
        # restarting it: unlike a windowed page_cap, this is not "paging isn't
        # working", just "that's enough calls for a first sync" (newest 1000).
        # v1.15 B5: measured on `pages_stored`, the CONTIGUOUS pages of one
        # attempt — a restart re-lists from page 1 and zeroes it with them, so
        # replayed pages can never spend the cap on history already covered.
        capped = (
            bootstrap
            and page.has_more
            and window["pages_stored"] >= RETELL_BOOTSTRAP_MAX_PAGES
        )
        if capped:
            logger.info(
                "retell_bootstrap_capped",
                provider_id=str(provider_id),
                pages_stored=window["pages_stored"],
            )

        # A page stored across several runs takes `has_more` and `next_key` from
        # whichever response completed it, never from the first: both are read
        # only here, once the page is done, and the cursor that follows the page
        # as Retell listed it last is the one that pages on from it correctly.
        if page.has_more and not capped:
            if window["skip"] is not None:
                window["skip"] += RETELL_LIST_PAGE_LIMIT
            else:
                window["key"] = page.next_key
            _reset_page_progress(window)  # the cursor moved: a new page starts at 0
            if not _write_retell_state(provider_id, state):
                return None
            if not bootstrap:  # the bootstrap frontier is frozen, not "behind"
                # F2/N2: a fresh clock reading, not the run-start `now` — a
                # multi-page run can span the RETELL_BEHIND_WARN threshold, and
                # window bounds (not lag reporting) are what must stay frozen.
                _log_behind(
                    provider_id, timezone.now(), window_end, window["pages_stored"]
                )
            # checkpointed: a run that dies here resumes from here, not from
            # the start. Still within the per-run wall-clock budget (and, when
            # given, the activity-level deadline): keep paging in this same
            # run instead of returning (v1.14 R1 M2; N1 F1).
            if timezone.now() < run_deadline:
                continue
            return outcome

        if bootstrap:
            if not _complete_bootstrap(provider_id, state, window_end):
                return None
            return outcome

        # Hint recovery. (1) Cursor paging just completed a multi-page window without a restart: cursors work, drop the cap entirely.
        # (2) Otherwise a streak of one-page windows opened at the hint (never narrowed) says the hint may be too small; one such window
        #     is not enough (the window right after a narrowing completes on page 1 by construction), a streak is. Any one-page window
        #     counts, whatever its size, so there is no dead band between "grow" and "narrow".
        if (
            window["pages_stored"] >= 2
            and not window["narrowed"]
            and window["restarts"] == 0
            and window["skip"] is None
        ):
            state.pop("window_hint_seconds", None)
            state.pop("one_page_streak", None)
        elif (
            window["opened_at_hint"]
            and not window["narrowed"]
            and window["pages_stored"] == 1
        ):
            state["one_page_streak"] = state.get("one_page_streak", 0) + 1
            if state["one_page_streak"] >= RETELL_WINDOW_GROW_AFTER:
                state["window_hint_seconds"] = int(
                    min(_hint_for(state) * 2, RETELL_WINDOW_HINT_MAX).total_seconds()
                )
                state["one_page_streak"] = 0
        else:
            state.pop("one_page_streak", None)
        state.pop(
            "window", None
        )  # the window is complete; the next run opens a new one from window_end
        if not _write_retell_state(provider_id, state):
            return None
        _advance_watermark(provider_id, window_end)
        # F2/N2: fresh clock here too, for the same reason as the in-loop check above.
        behind_now = timezone.now()
        if (
            (behind_now - window_end) > RETELL_BEHIND_WARN
        ):  # independent of has_more: a capped window that leaves the frontier old is also "behind"
            _log_behind(provider_id, behind_now, window_end, window["pages_stored"])
        return outcome


def _log_counts(provider_id, mode, pages_stored, page, counts):
    # Every number here is the COMPLETE page's, accumulated across however many
    # runs it took; the page argument is only for `has_more`.
    logger.info(
        "retell_poll_counts",
        provider_id=str(provider_id),
        mode=mode,
        pages_stored=pages_stored,
        stored=counts[0],
        malformed=counts[1],
        export_failed=counts[2],
        dropped_no_end=counts[3],
        dropped_missing=counts[4],
        dropped_failed=counts[5],
        has_more=page.has_more,
    )


def _log_behind(provider_id, now, window_end, pages_stored):
    age = int((now - window_end).total_seconds())
    if age > RETELL_BEHIND_ERROR.total_seconds():
        logger.error(
            "retell_poll_stalled",
            provider_id=str(provider_id),
            frontier_age_seconds=age,
            pages_stored=pages_stored,
        )
    else:
        logger.warning(
            "retell_poll_behind",
            provider_id=str(provider_id),
            frontier_age_seconds=age,
            pages_stored=pages_stored,
        )


def _log_incomplete(provider_id, counts, failed_runs):
    logger.warning(
        "retell_store_incomplete",
        provider_id=str(provider_id),
        stored=counts[0],
        export_failed=counts[2],
        dropped_failed=counts[5],
        malformed=counts[1],
        failed_runs=failed_runs,
    )


def _on_total_failure(
    provider_id, state, counts
) -> None:  # outage / misconfiguration: wait with backoff, never abandon
    state["total_failures"] = state.get("total_failures", 0) + 1
    state["backoff_until"] = (
        timezone.now() + _backoff_delay(state["total_failures"])
    ).isoformat()  # fresh clock: the page may have taken minutes
    _log_incomplete(provider_id, counts, state.get("failed_runs", 0))
    _write_retell_state(
        provider_id, state
    )  # a skipped write is logged by the helper; this run ends without progress either way
    return None


def _complete_bootstrap(provider_id, state, end: datetime) -> bool:
    """Finish a bootstrap: watermark repair/advance to ``end`` (the frozen
    first-run end), then replace state with just the marker — which is also
    what clears ``bootstrap_stuck``. v1.15 B6 leaves this with a single caller
    (the page loop, not has_more / capped): a bootstrap that cannot page is
    now retried behind a backoff, never completed without coverage. Returns
    whether the write succeeded.
    """
    if (
        _repair_future_watermark(provider_id, end, end) == 0
    ):  # any watermark later than the bootstrap end is invalid after a bootstrap
        _advance_watermark(provider_id, end)
    state.clear()
    state["bootstrapped"] = True  # nothing from an older layout survives the bootstrap
    return _write_retell_state(provider_id, state)


def _restart_window(provider_id, state, *, cause) -> None:
    window = state["window"]
    window["restarts"] += 1
    logger.warning(
        "retell_window_restarted",
        provider_id=str(provider_id),
        cause=cause,
        restarts=window["restarts"],
        pages_stored=window["pages_stored"],
    )
    window["key"] = None
    window["pages_stored"] = 0
    window["digests"] = []
    _reset_page_progress(window)  # v1.15 B1: a restart re-lists from page 1
    state.pop("page_changed", None)  # the page those mismatches belonged to is gone
    if window["restarts"] >= RETELL_MAX_WINDOW_RESTARTS:
        bootstrap = window["start"] is None
        width = None if bootstrap else _parse(window["end"]) - _parse(window["start"])
        if (
            not bootstrap and width > RETELL_MIN_WINDOW
        ):  # paging is not working: halve this window and remember the size for new windows
            window_start = _parse(window["start"])
            new_end = window_start + width / 2
            window["end"] = new_end.isoformat()
            window["skip"] = None
            window["restarts"] = 0
            window["narrowed"] = True
            state["window_hint_seconds"] = max(1, int((width / 2).total_seconds()))
            state.pop("one_page_streak", None)
            logger.warning(
                "retell_window_narrowed",
                provider_id=str(provider_id),
                window_seconds=state["window_hint_seconds"],
            )
        elif (
            window["skip"] is None
        ):  # bootstrap, or ≤ 1 s and still > RETELL_LIST_PAGE_LIMIT calls: last resort, offset paging (D15)
            window["skip"] = 0
            window["restarts"] = 0
            logger.error("retell_window_offset_mode", provider_id=str(provider_id))
        elif bootstrap:
            # v1.15 B6: a bootstrap whose offset fallback failed too must NOT
            # complete — that would advance the watermark past history it never
            # covered and exclude those calls from every later window. Keep the
            # window (frozen `end` and all), back off with escalation, and try
            # the whole attempt again in cursor mode when the backoff expires.
            state["bootstrap_stuck"] = state.get("bootstrap_stuck", 0) + 1
            state["backoff_until"] = (
                timezone.now() + _backoff_delay(state["bootstrap_stuck"])
            ).isoformat()
            window["skip"] = None
            window["restarts"] = 0
            logger.error(
                "retell_bootstrap_stuck",
                provider_id=str(provider_id),
                stuck_runs=state["bootstrap_stuck"],
                backoff_until=state["backoff_until"],
            )
            _write_retell_state(provider_id, state)
            return None
        else:  # offset paging failed too: stall loudly, never advance
            logger.error("retell_window_stuck", provider_id=str(provider_id))
            window["skip"] = 0
            window["restarts"] = 0
    else:
        window["skip"] = (
            0 if window["skip"] is not None else None
        )  # stay in the current mode, from its first page
    _write_retell_state(provider_id, state)
    return None


def _manual_retell_run(
    provider, *, start_time: datetime | None, end_time: datetime | None
) -> StoreOutcome | None:
    provider_id = provider.id
    if start_time is None:
        logger.error(
            "provider_manual_run_rejected",
            provider_id=str(provider_id),
            reason="start_time_required",
        )
        return None
    end = end_time or (timezone.now() - RETELL_VISIBILITY_LAG)
    if start_time >= end:
        logger.error(
            "provider_manual_run_rejected",
            provider_id=str(provider_id),
            reason="empty_range",
        )
        return None
    key, pages, calls, has_more, outcome = None, 0, 0, False, StoreOutcome(0, 0, 0)
    while pages < RETELL_MANUAL_RUN_MAX_PAGES:
        page = ObservabilityService.fetch_retell_page(
            provider, start_time, end, pagination_key=key
        )  # RetellCursorRejected propagates: the run fails
        outcome = process_and_store_logs(page.calls, provider)
        # A manual run has no window to accumulate in: one response IS the
        # whole page, so its own drop counters are the page's.
        _log_counts(
            provider_id,
            "manual",
            pages,
            page,
            [
                outcome.stored,
                outcome.malformed,
                outcome.export_failed,
                page.dropped_no_end,
                page.dropped_missing,
                page.dropped_failed,
            ],
        )
        pages += 1
        calls += len(page.calls)
        has_more = page.has_more
        if not page.has_more:
            break
        key = page.next_key
    logger.info(
        "retell_manual_run_covered",
        provider_id=str(provider_id),
        pages=pages,
        calls=calls,
        has_more=has_more,
    )
    return outcome


def _poll_other_provider(
    provider, *, start_time: datetime | None, end_time: datetime | None
) -> StoreOutcome:
    provider_id = provider.id
    now = timezone.now()
    end = min(end_time or now, now)
    if provider.last_fetched_at is not None and provider.last_fetched_at > now:
        logger.warning("provider_watermark_in_future", provider_id=str(provider_id))
        _repair_future_watermark(
            provider_id, now, now - RETELL_FUTURE_WATERMARK_LOOKBACK
        )
        start = now - RETELL_FUTURE_WATERMARK_LOOKBACK
    else:
        start = (
            start_time if start_time is not None else provider.last_fetched_at
        )  # today's precedence: explicit start wins
    logger.info(
        "provider_log_fetch_started",
        provider_type=provider.provider,
        start_time=str(start) if start else None,
        end_time=str(end),
    )
    logs = ObservabilityService.get_call_logs(
        provider=provider, start_time=start, end_time=end
    )  # HTTPError propagates to fetch_logs_for_provider
    try:
        outcome = process_and_store_logs(logs, provider)
    except Exception as exc:
        logger.error(
            "provider_log_processing_failed",
            provider_type=provider.provider,
            logs_count=len(logs) if logs else 0,
            error_type=type(exc).__name__,
        )
        raise  # CHANGED from today: no advance and the run counts as failed (today it advanced before storing and reported success)
    _advance_watermark(
        provider_id, end
    )  # after the store (today it is before); not gated on the outcome counts
    logger.info(
        "Successfully fetched and stored logs for provider",
        provider_id=str(provider_id),
        provider_type=provider.provider,
        logs_count=len(logs) if logs else 0,
    )
    return outcome


def _create_observation_span(
    project, provider, normalized_data, metadata, provider_log_id=None
):
    """Build the conversation Trace + ObservationSpan in memory for a pulled call.

    CDC is off (CH25): the fi-collector export owns the CH ``spans``/``traces``
    write — there are no PG ``tracer_trace`` / ``tracer_observation_span`` tables.
    The trace id is deterministic (project id : provider : log id) so a re-poll
    upserts in place under the CH RMT sort keys (both include trace_id) instead
    of duplicating.
    """
    span_kwargs = {
        "id": uuid.uuid4(),
        "project": project,
        "name": f"{provider.provider.capitalize()} Call Log",
        "observation_type": "conversation",
        "start_time": normalized_data.get("start_time"),
        "end_time": normalized_data.get("end_time"),
        "input": normalized_data.get("input", {}),
        "output": normalized_data.get("output", {}),
        "metadata": metadata,
        "provider": provider.provider,
        "cost": normalized_data.get("cost"),
        "status": normalized_data.get("status"),
        "span_attributes": normalized_data.get("span_attributes", {}),
        "prompt_tokens": normalized_data.get("prompt_tokens"),
        "completion_tokens": normalized_data.get("completion_tokens"),
        "total_tokens": normalized_data.get("total_tokens"),
        "latency_ms": normalized_data.get("latency_ms"),
    }
    trace = Trace(
        id=_provider_collector_trace_id(project.id, provider.provider, provider_log_id),
        project=project,
        metadata=metadata,
    )
    return ObservationSpan(trace=trace, **span_kwargs)


_PROVIDER_SPAN_NS = uuid.UUID("4d61d4e2-7b3c-4a1e-9f02-2c6a5b8e1d70")
_REHOST_BILLING_NS = uuid.UUID("8de415d3-3146-47fa-b3d6-bf3c05421621")


def _rehost_billing_event_id(
    project_id: str | uuid.UUID,
    provider: str,
    call_id: str,
    artifact_type: str,
) -> str:
    """Stable billing ID for one project-scoped provider recording artifact.

    The namespace and Vapi input string intentionally match the prior Vapi-only
    helper, so already-issued Vapi event IDs remain stable across this refactor.
    """
    return str(
        uuid.uuid5(
            _REHOST_BILLING_NS,
            f"{project_id}:{provider}:{call_id}:{artifact_type}",
        )
    )


def _provider_collector_span_id(
    project_id: str | uuid.UUID, provider: str, provider_log_id: str
) -> str:
    """Deterministic id stable across re-polls so CH ``spans`` (ReplacingMergeTree) upserts in place.
    Keyed by ``project_id`` so a call shared across projects (one provider account, many
    projects) gets a distinct id per project — only the project-scoping convention matches
    deterministic_id.py; this natural key (``:``-joined, provider-call) is local to this module.
    """
    return uuid.uuid5(
        _PROVIDER_SPAN_NS, f"{str(project_id)}:{provider}:{provider_log_id}"
    ).hex[:16]


def _provider_collector_trace_id(
    project_id: str | uuid.UUID, provider: str, provider_log_id: str
) -> uuid.UUID:
    """Deterministic trace id stable across re-polls. The CH ``spans`` and ``traces`` RMT sort keys both include trace_id, so a random id per poll would duplicate; this keys both writes to the call.
    Keyed by ``project_id`` so the same provider call ingested into multiple projects yields a distinct trace per project.
    """
    return uuid.uuid5(
        _PROVIDER_SPAN_NS, f"trace:{str(project_id)}:{provider}:{provider_log_id}"
    )


def _to_epoch_ns(value) -> int | None:
    """Coerce a datetime / epoch-seconds / epoch-ns value to epoch nanoseconds."""
    if value is None:
        return None
    if hasattr(value, "timestamp"):
        return int(value.timestamp() * 1e9)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # Heuristic: < 1e12 seconds, < 1e15 ms, else ns — normalize to ns.
    if v < 1e12:
        return int(v * 1e9)
    if v < 1e15:  # milliseconds
        return int(v * 1e6)
    return int(v)


def _export_provider_call_to_collector(
    span, provider: str, provider_log_id: str
) -> int:
    """Emit a pulled call's CONVERSATION span to the fi-collector, which writes it to CH ``spans``/``traces``.

    Returns the count `emit_spans_to_collector` acknowledged (0 on any early
    return or exception). Never raises.
    """
    try:
        project = span.project
        organization_id = str(getattr(project, "organization_id", "") or "")
        if not organization_id:
            return 0
        # OTLP can't carry the nested raw_log dict; re-attach it as a JSON string below.
        attrs = {
            k: v for k, v in (span.span_attributes or {}).items() if k != "raw_log"
        }
        attrs["gen_ai.span.kind"] = "CONVERSATION"
        attrs["gen_ai.system"] = provider
        if span.input not in (None, "", [], {}):
            attrs["input.value"] = span.input
        if span.output not in (None, "", [], {}):
            attrs["output.value"] = span.output
        raw_log = (span.span_attributes or {}).get("raw_log") or {}
        if raw_log:
            attrs["raw_log"] = json.dumps(raw_log, default=str)
        # Stamp the normalized transcript (read path falls back to this).
        try:
            processed = ObservabilityService.process_raw_logs(
                raw_log, provider, span_attributes=span.span_attributes or {}
            )
            if processed.get("transcript"):
                attrs["fi.conversation.transcript"] = processed["transcript"]
        except Exception as exc:
            logger.warning(
                "provider_transcript_compute_failed",
                provider=provider,
                error_type=type(exc).__name__,
            )
        start_ns = _to_epoch_ns(span.start_time)
        span_dict = {
            "trace_id": span.trace.id.hex,
            "span_id": _provider_collector_span_id(
                project.id, provider, provider_log_id
            ),
            "parent_span_id": None,
            "parent_id": None,
            "name": span.name,
            "attributes": attrs,
        }
        if start_ns is not None:
            span_dict["start_time"] = start_ns
        end_ns = _to_epoch_ns(span.end_time)
        if end_ns is not None:
            span_dict["end_time"] = end_ns
        # Stamp OTLP status from call outcome so a failed call isn't recorded as completed (collector copies it into CH `spans.status`).
        _call_status = (
            str(attrs.get("call.status") or getattr(span, "status", "") or "")
            .strip()
            .lower()
        )
        if _call_status in (
            "error",
            "failed",
            "failure",
            "busy",
            "no-answer",
            "no_answer",
            "canceled",
            "cancelled",
        ):
            span_dict["status_code"] = "ERROR"
        from tracer.services.collector_ingest import emit_spans_to_collector

        return emit_spans_to_collector(
            [span_dict],
            project_name=project.name,
            project_type=project.trace_type,
            organization_id=organization_id,
            workspace_id=str(project.workspace_id) if project.workspace_id else None,
            service_name="fi-provider",
        )
        # collectTrace is sole `traces` writer (derives it from this root span); no app-side mirror, a second row would never merge.
    except Exception as exc:
        logger.error(
            "provider_collector_export_failed",
            provider=provider,
            error_type=type(exc).__name__,
        )
        return 0


def flatten_provider_call_attributes(provider_key: str, payload: dict) -> dict:
    """Flat eval attributes for one provider call payload — the same shape the
    ingest pipeline stores on the span.

    Lets callers (e.g. the simulate call-detail drawer) render flat keys like
    ``call.duration`` / ``conversation.transcript.*`` / cost for non-VAPI
    providers instead of a single collapsed ``raw_log`` tree. VAPI is handled by
    its caller directly because it needs ``include_call_logs=False`` to skip a
    blocking log fetch. Returns ``{}`` for an unknown provider or on any
    normalizer error, so callers can fall back to raw_log.
    """
    normalizers = {
        ProviderChoices.RETELL.value: normalize_retell_data,
        ProviderChoices.ELEVEN_LABS.value: normalize_eleven_labs_data,
        ProviderChoices.BLAND.value: normalize_bland_data,
        ProviderChoices.TWILIO.value: normalize_twilio_data,
    }
    normalize_fn = normalizers.get(provider_key)
    if normalize_fn is None or not isinstance(payload, dict):
        return {}
    try:
        return normalize_fn(payload).get("span_attributes") or {}
    except Exception as exc:
        logger.warning(
            "flatten_provider_call_attributes_failed",
            provider=provider_key,
            error_type=type(exc).__name__,
        )
        return {}


def process_and_store_logs(
    logs: list,
    provider: ObservabilityProvider,
    *,
    api_key: str | None = None,
) -> StoreOutcome:
    """
    Processes raw log data and stores it as ObservationSpan objects.

    For Vapi providers, ``api_key`` is threaded through to
    :func:`normalize_vapi_data` so the call-log download can use the
    authenticated endpoint. When ``api_key`` is None it is resolved
    via the Selector; when no key is available the pipeline falls back
    to the legacy unauthenticated fetch.
    """
    project = provider.project

    if provider.provider == ProviderChoices.VAPI and api_key is None:
        try:
            from tracer.selectors import get_agent_api_key

            api_key = get_agent_api_key(project.id, provider.provider)
        except Exception as exc:
            logger.error(
                "process_and_store_logs: vapi api_key resolution failed",
                provider_id=str(provider.id),
                error_type=type(exc).__name__,
            )

    normalization_functions = {
        ProviderChoices.VAPI: lambda log: normalize_vapi_data(
            log, api_key=api_key, project_id=str(project.id)
        ),
        ProviderChoices.RETELL: lambda log: normalize_retell_data(
            log, project_id=str(project.id)
        ),
        ProviderChoices.ELEVEN_LABS: normalize_eleven_labs_data,
        ProviderChoices.BLAND: lambda log: normalize_bland_data(
            log, project_id=str(project.id)
        ),
        ProviderChoices.TWILIO: normalize_twilio_data,
    }

    if provider.provider not in normalization_functions:
        return StoreOutcome(0, 0, 0)

    normalize_fn = normalization_functions[provider.provider]

    if not isinstance(logs, (list, tuple)):
        logger.error(
            "process_and_store_logs: logs is NOT a list/tuple",
            logs_type=type(logs).__name__,
        )
        return StoreOutcome(0, 0, 0)

    stored = 0
    malformed = 0
    export_failed = 0

    for log in logs:
        provider_log_id = None
        try:
            normalized_data = normalize_fn(log)
            provider_log_id = normalized_data.get("id")
        except Exception as exc:
            logger.error(
                "provider_log_normalization_failed",
                provider_type=provider.provider,
                error_type=type(exc).__name__,
            )
            malformed += 1
            continue

        if not provider_log_id:
            logger.error(
                "provider_log_id_missing",
                provider_type=provider.provider,
            )
            malformed += 1
            continue

        metadata = {
            "provider": provider.provider,
            "provider_log_id": provider_log_id,
        }

        try:
            # CH25: no PG span/trace store; the fi-collector owns CH `spans` and
            # the deterministic span/trace ids upsert re-polls in CH (RMT), so
            # build the span in memory for the collector export below.
            span = _create_observation_span(
                project, provider, normalized_data, metadata, provider_log_id
            )
        except Exception as exc:
            logger.error(
                "provider_observation_span_creation_failed",
                provider_type=provider.provider,
                error_type=type(exc).__name__,
            )
            malformed += 1
            continue

        # Emit to the fi-collector: it writes CH `spans`/`traces` (the read store)
        # AND meters ingestion usage, so there is no app-side CH write or usage emit.
        exported = _export_provider_call_to_collector(
            span, provider.provider, provider_log_id
        )
        if exported and exported > 0:
            stored += 1
        else:
            export_failed += 1

        # Emit one idempotent ledger event per rehosted provider recording type.
        # Provider polls repeat raw URLs, so UUID5 is the durable
        # dedupe key; this deployment has no ProviderLog model to persist on.
        rehost_uploads = normalized_data.get("rehost_uploads") or {}
        if rehost_uploads:
            organization_id = str(getattr(project, "organization_id", "") or "")
            if organization_id:
                for artifact_type, payload_bytes in rehost_uploads.items():
                    emit_span_ingestion_usage(
                        organization_id=organization_id,
                        num_traces=0,
                        num_spans=0,
                        payload_bytes=payload_bytes,
                        source="voice_recording_rehost",
                        event_id=_rehost_billing_event_id(
                            project.id,
                            provider.provider,
                            provider_log_id,
                            artifact_type,
                        ),
                    )

    return StoreOutcome(stored, malformed, export_failed)


def create_observability_provider(
    enabled: bool,
    user_id: str,
    organization: Organization,
    workspace: str,
    project_name: str,
    provider: str,
):
    try:
        if not enabled:
            return None

        from accounts.models.workspace import Workspace as WorkspaceModel

        # Resolve workspace to a model instance — callers may pass either
        # a string UUID (MCP tools) or a Workspace instance (REST views).
        if workspace and isinstance(workspace, str):
            workspace_instance = WorkspaceModel.objects.get(id=workspace)
            workspace_id = workspace
        elif workspace:
            workspace_instance = workspace
            workspace_id = str(workspace.id)
        else:
            workspace_instance = None
            workspace_id = None

        project = get_or_create_project(
            project_name=project_name,
            organization_id=organization.id,
            project_type="observe",
            user_id=user_id,
            workspace_id=workspace_id,
            source=ProjectSourceChoices.SIMULATOR.value,
        )

        serializer = ObservabilityProviderSerializer(
            data={
                "project": project.id if project else None,
                "provider": provider,
                "enabled": True,
                "organization": organization.id,
                "workspace": workspace_id,
            }
        )
        if not serializer.is_valid():
            return serializer.errors

        obj = serializer.save(
            project=project,
            organization=organization,
            workspace=workspace_instance,
        )
        return obj
    except ResourceLimitError:
        raise
    except Exception as e:
        return {"error": "Invalid data", "details": e}
