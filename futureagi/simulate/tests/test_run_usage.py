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
            llm=False,
            model="",
            cost=0.0,
            tokens=0,
            project=PROJECT,
            job=JOB,
            at=START + timedelta(minutes=5),
        ):
            attrs = {
                **({"fi.simulator.call_execution_id": call} if call else {}),
                **({"gen_ai.request.model": model} if model else {}),
            }
            pairs = ", ".join(f"'{key}', '{value}'" for key, value in attrs.items())
            client.execute(
                "INSERT INTO spans (project_id, observation_type, start_time, trace_id,"
                " id, cost, total_tokens, attrs_string, resource_attrs, created_at,"
                " updated_at, _version) VALUES"
                f" ('{project}', '{'llm' if llm else 'chain'}',"
                f" '{at:%Y-%m-%d %H:%M:%S}', '{trace}', '{uuid.uuid4()}', {cost},"
                f" {tokens}, map({pairs}),"
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
    spans(one, llm=True, model="m1", cost=0.01, tokens=100)
    spans(one, llm=True, model="m2", cost=0.02, tokens=50)
    spans(two, call=priced)
    spans(two, llm=True, model="m1", cost=0.005, tokens=20)
    # An LLM span with tokens and no cost: an unpriced model, not a free one.
    spans(three, call=unpriced)
    spans(three, llm=True, model="m1", cost=0.0, tokens=70)
    spans(three, llm=True, model="m1", cost=0.01, tokens=70)

    usage = run_traces_v3.run_usage(SimpleNamespace())

    assert usage.keys() == {priced, unpriced}
    assert usage[priced].models == ["m1", "m2"]
    assert usage[priced].llm_spans == 3
    assert usage[priced].cost_usd == pytest.approx(0.035)
    assert usage[unpriced] == CallUsage(models=["m1"], llm_spans=2, cost_usd=None)


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
        spans(trace, llm=True, model="m1", cost=0.01, tokens=10, **where)
    # A trace no call owns, and a call's trace with no LLM span.
    spans(str(uuid.uuid4()), llm=True, model="m9", cost=1.0, tokens=10)
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
