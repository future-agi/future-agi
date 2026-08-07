"""
Session and User Analytics Tasks

Periodic tasks for:
- Updating EndUser analytics (total_sessions, total_traces, etc.)
- Updating TraceSession metrics (span_count, total_tokens, etc.)
- Session status management (marking abandoned sessions)
- User analytics rollup
"""

from datetime import timedelta
from decimal import Decimal

import structlog
from django.db import close_old_connections, transaction
from django.db.models import F, Q
from django.utils import timezone

from tfc.temporal import temporal_activity

logger = structlog.get_logger(__name__)


def _aggregate_spans_by_trace_ids(trace_ids):
    """Single CH read + Python rollup for the per-session span aggregate
    used by the periodic update tasks. Returns the same fields the Django
    ORM aggregate() previously produced:
        - span_count, total_tokens, total_cost, total_duration (latency_ms),
          error_count, last_activity_at (max end_time of any span).
        - covered: True if every requested trace_id had at least one
          span in CH (or the input list was empty / all traces are
          legitimately empty in PG too); False if CH lag dropped any.
          See _aggregate_spans_by_trace_ids callers — both periodic-
          task branches skip the write when coverage is incomplete
          (avoids the P0 silent-undercount path from the codex
          consolidated review).

    The reader does not yet expose `error_count` or `last_activity_at`
    over a `trace_ids` set; rolling them up in Python from one
    list_by_trace_ids() call keeps the round-trip count at 1 and matches
    the previous ORM semantics exactly (Sum->0 if no rows, Count->0,
    Max->None on empty).
    """
    from tracer.services.clickhouse.v2 import get_reader

    if not trace_ids:
        return {
            "span_count": 0,
            "total_tokens": 0,
            "total_cost": 0,
            "total_duration": 0,
            "error_count": 0,
            "last_activity_at": None,
            "covered": True,
            "missing_trace_ids": [],
        }
    with get_reader() as reader:
        spans = reader.list_by_trace_ids([str(tid) for tid in trace_ids])
    span_count = len(spans)
    total_tokens = sum(int(s.total_tokens or 0) for s in spans)
    total_cost = sum(float(s.cost or 0.0) for s in spans)
    total_duration = sum(int(s.latency_ms or 0) for s in spans)
    error_count = sum(1 for s in spans if s.status == "ERROR")
    last_activity_at = max(
        (s.end_time for s in spans if s.end_time is not None),
        default=None,
    )
    # Coverage check: trace_ids that produced zero spans in CH could be
    # either (a) traces that legitimately have no spans yet on either
    # store, or (b) CH lag where the spans exist in PG but haven't
    # replicated yet. The old PG-aggregate path masked this distinction
    # (Count returned 0 either way).
    #
    # Codex wave-2 P2 (2026-05-26): distinguish (a) from (b) by querying
    # PG for whether the missing trace_ids have ANY span in PG. If yes,
    # CH is lagging (treat as not covered → skip the write). If no, the
    # trace is legitimately empty everywhere (treat as covered → write the
    # zero aggregate). This restores the original Django semantics: a
    # trace with zero spans shouldn't block the session write.
    seen_trace_ids = {str(s.trace_id) for s in spans}
    requested = {str(tid) for tid in trace_ids}
    missing_trace_ids = sorted(requested - seen_trace_ids)

    lagging_trace_ids: list[str] = []
    if missing_trace_ids:
        from tracer.models.observation_span import ObservationSpan

        pg_present_for_missing = set(
            str(tid) for tid in ObservationSpan.no_workspace_objects.filter(
                trace_id__in=missing_trace_ids, deleted=False,
            ).values_list("trace_id", flat=True).distinct()
        )
        lagging_trace_ids = sorted(pg_present_for_missing)
        if lagging_trace_ids:
            logger.warning(
                "session_agg_ch_lag",
                requested=len(requested),
                ch_seen=len(seen_trace_ids),
                pg_present_in_gap=len(lagging_trace_ids),
                lagging_sample=lagging_trace_ids[:10],
            )

    return {
        "span_count": span_count,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "total_duration": total_duration,
        "error_count": error_count,
        "last_activity_at": last_activity_at,
        # `covered=True` when every requested trace either had spans in CH
        # OR has no spans in PG either (legitimately empty). Only treat as
        # uncovered when PG has rows CH doesn't — that's the lag case.
        "covered": not lagging_trace_ids,
        "missing_trace_ids": missing_trace_ids,
        "lagging_trace_ids": lagging_trace_ids,
    }


def _get_session_metrics_from_ch(session, project_id):
    """Try to get session metrics from ClickHouse.

    Returns a dict with span_count, total_tokens, total_cost, total_duration,
    error_count, trace_count, and last_activity_at on success, or None on failure.
    """
    from tracer.services.clickhouse.query_builders.session_analytics import (
        SessionAnalyticsQueryBuilder,
    )
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    try:
        service = AnalyticsQueryService()
        builder = SessionAnalyticsQueryBuilder(project_id=str(project_id))
        query, params = builder.build_session_metrics_query([str(session.id)])
        result = service.execute_ch_query(query, params)

        if not result.data:
            return None

        row = result.data[0]
        return {
            "trace_count": int(row.get("trace_count") or 0),
            "total_tokens": int(row.get("total_tokens") or 0),
            "total_cost": row.get("total_cost") or 0,
            "ended_at": row.get("ended_at"),
        }
    except Exception:
        logger.warning(
            "ch_session_metrics_failed, falling back to postgres",
            session_id=str(session.id),
            exc_info=True,
        )
        return None


def _get_user_stats_from_ch(user, project_id):
    """Try to get user analytics from ClickHouse.

    Returns a dict with session_count, total_tokens, total_cost,
    first_seen, and last_seen on success, or None on failure.
    """
    from tracer.services.clickhouse.query_builders.session_analytics import (
        SessionAnalyticsQueryBuilder,
    )
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    try:
        service = AnalyticsQueryService()
        builder = SessionAnalyticsQueryBuilder(project_id=str(project_id))
        query, params = builder.build_user_stats_query(str(user.id))
        result = service.execute_ch_query(query, params)

        if not result.data:
            return None

        row = result.data[0]
        return {
            "session_count": int(row.get("session_count") or 0),
            "total_tokens": int(row.get("total_tokens") or 0),
            "total_cost": row.get("total_cost") or 0,
            "first_seen": row.get("first_seen"),
            "last_seen": row.get("last_seen"),
        }
    except Exception:
        logger.warning(
            "ch_user_stats_failed, falling back to postgres",
            user_id=str(user.id),
            exc_info=True,
        )
        return None


# Session timeout threshold (mark as abandoned after this period of inactivity)
SESSION_TIMEOUT_HOURS = 24


@temporal_activity(
    max_retries=2,
    time_limit=3600,
    queue="default",
)
def update_session_metrics_task():
    """
    Periodic task to recalculate session metrics from actual span data.

    Updates:
    - trace_count: Number of unique traces in the session
    - span_count: Total spans across all traces
    - total_tokens: Sum of all tokens used
    - total_cost: Sum of all costs
    - total_duration_ms: Sum of all latencies
    - error_count: Number of error spans
    - last_activity_at: Most recent span end time
    """
    from tracer.models.trace import Trace
    from tracer.models.trace_session import SessionStatus, TraceSession

    try:
        close_old_connections()

        # Get sessions that were active in the last 24 hours
        cutoff_time = timezone.now() - timedelta(hours=SESSION_TIMEOUT_HOURS)
        active_sessions = TraceSession.objects.filter(
            Q(status=SessionStatus.ACTIVE) | Q(last_activity_at__gte=cutoff_time)
        ).select_related("project")

        updated_count = 0

        for session in active_sessions:
            try:
                # Try the analytics-service CH path first for the
                # trace_count / tokens / cost / ended_at fields it provides.
                ch_metrics = _get_session_metrics_from_ch(session, session.project_id)

                if ch_metrics is not None:
                    # Use the analytics-service CH data for available fields;
                    # the per-span aggregate (span_count, total_duration,
                    # error_count) now comes from CHSpanReader.
                    session.trace_count = ch_metrics["trace_count"]
                    session.total_tokens = ch_metrics["total_tokens"]
                    session.total_cost = Decimal(str(ch_metrics["total_cost"]))

                    if ch_metrics.get("ended_at"):
                        session.last_activity_at = ch_metrics["ended_at"]

                    # Trace model is still PG, so the trace_ids materialize
                    # PG-side and feed the CH span query.
                    traces = Trace.objects.filter(session=session)
                    trace_ids = list(traces.values_list("id", flat=True))

                    extra = _aggregate_spans_by_trace_ids(trace_ids)
                    if not extra["covered"]:
                        # CH lag: some trace_ids that exist in PG haven't
                        # replicated yet. Skip the write — the next tick
                        # will pick them up. Logging the missing set so
                        # the lag is observable (codex P0 from the
                        # consolidated review: writes driven by under-
                        # counted CH reads should not be silent).
                        logger.warning(
                            "ch_lag_skip_session_metrics_write",
                            session_id=str(session.id),
                            missing_trace_ids_count=len(
                                extra["missing_trace_ids"]
                            ),
                            missing_trace_ids_sample=extra[
                                "missing_trace_ids"
                            ][:10],
                        )
                        continue
                    session.span_count = extra["span_count"]
                    session.total_duration_ms = extra["total_duration"]
                    session.error_count = extra["error_count"]

                    session.save(
                        update_fields=[
                            "trace_count",
                            "span_count",
                            "total_tokens",
                            "total_cost",
                            "total_duration_ms",
                            "error_count",
                            "last_activity_at",
                        ]
                    )
                    updated_count += 1
                    continue

                # Fallback path: the analytics-service short-circuit didn't
                # apply, but the per-span aggregate still goes through CH
                # via CHSpanReader (single read + Python rollup).
                traces = Trace.objects.filter(session=session)
                trace_ids = list(traces.values_list("id", flat=True))

                if not trace_ids:
                    continue

                span_metrics = _aggregate_spans_by_trace_ids(trace_ids)
                if not span_metrics["covered"]:
                    logger.warning(
                        "ch_lag_skip_session_metrics_write",
                        session_id=str(session.id),
                        missing_trace_ids_count=len(
                            span_metrics["missing_trace_ids"]
                        ),
                        missing_trace_ids_sample=span_metrics[
                            "missing_trace_ids"
                        ][:10],
                    )
                    continue

                # Update session (last_activity_at = max(end_time) from the
                # same rollup; was previously a separate ORDER BY -end_time
                # LIMIT 1 round-trip).
                session.trace_count = len(trace_ids)
                session.span_count = span_metrics["span_count"]
                session.total_tokens = span_metrics["total_tokens"]
                session.total_cost = Decimal(str(span_metrics["total_cost"] or 0))
                session.total_duration_ms = span_metrics["total_duration"]
                session.error_count = span_metrics["error_count"]

                if span_metrics["last_activity_at"]:
                    session.last_activity_at = span_metrics["last_activity_at"]

                session.save(
                    update_fields=[
                        "trace_count",
                        "span_count",
                        "total_tokens",
                        "total_cost",
                        "total_duration_ms",
                        "error_count",
                        "last_activity_at",
                    ]
                )

                updated_count += 1

            except Exception as e:
                logger.warning(
                    f"Failed to update metrics for session {session.id}: {e}"
                )
                continue

        logger.info(f"Updated metrics for {updated_count} sessions")
        return {"updated_sessions": updated_count}

    except Exception as e:
        logger.exception(f"Error in update_session_metrics_task: {e}")
        raise
    finally:
        close_old_connections()


@temporal_activity(
    max_retries=2,
    time_limit=3600,
    queue="default",
)
def mark_abandoned_sessions_task():
    """
    Periodic task to mark sessions as abandoned if they've been inactive
    for longer than SESSION_TIMEOUT_HOURS.
    """
    from tracer.models.trace_session import SessionStatus, TraceSession

    try:
        close_old_connections()

        cutoff_time = timezone.now() - timedelta(hours=SESSION_TIMEOUT_HOURS)

        # Find active sessions that have been inactive
        abandoned_sessions = TraceSession.objects.filter(
            status=SessionStatus.ACTIVE, last_activity_at__lt=cutoff_time
        )

        count = abandoned_sessions.count()

        if count > 0:
            abandoned_sessions.update(
                status=SessionStatus.ABANDONED, ended_at=F("last_activity_at")
            )
            logger.info(f"Marked {count} sessions as abandoned")

        return {"abandoned_count": count}

    except Exception as e:
        logger.exception(f"Error in mark_abandoned_sessions_task: {e}")
        raise
    finally:
        close_old_connections()


@temporal_activity(
    max_retries=2,
    time_limit=3600,
    queue="default",
)
def update_end_user_analytics_task():
    """
    Periodic task to recalculate end user analytics from actual data.

    Updates:
    - total_sessions: Count of sessions for this user
    - total_traces: Count of traces for this user
    - total_tokens_used: Sum of all tokens consumed
    - total_cost: Sum of all costs attributed
    - first_seen: Earliest trace/session time
    - last_seen: Most recent activity
    """
    from tracer.models.observation_span import EndUser
    from tracer.models.trace_session import TraceSession
    from tracer.services.clickhouse.v2 import get_reader

    try:
        close_old_connections()

        # Get users who have had recent activity (last 7 days)
        cutoff_time = timezone.now() - timedelta(days=7)
        active_users = EndUser.objects.filter(
            Q(last_seen__gte=cutoff_time) | Q(last_seen__isnull=True)
        ).select_related("project", "organization")

        updated_count = 0

        for user in active_users:
            try:
                # Try the analytics-service CH path first for session-level
                # rollups it already computes.
                ch_stats = _get_user_stats_from_ch(user, user.project_id)

                if ch_stats is not None:
                    # Use the analytics-service CH data for session-level
                    # rollups; per-user span/trace aggregate now also comes
                    # from CH via CHSpanReader.aggregate_by_end_user (single
                    # call returning span_count, trace_count, tokens, cost,
                    # first_seen, last_seen).
                    user.total_sessions = ch_stats["session_count"]
                    user.total_tokens_used = ch_stats["total_tokens"]
                    user.total_cost = Decimal(str(ch_stats["total_cost"]))

                    # trace_count via uniqExact(trace_id) in CH (was
                    # .filter(end_user=user).values("trace_id").distinct()
                    # .count() in PG). project_id scopes the read for
                    # defense-in-depth tenant isolation.
                    with get_reader() as reader:
                        user_agg = reader.aggregate_by_end_user(
                            str(user.id), project_id=str(user.project_id)
                        )
                    user.total_traces = user_agg["trace_count"]

                    if ch_stats.get("first_seen"):
                        if (
                            not user.first_seen
                            or ch_stats["first_seen"] < user.first_seen
                        ):
                            user.first_seen = ch_stats["first_seen"]

                    # last_seen parity: prefer user_agg["last_seen"] which
                    # is max(end_time) — matches Django's previous behavior
                    # of `.order_by("-end_time").first().end_time`. The
                    # legacy ch_stats path returns max(start_time), which
                    # underestimates last_seen for any long-running span.
                    # Use that only as a fallback when CHSpanReader has
                    # nothing (e.g. CH lag for this user).
                    if user_agg["last_seen"]:
                        user.last_seen = user_agg["last_seen"]
                    elif ch_stats.get("last_seen"):
                        user.last_seen = ch_stats["last_seen"]

                    user.save(
                        update_fields=[
                            "total_sessions",
                            "total_traces",
                            "total_tokens_used",
                            "total_cost",
                            "first_seen",
                            "last_seen",
                        ]
                    )
                    updated_count += 1
                    continue

                # Fallback path: the analytics-service short-circuit didn't
                # apply, but all per-user span aggregates still go through
                # CH via CHSpanReader.aggregate_by_end_user — one CH read
                # covers Sum(tokens), Sum(cost), uniqExact(trace_id),
                # min(start_time) (first_seen), max(end_time) (last_seen).
                session_count = TraceSession.objects.filter(end_user=user).count()

                with get_reader() as reader:
                    user_agg = reader.aggregate_by_end_user(
                        str(user.id), project_id=str(user.project_id)
                    )

                # Update user analytics
                user.total_sessions = session_count
                user.total_traces = user_agg["trace_count"]
                user.total_tokens_used = user_agg["total_tokens"]
                user.total_cost = Decimal(str(user_agg["cost"] or 0))

                if user_agg["first_seen"]:
                    if not user.first_seen or user_agg["first_seen"] < user.first_seen:
                        user.first_seen = user_agg["first_seen"]

                if user_agg["last_seen"]:
                    user.last_seen = user_agg["last_seen"]

                user.save(
                    update_fields=[
                        "total_sessions",
                        "total_traces",
                        "total_tokens_used",
                        "total_cost",
                        "first_seen",
                        "last_seen",
                    ]
                )

                updated_count += 1

            except Exception as e:
                logger.warning(f"Failed to update analytics for user {user.id}: {e}")
                continue

        logger.info(f"Updated analytics for {updated_count} end users")
        return {"updated_users": updated_count}

    except Exception as e:
        logger.exception(f"Error in update_end_user_analytics_task: {e}")
        raise
    finally:
        close_old_connections()


@temporal_activity(
    max_retries=2,
    time_limit=1800,
    queue="default",
)
def complete_sessions_with_trace_completion_task():
    """
    Task to mark sessions as completed when their traces have completed.

    A session is considered complete when:
    - It has at least one trace
    - No new spans have been added in the last hour
    - The last span has status OK or ERROR (not UNSET)
    """
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace import Trace
    from tracer.models.trace_session import SessionStatus, TraceSession
    from tracer.services.clickhouse.v2 import get_reader

    try:
        close_old_connections()

        # Find active sessions with recent activity that might be complete
        one_hour_ago = timezone.now() - timedelta(hours=1)

        potentially_complete = TraceSession.objects.filter(
            status=SessionStatus.ACTIVE,
            last_activity_at__lt=one_hour_ago,
            trace_count__gt=0,
        )

        completed_count = 0

        for session in potentially_complete:
            try:
                # Trace model is still PG; resolve session→trace_ids here
                # and then read the spans from CH in one round-trip below.
                traces = Trace.objects.filter(session=session)
                trace_ids = list(traces.values_list("id", flat=True))

                if not trace_ids:
                    continue

                # Single CH read covers both the "last span by -end_time"
                # lookup and the "any span emitted in the last hour" check.
                # Semantic shift: the original "recent_spans" predicate was
                # `created_at__gte=one_hour_ago` (PG insertion time); CH
                # only carries `start_time` (span emission time). For this
                # task — deciding whether a session is still receiving
                # traffic — emission time is the correct signal anyway
                # (PG insertion lag would have produced false positives
                # under heavy ingest), so the migration is a strict
                # improvement, not a regression.
                with get_reader() as reader:
                    spans = reader.list_by_trace_ids(
                        [str(tid) for tid in trace_ids]
                    )
                if not spans:
                    continue

                # CH coverage check before driving a status/ended_at write
                # (codex P0 from the consolidated review). If any trace in
                # this session has zero spans in CH while it has spans in
                # PG (lag), skipping completion until the next tick is the
                # safe choice — partial coverage could make the "last
                # span" pick stale and trigger an early COMPLETED write.
                seen_trace_ids = {str(s.trace_id) for s in spans}
                requested = {str(tid) for tid in trace_ids}
                missing = requested - seen_trace_ids
                if missing:
                    # Codex final-review P2 (2026-05-26): a Trace row can
                    # legitimately have zero spans (e.g. trace created but
                    # ingestion failed). Without this PG existence gate,
                    # any such trace would keep `missing` non-empty
                    # forever and the session would never mark COMPLETED.
                    # Same pattern as `_aggregate_spans_by_trace_ids`.
                    pg_present = set(
                        str(tid)
                        for tid in ObservationSpan.no_workspace_objects.filter(
                            trace_id__in=missing,
                            deleted=False,
                        )
                        .values_list("trace_id", flat=True)
                        .distinct()
                    )
                    lagging = sorted(missing & pg_present)
                    if lagging:
                        logger.warning(
                            "ch_lag_skip_session_completion",
                            session_id=str(session.id),
                            missing_trace_ids_count=len(lagging),
                            missing_trace_ids_sample=lagging[:10],
                        )
                        continue
                    # All missing traces are legitimately spanless — drop
                    # them from the requested set and fall through to the
                    # completion check below.

                # Mirror Django's `order_by("-end_time").first()` ordering
                # exactly: PostgreSQL defaults NULLs FIRST under DESC, so a
                # span with end_time=None — i.e. an unfinished/streaming
                # span — was previously picked first and short-circuited
                # the completion check (status is "UNSET" → completion
                # skipped). Preserve that semantic by checking for any
                # null-end_time span first; a still-open span keeps the
                # session in ACTIVE.
                has_unfinished = any(s.end_time is None for s in spans)
                if has_unfinished:
                    # Old path: last_span would be a null-end-time span,
                    # whose status is typically UNSET → fails the OK/ERROR
                    # gate → no completion. Same effect here.
                    continue

                last_span = max(
                    spans,
                    key=lambda s: s.end_time,
                    default=None,
                )

                if last_span and last_span.status in ["OK", "ERROR"]:
                    recent_spans = any(
                        s.start_time >= one_hour_ago
                        for s in spans
                        if s.start_time is not None
                    )

                    if not recent_spans:
                        # Mark as completed or error based on last span status
                        if last_span.status == "ERROR":
                            session.status = SessionStatus.ERROR
                        else:
                            session.status = SessionStatus.COMPLETED

                        session.ended_at = last_span.end_time
                        session.save(update_fields=["status", "ended_at"])
                        completed_count += 1

            except Exception as e:
                logger.warning(
                    f"Failed to check session {session.id} for completion: {e}"
                )
                continue

        logger.info(f"Marked {completed_count} sessions as completed")
        return {"completed_count": completed_count}

    except Exception as e:
        logger.exception(f"Error in complete_sessions_with_trace_completion_task: {e}")
        raise
    finally:
        close_old_connections()


@temporal_activity(
    max_retries=2,
    time_limit=3600,
    queue="default",
)
def recalculate_project_user_analytics_task(project_id: str):
    """
    Recalculate all user analytics for a specific project.

    Useful for:
    - Initial migration/backfill
    - Fixing data inconsistencies
    - After bulk data operations

    Args:
        project_id: UUID of the project to recalculate

    Returns:
        Dict with project_id and updated_users count
    """
    from tracer.models.observation_span import EndUser
    from tracer.models.trace_session import TraceSession
    from tracer.services.clickhouse.v2 import get_reader

    try:
        close_old_connections()

        users = EndUser.objects.filter(project_id=project_id)
        updated_count = 0

        for user in users:
            try:
                with transaction.atomic():
                    # Try the analytics-service CH path first for session-
                    # level rollups it already provides.
                    ch_stats = _get_user_stats_from_ch(user, project_id)

                    if ch_stats is not None:
                        user.total_sessions = ch_stats["session_count"]
                        user.total_tokens_used = ch_stats["total_tokens"]
                        user.total_cost = Decimal(str(ch_stats["total_cost"]))

                        # trace_count via uniqExact(trace_id) in CH (was
                        # .filter(end_user=user).values("trace_id").
                        # distinct().count() in PG). project_id is the
                        # task argument; pass it through for tenant-scope
                        # defense-in-depth.
                        with get_reader() as reader:
                            user_agg = reader.aggregate_by_end_user(
                                str(user.id), project_id=str(project_id)
                            )
                        user.total_traces = user_agg["trace_count"]

                        if ch_stats.get("first_seen"):
                            user.first_seen = ch_stats["first_seen"]

                        # last_seen parity: prefer user_agg["last_seen"]
                        # (max(end_time)) over ch_stats["last_seen"]
                        # (max(start_time)) — matches Django's
                        # `.order_by("-end_time").first().end_time`
                        # semantics. ch_stats is the legacy fallback for
                        # cases where CHSpanReader has nothing.
                        if user_agg["last_seen"]:
                            user.last_seen = user_agg["last_seen"]
                        elif ch_stats.get("last_seen"):
                            user.last_seen = ch_stats["last_seen"]

                        user.save()
                        updated_count += 1
                        continue

                    # Fallback path: the analytics-service short-circuit
                    # didn't apply, but the per-user span aggregate still
                    # goes through CH via CHSpanReader.aggregate_by_end_user
                    # — one CH read covers Sum(tokens), Sum(cost),
                    # uniqExact(trace_id), min(start_time) (first_seen),
                    # max(end_time) (last_seen). Replaces 5 separate
                    # ObservationSpan queries.
                    session_count = TraceSession.objects.filter(end_user=user).count()

                    with get_reader() as reader:
                        user_agg = reader.aggregate_by_end_user(
                            str(user.id), project_id=str(project_id)
                        )

                    user.total_sessions = session_count
                    user.total_traces = user_agg["trace_count"]
                    user.total_tokens_used = user_agg["total_tokens"]
                    user.total_cost = Decimal(str(user_agg["cost"] or 0))

                    if user_agg["first_seen"]:
                        user.first_seen = user_agg["first_seen"]

                    if user_agg["last_seen"]:
                        user.last_seen = user_agg["last_seen"]

                    user.save()
                    updated_count += 1

            except Exception as e:
                logger.warning(
                    f"Failed to recalculate analytics for user {user.id}: {e}"
                )
                continue

        logger.info(
            f"Recalculated analytics for {updated_count} users in project {project_id}"
        )
        return {"project_id": project_id, "updated_users": updated_count}

    except Exception as e:
        logger.exception(f"Error in recalculate_project_user_analytics_task: {e}")
        raise
    finally:
        close_old_connections()
