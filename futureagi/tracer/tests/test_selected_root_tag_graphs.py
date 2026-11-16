"""Native tags must use the list's selected physical root, not any root.

These proofs execute real ClickHouse 25 SQL, including production UUID trace
keys and the six-part span replacement identity. No telemetry PostgreSQL rows
are involved; the SDK-only cases explicitly isolate an empty manual overlay.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.services.clickhouse import exact_graph_reads
from tracer.services.clickhouse.query_builders.agent_graph import AgentGraphQueryBuilder
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    GraphTagFilterTooBroadError,
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_exact_graph_filter_predicates,
)
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2 import effective_tags
from tracer.services.clickhouse.v2.entity_tag_targets import TRACE_ASSIGNMENT_HOUR
from tracer.services.clickhouse.v2.query_builders.eval_metrics import (
    EvalMetricsQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.tests.tag_read_fixtures import empty_tag_assignments  # noqa: F401
from tracer.tests.test_effective_tag_filters import leaf
from tracer.tests.test_entity_tag_assignment_postgres import assignment_schema  # noqa: F401
from tracer.tests.test_trace_classifier_rmt_identity_ch25 import _local_ch25_client

PROJECT = "11111111-1111-1111-1111-111111111111"
OTHER_PROJECT = "22222222-2222-2222-2222-222222222222"
TRACE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PV = "33333333-3333-3333-3333-333333333333"
OTHER_PV = "44444444-4444-4444-4444-444444444444"
START = datetime(2026, 8, 8, 12, 10, tzinfo=UTC)
END = START + timedelta(hours=2)
_REAL_ASSIGNMENT_MANAGER = EntityTagAssignment.objects


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture
def roots_store(monkeypatch):
    client = _local_ch25_client()
    database = "test_selected_roots_" + uuid4().hex
    client.execute(f"CREATE DATABASE {database}")
    client.execute(f"USE {database}")
    try:
        assert client.execute("SELECT version()")[0][0].startswith("25.")
        client.execute("""CREATE TABLE spans (
            project_id UUID, observation_type LowCardinality(String),
            service_name LowCardinality(String), start_time DateTime64(6, 'UTC'),
            trace_id String, id String, parent_span_id Nullable(String),
            name String, status Nullable(String), trace_name String MATERIALIZED '',
            end_time Nullable(DateTime64(6, 'UTC')), latency_ms Int64 DEFAULT 1,
            cost Float64 DEFAULT 0, total_tokens Int64 DEFAULT 1,
            prompt_tokens Int64 DEFAULT 0, completion_tokens Int64 DEFAULT 0,
            model String, provider String, project_version_id Nullable(UUID),
            end_user_id Nullable(UUID), trace_session_id Nullable(UUID),
            created_at DateTime64(6, 'UTC') DEFAULT start_time,
            attrs_string Map(String, String), attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8), attributes_extra String DEFAULT '{}',
            input String, output String, tags String DEFAULT '[]',
            is_deleted UInt8 DEFAULT 0, _version UInt64
        ) ENGINE=ReplacingMergeTree(_version, is_deleted)
          PARTITION BY toDate(start_time)
          ORDER BY (project_id, observation_type, service_name,
                    toStartOfHour(start_time), trace_id, id)""")
        client.execute("SYSTEM STOP MERGES spans")
        client.execute("""CREATE TABLE traces (
            project_id UUID, id UUID, tags String, _version UInt64, is_deleted UInt8
        ) ENGINE=ReplacingMergeTree(_version) ORDER BY (project_id, id)""")

        def execute(sql, params=None, **kwargs):
            rows, columns = client.execute(sql, params, with_column_types=True)
            return [
                dict(zip((c[0] for c in columns), row, strict=False)) for row in rows
            ]

        def query(sql, params=None, **kwargs):
            rows = execute(sql, params)
            return SimpleNamespace(
                data=rows, complete=True, row_count=len(rows), query_time_ms=0
            )

        def insert(**changes):
            row = {
                "project_id": PROJECT,
                "trace_id": TRACE,
                "id": "old",
                "name": TRACE,
                "observation_type": "span",
                "service_name": "svc",
                "parent_span_id": "",
                "start_time": START + timedelta(minutes=10),
                "_version": 1,
                "tags": '["wanted"]',
                "project_version_id": PV,
            }
            row.update(changes)
            client.execute(
                f"INSERT INTO spans ({', '.join(row)}) VALUES", [tuple(row.values())]
            )

        monkeypatch.setattr(effective_tags, "graph_overlay_executor", lambda: query)
        yield SimpleNamespace(
            execute=execute,
            query=query,
            insert=insert,
            render=lambda sql, params: client.substitute_params(
                sql, params, client.connection.context
            ),
        )
    finally:
        client.execute(f"DROP DATABASE IF EXISTS {database}")
        client.disconnect()


def window(*items):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START.isoformat(), END.isoformat()],
            },
        },
        *items,
    ]


def selected_by_list(store, filters, project_version_id=None):
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT, filters=filters, project_version_id=project_version_id
    )
    sql, params = builder.build_filter_match_query([TRACE])
    params = effective_tags.prepare_effective_tag_params(
        store.query, params, deadline=ReadDeadline.start(10000)
    )
    return bool(store.execute(sql, params))


@pytest.fixture
def real_tag_assignments(request, monkeypatch):
    request.getfixturevalue("empty_tag_assignments")
    request.getfixturevalue("assignment_schema")
    monkeypatch.setattr(EntityTagAssignment, "objects", _REAL_ASSIGNMENT_MANAGER)
    EntityTagAssignment.objects.all().delete()
    return EntityTagAssignment.objects


def selected_by_graph(store, filters, surface, project_version_id=None):
    scope = {"project_version_id": project_version_id} if project_version_id else {}
    if surface in {"timeseries", "append_only"}:
        sql, params = TimeSeriesQueryBuilder(
            project_id=PROJECT,
            filters=filters,
            observe_type="trace",
            exact_snapshot=True,
            resolve_span_versions=surface != "append_only",
            start_date=START,
            end_date=END,
            interval="hour",
            **scope,
        ).build()
        return sum(row["total_tokens"] for row in store.execute(sql, params)) > 0
    if surface == "agent":
        sql, params = AgentGraphQueryBuilder(
            project_id=PROJECT, filters=filters, **scope
        ).build()
        return bool(store.execute(sql, params))
    if surface == "eval":
        builder = EvalMetricsQueryBuilderV2(
            custom_eval_config_id=PV,
            project_id=PROJECT,
            filters=filters,
            observe_type="trace",
            start_date=START,
            end_date=END,
        )
        fragment = builder._filter_fragment()
        return bool(
            store.execute(
                "SELECT trace_id FROM (SELECT toUUID(%(root_trace)s) AS trace_id) "
                "AS raw_eval_logger WHERE 1 " + fragment,
                {**builder.params, "root_trace": TRACE},
            )
        )
    if surface == "annotation":
        predicate, params = exact_graph_reads._compile_membership_filter(
            project_id=PROJECT,
            filters=filters,
            observe_type="trace",
            annotation_label_ids=(),
            window_start=START,
            window_end=END,
        )
        return bool(
            exact_graph_reads._matching_trace_ids(
                analytics=SimpleNamespace(execute_ch_query=store.query),
                project_id=PROJECT,
                trace_ids=(TRACE,),
                start_date=START,
                end_date=END,
                predicate=predicate,
                predicate_params=params,
                timeout_ms=10000,
                settings={},
            )
        )
    clause, params = compile_exact_graph_filter_predicates(
        filters,
        project_id=PROJECT,
        observe_type="trace",
        window_start=START,
        window_end=END,
        **scope,
    )
    params.update(project_id=PROJECT, snapshot_start_date=START, snapshot_end_date=END)
    return bool(
        store.execute(
            "SELECT DISTINCT trace_id FROM spans FINAL WHERE project_id = toUUID(%(project_id)s) "
            "AND is_deleted = 0 AND start_time >= %(snapshot_start_date)s "
            "AND start_time < %(snapshot_end_date)s AND " + clause,
            params,
        )
    )


@pytest.mark.parametrize(
    "surface",
    ["timeseries", "append_only", "agent", "membership", "eval", "annotation"],
)
@pytest.mark.parametrize(
    "operation", ["contains", "not_contains", "is_not_null", "is_null"]
)
def test_native_tags_follow_selected_root_not_any_root(roots_store, surface, operation):
    roots_store.insert()
    roots_store.insert(id="new", start_time=START + timedelta(minutes=70), tags="[]")
    filters = window(leaf("tags", ["wanted"], operation))
    expected = operation in {"not_contains", "is_null"}
    assert selected_by_list(roots_store, filters) is expected
    assert selected_by_graph(roots_store, filters, surface) is expected


@pytest.mark.parametrize(
    "surface", ["timeseries", "append_only", "agent", "membership"]
)
@pytest.mark.parametrize(
    "scenario",
    [
        "deleted",
        "corrected_time",
        "excluded_time",
        "time_tie",
        "service_tie",
        "type_tie",
        "version",
        "moved_version",
        "foreign_project",
    ],
)
@pytest.mark.parametrize("operation", ["contains", "not_contains"])
def test_native_tags_root_eligibility_and_ties(
    roots_store, surface, scenario, operation
):
    roots_store.insert()
    new = {"id": "new", "start_time": START + timedelta(minutes=20), "tags": "[]"}
    extra = []
    project_version_id = None
    expected_positive = True
    if scenario == "deleted":
        roots_store.insert(**new)
        new.update(_version=2, is_deleted=1)
    elif scenario == "corrected_time":
        roots_store.insert(**new)
        new.update(_version=2, start_time=START - timedelta(minutes=1))
    elif scenario == "excluded_time":
        extra = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "not_between",
                    "filter_value": [
                        (START + timedelta(minutes=19)).isoformat(),
                        (START + timedelta(minutes=21)).isoformat(),
                    ],
                },
            }
        ]
    elif scenario == "time_tie":
        new.update(id="z-new", start_time=START + timedelta(minutes=10))
        expected_positive = False
    elif scenario == "service_tie":
        new.update(
            id="old", service_name="zzz", start_time=START + timedelta(minutes=10)
        )
        expected_positive = False
    elif scenario == "type_tie":
        new.update(
            id="old", observation_type="tool", start_time=START + timedelta(minutes=10)
        )
        expected_positive = False
    elif scenario in {"version", "moved_version"}:
        project_version_id = PV
        if scenario == "moved_version":
            roots_store.insert(**new)
            new["_version"] = 2
        new["project_version_id"] = OTHER_PV
    elif scenario == "foreign_project":
        new["project_id"] = OTHER_PROJECT
    roots_store.insert(**new)
    filters = window(*extra, leaf("tags", ["wanted"], operation))
    expected = expected_positive if operation == "contains" else not expected_positive
    assert selected_by_list(roots_store, filters, project_version_id) is expected
    assert (
        selected_by_graph(roots_store, filters, surface, project_version_id) is expected
    )


@pytest.mark.parametrize(
    "surface",
    ["timeseries", "append_only", "agent", "membership", "eval", "annotation"],
)
@pytest.mark.parametrize("operation", ["contains", "not_contains"])
def test_singular_tag_still_matches_any_span(roots_store, surface, operation):
    roots_store.insert()
    roots_store.insert(id="new", start_time=START + timedelta(minutes=20), tags="[]")
    filters = window(leaf("tag", ["wanted"], operation))
    expected = operation == "contains"
    assert selected_by_list(roots_store, filters) is expected
    assert selected_by_graph(roots_store, filters, surface) is expected


@pytest.mark.parametrize(
    "surface", ["timeseries", "agent", "membership", "eval", "annotation"]
)
@pytest.mark.parametrize("operation", ["contains", "not_contains"])
@pytest.mark.parametrize("source", ["curated", "manual", "saved_empty", "selected_sdk"])
def test_selected_root_combines_sdk_manual_and_curated_tags(
    roots_store, real_tag_assignments, surface, operation, source
):
    roots_store.insert()
    roots_store.insert(
        id="new",
        start_time=START + timedelta(minutes=20),
        tags='["wanted"]' if source == "selected_sdk" else "[]",
    )
    roots_store.execute(
        "INSERT INTO traces VALUES (%(project)s, %(trace)s, '[\"wanted\"]', 1, 0)",
        {"project": PROJECT, "trace": TRACE},
    )
    if source != "curated":
        real_tag_assignments.create(
            project_id=PROJECT,
            entity_kind="trace",
            trace_id=TRACE,
            span_id="",
            start_hour=TRACE_ASSIGNMENT_HOUR,
            observation_type="",
            service_name="",
            manual_tags=[{"name": "wanted"}] if source == "manual" else [],
        )
    positive = source != "saved_empty"
    expected = positive if operation == "contains" else not positive
    filters = window(leaf("tags", ["wanted"], operation))
    assert selected_by_list(roots_store, filters) is expected
    assert selected_by_graph(roots_store, filters, surface) is expected


@pytest.mark.parametrize(
    "surface", ["timeseries", "agent", "membership", "eval", "annotation"]
)
@pytest.mark.parametrize(
    "operation", ["contains", "not_contains", "is_null", "is_not_null"]
)
def test_no_eligible_root_does_not_match_negative_tags(roots_store, surface, operation):
    roots_store.insert(parent_span_id="missing-parent")
    filters = window(leaf("tags", ["wanted"], operation))
    assert not selected_by_list(roots_store, filters)
    assert not selected_by_graph(roots_store, filters, surface)


@pytest.mark.parametrize(
    "surface",
    ["timeseries", "append_only", "agent", "membership", "eval", "annotation"],
)
@pytest.mark.parametrize("replacement", ["cleared", "root_to_child", "after_window"])
def test_selected_root_uses_latest_physical_state(roots_store, surface, replacement):
    roots_store.insert(tags="[]")
    newer = {
        "id": "new",
        "start_time": END - timedelta(minutes=2),
        "tags": '["wanted"]',
    }
    roots_store.insert(**newer)
    if replacement == "cleared":
        newer["tags"] = "[]"
    elif replacement == "root_to_child":
        newer["parent_span_id"] = "old"
    else:
        newer["start_time"] = END + timedelta(minutes=1)
    roots_store.insert(**newer, _version=2)
    for operation in ("contains", "not_contains"):
        filters = window(leaf("tags", ["wanted"], operation))
        expected = operation == "not_contains"
        assert selected_by_list(roots_store, filters) is expected
        assert selected_by_graph(roots_store, filters, surface) is expected


def test_graph_budget_counts_shared_literals_once_and_distinct_names_across_leaves(
    roots_store, monkeypatch
):
    first, second = "a" * 1000, "b" * 1000
    _, one_leaf = effective_tags.graph_tag_overlay_params(
        "probe",
        project_ids=(PROJECT,),
        values=(first,),
        window_start=START,
        window_end=END,
        deadline=ReadDeadline.start(10000),
    )
    monkeypatch.setattr(
        effective_tags, "MAX_TAG_OVERLAY_BYTES", one_leaf + one_leaf // 2
    )
    shared_plan = compile_exact_graph_row_predicates(
        [leaf("tags", [first], "contains")] * 3,
        project_id=PROJECT,
        observe_type="trace",
        window_start=START,
        window_end=END,
    )
    assert len(shared_plan.predicates) == 3
    with pytest.raises(GraphTagFilterTooBroadError):
        compile_exact_graph_row_predicates(
            [leaf("tags", [value], "contains") for value in (first, second)],
            project_id=PROJECT,
            observe_type="trace",
            window_start=START,
            window_end=END,
        )


def test_graph_budget_counts_name_escaping_before_assignment_read(monkeypatch):
    # A valid raw name can cost twice its UTF-8 bytes on the wire. This
    # deterministic rejection must happen before querying PostgreSQL JSONB.
    monkeypatch.setattr(effective_tags, "MAX_TAG_OVERLAY_BYTES", 1500)

    def unexpected_read(*args, **kwargs):
        raise AssertionError("oversized tag names must be rejected before PG")

    monkeypatch.setattr(effective_tags, "read_tag_overlay", unexpected_read)
    with pytest.raises(GraphTagFilterTooBroadError):
        compile_exact_graph_row_predicates(
            [leaf("tags", ["'" * 1000], "contains")],
            project_id=PROJECT,
            observe_type="trace",
            window_start=START,
            window_end=END,
        )


@pytest.mark.parametrize(
    "surface", ["timeseries", "agent", "membership", "eval", "annotation"]
)
def test_native_tags_conjunction_uses_one_root_but_tag_can_use_a_sibling(
    roots_store, surface
):
    roots_store.insert()
    roots_store.insert(
        id="new", start_time=START + timedelta(minutes=20), tags='["other"]'
    )
    for old_column in ("tags", "tag"):
        filters = window(
            leaf(old_column, ["wanted"], "contains"),
            leaf("tags", ["other"], "contains"),
        )
        expected = old_column == "tag"
        assert selected_by_list(roots_store, filters) is expected
        assert selected_by_graph(roots_store, filters, surface) is expected


def test_selected_root_fold_keeps_existing_physical_scan_count(roots_store):
    import re

    def scans(sql):
        return len(re.findall(r"\bFROM\s+spans\b", sql, re.IGNORECASE))

    for builder_class in (TimeSeriesQueryBuilder, AgentGraphQueryBuilder):
        queries = []
        for column in ("tag", "tags"):
            kwargs = {
                "project_id": PROJECT,
                "filters": window(leaf(column, ["wanted"], "contains")),
            }
            if builder_class is TimeSeriesQueryBuilder:
                kwargs.update(
                    exact_snapshot=True,
                    observe_type="trace",
                    start_date=START,
                    end_date=END,
                )
            sql, _ = builder_class(**kwargs).build()
            queries.append(sql)
        assert scans(queries[0]) == scans(queries[1])
        assert "graph_root_order" not in queries[0]
        assert "graph_root_order" in queries[1]


@pytest.mark.parametrize(
    "builder_class", [TimeSeriesQueryBuilder, AgentGraphQueryBuilder]
)
def test_repeated_graph_names_fit_actual_native_parser_after_shared_binding(
    roots_store, builder_class
):
    names = [f"{index:02d}" + "x" * 4094 for index in range(16)]
    roots_store.insert()
    kwargs = {
        "project_id": PROJECT,
        "filters": window(
            leaf("tags", names, "contains"), leaf("tags", names, "contains")
        ),
    }
    if builder_class is TimeSeriesQueryBuilder:
        kwargs.update(
            exact_snapshot=True, observe_type="trace", start_date=START, end_date=END
        )
    sql, params = builder_class(**kwargs).build()
    assert (
        len(roots_store.render(sql, params).encode("utf-8"))
        > effective_tags.MAX_TAG_QUERY_BYTES
    )
    sql, params = effective_tags.bind_effective_tag_query(sql, params)
    assert (
        len(roots_store.render(sql, params).encode("utf-8"))
        <= effective_tags.MAX_TAG_QUERY_BYTES
    )
    assert roots_store.execute(sql, params) == []
