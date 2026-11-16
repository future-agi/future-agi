"""The traces a code agent's own process exported during one run.

A run of a ``source`` agent marks every span its process exports with the job id
as a resource attribute, and every span of a scenario with that call's id. The
collector stores them in the agent's hidden simulation project. These reads list
one call's traces, newest first, and sum each call's model calls, by the job
mark, scoped to that project and to the job's own time window.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.utils import timezone

from simulate.models import CallExecution, HostedHarnessJob, TestExecution
from simulate.services.agent_setup import AgentNotFound, agent_summary
from tracer.services.clickhouse.client import get_clickhouse_client
from tracer.services.clickhouse.v2.query_builders import columns as cols
from tracer.services.simulation_project import agent_of_run, simulation_project_of

RUN_MARK_KEY = "fi.simulation.job_id"
CALL_MARK_KEY = "fi.simulator.call_execution_id"
# The sandbox stamps a call some of whose model calls it could not measure.
UNMEASURED_KEY = "fi.simulation.instrumentation"
READ_TIMEOUT_MS = 2000
MAX_TRACES = 200
TRACED_AGENT_KIND = "source"


@dataclass(frozen=True)
class RunTrace:
    trace_id: str
    name: str | None
    start_time: datetime | None
    status: str | None
    latency_ms: int | None
    call_execution_id: str | None


@dataclass(frozen=True)
class RunTracesSource:
    job: HostedHarnessJob
    project_id: str


@dataclass(frozen=True)
class RunTraces:
    project_id: str | None
    traces: list[RunTrace]


@dataclass(frozen=True)
class CallUsage:
    """One call's model calls, from the LLM spans of its traces."""

    models: list[str]
    llm_spans: int
    # None when an LLM or embedding span carried no cost.
    cost_usd: float | None
    # The models named by its spans that carried no cost, so had no price.
    unpriced_models: list[str] = field(default_factory=list)
    # The sandbox could not measure one of its model calls: its spans say so, so none
    # is lost, and its cost is unknown.
    partial: bool = False


def run_traces_source(execution: TestExecution) -> RunTracesSource | None:
    job = HostedHarnessJob.no_workspace_objects.filter(
        test_execution=execution, deleted=False
    ).first()
    if job is None:
        return None
    agent = agent_of_run(execution)
    if agent is None:
        return None
    version = execution.agent_version or execution.run_test.agent_version
    try:
        summary = agent_summary(
            agent.id, agent_version_id=version.id if version else None
        )
        if version is not None and not summary.agent_stored and not summary.setup_stale:
            # A version made by a push stores no setup; the agent's own decides the kind.
            summary = agent_summary(agent.id)
    except AgentNotFound:
        return None
    if summary.kind != TRACED_AGENT_KIND:
        return None
    project = simulation_project_of(agent.id)
    # The helper also returns a deleted project; its spans are not served.
    if project is None or project.deleted:
        return None
    return RunTracesSource(job=job, project_id=str(project.id))


class CallNotInRun(ValueError):
    """The call asked for is not a call of this run."""


def list_run_traces(
    execution: TestExecution, *, call_execution_id: uuid.UUID
) -> RunTraces:
    # A trace no call owns is never shown, so a read always names one call of this run.
    if not CallExecution.objects.filter(
        id=call_execution_id, test_execution=execution
    ).exists():
        raise CallNotInRun(str(call_execution_id))
    source = run_traces_source(execution)
    if source is None:
        return RunTraces(project_id=None, traces=[])
    return RunTraces(
        project_id=source.project_id,
        traces=read_run_traces(source, call_execution_id=str(call_execution_id)),
    )


def _job_scope_sql() -> str:
    """The job's spans in its agent's hidden project, within the job's own window."""
    job_mark = cols.json_path(cols.RESOURCE_ATTRS, RUN_MARK_KEY, "String")
    return (
        "WHERE project_id = %(project_id)s "
        "AND start_time >= %(window_start)s "
        "AND start_time <= %(window_end)s "
        f"AND {job_mark} = %(job_id)s "
    )


def _job_scope_params(source: RunTracesSource) -> dict[str, Any]:
    job = source.job
    return {
        "project_id": source.project_id,
        "job_id": str(job.id),
        "window_start": job.created_at,
        "window_end": job.terminal_at or timezone.now(),
    }


# A trace belongs to the call named on its earliest span that names one.
_CALL_OF_TRACE = (
    f"argMinIf({cols.ATTRS_STRING}['{CALL_MARK_KEY}'], start_time, "
    f"mapContains({cols.ATTRS_STRING}, '{CALL_MARK_KEY}')) AS call_execution_id"
)


def run_traces_sql() -> str:
    is_root = "parent_span_id = ''"
    return (
        "SELECT trace_id, "
        f"countIf({is_root}) > 0 AS has_root, "
        f"argMinIf(name, start_time, {is_root}) AS root_name, "
        f"minIf(start_time, {is_root}) AS root_start_time, "
        f"argMinIf(status, start_time, {is_root}) AS root_status, "
        f"argMinIf(latency_ms, start_time, {is_root}) AS root_latency_ms, "
        f"{_CALL_OF_TRACE} "
        "FROM spans FINAL "
        f"{_job_scope_sql()}"
        "GROUP BY trace_id "
        "HAVING call_execution_id = %(call_execution_id)s "
        "ORDER BY has_root ASC, root_start_time DESC, trace_id "
        "LIMIT %(limit)s"
    )


def run_traces_params(
    source: RunTracesSource, call_execution_id: str
) -> dict[str, Any]:
    return {
        **_job_scope_params(source),
        "call_execution_id": call_execution_id,
        "limit": MAX_TRACES,
    }


def run_usage_sql() -> str:
    """Each call's model calls: the models its LLM spans named, how many there were,
    what its LLM and embedding spans cost, and how many of those carried no cost. A span
    counts for the call it names, else for the call its trace names, as in the list."""
    call_mark = f"{cols.ATTRS_STRING}['{CALL_MARK_KEY}']"
    has_call_mark = f"mapContains({cols.ATTRS_STRING}, '{CALL_MARK_KEY}')"
    # The request model a span names, else the model column, for an instrumentor that
    # names its model in another convention.
    model = f"if({cols.LLM_REQUEST_MODEL} != '', {cols.LLM_REQUEST_MODEL}, model)"
    return (
        "SELECT call_execution_id, "
        "groupUniqArrayIf(span_model, is_llm AND span_model != '') AS models, "
        "countIf(is_llm) AS llm_spans, "
        "countIf(is_billed) AS billed_spans, "
        "sumIf(span_cost, is_billed) AS cost, "
        "countIf(is_billed AND NOT span_cost > 0) AS unpriced, "
        "groupUniqArrayIf(span_model, is_billed AND NOT span_cost > 0 "
        "AND span_model != '') AS unpriced_models, "
        "countIf(is_partial) AS partial "
        "FROM ("
        f"SELECT if({has_call_mark}, {call_mark}, "
        f"argMinIf({call_mark}, start_time, {has_call_mark}) "
        "OVER (PARTITION BY trace_id)) AS call_execution_id, "
        "observation_type = 'llm' AS is_llm, "
        "observation_type IN ('llm', 'embedding') AS is_billed, "
        f"{cols.ATTRS_STRING}['{UNMEASURED_KEY}'] = 'partial' AS is_partial, "
        f"{model} AS span_model, "
        "cost AS span_cost "
        "FROM spans FINAL "
        f"{_job_scope_sql()}"
        ") "
        "WHERE call_execution_id != '' "
        "GROUP BY call_execution_id"
    )


def run_usage(execution: TestExecution) -> dict[str, CallUsage] | None:
    """Each call's model calls in a run, by call id; a call that exported no LLM or
    embedding span, and that the sandbox did not flag partial, is missing. None when the run's agent is not traced, so none of its
    calls can be seen. A span with no cost leaves its call's cost unknown: it may be an
    unpriced model, and an unpriced model is not free. A call the sandbox could not wholly
    measure (a span stamped partial) reads unknown too: some of its model calls sent no
    span."""
    source = run_traces_source(execution)
    if source is None:
        return None
    rows, _columns, _elapsed_ms = get_clickhouse_client().execute_read(
        run_usage_sql(), _job_scope_params(source), timeout_ms=READ_TIMEOUT_MS
    )
    return {
        str(call_id): CallUsage(
            models=sorted(models),
            llm_spans=int(llm_spans),
            cost_usd=None if unpriced or partial else float(cost),
            unpriced_models=sorted(unpriced_models),
            partial=bool(partial),
        )
        for (
            call_id,
            models,
            llm_spans,
            billed_spans,
            cost,
            unpriced,
            unpriced_models,
            partial,
        ) in rows
        # A call the sandbox flagged partial is kept with no billed span: its model call
        # went unmeasured, so it is not a call that exported nothing.
        if billed_spans or partial
    }


def read_run_traces(
    source: RunTracesSource, *, call_execution_id: str
) -> list[RunTrace]:
    rows, _columns, _elapsed_ms = get_clickhouse_client().execute_read(
        run_traces_sql(),
        run_traces_params(source, call_execution_id),
        timeout_ms=READ_TIMEOUT_MS,
    )
    return [
        RunTrace(
            trace_id=str(trace_id),
            name=name if has_root else None,
            start_time=start_time if has_root else None,
            status=status if has_root else None,
            latency_ms=latency_ms if has_root else None,
            call_execution_id=call_id or None,
        )
        for trace_id, has_root, name, start_time, status, latency_ms, call_id in rows
    ]
