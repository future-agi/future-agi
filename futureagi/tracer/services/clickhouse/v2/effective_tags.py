"""Finite latest-state tag relations shared by discovery and filter classifiers.

Only identities returned by a project/time-fenced CH read reach PostgreSQL.
Overflow is unavailable, never a truncated membership set. Tag leaves are exact
residuals, so a source-only seed cannot exclude a manually labelled candidate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.entity_tag_reads import MAX_TAG_READ_ROWS, enrich_entity_tags
from tracer.services.entity_tags import TagReadUnavailable

TAG_RELATION_TYPE = (
    "Array(Tuple(String, String, String, Int64, String, String, UInt8, Array(String)))"
)
TAG_SOURCE_SETTINGS = {
    "max_threads": 2,
    "max_bytes_to_read": 256 * 1024 * 1024,
    "max_memory_usage": 256 * 1024 * 1024,
    "max_result_bytes": 8 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}


@dataclass(frozen=True)
class EffectiveTagRelation:
    """Unbound compiler parameter; must be prepared before executing SQL."""

    project_ids: tuple[str, ...]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def read_effective_tag_rows(
    execute: Callable[..., Any],
    *,
    project_ids: tuple[str, ...],
    window_start: datetime,
    window_end: datetime,
    deadline: ReadDeadline,
    trace_ids: tuple[str, ...] | None = None,
    root_project_version: str | None = None,
) -> list[dict[str, Any]]:
    """Collapse the complete physical hour before applying mutable predicates.

    A single tuple argMax selects a coherent winner, including NULL parent/tags.
    Curated trace tags are a bounded legacy fallback only, suppressed by even an
    empty manual assignment. The root flag uses the list's canonical root order.
    """
    if (
        getattr(
            getattr(execute, "__self__", None), "supports_per_query_read_settings", True
        )
        is False
    ):
        raise TagReadUnavailable("tag_source_unavailable")
    start, end = _utc(window_start), _utc(window_end)
    if start >= end or not project_ids:
        raise TagReadUnavailable("tag_scope_required")
    if trace_ids is not None and (not trace_ids or len(trace_ids) > 512):
        raise TagReadUnavailable("tag_candidate_budget_exceeded")
    hour_start = start.replace(minute=0, second=0, microsecond=0)
    hour_end = end.replace(minute=0, second=0, microsecond=0)
    if hour_end < end:
        hour_end += timedelta(hours=1)
    params = {
        "tag_projects": project_ids,
        "tag_start": start,
        "tag_end": end,
        "tag_hour_start": hour_start,
        "tag_hour_end": hour_end,
        "tag_row_limit": MAX_TAG_READ_ROWS + 1,
    }
    candidates = ""
    if trace_ids is not None:
        candidates = "AND trace_id IN %(tag_trace_ids)s"
        params["tag_trace_ids"] = trace_ids
    query = f"""
        WITH tag_live AS (
            SELECT project_id, trace_id, id, observation_type, service_name,
                toStartOfHour(start_time) AS start_hour,
                argMax(tuple(start_time, parent_span_id, is_deleted, tags,
                             attrs_string, attributes_extra, _version, project_version_id), _version) AS winner,
                winner.1 AS selected_start_time
            FROM spans
            PREWHERE project_id IN %(tag_projects)s
              AND start_time >= %(tag_hour_start)s
              AND start_time < %(tag_hour_end)s
              {candidates}
            GROUP BY project_id, observation_type, service_name,
                     toStartOfHour(start_time), trace_id, id
            HAVING winner.3 = 0
               AND selected_start_time >= %(tag_start)s
               AND selected_start_time < %(tag_end)s
        ), tag_legacy AS (
            SELECT project_id, toString(id) AS trace_id,
                argMax(tuple(tags, is_deleted), _version) AS legacy
            FROM traces
            WHERE project_id IN %(tag_projects)s
              AND (project_id, toString(id)) IN (SELECT project_id, trace_id FROM tag_live)
            GROUP BY project_id, id
        )
        SELECT toString(l.project_id) AS project_id, l.trace_id AS trace_id,
            l.id AS id, l.observation_type AS observation_type,
            l.service_name AS service_name, l.start_hour AS start_hour,
            l.winner.1 AS start_time, l.winner.2 AS parent_span_id,
            l.winner.4 AS tags, l.winner.5 AS attrs_string,
            l.winner.6 AS attributes_extra, l.winner.7 AS _version,
            l.winner.8 AS project_version_id,
            if(ifNull(l.winner.2, '') = '' AND g.legacy.2 = 0, g.legacy.1, '[]') AS trace_tags
        FROM tag_live AS l
        LEFT JOIN tag_legacy AS g ON l.project_id = g.project_id AND l.trace_id = g.trace_id
        LIMIT %(tag_row_limit)s
        SETTINGS max_bytes_to_read = 268435456, max_result_bytes = 8388608,
                 max_memory_usage = 268435456, read_overflow_mode = 'throw',
                 result_overflow_mode = 'throw', timeout_overflow_mode = 'throw'
    """
    result = execute(
        query,
        params,
        timeout_ms=deadline.remaining_ms(),
        server_execution_cap_ms=deadline.remaining_ms(),
        settings={**TAG_SOURCE_SETTINGS, "max_result_rows": MAX_TAG_READ_ROWS + 1},
    )
    deadline.remaining_ms()
    rows = [dict(row) for row in result.data or []]
    if len(rows) > MAX_TAG_READ_ROWS:
        # Cursor discovery can retry a narrower unconsumed time segment.
        raise ReadDeadlineExceeded("tag_candidate_budget_exceeded")
    for row in rows:
        for key in ("start_time", "start_hour"):
            if not isinstance(row[key], datetime):
                raise TagReadUnavailable("tag_identity_incomplete")
            row[key] = _utc(row[key])
    enrich_entity_tags(rows, kind="span", deadline=deadline)
    roots: dict[tuple[str, str], dict[str, Any]] = {}

    def order(row):
        return (
            row["start_time"],
            row["id"],
            row["observation_type"],
            row["service_name"],
            row["start_hour"],
        )

    for row in rows:
        key = (row["project_id"], row["trace_id"])
        if (
            row["parent_span_id"] in (None, "")
            and (
                root_project_version is None
                or str(row["project_version_id"]) == str(root_project_version)
            )
            and (key not in roots or order(row) > order(roots[key]))
        ):
            roots[key] = row
    for row in rows:
        row["is_tag_root"] = roots.get((row["project_id"], row["trace_id"])) is row
    return rows


def prepare_effective_tag_params(
    execute: Callable[..., Any],
    params: dict[str, Any],
    *,
    deadline: ReadDeadline,
    allow_window_scan: bool = False,
) -> dict[str, Any]:
    """Bind finite candidate relations before the classifier's LIMIT/count.

    Namespaced parameters (organization residual branches) retain their own
    project scope; never look up all assignments in a project. Non-tag queries
    return the original dictionary and perform no extra reads.
    """
    pending = {
        key: value
        for key, value in params.items()
        if isinstance(value, EffectiveTagRelation)
    }
    if not pending:
        return params
    start = params.get("candidate_start_date", params.get("start_date"))
    end = params.get("candidate_end_date", params.get("end_date"))
    ids = params.get("candidate_trace_ids", params.get("candidate_span_trace_ids"))
    if (
        not isinstance(start, datetime)
        or not isinstance(end, datetime)
        or (not ids and not allow_window_scan)
    ):
        raise TagReadUnavailable("tag_scope_required")
    bound = dict(params)
    cache = {}
    for name, request in pending.items():
        if request.project_ids not in cache:
            rows = read_effective_tag_rows(
                execute,
                project_ids=request.project_ids,
                trace_ids=tuple(ids) if ids else None,
                window_start=start,
                window_end=end,
                deadline=deadline,
                root_project_version=params.get("project_version_id"),
            )
            cache[request.project_ids] = [
                (
                    row["project_id"],
                    row["trace_id"],
                    row["id"],
                    int(row["start_hour"].timestamp()),
                    row["observation_type"],
                    row["service_name"],
                    int(row["is_tag_root"]),
                    [tag["name"] for tag in row["tags"]],
                )
                for row in rows
            ]
        bound[name] = cache[request.project_ids]
    return bound


def effective_tag_values(
    execute: Callable[..., Any],
    *,
    project_ids: tuple[str, ...],
    metric_name: str,
    window_start: datetime,
    window_end: datetime,
    deadline: ReadDeadline,
    search: str = "",
    after: str | None = None,
    limit: int,
) -> tuple[str, ...]:
    rows = read_effective_tag_rows(
        execute,
        project_ids=project_ids,
        window_start=window_start,
        window_end=window_end,
        deadline=deadline,
    )
    values = {
        tag["name"]
        for row in rows
        if metric_name == "tag" or row["is_tag_root"]
        for tag in row["tags"]
        if (after is None or tag["name"] > after)
        and (not search or search.casefold() in tag["name"].casefold())
    }
    deadline.remaining_ms()
    return tuple(sorted(values)[:limit])
