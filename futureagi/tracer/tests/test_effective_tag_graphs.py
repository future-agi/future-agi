"""Observe graphs filter by effective tags exactly as the lists do.

The Observe page draws its Primary Graph above the grid with the same filters.
A Tag leaf must select the same traces there as in the trace list: SDK tags on
any span, UI trace and child-span assignments, and curated ``traces.tags``
unless a saved trace assignment suppresses them. Real disposable ClickHouse
(chdb) and PostgreSQL; run only on a disposable cloud worker.
"""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.services.clickhouse import graph_dispatch
from tracer.services.clickhouse.bounded_graph_reads import BoundedGraphReadError
from tracer.services.clickhouse.query_builders.agent_graph import (
    AgentGraphQueryBuilder,
)
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    GraphTagFilterTooBroadError,
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
    compile_exact_graph_filter_predicates,
)
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.clickhouse.v2 import effective_tags
from tracer.services.clickhouse.v2.effective_tags import EffectiveTagRelation
from tracer.services.clickhouse.v2.entity_tag_targets import TRACE_ASSIGNMENT_HOUR
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


def membership_rows(stores, observe_type, item):
    """The eval/annotation graph membership statement (EvalMetricsQueryBuilderV2)."""
    clause, params = compile_exact_graph_filter_predicates(
        [item],
        project_id=PROJECT,
        observe_type=observe_type,
        window_start=BEGIN,
        window_end=END,
    )
    assert not any(isinstance(v, EffectiveTagRelation) for v in params.values())
    selected = "trace_id, id" if observe_type == "span" else "DISTINCT trace_id"
    sql = (
        f"SELECT {selected} FROM spans FINAL "
        "WHERE project_id = toUUID(%(project_id)s) AND is_deleted = 0 "
        "AND start_time >= %(snapshot_start_date)s "
        "AND start_time < %(snapshot_end_date)s AND " + clause
    )
    params = {
        **params,
        "project_id": PROJECT,
        "snapshot_start_date": BEGIN,
        "snapshot_end_date": END,
    }
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= set(params)
    return stores.execute(sql, params)


@pytest.mark.parametrize(
    "column,operation,value,expected",
    TRACE_CASES,
    ids=[f"{c}-{o}-{v}" for c, o, v, _ in TRACE_CASES],
)
def test_eval_graph_trace_membership_matches_the_trace_list(
    stores, column, operation, value, expected
):
    tagged_project(stores)
    rows = membership_rows(stores, "trace", leaf(column, value, operation))
    assert {row["trace_id"] for row in rows} == expected


@pytest.mark.parametrize(
    "column,operation,value,expected",
    SPAN_CASES,
    ids=[f"{c}-{o}" for c, o, _, _ in SPAN_CASES],
)
def test_eval_graph_span_membership_selects_each_tagged_span(
    stores, column, operation, value, expected
):
    tagged_project(stores)
    rows = membership_rows(stores, "span", leaf(column, value, operation))
    assert {f"{row['trace_id']}/{row['id']}" for row in rows} == expected


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


def post_graph(stores, monkeypatch, observe_type, item, *, scheduled=False):
    """TraceView/ObservationSpanView.get_graph_methods for one filter.

    Cold inline calls stub the cache and cost probes; scheduled-path tests
    supply those stubs themselves. Compilation, overlay reads and the HTTP
    mapping are real in either case.
    """
    from tracer.views import observation_span as span_views
    from tracer.views import trace as trace_views

    project = SimpleNamespace(
        id=PROJECT,
        trace_type="observe",
        organization_id="org" if scheduled else None,
    )
    projects = SimpleNamespace(
        filter=lambda *a, **k: SimpleNamespace(first=lambda: project)
    )
    analytics = GraphAnalytics(stores)
    if not scheduled:
        monkeypatch.setattr(
            graph_dispatch, "_read_or_refresh_exact_graph", lambda **k: None
        )
        monkeypatch.setattr(
            graph_dispatch, "_affordable_raw_graph_seed", lambda **k: None
        )
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
        "filters": iso_filters(item),
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
    return view.get_graph_methods(request)


def graph_total(response):
    assert response.status_code == 200, response.data
    return sum(point["value"] or 0 for point in response.data["result"]["data"])


def expected_tokens(observe_type, traces, spans):
    if observe_type == "trace":
        return sum(_tokens(t, s) for t in traces for s in ("root", "child"))
    return sum(_tokens(*span.split("/")) for span in spans)


@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_observe_graph_endpoint_answers_200_for_a_tag_filter(
    stores, monkeypatch, observe_type
):
    """TraceView/ObservationSpanView.get_graph_methods: 400 before, 200 now."""
    tagged_project(stores)
    response = post_graph(
        stores, monkeypatch, observe_type, leaf("tag", "wanted", "equals")
    )
    assert graph_total(response) == expected_tokens(
        observe_type,
        ("alpha", "beta", "gamma", "delta"),
        ("alpha/root", "beta/child", "gamma/child", "delta/root"),
    )


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
    from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
        GraphTagFilterTooBroadError,
    )

    # Over budget is a property of the project's tags: a request error with
    # its own copy, never a retryable 503.
    with pytest.raises(GraphTagFilterTooBroadError) as raised:
        compile_exact_graph_row_predicates(
            [leaf("tag", "wanted"), leaf("tag", "wanted")],
            project_id=PROJECT,
            observe_type="trace",
        )
    assert isinstance(raised.value, UnsupportedFilterShapeError)
    assert not isinstance(raised.value, BoundedGraphReadError)


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


# Above MAX_TAG_OVERLAY_KEYS (2,048) and, at ~60 bytes a key, the 128 KiB
# rendered budget the graph used to spend binding every trace assignment.
LIFETIME_ASSIGNMENTS = 2_100


def lifetime_assignments(names=("reviewed",), *, count=LIFETIME_ASSIGNMENTS):
    """What a busy review team leaves behind over a project's lifetime.

    Trace assignments are never pruned: other names, cleared rows (every
    third one), and traces that aged out of ClickHouse long ago all remain.
    None of them carries ``wanted``/``other`` or hides a curated tag.
    """
    EntityTagAssignment.objects.bulk_create(
        [
            EntityTagAssignment(
                project_id=PROJECT,
                entity_kind="trace",
                trace_id=f"aged-{index:05d}",
                span_id="",
                start_hour=TRACE_ASSIGNMENT_HOUR,
                observation_type="",
                service_name="",
                manual_tags=[]
                if index % 3 == 0
                else [{"name": name, "color": "#123456"} for name in names],
            )
            for index in range(count)
        ]
    )


def bound(plan, suffix):
    return [value for key, value in plan.params.items() if key.endswith(suffix)]


LIFETIME_CASES = [
    ("tag", "equals", "wanted", {"alpha", "beta", "gamma", "delta"}),
    ("tag", "not_equals", "wanted", {"epsilon", "zeta", "eta"}),
    ("tag", "in", ["other"], {"zeta"}),
    ("tags", "contains", ["wanted"], {"alpha", "delta"}),
]


@pytest.mark.parametrize(
    "column,operation,value,expected",
    LIFETIME_CASES,
    ids=[f"{c}-{o}-{v}" for c, o, v, _ in LIFETIME_CASES],
)
def test_lifetime_trace_assignments_do_not_cap_the_graph(
    stores, column, operation, value, expected
):
    """R3-1: a project's lifetime trace assignments never reach the budget.

    Only the assignments that carry the filtered name, and the ones that hide
    a matching curated tag (epsilon), are bound. Before, every trace
    assignment was bound, so this project's graph was a permanent 503.
    """
    tagged_project(stores)
    lifetime_assignments()
    item = leaf(column, value, operation)
    assert counted(stores, "trace", item) == expected
    plan = compile_exact_graph_row_predicates(
        [item], project_id=PROJECT, observe_type="trace"
    )
    suppressed = (("", ""), (PROJECT, "epsilon")) if value != ["other"] else (("", ""),)
    assert bound(plan, "_assigned_keys") == [suppressed]


@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_lifetime_trace_assignments_keep_the_primary_graph_answering(
    stores, monkeypatch, observe_type
):
    tagged_project(stores)
    lifetime_assignments()
    response = post_graph(
        stores, monkeypatch, observe_type, leaf("tag", "wanted", "equals")
    )
    assert graph_total(response) == expected_tokens(
        observe_type,
        ("alpha", "beta", "gamma", "delta"),
        ("alpha/root", "beta/child", "gamma/child", "delta/root"),
    )


def test_lifetime_trace_assignments_keep_the_eval_graph_membership(stores):
    tagged_project(stores)
    lifetime_assignments()
    rows = membership_rows(stores, "trace", leaf("tag", "wanted", "equals"))
    assert {row["trace_id"] for row in rows} == {"alpha", "beta", "gamma", "delta"}


def test_lifetime_trace_assignments_keep_the_window_scan_count(stores, monkeypatch):
    """The direct count callers' window scan binds the same narrow set."""
    from tracer.tests.test_effective_tag_scale import window_count

    tagged_project(stores)
    lifetime_assignments()
    assert window_count(stores, monkeypatch, "wanted") == 4


@pytest.mark.parametrize("graph", ["agent", "primary", "http"])
def test_graph_tag_window_excludes_lifetime_matching_assignments(
    stores, monkeypatch, graph
):
    """Only live, in-window assignments may consume the graph bind budget."""
    seed(stores, "alpha", total_tokens=_tokens("alpha", "root"))
    seed(
        stores,
        "alpha",
        span="child",
        parent_span_id="root",
        total_tokens=_tokens("alpha", "child"),
    )
    manual("alpha", ["wanted"])
    EntityTagAssignment.objects.bulk_create(
        [
            EntityTagAssignment(
                project_id=PROJECT,
                entity_kind="trace",
                trace_id=f"old-{index:05d}",
                span_id="",
                start_hour=TRACE_ASSIGNMENT_HOUR,
                observation_type="",
                service_name="",
                manual_tags=[{"name": "wanted", "color": "#123456"}],
            )
            for index in range(LIFETIME_ASSIGNMENTS)
        ]
    )
    item = leaf("tag", "wanted")
    if graph == "agent":
        assert agent_graph_traces(stores, item, ("alpha",)) == {"alpha"}
    elif graph == "primary":
        assert counted(stores, "trace", item) == {"alpha"}
    else:
        assert graph_total(post_graph(stores, monkeypatch, "trace", item)) == 3


def test_window_graph_with_more_than_eight_pages_of_dead_trace_assignments(stores):
    """The page count must not mistake dead lifetime keys for live graph binds.

    Every one of the 8,100 aged trace assignments carries ``wanted`` (more than
    eight 1,000-row pages of the windowed walk), while only alpha, beta and
    gamma have a root in the window. ``counted`` names traces by their summed
    ``total_tokens`` and a trace graph must count each matched trace's root and
    child, so the three are seeded exactly as ``tagged_project`` seeds them.
    """
    for trace in ("alpha", "beta", "gamma"):
        seed(stores, trace, total_tokens=_tokens(trace, "root"))
        seed(
            stores,
            trace,
            span="child",
            parent_span_id="root",
            total_tokens=_tokens(trace, "child"),
        )
        manual(trace, ["wanted"])
    dead = 8_100
    assert dead > (
        effective_tags.MAX_WINDOW_OVERLAY_PAGES * effective_tags._ASSIGNMENT_PAGE_SIZE
    )
    EntityTagAssignment.objects.bulk_create(
        [
            EntityTagAssignment(
                project_id=PROJECT,
                entity_kind="trace",
                trace_id=f"aged-{index:05d}",
                span_id="",
                start_hour=TRACE_ASSIGNMENT_HOUR,
                observation_type="",
                service_name="",
                manual_tags=[{"name": "wanted", "color": "#123456"}],
            )
            for index in range(dead)
        ]
    )

    assert counted(stores, "trace", leaf("tag", "wanted")) == {"alpha", "beta", "gamma"}


@pytest.mark.unit
def test_window_overlay_pages_do_not_budget_dead_lifetime_assignments(monkeypatch):
    """Nine lifetime pages leave just three live keys in the narrow window."""
    records = [
        {
            "project_id": PROJECT,
            "entity_kind": "trace",
            "trace_id": f"old-{index:05d}",
            "span_id": "",
            "start_hour": TRACE_ASSIGNMENT_HOUR,
            "observation_type": "",
            "service_name": "",
            "manual_tags": [{"name": "wanted", "color": "#123456"}],
        }
        for index in range(8_100)
    ]
    records[-3:] = [
        {**record, "trace_id": trace}
        for record, trace in zip(records[-3:], ("alpha", "beta", "gamma"), strict=True)
    ]
    live_traces = {"alpha", "beta", "gamma"}
    monkeypatch.setattr(
        effective_tags, "_query_assignments", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(
        effective_tags,
        "_assignment_pages",
        lambda *args, **kwargs: (
            records[index : index + 1_000] for index in range(0, len(records), 1_000)
        ),
    )

    def execute(_sql, params, **_kwargs):
        return SimpleNamespace(
            data=[
                {"project_id": PROJECT, "trace_id": trace, "span_id": ""}
                for _, trace in params["tag_graph_live_trace_keys"]
                if trace in live_traces
            ]
        )

    overlay = effective_tags.read_tag_overlay(
        project_ids=(PROJECT,),
        trace_ids=None,
        values=("wanted",),
        deadline=ReadDeadline.start(60_000),
        window_start=BEGIN,
        window_end=END,
        execute=execute,
    )
    assert {trace for _, trace in overlay.trace_names} == live_traces


def test_window_overlay_work_bound_is_non_retryable(stores, monkeypatch):
    """Dead lifetime rows consume scanned work, but not the live bind budget."""
    seed(stores, "alpha", total_tokens=_tokens("alpha", "root"))
    manual("alpha", ["wanted"])
    lifetime_assignments(names=("wanted",), count=6)
    monkeypatch.setattr(effective_tags, "_ASSIGNMENT_PAGE_SIZE", 2)
    monkeypatch.setattr(effective_tags, "MAX_GRAPH_OVERLAY_SCAN_PAGES", 2)
    response = post_graph(stores, monkeypatch, "trace", leaf("tag", "wanted"))
    assert response.status_code == 400, response.data
    assert response.data["code"] == "tag_filter_too_broad"


@pytest.mark.parametrize("too_broad", [True, False])
def test_scheduled_tag_graph_reports_terminal_error_in_request(
    stores, monkeypatch, too_broad
):
    seed(stores, "alpha", total_tokens=_tokens("alpha", "root"))
    manual("alpha", ["wanted"])
    if too_broad:
        lifetime_assignments(names=("wanted",), count=6)
    monkeypatch.setattr(effective_tags, "_ASSIGNMENT_PAGE_SIZE", 2)
    monkeypatch.setattr(effective_tags, "MAX_GRAPH_OVERLAY_SCAN_PAGES", 2)
    monkeypatch.setattr(
        graph_dispatch,
        "_affordable_raw_graph_seed",
        lambda **_: graph_dispatch._GraphReadUnaffordable(estimated_rows=None),
    )
    scheduled = []

    def cache(**kw):
        if kw.get("schedule_on_miss") is not False:
            scheduled.append(kw)
            return kw["pending_payload"]
        return None

    monkeypatch.setattr(graph_dispatch, "_read_or_refresh_exact_graph", cache)
    response = post_graph(
        stores, monkeypatch, "trace", leaf("tag", "wanted"), scheduled=True
    )
    if too_broad:
        assert response.status_code == 400, response.data
        assert response.data["code"] == "tag_filter_too_broad"
        assert not scheduled
    else:
        assert response.status_code == 200, response.data
        assert len(scheduled) == 1


def test_window_overlay_deadline_before_work_bound_is_retryable(stores, monkeypatch):
    seed(stores, "alpha", total_tokens=_tokens("alpha", "root"))
    manual("alpha", ["wanted"])
    monkeypatch.setattr(effective_tags, "_ASSIGNMENT_PAGE_SIZE", 2)
    original = effective_tags._query_assignments

    def expired(predicate, *, deadline, limit):
        if limit == 1:
            return original(
                predicate, deadline=ReadDeadline(total_ms=1, started=0), limit=limit
            )
        return original(predicate, deadline=deadline, limit=limit)

    monkeypatch.setattr(effective_tags, "_query_assignments", expired)
    response = post_graph(stores, monkeypatch, "trace", leaf("tag", "wanted"))
    assert response.status_code == 503, response.data


def _dead_trace_pages(count, *, size=1_000):
    """``count`` full pages of dead 'wanted' trace assignments."""
    for page in range(count):
        yield [
            {
                "project_id": PROJECT,
                "entity_kind": "trace",
                "trace_id": f"dead-{page}-{index}",
                "span_id": "",
                "start_hour": TRACE_ASSIGNMENT_HOUR,
                "observation_type": "",
                "service_name": "",
                "manual_tags": [{"name": "wanted", "color": "#123456"}],
            }
            for index in range(size)
        ]


def _walk_until_deadline(monkeypatch, *, spent_on_page, failure):
    """Walk dead pages; on ``spent_on_page`` the read fails with ``failure``.

    A ``ReadDeadlineExceeded`` failure also spends the request's deadline, as
    a real deadline hit does; any other failure leaves the deadline running.
    """
    import time

    deadline = ReadDeadline.start(60_000)
    pages = {"n": 0}
    monkeypatch.setattr(
        effective_tags, "_query_assignments", lambda *args, **kwargs: []
    )

    def walk(*args, **kwargs):
        for page in _dead_trace_pages(effective_tags.MAX_GRAPH_OVERLAY_SCAN_PAGES):
            pages["n"] += 1
            yield page

    def execute(_sql, params, **_kwargs):
        if pages["n"] == spent_on_page:
            if isinstance(failure, ReadDeadlineExceeded):
                object.__setattr__(deadline, "started", time.monotonic() - 61)
            raise failure
        return SimpleNamespace(data=[])

    monkeypatch.setattr(effective_tags, "_assignment_pages", walk)
    with pytest.raises(TagReadUnavailable) as raised:
        effective_tags.read_tag_overlay(
            project_ids=(PROJECT,),
            trace_ids=None,
            values=("wanted",),
            deadline=deadline,
            window_start=BEGIN,
            window_end=END,
            execute=execute,
        )
    return raised.value.code


@pytest.mark.parametrize(
    "history,expected",
    [(9, "tag_source_unavailable"), (12, "tag_overlay_budget_exceeded")],
)
def test_deadline_after_eight_pages_probes_actual_history(
    stores, monkeypatch, history, expected
):
    """A stalled ClickHouse page is not itself evidence of too much PG history."""
    import time

    lifetime_assignments(names=("wanted",), count=history * 3 // 2 + 1)
    monkeypatch.setattr(effective_tags, "_ASSIGNMENT_PAGE_SIZE", 1)
    monkeypatch.setattr(effective_tags, "MAX_GRAPH_OVERLAY_SCAN_PAGES", 10)
    deadline = ReadDeadline.start(60_000)
    calls = {"n": 0}

    def stalled(_sql, _params, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 9:
            object.__setattr__(deadline, "started", time.monotonic() - 61)
            raise ReadDeadlineExceeded("read deadline exceeded")
        return SimpleNamespace(data=[])

    with pytest.raises(TagReadUnavailable) as raised:
        effective_tags.read_tag_overlay(
            project_ids=(PROJECT,),
            trace_ids=None,
            values=("wanted",),
            deadline=deadline,
            window_start=BEGIN,
            window_end=END,
            execute=stalled,
        )
    assert raised.value.code == expected


def test_matching_walk_does_not_charge_unwalked_suppression_history(
    stores, monkeypatch
):
    import time

    lifetime_assignments(names=("wanted",), count=14)  # nine matching rows
    EntityTagAssignment.objects.bulk_create(
        [
            EntityTagAssignment(
                project_id=PROJECT,
                entity_kind="trace",
                trace_id=f"other-{n}",
                span_id="",
                start_hour=TRACE_ASSIGNMENT_HOUR,
                observation_type="",
                service_name="",
                manual_tags=[{"name": "other", "color": "#123456"}],
            )
            for n in range(12)
        ]
    )
    monkeypatch.setattr(effective_tags, "_ASSIGNMENT_PAGE_SIZE", 1)
    monkeypatch.setattr(effective_tags, "MAX_GRAPH_OVERLAY_SCAN_PAGES", 10)
    deadline = ReadDeadline.start(60_000)
    calls = {"n": 0}

    def stalled(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 9:
            object.__setattr__(deadline, "started", time.monotonic() - 61)
            raise ReadDeadlineExceeded("read deadline exceeded")
        return SimpleNamespace(data=[])

    with pytest.raises(TagReadUnavailable) as raised:
        effective_tags.graph_tag_overlay_params(
            "test",
            project_ids=(PROJECT,),
            values=("wanted",),
            deadline=deadline,
            window_start=BEGIN,
            window_end=END,
            execute=stalled,
        )
    assert raised.value.code == "tag_source_unavailable"


@pytest.mark.parametrize("history", [9, 12])
def test_expired_curated_read_checks_suppression_history(stores, monkeypatch, history):
    """A stalled curated read cannot prove the suppression walk is too broad."""
    import time

    lifetime_assignments(names=("other",), count=history)
    monkeypatch.setattr(effective_tags, "_ASSIGNMENT_PAGE_SIZE", 1)
    monkeypatch.setattr(effective_tags, "MAX_GRAPH_OVERLAY_SCAN_PAGES", 10)
    deadline = ReadDeadline.start(60_000)

    def stalled(*_args, **_kwargs):
        object.__setattr__(deadline, "started", time.monotonic() - 61)
        raise ReadDeadlineExceeded("read deadline exceeded")

    with pytest.raises(TagReadUnavailable) as raised:
        effective_tags.graph_tag_overlay_params(
            "test",
            project_ids=(PROJECT,),
            values=("wanted",),
            deadline=deadline,
            window_start=BEGIN,
            window_end=END,
            execute=stalled,
        )
    assert raised.value.code == "tag_source_unavailable"


@pytest.mark.parametrize(
    "history,expected",
    [(9, "tag_source_unavailable"), (12, "tag_overlay_budget_exceeded")],
)
def test_expired_suppression_page_checks_walked_history(
    stores, monkeypatch, history, expected
):
    import time

    lifetime_assignments(names=("other",), count=history)
    monkeypatch.setattr(effective_tags, "_ASSIGNMENT_PAGE_SIZE", 1)
    monkeypatch.setattr(effective_tags, "MAX_GRAPH_OVERLAY_SCAN_PAGES", 10)
    deadline = ReadDeadline.start(60_000)
    walks = {"n": 0}
    scoped_reads = {"n": 0}
    curated = [
        {"legacy_project": PROJECT, "legacy_trace": f"curated-{index}"}
        for index in range(effective_tags.MAX_CURATED_SUPPRESSION_CANDIDATES + 1)
    ]

    def pages(*_args, **_kwargs):
        walks["n"] += 1
        if walks["n"] == 1:
            return iter(())  # matching assignment walk
        return _dead_trace_pages(9, size=1)

    def stalled(_sql, params, **_kwargs):
        if not params["tag_suppress_scoped"]:
            return SimpleNamespace(data=curated)
        scoped_reads["n"] += 1
        if scoped_reads["n"] == 9:
            object.__setattr__(deadline, "started", time.monotonic() - 61)
            raise ReadDeadlineExceeded("read deadline exceeded")
        return SimpleNamespace(data=[])

    monkeypatch.setattr(effective_tags, "_assignment_pages", pages)
    with pytest.raises(TagReadUnavailable) as raised:
        effective_tags.graph_tag_overlay_params(
            "test",
            project_ids=(PROJECT,),
            values=("wanted",),
            deadline=deadline,
            window_start=BEGIN,
            window_end=END,
            execute=stalled,
        )
    assert walks["n"] == 2
    assert raised.value.code == expected


def test_expired_sparse_suppression_lookup_is_retryable(stores, monkeypatch):
    import time

    lifetime_assignments(names=("other",), count=12)
    monkeypatch.setattr(effective_tags, "_ASSIGNMENT_PAGE_SIZE", 1)
    monkeypatch.setattr(effective_tags, "MAX_GRAPH_OVERLAY_SCAN_PAGES", 10)
    deadline = ReadDeadline.start(60_000)
    original = effective_tags._query_assignments

    def stalled_lookup(predicate, *, deadline, limit):
        if limit == effective_tags.MAX_TAG_OVERLAY_KEYS:
            object.__setattr__(deadline, "started", time.monotonic() - 61)
            raise TagReadUnavailable("tag_assignment_unavailable")
        return original(predicate, deadline=deadline, limit=limit)

    def curated(_sql, _params, **_kwargs):
        return SimpleNamespace(
            data=[{"legacy_project": PROJECT, "legacy_trace": "curated"}]
        )

    monkeypatch.setattr(effective_tags, "_query_assignments", stalled_lookup)
    with pytest.raises(TagReadUnavailable) as raised:
        effective_tags.graph_tag_overlay_params(
            "test",
            project_ids=(PROJECT,),
            values=("wanted",),
            deadline=deadline,
            window_start=BEGIN,
            window_end=END,
            execute=curated,
        )
    assert raised.value.code == "tag_assignment_unavailable"


@pytest.mark.unit
def test_failed_history_probe_is_not_too_broad(monkeypatch):
    from django.db.models import Q

    def broken(**_kwargs):
        raise RuntimeError("probe unavailable")

    monkeypatch.setattr(effective_tags, "application_postgres_reads", broken)
    assert not effective_tags._history_exceeds_graph_cap(Q(project_id=PROJECT))


@pytest.mark.unit
def test_later_leaf_deadline_stays_retryable_without_preceding_leaf_probe(monkeypatch):
    import time

    from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
        EffectiveTagGraphCompiler,
    )

    calls = {"n": 0}
    compiler = EffectiveTagGraphCompiler(
        project_id=PROJECT, observe_type="trace", window_start=BEGIN, window_end=END
    )

    def overlay(prefix, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            object.__setattr__(compiler._deadline, "started", time.monotonic() - 61)
            raise TagReadUnavailable("tag_source_unavailable")
        return {f"{prefix}_names": ["wanted"]}, 0

    monkeypatch.setattr(effective_tags, "graph_tag_overlay_params", overlay)
    probes = {"n": 0}

    def probe(*_args, **_kwargs):
        probes["n"] += 1
        return True

    monkeypatch.setattr(effective_tags, "_history_exceeds_graph_cap", probe)
    config = {"filter_op": "equals", "filter_value": "wanted"}
    compiler.requirement(column_id="tag", config=config, filter_index=0)
    with pytest.raises(BoundedGraphReadError) as raised:
        compiler.requirement(column_id="tag", config=config, filter_index=1)
    assert raised.value.retryable
    assert probes["n"] == 0


@pytest.mark.unit
def test_deadline_after_eight_dead_pages_is_too_broad_not_retryable(monkeypatch):
    """Only a proven history over the cap makes a deep deadline terminal."""
    monkeypatch.setattr(effective_tags, "_history_exceeds_graph_cap", lambda *_: True)
    code = _walk_until_deadline(
        monkeypatch,
        spent_on_page=effective_tags.MAX_WINDOW_OVERLAY_PAGES + 1,
        failure=ReadDeadlineExceeded("read deadline exceeded"),
    )
    assert code == "tag_overlay_budget_exceeded"


@pytest.mark.unit
@pytest.mark.parametrize(
    "spent_on_page,failure",
    [
        (2, ReadDeadlineExceeded("read deadline exceeded")),
        (9, OSError("connection reset")),
    ],
    ids=["early-deadline", "outage-after-eight-pages"],
)
def test_early_deadline_or_outage_in_the_walk_stays_retryable(
    monkeypatch, spent_on_page, failure
):
    monkeypatch.setattr(effective_tags, "_history_exceeds_graph_cap", lambda *_: True)
    code = _walk_until_deadline(
        monkeypatch, spent_on_page=spent_on_page, failure=failure
    )
    assert code == "tag_source_unavailable"


@pytest.mark.unit
def test_deadline_after_eight_pages_without_over_cap_proof_stays_retryable(monkeypatch):
    monkeypatch.setattr(effective_tags, "_history_exceeds_graph_cap", lambda *_: False)
    code = _walk_until_deadline(
        monkeypatch,
        spent_on_page=effective_tags.MIN_SCANNED_PAGES_FOR_TOO_BROAD + 1,
        failure=ReadDeadlineExceeded("read deadline exceeded"),
    )
    assert code == "tag_source_unavailable"


@pytest.mark.unit
def test_suppression_walk_deadline_after_eight_pages_is_too_broad(monkeypatch):
    import time

    monkeypatch.setattr(effective_tags, "_history_exceeds_graph_cap", lambda *_: True)

    deadline = ReadDeadline.start(60_000)
    monkeypatch.setattr(
        effective_tags, "_query_assignments", lambda *args, **kwargs: []
    )
    rows = [
        {"legacy_project": PROJECT, "legacy_trace": f"curated-{index}"}
        for index in range(effective_tags.MAX_TAG_OVERLAY_KEYS + 1)
    ]
    scoped = {"n": 0}

    def execute(_sql, params, **_kwargs):
        if not params["tag_suppress_scoped"]:
            return SimpleNamespace(data=rows)
        scoped["n"] += 1
        if scoped["n"] == effective_tags.MAX_WINDOW_OVERLAY_PAGES * 2 + 1:
            object.__setattr__(deadline, "started", time.monotonic() - 61)
            raise ReadDeadlineExceeded("read deadline exceeded")
        return SimpleNamespace(data=[])

    monkeypatch.setattr(
        effective_tags,
        "_assignment_pages",
        lambda *args, **kwargs: _dead_trace_pages(
            effective_tags.MAX_GRAPH_OVERLAY_SCAN_PAGES
        ),
    )
    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        effective_tags.legacy_suppressions(
            execute,
            project_ids=(PROJECT,),
            values=("wanted",),
            deadline=deadline,
            window_start=BEGIN,
            window_end=END,
        )


@pytest.mark.unit
def test_large_curated_window_suppression_walk_is_capped(monkeypatch):
    """Only pages with window-live curated hits consume the suppression budget."""
    monkeypatch.setattr(effective_tags, "MAX_WINDOW_OVERLAY_PAGES", 2)
    monkeypatch.setattr(
        effective_tags, "_query_assignments", lambda *args, **kwargs: []
    )
    rows = [
        {"legacy_project": PROJECT, "legacy_trace": f"curated-{index}"}
        for index in range(effective_tags.MAX_TAG_OVERLAY_KEYS + 1)
    ]

    def execute(_sql, params, **_kwargs):
        return SimpleNamespace(data=rows if not params["tag_suppress_scoped"] else [])

    def pages(*args, **kwargs):
        for index in range(3):
            yield [
                {
                    "project_id": PROJECT,
                    "entity_kind": "trace",
                    "trace_id": f"unmatched-{index}",
                    "manual_tags": [{"name": "other"}],
                }
            ]

    monkeypatch.setattr(effective_tags, "_assignment_pages", pages)
    assert (
        effective_tags.legacy_suppressions(
            execute,
            project_ids=(PROJECT,),
            values=("wanted",),
            deadline=ReadDeadline.start(10_000),
            window_start=BEGIN,
            window_end=END,
        )
        == set()
    )

    def execute_live(_sql, params, **_kwargs):
        if not params["tag_suppress_scoped"]:
            return SimpleNamespace(data=rows)
        return SimpleNamespace(
            data=[
                {"legacy_project": PROJECT, "legacy_trace": trace}
                for trace in params["tag_suppress_traces"]
            ]
        )

    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        effective_tags.legacy_suppressions(
            execute_live,
            project_ids=(PROJECT,),
            values=("wanted",),
            deadline=ReadDeadline.start(10_000),
            window_start=BEGIN,
            window_end=END,
        )


@pytest.mark.unit
def test_curated_suppression_walk_has_a_scanned_page_bound(monkeypatch):
    """Hitless dead pages are free of the live budget, not of all work.

    Without a scanned-page bound only the read deadline stops a lifetime walk,
    and a deadline is a retryable 503 that retrying never fixes.
    """
    monkeypatch.setattr(effective_tags, "MAX_GRAPH_OVERLAY_SCAN_PAGES", 3)
    monkeypatch.setattr(
        effective_tags, "_query_assignments", lambda *args, **kwargs: []
    )
    rows = [
        {"legacy_project": PROJECT, "legacy_trace": f"curated-{index}"}
        for index in range(effective_tags.MAX_TAG_OVERLAY_KEYS + 1)
    ]
    walked = []

    def execute(_sql, params, **_kwargs):
        return SimpleNamespace(data=rows if not params["tag_suppress_scoped"] else [])

    def pages(*args, **kwargs):
        for index in range(10):
            walked.append(index)
            yield [
                {
                    "project_id": PROJECT,
                    "entity_kind": "trace",
                    "trace_id": f"dead-{index}",
                    "manual_tags": [],
                }
            ]

    monkeypatch.setattr(effective_tags, "_assignment_pages", pages)
    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        effective_tags.legacy_suppressions(
            execute,
            project_ids=(PROJECT,),
            values=("wanted",),
            deadline=ReadDeadline.start(10_000),
            window_start=BEGIN,
            window_end=END,
        )
    assert len(walked) == 4


def test_dense_curated_suppression_ignores_dead_lifetime_pages(stores, monkeypatch):
    """More than eight thousand cleared/unmatched old rows must not hide live tags."""
    tagged_project(stores)
    lifetime_assignments(count=8_100)
    for index in range(3):
        trace = f"imported-{index}"
        seed(stores, trace, total_tokens=1 << (16 + index))
        stores.execute(
            "INSERT INTO traces VALUES (%(project)s, %(trace)s, '[\"wanted\"]', 1, 0)",
            {"project": PROJECT, "trace": trace},
        )
    manual("imported-0", ["kept"])
    monkeypatch.setattr(effective_tags, "MAX_TAG_OVERLAY_KEYS", 4)
    monkeypatch.setattr(effective_tags, "MAX_CURATED_SUPPRESSION_CANDIDATES", 4)
    response = post_graph(stores, monkeypatch, "trace", leaf("tag", "wanted"))
    assert graph_total(response) == expected_tokens(
        "trace",
        ("alpha", "beta", "gamma", "delta"),
        ("alpha/root", "beta/child", "gamma/child", "delta/root"),
    ) + (1 << 17) + (1 << 18)
    plan = compile_exact_graph_row_predicates(
        [leaf("tag", "wanted")], project_id=PROJECT, observe_type="trace"
    )
    assert bound(plan, "_assigned_keys") == [
        (("", ""), (PROJECT, "epsilon"), (PROJECT, "imported-0"))
    ]


def test_graph_tag_window_excludes_lifetime_span_assignments(stores):
    seed(stores, "alpha", total_tokens=_tokens("alpha", "root"))
    seed(
        stores,
        "alpha",
        span="child",
        parent_span_id="root",
        total_tokens=_tokens("alpha", "child"),
    )
    manual("alpha", ["wanted"], span="child")
    EntityTagAssignment.objects.bulk_create(
        [
            EntityTagAssignment(
                project_id=PROJECT,
                entity_kind="span",
                trace_id="alpha",
                span_id=f"old-{index:05d}",
                start_hour=BEGIN - timedelta(hours=3),
                observation_type="span",
                service_name="svc",
                manual_tags=[{"name": "wanted", "color": "#123456"}],
            )
            for index in range(LIFETIME_ASSIGNMENTS)
        ]
    )
    assert agent_graph_traces(stores, leaf("tag", "wanted"), ("alpha",)) == {"alpha"}


def test_curated_suppressions_page_when_curated_tags_are_everywhere(
    stores, monkeypatch
):
    """An importer writes curated tags on every trace: the curated set is
    larger than the budget, so unmatched assignments are paged from
    PostgreSQL and ClickHouse is asked, a batch at a time, which of them
    hide a matching curated tag. The answer is the same."""
    tagged_project(stores)
    lifetime_assignments()
    monkeypatch.setattr(effective_tags, "MAX_CURATED_SUPPRESSION_CANDIDATES", 1)
    item = leaf("tag", "wanted", "equals")
    assert counted(stores, "trace", item) == {"alpha", "beta", "gamma", "delta"}
    plan = compile_exact_graph_row_predicates(
        [item], project_id=PROJECT, observe_type="trace"
    )
    assert bound(plan, "_assigned_keys") == [(("", ""), (PROJECT, "epsilon"))]


def test_too_many_suppressions_are_over_budget_not_partial(stores, monkeypatch):
    tagged_project(stores)
    monkeypatch.setattr(effective_tags, "MAX_TAG_OVERLAY_KEYS", 1)
    manual("delta", ["kept"])
    # delta and epsilon both hide a curated ``wanted``: two suppressions.
    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        effective_tags.legacy_suppressions(
            stores.analytics.execute_ch_query,
            project_ids=(PROJECT,),
            values=("wanted",),
            deadline=ReadDeadline.start(10000),
        )


def test_unavailable_curated_read_is_retryable_not_unsuppressed(stores, monkeypatch):
    tagged_project(stores)

    def unavailable(*args, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(effective_tags, "graph_overlay_executor", lambda: unavailable)
    with pytest.raises(BoundedGraphReadError) as raised:
        compile_exact_graph_row_predicates(
            [leaf("tag", "wanted")], project_id=PROJECT, observe_type="trace"
        )
    assert raised.value.retryable


def test_live_matching_assignments_still_fail_closed_over_budget(stores, monkeypatch):
    tagged_project(stores)
    monkeypatch.setattr(effective_tags, "MAX_TAG_OVERLAY_KEYS", 1)
    response = post_graph(stores, monkeypatch, "trace", leaf("tag", "wanted"))
    assert response.status_code == 400, response.data
    assert response.data["code"] == "tag_filter_too_broad"


@pytest.mark.parametrize("observe_type", ["trace", "span"])
@pytest.mark.parametrize("operation", ["is_null", "is_not_null"])
def test_old_assignments_do_not_make_graph_presence_filter_too_broad(
    stores, monkeypatch, observe_type, operation
):
    """Lifetime assignments outside the window cannot exhaust graph binds."""
    tagged_project(stores)
    lifetime_assignments(count=3 * 1_100)
    response = post_graph(
        stores, monkeypatch, observe_type, leaf("tag", None, operation)
    )
    assert response.status_code == 200, response.data


@pytest.mark.parametrize(
    "operation,value,expected",
    [("equals", "late", set()), ("not_equals", "late", {"alpha", "beta"})],
)
def test_trace_graph_tag_ignores_a_tagged_child_outside_the_window(
    stores, operation, value, expected
):
    """The graph's Tag leaf reads the same event-time window as the list.

    alpha's root is in the window; only its child, which starts after the
    window, carries the SDK tag. The list does not match alpha on that tag, so
    neither may the graph, positively or negatively.
    """
    for trace in ("alpha", "beta"):
        seed(stores, trace, span="root", total_tokens=_tokens(trace, "root"))
    seed(
        stores,
        "alpha",
        span="child",
        parent_span_id="root",
        start_time=END + timedelta(minutes=30),
        total_tokens=_tokens("alpha", "child"),
        attrs_string={"tag.tags": '["late"]'},
    )
    item = leaf("tag", value, operation)
    listed = {row["trace_id"] for row in classify(stores, item, ids=("alpha", "beta"))}
    assert listed == expected
    total = sum(int(row["total_tokens"]) for row in graph_rows(stores, "trace", item))
    graphed = {t for t in ("alpha", "beta") if total & _tokens(t, "root")}
    assert graphed == expected


AGENT_GRAPH_HOUR_PRUNE = "AND toStartOfHour(start_time) >= toStartOfHour("


def agent_graph_query(stores, *leaves, traces=TRACES):
    """The Agent Graph statement and the traces whose spans it counted."""
    builder = AgentGraphQueryBuilder(project_id=PROJECT, filters=filters(*leaves))
    sql, params = builder.build()
    assert not any(isinstance(v, EffectiveTagRelation) for v in params.values())
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= set(params)
    rows = stores.execute(sql, params)
    total = sum(int(row["total_tokens"]) for row in rows if row["row_kind"] == "node")
    return sql, {trace for trace in traces if total & _tokens(trace, "root")}


def agent_graph_traces(stores, item, traces=TRACES):
    """The traces whose spans the Observe Agent Graph counted under ``item``."""
    return agent_graph_query(stores, item, traces=traces)[1]


@pytest.mark.parametrize(
    "column,operation,value,expected",
    TRACE_CASES,
    ids=[f"{c}-{o}-{v}" for c, o, v, _ in TRACE_CASES],
)
def test_agent_graph_matches_the_trace_list(stores, column, operation, value, expected):
    tagged_project(stores)
    item = leaf(column, value, operation)
    listed = {row["trace_id"] for row in classify(stores, item, ids=TRACES)}
    assert listed == expected
    assert agent_graph_traces(stores, item) == expected


@pytest.mark.parametrize(
    "operation,value,expected",
    [
        ("equals", "late", set()),
        ("not_equals", "late", {"alpha", "beta"}),
        ("equals", "early", set()),
        ("not_equals", "early", {"alpha", "beta"}),
        ("equals", "inside", {"beta"}),
        ("not_equals", "inside", {"alpha"}),
    ],
)
def test_agent_graph_tag_ignores_a_tagged_child_outside_the_window(
    stores, operation, value, expected
):
    """The Agent Graph's Tag leaf reads the same event-time window as the list.

    Both roots are in the window. alpha's only tagged spans start after
    (``late``) or before (``early``) it; beta's child carries ``inside`` within
    it. The list does not match alpha on an out-of-window tag, so neither may
    the Agent Graph, positively or negatively.
    """
    for trace in ("alpha", "beta"):
        seed(stores, trace, span="root", total_tokens=_tokens(trace, "root"))
    for span, start, tag in (
        ("late", END + timedelta(minutes=30), "late"),
        ("early", BEGIN - timedelta(hours=3), "early"),
    ):
        seed(
            stores,
            "alpha",
            span=span,
            parent_span_id="root",
            start_time=start,
            total_tokens=_tokens("alpha", "child"),
            attrs_string={"tag.tags": f'["{tag}"]'},
        )
    seed(
        stores,
        "beta",
        span="child",
        parent_span_id="root",
        total_tokens=_tokens("beta", "child"),
        attrs_string={"tag.tags": '["inside"]'},
    )
    item = leaf("tag", value, operation)
    listed = {row["trace_id"] for row in classify(stores, item, ids=("alpha", "beta"))}
    assert listed == expected
    assert agent_graph_traces(stores, item, ("alpha", "beta")) == expected


def test_agent_graph_tag_window_leaves_other_leaves_unbounded(stores):
    """Only the Tag witness is windowed; other leaves keep any-span witnesses.

    alpha's in-window root carries the SDK tag; its only ``customer=acme``
    span starts three hours after the window. An attribute leaf is witnessed
    by any span at any time, with or without a Tag leaf beside it, exactly as
    in the trace list (see test_tag_leaf_does_not_hour_bound_other_any_span_leaves).
    """
    seed(
        stores,
        "alpha",
        total_tokens=_tokens("alpha", "root"),
        attrs_string={"tag.tags": '["sdk"]'},
    )
    seed(
        stores,
        "alpha",
        span="late-child",
        parent_span_id="root",
        start_time=END + timedelta(hours=3),
        total_tokens=_tokens("alpha", "child"),
        attrs_string={"customer": "acme"},
    )
    seed(
        stores,
        "beta",
        total_tokens=_tokens("beta", "root"),
        attrs_string={"tag.tags": '["sdk"]'},
    )
    attribute = {
        "column_id": "customer",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "acme",
        },
    }
    traces = ("alpha", "beta")
    sql, graphed = agent_graph_query(
        stores, leaf("tag", "sdk"), attribute, traces=traces
    )
    assert graphed == {"alpha"}
    sql, graphed = agent_graph_query(stores, leaf("tag", "sdk"), traces=traces)
    assert graphed == {"alpha", "beta"}
    # Remote hours still provide membership and monetary ancestry, but their
    # physical projection must not evaluate the Tag JSON predicate.
    assert sql.count("FROM spans") == 2
    assert "graph_row_match_0" in sql
    remote = sql.split("UNION ALL", 1)[1].split("AS graph_physical_versions", 1)[0]
    assert "JSONExtractRaw(attributes_extra" not in remote
    _, params = AgentGraphQueryBuilder(
        project_id=PROJECT, filters=filters(leaf("tag", "sdk"))
    ).build()
    explain = stores.execute("EXPLAIN PLAN " + remote.rsplit(")", 1)[0], params)
    assert "attributes_extra" not in "\n".join(row["explain"] for row in explain)


@pytest.mark.parametrize("graph", ["agent", "primary"])
def test_graph_suppressions_ignore_curated_traces_outside_the_window(
    stores, monkeypatch, graph
):
    """Curated tags hidden by an assignment matter only for window-live traces.

    Old traces whose only roots started before the window carry a curated
    ``wanted`` that their saved assignments replaced. They can never be in the
    graph, so they must not consume the suppression budget either.
    """
    seed(stores, "alpha", total_tokens=_tokens("alpha", "root"))
    seed(
        stores,
        "alpha",
        span="child",
        parent_span_id="root",
        total_tokens=_tokens("alpha", "child"),
    )
    manual("alpha", ["wanted"])
    for trace in ("delta", "epsilon"):
        seed(
            stores,
            trace,
            start_time=BEGIN - timedelta(hours=3),
            total_tokens=_tokens(trace, "root"),
        )
        stores.execute(
            "INSERT INTO traces VALUES (%(project)s, %(trace)s, '[\"wanted\"]', 1, 0)",
            {"project": PROJECT, "trace": trace},
        )
        manual(trace, ["kept"])
    monkeypatch.setattr(effective_tags, "MAX_TAG_OVERLAY_KEYS", 1)
    item = leaf("tag", "wanted")
    if graph == "agent":
        assert agent_graph_traces(stores, item, ("alpha", "delta", "epsilon")) == {
            "alpha"
        }
    else:
        assert counted(stores, "trace", item) == {"alpha"}


def test_curated_window_roots_replay_only_identity_columns():
    """Curated suppression must not decode source-tag JSON for every span."""
    sql = effective_tags.legacy_trace_tags_sql("tag_suppress", live_roots=True)
    root_scope = sql.split("AND (project_id, id) IN (", 1)[1].split("FROM spans", 1)[0]
    assert (
        "argMax(tuple(start_time, ifNull(parent_span_id, ''), is_deleted)" in root_scope
    )
    assert "tags, attrs_string" not in root_scope
    assert "attributes_extra" not in root_scope


def test_exact_membership_scopes_tag_assignments_to_snapshot(stores):
    seed(stores, "alpha")
    manual("alpha", ["wanted"])
    EntityTagAssignment.objects.bulk_create(
        [
            EntityTagAssignment(
                project_id=PROJECT,
                entity_kind="trace",
                trace_id=f"old-{index:05d}",
                span_id="",
                start_hour=TRACE_ASSIGNMENT_HOUR,
                observation_type="",
                service_name="",
                manual_tags=[{"name": "wanted", "color": "#123456"}],
            )
            for index in range(LIFETIME_ASSIGNMENTS)
        ]
    )
    item = leaf("tag", "wanted")
    assert {row["trace_id"] for row in membership_rows(stores, "trace", item)} == {
        "alpha"
    }
    from tracer.services.clickhouse import exact_graph_reads
    from tracer.services.clickhouse.v2.query_builders.eval_metrics import (
        EvalMetricsQueryBuilderV2,
    )

    assert exact_graph_reads._compile_membership_filter(
        project_id=PROJECT,
        filters=[item],
        observe_type="trace",
        window_start=BEGIN,
        window_end=END,
    )[0]
    assert exact_graph_reads._session_membership_plan(
        project_id=PROJECT,
        filters=[item],
        window_start=BEGIN,
        window_end=END,
    ).relational_predicates
    session_sql, session_params = exact_graph_reads._session_aggregate_source_sql(
        project_id=PROJECT,
        filters=[item],
        start_date=BEGIN,
        end_date=END,
        include_trace_ids=False,
        candidate_trace_ids_param="candidate_trace_ids",
    )
    assert session_sql
    assert session_params["session_relational_0_etag_graph_0_trace_keys"] == (
        ("", ""),
        (PROJECT, "alpha"),
    )
    builder = EvalMetricsQueryBuilderV2(
        project_id=PROJECT,
        custom_eval_config_id="33333333-3333-4333-8333-333333333333",
        start_date=BEGIN,
        end_date=END,
        interval="hour",
        filters=[item],
    )
    assert builder._filter_fragment()


def test_window_overlay_walk_does_not_cap_dead_pages(stores, monkeypatch):
    """Only the deadline and live bind count bound old assignments."""
    for index in range(3):
        manual(f"old-{index}", ["wanted"])
    monkeypatch.setattr(effective_tags, "_ASSIGNMENT_PAGE_SIZE", 2)
    monkeypatch.setattr(effective_tags, "MAX_WINDOW_OVERLAY_PAGES", 1)
    overlay = effective_tags.read_tag_overlay(
        project_ids=(PROJECT,),
        trace_ids=None,
        values=("wanted",),
        deadline=ReadDeadline.start(10000),
        window_start=BEGIN,
        window_end=END,
        execute=stores.analytics.execute_ch_query,
    )
    assert overlay.trace_names == {}
    response = post_graph(stores, monkeypatch, "trace", leaf("tag", "wanted"))
    assert response.status_code == 200, response.data


def test_window_scan_does_not_budget_dead_matching_lifetime_assignments(
    stores, monkeypatch
):
    """The Users graph's window scan binds only assignments live in the window.

    Over 2,048 aged assignments carry the filtered name. Bound without the
    window they exceeded the overlay budget, so the graph could never answer.
    """
    from tracer.tests.test_effective_tag_scale import window_count

    tagged_project(stores)
    lifetime_assignments(names=("wanted",), count=3_200)
    assert window_count(stores, monkeypatch, "wanted") == 4


def _users_graph_over_a_real_overlay(stores, monkeypatch, failure=None):
    """GET Users graph whose system-metric read is the real window scan."""
    from tracer.tests import test_user_session_graph_action_deadline as users
    from tracer.tests.test_effective_tag_scale import window_count

    tagged_project(stores)

    def read(_metric):
        if failure is not None:
            raise failure
        window_count(stores, monkeypatch, "wanted")
        raise AssertionError("the overlay should be over budget")

    # The helper's system-metric fetch builds its payload here.
    monkeypatch.setattr(users, "_complete_graph", read)
    response, _events, _dispatched = users._call_project_user_action(
        monkeypatch,
        "get_users_aggregate_graph_data",
        deadline_values=[60_000, 60_000],
    )
    return response


def test_users_graph_too_broad_tag_filter_is_a_request_error(stores, monkeypatch):
    """Live matching assignments over the overlay budget: a 400 with the Tag
    filter copy, never a 503 that asks the user to retry."""
    monkeypatch.setattr(effective_tags, "MAX_TAG_OVERLAY_KEYS", 1)
    response = _users_graph_over_a_real_overlay(stores, monkeypatch)
    assert response.status_code == 400, response.data
    assert response.data["code"] == "tag_filter_too_broad"


def test_users_graph_unavailable_tag_source_stays_retryable(stores, monkeypatch):
    from tracer.services.entity_tags import TagReadUnavailable

    response = _users_graph_over_a_real_overlay(
        stores, monkeypatch, failure=TagReadUnavailable("tag_source_unavailable")
    )
    assert response.status_code == 503, response.data


def _scheduled_users_graph(stores, monkeypatch, *metric_filters):
    """Run the Users system graph as the largest tenants do: the cost gate
    says the window is unaffordable, so the read is handed to the worker.
    Returns the scheduling calls (the cache-only probe is not one)."""
    tagged_project(stores)
    monkeypatch.setattr(
        graph_dispatch,
        "_affordable_user_graph_read",
        lambda **_kwargs: graph_dispatch._GraphReadUnaffordable(None),
    )
    scheduled = []

    def read_or_refresh(**kwargs):
        if kwargs.get("schedule_on_miss") is False:
            return None
        scheduled.append(kwargs)
        return {"query_status": "pending", "query_refreshing": True}

    monkeypatch.setattr(graph_dispatch, "_read_or_refresh_exact_graph", read_or_refresh)
    analytics = SimpleNamespace(
        execute_ch_query=lambda sql, params=None, **kwargs: (
            stores.analytics.execute_ch_query(sql, params, **kwargs)
        ),
    )
    graph_dispatch.fetch_user_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT,
        filters=filters(*metric_filters),
        interval="hour",
        metric_id="active_users",
        organization_id="org-1",
        workspace_id=None,
    )
    return scheduled


def test_scheduled_users_graph_too_broad_tag_filter_is_a_request_error(
    stores, monkeypatch
):
    """A Users graph the cost gate hands to the worker binds its Tag leaves
    as the worker will before scheduling: live matching assignments over the
    overlay budget are the inline read's 400, never a worker failure that
    every later poll reports as a 503 asking to retry."""
    monkeypatch.setattr(effective_tags, "MAX_TAG_OVERLAY_KEYS", 1)
    with pytest.raises(GraphTagFilterTooBroadError):
        _scheduled_users_graph(stores, monkeypatch, leaf("tag", "wanted"))


def test_scheduled_users_graph_within_the_tag_budget_is_scheduled(stores, monkeypatch):
    scheduled = _scheduled_users_graph(stores, monkeypatch, leaf("tag", "wanted"))
    assert [call["namespace"] for call in scheduled] == ["observe-user-system-graph"]


@pytest.mark.parametrize(
    "namespace,aggregation_context",
    [
        ("observe-user-system-graph", None),
        ("observe-eval-graph", "user"),
        ("observe-annotation-graph", "user"),
    ],
)
def test_scheduled_users_graph_preflight_compiles_aggregate_eval_leaves(
    monkeypatch, namespace, aggregation_context
):
    """A Tag leaf next to an aggregate eval leaf (``eval_score >= 50``): the
    Users selector proves eval ownership, which needs a request deadline. The
    preflight must give it one, or the graph is a 500 instead of being
    scheduled (or refused for a too-broad Tag)."""
    from tracer.services.clickhouse import exact_graph_reads

    owned = []

    def owned_ids(project_id, *, started, remaining_read_ms=None):
        assert started is not None, "eval ownership needs a request deadline"
        owned.append(project_id)
        return ("eval-config",)

    monkeypatch.setattr(exact_graph_reads, "_owned_user_eval_config_ids", owned_ids)
    bound = []
    monkeypatch.setattr(
        effective_tags,
        "prepare_effective_tag_params",
        lambda _execute, params, **kwargs: bound.append(kwargs) or params,
    )
    eval_leaf = {
        "column_id": "eval_score",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": "greater_than_or_equal",
            "filter_value": 50,
        },
    }
    identity = {
        "project_id": PROJECT,
        "filters": filters(leaf("tag", "wanted"), eval_leaf),
    }
    if aggregation_context:
        identity["aggregation_context"] = aggregation_context
    scheduled = []
    monkeypatch.setattr(
        graph_dispatch,
        "_read_or_refresh_exact_graph",
        lambda **kwargs: scheduled.append(kwargs) or {"query_status": "pending"},
    )
    graph_dispatch._schedule_unaffordable_graph_read(
        metric_id="m",
        verdict=graph_dispatch._GraphReadUnaffordable(None),
        identity=identity,
        pending_payload={"query_status": "pending"},
        refresh=False,
        organization_id="org-1",
        workspace_id=None,
        namespace=namespace,
        analytics=SimpleNamespace(execute_ch_query=None),
    )
    assert owned == [PROJECT]
    assert bound and bound[0]["allow_window_scan"] and bound[0]["window_overlay"]
    assert [call["namespace"] for call in scheduled] == [namespace]
