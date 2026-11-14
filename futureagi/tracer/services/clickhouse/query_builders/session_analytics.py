"""
Session Analytics Query Builder for ClickHouse.

Provides queries for session-level and user-level aggregate metrics,
replacing heavy PG queries on ObservationSpan, Trace, and TraceSession
tables with efficient ClickHouse GROUP BY queries on the denormalized
``spans`` table.
"""

from typing import Any

from tracer.services.clickhouse.query_builders.base import NIL_UUID, BaseQueryBuilder
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)


class SessionAnalyticsQueryBuilder(BaseQueryBuilder):
    """Build queries for session and user analytics aggregations.

    All queries operate on the ``spans`` table which denormalizes
    trace context (including ``trace_session_id`` and ``end_user_id``)
    into every span row.

    Args:
        project_id: Project UUID string.
    """

    TABLE = "spans"

    def __init__(self, project_id: str, **kwargs: Any) -> None:
        super().__init__(project_id, **kwargs)

    def build(self) -> tuple[str, dict[str, Any]]:
        """Not used directly -- call specific build_* methods instead."""
        raise NotImplementedError(
            "Use build_session_metrics_query, build_session_navigation_query, "
            "build_user_stats_query, or build_first_last_message_query instead."
        )

    # ------------------------------------------------------------------
    # Session metrics (per-session aggregates)
    # ------------------------------------------------------------------

    def build_session_metrics_query(
        self, session_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Build a query returning per-session aggregate metrics.

        Args:
            session_ids: List of session ID strings to aggregate.

        Returns:
            A ``(query_string, params)`` tuple.
        """
        params = dict(self.params)
        params["session_ids"] = session_ids

        # P3b step1.5 (DESIGN §3 / id_remap_sql): the input `session_ids` are OLD
        # curated `TraceSession.id`s (the caller passes `str(session.id)`). A
        # cross-cutover straddler's NEW (deterministic-id) spans carry
        # `trace_session_id = new_id`, so resolve each span's `trace_session_id`
        # new→old through `trace_session_id_remap` BEFORE the membership check and
        # the GROUP BY, so old + new spans aggregate under ONE (old) session key.
        # The resolved id is projected AS `trace_session_id`, so the result-row
        # contract is unchanged. The project predicate stays on the bare inner
        # scan; only the identity match + grouping move to the resolved layer.
        # `resolved_id_expr` is the zero-uuid-guarded map (NOT a COALESCE — an
        # unmatched LEFT JOIN fills `old_id` with the zero-uuid, not NULL). Pre-
        # flip NO span matches a `new_id` → resolved id == own id → byte-identical
        # no-op (gate B).
        remap_join = remap_left_join("rs.trace_session_id", "trace_session_id_remap")
        resolved_ts = resolved_id_expr("rs.trace_session_id")
        query = f"""
        SELECT
            trace_session_id,
            min(start_time) AS first_trace_time,
            max(start_time) AS last_trace_time,
            count(DISTINCT trace_id) AS trace_count,
            sum(total_tokens) AS total_tokens,
            sum(cost) AS total_cost,
            min(start_time) AS started_at,
            max(COALESCE(end_time, start_time)) AS ended_at
        FROM (
            SELECT
                {resolved_ts} AS trace_session_id,
                rs.trace_id AS trace_id,
                rs.start_time AS start_time,
                rs.end_time AS end_time,
                rs.total_tokens AS total_tokens,
                rs.cost AS cost
            FROM (
                SELECT trace_session_id, trace_id, start_time, end_time,
                       total_tokens, cost
                FROM {self.TABLE}
                {self.project_where()}
            ) AS rs
            {remap_join}
        )
        WHERE trace_session_id IN %(session_ids)s
        GROUP BY trace_session_id
        """
        return query, params

    # ------------------------------------------------------------------
    # Session navigation (all sessions with metrics)
    # ------------------------------------------------------------------

    def build_session_navigation_query(self) -> tuple[str, dict[str, Any]]:
        """Build a query returning all sessions with their metrics for navigation.

        Returns:
            A ``(query_string, params)`` tuple.

        P3b step1.5 — DORMANT, NOT id-remap-resolved (intentional). This CH query
        ERRORS on the v2 spans schema (CH 25.3): `trace_session_id` is
        `Nullable(UUID)` and the committed `AND trace_session_id != ''` raises
        `Code 376 Cannot parse uuid : converting '' to UUID`, so its only caller
        (`tracer/utils/session.py::_try_session_navigation_ch`) always hits the
        `except` and falls back to the PG navigation path. A read that cannot
        execute can produce neither a gate-B byte-identical proof nor a gate-C
        straddler-unify, so adding remap resolution here would be untestable dead
        SQL. Mirrors the end_user `dashboard.py` deferral (commit 9bcbff7e7).
        SEQUENCING FLAG (human): when the `!= ''`→`Nullable(UUID)` bug is fixed to
        make this CH path live, it MUST simultaneously resolve `trace_session_id`
        new→old (mirror `build_session_metrics_query` above) BEFORE step2's
        ingestion flip — else session prev/next-nav splits a cross-cutover
        straddler. The transitively-reached `build_first_last_message_query` (fed
        this query's session ids) is dormant for the same reason.
        """
        params = dict(self.params)

        # trace_session_id is UUID; comparing to '' makes CH coerce '' -> UUID
        # and raise Code 376. Use IS NOT NULL; the NIL-UUID line still
        # excludes the "no session" sentinel.
        query = f"""
        SELECT
            trace_session_id,
            min(start_time) AS started_at,
            max(COALESCE(end_time, start_time)) AS ended_at,
            count(DISTINCT trace_id) AS trace_count,
            sum(total_tokens) AS total_tokens,
            sum(cost) AS total_cost
        FROM {self.TABLE}
        {self.project_where()}
          AND trace_session_id IS NOT NULL
          AND trace_session_id != toUUID('{NIL_UUID}')
        GROUP BY trace_session_id
        ORDER BY started_at DESC
        """
        return query, params

    # ------------------------------------------------------------------
    # User stats (per-user aggregates)
    # ------------------------------------------------------------------

    def build_user_stats_query(self, user_id: str) -> tuple[str, dict[str, Any]]:
        """Build a query returning aggregate stats for a specific user.

        Args:
            user_id: The end-user ID string.

        Returns:
            A ``(query_string, params)`` tuple.
        """
        params = dict(self.params)
        params["user_id"] = user_id

        # P3b step1.5 — DUAL id-remap (DESIGN §3 / id_remap_sql): `user_id` here is
        # the OLD curated id (callers pass `str(EndUser.id)` / `str(user.id)` —
        # tracer/tasks/session.py, tracer/utils/session.py). This read filters by
        # the user AND reports `count(DISTINCT trace_session_id)`, so a cross-
        # cutover straddler would split on BOTH axes. Resolve BOTH columns new→old:
        # `end_user_id` (match the OLD id) so old + new spans roll into this
        # per-user stat as ONE user, AND `trace_session_id` so the distinct-session
        # count treats a straddler's old+new session ids as ONE session (else
        # `session_count` over-counts). The two joins hang off the SAME inner scan
        # `rs` and so MUST carry DISTINCT aliases (the default `id_remap` would
        # collide) — `eu_remap` / `ts_remap`. The project scope stays on the bare
        # inner scan. `resolved_id_expr` is the zero-uuid-guarded map (NOT a
        # COALESCE — an unmatched LEFT JOIN fills `old_id` with the zero-uuid; see
        # id_remap_sql). Pre-flip NO span matches either `new_id`, so both resolved
        # ids == own id and this is a no-op (gate B).
        eu_join = remap_left_join("rs.end_user_id", "end_user_id_remap", "eu_remap")
        ts_join = remap_left_join(
            "rs.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        resolved_eu = resolved_id_expr("rs.end_user_id", "eu_remap")
        resolved_ts = resolved_id_expr("rs.trace_session_id", "ts_remap")
        query = f"""
        SELECT
            count(DISTINCT trace_session_id) AS session_count,
            sum(total_tokens) AS total_tokens,
            sum(cost) AS total_cost,
            min(start_time) AS first_seen,
            max(start_time) AS last_seen
        FROM (
            SELECT
                {resolved_eu} AS end_user_id,
                {resolved_ts} AS trace_session_id,
                rs.total_tokens AS total_tokens,
                rs.cost AS cost,
                rs.start_time AS start_time
            FROM (
                SELECT end_user_id, trace_session_id, total_tokens, cost, start_time
                FROM {self.TABLE}
                {self.project_where()}
            ) AS rs
            {eu_join}
            {ts_join}
        )
        WHERE end_user_id = %(user_id)s
        """
        return query, params

    # ------------------------------------------------------------------
    # First/last message per session
    # ------------------------------------------------------------------

    def build_first_last_message_query(
        self, session_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Build queries returning the first and last input/output per session.

        Uses ClickHouse's ``LIMIT 1 BY`` to efficiently get the first and
        last root spans per session.

        Args:
            session_ids: List of session ID strings.

        Returns:
            A ``(first_query, last_query, params)`` tuple. Both queries share
            the same params dict.

        P3b step1.5 — DORMANT, NOT id-remap-resolved: the only caller
        (`_try_session_navigation_ch`) runs `build_session_navigation_query`
        FIRST, which errors on the v2 schema (see that method) and short-circuits
        to PG, so this never executes on CH. Resolve it together with the nav
        query when that path is made live (same SEQUENCING FLAG).
        """
        params = dict(self.params)
        params["session_ids"] = session_ids

        first_query = f"""
        SELECT trace_session_id, input, output
        FROM {self.TABLE}
        {self.project_where()}
          AND trace_session_id IN %(session_ids)s
          AND (parent_span_id IS NULL OR parent_span_id = '')
        ORDER BY start_time ASC
        LIMIT 1 BY trace_session_id
        """

        last_query = f"""
        SELECT trace_session_id, input, output
        FROM {self.TABLE}
        {self.project_where()}
          AND trace_session_id IN %(session_ids)s
          AND (parent_span_id IS NULL OR parent_span_id = '')
        ORDER BY start_time DESC
        LIMIT 1 BY trace_session_id
        """

        return first_query, last_query, params
