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

# Trace name of a spans row: an explicit stored name wins (demo onboarding,
# history named by the old dictionary); otherwise a root span is named by its own name.
TRACE_NAME_SQL = "if(trace_name != '', trace_name, if(empty(parent_span_id), name, ''))"

# Roots that started before a read window still name the window's child spans.
TRACE_ROOT_LOOKBACK = "INTERVAL 1 DAY"

# Marker inside a span-mode trace-name root lookup. A statement with a finite
# set of candidate traces replaces it (``scope_trace_name_roots``) so the root
# read covers only those traces; left alone, it is a no-op and the lookup is
# bounded by its time window alone.
TRACE_NAME_ROOT_TRACES_MARKER = "1 = 1 /* trace-name root candidates */"


def scope_trace_name_roots(sql: str, candidates_sql: str) -> str:
    """Restrict every trace-name root lookup in ``sql`` to ``candidates_sql``.

    ``candidates_sql`` is a code-owned predicate over the root scan's
    ``spans`` columns, such as ``trace_id IN (SELECT trace_id FROM <candidate
    CTE>)``. Keep it on ``trace_id`` alone: the ``idx_trace_id`` bloom index
    prunes granules for a single-column IN, not for a tuple. The lookup's own
    project predicate and the outer ``(project_id, trace_id)`` match keep
    tenant correlation.
    """

    return sql.replace(TRACE_NAME_ROOT_TRACES_MARKER, f"({candidates_sql})")
