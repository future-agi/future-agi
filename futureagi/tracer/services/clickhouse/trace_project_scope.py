"""Read-only project scoping for trace-attached ClickHouse rows."""

from __future__ import annotations

import re

_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRACE_TABLE = "traces"


def latest_live_project_traces_sql(*, project_ids_param: str = "project_ids") -> str:
    """Return every unambiguous latest-live trace in the requested projects.

    This is the single-scan counterpart to :func:`latest_live_trace_projects_sql`.
    It is appropriate when the caller already has to read a large source slice:
    deriving trace candidates by rereading that same slice can exceed a shared
    ``max_rows_to_read`` budget even though both individual reads are healthy.

    The relation reads only the narrow ``(project_id, id, is_deleted, _version)``
    trace columns behind the ``(project_id, id)`` primary-key prefix.  Latest-state
    and ambiguous-ID handling are identical to the candidate-scoped helper.
    """

    if not _PARAM_NAME_RE.fullmatch(project_ids_param):
        raise ValueError("Invalid ClickHouse parameter name")

    return f"""
        SELECT
            trace_id,
            tupleElement(latest_state, 1) AS project_id
        FROM (
            SELECT
                trace_project_scan.id AS trace_id,
                uniqExact(trace_project_scan.project_id) AS project_identity_count,
                argMax(
                    tuple(
                        trace_project_scan.project_id,
                        trace_project_scan.is_deleted
                    ),
                    trace_project_scan._version
                ) AS latest_state
            FROM {_TRACE_TABLE} AS trace_project_scan
            PREWHERE trace_project_scan.project_id IN %({project_ids_param})s
            GROUP BY trace_project_scan.id
        ) AS latest_trace_project_state
        WHERE project_identity_count = 1
          AND tupleElement(latest_state, 2) = 0
    """


def latest_live_trace_projects_sql(
    *,
    candidate_trace_ids_sql: str,
    project_ids_param: str = "project_ids",
) -> str:
    """Return a project-bounded latest-live relation from direct-write traces.

    The caller must provide a finite, tenant/time/source-scoped trace-ID
    candidate query. This prevents a project-wide trace aggregation when only
    a small eval slice is relevant. Trace project membership is immutable, so
    applying both project and ID predicates before ``argMax`` is exact and
    aligned with ``ORDER BY (project_id, id)``. A trace ID reused across the
    requested projects is ambiguous and is therefore excluded. The live
    predicate remains outside the version collapse so a tombstone cannot
    resurrect an old row.
    """

    if not _PARAM_NAME_RE.fullmatch(project_ids_param):
        raise ValueError("Invalid ClickHouse parameter name")
    if not candidate_trace_ids_sql.strip():
        raise ValueError("A bounded trace-ID candidate query is required")

    return f"""
        SELECT
            trace_id,
            tupleElement(latest_state, 1) AS project_id
        FROM (
            SELECT
                trace_project_scan.id AS trace_id,
                uniqExact(trace_project_scan.project_id) AS project_identity_count,
                argMax(
                    tuple(
                        trace_project_scan.project_id,
                        trace_project_scan.is_deleted
                    ),
                    trace_project_scan._version
                ) AS latest_state
            FROM {_TRACE_TABLE} AS trace_project_scan
            INNER JOIN ({candidate_trace_ids_sql}) AS bounded_trace_candidates
              ON trace_project_scan.id = bounded_trace_candidates.trace_id
            PREWHERE trace_project_scan.project_id IN %({project_ids_param})s
            GROUP BY trace_project_scan.id
        ) AS latest_trace_project_state
        WHERE project_identity_count = 1
          AND tupleElement(latest_state, 2) = 0
    """


__all__ = ["latest_live_project_traces_sql", "latest_live_trace_projects_sql"]
