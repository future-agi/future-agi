"""Bounded root-span reads for a page of traces (one experiment run's page).

The run page needs, for each trace of a page, only its root span's
name, start time and attributes, and its live span count. Reading every trace
in full (``read_trace_detail``: every span's content, one plan per trace) costs
one plan per row and every span of large agent traces. This module answers a
whole page with two queries, whatever its size:

1. a summary of each (project, trace) copy: its live span count, its newest
   version, the recorded root span's identity when that span is live, and the
   identity of its earliest live parentless span;
2. the content of the one chosen root per trace, by its exact physical
   identity (as ``build_content_query`` hydrates a trace).

The rules are those of ``read_trace_detail``: a span's latest version wins and
a tombstone is never resurrected; a trace id written into several projects of
the caller's scope is served from its most recently written copy; the root is
the recorded root span when it is live, else the earliest parentless span.
Reads share one wall deadline and fail closed (``TraceDetailReadUnavailable``)
on a read-budget or query failure. Reads never write.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any

from tracer.services.clickhouse.read_budget import (
    is_clickhouse_query_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.v2.trace_detail_reads import (
    _DETAIL_DEADLINE_MS,
    _MAX_ACCESSIBLE_PROJECTS,
    _QUERY_TIMEOUT_MS,
    _READ_SETTINGS,
    PhysicalSpanIdentity,
    TraceDetailReadUnavailable,
    _Analytics,
)

MAX_TRACES_PER_READ = 200
# a trace id lives in at most a few projects of one scope (a replay or a
# re-import); bound the summary rows so an unexpected fan-out fails closed
_MAX_COPIES_PER_TRACE = 16
# never matches: trace ids in the page are non-empty
_NO_ROOT = (("", ""),)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class TraceRoot:
    """One stored trace as a list page needs it."""

    project_id: str
    span_count: int
    root: dict[str, Any]


def build_summary_query(
    *, project_ids: tuple[str, ...], trace_ids: tuple[str, ...], roots: tuple
) -> tuple[str, dict[str, Any]]:
    return (
        """
        SELECT
            toString(project_id) AS project_id,
            trace_id,
            countIf(latest_is_deleted = 0) AS span_count,
            maxIf(latest_version, latest_is_deleted = 0) AS latest_version,
            countIf(latest_is_deleted = 0 AND is_recorded_root) AS recorded_roots,
            argMinIf(
                tuple(span_id, toUnixTimestamp64Micro(start_time)),
                start_time,
                latest_is_deleted = 0 AND is_recorded_root
            ) AS recorded_root,
            countIf(latest_is_deleted = 0 AND latest_parent_span_id = '')
                AS parentless_roots,
            argMinIf(
                tuple(span_id, toUnixTimestamp64Micro(start_time)),
                tuple(start_time, span_id),
                latest_is_deleted = 0 AND latest_parent_span_id = ''
            ) AS first_parentless
        FROM (
            SELECT
                project_id,
                trace_id,
                id AS span_id,
                start_time,
                argMax(is_deleted, _version) AS latest_is_deleted,
                max(_version) AS latest_version,
                ifNull(argMax(tuple(parent_span_id), _version).1, '')
                    AS latest_parent_span_id,
                (trace_id, id) IN %(run_page_roots)s AS is_recorded_root
            FROM spans
            PREWHERE project_id IN %(run_page_project_ids)s
              AND trace_id IN %(run_page_trace_ids)s
            GROUP BY project_id, trace_id, id, start_time
        ) AS latest_physical_spans
        GROUP BY project_id, trace_id
        HAVING span_count > 0
        ORDER BY trace_id ASC, project_id ASC
        LIMIT %(run_page_summary_limit)s
        """,
        {
            "run_page_project_ids": project_ids,
            "run_page_trace_ids": trace_ids,
            "run_page_roots": roots or _NO_ROOT,
            "run_page_summary_limit": len(trace_ids) * _MAX_COPIES_PER_TRACE + 1,
        },
    )


def build_root_content_query(
    identities: list[PhysicalSpanIdentity],
) -> tuple[str, dict[str, Any]]:
    return (
        """
        SELECT
            toString(grouped_project_id) AS project_id,
            grouped_trace_id AS trace_id,
            grouped_id AS id,
            grouped_start_time AS start_time,
            latest_parent_span_id AS parent_span_id,
            latest_name AS name,
            latest_attributes_extra AS span_attributes,
            latest_attrs_string AS attrs_string,
            latest_attrs_number AS attrs_number,
            latest_attrs_bool AS attrs_bool
        FROM (
            SELECT
                project_id AS grouped_project_id,
                trace_id AS grouped_trace_id,
                id AS grouped_id,
                start_time AS grouped_start_time,
                argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id,
                argMax(name, _version) AS latest_name,
                argMax(tuple(attributes_extra), _version).1
                    AS latest_attributes_extra,
                argMax(attrs_string, _version) AS latest_attrs_string,
                argMax(attrs_number, _version) AS latest_attrs_number,
                argMax(attrs_bool, _version) AS latest_attrs_bool,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM spans
            PREWHERE project_id IN %(run_root_project_ids)s
              AND trace_id IN %(run_root_trace_ids)s
              AND id IN %(run_root_span_ids)s
            WHERE toDate(start_time) IN %(run_root_dates)s
              AND (
                  toString(project_id), trace_id, id,
                  toUnixTimestamp64Micro(start_time)
              ) IN %(run_root_identities)s
            GROUP BY project_id, trace_id, id, start_time
        ) AS latest_root_spans
        WHERE latest_is_deleted = 0
        ORDER BY grouped_trace_id ASC
        LIMIT %(run_root_limit)s
        """,
        {
            "run_root_project_ids": tuple(
                dict.fromkeys(i.project_id for i in identities)
            ),
            "run_root_trace_ids": tuple(dict.fromkeys(i.trace_id for i in identities)),
            "run_root_span_ids": tuple(dict.fromkeys(i.span_id for i in identities)),
            "run_root_dates": tuple(
                dict.fromkeys(i.start_time.date() for i in identities)
            ),
            "run_root_identities": tuple(
                (i.project_id, i.trace_id, i.span_id, i.start_us) for i in identities
            ),
            "run_root_limit": len(identities) + 1,
        },
    )


def _identity(project_id: str, trace_id: str, pair: Any) -> PhysicalSpanIdentity | None:
    span_id, start_us = (tuple(pair) + (None, None))[:2] if pair else (None, None)
    if not span_id or start_us is None:
        return None
    return PhysicalSpanIdentity(
        project_id=project_id,
        trace_id=trace_id,
        span_id=str(span_id),
        start_time=_EPOCH + timedelta(microseconds=int(start_us)),
    )


def read_trace_roots(
    *,
    analytics: _Analytics,
    project_ids: Iterable[str],
    wanted: Iterable[tuple[str, str | None]],
    deadline_ms: int = _DETAIL_DEADLINE_MS,
) -> dict[str, TraceRoot]:
    """Each wanted (trace id, recorded root span id or None) found live in the
    caller's projects -> its served copy's project, live span count and root
    span row. A trace with no live span, or with no root, is absent."""

    scope = tuple(dict.fromkeys(str(p) for p in project_ids if p))
    pairs = list(dict.fromkeys((str(t), str(r) if r else None) for t, r in wanted if t))
    if not scope or not pairs:
        return {}
    if len(scope) > _MAX_ACCESSIBLE_PROJECTS:
        raise TraceDetailReadUnavailable("project_scope_too_large")
    trace_ids = tuple(dict.fromkeys(t for t, _ in pairs))
    if len(trace_ids) > MAX_TRACES_PER_READ:
        raise ValueError("trace root read exceeds its bounded page")
    deadline = monotonic() + deadline_ms / 1000

    def execute(query: str, params: dict[str, Any], *, max_rows: int) -> list[dict]:
        remaining_ms = int((deadline - monotonic()) * 1000)
        if remaining_ms < 25:
            raise TraceDetailReadUnavailable("deadline_exceeded")
        try:
            result = analytics.execute_ch_query(
                query,
                params,
                timeout_ms=min(_QUERY_TIMEOUT_MS, remaining_ms),
                settings={**_READ_SETTINGS, "max_result_rows": max_rows},
            )
        except Exception as exc:
            if is_read_budget_error(exc):
                raise TraceDetailReadUnavailable("read_budget_exceeded") from None
            if is_clickhouse_query_error(exc):
                raise TraceDetailReadUnavailable("clickhouse_query_failed") from None
            raise
        return list(result.data or [])

    query, params = build_summary_query(
        project_ids=scope,
        trace_ids=trace_ids,
        roots=tuple((t, r) for t, r in pairs if r),
    )
    limit = params["run_page_summary_limit"]
    summary = execute(query, params, max_rows=limit)
    if len(summary) >= limit:
        raise TraceDetailReadUnavailable("trace_copy_limit_exceeded")

    # the served copy of each trace: its most recently written project
    served: dict[str, dict[str, Any]] = {}
    for row in summary:
        trace_id = str(row.get("trace_id") or "")
        if trace_id not in trace_ids or not int(row.get("span_count") or 0):
            continue
        key = (int(row.get("latest_version") or 0), str(row.get("project_id")))
        best = served.get(trace_id)
        if best is None or key > (
            int(best.get("latest_version") or 0),
            str(best.get("project_id")),
        ):
            served[trace_id] = row

    chosen: dict[str, tuple[PhysicalSpanIdentity, int]] = {}
    for trace_id, row in served.items():
        project_id = str(row["project_id"])
        identity = None
        if int(row.get("recorded_roots") or 0):
            identity = _identity(project_id, trace_id, row.get("recorded_root"))
        if identity is None and int(row.get("parentless_roots") or 0):
            identity = _identity(project_id, trace_id, row.get("first_parentless"))
        if identity is not None:
            chosen[trace_id] = (identity, int(row["span_count"]))
    if not chosen:
        return {}

    query, params = build_root_content_query([i for i, _ in chosen.values()])
    rows = execute(query, params, max_rows=params["run_root_limit"])
    found: dict[str, TraceRoot] = {}
    for root in rows:
        trace_id = str(root.get("trace_id") or "")
        identity, span_count = chosen.get(trace_id, (None, 0))
        if identity is None or str(root.get("id") or "") != identity.span_id:
            continue
        found[trace_id] = TraceRoot(
            project_id=identity.project_id, span_count=span_count, root=dict(root)
        )
    return found


__all__ = [
    "MAX_TRACES_PER_READ",
    "TraceRoot",
    "build_root_content_query",
    "build_summary_query",
    "read_trace_roots",
]
