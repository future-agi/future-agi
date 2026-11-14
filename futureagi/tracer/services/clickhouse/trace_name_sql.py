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
from typing import Any

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
