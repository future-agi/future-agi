"""Derive an eval task's sampling threshold — the hash cut-off that turns
``sampling_rate`` into an exact row count.

Membership is ``sampling_hash_sql(...) <= threshold``: a pure function of
(task id, row id, threshold), so a lower rate yields a strict subset of a higher
one and a late-arriving row can never displace one already selected. A
historical task ranks its real population to find the cut-off; a continuous task
has no closed population to rank, so its cut-off is analytic — and therefore
probabilistic, as every streaming sampler is.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from tracer.models.eval_task import RunType
from tracer.selectors.eval_tasks.row_resolver import build_eligible_query
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.eval_tasks.ch_guardrails import eval_ch_guardrails

if TYPE_CHECKING:
    from tracer.models.eval_task import EvalTask

# cityHash64 >> 1 — the 63-bit space that fits a signed BIGINT.
HASH_SPACE = 2**63

# Above this k the naive top-k sort approaches CH's spill threshold (1.6 GiB at
# k=105M); below it the histogram's second pass over the inner query costs more
# than the sort it saves.
NAIVE_MAX_K = 10_000_000

# Top 16 bits of the 63-bit hash → 65536 histogram buckets.
_BUCKET_SHIFT = 47


def sampling_hash_sql(salt_param: str, id_col: str) -> str:
    """The single definition of the sampling hash expression — every consumer
    imports it rather than re-spelling it."""
    return f"bitShiftRight(cityHash64(%({salt_param})s, toString({id_col})), 1)"


def derive_threshold(task: EvalTask) -> int:
    """The hash cut-off for the task's current rate, filters and row type.

    A row is in the sample when its ``sampling_hash_sql`` value is ``<=`` the
    returned threshold; ``-1`` selects nothing.
    """
    rate = task.sampling_rate if task.sampling_rate is not None else 100.0
    if rate >= 100:
        return HASH_SPACE - 1
    if rate <= 0:
        return -1
    if task.run_type == RunType.CONTINUOUS:
        return round(rate / 100 * HASH_SPACE)

    sql, params, id_col = _eligible_query(task)
    total = int(_rows(f"SELECT count() FROM ({sql})", params)[0][0])
    if total == 0:
        return -1

    # Multiply before dividing: ceil(rate / 100 * total) rounds a whole-number
    # k up (7% of 100 rows -> 8) on rates that aren't binary-exact.
    k = math.ceil(rate * total / 100)
    hash_sql = sampling_hash_sql("salt", id_col)
    if k <= NAIVE_MAX_K:
        return _naive_threshold(sql, params, hash_sql, k)
    return _histogram_threshold(sql, params, hash_sql, k)


def _naive_threshold(sql: str, params: dict[str, Any], hash_sql: str, k: int) -> int:
    """Single pass: the largest of the k smallest hashes."""
    rows = _rows(
        f"SELECT max(h) FROM "
        f"(SELECT {hash_sql} AS h FROM ({sql}) ORDER BY h LIMIT %(k)s)",
        {**params, "k": k},
    )
    return int(rows[0][0])


def _histogram_threshold(
    sql: str, params: dict[str, Any], hash_sql: str, k: int
) -> int:
    """Two passes, flat memory: bucket the hash space, then resolve the k-th
    smallest hash inside the one bucket that contains it."""
    histogram = [
        (int(bucket), int(count))
        for bucket, count in _rows(
            f"SELECT bitShiftRight({hash_sql}, {_BUCKET_SHIFT}) AS bucket, count() AS c "
            f"FROM ({sql}) GROUP BY bucket ORDER BY bucket",
            params,
        )
    ]
    if not histogram:
        return -1
    bucket, offset = _bucket_and_offset(histogram, k)
    rows = _rows(
        f"SELECT h FROM (SELECT {hash_sql} AS h FROM ({sql}) "
        f"WHERE bitShiftRight(h, {_BUCKET_SHIFT}) = %(bucket)s) "
        f"ORDER BY h LIMIT 1 OFFSET %(offset)s",
        {**params, "bucket": bucket, "offset": offset},
    )
    return int(rows[0][0])


def _bucket_and_offset(histogram: list[tuple[int, int]], k: int) -> tuple[int, int]:
    """Bucket holding the k-th smallest hash, and its 0-based offset within it."""
    cumulative = 0
    for bucket, count in histogram:
        if cumulative + count >= k:
            return bucket, k - cumulative - 1
        cumulative += count
    # Fewer rows than the count() saw (arrivals between the passes): take the
    # largest hash there is.
    bucket, count = histogram[-1]
    return bucket, count - 1


def _eligible_query(task: EvalTask) -> tuple[str, dict[str, Any], str]:
    """``(sql, params, id_col)`` for the task's eligible rows — the resolver's
    filtered id set, before sampling and before the row limit."""
    sql, params, id_col, _sort_col = build_eligible_query(
        project_id=str(task.project_id),
        row_type=task.row_type,
        filters=task.filters,
    )
    return sql, {**params, "salt": str(task.id)}, id_col


def _rows(sql: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    with eval_ch_guardrails(), get_reader() as reader:
        return reader.query_rows(sql, params)
