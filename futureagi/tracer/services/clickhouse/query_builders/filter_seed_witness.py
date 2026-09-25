"""The time envelope a candidate seed's any-span witness scan may use.

One concern: turn the interval of roots a seed statement can publish, plus a
slack in hours, into the SQL fragment and bound parameters that keep that
statement's witness scan inside the bounded-witness contract.

Two lanes compile the same envelope - the trace list's short exact-string
candidate seed and the session list's identity-superset seed gate - and both
prune on the immutable ``toStartOfHour`` primary-key prefix, so the hour
alignment and the microsecond binding live here rather than in either builder.

The envelope narrows *candidacy* only. Every lane that emits one keeps an
unbounded latest-state classifier as the authority on what is published, so an
envelope can omit a candidate whose sole witness lies outside it and can never
admit one the classifier rejects.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tracer.services.clickhouse.query_builders.base import _unix_microseconds

# One week. Beyond it an envelope stops bounding the windows these lanes read,
# so it is both the runtime settings' ceiling and the cursor codec's.
MAX_WITNESS_SLACK_HOURS = 168

# The fragment carries its own leading newline and indentation so an empty one
# leaves the statement byte-identical. The default matches the trace list's
# witness CTE; a caller whose block sits elsewhere passes its own.
_DEFAULT_INDENT = " " * 14


def floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def ceil_hour(moment: datetime) -> datetime:
    floored = floor_hour(moment)
    return floored if floored == moment else floored + timedelta(hours=1)


def witness_envelope_sql(
    *,
    root_start: datetime,
    root_end: datetime,
    slack: timedelta,
    column: str = "start_time",
    indent: str = _DEFAULT_INDENT,
) -> tuple[str, dict[str, Any]]:
    """Bound a witness scan to ``[root_start, root_end]`` widened by ``slack``.

    ``[root_start, root_end]`` is the interval of roots the calling statement
    can publish; the bound is ``[hour_floor(root_start) - slack,
    hour_ceil(root_end) + slack)``, so every root the statement publishes keeps
    any witness within ``slack`` of itself.

    Zero slack is the legacy escape hatch: this emits nothing and the caller's
    statement stays byte-identical to the unbounded contract.

    Both ends are spelled as epoch microseconds through
    ``fromUnixTimestamp64Micro`` rather than as datetime literals, so neither
    bound depends on how the driver or the server resolves a timezone. Callers
    whose block has a ``start_time`` alias in scope pass a qualified ``column``
    so the analyzer cannot substitute that alias into this physical-row
    predicate.
    """

    if not slack:
        return "", {}
    witness_start = floor_hour(root_start) - slack
    witness_end = ceil_hour(root_end) + slack
    fragment = (
        f"\n{indent}AND {column} >= "
        "fromUnixTimestamp64Micro(%(filter_witness_start_us)s)"
        f"\n{indent}AND {column} < "
        "fromUnixTimestamp64Micro(%(filter_witness_end_us)s)"
    )
    return fragment, {
        # The datetime pair is bound for the orchestration contract only,
        # exactly as ``filter_slice_start``/``_end`` are; SQL reads the
        # microsecond pair so a boundary microsecond cannot be rounded off.
        "filter_witness_start": witness_start,
        "filter_witness_end": witness_end,
        "filter_witness_start_us": _unix_microseconds(witness_start),
        "filter_witness_end_us": _unix_microseconds(witness_end),
    }


__all__ = [
    "MAX_WITNESS_SLACK_HOURS",
    "ceil_hour",
    "floor_hour",
    "witness_envelope_sql",
]
