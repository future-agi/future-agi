"""CH-only HTTP writes/readback with real CH25 queries and primary PG overlay.

Run only on a disposable cloud worker. Project/config authorization is isolated
from these storage proofs; no legacy PostgreSQL telemetry tables are installed.
"""

import json
from datetime import UTC, datetime, timedelta
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from tracer.services import entity_tag_assignments as writes
from tracer.services.clickhouse.v2 import effective_tags
from tracer.services.entity_tag_reads import enrich_entity_tags
from tracer.services.entity_tags import TagReadUnavailable
from tracer.tests.test_entity_tag_assignment_postgres import (
    assignment_schema,  # noqa: F401
)
from tracer.tests.test_entity_tag_target import PROJECT, START, TRACE
from tracer.tests.test_entity_tag_writes import invoke, make_request, payload
from tracer.tests.test_span_physical_identity_latest import (
    engine as physical_engine,  # noqa: F401
)
from tracer.views import observation_span as span_views
from tracer.views import trace as trace_views

PV = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture
def stores(request, monkeypatch):
    request.getfixturevalue("assignment_schema")
    execute, insert = request.getfixturevalue("physical_engine")
    execute("""ALTER TABLE spans
        ADD COLUMN input_length UInt64 MATERIALIZED length(input),
        ADD COLUMN output_length UInt64 MATERIALIZED length(output),
        ADD COLUMN span_events String DEFAULT '[]',
        ADD COLUMN status_message String DEFAULT '',
        ADD COLUMN custom_eval_config_id Nullable(UUID),
        ADD COLUMN trace_session_id Nullable(UUID),
        ADD COLUMN resource_attrs Map(String, String),
        ADD COLUMN metadata Map(String, String)""")
    execute("""CREATE TABLE traces (project_id UUID, id String, tags String,
        _version UInt64, is_deleted UInt8) ENGINE=ReplacingMergeTree(_version)
        ORDER BY (project_id, id)""")
    execute("""CREATE TABLE model_hub_score (id UUID, tracer_project_id UUID,
        trace_id String, observation_span_id String, label_id UUID, value String,
        _peerdb_version UInt64, _peerdb_is_deleted UInt8, deleted UInt8)
        ENGINE=MergeTree ORDER BY (tracer_project_id, trace_id, observation_span_id, id)""")
    execute("""CREATE TABLE end_user_id_remap (new_id UUID, old_id UUID)
        ENGINE=ReplacingMergeTree ORDER BY (new_id, old_id)""")
    lock = RLock()

    class Analytics:
        supports_per_query_read_settings = True

        def execute_ch_query(self, sql, params=None, **kwargs):
            with lock:
                rows = execute(sql, params)
            for row in rows:
                for field in ("start_time", "start_hour", "_root_start_hour"):
                    if isinstance(row.get(field), str):
                        row[field] = datetime.fromisoformat(row[field])
                    if isinstance(row.get(field), datetime):
                        row[field] = row[field].replace(tzinfo=UTC)
            return SimpleNamespace(
                data=rows, complete=True, row_count=len(rows), query_time_ms=0
            )

    analytics = Analytics()
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM tracer_entity_tag_assignment")
    monkeypatch.setattr(writes, "V2AnalyticsQueryService", lambda: analytics)
    monkeypatch.setattr(
        writes,
        "_authorize_project",
        lambda request, project_id: SimpleNamespace(id=project_id),
    )
    monkeypatch.setattr(trace_views, "V2AnalyticsQueryService", lambda: analytics)
    monkeypatch.setattr(span_views, "V2AnalyticsQueryService", lambda: analytics)
    # Graph compilers read curated-tag suppressions from the same CH25 store.
    monkeypatch.setattr(
        effective_tags,
        "graph_overlay_executor",
        lambda: analytics.execute_ch_query,
        raising=False,
    )
    projects = MagicMock()
    projects.filter.return_value.filter.return_value = projects.filter.return_value
    projects.filter.return_value.values_list.return_value.__getitem__.return_value = [
        PROJECT
    ]
    projects.filter.return_value.exists.return_value = True
    monkeypatch.setattr(trace_views.Project, "no_workspace_objects", projects)
    versions = MagicMock()
    versions.objects.get.return_value = SimpleNamespace(project_id=PROJECT)
    monkeypatch.setattr(trace_views, "ProjectVersion", versions)
    configs = MagicMock()
    configs.objects.filter.return_value.select_related.return_value = []
    configs.no_workspace_objects.filter.return_value.select_related.return_value.__getitem__.return_value = []
    # The detail handler imports this model directly, so patch its managers too.
    from tracer.models.custom_eval_config import CustomEvalConfig

    monkeypatch.setattr(CustomEvalConfig, "objects", configs.objects)
    monkeypatch.setattr(
        CustomEvalConfig, "no_workspace_objects", configs.no_workspace_objects
    )
    for module in (trace_views, span_views):
        monkeypatch.setattr(
            module, "get_annotation_labels_for_project", lambda *a, **k: []
        )
    # Existing non-tag metadata fallback, explicitly no PG telemetry requirement.
    monkeypatch.setattr(
        trace_views.Trace.objects,
        "filter",
        lambda **kw: SimpleNamespace(first=lambda: None),
    )

    def seed(**changes):
        insert(
            **{
                "project_id": PROJECT,
                "trace_id": TRACE,
                "id": "0123456789abcdef",
                "observation_type": "span",
                "service_name": "svc",
                "start_time": START,
                "parent_span_id": "",
                "_version": 2,
                "project_version_id": PV,
                "tags": '["sdk"]',
                "attrs_string": {"tag.tags": '["sdk-attribute"]'},
                **changes,
            }
        )

    return SimpleNamespace(
        analytics=analytics, seed=seed, execute=execute, insert=insert
    )


def get_request(query=None):
    request = Request(APIRequestFactory().get("/", query or {"project_id": PROJECT}))
    request.organization = SimpleNamespace(id="org")
    request.workspace = SimpleNamespace(
        id="ws", organization_id="org", is_default=False
    )
    request._user = SimpleNamespace(organization=request.organization)
    request.validated_query_data = {"project_id": PROJECT}
    return request


def trace_detail():
    request = get_request()
    view = trace_views.TraceView()
    view.request = request
    response = view.retrieve(request, pk=TRACE)
    assert response.status_code == 200, response.data
    return response.data["result"]


def span_detail(span_id="0123456789abcdef"):
    request = get_request(
        {
            key: value
            for key, value in payload().items()
            if key not in {"tags", "expected_tag_revision", "span_id"}
        }
    )
    view = span_views.ObservationSpanView()
    view.request = request
    response = view.retrieve(request, pk=span_id)
    assert response.status_code == 200, response.data
    return response.data["result"]["observation_span"]


def window():
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    (START - timedelta(minutes=30)).isoformat(),
                    (START + timedelta(minutes=30)).isoformat(),
                ],
            },
        }
    ]


def trace_list(stores):
    request = get_request()
    view = trace_views.TraceView()
    view.request = request
    response = view._list_traces_clickhouse(
        request,
        PV,
        stores.analytics,
        {
            "filters": window(),
            "sort_params": [],
            "page_number": 0,
            "page_size": 30,
        },
    )
    assert response.status_code == 200, response.data
    return response.data["result"]["table"]


def span_list(stores):
    request = get_request()
    view = span_views.ObservationSpanView()
    view.request = request
    response = view._list_spans_non_observe_clickhouse(
        request,
        PV,
        SimpleNamespace(project_id=PROJECT),
        stores.analytics,
        {"filters": window(), "page_number": 0, "page_size": 30},
    )
    assert response.status_code == 200, response.data
    return response.data["result"]["table"]


def observe_lists(stores):
    request = get_request()
    query = {"filters": window(), "page_number": 0, "page_size": 30}
    trace_view = trace_views.TraceView()
    trace_view.request = request
    trace_response = trace_view._list_traces_of_session_clickhouse(
        request,
        PROJECT,
        query,
        stores.analytics,
    )
    assert trace_response.status_code == 200, trace_response.data
    span_view = span_views.ObservationSpanView()
    span_view.request = request
    span_response = span_view._list_spans_clickhouse(
        request,
        PROJECT,
        query,
        stores.analytics,
    )
    assert span_response.status_code == 200, span_response.data
    return [
        *trace_response.data["result"]["table"],
        *span_response.data["result"]["table"],
    ]


def test_http_save_detail_drawer_and_lists_then_clear_preserves_sdk(stores):
    stores.seed()
    response = invoke(make_request())
    assert response.status_code == 200, response.data
    for revision, expected in (
        (
            1,
            [
                {"name": "sdk"},
                {"name": "sdk-attribute"},
                {"name": "ui", "color": "#123456"},
            ],
        ),
        (2, [{"name": "sdk"}, {"name": "sdk-attribute"}]),
    ):
        detail = trace_detail()
        displayed = [
            detail["trace"],
            detail["observation_spans"][0]["observation_span"],
            span_detail(),
            *trace_list(stores),
            *span_list(stores),
            *observe_lists(stores),
        ]
        assert len(displayed) == 7
        for row in displayed:
            assert row["tags"] == expected
            assert row["source_tag_names"] == ["sdk", "sdk-attribute"]
            assert row["tag_revision"] == revision
            assert row["tag_target"]["expected_version"] == "2"
            assert row["tag_target"]["span_id"] == "0123456789abcdef"
        if revision == 1:
            cleared = invoke(
                make_request(body=payload(tags=[], expected_tag_revision=1))
            )
            assert cleared.status_code == 200, cleared.data
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regclass('tracer_trace'), to_regclass('tracer_observation_span')"
        )
        assert cursor.fetchone() == (None, None)


def test_large_non_tag_attributes_keep_every_read_surface_available(stores):
    # Collector overflow keeps input.value/output.value beside tag.tags. A
    # long-context span far above the 1 MiB tag budget must stay readable.
    large = "x" * (2 * 1024 * 1024)
    stores.seed(
        attrs_string={"tag.tags": '["sdk-attribute"]', "output.value": large},
        attributes_extra=json.dumps({"input.value": large}),
    )
    detail = trace_detail()
    displayed = [
        detail["trace"],
        detail["observation_spans"][0]["observation_span"],
        span_detail(),
        *trace_list(stores),
        *span_list(stores),
        *observe_lists(stores),
    ]
    assert len(displayed) == 7
    for row in displayed:
        assert row["source_tag_names"] == ["sdk", "sdk-attribute"]
        assert row["tag_target"]["span_id"] == "0123456789abcdef"


def test_legacy_trace_tags_reach_every_root_reader_and_survive_a_save(stores):
    # A tag saved by the previous editor lives only in the curated traces row.
    stores.seed()
    stores.execute(
        "INSERT INTO traces VALUES (%(project)s, %(trace)s, '[\"reviewed\"]', 1, 0)",
        {"project": PROJECT, "trace": TRACE},
    )
    expected = [{"name": "sdk"}, {"name": "sdk-attribute"}, {"name": "reviewed"}]
    detail = trace_detail()
    displayed = [
        detail["trace"],
        detail["observation_spans"][0]["observation_span"],
        span_detail(),
        *trace_list(stores),
        *span_list(stores),
        *observe_lists(stores),
    ]
    assert len(displayed) == 7
    for row in displayed:
        assert row["tags"] == expected
        assert row["source_tag_names"] == ["sdk", "sdk-attribute"]
        assert row["tag_revision"] == 0
    # Saving from any of those surfaces sends what it displays, plus the edit.
    saved = invoke(
        make_request(
            body=payload(
                tags=[{"name": "reviewed"}, {"name": "x", "color": "#123456"}],
                expected_tag_revision=0,
            )
        )
    )
    assert saved.status_code == 200, saved.data
    for row in (span_detail(), *span_list(stores), *trace_list(stores)):
        assert [tag["name"] for tag in row["tags"]] == [
            "sdk",
            "sdk-attribute",
            "reviewed",
            "x",
        ]


def test_child_assignment_does_not_inherit_trace_assignment(stores):
    stores.seed()
    stores.insert(
        project_id=PROJECT,
        trace_id=TRACE,
        id="child",
        service_name="svc",
        observation_type="span",
        start_time=START,
        parent_span_id="0123456789abcdef",
        _version=2,
        project_version_id=PV,
        tags='["child-sdk"]',
    )
    assert invoke(make_request()).status_code == 200
    response = invoke(
        make_request(span=True, body=payload(span_id="child", tags=["child-ui"])),
        span=True,
    )
    assert response.status_code == 200, response.data
    detail = trace_detail()
    child = detail["observation_spans"][0]["children"][0]["observation_span"]
    assert child["tags"] == [{"name": "child-sdk"}, {"name": "child-ui"}]
    assert {tag["name"] for tag in detail["trace"]["tags"]} == {
        "sdk",
        "sdk-attribute",
        "ui",
    }
    listed = {row["span_id"]: row for row in span_list(stores)}
    assert listed["child"]["tags"] == child["tags"]
    assert span_detail("child")["tags"] == child["tags"]


def test_overlay_reads_are_batched_exact_and_primary(stores):
    stores.seed()
    assert invoke(make_request()).status_code == 200
    rows = [
        {"project_id": PROJECT, "trace_id": f"trace-{index}", "tags": "[]"}
        for index in range(401)
    ]
    rows[0]["trace_id"] = TRACE
    with CaptureQueriesContext(connection) as queries:
        enrich_entity_tags(rows, kind="trace")
    reads = [
        query["sql"]
        for query in queries
        if 'FROM "tracer_entity_tag_assignment"' in query["sql"]
    ]
    assert len(reads) == 3
    assert all(
        "LIMIT " in sql and '"project_id"' in sql and '"trace_id"' in sql
        for sql in reads
    )
    assert rows[0]["tags"] == [{"name": "ui", "color": "#123456"}]
    assert all(row["tags"] == [] for row in rows[1:])


def test_candidate_limit_fails_before_pg_and_leaves_rows_unchanged(stores):
    rows = [{"project_id": PROJECT, "trace_id": TRACE}] * 4097
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(TagReadUnavailable, match="candidate_budget"):
            enrich_entity_tags(rows, kind="trace")
    assert len(queries) == 0
    assert "tags" not in rows[0]


@pytest.mark.parametrize("span", [False, True])
def test_pg_read_failure_is_public_503_not_missing_tags(stores, monkeypatch, span):
    from django.db import OperationalError

    from tracer.services import entity_tag_reads

    stores.seed()
    monkeypatch.setattr(
        entity_tag_reads.EntityTagAssignment.objects,
        "using",
        MagicMock(side_effect=OperationalError("private database diagnostic")),
    )
    request = get_request()
    view = span_views.ObservationSpanView() if span else trace_views.TraceView()
    view.request = request
    response = view.retrieve(request, pk="0123456789abcdef" if span else TRACE)
    assert response.status_code == 503, response.data
    assert response.data["code"] == "service_unavailable"
    assert "private database diagnostic" not in str(response.data)
