"""Each call's model calls in a run, read from the spans its traced agent exported.

Proven against a real ClickHouse table copied from ``spans``: the materialized model
column, the job mark in the resource attributes and the call mark on the spans are
the ones the collector writes.
"""

import json
import math
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings

from conftest import (
    _ch_test_native_client,
    _ch_test_owned_database,
    _open_ch_test_http_client,
)
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
    """The application client's read, on a native client of the scratch database,
    under the timeout the caller asked for and the application's read threads."""

    def __init__(self, client):
        self._client = client

    def execute_read(self, query, params=None, timeout_ms=None, settings=None):
        read = {
            "max_execution_time": timeout_ms / 1000,
            "max_threads": django_settings.CLICKHOUSE_APPLICATION_READ_DEFAULT_THREADS,
        }
        return self._client.execute(query, params, settings=read), [], 0.0


@pytest.fixture
def table(ch_database, monkeypatch):
    """A native client on a fresh copy of ``spans`` that the run reader reads."""
    with _ch_test_native_client(database=ch_database) as client:
        client.execute(f"CREATE TABLE spans AS {get_v2_config()['database']}.spans")
        monkeypatch.setattr(
            run_traces_v3, "get_clickhouse_client", lambda: _NativeRead(client)
        )
        try:
            yield client
        finally:
            client.execute("DROP TABLE IF EXISTS spans SYNC")


@pytest.fixture
def spans(ch_database, table):
    """Writes one span as a typed row, over HTTP: the native client cannot write the
    table's JSON columns."""
    writer = _open_ch_test_http_client(database=ch_database)

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
        numbers=None,
        overflow=None,
        span_id=None,
        service="",
        parent="",
        name="",
        status="",
        latency_ms=0,
    ):
        writer.insert(
            "spans",
            [
                [
                    project,
                    kind,
                    service,
                    at,
                    trace,
                    span_id or str(uuid.uuid4()),
                    parent,
                    name,
                    status,
                    latency_ms,
                    model_column or "",
                    cost,
                    {
                        **({run_traces_v3.CALL_MARK_KEY: call} if call else {}),
                        **({"gen_ai.request.model": model} if model else {}),
                        **(extra or {}),
                    },
                    numbers or {},
                    json.dumps(overflow or {}),
                    {"fi": {"simulation": {"job_id": job}}},
                    1,
                ]
            ],
            column_names=[
                "project_id",
                "observation_type",
                "service_name",
                "start_time",
                "trace_id",
                "id",
                "parent_span_id",
                "name",
                "status",
                "latency_ms",
                "model",
                "cost",
                "attrs_string",
                "attrs_number",
                "attributes_extra",
                "resource_attrs",
                "_version",
            ],
        )

    try:
        yield add
    finally:
        writer.close()


# A sender's negative cost, which the collector writes to the cost column as it is.
NEGATIVE_REPORT = {"cost": -0.01, "numbers": {"gen_ai.cost.total": -0.01}}
# A report with a negative side, which the collector sums into the cost column.
REJECTED_REPORT = {
    "cost": 0.03,
    "numbers": {"gen_ai.cost.input": -0.01, "gen_ai.cost.output": 0.04},
}


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
def test_a_span_exported_twice_counts_once_by_its_request_model(spans, monkeypatch):
    _traced(monkeypatch)
    call, trace = str(uuid.uuid4()), str(uuid.uuid4())
    spans(trace, call=call)
    # A runtime with two exporters sends each model call twice under two service
    # names, which the table keeps apart. Both name the request model with the
    # runtime's own provider id; only the Future AGI copy names the bare model, and
    # only it is priced. The span keeps the name both copies had on their own.
    for cost in (0.01, 0.02):
        span_id = str(uuid.uuid4())
        spans(
            trace,
            kind="llm",
            model="gateway:gpt-4o-mini",
            model_column="gateway:gpt-4o-mini",
            service="omega",
            span_id=span_id,
        )
        spans(
            trace,
            kind="llm",
            model="gateway:gpt-4o-mini",
            model_column="gpt-4o-mini",
            cost=cost,
            service="agent",
            span_id=span_id,
            extra={"llm.model_name": "gpt-4o-mini", "fi.span.kind": "LLM"},
        )

    usage = run_traces_v3.run_usage(SimpleNamespace())

    assert usage == {
        call: CallUsage(
            models=["gateway:gpt-4o-mini"], llm_spans=2, cost_usd=pytest.approx(0.03)
        )
    }


@pytest.mark.integration
@pytest.mark.parametrize(
    ("one", "other", "merged"),
    [
        pytest.param(
            {"service": "agent", "kind": "llm", "model": "m1"},
            {"service": "omega", "cost": 0.01},
            CallUsage(models=["m1"], llm_spans=1, cost_usd=pytest.approx(0.01)),
            id="only_the_unpriced_copy_is_a_model_call",
        ),
        pytest.param(
            {"service": "agent", "kind": "llm", "cost": 0.01},
            {"service": "omega", "kind": "llm", "model": "m1"},
            CallUsage(models=["m1"], llm_spans=1, cost_usd=pytest.approx(0.01)),
            id="the_price_on_one_copy_the_model_on_the_other",
        ),
        # Nothing ranks these two, so a read that kept one row could keep either. The
        # chain copy's model names no model call.
        pytest.param(
            {"service": "agent", "kind": "llm"},
            {"service": "agent", "model_column": "m1"},
            CallUsage(models=[], llm_spans=1, cost_usd=None),
            id="one_service_two_kinds",
        ),
        pytest.param(
            {"service": "agent", "kind": "llm", "model": "m1", "cost": 0.01},
            {"service": "omega", "extra": {run_traces_v3.UNMEASURED_KEY: "partial"}},
            CallUsage(models=["m1"], llm_spans=1, cost_usd=None, partial=True),
            id="the_priced_copy_and_the_partial_marker",
        ),
        # Two copies that each name a model and report a cost: both names, the
        # greater cost, every read.
        pytest.param(
            {
                "service": "agent",
                "kind": "llm",
                "model": "m1",
                "cost": 0.01,
                "numbers": {"gen_ai.cost.total": 0.01},
            },
            {
                "service": "omega",
                "kind": "llm",
                "model": "m2",
                "cost": 0.02,
                "numbers": {"gen_ai.cost.total": 0.02},
            },
            CallUsage(models=["m1", "m2"], llm_spans=1, cost_usd=pytest.approx(0.02)),
            id="two_names_two_reported_costs",
        ),
        # A negative report is no price, so it never outranks the other copy.
        pytest.param(
            {"service": "agent", "kind": "llm", "model": "m1", **NEGATIVE_REPORT},
            {"service": "omega", "kind": "llm", "model": "m1"},
            CallUsage(
                models=["m1"], llm_spans=1, cost_usd=None, unpriced_models=["m1"]
            ),
            id="a_negative_reported_cost_and_no_price",
        ),
        pytest.param(
            {"service": "agent", "kind": "llm", "model": "m1", **NEGATIVE_REPORT},
            {
                "service": "omega",
                "kind": "llm",
                "model": "m1",
                "cost": 0.02,
                "numbers": {"gen_ai.cost.total": 0.02},
            },
            CallUsage(models=["m1"], llm_spans=1, cost_usd=pytest.approx(0.02)),
            id="a_negative_and_a_valid_reported_cost",
        ),
        pytest.param(
            {"service": "agent", "kind": "llm", "model": "m1", **NEGATIVE_REPORT},
            {"service": "omega", "kind": "llm", "model": "m1", "cost": 0.015},
            CallUsage(models=["m1"], llm_spans=1, cost_usd=pytest.approx(0.015)),
            id="a_negative_reported_cost_and_an_estimate",
        ),
        # The collector sums a report with a negative side into a positive cost. That
        # sum is no estimate, so the other copy's estimate is the price.
        pytest.param(
            {"service": "agent", "kind": "llm", "model": "m1", **REJECTED_REPORT},
            {"service": "omega", "kind": "llm", "model": "m1", "cost": 0.01},
            CallUsage(models=["m1"], llm_spans=1, cost_usd=pytest.approx(0.01)),
            id="a_rejected_report_and_an_estimate",
        ),
    ],
)
def test_a_span_exported_twice_keeps_what_either_copy_says(
    spans, monkeypatch, one, other, merged
):
    _traced(monkeypatch)
    call, trace, span_id = (str(uuid.uuid4()) for _ in range(3))
    spans(trace, call=call)
    spans(trace, span_id=span_id, **one)
    spans(trace, span_id=span_id, **other)

    reads = [run_traces_v3.run_usage(SimpleNamespace()) for _ in range(3)]

    assert reads == [{call: merged}] * 3


@pytest.mark.integration
def test_a_cost_its_sender_reported_outranks_the_other_copy_s_estimate(
    spans, monkeypatch
):
    _traced(monkeypatch)
    free, paid, padded, padded_free, input_free, output_free = (
        str(uuid.uuid4()) for _ in range(6)
    )
    # The collector prices a span from its tokens unless its sender reported a cost,
    # as a number or as text. A reported zero is a price: the call was free. Text is
    # read as the collector reads it, its surrounding whitespace trimmed (adapter.go
    # firstNumber), and one side alone is the whole report.
    for call, estimate, reported in (
        (free, 0.015, {"cost": 0.0, "numbers": {"gen_ai.cost.total": 0.0}}),
        (paid, 0.015, {"cost": 0.01, "extra": {"llm.cost.total": "0.01"}}),
        (padded, 0.015, {"cost": 0.01, "extra": {"llm.cost.total": " 0.01 "}}),
        (padded_free, 0.015, {"cost": 0.0, "extra": {"llm.cost.total": "\t0\n"}}),
        (input_free, 0.015, {"cost": 0.0, "numbers": {"gen_ai.cost.input": 0.0}}),
        (output_free, 0.015, {"cost": 0.0, "extra": {"llm.cost.completion": "0"}}),
    ):
        trace, span_id = str(uuid.uuid4()), str(uuid.uuid4())
        spans(trace, call=call)
        spans(trace, span_id=span_id, service="agent", kind="llm", cost=estimate)
        spans(trace, span_id=span_id, service="omega", kind="llm", **reported)

    usage = run_traces_v3.run_usage(SimpleNamespace())

    assert {call: one.cost_usd for call, one in usage.items()} == {
        free: 0.0,
        paid: pytest.approx(0.01),
        padded: pytest.approx(0.01),
        padded_free: 0.0,
        input_free: 0.0,
        output_free: 0.0,
    }


@pytest.mark.integration
@pytest.mark.parametrize(
    ("zero", "cost_usd", "unpriced_models"),
    [
        pytest.param(
            {"numbers": {"gen_ai.cost.total": 0.0}},
            pytest.approx(0.01),
            [],
            id="reported_as_a_number",
        ),
        pytest.param(
            {"extra": {"llm.cost.total": "0"}},
            pytest.approx(0.01),
            [],
            id="reported_as_text",
        ),
        pytest.param(
            {"numbers": {"gen_ai.usage.total_cost": 0.0}},
            pytest.approx(0.01),
            [],
            id="billed_by_its_sender",
        ),
        # The table's default zero: nothing priced the call.
        pytest.param({}, None, ["m2"], id="no_cost_reported"),
        # The collector priced it from its tokens instead, or found no price.
        pytest.param(
            {"extra": {"llm.cost.total": "n/a"}},
            None,
            ["m2"],
            id="a_cost_that_is_no_number",
        ),
        pytest.param(
            {
                "numbers": {"gen_ai.cost.total": 0.0},
                "extra": {run_traces_v3.COST_KIND_KEY: "unknown"},
            },
            None,
            ["m2"],
            id="a_cost_the_collector_marked_unknown",
        ),
        # Only a profiled producer's copy carries the collector's marker (converter.go
        # trackCost). The marker decides before any cost key is read.
        pytest.param(
            {"extra": {run_traces_v3.COST_KIND_KEY: "estimated"}},
            pytest.approx(0.01),
            [],
            id="a_free_model_the_collector_priced_from_its_tokens",
        ),
        pytest.param(
            {
                "extra": {
                    run_traces_v3.COST_KIND_KEY: "provider_reported",
                    "llm.cost.total": "0x0",
                }
            },
            pytest.approx(0.01),
            [],
            id="a_report_the_collector_read_and_marked",
        ),
        # An estimate beside an unknown marker is no price either.
        pytest.param(
            {"cost": 0.02, "extra": {run_traces_v3.COST_KIND_KEY: "unknown"}},
            None,
            ["m2"],
            id="an_estimate_marked_unknown",
        ),
        # A report that is no price: the collector writes a negative to the cost column
        # as it is, so nothing priced the call.
        pytest.param(NEGATIVE_REPORT, None, ["m2"], id="a_negative_number"),
        pytest.param(
            {"cost": -0.01, "extra": {"llm.cost.total": "-0.01"}},
            None,
            ["m2"],
            id="a_negative_text",
        ),
        # The collector sums the sides into a zero (adapter.go userCost), but one side
        # is no price, so neither is the sum.
        pytest.param(
            {
                "cost": 0.0,
                "numbers": {"gen_ai.cost.input": -0.01, "gen_ai.cost.output": 0.01},
            },
            None,
            ["m2"],
            id="a_negative_part_beside_a_valid_part",
        ),
        # The same report summed above zero: the sum is neither a report nor an
        # estimate, as the collector never priced the call from its tokens.
        pytest.param(
            REJECTED_REPORT, None, ["m2"], id="a_negative_part_summed_above_zero"
        ),
        # A NaN or infinite number goes to attributes_extra (adapter.go Split), where
        # the collector reads no cost: the sides it did read are the report.
        pytest.param(
            {
                "numbers": {"gen_ai.cost.input": 0.0, "gen_ai.cost.output": 0.0},
                "overflow": {"gen_ai.cost.total": "NaN"},
            },
            pytest.approx(0.01),
            [],
            id="a_nan_total_the_collector_set_aside",
        ),
        # The collector cannot write a NaN or infinite cost: such text parses to one,
        # and its row then fails to encode. These rows stand for another writer's.
        pytest.param(
            {
                "extra": {"llm.cost.total": "nan"},
                "numbers": {"gen_ai.cost.input": 0.0},
            },
            None,
            ["m2"],
            id="a_nan_text_total_beside_a_valid_part",
        ),
        pytest.param(
            {"cost": math.inf, "extra": {"llm.cost.total": "inf"}},
            None,
            ["m2"],
            id="an_infinite_text",
        ),
        pytest.param(
            {"cost": math.nan, "extra": {"llm.cost.total": "nan"}},
            None,
            ["m2"],
            id="a_nan_text",
        ),
        pytest.param(
            {"cost": math.nan, "numbers": {"gen_ai.cost.total": math.nan}},
            None,
            ["m2"],
            id="a_nan_number",
        ),
        # The total wins over a valid input cost, so the cost column is negative.
        pytest.param(
            {
                "cost": -0.01,
                "numbers": {"gen_ai.cost.total": -0.01, "gen_ai.cost.input": 0.005},
            },
            None,
            ["m2"],
            id="a_negative_total_beside_a_valid_part",
        ),
    ],
)
def test_a_free_model_call_leaves_its_call_s_cost_known(
    spans, monkeypatch, zero, cost_usd, unpriced_models
):
    _traced(monkeypatch)
    call, trace = str(uuid.uuid4()), str(uuid.uuid4())
    spans(trace, call=call)
    spans(trace, kind="llm", model="m1", cost=0.01)
    spans(trace, kind="llm", model="m2", **zero)

    usage = run_traces_v3.run_usage(SimpleNamespace())

    assert (usage[call].cost_usd, usage[call].unpriced_models) == (
        cost_usd,
        unpriced_models,
    )


@pytest.mark.integration
def test_a_span_exported_twice_counts_for_the_call_either_copy_names(
    spans, monkeypatch
):
    _traced(monkeypatch)
    first, second, trace, span_id = (str(uuid.uuid4()) for _ in range(4))
    spans(trace, call=first, at=START + timedelta(minutes=1))
    # Only one exporter stamps the call, and the other copy is the priced one.
    spans(trace, span_id=span_id, service="agent", kind="llm", model="m2", cost=0.02)
    spans(trace, span_id=span_id, service="omega", kind="llm", model="m2", call=second)
    # A sibling span that no copy marks counts for the trace's call.
    spans(trace, kind="llm", model="m3", cost=0.03)

    assert run_traces_v3.run_usage(SimpleNamespace()) == {
        first: CallUsage(models=["m3"], llm_spans=1, cost_usd=pytest.approx(0.03)),
        second: CallUsage(models=["m2"], llm_spans=1, cost_usd=pytest.approx(0.02)),
    }


def _owner_case(spans, trace, low, high, case):
    """Seeds a trace whose marked spans, or the copies of one, could name two calls,
    and returns the call it belongs to. An unmarked LLM sibling, m9, counts for it."""
    at = {n: START + timedelta(minutes=n) for n in range(1, 5)}
    span_id = str(uuid.uuid4())
    if case == "earliest_span_names_the_lesser":
        # Two spans name two calls: the earliest span's, though its id is the lesser.
        spans(trace, call=low, at=at[1])
        spans(trace, call=high, at=at[2])
        return low
    if case == "marked_spans_start_together":
        # Two spans start together and name two calls: the lesser id.
        spans(trace, call=high, at=at[1])
        spans(trace, call=low, at=at[1])
        return low
    if case in ("conflicting_marks_same_start", "conflicting_marks_swapped"):
        # Copies that name two calls: the greater id, whichever exporter sent it.
        swapped = case == "conflicting_marks_swapped"
        one, other = (high, low) if swapped else (low, high)
        spans(trace, span_id=span_id, service="agent", call=one, at=at[1])
        spans(trace, span_id=span_id, service="omega", call=other, at=at[1])
        return high
    if case == "conflicting_marks_skewed_starts":
        # The lesser id's copy starts first; the span is still the greater id's.
        spans(trace, span_id=span_id, service="agent", call=low, at=at[1])
        spans(trace, span_id=span_id, service="omega", call=high, at=at[2])
        return high
    # unmarked_copy_starts_first: a span is as early as its marked copy, so the
    # unmarked copy's earlier start does not make its call the trace's.
    spans(trace, span_id=span_id, service="agent", at=at[1])
    spans(trace, span_id=span_id, service="omega", call=low, at=at[4])
    spans(trace, call=high, at=at[2])
    return high


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case", "listed_under"),
    [
        ("earliest_span_names_the_lesser", "owner"),
        ("unmarked_copy_starts_first", "owner"),
        # The list names a trace's call from its earliest marked row, not span: the
        # lesser id's copy starts first, so the list shows the trace under it.
        ("conflicting_marks_skewed_starts", "low"),
        # Marked rows that start together: the list may pick either, so only usage's
        # call is pinned.
        ("marked_spans_start_together", None),
        ("conflicting_marks_same_start", None),
        ("conflicting_marks_swapped", None),
    ],
)
def test_usage_names_a_trace_s_call_by_its_spans_and_the_list_by_its_rows(
    spans, monkeypatch, case, listed_under
):
    _traced(monkeypatch)
    low, high = sorted(str(uuid.uuid4()) for _ in range(2))
    trace = str(uuid.uuid4())
    owner = _owner_case(spans, trace, low, high, case)
    spans(trace, kind="llm", model="m9", cost=0.02, at=START + timedelta(minutes=5))
    source = run_traces_v3.run_traces_source(None)

    for _ in range(3):
        usage = run_traces_v3.run_usage(SimpleNamespace())
        listed = {
            call
            for call in (low, high)
            for one in run_traces_v3.read_run_traces(source, call_execution_id=call)
            if one.trace_id == trace
        }
        assert {call for call, one in usage.items() if "m9" in one.models} == {owner}
        if listed_under:
            assert listed == {owner if listed_under == "owner" else low}


@pytest.mark.integration
def test_a_listed_trace_s_root_is_its_earliest_span_with_no_parent(spans, monkeypatch):
    _traced(monkeypatch)
    call = str(uuid.uuid4())
    at = {n: START + timedelta(minutes=n) for n in range(1, 6)}
    older, newer, rootless, root_id = (str(uuid.uuid4()) for _ in range(4))
    # A child that starts first and a later root supply none of the root's fields.
    spans(older, call=call, parent=root_id, name="child", latency_ms=5, at=at[1])
    # The root exported twice: its earlier copy's fields.
    spans(older, span_id=root_id, service="omega", name="late", latency_ms=9, at=at[3])
    spans(
        older,
        span_id=root_id,
        service="agent",
        name="book_ride",
        status="ERROR",
        latency_ms=1830,
        at=at[2],
    )
    spans(older, name="second_root", status="OK", latency_ms=7, at=at[4])
    spans(newer, call=call, name="verify_identity", status="OK", latency_ms=3, at=at[5])
    # Every span of this trace has a parent: it has no root.
    spans(rootless, call=call, parent=root_id, name="orphan", status="OK", at=at[5])

    listed = run_traces_v3.read_run_traces(
        run_traces_v3.run_traces_source(None), call_execution_id=call
    )

    assert [
        (one.trace_id, one.name, one.start_time, one.status, one.latency_ms)
        for one in listed
    ] == [
        (rootless, None, None, None, None),
        (newer, "verify_identity", at[5], "OK", 3),
        (older, "book_ride", at[2], "ERROR", 1830),
    ]
    assert {one.call_execution_id for one in listed} == {call}


@pytest.mark.integration
def test_one_span_id_in_two_traces_is_two_spans(spans, monkeypatch):
    _traced(monkeypatch)
    costs = {str(uuid.uuid4()): 0.01, str(uuid.uuid4()): 0.02}
    for call, cost in costs.items():
        trace = str(uuid.uuid4())
        spans(trace, call=call)
        for service in ("agent", "omega"):
            spans(
                trace,
                span_id="00f067aa0ba902b7",
                service=service,
                kind="llm",
                model="m1",
                cost=cost,
            )

    assert run_traces_v3.run_usage(SimpleNamespace()) == {
        call: CallUsage(models=["m1"], llm_spans=1, cost_usd=pytest.approx(cost))
        for call, cost in costs.items()
    }


@pytest.mark.integration
def test_a_span_s_request_model_outranks_its_model_column(spans, monkeypatch):
    _traced(monkeypatch)
    call, trace = str(uuid.uuid4()), str(uuid.uuid4())
    # A plain OpenTelemetry instrumentor names no llm.model_name.
    spans(trace, call=call)
    spans(trace, kind="llm", model="gpt-4o", model_column="column-alias", cost=0.01)

    usage = run_traces_v3.run_usage(SimpleNamespace())

    assert usage[call].models == ["gpt-4o"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("one", "other", "models"),
    [
        pytest.param(
            {"model": "m2"}, {"model": "m1"}, ["m1", "m2"], id="two_request_models"
        ),
        pytest.param(
            {"model_column": "zz-column"},
            {"model": "m1", "model_column": "gateway:m1"},
            ["m1", "zz-column"],
            id="a_request_model_and_a_model_column",
        ),
        pytest.param(
            {"model_column": "m2"},
            {"model_column": "m1"},
            ["m1", "m2"],
            id="two_columns",
        ),
    ],
)
def test_a_span_whose_copies_name_two_models_keeps_both_names(
    spans, monkeypatch, one, other, models
):
    _traced(monkeypatch)
    calls = {}
    # A lock checks every name a copy gave, so no name hides another; the span still
    # counts and costs once.
    for agent, omega in ((one, other), (other, one)):
        call, trace, span_id = (str(uuid.uuid4()) for _ in range(3))
        spans(trace, call=call)
        spans(trace, span_id=span_id, service="agent", kind="llm", cost=0.01, **agent)
        spans(trace, span_id=span_id, service="omega", kind="llm", cost=0.01, **omega)
        calls[call] = CallUsage(
            models=models, llm_spans=1, cost_usd=pytest.approx(0.01)
        )

    reads = [run_traces_v3.run_usage(SimpleNamespace()) for _ in range(3)]

    assert reads == [calls] * 3


@pytest.mark.integration
def test_a_model_only_a_span_s_non_llm_copy_names_is_never_read(spans, monkeypatch):
    _traced(monkeypatch)
    call, trace, span_id = (str(uuid.uuid4()) for _ in range(3))
    spans(trace, call=call)
    spans(trace, span_id=span_id, service="agent", kind="llm", model_column="m1")
    spans(trace, span_id=span_id, service="omega", model="m2", cost=0.01)

    assert run_traces_v3.run_usage(SimpleNamespace()) == {
        call: CallUsage(models=["m1"], llm_spans=1, cost_usd=pytest.approx(0.01))
    }


def _dev_name(copy):
    """The name a copy had read alone: its request model, else its model column."""
    return copy.get("model") or copy.get("model_column") or ""


@pytest.mark.integration
def test_a_run_s_model_names_are_those_its_copies_had_each_read_alone(
    spans, monkeypatch
):
    _traced(monkeypatch)
    llm = {"kind": "llm", "cost": 0.01}
    # Each call's spans: one copy, or two that share a span id.
    runs = [
        [
            # Two exporters that agree, the Future AGI copy naming the bare model.
            [
                {**llm, "model": "gateway:m1", "model_column": "gateway:m1"},
                {
                    **llm,
                    "model": "gateway:m1",
                    "model_column": "m1",
                    "extra": {"llm.model_name": "m1"},
                },
            ],
            [{**llm, "model": "m2"}, {**llm, "model_column": "m3"}],
            [{**llm, "model_column": "m4"}, {"model": "m5"}],
            [{**llm, "model": "llama3:8b"}],
            [{"model": "m6"}],
            [{"kind": "embedding", "model": "m7", "cost": 0.01}],
        ],
        [[{**llm, "model": "m2"}], [{"kind": "llm"}, {"model_column": "m8"}]],
    ]
    expected = {}
    for copies_of_spans in runs:
        call, trace = str(uuid.uuid4()), str(uuid.uuid4())
        spans(trace, call=call)
        for copies in copies_of_spans:
            span_id = str(uuid.uuid4())
            for service, copy in zip(("agent", "omega"), copies, strict=False):
                spans(trace, span_id=span_id, service=service, **copy)
        expected[call] = sorted(
            {
                _dev_name(copy)
                for copies in copies_of_spans
                for copy in copies
                if copy.get("kind") == "llm" and _dev_name(copy)
            }
        )

    usage = run_traces_v3.run_usage(SimpleNamespace())

    assert {call: one.models for call, one in usage.items()} == expected
    # What the reader on dev returns for these rows, so the names are its own.
    assert list(expected.values()) == [
        ["gateway:m1", "llama3:8b", "m2", "m3", "m4"],
        ["m2"],
    ]


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


SPANS_PER_TRACE, TRACES_PER_CALL, INSERTS = 20, 10, 8


def _write_large_run(client, logical_spans):
    """A run whose every span two exporters sent: each trace is a root that names its
    call and model calls, as the priced copy an instrumentor named and an unpriced one."""
    per_insert = logical_spans // INSERTS
    # A run's spans arrive in many inserts; unmerged parts are what a read meets.
    client.execute("SYSTEM STOP MERGES spans")
    for service, model, cost, named in (
        ("agent", "gpt-4o-mini", 0.0001, "gpt-4o-mini"),
        ("omega", "gateway:gpt-4o-mini", 0, ""),
    ):
        for insert in range(INSERTS):
            client.execute(
                "INSERT INTO spans (project_id, observation_type, service_name,"
                " start_time, trace_id, id, model, cost, attrs_string, attrs_number,"
                " resource_attrs, _version) "
                "SELECT %(project)s, if(is_root, 'chain', 'llm'), %(service)s,"
                " %(start)s + toIntervalMillisecond(number),"
                " UUIDNumToString(sipHash128(trace)), lower(hex(sipHash64(number))),"
                " if(is_root, '', %(model)s), if(is_root, 0, %(cost)s),"
                " mapConcat(if(is_root, map(%(mark)s, UUIDNumToString(sipHash128("
                "'call', intDiv(trace, %(traces_per_call)s)))), mapFilter("
                "(key, value) -> value != '', map('llm.model_name', %(named)s,"
                " 'gen_ai.request.model', 'gateway:gpt-4o-mini'))),"
                " map('gen_ai.system', 'openai', 'gen_ai.operation.name', 'chat',"
                " 'gen_ai.response.id', toString(number), 'gen_ai.response.model',"
                " 'gpt-4o-mini-2024-07-18', 'gen_ai.response.finish_reason', 'stop',"
                " 'session.id', toString(trace), 'fi.span.kind', 'LLM',"
                " 'server.address', 'gateway.internal')),"
                " map('gen_ai.usage.input_tokens', 1200., 'gen_ai.usage.output_tokens',"
                " 300., 'gen_ai.request.temperature', 0.2, 'server.port', 443.),"
                " %(resource)s::JSON, 1 "
                "FROM (SELECT number, intDiv(number, %(spans_per_trace)s) AS trace,"
                " number %% %(spans_per_trace)s = 0 AS is_root"
                " FROM numbers(%(first)s, %(count)s))",
                {
                    "project": PROJECT,
                    "service": service,
                    "start": START,
                    "model": model,
                    "named": named,
                    "cost": cost,
                    "mark": run_traces_v3.CALL_MARK_KEY,
                    "traces_per_call": TRACES_PER_CALL,
                    "spans_per_trace": SPANS_PER_TRACE,
                    "resource": f'{{"fi": {{"simulation": {{"job_id": "{JOB}"}}}}}}',
                    "first": insert * per_insert,
                    "count": per_insert,
                },
            )


@pytest.mark.benchmark
@pytest.mark.skipif(
    not os.environ.get("RUN_USAGE_BENCH_SPANS"),
    reason="set RUN_USAGE_BENCH_SPANS to time a large run's usage read",
)
def test_a_large_run_s_usage_reads_within_its_timeout(table, monkeypatch):
    """Opt-in twice over, as a seed this large must never run in an ordinary suite:

    RUN_USAGE_BENCH_SPANS=1280000 pytest simulate/tests/test_run_usage.py -m benchmark
    """
    _traced(monkeypatch)
    logical_spans = int(os.environ["RUN_USAGE_BENCH_SPANS"])
    _write_large_run(table, logical_spans)
    traces = logical_spans // SPANS_PER_TRACE

    # The read fails when it outlasts the timeout its caller gives it.
    usage = run_traces_v3.run_usage(SimpleNamespace())

    llm_spans = TRACES_PER_CALL * (SPANS_PER_TRACE - 1)
    assert len(usage) == traces // TRACES_PER_CALL
    assert all(
        one
        == CallUsage(
            models=["gateway:gpt-4o-mini"],
            llm_spans=llm_spans,
            cost_usd=pytest.approx(llm_spans * 0.0001),
        )
        for one in usage.values()
    )
