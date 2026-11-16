"""L2-1 regressions on native CH25, with production UUID trace identities."""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from clickhouse_driver import Client
from django.db import connection

from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2 import effective_tags as tags
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_service import EffectiveTagBindingMixin
from tracer.services.entity_tags import TagReadUnavailable

PROJECT = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
START = datetime(2026, 8, 8, 12, tzinfo=UTC)
END = START + timedelta(hours=1)
IDS = tuple(str(UUID(int=i + 1)) for i in range(512))


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def leaf(name="wanted", operation="equals"):
    return {
        "column_id": "tag",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": name,
        },
    }


def filters(*items):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START, END],
            },
        },
        *items,
    ]


@pytest.mark.parametrize(
    "candidates,leaves,projects", [(512, 2, 1), (200, 5, 1), (512, 2, 2)]
)
def test_preparation_budgets_shared_sets_not_occurrences(
    monkeypatch, candidates, leaves, projects
):
    monkeypatch.setattr(tags, "read_tag_overlay", Mock(return_value=tags.TagOverlay()))
    params = {
        "candidate_trace_ids": IDS[:candidates],
        "start_date": START,
        "end_date": END,
    }
    for project in (PROJECT, OTHER)[:projects]:
        for index in range(leaves):
            params[f"p{project[0]}_tag_{index}"] = tags.EffectiveTagRelation(
                (project,), ("wanted",)
            )
    bound = tags.prepare_effective_tag_params(
        Mock(), params, deadline=ReadDeadline.start(10000)
    )
    assert tags.shared_tag_parameter_bytes(bound) < tags.MAX_TAG_OVERLAY_BYTES


def test_graph_name_budget_escapes_and_deduplicates_before_assignment_reads(
    monkeypatch,
):
    read = Mock(return_value=tags.TagOverlay())
    monkeypatch.setattr(tags, "read_tag_overlay", read)
    names = ("'\\\n雪" * 100,)
    first, size = tags.graph_tag_overlay_params(
        "first",
        project_ids=(PROJECT,),
        values=names * 2,
        deadline=ReadDeadline.start(1000),
    )
    second, second_size = tags.graph_tag_overlay_params(
        "second",
        project_ids=(PROJECT,),
        values=names,
        deadline=ReadDeadline.start(1000),
    )
    assert first["first_names"] == list(names)
    assert size == second_size == tags.shared_tag_parameter_bytes({**first, **second})
    assert size > len(names[0].encode("utf-8"))  # quotes/backslashes really expand
    read.reset_mock()
    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        tags.graph_tag_overlay_params(
            "oversized",
            project_ids=(PROJECT,),
            values=("\\" * 70000,),
            deadline=ReadDeadline.start(1000),
        )
    read.assert_not_called()


def test_non_tag_queries_are_unchanged():
    params = {"ids": IDS}
    query = "SELECT %(ids)s, %(ids)s"
    assert tags.bind_effective_tag_query(query, params) == (query, params)


@pytest.fixture
def stores(django_db_blocker):
    if not os.environ.get("CH25_TCP_PORT"):
        pytest.skip("Requires the disposable loopback CH25/PostgreSQL test stack")
    assert os.environ.get("CH25_HOST") in {"localhost", "127.0.0.1"}
    assert connection.settings_dict["HOST"] in {"localhost", "127.0.0.1"}
    assert connection.settings_dict["NAME"].startswith("test_")
    client = Client(
        os.environ["CH25_HOST"],
        port=int(os.environ["CH25_TCP_PORT"]),
        user=os.environ.get("CH25_USER", "default"),
        password=os.environ.get("CH25_PASSWORD", ""),
    )
    database = "test_tag_budget_" + uuid4().hex
    schema = "tag_budget_" + uuid4().hex
    client.execute(f"CREATE DATABASE {database}")
    client.execute(f"USE {database}")
    client.execute("""CREATE TABLE spans (
        project_id UUID, observation_type LowCardinality(String),
        service_name LowCardinality(String), start_time DateTime64(6, 'UTC'),
        trace_id String, id String, parent_span_id Nullable(String),
        name String DEFAULT '', status Nullable(String),
        trace_name String MATERIALIZED '',
        end_time Nullable(DateTime64(6, 'UTC')), latency_ms Nullable(Int64),
        cost Float64 DEFAULT 0, total_tokens Int64 DEFAULT 0,
        prompt_tokens Int64 DEFAULT 0, completion_tokens Int64 DEFAULT 0,
        model String DEFAULT '', provider String DEFAULT '',
        project_version_id Nullable(UUID), end_user_id Nullable(UUID),
        trace_session_id Nullable(UUID),
        created_at DateTime64(6, 'UTC') DEFAULT start_time,
        attrs_string Map(String, String), attrs_number Map(String, Float64),
        attrs_bool Map(String, UInt8), attributes_extra String DEFAULT '{}',
        input String DEFAULT '', output String DEFAULT '', tags String DEFAULT '[]',
        is_deleted UInt8 DEFAULT 0, _version UInt64
    ) ENGINE = ReplacingMergeTree(_version, is_deleted)
      PARTITION BY toDate(start_time)
      ORDER BY (project_id, observation_type, service_name,
                toStartOfHour(start_time), trace_id, id)""")
    client.execute("""CREATE TABLE traces (project_id UUID, id UUID, tags String,
        _version UInt64, is_deleted UInt8) ENGINE=ReplacingMergeTree(_version)
        ORDER BY (project_id, id)""")
    rendered_sizes = []

    class Native:
        supports_per_query_read_settings = True
        read_timeout_ceiling_ms = 30000

        def execute_ch_query(self, sql, params=None, **kwargs):
            if params:
                rendered = client.substitute_params(
                    sql, params, client.connection.context
                )
                rendered_sizes.append(len(rendered.encode("utf-8")))
            data, columns = client.execute(
                sql, params, with_column_types=True, settings=kwargs.get("settings")
            )
            return SimpleNamespace(
                data=[
                    dict(zip((c[0] for c in columns), row, strict=True)) for row in data
                ],
                complete=True,
                query_time_ms=0,
                row_count=len(data),
            )

    class Analytics(EffectiveTagBindingMixin, Native):
        pass

    with django_db_blocker.unblock():
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE SCHEMA {schema}")
                cursor.execute(f"SET search_path TO {schema}")
                cursor.execute("CREATE TABLE tracer_project (id uuid PRIMARY KEY)")
                cursor.execute(
                    "INSERT INTO tracer_project VALUES (%s), (%s)", [PROJECT, OTHER]
                )
            with connection.schema_editor() as editor:
                editor.create_model(EntityTagAssignment)
            yield SimpleNamespace(
                client=client, analytics=Analytics(), sizes=rendered_sizes
            )
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO public")
                cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            client.execute(f"DROP DATABASE {database}")
            client.disconnect()


def seed(stores, *, projects=(PROJECT,), count=512):
    stores.client.execute(
        """INSERT INTO spans
        (project_id, trace_id, id, observation_type, service_name, start_time,
         parent_span_id, status, tags, _version) VALUES""",
        [
            (
                UUID(project),
                trace,
                "root",
                "span",
                "svc",
                START + timedelta(seconds=index),
                "",
                "ERROR",
                '["wanted"]' if index % 2 == 0 else "[]",
                1,
            )
            for project in projects
            for index, trace in enumerate(IDS[:count])
        ],
    )
    # Exercise UUID/String membership in the curated path, not an empty table.
    stores.client.execute(
        "INSERT INTO traces VALUES",
        [
            (UUID(project), UUID(trace), '["wanted"]', 1, 0)
            for project in projects
            for trace in IDS[:count:2]
        ],
    )


@pytest.mark.parametrize("projects", [(PROJECT,), (PROJECT, OTHER)])
def test_native_trace_classifier_two_leaves_512(stores, projects):
    count = 512 // len(projects)  # the org limit counts (project, trace) pairs
    seed(stores, projects=projects, count=count)
    active = filters(leaf(), leaf("excluded", "not_equals"))
    builder = TraceListQueryBuilderV2(project_ids=list(projects), filters=active)
    sql, params = builder.build_filter_match_query(list(IDS[:count]))
    rows = stores.analytics.execute_ch_query(sql, params).data
    assert {(str(row["project_id"]), row["trace_id"]) for row in rows} == {
        (project, trace) for project in projects for trace in IDS[:count:2]
    }
    assert max(stores.sizes) < tags.MAX_TAG_QUERY_BYTES


def test_native_small_classifier_without_shared_bindings(stores):
    seed(stores, count=2)
    builder = TraceListQueryBuilderV2(project_id=PROJECT, filters=filters(leaf()))
    sql, params = builder.build_filter_match_query(list(IDS[:2]))
    params = tags.prepare_effective_tag_params(
        stores.analytics.execute_ch_query, params, deadline=ReadDeadline.start(10000)
    )
    assert stores.client.execute(sql, params)


def test_native_span_predicate_five_leaves_200(stores):
    seed(stores, count=200)
    compiler = ClickHouseFilterBuilderV2(project_id=PROJECT, query_mode="span")
    sql, params = compiler.translate(
        [leaf(), *(leaf(f"missing{i}", "not_equals") for i in range(4))]
    )
    params.update(candidate_span_trace_ids=IDS[:200], start_date=START, end_date=END)
    rows = stores.analytics.execute_ch_query(
        "SELECT trace_id FROM spans WHERE " + sql, params
    ).data
    assert {row["trace_id"] for row in rows} == set(IDS[:200:2])
    assert max(stores.sizes) < tags.MAX_TAG_QUERY_BYTES


def test_native_classifier_at_assignment_count_cap_and_one_over(stores):
    seed(stores)
    assignments = []
    for index, trace in enumerate(IDS):
        assignments.append(
            EntityTagAssignment(
                project_id=PROJECT,
                entity_kind="trace",
                trace_id=trace,
                span_id="",
                start_hour=datetime(1970, 1, 1, tzinfo=UTC),
                observation_type="",
                service_name="",
                manual_tags=[{"name": "wanted" if index % 2 == 0 else "other"}],
            )
        )
        for child in range(3):
            assignments.append(
                EntityTagAssignment(
                    project_id=PROJECT,
                    entity_kind="span",
                    trace_id=trace,
                    span_id=f"child{child}",
                    start_hour=START,
                    observation_type="span",
                    service_name="svc",
                    manual_tags=[{"name": "other"}],
                )
            )
    assert len(assignments) == tags.MAX_TAG_OVERLAY_KEYS
    EntityTagAssignment.objects.bulk_create(assignments)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=filters(leaf(), leaf("excluded", "not_equals"))
    )
    sql, params = builder.build_filter_match_query(list(IDS))
    rows = stores.analytics.execute_ch_query(sql, params).data
    assert {row["trace_id"] for row in rows} == set(IDS[::2])
    assert max(stores.sizes) < tags.MAX_TAG_QUERY_BYTES
    EntityTagAssignment.objects.create(
        project_id=PROJECT,
        entity_kind="span",
        trace_id=IDS[0],
        span_id="one-over",
        start_hour=START,
        observation_type="span",
        service_name="svc",
        manual_tags=[],
    )
    executed = len(stores.sizes)
    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        stores.analytics.execute_ch_query(sql, params)
    assert (
        len(stores.sizes) == executed
    )  # no partial query/result at the count boundary


@pytest.mark.parametrize(
    "cursor_mode,page_size,page_number",
    [(False, 100, 1), (False, 512, 0), (True, 100, 0)],
)
def test_native_numbered_csv_and_cursor_page_memberships(
    stores, cursor_mode, page_size, page_number
):
    seed(stores)
    active = filters(
        leaf(),
        leaf("excluded", "not_equals"),
        {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "ERROR",
            },
        },
    )
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=active, cursor_mode=cursor_mode
    )
    page = read_bounded_filter_page(
        builder=builder,
        analytics=stores.analytics,
        filters=active,
        key_field="trace_id",
        page_number=page_number,
        page_size=page_size,
        deadline_ms=30000,
        classify_batch_size=512,
        max_candidates=512,
    )
    assert page.complete, page
    expected = list(reversed(IDS[::2]))[
        page_number * page_size : (page_number + 1) * page_size
    ]
    assert [row["trace_id"] for row in page.rows] == expected
    assert max(stores.sizes) < tags.MAX_TAG_QUERY_BYTES


def test_native_shared_binding_nested_with_union_and_escaped_names(stores):
    names = ["a'\\\n\t\x00雪", "plain"]
    params = {"tag_assigned_keys": (("", ""),), "a": names, "b": names.copy()}
    sql = """WITH 1 AS existing SELECT * FROM
        (WITH 2 AS nested SELECT hasAny(%(a)s, %(b)s) AS matched, existing + nested AS n)
        UNION ALL SELECT hasAny(%(b)s, %(a)s), 4"""
    bound, values = tags.bind_effective_tag_query(sql, params)
    assert names[0] not in bound
    assert tags.bind_effective_tag_query(bound, values) == (bound, values)
    assert stores.client.execute(bound, values) == [(1, 3), (1, 4)]
    expected = sum(
        len(tags._collection_literal(v).encode("utf-8"))
        for v in (names, params["tag_assigned_keys"])
    )
    assert tags.shared_tag_parameter_bytes(params) == expected


def test_real_rendered_statement_cap_includes_sql_and_escaping(stores):
    params = {"tag_assigned_keys": (("", ""),), "names": ["\\'雪" * 10000]}
    query = "SELECT hasAny(%(names)s, %(names)s)"
    sql, values = tags.bind_effective_tag_query(query, params)
    assert stores.client.execute(sql, values) == [(1,)]
    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        tags.bind_effective_tag_query(query + " " * tags.MAX_TAG_QUERY_BYTES, params)
    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        tags.bind_effective_tag_query(query, {**params, "names": ["\\'雪" * 30000]})
    with pytest.raises(TagReadUnavailable, match="tag_overlay_budget_exceeded"):
        tags.bind_effective_tag_query(query, params, max_query_size=1000)
