"""One Console agent's runs, read from its Observe project in ClickHouse (learning-service
PLAN stage 6, lane P1-09).

One concern: the statement, and turning its rows into ``AgentRun``. The project id is the
one the curves view took from ``Project.no_workspace_objects`` in the caller's workspace and
organization.
The arithmetic is ``aggregate.py``.

What the statement relies on is what P1-01 stamps on a Console run's export:
- ``omega.agent.id`` on every span;
- ``omega.agent.version`` and ``omega.task.approval_wait_ms``, integers;
- ``omega.task.id`` on every run's root span;
- ``gen_ai.cost.total`` on each model-call span, as a double (an OTLP double, never a string),
  which Future AGI also stores as the span's ``cost``. The collector and the Python adapter
  both keep a string value in ``attrs_string``, and the statement reads the stamp from
  ``attrs_number`` only. A string stamp therefore leaves the run unpriced: it counts in n but
  drops out of every cost figure;
- each model-call span resolves to ``observation_type = 'llm'``. That takes a span-kind
  attribute of ``LLM`` (the first present of ``fi.span.kind``, ``gen_ai.span.kind``,
  ``llm.request.type``, ``openinference.span.kind``), or, with none of those,
  ``gen_ai.operation.name`` of ``chat``, ``generate_content`` or ``text_completion``
  (fi-collector ``resolveObservationType``, which mirrors Django's ``get_observation_type``).
  A span of any other kind is not a model call here, and its cost is not counted.

The integers land in ``attrs_number`` through the collector (an OTLP int) but in
``attrs_string`` through the Django receiver (proto3 JSON writes an int64 as a string).
Each one is therefore read from the number map when present, else parsed from the string map.

- **A run is a trace.** Its root is the trace's earliest span with no parent (ties on the
  span id), as P1-01 picks it. The run is in the window when that root began in it, so the
  scan for roots looks back ``ROOT_LOOKBACK`` to find a trace's true first root.
- **Cost reads every LLM span of those traces in this project.** The read runs up to
  ``CHILD_GRACE`` past the window's end, so a run that began near the end keeps its later
  calls. Another project's span in the same trace (tracing across tenants) is never read.
- **Each span counts once.** Rows are read ``FINAL``: the table replaces a span's older
  versions (``ReplacingMergeTree(_version, is_deleted)``), so a re-sent span or a deleted one
  never doubles or lingers in a sum. A filter applied before replacement (``PREWHERE``, with
  the optimizer's own moves turned off) must give the same answer for every version of a
  span. The sort key is ``(project_id, observation_type, service_name,
  toStartOfHour(start_time), trace_id, id)`` (clickhouse/v2/schema/002_spans_v2.sql).
  ``project_id`` and ``observation_type`` are in it. ``start_time`` is not; only its hour is.
  So the ``start_time`` filters are safe only because every bound is on a UTC hour, and
  ``statement_params`` refuses any bound that is not. Everything else is filtered after
  replacement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tracer.services.clickhouse.query_builders.base import _unix_microseconds
from tracer.services.omega_agents.curves.aggregate import AgentRun

ROOT_LOOKBACK = timedelta(days=1)
CHILD_GRACE = timedelta(days=30)
# More runs than this in one window is refused, with the reason, rather than cut short.
MAX_RUNS = 100_000
_HOUR_US = 3_600_000_000

# Never Nullable: argMin skips a NULL argument, which would take the value from another span.
# Absent or unreadable reads 0, which is no version and no wait.
_NUMBER_OR_STRING = (
    "if(mapContains(attrs_number, '{key}'), toInt64(attrs_number['{key}']), "
    "toInt64OrZero(attrs_string['{key}']))"
)
_VERSION = _NUMBER_OR_STRING.format(key="omega.agent.version")
_APPROVAL_WAIT = _NUMBER_OR_STRING.format(key="omega.task.approval_wait_ms")
# Everything the run takes from its root, picked together from that one span.
_ROOT_FIELDS = (
    f"tuple(attrs_string['omega.task.id'], {_VERSION}, {_APPROVAL_WAIT}, "
    "toInt64(latency_ms), toString(status))"
)

# Replacement before any filter on a column a newer version of the span can change.
_FINAL_SETTINGS = """
SETTINGS optimize_move_to_prewhere = 0,
         optimize_move_to_prewhere_if_final = 0,
         use_skip_indexes_if_final = 0,
         enable_optimize_predicate_expression_to_final_subquery = 0
"""

AGENT_RUNS_SQL = f"""
WITH
roots AS (
    SELECT
        trace_id,
        min(start_time) AS root_start,
        argMin({_ROOT_FIELDS}, (start_time, id)) AS root
    FROM spans FINAL
    PREWHERE project_id = %(project_id)s
        AND start_time >= fromUnixTimestamp64Micro(%(lookback_us)s, 'UTC')
        AND start_time < fromUnixTimestamp64Micro(%(end_us)s, 'UTC')
    WHERE is_deleted = 0
        AND parent_span_id = ''
        AND attrs_string['omega.agent.id'] = %(agent_id)s
    GROUP BY trace_id
    HAVING root_start >= fromUnixTimestamp64Micro(%(start_us)s, 'UTC')
),
llm AS (
    SELECT
        trace_id,
        count() AS llm_spans,
        countIf(NOT mapContains(attrs_number, 'gen_ai.cost.total')) AS unpriced_llm_spans,
        sum(toInt64(round(cost * 1000000))) AS cost_micros
    FROM spans FINAL
    PREWHERE project_id = %(project_id)s
        AND observation_type = 'llm'
        AND start_time >= fromUnixTimestamp64Micro(%(lookback_us)s, 'UTC')
        AND start_time < fromUnixTimestamp64Micro(%(child_end_us)s, 'UTC')
    WHERE is_deleted = 0
        AND trace_id IN (SELECT trace_id FROM roots)
    GROUP BY trace_id
)
SELECT
    roots.trace_id AS trace_id,
    toUnixTimestamp64Micro(roots.root_start) AS started_us,
    tupleElement(roots.root, 1) AS task_id,
    tupleElement(roots.root, 2) AS version,
    tupleElement(roots.root, 3) AS approval_wait_ms,
    tupleElement(roots.root, 4) AS latency_ms,
    tupleElement(roots.root, 5) AS status,
    ifNull(llm.llm_spans, 0) AS llm_spans,
    ifNull(llm.unpriced_llm_spans, 0) AS unpriced_llm_spans,
    ifNull(llm.cost_micros, 0) AS cost_micros
FROM roots
LEFT JOIN llm ON llm.trace_id = roots.trace_id
ORDER BY roots.root_start, roots.trace_id
LIMIT %(limit)s
{_FINAL_SETTINGS}
"""


class TooManyRuns(Exception):
    """The window holds more than ``MAX_RUNS`` runs of the agent."""


def statement_params(
    *, project_id: str, agent_id: str, start: datetime, end: datetime
) -> dict:
    """The parameters for runs whose root began in [start, end).

    Raises ``ValueError`` when any bound, the look-back and grace ones included, is not on a
    UTC hour (the module note on replacement says why).
    """
    bounds = {
        "start_us": _unix_microseconds(start),
        "end_us": _unix_microseconds(end),
        "lookback_us": _unix_microseconds(start - ROOT_LOOKBACK),
        "child_end_us": _unix_microseconds(end + CHILD_GRACE),
    }
    off_the_hour = sorted(name for name, us in bounds.items() if us % _HOUR_US)
    if off_the_hour:
        raise ValueError(
            "the runs statement's bounds must be on the hour (UTC): "
            + ", ".join(off_the_hour)
        )
    return {
        "project_id": str(project_id),
        "agent_id": agent_id,
        **bounds,
        "limit": MAX_RUNS + 1,
    }


def _version(value) -> int | None:
    """EE versions are positive integers; 0 (absent or unreadable) or less is unversioned."""
    number = int(value or 0)
    return number if number > 0 else None


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def run_from_row(row: dict) -> AgentRun:
    started = _EPOCH + timedelta(microseconds=int(row["started_us"]))
    return AgentRun(
        trace_id=str(row["trace_id"]),
        task_id=str(row["task_id"] or ""),
        version=_version(row["version"]),
        started_at=started,
        latency_ms=int(row["latency_ms"] or 0),
        approval_wait_ms=max(0, int(row["approval_wait_ms"] or 0)),
        status=str(row["status"] or ""),
        llm_spans=int(row["llm_spans"]),
        unpriced_llm_spans=int(row["unpriced_llm_spans"]),
        cost_micros=int(row["cost_micros"]),
    )


def read_agent_runs(
    *, project_id: str, agent_id: str, start: datetime, end: datetime, executor=None
) -> list[AgentRun]:
    """The agent's runs whose root began in [start, end), oldest first.

    ``executor`` is the CH25 read service (``V2AnalyticsQueryService``) unless a test passes
    its own. Raises ``TooManyRuns`` past ``MAX_RUNS``.
    """
    if executor is None:
        from tracer.services.clickhouse.v2.query_service import (
            V2AnalyticsQueryService,
        )

        executor = V2AnalyticsQueryService()
    params = statement_params(
        project_id=project_id, agent_id=agent_id, start=start, end=end
    )
    result = executor.execute_ch_query(AGENT_RUNS_SQL, params)
    if len(result.data) > MAX_RUNS:
        raise TooManyRuns()
    return [run_from_row(row) for row in result.data]
