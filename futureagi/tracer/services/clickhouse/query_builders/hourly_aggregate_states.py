"""One hourly aggregate-state source for every unfiltered span-metric read.

Both unfiltered read paths — the Observe system-metric graph and the dashboard
time-series widgets — used to read ``spans_hourly_rollup``, an
``AggregatingMergeTree`` fed by a materialized view that fires once per insert
*delivery*.  ``spans`` is a ``ReplacingMergeTree``: a replayed batch collapses
back to one row per dedup key at merge time, but a delivered ``countState()``
can never be retracted, so the rollup keeps counting deliveries the base table
no longer holds.  Measured on production, the unfiltered graphs read up to
3.5x the base table's rows across a month and 1.70x across the trailing year
for the highest-volume tenant, while every filtered graph — which reads
``spans`` — showed the smaller, correct number.

``spans`` already carries aggregate projections keyed on ``(project_id,
toStartOfHour(start_time), ...)``.  A projection lives *inside* the table and
is rebuilt with the part it belongs to (``deduplicate_merge_projection_mode =
'rebuild'``), so a replayed batch stops counting once its parts are merged,
instead of counting forever.  Measured against the base table on the months
where the rollup is wrong, it agrees exactly.

That is agreement with the merged parts, not with the latest live row.  A
projection is per part: it applies no latest-version reduction and cannot
filter ``is_deleted``.  So this source is an approximation, and every caller
publishes it with ``query_exact`` false:

* Until a merge, every version of a corrected row is counted, each at its own
  values.
* ``ReplacingMergeTree(_version, is_deleted)`` keeps the tombstone as the
  surviving row of an ordinary merge (the table does not enable delete
  cleanup), so a deleted row is counted at its tombstone's values even after
  ``OPTIMIZE ... FINAL``.

``test_hourly_aggregate_state_exactness_ch25`` pins both on a real
ClickHouse against the ``FINAL ... is_deleted = 0`` answer.

Why the aggregates below are written as ``-State`` rather than plainly: a
``PROJECTION`` body applies ``-State`` implicitly, and these projections were
declared with a second explicit one.  They therefore store
``AggregateFunction(countState)``, not ``AggregateFunction(count)``.  A query
asking for ``count()`` looks for ``AggregateFunction(count)`` and can never
match; a query asking for ``countState()`` matches immediately.  The caller
applies the paired ``-Merge`` combinator on top of this source.

Three details are load-bearing and must not be "tidied":

* No ``toInt64()`` around the token columns.  The retired rollup's view body
  cast them; the projections store ``sumState`` over the raw ``Int32``, and a
  cast makes the aggregate signature stop matching.
* The window is filtered on ``toStartOfHour(start_time)``, the projection's
  own key expression.  That is the same boundary semantics the rollup path
  used (``hour >= from AND hour < to``), so nothing about which rows land in
  which bucket changes.
* ``status`` is in the ``GROUP BY`` so ``error_rate`` can be merged
  conditionally, and ``countState()`` is always selected so the shape is
  constant regardless of which metrics the caller asks for.

Nothing here names a projection, deliberately.  The optimiser chooses, and
with a real ``WHERE`` the cost model may legitimately prefer a different one
of the projections on ``spans``; all of them are maintained inside the same
table and see the same merged parts.  Write the shape, not the target.

One semantic the projections cannot express: ``is_deleted``.  It is not a
projection column, so a predicate on it would stop the query matching.  Soft
deletes are therefore counted here, where the retired view body excluded them
(``WHERE is_deleted = 0``).  Merging does not clear them: the tombstone is
the row a merge keeps.  Production measurement put live tombstones at zero
across the whole diverging band, which bounds today's error but is not a
contract.
"""

SPANS_TABLE = "spans"

# The aggregate-state aliases below intentionally reuse the retired rollup's
# column names so every ``*Merge()`` expression at the call sites is unchanged.
HOURLY_STATE_ALIASES = (
    "n",
    "cost_sum",
    "total_tokens_sum",
    "prompt_tokens_sum",
    "completion_tokens_sum",
    "latency_q",
)

# Merge-side expression for the share of ERROR spans in a bucket, paired with
# the ``status`` key this source keeps in its ``GROUP BY``.
ERROR_RATE_MERGE_EXPRESSION = (
    "countMergeIf(n, status = 'ERROR') * 100.0 / greatest(countMerge(n), 1)"
)


def hourly_aggregate_state_source(
    project_predicate: str,
    *,
    table: str = SPANS_TABLE,
    start_param: str = "start_date",
    end_param: str = "end_date",
) -> str:
    """Return the parenthesised hourly aggregate-state subquery over *table*.

    Args:
        project_predicate: A code-owned project scope, e.g.
            ``project_id = %(project_id)s`` or ``project_id IN %(project_ids)s``.
            No request value is interpolated here by any caller.
        table: Physical span table. Only the default carries the projections.
        start_param: Name of the inclusive window-start query parameter.
        end_param: Name of the exclusive window-end query parameter.

    Returns:
        A subquery emitting one row per ``(project_id, hour, status)`` whose
        metric columns are aggregate *states*, to be combined by the caller
        with the matching ``-Merge`` combinators.
    """

    return f"""(
            SELECT
                project_id,
                toStartOfHour(start_time) AS hour,
                status,
                countState() AS n,
                sumState(cost) AS cost_sum,
                sumState(total_tokens) AS total_tokens_sum,
                sumState(prompt_tokens) AS prompt_tokens_sum,
                sumState(completion_tokens) AS completion_tokens_sum,
                quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms) AS latency_q
            FROM {table}
            WHERE {project_predicate}
              AND toStartOfHour(start_time) >= %({start_param})s
              AND toStartOfHour(start_time) < %({end_param})s
            GROUP BY project_id, hour, status
        )"""
