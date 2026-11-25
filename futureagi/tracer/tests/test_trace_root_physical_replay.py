"""Trace root replay contract, with UTC inline chdb fixtures (no server/DDL)."""

import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.server_readonly import without_query_settings
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 8, 31, 12, 30, 0, 123456, tzinfo=UTC)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


def make_builder(cls=TraceListQueryBuilderV2, *, days=7, **kwargs):
    scope = {} if "project_ids" in kwargs else {"project_id": PROJECT}
    return cls(
        **scope,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [NOW - timedelta(days=days), NOW],
                },
            }
        ],
        **kwargs,
    )


def root_row(**changes):
    return {
        "project_id": PROJECT,
        "trace_id": "trace",
        "root_span_id": "root",
        "start_time": NOW - timedelta(minutes=1),
        "_root_observation_type": "SPAN",
        "_root_service_name": "svc",
        "_root_start_hour": NOW.replace(minute=0, second=0, microsecond=0),
        "_root_version": 2,
        **changes,
    }


def complete_root_row(row, *, project_id=PROJECT):
    """Explicit V2 wire fixture for tests mocking the classifier/hydration."""
    result = {
        "project_id": project_id,
        "root_span_id": f"root-{row.get('trace_id', '')}",
        "start_time": NOW - timedelta(minutes=1),
        **row,
    }
    result.setdefault("_root_observation_type", result.get("observation_type", "SPAN"))
    result.setdefault("_root_service_name", "svc")
    utc_start = result["start_time"]
    utc_start = (
        utc_start.replace(tzinfo=UTC)
        if utc_start.tzinfo is None
        else utc_start.astimezone(UTC)
    )
    result.setdefault(
        "_root_start_hour",
        utc_start.replace(minute=0, second=0, microsecond=0),
    )
    result.setdefault("_root_version", 1)
    return result


def content_identity_row(identity):
    project, trace, span, start, kind, service, hour, version = identity
    return {
        "project_id": project,
        "trace_id": trace,
        "root_span_id": span,
        "start_time": datetime.fromtimestamp(start // 1_000_000, UTC).replace(
            microsecond=start % 1_000_000
        ),
        "_root_observation_type": kind,
        "_root_service_name": service,
        "_root_start_hour": datetime.fromtimestamp(hour // 1_000_000, UTC),
        "_root_version": version,
    }


def assert_coherent_classifier(sql):
    """Assert the new physical tuple AND its tombstone/value bindings."""
    assert "argMax(tuple(" in sql and "AS _physical_winner" in sql
    assert "is_deleted" in sql.split("AS _physical_winner", 1)[0]
    assert re.search(r"_physical_winner\.\d+ AS latest_is_deleted", sql)
    assert "WHERE latest_is_deleted = 0" in sql


def mock_content_rows(rows, params):
    """Attach exactly the requested physical identity to mocked content results."""
    identities = [
        content_identity_row(identity) for identity in params["content_root_identities"]
    ]
    result = []
    for row in rows:
        matches = [
            identity
            for identity in identities
            if identity["trace_id"] == row["trace_id"]
            and (
                not row.get("project_id") or identity["project_id"] == row["project_id"]
            )
        ]
        assert len(matches) == 1
        result.append({**matches[0], **row})
    return result


def test_v2_classifier_and_hydration_carry_full_root_key_and_version():
    builder = make_builder()
    classifier, _ = builder.build_filter_identity_match_query_from_seed_rows(
        [root_row()]
    )
    hydration, _ = builder.build_filter_page_hydration_query([root_row()])
    for sql in (classifier, hydration):
        for field in (
            "_root_observation_type",
            "_root_service_name",
            "_root_start_hour",
            "_root_version",
        ):
            assert field in sql
    assert "GROUP BY project_id, trace_id, id, start_time" not in hydration
    scan = hydration.split("PREWHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "toUnixTimestamp64Micro(start_time)" not in scan


def test_v2_drift_check_includes_service_and_version():
    builder = make_builder()
    expected = builder.bounded_filter_page_hydration_identity(root_row())
    for changes in ({"_root_service_name": "other"}, {"_root_version": 3}):
        assert (
            builder.bounded_filter_page_hydration_identity(root_row(**changes))
            != expected
        )


@pytest.fixture(scope="module")
def engine():
    return pytest.importorskip("chdb")


def physical_row(**changes):
    return {
        "project_id": PROJECT,
        "observation_type": "SPAN",
        "service_name": "svc",
        "trace_id": "trace",
        "id": "root",
        "start_time": NOW - timedelta(minutes=1),
        "_version": 2,
        "is_deleted": 0,
        "parent_span_id": "",
        "input": "new-input",
        "output": None,
        "project_version_id": None,
        "name": "new-name",
        **changes,
    }


def values_where(sql):
    """Lower PREWHERE and an ensuing same-depth WHERE without losing predicates."""
    depth, prewhere = 0, {}

    def token(match):
        nonlocal depth
        word = match.group()
        if word == "(":
            depth += 1
        elif word == ")":
            prewhere.pop(depth, None)
            depth -= 1
        elif word == "SELECT":
            prewhere[depth] = False
        elif word == "PREWHERE":
            prewhere[depth] = True
            return "WHERE"
        elif word == "WHERE" and prewhere.get(depth):
            return "AND"
        return word

    return re.sub(r"'(?:\\.|[^'\\])*'|\b(?:SELECT|PREWHERE|WHERE)\b|[()]", token, sql)


def execute(engine, builder, rows, sql_and_params, *, tag_rows=None, join_use_nulls=0):
    """Execute actual generated SQL on inline values, explicitly matching UTC storage.

    PREWHERE becomes WHERE because values() is not MergeTree. This is semantic
    execution evidence, not deployed ClickHouse25 planner/latency qualification.
    """
    context = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))
    columns = {
        "project_id": "UUID",
        "observation_type": "String",
        "service_name": "String",
        "trace_id": "String",
        "id": "String",
        "start_time": "DateTime64(6, 'UTC')",
        "_version": "UInt64",
        "is_deleted": "UInt8",
        "parent_span_id": "String",
        "input": "Nullable(String)",
        "output": "Nullable(String)",
        "project_version_id": "Nullable(UUID)",
        "name": "String",
    }
    tuples = [
        tuple(
            row[key].astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")
            if isinstance(row[key], datetime)
            else row[key]
            for key in columns
        )
        for row in rows
    ]
    params = escape_params(
        {
            "columns": ", ".join(f"{key} {typ}" for key, typ in columns.items()),
            "rows": tuple(tuples),
        },
        context,
    )
    sql, bindings = sql_and_params
    sql = values_where(without_query_settings(sql))
    sql = sql % escape_params(bindings, context)
    traces_relation = """
        SELECT toUUID('11111111-1111-4111-8111-111111111111') AS project_id,
            'trace' AS id, '[]' AS tags, toUInt64(1) AS _version, toUInt8(0) AS is_deleted
    """
    if tag_rows is not None:
        # Match 015_traces_and_trace_dict.sql: tags is non-nullable String.
        # join_use_nulls=1 below independently exercises missing LEFT JOIN NULLs.
        tag_bindings = escape_params({"rows": tuple(tag_rows)}, context)
        traces_relation = f"""
            SELECT * FROM values(
                'project_id UUID, id UUID, tags String, _version UInt64, is_deleted UInt8',
                {tag_bindings["rows"][1:-1]})
        """
    fixture = f"""WITH spans AS (
        SELECT *, 'trace-name' AS trace_name, 'ok' AS status,
            CAST(NULL AS Nullable(DateTime64(6, 'UTC'))) AS end_time,
            7 AS latency_ms, 0.25 AS cost, 8 AS total_tokens,
            3 AS prompt_tokens, 5 AS completion_tokens,
            'model' AS model, 'provider' AS provider,
            CAST(NULL, 'Nullable(UUID)') AS trace_session_id,
            map('company_id', 'company') AS attrs_string,
            map('duration', 2.0) AS attrs_number, map('flag', toUInt8(1)) AS attrs_bool,
            '{{}}' AS attributes_extra, map('source', 'fixture') AS metadata
        FROM values({params["columns"]}, {params["rows"][1:-1]})
    ), traces AS ({traces_relation})
    {sql} SETTINGS max_threads=1, max_memory_usage=536870912,
        join_use_nulls={int(join_use_nulls)}
    """
    result = [
        json.loads(line)
        for line in str(engine.query(fixture, "JSONEachRow")).splitlines()
        if line
    ]
    for row in result:
        for key in ("start_time", "_root_start_hour"):
            if isinstance(row.get(key), str):
                row[key] = datetime.fromisoformat(row[key]).replace(tzinfo=UTC)
        if "_root_version" in row:
            row["_root_version"] = int(row["_root_version"])
    return result


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("join_use_nulls", [0, 1])
@pytest.mark.parametrize(
    "tag_versions,expected",
    [
        ([], "[]"),
        ([(1, '["old"]', 0), (3, '["latest"]', 0)], '["latest"]'),
        ([(1, '["old"]', 0), (3, "", 0)], "[]"),
        ([(1, '["old"]', 0), (3, "[]", 0)], "[]"),
        ([(1, '["old"]', 0), (3, '["deleted"]', 1)], "[]"),
        ([(1, '["old"]', 1), (3, '["restored"]', 0)], '["restored"]'),
    ],
    ids=[
        "no-match",
        "latest",
        "latest-empty",
        "latest-cleared",
        "tombstone",
        "restored",
    ],
)
def test_actual_content_tags_preserve_latest_null_join_and_tenant_semantics(
    engine, days, join_use_nulls, tag_versions, expected
):
    other_project = "22222222-2222-4222-8222-222222222222"
    trace_id = "33333333-3333-4333-8333-333333333333"
    builder = make_builder(days=days, project_ids=[PROJECT, other_project])
    roots = [
        root_row(project_id=project, trace_id=trace_id)
        for project in (PROJECT, other_project)
    ]
    rows = [
        physical_row(project_id=project, trace_id=trace_id)
        for project in (PROJECT, other_project)
    ]
    # A colliding other-tenant row must neither leak into the missing join nor
    # disappear when this tenant's latest trace row is soft-deleted.
    tags = [(other_project, trace_id, '["other-tenant"]', 100, 0)] + [
        (PROJECT, trace_id, value, version, deleted)
        for version, value, deleted in reversed(tag_versions)
    ]
    content = execute(
        engine,
        builder,
        rows,
        builder.build_content_query(
            [trace_id], root_identities=builder.content_root_identities_for_rows(roots)
        ),
        tag_rows=tags,
        join_use_nulls=join_use_nulls,
    )
    assert builder.content_root_rows_match(roots, content)
    assert {row["project_id"]: row["trace_tags"] for row in content} == {
        PROJECT: expected,
        other_project: '["other-tenant"]',
    }
    assert all(row["output"] is None for row in content)


@pytest.mark.parametrize("join_use_nulls", [0, 1])
def test_actual_content_tags_do_not_resurrect_a_deleted_selected_root(
    engine, join_use_nulls
):
    trace_id = "33333333-3333-4333-8333-333333333333"
    builder = make_builder()
    root = root_row(trace_id=trace_id)
    result = execute(
        engine,
        builder,
        [
            physical_row(trace_id=trace_id),
            physical_row(trace_id=trace_id, _version=3, is_deleted=1),
        ],
        builder.build_content_query(
            [trace_id], root_identities=builder.content_root_identities_for_rows([root])
        ),
        tag_rows=[(PROJECT, trace_id, '["still-live-tags"]', 10, 0)],
        join_use_nulls=join_use_nulls,
    )
    assert result == []
    assert not builder.content_root_rows_match([root], result)


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("collision", ["service", "type", "hour"])
def test_actual_root_classifier_light_and_content_preserve_live_physical_identity(
    engine, days, collision
):
    builder = make_builder(days=days)
    live = physical_row()
    dead = physical_row(_version=9, is_deleted=1, input="deleted")
    if collision == "service":
        dead["service_name"] = "other"
    elif collision == "type":
        dead["observation_type"] = "GENERATION"
    else:
        dead["start_time"] -= timedelta(hours=1)
    rows = [live, dead]
    classified = execute(
        engine,
        builder,
        rows,
        builder.build_filter_identity_match_query_from_seed_rows([root_row()]),
    )
    assert len(classified) == 1
    light = execute(
        engine, builder, rows, builder.build_filter_page_hydration_query(classified)
    )
    assert len(light) == 1
    assert builder.content_root_rows_match(classified, light)
    content = execute(
        engine,
        builder,
        rows,
        builder.build_content_query(
            ["trace"], root_identities=builder.content_root_identities_for_rows(light)
        ),
    )
    assert builder.content_root_rows_match(light, content)
    assert content[0]["input"] == "new-input"
    assert content[0]["output"] is None


@pytest.mark.parametrize(
    "change", ["time", "deleted", "parent", "version", "project_version"]
)
def test_actual_replay_observes_new_same_hour_version_before_matching_identity(
    engine, change
):
    project_version = "22222222-2222-4222-8222-222222222222"
    builder = make_builder(
        project_version_id=project_version if change == "project_version" else None
    )
    old = physical_row(project_version_id=project_version)
    classified = execute(
        engine,
        builder,
        [old],
        builder.build_filter_identity_match_query_from_seed_rows([root_row()]),
    )
    latest = {**old, "_version": 3, "input": None, "output": "latest"}
    if change == "time":
        latest["start_time"] += timedelta(microseconds=1)
    elif change == "deleted":
        latest.update(is_deleted=1, start_time=old["start_time"] + timedelta(seconds=1))
    elif change == "parent":
        latest["parent_span_id"] = "parent"
    elif change == "project_version":
        latest["project_version_id"] = None
    rows = [old, latest]
    for content in (False, True):
        query = (
            builder.build_content_query(
                ["trace"],
                root_identities=builder.content_root_identities_for_rows(classified),
            )
            if content
            else builder.build_filter_page_hydration_query(classified)
        )
        replay = execute(engine, builder, rows, query)
        assert not builder.content_root_rows_match(classified, replay)
        if change in {"deleted", "parent", "project_version"}:
            assert replay == []
        else:
            assert replay[0]["_root_version"] == 3
            if content:
                assert replay[0]["input"] is None
                assert replay[0]["output"] == "latest"


@pytest.mark.parametrize(
    "missing",
    [
        "_root_observation_type",
        "_root_service_name",
        "_root_start_hour",
        "_root_version",
    ],
)
def test_v2_missing_identity_never_downgrades_to_four_part(missing):
    builder = make_builder()
    row = root_row()
    row.pop(missing)
    assert builder.bounded_filter_page_hydration_identity(row) is None
    with pytest.raises(ValueError):
        builder.build_filter_page_hydration_query([row])
    with pytest.raises(ValueError):
        builder.build_content_query(
            ["trace"], root_identities=[(PROJECT, "trace", "root", NOW)]
        )


@pytest.mark.parametrize("full", [False, True])
def test_actual_canonical_tie_is_deterministic_and_full_projection_coherent(
    engine, full
):
    builder = make_builder()
    rows = [
        physical_row(service_name="a", name="a", input="a"),
        physical_row(service_name="z", name="z", input="z"),
    ]
    sql = builder.build_filter_match_query(["trace"], candidate_identity_only=not full)
    classified = execute(engine, builder, rows, sql)
    assert classified[0]["_root_service_name"] == "z"
    light = execute(
        engine, builder, rows, builder.build_filter_page_hydration_query(classified)
    )
    assert light[0]["span_name"] == "z"
    if full:
        assert classified[0]["span_name"] == "z"
    assert builder.content_root_rows_match(classified, light)


def test_actual_org_duplicate_trace_ids_replay_only_their_own_roots(engine):
    other = "22222222-2222-4222-8222-222222222222"
    builder = make_builder(project_ids=[PROJECT, other])
    rows = [
        physical_row(),
        physical_row(project_id=other, input="other-project", _version=99),
    ]
    classified = execute(
        engine,
        builder,
        rows,
        builder.build_filter_match_query(
            ["trace"],
            candidate_identity_only=True,
            candidate_trace_identities=[(PROJECT, "trace"), (other, "trace")],
        ),
    )
    assert len(classified) == 2
    light = execute(
        engine, builder, rows, builder.build_filter_page_hydration_query(classified)
    )
    assert builder.content_root_rows_match(classified, light)
    content = execute(
        engine,
        builder,
        rows,
        builder.build_content_query(
            ["trace"], root_identities=builder.content_root_identities_for_rows(light)
        ),
    )
    assert builder.content_root_rows_match(light, content)
    assert {row["project_id"]: row["input"] for row in content} == {
        PROJECT: "new-input",
        other: "other-project",
    }


@pytest.mark.parametrize("cls", [TraceListQueryBuilder])
def test_legacy_keeps_four_part_contract(cls):
    builder = make_builder(cls)
    row = root_row()
    expected = (
        PROJECT,
        "trace",
        "root",
        int(row["start_time"].timestamp() * 1_000_000),
    )
    assert builder.bounded_filter_page_hydration_identity(row) == expected
    sql, _ = builder.build_filter_page_hydration_query([row])
    assert "GROUP BY project_id, trace_id, id, start_time" in sql
    assert "_root_service_name" not in sql


class InlineAnalytics:
    def __init__(self, engine, builder, rows, *, drift=False):
        self.engine, self.builder, self.rows, self.drift = engine, builder, rows, drift
        self.calls = []

    def execute_ch_query(self, query, params, **kwargs):
        from tracer.services.clickhouse.query_service import QueryResult

        self.calls.append((query, params))
        rows = self.rows
        if self.drift and params.get("page_hydration_physical_keys"):
            rows = [*rows, {**rows[0], "_version": rows[0]["_version"] + 1}]
        result = execute(self.engine, self.builder, rows, (query, params))
        return QueryResult(result, len(result), "clickhouse", 1.0)


@pytest.mark.parametrize("days", [7, 30, 365])
def test_actual_selector_cursor_pages_replay_full_key_without_skips(engine, days):
    from tracer.selectors.trace_filter_reads import read_bounded_filter_page

    builder = make_builder(days=days, page_size=2)
    rows = [
        physical_row(
            trace_id=f"trace-{index}", start_time=NOW - timedelta(seconds=index + 1)
        )
        for index in range(7)
    ]
    analytics = InlineAnalytics(engine, builder, rows)
    cursor, seen = {}, []
    for _ in range(5):
        page = read_bounded_filter_page(
            builder=builder,
            analytics=analytics,
            filters=builder.filters,
            key_field="trace_id",
            page_size=2,
            page_number=0,
            deadline_ms=5_000,
            bounded_continuation=True,
            include_incomplete_rows=True,
            **cursor,
        )
        assert page.complete, page.error_code
        seen.extend(row["trace_id"] for row in page.rows)
        if not page.has_more:
            break
        cursor = {
            "cursor_start_time": page.rows[-1]["start_time"],
            "cursor_order_token": page.rows[-1]["trace_id"],
        }
    assert seen == [f"trace-{index}" for index in range(7)]
    assert any(
        params.get("page_hydration_physical_keys") for _, params in analytics.calls
    )


def test_actual_selector_version_drift_rolls_back_unpublished_cursor(engine):
    from tracer.selectors.trace_filter_reads import read_bounded_filter_page

    builder = make_builder(page_size=2)
    rows = [
        physical_row(
            trace_id=f"trace-{index}", start_time=NOW - timedelta(seconds=index + 1)
        )
        for index in range(4)
    ]
    kwargs = {
        "builder": builder,
        "filters": builder.filters,
        "key_field": "trace_id",
        "page_size": 2,
        "page_number": 0,
        "deadline_ms": 5_000,
        "bounded_continuation": True,
        "include_incomplete_rows": True,
    }
    page = read_bounded_filter_page(
        analytics=InlineAnalytics(engine, builder, rows, drift=True), **kwargs
    )
    assert not page.complete and page.error_code == "classification_drift"
    assert not page.rows and not page.has_more
    assert page.continuation_before_id is None and page.continuation_slice_end is None
    retry = read_bounded_filter_page(
        analytics=InlineAnalytics(engine, builder, rows), **kwargs
    )
    assert retry.complete
    assert [row["trace_id"] for row in retry.rows] == ["trace-0", "trace-1"]


@pytest.mark.parametrize("drift", [None, "version", "service", "missing"])
def test_public_observe_content_drift_never_emits_cursor(engine, drift):
    from tracer.tests.test_traces_of_session_pagination import (
        TestTracesOfSessionPagination,
    )

    harness = TestTracesOfSessionPagination()
    view, request = harness._make_view(), harness._make_request(page_size=2)
    builder = make_builder()
    rows = [physical_row()]
    classified = execute(
        engine,
        builder,
        rows,
        builder.build_filter_match_query(["trace"], candidate_identity_only=True),
    )
    light = execute(
        engine, builder, rows, builder.build_filter_page_hydration_query(classified)
    )
    actual = execute(
        engine,
        builder,
        rows,
        builder.build_content_query(
            ["trace"], root_identities=builder.content_root_identities_for_rows(light)
        ),
    )
    if drift == "version":
        actual[0]["_root_version"] += 1
    elif drift == "service":
        actual[0]["_root_service_name"] = "different"
    elif drift == "missing":
        actual[0].pop("_root_version")
    analytics = mock.MagicMock()
    analytics.execute_ch_query.side_effect = lambda query, params, **kw: (
        SimpleNamespace(data=actual if params.get("content_physical_keys") else [])
    )
    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as configs,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=harness._bounded_page(light, total=2, has_more=True),
        ),
    ):
        configs.objects.filter.return_value.select_related.return_value = []
        result = view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT,
            validated_data={
                "filters": builder.filters,
                "page_number": 0,
                "page_size": 2,
                "cursor_mode": True,
            },
            analytics=analytics,
            org_project_ids=None,
            org=request.organization,
        )
    if drift:
        assert result[0] == "error" and result[1][0] == 503
        assert "next_cursor" not in repr(result)
    else:
        assert result[0] == "ok"
        assert result[1]["metadata"]["next_cursor"]


def test_public_old_cursor_contract_is_rejected_before_metadata_or_reads():
    from tracer.services.clickhouse.list_cursor import (
        ListCursorError,
        cursor_scope_for_request,
        encode_list_cursor,
    )
    from tracer.tests.test_traces_of_session_pagination import (
        TestTracesOfSessionPagination,
    )

    harness = TestTracesOfSessionPagination()
    view, request = harness._make_view(), harness._make_request(page_size=2)
    builder = make_builder()
    query = {
        "filters": builder.filters,
        "page_number": 0,
        "page_size": 2,
        "cursor_mode": True,
    }
    token = encode_list_cursor(
        resource="observe_traces",
        scope=cursor_scope_for_request(request, project_ids=[PROJECT]),
        query=query,
        page_size=2,
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
        order=(NOW - timedelta(minutes=1), "trace"),
        seen_rows=1,
    )
    analytics = mock.MagicMock()
    with (
        mock.patch("tracer.views.trace.CustomEvalConfig") as configs,
        pytest.raises(ListCursorError),
    ):
        view._list_traces_of_session_clickhouse(
            request,
            project_id=PROJECT,
            validated_data={**query, "cursor": token},
            analytics=analytics,
            org_project_ids=None,
            org=request.organization,
        )
    configs.objects.filter.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


@pytest.mark.parametrize("drift", [False, True])
def test_public_project_version_content_identity_check(engine, drift):
    from tracer.tests.test_traces_of_session_pagination import (
        TestTracesOfSessionPagination,
    )

    harness = TestTracesOfSessionPagination()
    view, request = harness._make_view(), harness._make_request(page_size=2)
    view.request = request
    version = "22222222-2222-4222-8222-222222222222"
    builder = make_builder(project_version_id=version)
    rows = [physical_row(project_version_id=version)]
    classified = execute(
        engine,
        builder,
        rows,
        builder.build_filter_match_query(["trace"], candidate_identity_only=True),
    )
    light = execute(
        engine, builder, rows, builder.build_filter_page_hydration_query(classified)
    )
    content = execute(
        engine,
        builder,
        rows,
        builder.build_content_query(
            ["trace"], root_identities=builder.content_root_identities_for_rows(light)
        ),
    )
    if drift:
        content[0]["_root_version"] += 1
    analytics = mock.MagicMock()
    analytics.execute_ch_query.side_effect = lambda query, params, **kw: (
        SimpleNamespace(data=content if params.get("content_physical_keys") else [])
    )
    with (
        mock.patch("tracer.views.trace.ProjectVersion") as versions,
        mock.patch("tracer.views.trace.CustomEvalConfig") as configs,
        mock.patch(
            "tracer.views.trace.get_annotation_labels_for_project", return_value=[]
        ),
        mock.patch(
            "tracer.views.trace._build_annotation_map_from_scores", return_value={}
        ),
        mock.patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=harness._bounded_page(light, total=1),
        ),
    ):
        versions.objects.get.return_value = SimpleNamespace(project_id=PROJECT)
        configs.objects.filter.return_value.select_related.return_value = []
        result = view._list_traces_clickhouse(
            request,
            project_version_id=version,
            query_params={
                "filters": builder.filters,
                "sort_params": [],
                "page_number": 0,
                "page_size": 2,
            },
            analytics=analytics,
        )
    if drift:
        assert result[0] == "error" and result[1][0] == 503
    else:
        assert result[0] == "ok"
