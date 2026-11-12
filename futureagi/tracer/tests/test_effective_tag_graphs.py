"""Observe graphs filter by effective tags exactly as the lists do.

The Observe page draws its Primary Graph above the grid with the same filters.
A Tag leaf must select the same traces there as in the trace list: SDK tags on
any span, UI trace and child-span assignments, and curated ``traces.tags``
unless a saved trace assignment suppresses them. Real disposable ClickHouse
(chdb) and PostgreSQL; run only on a disposable cloud worker.
"""

import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from tracer.services.clickhouse import graph_dispatch
from tracer.services.clickhouse.bounded_graph_reads import BoundedGraphReadError
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2 import effective_tags
from tracer.services.clickhouse.v2.effective_tags import EffectiveTagRelation
from tracer.services.entity_tags import TagReadUnavailable
from tracer.tests import test_entity_tag_readback as readback
from tracer.tests.test_effective_tag_filters import (
    BEGIN,
    END,
    classify,
    filters,
    leaf,
    manual,
    seed,
)
from tracer.tests.test_entity_tag_readback import (
    assignment_schema,  # noqa: F401
    physical_engine,  # noqa: F401
)
from tracer.tests.test_entity_tag_target import PROJECT
from tracer.tests.test_entity_tag_targets_ch25 import OTHER_PROJECT

stores = readback.stores

# Every trace is a root plus one child. Each span carries a unique power of two
# in total_tokens, so a graph's token sum names exactly the rows it counted.
TRACES = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta")


def _tokens(trace, span):
    return 1 << (2 * TRACES.index(trace) + (span == "child"))


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def tagged_project(stores):
    """One mechanism per trace; ``wanted`` reaches four of them.

    alpha: UI trace label. beta: UI label on its child. gamma: SDK tag on its
    child only. delta: curated traces.tags. epsilon: curated ``wanted``
    suppressed by a saved trace label ``kept``. zeta: SDK ``other`` on its
    root. eta: no tags.
    """
    child_sdk = {"gamma": {"attributes_extra": '{"tag.tags":["wanted"]}'}}
    root_sdk = {"zeta": {"attrs_string": {"tag.tags": '["other"]'}}}
    for trace in TRACES:
        seed(
            stores,
            trace,
            span="root",
            total_tokens=_tokens(trace, "root"),
            **root_sdk.get(trace, {}),
        )
        seed(
            stores,
            trace,
            span="child",
            parent_span_id="root",
            total_tokens=_tokens(trace, "child"),
            **child_sdk.get(trace, {}),
        )
    for trace in ("delta", "epsilon"):
        stores.execute(
            "INSERT INTO traces VALUES (%(project)s, %(trace)s, '[\"wanted\"]', 1, 0)",
            {"project": PROJECT, "trace": trace},
        )
    manual("alpha", ["wanted"])
    manual("beta", ["wanted"], span="child")
    manual("epsilon", ["kept"])


def iso_filters(item):
    """The browser's wire shape: an ISO-string window plus the tag leaf."""
    window, tag_leaf = filters(item)
    window = {
        **window,
        "filter_config": {
            **window["filter_config"],
            "filter_value": [BEGIN.isoformat(), END.isoformat()],
        },
    }
    return [window, tag_leaf]


def graph_rows(stores, observe_type, item, *, resolve_span_versions=False):
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=filters(item),
        interval="hour",
        observe_type=observe_type,
        exact_snapshot=True,
        resolve_span_versions=resolve_span_versions,
        start_date=BEGIN,
        end_date=END,
    )
    sql, params = builder.build()
    assert not any(isinstance(v, EffectiveTagRelation) for v in params.values())
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= set(params)
    return stores.execute(sql, params)


def counted(stores, observe_type, item):
    """The trace ids (or ``trace/span`` ids) whose rows the graph counted."""
    total = sum(
        int(row["total_tokens"]) for row in graph_rows(stores, observe_type, item)
    )
    spans = {
        f"{trace}/{span}"
        for trace in TRACES
        for span in ("root", "child")
        if total & _tokens(trace, span)
    }
    if observe_type == "span":
        return spans
    traces = {key.split("/")[0] for key in spans}
    # A trace graph counts every row of a matched trace, never part of one.
    assert spans == {f"{t}/{s}" for t in traces for s in ("root", "child")}
    return traces


TRACE_CASES = [
    ("tag", "equals", "wanted", {"alpha", "beta", "gamma", "delta"}),
    ("tag", "in", ["wanted"], {"alpha", "beta", "gamma", "delta"}),
    ("tag", "contains", ["wanted"], {"alpha", "beta", "gamma", "delta"}),
    ("tag", "not_equals", "wanted", {"epsilon", "zeta", "eta"}),
    ("tag", "not_in", ["wanted"], {"epsilon", "zeta", "eta"}),
    ("tag", "in", ["kept", "other"], {"epsilon", "zeta"}),
    ("tag", "is_null", None, {"eta"}),
    (
        "tag",
        "is_not_null",
        None,
        {"alpha", "beta", "gamma", "delta", "epsilon", "zeta"},
    ),
    ("tags", "contains", ["wanted"], {"alpha", "delta"}),
    ("tags", "not_contains", ["wanted"], {"beta", "gamma", "epsilon", "zeta", "eta"}),
]


@pytest.mark.parametrize(
    "column,operation,value,expected",
    TRACE_CASES,
    ids=[f"{c}-{o}-{v}" for c, o, v, _ in TRACE_CASES],
)
def test_trace_graph_matches_the_trace_list(stores, column, operation, value, expected):
    tagged_project(stores)
    item = leaf(column, value, operation)
    listed = {row["trace_id"] for row in classify(stores, item, ids=TRACES)}
    assert listed == expected
    assert counted(stores, "trace", item) == expected


SPAN_CASES = [
    (
        "tag",
        "equals",
        "wanted",
        {"alpha/root", "beta/child", "gamma/child", "delta/root"},
    ),
    (
        "tags",
        "contains",
        ["wanted"],
        {"alpha/root", "beta/child", "gamma/child", "delta/root"},
    ),
    (
        "tag",
        "is_not_null",
        None,
        {
            "alpha/root",
            "beta/child",
            "gamma/child",
            "delta/root",
            "epsilon/root",
            "zeta/root",
        },
    ),
]


@pytest.mark.parametrize(
    "column,operation,value,expected",
    SPAN_CASES,
    ids=[f"{c}-{o}" for c, o, _, _ in SPAN_CASES],
)
def test_span_graph_counts_each_tagged_span(stores, column, operation, value, expected):
    tagged_project(stores)
    item = leaf(column, value, operation)
    assert counted(stores, "span", item) == expected
    every = {f"{t}/{s}" for t in TRACES for s in ("root", "child")}
    negated = {"equals": "not_equals", "contains": "not_contains"}.get(
        operation, "is_null"
    )
    assert counted(stores, "span", leaf(column, value, negated)) == every - expected


@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_latest_state_graph_uses_the_same_tag_membership(stores, observe_type):
    tagged_project(stores)
    rows = graph_rows(
        stores,
        observe_type,
        leaf("tag", "wanted", "equals"),
        resolve_span_versions=True,
    )
    traffic = sum(int(row["traffic_count"]) for row in rows)
    assert traffic == (8 if observe_type == "trace" else 4)


class GraphAnalytics:
    """The graph reader's executor contract: typed rows plus their columns."""

    def __init__(self, stores):
        self.stores = stores

    def execute_ch_query(self, sql, params=None, **kwargs):
        rows = self.stores.execute(sql, params)
        for row in rows:
            bucket = row["time_bucket"]
            if isinstance(bucket, str):
                row["time_bucket"] = datetime.fromisoformat(bucket).replace(tzinfo=UTC)
            for field, cast in (
                ("total_tokens", int),
                ("traffic_count", int),
                ("prompt_tokens", int),
                ("completion_tokens", int),
                ("avg_latency", float),
                ("avg_cost", float),
                ("error_rate", float),
            ):
                if row.get(field) is not None:
                    row[field] = cast(row[field])
        columns = (
            list(rows[0])
            if rows
            else sorted(graph_dispatch._TRACE_ROLLUP_RESULT_COLUMNS)
        )
        return SimpleNamespace(data=rows, columns=columns)


@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_observe_graph_endpoint_reader_answers_a_tag_filter(stores, observe_type):
    """The Primary Graph's own reader, which the endpoint used to refuse (400)."""
    tagged_project(stores)
    response = graph_dispatch._fetch_direct_raw_system_metric_graph(
        analytics=GraphAnalytics(stores),
        project_id=PROJECT,
        filters=iso_filters(leaf("tag", "wanted", "equals")),
        interval="hour",
        metric_id="tokens",
        observe_type=observe_type,
        timeout_ms=60000,
        seed=(None, 0),
    )
    expected = (
        sum(
            _tokens(t, s)
            for t in ("alpha", "beta", "gamma", "delta")
            for s in ("root", "child")
        )
        if observe_type == "trace"
        else _tokens("alpha", "root")
        + _tokens("beta", "child")
        + _tokens("gamma", "child")
        + _tokens("delta", "root")
    )
    assert sum(point["value"] or 0 for point in response["data"]) == expected


@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_observe_graph_endpoint_answers_200_for_a_tag_filter(
    stores, monkeypatch, observe_type
):
    """TraceView/ObservationSpanView.get_graph_methods: 400 before, 200 now.

    Only the cache probe and the EXPLAIN-based affordability probe are
    replaced (both are optional decorations of a cold read); compilation,
    the overlay read, the statement and the HTTP mapping are the real ones.
    """
    from tracer.views import observation_span as span_views
    from tracer.views import trace as trace_views

    tagged_project(stores)
    project = SimpleNamespace(id=PROJECT, trace_type="observe", organization_id=None)
    projects = SimpleNamespace(
        filter=lambda *a, **k: SimpleNamespace(first=lambda: project)
    )
    analytics = GraphAnalytics(stores)
    monkeypatch.setattr(
        graph_dispatch, "_read_or_refresh_exact_graph", lambda **k: None
    )
    monkeypatch.setattr(graph_dispatch, "_affordable_raw_graph_seed", lambda **k: None)
    if observe_type == "trace":
        module, view = trace_views, trace_views.TraceView()
    else:
        module, view = span_views, span_views.ObservationSpanView()
    monkeypatch.setattr(module, "V2AnalyticsQueryService", lambda: analytics)
    if observe_type == "trace":
        monkeypatch.setattr(
            module, "_project_queryset_for_request", lambda request: projects
        )
    else:
        monkeypatch.setattr(
            module.Project, "objects", SimpleNamespace(get=lambda *a, **k: project)
        )
    # The browser's POST, through the real request/response contract decorators.
    body = {
        "project_id": PROJECT,
        "filters": iso_filters(leaf("tag", "wanted", "equals")),
        "interval": "hour",
        "req_data_config": {"type": "SYSTEM_METRIC", "id": "tokens"},
    }
    request = Request(
        APIRequestFactory().post("/", body, format="json"), parsers=[JSONParser()]
    )
    request.organization = SimpleNamespace(id="org")
    request.workspace = SimpleNamespace(
        id="ws", organization_id="org", is_default=False
    )
    request._user = SimpleNamespace(
        is_authenticated=True, organization=request.organization
    )
    view.request = request
    view.format_kwarg = None
    response = view.get_graph_methods(request)
    assert response.status_code == 200, response.data
    points = response.data["result"]["data"]
    expected = (
        sum(
            _tokens(t, s)
            for t in ("alpha", "beta", "gamma", "delta")
            for s in ("root", "child")
        )
        if observe_type == "trace"
        else _tokens("alpha", "root")
        + _tokens("beta", "child")
        + _tokens("gamma", "child")
        + _tokens("delta", "root")
    )
    assert sum(point["value"] or 0 for point in points) == expected


@pytest.mark.parametrize("operation", ["starts_with", "ends_with", "between"])
def test_non_membership_tag_operator_is_a_bad_graph_filter(stores, operation):
    with pytest.raises(UnsupportedFilterShapeError):
        compile_exact_graph_row_predicates(
            [leaf("tag", "wan", operation)], project_id=PROJECT, observe_type="trace"
        )


def test_unavailable_tag_overlay_is_a_retryable_graph_error(stores, monkeypatch):
    def unavailable(**kwargs):
        raise TagReadUnavailable("tag_assignment_unavailable")

    monkeypatch.setattr(effective_tags, "read_tag_overlay", unavailable)
    with pytest.raises(BoundedGraphReadError) as raised:
        compile_exact_graph_row_predicates(
            [leaf("tag", "wanted")], project_id=PROJECT, observe_type="trace"
        )
    assert raised.value.retryable


def test_tag_overlay_byte_budget_spans_every_graph_leaf(stores, monkeypatch):
    manual("alpha", ["wanted"])
    _, one_leaf = effective_tags.graph_tag_overlay_params(
        "probe",
        project_ids=(PROJECT,),
        values=("wanted",),
        deadline=ReadDeadline.start(10000),
    )
    monkeypatch.setattr(
        effective_tags, "MAX_TAG_OVERLAY_BYTES", one_leaf + one_leaf // 2
    )
    compile_exact_graph_row_predicates(
        [leaf("tag", "wanted")], project_id=PROJECT, observe_type="trace"
    )
    with pytest.raises(BoundedGraphReadError):
        compile_exact_graph_row_predicates(
            [leaf("tag", "wanted"), leaf("tag", "wanted")],
            project_id=PROJECT,
            observe_type="trace",
        )


def test_graph_tag_scope_is_the_requested_project(stores):
    manual("alpha", ["wanted"])
    manual("alpha", ["wanted"], project=OTHER_PROJECT)
    manual("beta", ["wanted"], project=OTHER_PROJECT, span="child")
    plan = compile_exact_graph_row_predicates(
        [leaf("tag", "wanted")], project_id=PROJECT, observe_type="trace"
    )
    trace_keys = [v for k, v in plan.params.items() if k.endswith("_trace_keys")]
    span_keys = [v for k, v in plan.params.items() if k.endswith("_span_keys")]
    assert trace_keys == [(("", ""), (PROJECT, "alpha"))]
    assert span_keys == [(("", "", "", 0, "", ""),)]
