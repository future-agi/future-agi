"""Each call's model calls in a run, read from the spans its traced agent exported.

Proven against a real ClickHouse table copied from ``spans``: the materialized model
column, the job mark in the resource attributes and the call mark on the spans are
the ones the collector writes.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import _ch_test_native_client, _ch_test_owned_database
from simulate.services import run_traces_v3
from simulate.services.run_traces_v3 import CallUsage, RunTracesSource
from tracer.services.clickhouse.v2 import get_v2_config

PROJECT = "5f0c1a52-0000-4000-8000-000000000001"
OTHER_PROJECT = "5f0c1a52-0000-4000-8000-000000000002"
JOB = "7a1d0c33-0000-4000-8000-000000000001"
OTHER_JOB = "7a1d0c33-0000-4000-8000-000000000002"
START = datetime(2026, 11, 9, 10, tzinfo=UTC)


@pytest.fixture(scope="module")
def ch_database():
    with _ch_test_owned_database("test_run_usage_") as database:
        yield database


class _NativeRead:
    """The application client's read, on a native client of the scratch database."""

    def __init__(self, client):
        self._client = client

    def execute_read(self, query, params=None, timeout_ms=None, settings=None):
        return self._client.execute(query, params), [], 0.0


@pytest.fixture
def spans(ch_database, monkeypatch):
    """A fresh copy of ``spans`` that the run reader reads; ``add`` writes one span."""
    with _ch_test_native_client(database=ch_database) as client:
        client.execute(f"CREATE TABLE spans AS {get_v2_config()['database']}.spans")
        monkeypatch.setattr(
            run_traces_v3, "get_clickhouse_client", lambda: _NativeRead(client)
        )

        def add(
            trace,
            *,
            call=None,
            kind="chain",
            model="",
            model_column=None,
            cost=0.0,
            project=PROJECT,
            job=JOB,
            at=START + timedelta(minutes=5),
            extra=None,
        ):
            attrs = {
                **({"fi.simulator.call_execution_id": call} if call else {}),
                **({"gen_ai.request.model": model} if model else {}),
                **(extra or {}),
            }
            column = model_column or ""
            pairs = ", ".join(f"'{key}', '{value}'" for key, value in attrs.items())
            client.execute(
                "INSERT INTO spans (project_id, observation_type, start_time, trace_id,"
                " id, model, cost, attrs_string, resource_attrs,"
                " created_at, updated_at, _version) VALUES"
                f" ('{project}', '{kind}',"
                f" '{at:%Y-%m-%d %H:%M:%S}', '{trace}', '{uuid.uuid4()}',"
                f" '{column}', {cost}, map({pairs}),"
                f""" '{{"fi": {{"simulation": {{"job_id": "{job}"}}}}}}',"""
                " now64(), now64(), 1)"
            )

        try:
            yield add
        finally:
            client.execute("DROP TABLE IF EXISTS spans SYNC")


def _traced(monkeypatch):
    job = SimpleNamespace(
        id=JOB, created_at=START, terminal_at=START + timedelta(hours=1)
    )
    monkeypatch.setattr(
        run_traces_v3,
        "run_traces_source",
        lambda execution: RunTracesSource(job=job, project_id=PROJECT),
    )


@pytest.mark.integration
def test_each_call_s_models_spans_and_cost_come_from_its_traces(spans, monkeypatch):
    _traced(monkeypatch)
    priced, unpriced = str(uuid.uuid4()), str(uuid.uuid4())
    one, two, three = (str(uuid.uuid4()) for _ in range(3))
    # The call is named on the trace's root only; its LLM spans count for it.
    spans(one, call=priced)
    spans(one, kind="llm", model="m1", cost=0.01)
    spans(one, kind="llm", model="m2", cost=0.02)
    spans(two, call=priced)
    spans(two, kind="llm", model="m1", cost=0.005)
    # A span that names its model only in the model column, not in
    # gen_ai.request.model.
    spans(two, kind="llm", model_column="m3", cost=0.001)
    # An embedding's cost is the agent's too; it names no model the lock checks.
    spans(two, kind="embedding", model_column="e1", cost=0.002)
    # An LLM span with no cost, whatever its tokens say: an instrumentor that
    # reports input and output tokens only leaves the total at 0 too.
    spans(three, call=unpriced)
    spans(three, kind="llm", model="m1", cost=0.0)
    spans(three, kind="llm", model="m1", cost=0.01)

    usage = run_traces_v3.run_usage(SimpleNamespace())

    assert usage.keys() == {priced, unpriced}
    assert usage[priced].models == ["m1", "m2", "m3"]
    assert usage[priced].llm_spans == 4
    assert usage[priced].cost_usd == pytest.approx(0.038)
    assert usage[priced].unpriced_models == []
    assert usage[unpriced] == CallUsage(
        models=["m1"], llm_spans=2, cost_usd=None, unpriced_models=["m1"]
    )


@pytest.mark.integration
def test_a_call_the_sandbox_could_not_wholly_measure_has_no_known_cost(
    spans, monkeypatch
):
    _traced(monkeypatch)
    measured, marker_only = str(uuid.uuid4()), str(uuid.uuid4())
    one, two = str(uuid.uuid4()), str(uuid.uuid4())
    partial = {run_traces_v3.UNMEASURED_KEY: "partial"}
    # A priced LLM call, then a model call nothing measured: the sandbox's marker span.
    spans(one, call=measured)
    spans(one, kind="llm", model="m1", cost=0.01)
    spans(one, extra=partial)
    # A call whose only model call went unmeasured sends the marker alone.
    spans(two, call=marker_only, extra=partial)

    usage = run_traces_v3.run_usage(SimpleNamespace())

    assert usage[measured] == CallUsage(
        models=["m1"], llm_spans=1, cost_usd=None, partial=True
    )
    # No LLM or embedding span, but the marker says a model call went unmeasured: the
    # call is kept, so it never reads as one that exported nothing.
    assert usage[marker_only] == CallUsage(
        models=[], llm_spans=0, cost_usd=None, partial=True
    )


@pytest.mark.integration
def test_a_span_counts_for_the_call_it_names_before_its_trace_s(spans, monkeypatch):
    _traced(monkeypatch)
    first, second, trace = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    # The trace's root starts first, so the trace is its call's.
    spans(trace, call=first, at=START + timedelta(minutes=1))
    spans(trace, kind="llm", model="m1", cost=0.01)
    # One trace, two calls: this span names its own.
    spans(trace, call=second, kind="llm", model="m2", cost=0.02)

    usage = run_traces_v3.run_usage(SimpleNamespace())

    assert {call: (one.models, one.llm_spans) for call, one in usage.items()} == {
        first: (["m1"], 1),
        second: (["m2"], 1),
    }
    assert usage[second].cost_usd == pytest.approx(0.02)


@pytest.mark.integration
def test_spans_outside_the_job_its_project_or_its_window_do_not_count(
    spans, monkeypatch
):
    _traced(monkeypatch)
    call = str(uuid.uuid4())
    for where in (
        {"job": OTHER_JOB},
        {"project": OTHER_PROJECT},
        {"at": START - timedelta(minutes=1)},
        {"at": START + timedelta(hours=2)},
    ):
        trace = str(uuid.uuid4())
        spans(trace, call=call, **where)
        spans(trace, kind="llm", model="m1", cost=0.01, **where)
    # A trace no call owns, and a call's trace with no LLM span.
    spans(str(uuid.uuid4()), kind="llm", model="m9", cost=1.0)
    spans(str(uuid.uuid4()), call=call)

    assert run_traces_v3.run_usage(SimpleNamespace()) == {}


@pytest.mark.integration
def test_an_untraced_agent_s_run_reads_nothing(monkeypatch):
    monkeypatch.setattr(run_traces_v3, "run_traces_source", lambda execution: None)
    monkeypatch.setattr(
        run_traces_v3,
        "get_clickhouse_client",
        lambda: pytest.fail("an untraced run reads no span"),
    )

    assert run_traces_v3.run_usage(SimpleNamespace()) is None
