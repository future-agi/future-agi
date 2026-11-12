"""Tag filters and pickers at agent-scale density, on real disposable stores.

SDK tags sit on every span of an agent trace beside multi-KB LLM input/output,
and some importers write curated ``traces.tags`` on every trace. Filters and
pickers must stay exact without shipping spans, attribute maps or per-trace
curated rows into a statement, and a statement over ClickHouse's
``max_query_size`` must be a 503, never a 500. Run only on a disposable cloud
worker; this module imports only entry points the tag reader always had.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver.errors import ServerException

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.filter_value_reads import (
    read_span_system_filter_value_cursor_page,
    read_span_system_filter_values,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests import test_entity_tag_readback as readback
from tracer.tests.test_effective_tag_filters import (
    BEGIN,
    END,
    filters,
    leaf,
    manual,
)
from tracer.tests.test_entity_tag_readback import (
    PV,
    assignment_schema,  # noqa: F401
    physical_engine,  # noqa: F401
)
from tracer.tests.test_entity_tag_target import PROJECT, START
from tracer.views import trace as trace_views

stores = readback.stores

DENSE_TRACES = 200
DENSE_SPANS = 45
NEEDLES = ("dense-0017", "dense-0103", "dense-0188")
UI_ROOTS = ("dense-0042", "dense-0150")
UI_CHILD = "dense-0077"
CURATED_TRACES = 2500


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def dense_seed(stores):
    """200 traces x 45 spans = 9,000 live spans with 5-10 KiB of LLM input.

    All inside one hour, so no time slice can narrow them. The SDK sends tags
    as an OTel array, which the collector keeps in attributes_extra next to
    input.value.
    """
    stores.execute(
        """
        INSERT INTO spans (project_id, observation_type, service_name, start_time,
            trace_id, id, parent_span_id, project_version_id, attrs_string,
            attributes_extra, tags, _version)
        SELECT %(project)s, 'span', 'svc',
            fromUnixTimestamp64Micro(%(start)s + intDiv(number, %(spans)s) * 1000
                + modulo(number, %(spans)s)),
            concat('dense-', leftPad(toString(intDiv(number, %(spans)s)), 4, '0')),
            if(modulo(number, %(spans)s) = 0, 'root',
               concat('child-', toString(modulo(number, %(spans)s)))),
            if(modulo(number, %(spans)s) = 0, '', 'root'),
            toUUID(%(pv)s),
            map('output.value', repeat('o', 64)),
            concat('{"tag.tags":["agent"],"input.value":"',
                   repeat('x', 5120 + modulo(number, 6) * 1024), '"}'),
            '[]', 1
        FROM numbers(%(rows)s)
        """,
        {
            "project": PROJECT,
            "start": int(START.timestamp() * 1_000_000),
            "spans": DENSE_SPANS,
            "rows": DENSE_TRACES * DENSE_SPANS,
            "pv": PV,
        },
    )
    # A newer physical version of one child span per needle trace adds a tag.
    for trace in NEEDLES:
        index = int(trace.split("-")[1])
        stores.insert(
            project_id=PROJECT,
            trace_id=trace,
            id="child-7",
            observation_type="span",
            service_name="svc",
            start_time=START + timedelta(milliseconds=index, microseconds=7),
            parent_span_id="root",
            project_version_id=PV,
            attributes_extra='{"tag.tags":["agent","needle"],"input.value":"y"}',
            _version=2,
        )
    for trace in UI_ROOTS:
        manual(trace, ["ui-needle"])
    manual(UI_CHILD, ["child-ui"], span="child-3")


def page(stores, *leaves, page_size=50):
    active = filters(*leaves)
    builder = TraceListQueryBuilderV2(project_id=PROJECT, filters=active)
    return read_bounded_filter_page(
        builder=builder,
        analytics=stores.analytics,
        filters=active,
        key_field="trace_id",
        page_number=0,
        page_size=page_size,
        deadline_ms=120000,
    )


def observe_trace_list(stores, *leaves):
    """The Observe trace grid's own handler, with its HTTP error mapping."""
    request = readback.get_request()
    view = trace_views.TraceView()
    view.request = request
    window, *tag_leaves = filters(*leaves)
    window = {
        **window,
        "filter_config": {
            **window["filter_config"],
            "filter_value": [BEGIN.isoformat(), END.isoformat()],
        },
    }
    return view._list_traces_of_session_clickhouse(
        request,
        PROJECT,
        {"filters": [window, *tag_leaves], "page_number": 0, "page_size": 30},
        stores.analytics,
    )


def window_count(stores, monkeypatch, tag):
    """A direct graph/count caller: the effective relation over the window."""
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    service = object.__new__(V2AnalyticsQueryService)
    service._ch_client = SimpleNamespace(
        server_enforced_readonly=False, server_profile_locked=False
    )
    service._read_timeout_ceiling_ms = 60000
    monkeypatch.setattr(
        AnalyticsQueryService,
        "execute_ch_query",
        lambda _self, sql, params=None, **kwargs: stores.analytics.execute_ch_query(
            sql, params, **kwargs
        ),
    )
    condition, params = ClickHouseFilterBuilderV2(project_id=PROJECT).translate(
        [leaf("tag", tag)]
    )
    params.update(project_id=PROJECT, start_date=BEGIN, end_date=END)
    result = service.execute_ch_query(
        "SELECT count(DISTINCT trace_id) AS count FROM spans FINAL "
        "WHERE project_id = %(project_id)s AND start_time >= %(start_date)s "
        "AND start_time < %(end_date)s AND (" + condition + ")",
        params,
        timeout_ms=60000,
    )
    return result.data[0]["count"]


def picker(stores, column):
    finite = read_span_system_filter_values(
        stores.analytics, project_ids=[PROJECT], metric_name=column, now=END
    )
    assert finite.query_complete, finite
    found, state = [], {}
    for _ in range(50):
        cursor = read_span_system_filter_value_cursor_page(
            stores.analytics,
            project_ids=[PROJECT],
            metric_name=column,
            page_size=2,
            window_start=BEGIN,
            window_end=END,
            deadline=ReadDeadline.start(60000),
            **state,
        )
        found.extend(cursor.values)
        if not cursor.has_more:
            break
        state = {
            "segment_end": cursor.next_segment_end,
            "segment_start": cursor.next_segment_start,
            "value_after": cursor.next_value_after,
            "seen_value_digests": cursor.seen_value_digests,
        }
    return finite.values, tuple(sorted(found))


@pytest.mark.parametrize(
    "leaves,expected",
    [
        ((leaf("tag", "needle"),), sorted(NEEDLES)),
        ((leaf("tag", "child-ui"),), [UI_CHILD]),
        ((leaf("tags", ["ui-needle"], "contains"),), sorted(UI_ROOTS)),
        ((leaf("tags", ["child-ui"], "contains"),), []),
        # Two tag leaves used to double the inlined per-span relation.
        ((leaf("tag", "needle"), leaf("tag", "agent")), sorted(NEEDLES)),
        ((leaf("tag", ["needle", "ui-needle"], "in"),), sorted(NEEDLES + UI_ROOTS)),
    ],
)
def test_dense_agent_traces_filter_exactly_through_the_classifier(
    stores, leaves, expected
):
    dense_seed(stores)
    result = page(stores, *leaves)
    assert result.complete, result
    assert sorted(row["trace_id"] for row in result.rows) == expected
    assert result.has_more is False


def test_dense_sdk_tag_on_every_span_pages_all_traces(stores):
    dense_seed(stores)
    every = page(stores, leaf("tag", "agent"))
    assert every.complete, every
    assert len(every.rows) == 50 and every.has_more is True
    negated = page(stores, leaf("tag", "needle", "not_equals"))
    assert negated.complete, negated
    assert len(negated.rows) == 50
    assert not set(NEEDLES) & {row["trace_id"] for row in negated.rows}


@pytest.mark.parametrize(
    "tag_leaf,expected",
    [
        (leaf("tag", "needle"), sorted(NEEDLES)),
        (leaf("tag", "ui-needle"), sorted(UI_ROOTS)),
    ],
)
def test_dense_observe_trace_grid_filters_by_tag(stores, tag_leaf, expected):
    dense_seed(stores)
    response = observe_trace_list(stores, tag_leaf)
    assert response.status_code == 200, response.data
    rows = response.data["result"]["table"]
    assert sorted(row["trace_id"] for row in rows) == expected
    for row in rows:
        assert "agent" in {tag["name"] for tag in row["tags"]}


@pytest.mark.parametrize("column", ["tag", "tags"])
def test_dense_pickers_list_sdk_and_manual_names(stores, column):
    dense_seed(stores)
    expected = (
        ("agent", "child-ui", "needle", "ui-needle")
        if column == "tag"
        else ("agent", "ui-needle")
    )
    assert picker(stores, column) == (expected, expected)


def test_dense_window_scan_count_uses_in_place_source_tags(stores, monkeypatch):
    dense_seed(stores)
    assert window_count(stores, monkeypatch, "needle") == 3
    assert window_count(stores, monkeypatch, "agent") == DENSE_TRACES
    assert window_count(stores, monkeypatch, "ui-needle") == 2
    assert window_count(stores, monkeypatch, "child-ui") == 1


def curated_seed(stores):
    """An importer that writes curated tags on every trace it creates."""
    stores.execute(
        """
        INSERT INTO spans (project_id, observation_type, service_name, start_time,
            trace_id, id, parent_span_id, project_version_id, attrs_string,
            attributes_extra, tags, _version)
        SELECT %(project)s, 'span', 'svc',
            fromUnixTimestamp64Micro(%(start)s + number * 1000),
            concat('imported-', leftPad(toString(number), 5, '0')), 'root', '',
            toUUID(%(pv)s), map(), '{}', '[]', 1
        FROM numbers(%(rows)s)
        """,
        {
            "project": PROJECT,
            "start": int(START.timestamp() * 1_000_000),
            "rows": CURATED_TRACES,
            "pv": PV,
        },
    )
    stores.execute(
        """
        INSERT INTO traces
        SELECT %(project)s, concat('imported-', leftPad(toString(number), 5, '0')),
            if(number = 7, '["imported","cleared-name"]', '["imported"]'), 1, 0
        FROM numbers(%(rows)s)
        """,
        {"project": PROJECT, "rows": CURATED_TRACES},
    )
    # A saved (empty) assignment suppresses that trace's curated fallback.
    manual("imported-00007", [])


def test_machine_scale_curated_tags_filter_and_list_without_a_key_budget(
    stores, monkeypatch
):
    curated_seed(stores)
    assert window_count(stores, monkeypatch, "imported") == CURATED_TRACES - 1
    assert window_count(stores, monkeypatch, "cleared-name") == 0
    every = page(stores, leaf("tag", "imported"))
    assert every.complete, every
    assert len(every.rows) == 50 and every.has_more is True
    # A selective leaf over thousands of candidates may stop at the scan
    # budget, but never reports a suppressed curated tag as a match.
    cleared = page(stores, leaf("tags", ["cleared-name"], "contains"))
    assert cleared.rows == []
    assert picker(stores, "tag") == (("imported",), ("imported",))


def test_statement_over_max_query_size_is_unavailable_not_a_server_error(
    stores, monkeypatch
):
    stores.seed()
    execute = stores.analytics.execute_ch_query

    def refuse_classifier(sql, params=None, **kwargs):
        if "effective_tag_rows" in sql:
            raise ServerException("Max query size exceeded at position 262133", code=62)
        return execute(sql, params, **kwargs)

    monkeypatch.setattr(stores.analytics, "execute_ch_query", refuse_classifier)
    response = observe_trace_list(stores, leaf("tag", "sdk"))
    assert response.status_code == 503, response.data
