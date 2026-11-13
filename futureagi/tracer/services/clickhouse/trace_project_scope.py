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


def live_trace_project_sets_sql(
    *,
    candidate_trace_ids_sql: str,
    project_ids_param: str = "project_ids",
    configured_project_ids_param: str | None = None,
) -> str:
    """Return, per candidate trace, every requested project holding it live.

    Unlike :func:`latest_live_trace_projects_sql`, a trace ID present in more
    than one requested project is kept, with all of those projects in
    ``live_project_ids``. A caller that may read every requested project can
    then decide membership itself (for example by a recorded project id)
    instead of losing the row. Latest state is judged per ``(trace, project)``
    so a tombstone in one project cannot hide or resurrect another project's
    copy. The caller must provide a finite trace-ID candidate query.

    With ``configured_project_ids_param`` the relation also returns
    ``configured_live``: 1 exactly when :func:`latest_live_trace_projects_sql`
    over those projects would return the trace (rows of it, live or
    tombstoned, in exactly one of them, and that copy's latest version live).
    They must be a subset of ``project_ids_param``, which is the only scope
    the relation reads.

    The candidate IDs are an ``IN`` set next to the project list, not a join:
    ClickHouse prunes the ``(project_id, id)`` primary key with both, so the
    scan reads the granules of the candidate traces only. A join reads every
    trace of every requested project, which grows with the workspace.
    """

    names = [project_ids_param, configured_project_ids_param]
    if not all(_PARAM_NAME_RE.fullmatch(name) for name in names if name is not None):
        raise ValueError("Invalid ClickHouse parameter name")
    if not candidate_trace_ids_sql.strip():
        raise ValueError("A bounded trace-ID candidate query is required")

    configured_live = ""
    if configured_project_ids_param is not None:
        configured = f"project_id IN %({configured_project_ids_param})s"
        configured_live = f""",
            toUInt8(
                countIf({configured}) = 1
                AND countIf({configured} AND latest_is_deleted = 0) = 1
            ) AS configured_live"""
    return f"""
        SELECT
            trace_id,
            groupUniqArrayIf(project_id, latest_is_deleted = 0)
                AS live_project_ids{configured_live}
        FROM (
            SELECT
                trace_project_scan.id AS trace_id,
                trace_project_scan.project_id AS project_id,
                argMax(
                    trace_project_scan.is_deleted,
                    trace_project_scan._version
                ) AS latest_is_deleted
            FROM {_TRACE_TABLE} AS trace_project_scan
            PREWHERE trace_project_scan.project_id IN %({project_ids_param})s
              AND trace_project_scan.id IN (
                  SELECT trace_id FROM ({candidate_trace_ids_sql})
              )
            GROUP BY trace_project_scan.id, trace_project_scan.project_id
        ) AS trace_project_state
        GROUP BY trace_id
    """


__all__ = [
    "latest_live_project_traces_sql",
    "latest_live_trace_projects_sql",
    "live_trace_project_sets_sql",
]
