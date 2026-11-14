"""Real disposable CH25 + PostgreSQL proofs for discovery and pre-page matching."""

import re
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.filter_value_reads import (
    read_span_system_filter_value_cursor_page,
    read_span_system_filter_values,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2 import effective_tags
from tracer.services.clickhouse.v2.effective_tags import (
    EffectiveTagRelation,
    prepare_effective_tag_params,
    read_tag_overlay,
)
from tracer.services.clickhouse.v2.entity_tag_targets import TRACE_ASSIGNMENT_HOUR
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.entity_tags import TagReadUnavailable
from tracer.tests import test_entity_tag_readback as readback
from tracer.tests.test_entity_tag_readback import (
    assignment_schema,  # noqa: F401
    physical_engine,  # noqa: F401
)
from tracer.tests.test_entity_tag_target import PROJECT, START
from tracer.tests.test_entity_tag_targets_ch25 import OTHER_PROJECT

stores = readback.stores

BEGIN = START - timedelta(hours=1)
END = START + timedelta(hours=1)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def leaf(column="tag", value="sdk", operation="equals", col_type="SYSTEM_METRIC"):
    return {
        "column_id": column,
        "filter_config": {
            "col_type": col_type,
            "filter_type": "array" if column == "tags" else "text",
            "filter_op": operation,
            "filter_value": value,
        },
    }


def filters(*leaves):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [BEGIN, END],
            },
        },
        *leaves,
    ]


def manual(trace, names, *, project=PROJECT, span="", service="svc"):
    return EntityTagAssignment.objects.create(
        project_id=project,
        entity_kind="span" if span else "trace",
        trace_id=trace,
        span_id=span,
        start_hour=START.replace(minute=0, second=0, microsecond=0)
        if span
        else TRACE_ASSIGNMENT_HOUR,
        observation_type="span" if span else "",
        service_name=service if span else "",
        manual_tags=[{"name": name, "color": "#123456"} for name in names],
    )


def seed(stores, trace, *, span="root", **kwargs):
    row = {
        "project_id": PROJECT,
        "trace_id": trace,
        "id": span,
        "start_time": START,
        "observation_type": "span",
        "service_name": "svc",
        "parent_span_id": "",
        "_version": 1,
        "tags": "[]",
        "attrs_string": {},
        "attributes_extra": "{}",
    }
    row.update(kwargs)
    stores.insert(**row)


def classify(stores, *leaves, ids=("alpha", "beta"), project_ids=None):
    scope = {"project_ids": project_ids} if project_ids else {"project_id": PROJECT}
    builder = TraceListQueryBuilderV2(**scope, filters=filters(*leaves))
    query, params = builder.build_filter_match_query(list(ids))
    assert "AND start_time >= toStartOfHour(toDateTime64(%(start_date)s" in query
    params = prepare_effective_tag_params(
        stores.analytics.execute_ch_query,
        params,
        deadline=ReadDeadline.start(10000),
    )
    return stores.analytics.execute_ch_query(query, params).data


def test_sdk_and_manual_discovery_use_effective_names(stores):
    seed(
        stores,
        "alpha",
        attrs_string={"tag.tags": '["sdk","duplicate"]'},
        tags='[{"name":"column","color":"#112233"}]',
        attributes_extra='{"tag.tags":["overflow","duplicate"]}',
    )
    seed(stores, "beta")
    seed(stores, "alpha", project_id=OTHER_PROJECT, tags='["foreign"]')
    seed(stores, "old", start_time=BEGIN - timedelta(days=8), tags='["old"]')
    manual("alpha", ["ui", "duplicate"])
    manual("alpha", ["foreign-ui"], project=OTHER_PROJECT)
    for column in ("tag", "tags"):
        result = read_span_system_filter_values(
            stores.analytics, project_ids=[PROJECT], metric_name=column, now=END
        )
        assert result.query_complete
        assert result.values == ("column", "duplicate", "overflow", "sdk", "ui")


@pytest.mark.parametrize("column", ["tag", "tags"])
@pytest.mark.parametrize(
    "operation,value,expected",
    [
        ("equals", "sdk", ["alpha"]),
        ("in", ["ui"], ["alpha"]),
        ("contains", ["ui"], ["alpha"]),
        ("not_contains", ["ui"], ["beta"]),
        ("not_equals", "sdk", ["beta"]),
        ("is_null", None, ["beta"]),
        ("is_not_null", None, ["alpha"]),
    ],
)
def test_membership_exact_and_negated(column, operation, value, expected, stores):
    seed(stores, "alpha", attrs_string={"tag.tags": '["sdk"]'})
    seed(stores, "beta")
    manual("alpha", ["ui"])
    rows = classify(stores, leaf(column, value, operation))
    assert sorted(row["trace_id"] for row in rows) == expected


def test_child_domain_and_physical_identity(stores):
    seed(stores, "alpha")
    seed(stores, "beta")
    seed(stores, "alpha", span="child", parent_span_id="root")
    seed(
        stores,
        "alpha",
        span="child",
        parent_span_id="root",
        service_name="other",
        tags='["other-service"]',
    )
    manual("alpha", ["child-ui"], span="child")
    manual("alpha", ["root-ui"])
    assert [row["trace_id"] for row in classify(stores, leaf("tag", "child-ui"))] == [
        "alpha"
    ]
    assert classify(stores, leaf("tags", ["child-ui"], "contains")) == []
    assert [
        row["trace_id"]
        for row in classify(stores, leaf("tags", ["root-ui"], "contains"))
    ] == ["alpha"]
    compiler = ClickHouseFilterBuilderV2(project_id=PROJECT, query_mode="span")
    condition, params = compiler.translate([leaf("tag", "child-ui")])
    params.update(
        project_id=PROJECT,
        candidate_trace_ids=("alpha",),
        start_date=BEGIN,
        end_date=END,
    )
    params = prepare_effective_tag_params(
        stores.analytics.execute_ch_query, params, deadline=ReadDeadline.start(10000)
    )
    rows = stores.analytics.execute_ch_query(
        "SELECT id, service_name FROM spans FINAL WHERE " + condition, params
    ).data
    assert rows == [{"id": "child", "service_name": "svc"}]


def test_latest_state_clear_and_tenant_fence(stores):
    seed(stores, "alpha", tags='["stale"]')
    seed(
        stores,
        "alpha",
        _version=2,
        tags='["sdk"]',
        start_time=START + timedelta(minutes=1),
    )
    seed(stores, "beta", tags='["deleted"]')
    seed(stores, "beta", _version=2, is_deleted=1)
    seed(stores, "alpha", project_id=OTHER_PROJECT, tags='["foreign"]')
    stores.execute(
        "INSERT INTO traces VALUES (%(project)s, 'alpha', '[\"legacy\"]', 1, 0)",
        {"project": PROJECT},
    )
    manual("alpha", [])
    assert classify(stores, leaf("tag", "stale")) == []
    assert classify(stores, leaf("tag", "legacy")) == []
    assert classify(stores, leaf("tag", "deleted")) == []
    assert classify(stores, leaf("tag", "foreign")) == []
    rows = classify(
        stores, leaf("tag", "foreign"), project_ids=[PROJECT, OTHER_PROJECT]
    )
    assert [(str(row["project_id"]), row["trace_id"]) for row in rows] == [
        (OTHER_PROJECT, "alpha")
    ]


def test_picker_continuation_inside_multi_tag_row_and_search(stores):
    seed(stores, "alpha", attrs_string={"tag.tags": '["a","b","c","d","e"]'})
    manual("alpha", ["f", "g"])
    state = {}
    found = []
    for _ in range(20):
        page = read_span_system_filter_value_cursor_page(
            stores.analytics,
            project_ids=[PROJECT],
            metric_name="tag",
            page_size=2,
            window_start=BEGIN,
            window_end=END,
            deadline=ReadDeadline.start(10000),
            **state,
        )
        found.extend(page.values)
        if not page.has_more:
            break
        state = {
            "segment_end": page.next_segment_end,
            "segment_start": page.next_segment_start,
            "value_after": page.next_value_after,
            "seen_value_digests": page.seen_value_digests,
        }
    else:
        pytest.fail("picker did not exhaust")
    assert sorted(found) == list("abcdefg")
    assert len(set(found)) == len(found)
    result = read_span_system_filter_values(
        stores.analytics, project_ids=[PROJECT], metric_name="tag", now=END, search="F"
    )
    assert result.values == ("f",)


def test_filter_before_pagination_and_count_across_empty_batches(stores):
    for index in range(12):
        seed(stores, f"t{index:02}", start_time=START + timedelta(seconds=index))
    for trace in ("t01", "t03", "t05"):
        manual(trace, ["ui-only"])
    active = filters(leaf("tags", ["ui-only"], "contains"))
    for page_number, expected, has_more in (
        (0, ["t05", "t03"], True),
        (1, ["t01"], False),
    ):
        builder = TraceListQueryBuilderV2(project_id=PROJECT, filters=active)
        page = read_bounded_filter_page(
            builder=builder,
            analytics=stores.analytics,
            filters=active,
            key_field="trace_id",
            page_number=page_number,
            page_size=2,
            classify_batch_size=2,
            max_candidates=4,
            deadline_ms=30000,
        )
        assert page.complete, page
        assert [row["trace_id"] for row in page.rows] == expected
        assert page.has_more is has_more
        assert page.total_rows_lower_bound == 3
        # Preparation reads only PostgreSQL: no telemetry rows are fetched to
        # build the classifier, which evaluates tag sources in place.
        assert not any(attempt.kind == "tag_source" for attempt in page.attempts)


def test_source_qualified_tag_does_not_dispatch_to_overlay():
    compiler = ClickHouseFilterBuilderV2(project_id=PROJECT)
    _, params = compiler.translate([leaf("tag", "customer", col_type="SPAN_ATTRIBUTE")])
    assert not any(isinstance(value, EffectiveTagRelation) for value in params.values())
    execute = Mock()
    assert (
        prepare_effective_tag_params(execute, params, deadline=ReadDeadline.start(1000))
        is params
    )
    execute.assert_not_called()


def test_bounded_pg_lookup_and_failures_are_not_empty_results(stores, monkeypatch):
    for i in range(20):
        seed(stores, f"t{i}")
        manual(f"t{i}", ["ui"])
    with CaptureQueriesContext(connection) as queries:
        overlay = read_tag_overlay(
            project_ids=(PROJECT,),
            trace_ids=("t1", "t2"),
            values=("ui",),
            deadline=ReadDeadline.start(10000),
        )
    assert sorted(overlay.trace_names) == [(PROJECT, "t1"), (PROJECT, "t2")]
    assignment_queries = [
        q["sql"]
        for q in queries.captured_queries
        if 'FROM "tracer_entity_tag_assignment"' in q["sql"]
    ]
    assert len(assignment_queries) == 1
    assert (
        "t1" in assignment_queries[0]
        and "t2" in assignment_queries[0]
        and "t19" not in assignment_queries[0]
        and "LIMIT" in assignment_queries[0]
    )
    monkeypatch.setattr(
        effective_tags,
        "_query_assignments",
        Mock(side_effect=TagReadUnavailable("tag_assignment_unavailable")),
    )
    with pytest.raises(TagReadUnavailable):
        read_span_system_filter_values(
            stores.analytics, project_ids=[PROJECT], metric_name="tag", now=END
        )
    with pytest.raises(TagReadUnavailable):
        classify(stores, leaf("tag", "ui"))


def test_array_any_membership_and_conjunction(stores):
    seed(stores, "alpha", attrs_string={"tag.tags": '["source"]'})
    seed(stores, "beta", tags='["source"]')
    manual("alpha", ["manual"])
    assert [
        row["trace_id"]
        for row in classify(
            stores, leaf("tag", ["missing", "manual"], "in"), leaf("tag", "source")
        )
    ] == ["alpha"]
    assert classify(stores, leaf("tag", "MANUAL")) == []


def test_source_bounds_and_overlay_overflow_are_explicit(stores, monkeypatch):
    seed(stores, "alpha")
    seed(stores, "beta")
    manual("alpha", ["ui"])
    manual("beta", ["ui"])
    execute = Mock(wraps=stores.analytics.execute_ch_query)
    rows = classify(stores, leaf("tag", "ui"))
    assert sorted(row["trace_id"] for row in rows) == ["alpha", "beta"]
    params = {
        "effective_tag_rows_1": EffectiveTagRelation((PROJECT,), ("ui",)),
        "candidate_trace_ids": ("alpha", "beta"),
        "start_date": BEGIN,
        "end_date": END,
    }
    prepared = prepare_effective_tag_params(
        execute, params, deadline=ReadDeadline.start(10000)
    )
    # Source and curated tags are evaluated in SQL: preparation binds only the
    # scope and the PostgreSQL overlay, and reads no telemetry.
    execute.assert_not_called()
    assert prepared["effective_tag_rows_1_trace_keys"] == (
        ("", ""),
        (PROJECT, "alpha"),
        (PROJECT, "beta"),
    )
    # alpha and beta match through their own assignment, so whether they also
    # suppress curated tags cannot change the result: they are not bound twice.
    assert prepared["effective_tag_rows_1_assigned_keys"] == (("", ""),)
    # Human-scale overlay overflow is unavailable, never a truncated set:
    # by key count, and by rendered bytes so a statement never approaches
    # ClickHouse's max_query_size.
    monkeypatch.setattr(effective_tags, "MAX_TAG_OVERLAY_KEYS", 1)
    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        prepare_effective_tag_params(
            execute, params, deadline=ReadDeadline.start(10000)
        )
    monkeypatch.setattr(effective_tags, "MAX_TAG_OVERLAY_KEYS", 2048)
    monkeypatch.setattr(effective_tags, "MAX_TAG_OVERLAY_BYTES", 64)
    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        prepare_effective_tag_params(
            execute, params, deadline=ReadDeadline.start(10000)
        )


def test_direct_count_uses_same_effective_relation(stores, monkeypatch):
    from types import SimpleNamespace

    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    seed(stores, "alpha", attrs_string={"tag.tags": '["sdk"]'})
    seed(stores, "beta")
    manual("alpha", ["ui"])
    service = object.__new__(V2AnalyticsQueryService)
    service._ch_client = SimpleNamespace(
        server_enforced_readonly=False, server_profile_locked=False
    )
    service._read_timeout_ceiling_ms = 30000
    calls = []

    def execute(_self, sql, params=None, **kwargs):
        calls.append((sql, kwargs))
        return stores.analytics.execute_ch_query(sql, params, **kwargs)

    monkeypatch.setattr(AnalyticsQueryService, "execute_ch_query", execute)
    condition, params = ClickHouseFilterBuilderV2(project_id=PROJECT).translate(
        [leaf("tag", "ui")]
    )
    params.update(project_id=PROJECT, start_date=BEGIN, end_date=END)
    result = service.execute_ch_query(
        "SELECT count() AS count FROM spans FINAL WHERE project_id = %(project_id)s "
        "AND start_time >= %(start_date)s AND start_time < %(end_date)s AND ("
        + condition
        + ")",
        params,
        timeout_ms=10000,
    )
    assert result.data == [{"count": 1}]
    # Source and curated tags are evaluated in place. Before the count, one
    # read finds which PostgreSQL assignments are live in the window, so only
    # those are bound (the window overlay, as graph leaves have); both run
    # under the request's deadline.
    assert len(calls) == 2
    assert "SELECT DISTINCT" in calls[0][0]
    assert all(call[1]["server_execution_cap_ms"] > 0 for call in calls)


def test_exact_refresh_worker_binds_effective_tag_relations(stores, monkeypatch):
    """The exact-refresh worker's executor binds a Tag leaf, as the request
    path does: the Users graph's worker read compiles one through
    ClickHouseFilterBuilderV2, unbound."""
    from tracer.services.clickhouse import v2 as v2_module
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.tasks import exact_aggregation

    seed(stores, "alpha", attrs_string={"tag.tags": '["sdk"]'})
    seed(stores, "beta")
    manual("alpha", ["ui"])
    monkeypatch.setattr(
        v2_module,
        "get_v2_config",
        lambda: {
            "host": "127.0.0.1",
            "tcp_port": 9,
            "http_port": 9,
            "user": "default",
            "password": "",
            "database": "test_th7275_worker",
            "server_enforced_readonly": False,
        },
    )
    sent = []

    def execute(_self, sql, params=None, **kwargs):
        # The base service's read, run on chdb: only what reaches ClickHouse.
        sent.append(params)
        return stores.analytics.execute_ch_query(sql, params, **kwargs)

    monkeypatch.setattr(AnalyticsQueryService, "execute_ch_query", execute)
    condition, params = ClickHouseFilterBuilderV2(project_id=PROJECT).translate(
        [leaf("tag", "ui")]
    )
    params.update(project_id=PROJECT, start_date=BEGIN, end_date=END)
    with exact_aggregation._exact_observe_analytics() as analytics:
        result = analytics.execute_ch_query(
            "SELECT count() AS count FROM spans FINAL WHERE project_id = %(project_id)s "
            "AND start_time >= %(start_date)s AND start_time < %(end_date)s AND ("
            + condition
            + ")",
            params,
            timeout_ms=10000,
        )
    assert result.data == [{"count": 1}]
    from tracer.services.clickhouse.v2.effective_tags import EffectiveTagRelation

    assert not any(
        isinstance(value, EffectiveTagRelation)
        for bound in sent
        for value in (bound or {}).values()
    )


@pytest.mark.parametrize(
    "column,value,operation", [("tag", "ui", "equals"), ("tags", ["ui"], "contains")]
)
def test_http_trace_list_filters_manual_tag(
    stores, monkeypatch, column, value, operation
):
    from tracer.tests.test_entity_tag_target import TRACE

    stores.seed()
    manual(TRACE, ["ui"])
    monkeypatch.setattr(
        readback, "window", lambda: filters(leaf(column, value, operation))
    )
    rows = readback.trace_list(stores)
    assert len(rows) == 1
    assert {item["name"] for item in rows[0]["tags"]} == {"sdk", "sdk-attribute", "ui"}
    assert len(readback.span_list(stores)) == 1


def test_native_root_matches_selected_project_version(stores):
    from tracer.tests.test_entity_tag_readback import PV

    seed(stores, "alpha", span="old-root", project_version_id=PV, tags='["selected"]')
    seed(
        stores,
        "alpha",
        span="new-root",
        start_time=START + timedelta(minutes=1),
        tags='["other-version"]',
    )
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT,
        project_version_id=PV,
        filters=filters(leaf("tags", ["selected"], "contains")),
    )
    sql, params = builder.build_filter_match_query(["alpha"])
    params = prepare_effective_tag_params(
        stores.analytics.execute_ch_query, params, deadline=ReadDeadline.start(10000)
    )
    rows = stores.analytics.execute_ch_query(sql, params).data
    assert [row["root_span_id"] for row in rows] == ["old-root"]


@pytest.mark.parametrize("column", ["tag", "tags"])
def test_filter_values_http_returns_effective_names_and_sanitizes_failure(
    stores, monkeypatch, column
):
    from types import SimpleNamespace

    from tracer.tests.test_filter_value_http_deadline import _ProjectScopeStub
    from tracer.views import dashboard as dashboard_view

    seed(stores, "alpha", attrs_string={"tag.tags": '["sdk"]'})
    manual("alpha", ["ui"])
    monkeypatch.setattr(
        dashboard_view,
        "project_queryset_for_request",
        lambda _request: _ProjectScopeStub([PROJECT]),
    )
    monkeypatch.setattr(
        dashboard_view, "_run_filter_value_pg_read", lambda _deadline, read: read()
    )
    monkeypatch.setattr(
        dashboard_view, "V2AnalyticsQueryService", lambda: stores.analytics
    )
    monkeypatch.setattr(
        dashboard_view,
        "read_span_system_filter_values",
        lambda analytics, **kwargs: read_span_system_filter_values(
            analytics, now=END, **kwargs
        ),
    )
    request = SimpleNamespace(
        validated_query_data={
            "metric_name": column,
            "metric_type": "system_metric",
            "source": "traces",
            "project_ids": [PROJECT],
        },
        workspace=SimpleNamespace(id="00000000-0000-4000-8000-000000000002"),
    )
    response = dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(), request
    )
    assert response.status_code == 200, response.data
    assert [row["value"] for row in response.data["result"]["values"]] == ["sdk", "ui"]
    monkeypatch.setattr(
        effective_tags,
        "_query_assignments",
        Mock(side_effect=TagReadUnavailable("private dependency detail")),
    )
    response = dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(), request
    )
    assert response.status_code == 503, response.data
    assert "private dependency detail" not in str(response.data)


def test_sdk_names_use_filter_limits_not_assignment_limits(stores):
    import json

    name = "sdk-" + "x" * 300
    seed(stores, "alpha", attrs_string={"tag.tags": json.dumps([name])})
    assert [row["trace_id"] for row in classify(stores, leaf("tag", name))] == ["alpha"]


def test_native_tags_use_classifier_root_after_datetime_exclusion(stores):
    seed(stores, "alpha", span="selected", tags='["selected-root"]')
    excluded_time = START + timedelta(minutes=1)
    seed(
        stores,
        "alpha",
        span="excluded",
        start_time=excluded_time,
        tags='["excluded-root"]',
    )
    exclusion = {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "not_equals",
            "filter_value": excluded_time,
        },
    }
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=filters(leaf("tags", ["selected-root"], "contains"), exclusion),
    )
    sql, params = builder.build_filter_match_query(["alpha"])
    params = prepare_effective_tag_params(
        stores.analytics.execute_ch_query, params, deadline=ReadDeadline.start(10000)
    )
    rows = stores.analytics.execute_ch_query(sql, params).data
    assert [row["root_span_id"] for row in rows] == ["selected"]


def _encoded(value, times):
    import json

    for _ in range(times):
        value = json.dumps(value)
    return value


@pytest.mark.parametrize(
    "tags,attr,extra",
    [
        ('["sdk","dup"]', "", "{}"),
        (
            '[{"name":"column","color":"#112233"},{"name":7},{"color":"#1"},"  ",3,null]',
            "",
            "{}",
        ),
        ('["\\t"]', '["a1","dup"]', "{}"),
        ("[]", _encoded(["d1"], 2), "{}"),
        ("[]", _encoded(["t3"], 3), "{}"),
        ("[]", _encoded(["q4"], 4), "{}"),
        ("[]", "", '{"tag.tags":["e1","e2"],"input.value":"big"}'),
        ("[]", "", _encoded({"tag.tags": _encoded(["es"], 2)}, 1)),
        ("[]", "", _encoded({"tag.tags": _encoded(["w3"], 3)}, 1)),
        ("[]", "", _encoded({"tag.tags": _encoded(["w4"], 4)}, 1)),
        ("[]", "", '{"tag":{"tags":["n1"]},"tag.tags":["f1"]}'),
        ("not json", "{bad", '{"tag.tags":{"x":1}}'),
        ('"x"', "5", '{"tag.tags":7}'),
        ('["a"]', '["a","b"]', '{"tag.tags":["b","c"]}'),
    ],
)
def test_in_place_source_names_match_reader_semantics(
    physical_engine,  # noqa: F811
    tags,
    attr,
    extra,
):
    """Filters/pickers (SQL) and list/detail tags (Python) parse identically."""
    from tracer.services.clickhouse.v2.effective_tags import source_names_sql
    from tracer.services.entity_tags import source_tag_names

    execute, _ = physical_engine
    names = source_names_sql(
        ("%(tags)s", "%(attr)s"),
        (
            "JSONExtractRaw(%(extra)s, 'tag.tags')",
            "JSONExtractRaw(%(extra)s, 'tag', 'tags')",
        ),
    )
    rows = execute(
        f"SELECT {names} AS names", {"tags": tags, "attr": attr, "extra": extra}
    )
    expected = source_tag_names(
        {
            "tags": tags,
            "attrs_string": {"tag.tags": attr} if attr else {},
            "attributes_extra": extra,
        }
    )
    assert tuple(rows[0]["names"]) == expected


def _attribute_leaf(key, value):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _classify_unbounded(stores, *leaves, ids=("alpha", "beta")):
    builder = TraceListQueryBuilderV2(project_id=PROJECT, filters=filters(*leaves))
    query, params = builder.build_filter_match_query(list(ids))
    params = prepare_effective_tag_params(
        stores.analytics.execute_ch_query,
        params,
        deadline=ReadDeadline.start(10000),
    )
    rows = stores.analytics.execute_ch_query(query, params).data
    return query, sorted(row["trace_id"] for row in rows)


def test_tag_leaf_does_not_hour_bound_other_any_span_leaves(stores):
    """A Tag leaf must not change how other any-span leaves read children.

    The classifier replays every child version of a finite candidate trace
    for any-span leaves, because a child may start long after its root. Only
    the Tag relation is scoped to the request window, inside its own subquery.
    """
    late = END + timedelta(hours=3)
    seed(stores, "alpha", attrs_string={"tag.tags": '["sdk"]'})
    seed(
        stores,
        "alpha",
        span="late-child",
        parent_span_id="root",
        start_time=late,
        attrs_string={"customer": "acme"},
    )
    seed(stores, "beta", attrs_string={"tag.tags": '["sdk"]'})
    attribute = _attribute_leaf("customer", "acme")
    _, without_tag = _classify_unbounded(stores, attribute)
    assert without_tag == ["alpha"]
    query, with_tag = _classify_unbounded(stores, leaf("tag", "sdk"), attribute)
    assert with_tag == ["alpha"]
    assert "AND start_time >= toStartOfHour(toDateTime64(%(start_date)s" not in query
    # A Tag leaf alone still replays only the window's physical hours.
    query, tag_only = _classify_unbounded(stores, leaf("tag", "sdk"))
    assert tag_only == ["alpha", "beta"]
    assert "AND start_time >= toStartOfHour(toDateTime64(%(start_date)s" in query


def test_cursor_reuses_assignment_snapshot_across_time_slices(stores, monkeypatch):
    from tracer.services.clickhouse import filter_value_reads

    seed(stores, "new", start_time=START + timedelta(seconds=2, milliseconds=500))
    seed(stores, "old", start_time=START + timedelta(milliseconds=500))
    manual("new", ["new-ui"])
    manual("old", ["old-ui"])
    monkeypatch.setattr(
        filter_value_reads, "FILTER_VALUE_CURSOR_INITIAL_SEGMENT", timedelta(seconds=1)
    )
    lookup = Mock(wraps=effective_tags._query_assignments)
    monkeypatch.setattr(effective_tags, "_query_assignments", lookup)
    page = read_span_system_filter_value_cursor_page(
        stores.analytics,
        project_ids=[PROJECT],
        metric_name="tag",
        page_size=10,
        window_start=START,
        window_end=START + timedelta(seconds=3),
        deadline=ReadDeadline.start(60000),
    )
    assert page.values == ("new-ui", "old-ui")
    assert lookup.call_count == 1


def test_curated_picker_limits_both_history_reads_to_live_roots(stores, monkeypatch):
    seed(stores, "live")
    seed(stores, "outside", start_time=BEGIN - timedelta(days=8))
    stores.execute(
        "INSERT INTO traces VALUES (%(project)s, 'live', '[\"curated\"]', 1, 0)",
        {"project": PROJECT},
    )
    stores.execute(
        "INSERT INTO traces VALUES (%(project)s, 'outside', '[\"outside\"]', 1, 0)",
        {"project": PROJECT},
    )
    execute = stores.analytics.execute_ch_query
    queries = []

    def observed(sql, params=None, **kwargs):
        if "tag_legacy_vocab" in sql:
            queries.append(sql)
        return execute(sql, params, **kwargs)

    monkeypatch.setattr(stores.analytics, "execute_ch_query", observed)
    result = read_span_system_filter_values(
        stores.analytics,
        project_ids=[PROJECT],
        metric_name="tag",
        now=END,
    )
    assert result.values == ("curated",)
    assert len(queries) == 1
    # Both the replay and tag-bearing candidate set must be limited before
    # reading the project's entire curated history.
    assert queries[0].count("AND (project_id, id) IN (\n") == 2
    assert queries[0].count("SELECT project_id, trace_id\n") == 2


def test_window_suppressions_do_not_read_curated_history_outside_roots(stores):
    seed(stores, "live")
    seed(stores, "outside", start_time=BEGIN - timedelta(days=8))
    for trace in ("live", "outside"):
        stores.execute(
            "INSERT INTO traces VALUES (%(project)s, %(trace)s, '[\"curated\"]', 1, 0)",
            {"project": PROJECT, "trace": trace},
        )
        manual(trace, [])
    _, params = ClickHouseFilterBuilderV2(project_id=PROJECT).translate(
        [leaf("tag", "curated")]
    )
    params.update(start_date=BEGIN, end_date=END)
    queries = []
    execute = stores.analytics.execute_ch_query

    def observed(sql, bound=None, **kwargs):
        queries.append(sql)
        return execute(sql, bound, **kwargs)

    prepared = prepare_effective_tag_params(
        observed, params, deadline=ReadDeadline.start(10000), allow_window_scan=True
    )
    assert prepared["effective_tag_rows_1_assigned_keys"] == (
        ("", ""),
        (PROJECT, "live"),
    )
    assert len(queries) == 1
    assert queries[0].count("AND (project_id, id) IN (\n") == 2


def test_classifier_curated_replay_is_bounded_by_window_live_roots():
    for column in ("tag", "tags"):
        builder = ClickHouseFilterBuilderV2(project_id=PROJECT)
        sql, _ = builder.translate([leaf(column, "curated")])
        assert sql.count("AND (project_id, id) IN (\n") == 2


_RELATION_REF = re.compile(r"%\(([A-Za-z0-9_]*effective_tag_rows_\d+)_projects\)s")


@pytest.mark.parametrize(
    "leaves",
    [
        pytest.param([leaf("tag", "wanted")], id="tag-only"),
        pytest.param(
            [leaf("status", "OK"), leaf("tag", "wanted")], id="span-filter-then-tag"
        ),
        pytest.param([leaf("tag", "wanted"), leaf("tag", "other")], id="two-tags"),
    ],
)
def test_user_selector_binds_each_tag_leaf_under_its_own_name(leaves):
    """The Users selector (Users graph, user-context eval and annotation graphs)
    compiles each leaf with its own builder and namespaces its parameters. A
    Tag leaf's relation is read through derived names (``<name>_projects`` …),
    so it must be renamed and bound with them: otherwise a Tag leaf after any
    other span filter reads an unbound relation, and a second Tag leaf reads
    the first one's UI assignments. The discarded raw-span clause must not
    bind a relation of its own, which would be read and budgeted for nothing.
    """
    from tracer.services.clickhouse import exact_graph_reads
    from tracer.services.clickhouse.v2.effective_tags import EffectiveTagRelation

    sql, params, _ = exact_graph_reads._user_aggregate_source_sql(
        project_id=PROJECT,
        filters=leaves,
        start_date=BEGIN,
        end_date=END,
        include_trace_ids=False,
        all_snapshot_users=True,
    )
    relations = {
        name: value
        for name, value in params.items()
        if isinstance(value, EffectiveTagRelation)
    }
    referenced = set(_RELATION_REF.findall(sql))
    assert referenced <= set(relations), referenced - set(relations)
    assert set(relations) <= referenced, set(relations) - referenced
    tag_values = sorted(
        (item["filter_config"]["filter_value"],)
        for item in leaves
        if item["column_id"] == "tag"
    )
    assert sorted(relations[name].values for name in referenced) == tag_values


@pytest.mark.parametrize(
    "leaves",
    [
        pytest.param(
            [leaf("status", "OK"), leaf("tag", "wanted")], id="span-filter-then-tag"
        ),
        pytest.param([leaf("tag", "wanted"), leaf("tag", "other")], id="two-tags"),
    ],
)
def test_user_selector_has_no_unbound_tag_placeholder_after_binding(
    monkeypatch, leaves
):
    """After the binder runs, every placeholder of the Users selector has a
    value, and each Tag leaf's overlay is read for its own tag names."""
    from tracer.services.clickhouse import exact_graph_reads
    from tracer.services.clickhouse.read_budget import ReadDeadline
    from tracer.services.clickhouse.v2 import effective_tags

    read = []

    def overlay(**kwargs):
        read.append(kwargs["values"])
        return effective_tags.TagOverlay()

    monkeypatch.setattr(effective_tags, "read_tag_overlay", overlay)
    sql, params, _ = exact_graph_reads._user_aggregate_source_sql(
        project_id=PROJECT,
        filters=leaves,
        start_date=BEGIN,
        end_date=END,
        include_trace_ids=False,
        all_snapshot_users=True,
    )
    bound = effective_tags.prepare_effective_tag_params(
        lambda *args, **kwargs: None,
        {**params, "start_date": BEGIN, "end_date": END},
        deadline=ReadDeadline.start(10_000),
        allow_window_scan=True,
    )
    placeholders = set(re.findall(r"%\(([A-Za-z0-9_]+)\)s", sql))
    assert placeholders - set(bound) == set()
    assert sorted(read) == sorted(
        (item["filter_config"]["filter_value"],)
        for item in leaves
        if item["column_id"] == "tag"
    )
