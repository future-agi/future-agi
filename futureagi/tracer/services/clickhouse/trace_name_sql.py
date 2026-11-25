"""The trace name of a ``spans`` row, without a dictionary lookup.

``spans.trace_name`` used to be MATERIALIZED from ``trace_dict`` (015). The
collector inserts spans before the trace's ``traces`` row, so a new trace was
usually unknown to the dictionary when its spans were written, and the stored
name stayed blank. Readers therefore resolve the name from the row itself:

- an explicit stored name wins (demo onboarding writes one; history named by
  the old dictionary keeps it);
- otherwise a root span (empty ``parent_span_id``) is named by its own name;
- a child span never names its trace: readers resolve it through the root.

The expression gives the same answer under the old (015) and the new (039)
column definition, so reader code and schema can ship in either order.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from clickhouse_driver.util.escape import escape_params

# Trace name of a spans row: an explicit stored name wins (demo onboarding,
# history named by the old dictionary); otherwise a root span is named by its own name.
TRACE_NAME_SQL = "if(trace_name != '', trace_name, if(empty(parent_span_id), name, ''))"

# Roots that started before a read window still name the window's child spans.
TRACE_ROOT_LOOKBACK = "INTERVAL 1 DAY"

# Readers that issue many statements per request resolve each leaf's matching
# roots once (``trace_name_roots.resolve_trace_name_root_sets``) and bind the
# set into every statement instead of repeating the lookup. A set is capped so
# that every statement still parses (ClickHouse rejects a statement over
# max_query_size, 256 KiB, before it runs): above either bound the request is
# refused as too broad instead of building an unbounded set.
TRACE_NAME_ROOT_SET_MAX_ROOTS = 2_048
TRACE_NAME_ROOT_SET_MAX_BYTES = 96 * 1024


def trace_name_root_leaf_key(
    filter_type: str | None, filter_op: str | None, filter_value: Any
) -> str:
    """A stable key for one trace_name leaf: what its root lookup matches.

    The compiler tags each span-mode root lookup with it, and the resolver
    computes it from the same leaf, so a request-level root set binds to
    exactly the lookups of the leaf it was resolved for.
    """

    payload = json.dumps(
        [str(filter_type or ""), str(filter_op or ""), filter_value],
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def trace_name_root_lookup_tag(key: str) -> str:
    """The comment opening a tagged root lookup (hex key: no quotes, no parens)."""

    return f"/* trace-name roots {key} */"


# Marker inside a span-mode trace-name root lookup. A statement with a finite
# set of candidate traces replaces it (``scope_trace_name_roots``) so the
# lookup returns only those traces' roots; left alone, it is a no-op. Either
# way the lookup's read is bounded by its time terms alone.
TRACE_NAME_ROOT_TRACES_MARKER = "1 = 1 /* trace-name root candidates */"


def scope_trace_name_roots(sql: str, candidates_sql: str) -> str:
    """Restrict every trace-name root lookup in ``sql`` to ``candidates_sql``.

    ``candidates_sql`` is a code-owned predicate over the root scan's
    ``spans`` columns, such as ``trace_id IN (SELECT trace_id FROM <candidate
    CTE>)``. It narrows the roots the lookup returns (memory), not what it
    reads: the lookup reads ``spans FINAL``, and with
    use_skip_indexes_if_final=0 (every shipped statement) the
    ``idx_trace_id`` bloom index prunes no granules. The read is bounded by
    the lookup's time terms (``_trace_name_root_scope_sql``). The lookup's
    own project predicate and the outer ``(project_id, trace_id)`` match keep
    tenant correlation.
    """

    return sql.replace(TRACE_NAME_ROOT_TRACES_MARKER, f"({candidates_sql})")


# One resolved leaf: ((project_id, (trace_id, ...)), ...), grouped by project
# and sorted, so a statement binds each project's trace ids once.
TraceNameRootSet = tuple[tuple[str, tuple[str, ...]], ...]
TraceNameRootSets = Mapping[str, TraceNameRootSet]

# ``(project_id, trace_id) IN (SELECT ... /* trace-name roots <key> */)``, as
# ``ClickHouseFilterBuilder`` emits it for a span-mode trace_name leaf: the tag
# closes the subquery.
_LOOKUP_OPENING = (
    "(project_id, trace_id) IN (SELECT project_id, trace_id FROM spans FINAL "
)
_LOOKUP_TAG = re.compile(r"/\* trace-name roots ([0-9a-f]{16}) \*/\Z")


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


def trace_name_root_set_rendered_bytes(roots: TraceNameRootSet) -> int:
    """Bytes of one bound predicate, including the driver's literal escaping.

    These parameters contain only strings and tuples of strings, so driver
    rendering needs no timezone context. Count project literals, separators
    and predicate syntax too, not just the unescaped trace ids. Parameter
    names disappear during rendering, so the leaf key does not affect size.
    """

    key = "budget"
    rendered = _root_set_predicate(key, roots) % escape_params(
        trace_name_root_set_params(key, roots), context=None
    )
    return len(rendered.encode("utf-8"))


def bind_trace_name_root_sets(
    sql: str, root_sets: TraceNameRootSets | None
) -> tuple[str, dict[str, Any]]:
    """Replace each tagged root lookup in ``sql`` with its resolved set.

    Returns the statement and the parameters its bound sets add. A lookup
    whose leaf was not resolved is left as it is (still correct, just read
    per statement), and ``None`` binds nothing. Every replaced occurrence is
    charged to the 96 KiB rendered-root budget, preserving parser headroom
    for the reader's candidate ids and surrounding statement.
    """

    if not root_sets:
        return sql, {}
    params: dict[str, Any] = {}
    rendered_bytes = 0
    sizes: dict[str, int] = {}
    # Right to left, so earlier offsets stay valid. Resolution deduplicates
    # reads, not SQL occurrences: nested AND/OR/NOT terms must stay intact,
    # and the driver expands the same parameter again at every occurrence.
    for key, start, end, _subquery in reversed(tagged_trace_name_root_lookups(sql)):
        roots = root_sets.get(key)
        if roots is None:
            continue
        if key not in sizes:
            sizes[key] = trace_name_root_set_rendered_bytes(roots)
        rendered_bytes += sizes[key]
        if rendered_bytes > TRACE_NAME_ROOT_SET_MAX_BYTES:
            # Lazy import: the resolver imports this binding module.
            from tracer.services.clickhouse.trace_name_roots import (
                TraceNameFilterTooBroad,
            )

            raise TraceNameFilterTooBroad()
        sql = sql[:start] + _root_set_predicate(key, roots) + sql[end:]
        params.update(trace_name_root_set_params(key, roots))
    return sql, params
