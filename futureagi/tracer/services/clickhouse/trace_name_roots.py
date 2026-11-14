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

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.trace_name_sql import (
    TRACE_NAME_ROOT_SET_MAX_BYTES,
    TRACE_NAME_ROOT_SET_MAX_ROOTS,
)

# One resolved leaf: ((project_id, (trace_id, ...)), ...), grouped by project
# and sorted, so a statement binds each project's trace ids once.
TraceNameRootSet = tuple[tuple[str, tuple[str, ...]], ...]
TraceNameRootSets = Mapping[str, TraceNameRootSet]

TRACE_NAME_FILTER_TOO_BROAD_CODE = "trace_name_filter_too_broad"
TRACE_NAME_FILTER_TOO_BROAD_MESSAGE = (
    f"This Trace Name filter matches more than {TRACE_NAME_ROOT_SET_MAX_ROOTS:,} "
    "traces in the selected time range. Narrow the time range or the trace "
    "name and retry."
)

# ``(project_id, trace_id) IN (SELECT ... /* trace-name roots <key> */)``, as
# ``ClickHouseFilterBuilder`` emits it for a span-mode trace_name leaf: the tag
# closes the subquery.
_LOOKUP_OPENING = "(project_id, trace_id) IN (SELECT project_id, trace_id FROM spans FINAL "
_LOOKUP_TAG = re.compile(r"/\* trace-name roots ([0-9a-f]{16}) \*/\Z")
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


def _closing_paren(sql: str, open_index: int) -> int:
    """The index of the ``)`` closing the ``(`` at ``open_index``.

    Quoted text is skipped. The lookup is compiled with bound parameters, so
    it carries no user text, but a placeholder ``%(name)s`` and SQL string
    literals such as ``''`` must not miscount.
    """

    depth = 0
    quote = ""
    index = open_index
    length = len(sql)
    while index < length:
        char = sql[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError("unbalanced trace-name root lookup")


def tagged_trace_name_root_lookups(sql: str) -> list[tuple[str, int, int, str]]:
    """Every tagged root lookup in ``sql``: (key, start, end, subquery).

    ``sql[start:end]`` is the whole ``(project_id, trace_id) IN (...)`` term
    and ``subquery`` the SELECT inside its parentheses. An untagged lookup
    (no leaf key) is not returned.
    """

    lookups = []
    start = sql.find(_LOOKUP_OPENING)
    while start != -1:
        open_index = start + len("(project_id, trace_id) IN ")
        close_index = _closing_paren(sql, open_index)
        subquery = sql[open_index + 1 : close_index]
        tag = _LOOKUP_TAG.search(subquery.rstrip())
        if tag is not None:
            lookups.append((tag.group(1), start, close_index + 1, subquery))
        start = sql.find(_LOOKUP_OPENING, close_index)
    return lookups


def _root_set_param(key: str, index: int, part: str) -> str:
    return f"trace_name_roots_{key}_{index}_{part}"


def _root_set_predicate(key: str, roots: TraceNameRootSet) -> str:
    if not roots:
        # No matching root: the semi-join matched nothing, and so does this.
        return "(0 = 1)"
    branches = [
        f"(project_id = %({_root_set_param(key, index, 'project')})s "
        f"AND trace_id IN %({_root_set_param(key, index, 'traces')})s)"
        for index in range(len(roots))
    ]
    return "(" + " OR ".join(branches) + ")"


def trace_name_root_set_params(key: str, roots: TraceNameRootSet) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for index, (project_id, trace_ids) in enumerate(roots):
        params[_root_set_param(key, index, "project")] = project_id
        params[_root_set_param(key, index, "traces")] = tuple(trace_ids)
    return params


def bind_trace_name_root_sets(
    sql: str, root_sets: TraceNameRootSets | None
) -> tuple[str, dict[str, Any]]:
    """Replace each tagged root lookup in ``sql`` with its resolved set.

    Returns the statement and the parameters its bound sets add. A lookup
    whose leaf was not resolved is left as it is (still correct, just read
    per statement), and ``None`` binds nothing.
    """

    if not root_sets:
        return sql, {}
    params: dict[str, Any] = {}
    # Right to left, so earlier offsets stay valid.
    for key, start, end, _subquery in reversed(tagged_trace_name_root_lookups(sql)):
        roots = root_sets.get(key)
        if roots is None:
            continue
        sql = sql[:start] + _root_set_predicate(key, roots) + sql[end:]
        params.update(trace_name_root_set_params(key, roots))
    return sql, params


def _trace_name_leaves(filters: Iterable[dict[str, Any]] | None) -> list[dict]:
    return [
        item
        for item in filters or ()
        if isinstance(item, dict)
        and (item.get("column_id") or item.get("columnId")) == "trace_name"
    ]


def has_trace_name_leaf(filters: Iterable[dict[str, Any]] | None) -> bool:
    return bool(_trace_name_leaves(filters))


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

    Raises ``TraceNameFilterTooBroad`` when a leaf matches more roots than
    one request may bind. Returns ``{}`` when the filters have no such leaf.
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

    resolved: dict[str, TraceNameRootSet] = {}
    for key, (subquery, params) in lookups.items():
        rows = execute(
            # A code-owned lookup over bound parameters.
            "SELECT DISTINCT project_id, trace_id "  # noqa: S608
            f"FROM ({subquery}) LIMIT {TRACE_NAME_ROOT_SET_MAX_ROOTS + 1}",
            {**params, **scope, "start_date": start, "end_date": end},
        )
        rows = list(rows or ())
        if len(rows) > TRACE_NAME_ROOT_SET_MAX_ROOTS:
            raise TraceNameFilterTooBroad()
        roots = _group_roots(rows)
        if _rendered_bytes(roots) > TRACE_NAME_ROOT_SET_MAX_BYTES:
            raise TraceNameFilterTooBroad()
        resolved[key] = roots
    return resolved
