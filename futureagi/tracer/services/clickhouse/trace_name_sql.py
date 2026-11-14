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
