"""Span-only key propagation and in-process ReplacingMergeTree proofs.

Engine tests use chdb.Session(tmp_path), never a ClickHouse socket or environment
credentials. Install chdb in a disposable Python path to enable those tests.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver import Client

from tracer.selectors.trace_filter_reads import (
    read_bounded_filter_neighbors,
    read_bounded_filter_page,
)
from tracer.services.clickhouse.query_builders.span_list import (
    SpanListQueryBuilder,
    _unix_microseconds,
)
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.views.observation_span import (
    _merge_span_page_content,
    _span_cursor_order_for_partial_page,
    _span_identity_payload,
    _span_page_dedup_fields,
    _span_page_identity_sets,
)

PROJECT = "11111111-1111-1111-1111-111111111111"
OTHER_PROJECT = "22222222-2222-2222-2222-222222222222"
PV = "33333333-3333-3333-3333-333333333333"
START = datetime(2026, 8, 8, 12)


def time_filter(start=START, end=START + timedelta(hours=1)):
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def attr_filter(op="equals", value=1):
    return {
        "column_id": "tag",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "number",
            "filter_op": op,
            "filter_value": value,
        },
    }


def builder(**kwargs):
    return SpanListQueryBuilderV2(
        **{
            "project_id": PROJECT,
            "filters": [time_filter(), attr_filter()],
            "bounded_internal_scan": True,
            **kwargs,
        }
    )


def row(**kwargs):
    return {
        "project_id": PROJECT,
        "trace_id": "trace",
        "id": "span",
        "observation_type": "span",
        "service_name": "service-a",
        "start_time": START + timedelta(minutes=20),
        "_version": 1,
        **kwargs,
    }


@pytest.mark.unit
def test_legacy_and_v2_identity_and_keyset_are_separate():
    old = SpanListQueryBuilder(project_id=PROJECT, filters=[time_filter()])
    new = builder()
    sample = row()
    assert old.bounded_filter_row_identity(sample) == (
        PROJECT,
        "trace",
        "span",
        sample["start_time"],
    )
    assert new.bounded_filter_row_identity(sample) == (
        PROJECT,
        "trace",
        "span",
        START.replace(tzinfo=UTC),
        "span",
        "service-a",
    )
    corrected = row(start_time=START + timedelta(minutes=10))
    assert new.bounded_filter_row_identity(sample) == new.bounded_filter_row_identity(
        corrected
    )
    assert len(old.bounded_filter_row_order_token(sample)) == 3
    assert len(new.bounded_filter_row_order_token(sample)) == 5
    for legacy in (True, False):
        target = old if legacy else new
        sql, _ = target.build_filter_seed_page(
            slice_start=START,
            slice_end=START + timedelta(hours=1),
            limit=1,
            before_start_time=sample["start_time"],
            before_id=target.bounded_filter_row_order_token(sample),
        )
        assert ("FROM spans FINAL" in sql) is not legacy
        assert ("filter_before_service_name" in sql) is not legacy


@pytest.mark.unit
def test_v2_refuses_identity_downgrade_and_scope_escape():
    target = builder()
    with pytest.raises(ValueError, match="complete physical"):
        target.build_filter_match_query_from_seed_rows(
            [{k: v for k, v in row().items() if k != "service_name"}]
        )
    with pytest.raises(ValueError, match="six-part"):
        target.build_content_query(
            ["span"], span_identities=[(PROJECT, "trace", "span", START)]
        )
    with pytest.raises(ValueError, match="escaped request scope"):
        target.build_filter_match_query_from_seed_rows([row(project_id=OTHER_PROJECT)])


@pytest.mark.unit
def test_view_merge_checks_full_identity_and_winner_not_only_count():
    target = builder()
    page = [row(), row(service_name="service-b")]
    content = [{**item, "input": item["service_name"]} for item in page]
    physical, external, _ = _span_page_identity_sets(page, builder=target)
    assert len(physical) == 2 and len(external) == 1
    assert len(_span_page_dedup_fields(page, target)) == 6
    assert _merge_span_page_content(page, content, builder=target, keys=("input",))
    assert [item["input"] for item in page] == ["service-a", "service-b"]
    for wrong in (
        [content[0], content[0]],
        [content[0], {**content[1], "service_name": "other"}],
        [content[0], {**content[1], "_version": 2}],
        [content[0], {**content[1], "start_time": START}],
    ):
        assert not _merge_span_page_content(
            page, wrong, builder=target, keys=("input",)
        )
    order = _span_cursor_order_for_partial_page(
        rows=page, bounded_page=None, cursor_state=None
    )
    assert len(order) == 6
    assert order[1:] == target.bounded_filter_row_order_token(page[-1])


@pytest.mark.unit
def test_http_identity_payload_preserves_uint64_and_discriminators():
    payload = _span_identity_payload(row(_version=2**64 - 1))
    assert payload == {
        "_version": "18446744073709551615",
        "observation_type": "span",
        "service_name": "service-a",
    }
    assert _span_identity_payload({})["_version"] is None


@pytest.fixture
def engine(tmp_path):
    pytest.importorskip("chdb")
    from chdb.session import Session

    session = Session(str(tmp_path / "embedded-rmt"))
    # Only local formatting is used; this client never connects anywhere.
    formatter = Client("invalid.invalid")
    formatter.connection.context.server_info = SimpleNamespace(
        get_timezone=lambda: "UTC"
    )

    def execute(sql, params=None):
        if params:
            sql = formatter.substitute_params(sql, params, formatter.connection.context)
        result = str(session.query(sql, "JSONEachRow"))
        rows = [json.loads(line) for line in result.splitlines() if line]
        for value in rows:
            if isinstance(value.get("start_time"), str):
                value["start_time"] = datetime.fromisoformat(value["start_time"])
        return rows

    execute("""CREATE TABLE spans (
        project_id UUID, observation_type LowCardinality(String),
        service_name LowCardinality(String), start_time DateTime64(6, 'UTC'),
        trace_id String, id String, parent_span_id Nullable(String),
        name String DEFAULT '', status Nullable(String),
        end_time Nullable(DateTime64(6, 'UTC')), latency_ms Nullable(Int64),
        cost Float64 DEFAULT 0, total_tokens Int64 DEFAULT 0,
        prompt_tokens Int64 DEFAULT 0, completion_tokens Int64 DEFAULT 0,
        model String DEFAULT '', provider String DEFAULT '',
        project_version_id Nullable(UUID), end_user_id Nullable(UUID),
        created_at DateTime64(6, 'UTC') DEFAULT start_time,
        attrs_string Map(String, String), attrs_number Map(String, Float64),
        attrs_bool Map(String, UInt8), attributes_extra String DEFAULT '{}',
        input String DEFAULT '', output String DEFAULT '',
        is_deleted UInt8 DEFAULT 0, _version UInt64
    ) ENGINE = ReplacingMergeTree(_version, is_deleted)
      PARTITION BY toDate(start_time)
      PRIMARY KEY (project_id, observation_type, service_name, toStartOfHour(start_time))
      ORDER BY (project_id, observation_type, service_name,
                toStartOfHour(start_time), trace_id, id)""")
    execute("SYSTEM STOP MERGES spans")

    def insert(**kwargs):
        value = {**row(), "attrs_number": {"tag": 1}, **kwargs}
        columns = list(value)
        params = {f"v{index}": val for index, val in enumerate(value.values())}
        literals = []
        for index, column in enumerate(columns):
            if column == "start_time":
                params[f"v{index}"] = _unix_microseconds(value[column])
                literals.append(f"fromUnixTimestamp64Micro(%(v{index})s)")
            else:
                literals.append(f"%(v{index})s")
        execute(
            f"INSERT INTO spans ({', '.join(columns)}) VALUES ({', '.join(literals)})",
            params,
        )

    yield execute, insert
    session.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    "replacement",
    [
        {"is_deleted": 1},
        {"attrs_number": {}},
        {"attrs_number": {"tag": 0}},
    ],
)
def test_engine_corrected_time_does_not_revive_stale_match(engine, replacement):
    execute, insert = engine
    insert()
    insert(start_time=START + timedelta(minutes=10), _version=2, **replacement)
    # Demonstrate why four-part argMax is insufficient even without a tie.
    assert (
        execute("""SELECT count() AS n FROM (
        SELECT argMax(is_deleted, _version) AS gone,
               argMax(attrs_number['tag'], _version) AS tag
        FROM spans GROUP BY project_id, trace_id, id, start_time
    ) WHERE gone = 0 AND tag = 1""")[0]["n"]
        == 1
    )
    target = builder()
    assert execute(*target.build_filter_match_query_from_seed_rows([row()])) == []
    assert (
        execute(
            *target.build_filter_seed_page(
                slice_start=START,
                slice_end=START + timedelta(hours=1),
                limit=2,
            )
        )
        == []
    )


@pytest.mark.integration
def test_engine_separate_physical_keys_and_tie_pagination(engine):
    execute, insert = engine
    insert()
    insert(service_name="service-b")
    insert(observation_type="tool")
    insert(service_name="deleted-service", is_deleted=1, _version=9)
    insert(project_id=OTHER_PROJECT)
    target = builder()
    samples = [row(), row(service_name="service-b"), row(observation_type="tool")]
    matches = execute(*target.build_filter_match_query_from_seed_rows(samples))
    assert len(matches) == 3
    seen = []
    previous = None
    for _ in range(4):
        page = execute(
            *target.build_filter_seed_page(
                slice_start=START,
                slice_end=START + timedelta(hours=1),
                limit=1,
                before_start_time=previous["start_time"] if previous else None,
                before_id=target.bounded_filter_row_order_token(previous)
                if previous
                else None,
            )
        )
        if not page:
            break
        previous = page[0]
        seen.append(target.bounded_filter_row_identity(previous))
    assert len(seen) == len(set(seen)) == 3
    assert set(seen) == {target.bounded_filter_row_identity(value) for value in samples}
    # A bare public span id is ambiguous, not silently collapsed.
    assert (
        len(execute(*target.build_filter_navigation_target_query(target_id="span")))
        == 2
    )
    assert len(execute(*target.build_filter_match_query(["span"]))) == 3


@pytest.mark.integration
@pytest.mark.parametrize("new_pv", [None, OTHER_PROJECT])
def test_engine_project_version_applies_after_latest(engine, new_pv):
    execute, insert = engine
    insert(project_version_id=PV)
    insert(_version=2, project_version_id=new_pv)
    target = builder(project_version_id=PV)
    assert execute(*target.build_filter_match_query_from_seed_rows([row()])) == []
    assert (
        execute(
            *target.build_filter_seed_page(
                slice_start=START,
                slice_end=START + timedelta(hours=1),
                limit=2,
            )
        )
        == []
    )
    assert (
        execute(
            *target.build_content_query(
                ["span"],
                span_identities=[target.bounded_filter_row_identity(row())],
            )
        )
        == []
    )


@pytest.mark.integration
@pytest.mark.parametrize("old_minute,new_minute", [(20, 10), (10, 20), (20, 40)])
def test_engine_boundary_replay_and_null_content(engine, old_minute, new_minute):
    execute, insert = engine
    insert(start_time=START + timedelta(minutes=old_minute), latency_ms=9, input="old")
    latest = START + timedelta(minutes=new_minute)
    insert(
        start_time=latest, _version=2, latency_ms=None, parent_span_id=None, input="new"
    )
    target = builder(
        filters=[
            time_filter(START + timedelta(minutes=15), START + timedelta(minutes=30))
        ]
    )
    matches = execute(*target.build_filter_match_query_from_seed_rows([row()]))
    assert bool(matches) == (15 <= new_minute <= 30)
    seed = execute(
        *target.build_filter_seed_page(
            slice_start=START + timedelta(minutes=15),
            slice_end=START + timedelta(minutes=30),
            limit=5,
        )
    )
    assert bool(seed) == bool(matches)
    if matches:
        assert matches[0]["latency_ms"] is None
    content = execute(
        *target.build_content_query(
            ["span"],
            span_identities=[target.bounded_filter_row_identity(row())],
        )
    )
    assert content[0]["start_time"] == latest
    assert content[0]["input"] == "new"


@pytest.mark.integration
def test_engine_cross_hour_and_equal_version_winner_coherence(engine):
    execute, insert = engine
    insert(input="first", output="first", attrs_string={"tag": "first"})
    insert(input="second", output="second", attrs_string={"tag": "second"})
    insert(start_time=START + timedelta(hours=1, minutes=20), input="next-hour")
    target = builder(filters=[time_filter(end=START + timedelta(hours=2))])
    expected = execute(
        "SELECT input, output, attrs_string FROM spans FINAL WHERE toStartOfHour(start_time) = toDateTime('2026-08-08 12:00:00', 'UTC')"
    )
    actual = execute(
        *target.build_content_query(
            ["span"],
            span_identities=[target.bounded_filter_row_identity(row())],
        )
    )
    assert [
        {key: value[key] for key in ("input", "output", "attrs_string")}
        for value in actual
    ] == expected
    assert len(execute(*target.build_filter_match_query(["span"]))) == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "operation,attrs,expected",
    [
        ("not_equals", {}, False),
        ("not_equals", {"tag": 2}, True),
        ("is_null", {}, True),
        ("is_null", {"tag": 2}, False),
    ],
)
def test_engine_negative_and_absence_use_latest_typed_presence(
    engine, operation, attrs, expected
):
    execute, insert = engine
    insert()
    insert(_version=2, start_time=START + timedelta(minutes=10), attrs_number=attrs)
    target = builder(filters=[time_filter(), attr_filter(operation)])
    assert (
        bool(execute(*target.build_filter_match_query_from_seed_rows([row()])))
        is expected
    )


@pytest.mark.integration
def test_engine_anchor_and_org_candidates_keep_all_key_components(engine):
    execute, insert = engine
    insert()
    insert(service_name="service-b")
    insert(project_id=OTHER_PROJECT)
    target = builder()
    anchor = execute(*target.build_filter_anchor_probe(limit=10))
    assert {value["service_name"] for value in anchor} == {"service-a", "service-b"}
    assert len(execute(*target.build_filter_match_query_from_seed_rows(anchor))) == 2
    org = builder(project_id=None, project_ids=[PROJECT, OTHER_PROJECT])
    candidates = [row(), row(project_id=OTHER_PROJECT)]
    assert {
        value["project_id"]
        for value in execute(*org.build_filter_match_query_from_seed_rows(candidates))
    } == {PROJECT, OTHER_PROJECT}


@pytest.mark.integration
def test_engine_bounded_page_and_navigation_follow_corrected_order(engine):
    execute, insert = engine
    insert(id="middle")
    insert(id="older", start_time=START + timedelta(minutes=30))
    insert(id="older", start_time=START + timedelta(minutes=10), _version=2)
    insert(id="newer", start_time=START + timedelta(minutes=5))
    insert(id="newer", start_time=START + timedelta(minutes=40), _version=2)
    target = builder()

    class Analytics:
        def execute_ch_query(self, sql, params, **kwargs):
            values = execute(sql, params)
            return QueryResult(values, len(values), "clickhouse", 0)

    neighbors = read_bounded_filter_neighbors(
        builder=target,
        analytics=Analytics(),
        filters=target.filters,
        key_field="id",
        target_id="middle",
        deadline_ms=5000,
        scan_limit=200,
        page_size=1,
        max_query_count=128,
        require_unique_target=True,
    )
    assert neighbors.complete, neighbors.error_code
    assert neighbors.newer["id"] == "newer"
    assert neighbors.older["id"] == "older"
    first = read_bounded_filter_page(
        builder=target,
        analytics=Analytics(),
        filters=target.filters,
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5000,
        bounded_continuation=True,
        include_incomplete_rows=True,
    )
    assert first.complete, first.error_code
    assert [value["id"] for value in first.rows] == ["newer", "middle"]
    last = first.rows[-1]
    following = read_bounded_filter_page(
        builder=target,
        analytics=Analytics(),
        filters=target.filters,
        key_field="id",
        page_number=0,
        page_size=2,
        deadline_ms=5000,
        bounded_continuation=True,
        include_incomplete_rows=True,
        cursor_start_time=last["start_time"],
        cursor_order_token=target.bounded_filter_row_order_token(last),
    )
    assert following.complete, following.error_code
    assert [value["id"] for value in following.rows] == ["older"]


@pytest.mark.integration
def test_engine_ordinary_page_keeps_nulls_and_distinct_services(engine):
    execute, insert = engine
    insert(latency_ms=1)
    insert(latency_ms=None, start_time=START + timedelta(minutes=10), _version=2)
    insert(service_name="service-b", latency_ms=3)
    target = builder(filters=[time_filter()])
    page = execute(*target.build())
    assert len(page) == 2
    assert (
        next(value for value in page if value["service_name"] == "service-a")[
            "latency_ms"
        ]
        is None
    )
