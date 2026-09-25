"""The one latency statistic every Observe latency chart publishes.

Every Observe latency series is the ClickHouse t-digest median (p50) of span
``latency_ms``. The unfiltered graphs can only read the stored
``quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)`` aggregate states, so
every other path uses the same estimator: over rows (``quantileTDigest``),
over per-group value arrays (``quantileTDigestArray``) or by merging stored
states (``quantilesTDigestMerge``). Medians are never averaged or weighted.

An empty set gives ``nan`` (``-If``/``-Array``) or ``NULL`` (an all-NULL
``Nullable`` column). Every expression is wrapped so an empty bucket reads 0,
the same zero-fill the graphs already publish, and ``NaN`` can never reach
the JSON response.
"""

LATENCY_STATISTIC = "median"

# The level list of the stored per-hour states. The unfiltered route must
# merge with exactly this list or the optimizer stops matching the aggregate
# projections that hold them.
STORED_LATENCY_QUANTILE_LEVELS = "0.5, 0.95, 0.99"


def _finite_or_zero(expression: str) -> str:
    return f"coalesce(ifNotFinite({expression}, NULL), 0)"


def median_latency_sql(value_expr: str, cond: str | None = None) -> str:
    """p50 of ``value_expr`` over the group's rows (optionally ``-If``)."""

    if cond:
        return _finite_or_zero(f"quantileTDigestIf(0.5)({value_expr}, {cond})")
    return _finite_or_zero(f"quantileTDigest(0.5)({value_expr})")


def latency_values_sql(value_expr: str, cond: str | None = None) -> str:
    """Carry a group's non-NULL latencies as ``Array(Int32)``.

    For a later ``median_latency_from_arrays_sql``. ``latency_ms`` is
    ``Nullable(Int32)`` in storage, so the cast loses nothing and costs 4 B
    per contributing span (a t-digest state costs about 325 B per group).
    """

    present = f"isNotNull({value_expr})"
    condition = f"({cond}) AND {present}" if cond else present
    return f"groupArrayIf(toInt32({value_expr}), {condition})"


def median_latency_from_arrays_sql(array_expr: str) -> str:
    """p50 over the union of every array in the group."""

    return _finite_or_zero(f"quantileTDigestArray(0.5)({array_expr})")


def latency_state_from_arrays_sql(array_expr: str) -> str:
    """Fold a group's latency arrays into one mergeable ``quantileTDigest(0.5)``
    state, for a later ``median_latency_from_state_sql``."""

    return f"quantileTDigestStateArray(0.5)({array_expr})"


def median_latency_from_state_sql(state_expr: str) -> str:
    """p50 of merged ``quantileTDigest(0.5)`` states."""

    return _finite_or_zero(f"quantileTDigestMerge(0.5)({state_expr})")


def median_latency_from_states_sql(
    state_expr: str,
    levels: str = STORED_LATENCY_QUANTILE_LEVELS,
) -> str:
    """p50 of merged ``quantilesTDigest(levels)`` states (level 0.5 is first)."""

    return _finite_or_zero(f"(quantilesTDigestMerge({levels})({state_expr}))[1]")


__all__ = [
    "LATENCY_STATISTIC",
    "STORED_LATENCY_QUANTILE_LEVELS",
    "latency_state_from_arrays_sql",
    "latency_values_sql",
    "median_latency_from_state_sql",
    "median_latency_from_arrays_sql",
    "median_latency_from_states_sql",
    "median_latency_sql",
]
