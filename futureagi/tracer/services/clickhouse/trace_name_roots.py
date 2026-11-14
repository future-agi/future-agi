"""Resolve a request's trace-name roots once, then bind them per statement.

A span-mode ``trace_name`` leaf compiles to a lookup of its traces' live
roots: ``(project_id, trace_id) IN (SELECT project_id, trace_id FROM spans
FINAL PREWHERE <project and hour window> WHERE <live root named so>)``. A
reader that issues many statements per request (graph partitions, span-list
classifier batches, Users walk slices and certifications) used to repeat that
lookup in every statement. Under FINAL with use_skip_indexes_if_final=0 the
``trace_id`` bloom index prunes nothing, so each repeat read the whole request
window's root columns again.

Such a reader instead resolves each leaf's matching ``(project_id,
trace_id)`` set ONCE per request, over the same window and project scope
(``resolve_trace_name_root_sets``), and binds it into every statement in
place of the lookup (``bind_trace_name_root_sets``). The set is capped
(``TRACE_NAME_ROOT_SET_MAX_ROOTS`` roots, ``TRACE_NAME_ROOT_SET_MAX_BYTES``
of trace ids): above that the request is refused as too broad
(``TraceNameFilterTooBroad``, HTTP 400) instead of building an unbounded set.

Semantics are unchanged: a statement's lookup returned the same window's
matching roots (narrowed to the statement's candidate traces, which the
statement's own rows already are), and the set is one FINAL snapshot taken at
the start of the request instead of one per statement.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.trace_name_sql import (
    TRACE_NAME_ROOT_SET_MAX_BYTES,
    TRACE_NAME_ROOT_SET_MAX_ROOTS,
    TraceNameRootSet,
    TraceNameRootSets,
    bind_trace_name_root_sets,
    tagged_trace_name_root_lookups,
    trace_name_root_set_params,
)

__all__ = [
    "TRACE_NAME_FILTER_TOO_BROAD_CODE",
    "TRACE_NAME_FILTER_TOO_BROAD_MESSAGE",
    "TraceNameFilterTooBroad",
    "TraceNameRootSet",
    "TraceNameRootSets",
    "bind_trace_name_root_sets",
    "has_trace_name_leaf",
    "trace_name_leaf_count",
    "resolve_trace_name_root_sets",
    "tagged_trace_name_root_lookups",
    "trace_name_root_set_params",
]


TRACE_NAME_FILTER_TOO_BROAD_CODE = "trace_name_filter_too_broad"
TRACE_NAME_FILTER_TOO_BROAD_MESSAGE = (
    f"This Trace Name filter matches more than {TRACE_NAME_ROOT_SET_MAX_ROOTS:,} "
    "traces in the selected time range. Narrow the time range or the trace "
    "name and retry."
)

_PER_PROJECT_OVERHEAD_BYTES = 64


class TraceNameFilterTooBroad(UnsupportedFilterShapeError):
    """A trace_name leaf matches more roots than one request may bind.

    A subclass of ``UnsupportedFilterShapeError`` so every view that already
    answers that error with a 400 refuses it too; the list and graph views
    answer it first with its own message and code.
    """

    code = TRACE_NAME_FILTER_TOO_BROAD_CODE

    def __init__(self, message: str = TRACE_NAME_FILTER_TOO_BROAD_MESSAGE) -> None:
        super().__init__(message)


def _trace_name_leaves(filters: Iterable[dict[str, Any]] | None) -> list[dict]:
    return [
        item
        for item in filters or ()
        if isinstance(item, dict)
        and (item.get("column_id") or item.get("columnId")) == "trace_name"
    ]


def has_trace_name_leaf(filters: Iterable[dict[str, Any]] | None) -> bool:
    return bool(_trace_name_leaves(filters))


def trace_name_leaf_count(filters: Iterable[dict[str, Any]] | None) -> int:
    """At most this many resolution statements (one per distinct leaf)."""

    return len(_trace_name_leaves(filters))


def _group_roots(rows: Iterable[Any]) -> TraceNameRootSet:
    by_project: dict[str, set[str]] = {}
    for row in rows:
        if isinstance(row, Mapping):
            project_id, trace_id = row["project_id"], row["trace_id"]
        else:
            project_id, trace_id = row
        by_project.setdefault(str(project_id), set()).add(str(trace_id))
    return tuple(
        (project_id, tuple(sorted(trace_ids)))
        for project_id, trace_ids in sorted(by_project.items())
    )


def _rendered_bytes(roots: TraceNameRootSet) -> int:
    # Each trace id renders as a quoted literal plus a separator; each project
    # adds its own comparison and the IN.
    return sum(
        _PER_PROJECT_OVERHEAD_BYTES
        + sum(len(trace_id.encode("utf-8")) + 4 for trace_id in trace_ids)
        for _project_id, trace_ids in roots
    )


def resolve_trace_name_root_sets(
    filters: Iterable[dict[str, Any]] | None,
    *,
    start: datetime,
    end: datetime,
    execute: Callable[[str, dict[str, Any]], Sequence[Any]],
    project_id: str | None = None,
    project_ids: Sequence[str] | None = None,
) -> dict[str, TraceNameRootSet]:
    """One statement per distinct span-mode trace_name leaf, at most.

    Each leaf compiles exactly as a statement compiles it, without candidate
    scoping, over the request window ``[start, end)`` and the reader's
    project scope: ``project_ids`` for an organization read, else
    ``project_id``. ``execute(sql, params)`` runs a statement with the
    reader's own executor and settings and returns its rows, as
    ``(project_id, trace_id)`` sequences or mappings with those keys.

    Raises ``TraceNameFilterTooBroad`` when the leaves together match more
    roots than one request may bind. Returns ``{}`` when the filters have no
    such leaf.
    """

    leaves = _trace_name_leaves(filters)
    if not leaves:
        return {}
    # Lazy import: the v2 builder imports the query builders this module
    # serves.
    from tracer.services.clickhouse.v2.query_builders.filters import (
        ClickHouseFilterBuilderV2,
    )

    if project_ids is None and not project_id:
        return {}
    scope: dict[str, Any] = (
        {"project_ids": [str(project) for project in project_ids]}
        if project_ids is not None
        else {"project_id": str(project_id)}
    )
    lookups: dict[str, tuple[str, dict[str, Any]]] = {}
    for item in leaves:
        builder = ClickHouseFilterBuilderV2(
            table="spans",
            query_mode=ClickHouseFilterBuilderV2.QUERY_MODE_SPAN,
            score_date_scope=False,
            span_date_scope=False,
            **scope,
        )
        try:
            predicate, params = builder.translate([item])
        except UnsupportedFilterShapeError:
            # Each statement refuses the same leaf itself.
            continue
        for key, _start, _end, subquery in tagged_trace_name_root_lookups(
            predicate or ""
        ):
            lookups.setdefault(key, (subquery, params))

    # The cap holds for the request's sets together: a statement carrying
    # several leaves binds all of them, and it must still parse.
    resolved: dict[str, TraceNameRootSet] = {}
    total_roots = 0
    total_bytes = 0
    for key, (subquery, params) in lookups.items():
        rows = execute(
            # A code-owned lookup over bound parameters.
            "SELECT DISTINCT project_id, trace_id "  # noqa: S608
            f"FROM ({subquery}) LIMIT {TRACE_NAME_ROOT_SET_MAX_ROOTS + 1}",
            {**params, **scope, "start_date": start, "end_date": end},
        )
        roots = _group_roots(list(rows or ()))
        total_roots += sum(len(trace_ids) for _project, trace_ids in roots)
        total_bytes += _rendered_bytes(roots)
        if (
            total_roots > TRACE_NAME_ROOT_SET_MAX_ROOTS
            or total_bytes > TRACE_NAME_ROOT_SET_MAX_BYTES
        ):
            raise TraceNameFilterTooBroad()
        resolved[key] = roots
    return resolved
