import copy
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

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
    RetellPage,
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
RETELL_BEHIND_WARN = timedelta(minutes=20)
RETELL_BEHIND_ERROR = timedelta(hours=6)
RETELL_BACKOFF_BASE = timedelta(minutes=10)
RETELL_BACKOFF_MAX = timedelta(hours=6)
RETELL_MAX_BACKOFF_EXPONENT = 6
RETELL_MAX_FAILED_RUNS = 3
RETELL_MAX_WINDOW_RESTARTS = 3
RETELL_MANUAL_RUN_MAX_PAGES = 5


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
            start_dt = start_dt.replace(tzinfo=dt_timezone.utc)
    end_dt = None
    if end_time is not None:
        end_dt = datetime.fromisoformat(end_time)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=dt_timezone.utc)

    if scheduled:
        if provider_id is not None:
            provider_ids = [provider_id]
        else:
            provider_ids = list(
                ObservabilityProvider.objects.filter(enabled=True)
                .values_list("id", flat=True)
                .iterator(chunk_size=750)
            )
    else:
        if not provider_id:
            logger.error("provider_manual_run_rejected", reason="provider_id_required")
            return
        provider_ids = [provider_id]

    success_count = 0
    failure_count = 0

    for pid in provider_ids:
        try:
            result = fetch_logs_for_provider(
                pid, scheduled=scheduled, start_time=start_dt, end_time=end_dt
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
                _poll_retell_provider(provider)
                if scheduled
                else _manual_retell_run(provider, start_time=start_time, end_time=end_time)
            )
        return _poll_other_provider(provider, start_time=start_time, end_time=end_time)
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if provider.provider == ProviderChoices.RETELL and status in (401, 403):
            logger.error("retell_auth_failed", provider_id=str(provider_id), status_code=status)
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
    n = ObservabilityProvider.all_objects.filter(id=provider_id).update(poll_state=merged)
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


def _repair_future_watermark(provider_id, now: datetime, new: datetime) -> int:  # the single exception to monotonicity
    n = ObservabilityProvider.all_objects.filter(id=provider_id, last_fetched_at__gt=now).update(
        last_fetched_at=new
    )
    if n:
        logger.warning("provider_watermark_repaired", provider_id=str(provider_id), new=new.isoformat())
    return n


def _page_digest(calls: list[dict]) -> str | None:
    if not calls:
        return None
    return hashlib.sha256(",".join(sorted(c["call_id"] for c in calls)).encode()).hexdigest()


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)  # values were written by .isoformat() on aware UTC datetimes


def _backoff_delay(total_failures: int) -> timedelta:
    return min(
        RETELL_BACKOFF_BASE * (2 ** min(total_failures - 1, RETELL_MAX_BACKOFF_EXPONENT)),
        RETELL_BACKOFF_MAX,
    )


def _hint_for(state) -> timedelta:
    return timedelta(seconds=state.get("window_hint_seconds", int(RETELL_WINDOW_HINT_MAX.total_seconds())))


_WINDOW_TYPES = {
    "start": str,
    "end": str,
    "opened_at_hint": bool,
    "narrowed": bool,
    "key": (str, type(None)),
    "skip": (int, type(None)),
    "pages_stored": int,
    "digests": list,
    "restarts": int,
}


def _valid_window(window) -> bool:
    if not isinstance(window, dict):
        return False
    try:
        return all(isinstance(window[k], t) for k, t in _WINDOW_TYPES.items()) and \
            _parse(window["start"]).tzinfo is not None and _parse(window["end"]).tzinfo is not None
    except (KeyError, ValueError, TypeError):
        return False


def _classify(page: RetellPage, outcome: StoreOutcome) -> str:  # "ok" | "total" | "partial"
    problems = outcome.export_failed + page.dropped_failed  # infra-shaped failures (retryable)
    if problems == 0:
        return "ok"  # malformed-only pages are "ok": permanent, counted, never retried
    if outcome.stored == 0 and problems >= outcome.malformed:
        return "total"  # nothing worked, mostly for infra reasons: wait with backoff
    return "partial"  # retry the same page up to RETELL_MAX_FAILED_RUNS, then abandon the problems


def _poll_retell_provider(provider) -> StoreOutcome | None:
    provider_id = provider.id
    now = timezone.now()
    end = now - RETELL_VISIBILITY_LAG
    state = _read_retell_state(provider)

    try:
        backoff_until = _parse(state["backoff_until"]) if isinstance(state.get("backoff_until"), str) else None
    except ValueError:
        backoff_until = None
    if backoff_until is not None and backoff_until > now:
        logger.warning("retell_poll_backoff", provider_id=str(provider_id), until=state["backoff_until"])
        return StoreOutcome(0, 0, 0)

    if not state.get("bootstrapped"):  # first run under this code, whatever last_fetched_at says (D10)
        page = ObservabilityService.fetch_retell_page(provider, None, end)
        outcome = process_and_store_logs(page.calls, provider)
        _log_counts(provider_id, "bootstrap", 0, page, outcome)
        verdict = _classify(page, outcome)
        if verdict == "total":
            return _on_total_failure(provider_id, state, page, outcome)
        if verdict == "partial":
            state["failed_runs"] = state.get("failed_runs", 0) + 1
            if state["failed_runs"] < RETELL_MAX_FAILED_RUNS:
                _log_incomplete(provider_id, page, outcome, state["failed_runs"])
                _write_retell_state(provider_id, state)
                return None  # bootstrap is retried next run; marker not set
            logger.error(
                "retell_page_abandoned",
                provider_id=str(provider_id),
                abandoned=outcome.export_failed + page.dropped_failed,
                failed_runs=state["failed_runs"],
            )
        if outcome.stored == 0 and outcome.malformed > 0:
            logger.warning("retell_page_all_malformed", provider_id=str(provider_id), malformed=outcome.malformed)
        if _repair_future_watermark(provider_id, end, end) == 0:  # any watermark later than the bootstrap end is invalid after a bootstrap
            _advance_watermark(provider_id, end)
        state = {"bootstrapped": True}  # nothing from an older layout survives the bootstrap
        if not _write_retell_state(provider_id, state):
            return None
        return outcome

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
        window = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "opened_at_hint": opened_at_hint,
            "narrowed": False,
            "key": None,
            "skip": None,
            "pages_stored": 0,
            "digests": [],
            "restarts": 0,
        }
        state["window"] = window
    window_start, window_end = _parse(window["start"]), _parse(window["end"])

    try:
        page = ObservabilityService.fetch_retell_page(
            provider, window_start, window_end, pagination_key=window["key"], skip=window["skip"]
        )
    except RetellCursorRejected as exc:
        return _restart_window(provider_id, state, cause=exc.cause)
    digest = _page_digest(page.calls)
    if window["pages_stored"] > 0 and page.has_more and digest is None:
        return _restart_window(provider_id, state, cause="empty_page")
    if digest is not None and digest in window["digests"]:
        return _restart_window(provider_id, state, cause="page_repeated")
    if page.has_more and window["pages_stored"] + 1 >= RETELL_MAX_PAGES_PER_WINDOW:  # a window that needs more than 50 pages is not being paged honestly: restart → narrow
        return _restart_window(provider_id, state, cause="page_cap")

    outcome = process_and_store_logs(page.calls, provider)
    _log_counts(provider_id, "window", window["pages_stored"], page, outcome)
    verdict = _classify(page, outcome)
    if verdict == "total":
        return _on_total_failure(provider_id, state, page, outcome)
    state.pop("total_failures", None)
    state.pop("backoff_until", None)
    if verdict == "partial":
        state["failed_runs"] = state.get("failed_runs", 0) + 1
        if state["failed_runs"] < RETELL_MAX_FAILED_RUNS:
            _log_incomplete(provider_id, page, outcome, state["failed_runs"])
            _write_retell_state(provider_id, state)
            return None  # same page (same key/skip) is retried next run
        logger.error(
            "retell_page_abandoned",
            provider_id=str(provider_id),
            abandoned=outcome.export_failed + page.dropped_failed,
            failed_runs=state["failed_runs"],
        )
    state.pop("failed_runs", None)
    if outcome.stored == 0 and outcome.malformed > 0:
        logger.warning("retell_page_all_malformed", provider_id=str(provider_id), malformed=outcome.malformed)
    window["pages_stored"] += 1
    if digest is not None:
        window["digests"] = (window["digests"] + [digest])[-RETELL_DIGEST_HISTORY:]
    if page.has_more:
        if window["skip"] is not None:
            window["skip"] += RETELL_LIST_PAGE_LIMIT
        else:
            window["key"] = page.next_key
        if not _write_retell_state(provider_id, state):
            return None
        _log_behind(provider_id, now, window_end, window["pages_stored"])
        return outcome  # watermark unchanged until the window completes
    # Hint recovery. (1) Cursor paging just completed a multi-page window without a restart: cursors work, drop the cap entirely.
    # (2) Otherwise a streak of one-page windows opened at the hint (never narrowed) says the hint may be too small; one such window
    #     is not enough (the window right after a narrowing completes on page 1 by construction), a streak is. Any one-page window
    #     counts, whatever its size, so there is no dead band between "grow" and "narrow".
    if window["pages_stored"] >= 2 and not window["narrowed"] and window["restarts"] == 0 and window["skip"] is None:
        state.pop("window_hint_seconds", None)
        state.pop("one_page_streak", None)
    elif window["opened_at_hint"] and not window["narrowed"] and window["pages_stored"] == 1:
        state["one_page_streak"] = state.get("one_page_streak", 0) + 1
        if state["one_page_streak"] >= RETELL_WINDOW_GROW_AFTER:
            state["window_hint_seconds"] = int(min(_hint_for(state) * 2, RETELL_WINDOW_HINT_MAX).total_seconds())
            state["one_page_streak"] = 0
    else:
        state.pop("one_page_streak", None)
    state.pop("window", None)  # the window is complete; the next run opens a new one from window_end
    if not _write_retell_state(provider_id, state):
        return None
    _advance_watermark(provider_id, window_end)
    if (now - window_end) > RETELL_BEHIND_WARN:  # independent of has_more: a capped window that leaves the frontier old is also "behind"
        _log_behind(provider_id, now, window_end, window["pages_stored"])
    return outcome


def _log_counts(provider_id, mode, pages_stored, page, outcome):
    logger.info(
        "retell_poll_counts",
        provider_id=str(provider_id),
        mode=mode,
        pages_stored=pages_stored,
        stored=outcome.stored,
        malformed=outcome.malformed,
        export_failed=outcome.export_failed,
        dropped_no_end=page.dropped_no_end,
        dropped_missing=page.dropped_missing,
        dropped_failed=page.dropped_failed,
        has_more=page.has_more,
    )


def _log_behind(provider_id, now, window_end, pages_stored):
    age = int((now - window_end).total_seconds())
    if age > RETELL_BEHIND_ERROR.total_seconds():
        logger.error("retell_poll_stalled", provider_id=str(provider_id), frontier_age_seconds=age, pages_stored=pages_stored)
    else:
        logger.warning("retell_poll_behind", provider_id=str(provider_id), frontier_age_seconds=age, pages_stored=pages_stored)


def _log_incomplete(provider_id, page, outcome, failed_runs):
    logger.warning(
        "retell_store_incomplete",
        provider_id=str(provider_id),
        stored=outcome.stored,
        export_failed=outcome.export_failed,
        dropped_failed=page.dropped_failed,
        malformed=outcome.malformed,
        failed_runs=failed_runs,
    )


def _on_total_failure(provider_id, state, page, outcome) -> None:  # outage / misconfiguration: wait with backoff, never abandon
    state["total_failures"] = state.get("total_failures", 0) + 1
    state["backoff_until"] = (timezone.now() + _backoff_delay(state["total_failures"])).isoformat()  # fresh clock: the page may have taken minutes
    _log_incomplete(provider_id, page, outcome, state.get("failed_runs", 0))
    _write_retell_state(provider_id, state)  # a skipped write is logged by the helper; this run ends without progress either way
    return None


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
    if window["restarts"] >= RETELL_MAX_WINDOW_RESTARTS:
        window_start, window_end = _parse(window["start"]), _parse(window["end"])
        width = window_end - window_start
        if width > RETELL_MIN_WINDOW:  # paging is not working: halve this window and remember the size for new windows
            new_end = window_start + width / 2
            window["end"] = new_end.isoformat()
            window["skip"] = None
            window["restarts"] = 0
            window["narrowed"] = True
            state["window_hint_seconds"] = max(1, int((width / 2).total_seconds()))
            state.pop("one_page_streak", None)
            logger.warning("retell_window_narrowed", provider_id=str(provider_id), window_seconds=state["window_hint_seconds"])
        elif window["skip"] is None:  # ≤ 1 s and still > 1000 calls: last resort, offset paging (D15)
            window["skip"] = 0
            window["restarts"] = 0
            logger.error("retell_window_offset_mode", provider_id=str(provider_id))
        else:  # offset paging failed too: stall loudly, never advance
            window["skip"] = 0
            window["restarts"] = 0
            logger.error("retell_window_stuck", provider_id=str(provider_id))
    else:
        window["skip"] = 0 if window["skip"] is not None else None  # stay in the current mode, from its first page
    _write_retell_state(provider_id, state)
    return None


def _manual_retell_run(provider, *, start_time: datetime | None, end_time: datetime | None) -> StoreOutcome | None:
    provider_id = provider.id
    if start_time is None:
        logger.error("provider_manual_run_rejected", provider_id=str(provider_id), reason="start_time_required")
        return None
    end = end_time or (timezone.now() - RETELL_VISIBILITY_LAG)
    if start_time >= end:
        logger.error("provider_manual_run_rejected", provider_id=str(provider_id), reason="empty_range")
        return None
    key, pages, calls, has_more, outcome = None, 0, 0, False, StoreOutcome(0, 0, 0)
    while pages < RETELL_MANUAL_RUN_MAX_PAGES:
        page = ObservabilityService.fetch_retell_page(provider, start_time, end, pagination_key=key)  # RetellCursorRejected propagates: the run fails
        outcome = process_and_store_logs(page.calls, provider)
        _log_counts(provider_id, "manual", pages, page, outcome)
        pages += 1
        calls += len(page.calls)
        has_more = page.has_more
        if not page.has_more:
            break
        key = page.next_key
    logger.info("retell_manual_run_covered", provider_id=str(provider_id), pages=pages, calls=calls, has_more=has_more)
    return outcome


def _poll_other_provider(provider, *, start_time: datetime | None, end_time: datetime | None) -> StoreOutcome:
    provider_id = provider.id
    now = timezone.now()
    end = min(end_time or now, now)
    if provider.last_fetched_at is not None and provider.last_fetched_at > now:
        logger.warning("provider_watermark_in_future", provider_id=str(provider_id))
        _repair_future_watermark(provider_id, now, now - RETELL_FUTURE_WATERMARK_LOOKBACK)
        start = now - RETELL_FUTURE_WATERMARK_LOOKBACK
    else:
        start = start_time if start_time is not None else provider.last_fetched_at  # today's precedence: explicit start wins
    logger.info(
        "provider_log_fetch_started",
        provider_type=provider.provider,
        start_time=str(start) if start else None,
        end_time=str(end),
    )
    logs = ObservabilityService.get_call_logs(provider=provider, start_time=start, end_time=end)  # HTTPError propagates to fetch_logs_for_provider
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
    _advance_watermark(provider_id, end)  # after the store (today it is before); not gated on the outcome counts
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
    span_kwargs = dict(
        id=uuid.uuid4(),
        project=project,
        name=f"{provider.provider.capitalize()} Call Log",
        observation_type="conversation",
        start_time=normalized_data.get("start_time"),
        end_time=normalized_data.get("end_time"),
        input=normalized_data.get("input", {}),
        output=normalized_data.get("output", {}),
        metadata=metadata,
        provider=provider.provider,
        cost=normalized_data.get("cost"),
        status=normalized_data.get("status"),
        span_attributes=normalized_data.get("span_attributes", {}),
        prompt_tokens=normalized_data.get("prompt_tokens"),
        completion_tokens=normalized_data.get("completion_tokens"),
        total_tokens=normalized_data.get("total_tokens"),
        latency_ms=normalized_data.get("latency_ms"),
    )
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


def _export_provider_call_to_collector(span, provider: str, provider_log_id: str) -> int:
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
        exported = _export_provider_call_to_collector(span, provider.provider, provider_log_id)
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
