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
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
    compile_exact_graph_filter_predicates,
)
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.read_budget import ReadDeadline
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
        [item], project_id=PROJECT, observe_type=observe_type
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


def post_graph(stores, monkeypatch, observe_type, item):
    """TraceView/ObservationSpanView.get_graph_methods for one filter.

    Only the cache probe and the EXPLAIN-based affordability probe are
    replaced (both are optional decorations of a cold read); compilation,
    the overlay reads, the statement and the HTTP mapping are the real ones.
    """
    from tracer.views import observation_span as span_views
    from tracer.views import trace as trace_views

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


@pytest.mark.parametrize("observe_type", ["trace", "span"])
@pytest.mark.parametrize("operation", ["is_null", "is_not_null"])
def test_too_broad_tag_filter_is_a_clear_400_not_a_retry(
    stores, monkeypatch, observe_type, operation
):
    """What remains capped: more traces and spans tagged in the app with the
    filtered name (any name for is null / is not null) than one statement can
    bind. That is a property of the project, so the graph says so (400 with
    its own code and copy) instead of a 503 that asks the user to retry."""
    tagged_project(stores)
    lifetime_assignments(count=3 * 1_100)
    response = post_graph(
        stores, monkeypatch, observe_type, leaf("tag", None, operation)
    )
    assert response.status_code == 400, response.data
    assert response.data["code"] == "tag_filter_too_broad"
    assert response.data["message"] == (
        "The graph cannot apply this Tag filter: too many traces and spans "
        "were tagged with it in the app. The list below is still filtered by it."
    )


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
    # A Tag leaf keeps the unpruned physical read: out-of-window intermediate
    # spans still prove monetary ancestry for in-window usage.
    assert AGENT_GRAPH_HOUR_PRUNE not in sql
