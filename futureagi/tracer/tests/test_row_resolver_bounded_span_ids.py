"""Unit guards for bounded eval-task span identity resolution."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.models.eval_task import RowType, RunType
from tracer.selectors.eval_tasks import row_resolver

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def row_resolver_ch():
    """Isolated loopback-only CH table for fallback latest-state semantics."""
    if os.environ.get("FUTUREAGI_TEST_ALLOW_LOCAL_CH_DDL") != "1":
        pytest.skip("local ClickHouse DDL integration test requires explicit opt-in")
    clickhouse_connect = pytest.importorskip("clickhouse_connect")
    host = os.environ.get("CH25_HOST") or os.environ.get("CH_HOST") or "localhost"
    if host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("local ClickHouse DDL test refuses a non-loopback host")
    port = int(
        os.environ.get("CH25_HTTP_PORT") or os.environ.get("CH_HTTP_PORT") or 18124
    )
    database = f"test_eval_selector_{uuid.uuid4().hex[:8]}"
    try:
        admin = clickhouse_connect.get_client(
            host=host,
            port=port,
            username=os.environ.get("CH_USER", "default"),
            password=os.environ.get("CH_PASSWORD", ""),
        )
        admin.command("SELECT 1")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"ClickHouse not available: {exc!r}")

    admin.command(f"CREATE DATABASE {database}")
    client = clickhouse_connect.get_client(
        host=host,
        port=port,
        username=os.environ.get("CH_USER", "default"),
        password=os.environ.get("CH_PASSWORD", ""),
        database=database,
    )
    client.command(
        """
        CREATE TABLE spans (
            project_id UUID,
            trace_id String,
            id String,
            start_time DateTime64(6, 'UTC'),
            trace_session_id Nullable(UUID),
            observation_type LowCardinality(String) DEFAULT '',
            status LowCardinality(String) DEFAULT '',
            attrs_string Map(String, String),
            attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8),
            is_deleted UInt8 DEFAULT 0,
            _version UInt64
        ) ENGINE = ReplacingMergeTree(_version)
        ORDER BY (project_id, start_time, trace_id, id)
        """
    )
    # Keep every physical version present while exercising the scalar argMax
    # plans; background merges must not make a stale-version test pass by luck.
    client.command("SYSTEM STOP MERGES spans")
    try:
        yield client
    finally:
        client.command("SYSTEM START MERGES spans")
        client.close()
        admin.command(f"DROP DATABASE IF EXISTS {database}")
        admin.close()


def test_unix_nanoseconds_preserves_microsecond_cursor_exactly():
    value = datetime(2026, 7, 30, 12, 5, 6, 123456, tzinfo=UTC)
    expected = 1_785_413_106_123_456_000

    assert row_resolver._unix_nanoseconds(value) == expected
    assert row_resolver._unix_nanoseconds(value.replace(tzinfo=None)) == expected


def test_historical_span_query_samples_before_bounded_top_k():
    sql, params = row_resolver._build_sample_query(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        salt="task-1",
        sampling_rate=50,
        filters={"observation_type": ["llm"]},
        limit=100,
    )
    compact_sql = " ".join(sql.split())

    assert "LIMIT 1 BY id" not in compact_sql
    assert "FROM spans" in compact_sql
    assert "GROUP BY id" in compact_sql
    assert (
        compact_sql.count(
            "ORDER BY toStartOfMinute(latest_start_time) DESC, grouped_id ASC"
        )
        == 1
    )
    assert compact_sql.count("LIMIT %(latest_span_limit)s") == 1
    assert (
        "modulo(cityHash64(%(latest_span_sampling_salt)s, "
        "toString(grouped_id)), 100) < %(latest_span_sampling_rate)s"
    ) in compact_sql
    assert "argMax(observation_type" in compact_sql
    assert "lowerUTF8(toString(latest_span_column_value_0)) IN" in compact_sql
    assert params["latest_span_limit"] == 200
    assert "lim" not in params
    assert params["latest_span_sampling_salt"] == "task-1"
    assert params["latest_span_sampling_rate"] == 50.0


def test_eval_task_sort_spills_before_selector_memory_cap():
    settings = row_resolver._EVAL_TASK_READ_SETTINGS

    assert settings["max_memory_usage"] == 256 * 1024 * 1024
    assert settings["max_bytes_before_external_sort"] == 128 * 1024 * 1024
    assert 0 < settings["max_bytes_before_external_sort"] < settings["max_memory_usage"]


def test_continuous_span_query_streams_without_full_window_sort():
    sql, params = row_resolver._build_sample_query(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        salt="task-1",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:03:00Z",
            ]
        },
        limit=None,
    )

    assert "LIMIT 1 BY id" not in sql
    assert "FROM spans FINAL" not in sql
    assert "ORDER BY" not in sql
    assert "id_limit" not in params
    assert "lim" not in params


@pytest.mark.parametrize(
    ("row_type", "group_marker", "version_param"),
    [
        (RowType.SPANS, "GROUP BY id", "latest_span_changed_since_version"),
        (
            RowType.TRACES,
            "LIMIT 1 BY grouped_trace_id",
            "latest_root_changed_since_version",
        ),
    ],
)
def test_continuous_final_status_uses_no_final_latest_state_stream(
    monkeypatch,
    row_type,
    group_marker,
    version_param,
):
    class FakeReader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            yield ["changed-id"]

        def close(self):
            pass

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task_start = datetime(2026, 7, 30, 12)
    cursor = datetime(2026, 7, 30, 12, 5)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=row_type,
        id=f"continuous-{row_type}",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:10:00Z",
            ],
            "filters": [
                {
                    "column_id": "final_status",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "approved",
                    },
                }
            ],
        },
        run_type=RunType.CONTINUOUS,
        spans_limit=None,
        continuous_cursor=cursor,
        start_time=task_start,
        created_at=task_start,
    )

    assert list(row_resolver.iter_desired_rows(task)) == [["changed-id"]]
    assert len(reader.calls) == 1
    sql, params, settings = reader.calls[0]
    compact_sql = " ".join(sql.split())
    assert "FROM spans FINAL" not in compact_sql
    assert group_marker in compact_sql
    assert "final_status" in compact_sql
    assert "_version >=" in compact_sql
    assert params[version_param] == row_resolver._unix_nanoseconds(cursor)
    # Candidate discovery uses the cursor, while latest-state classification
    # retains the task's original start-time scope.
    assert params["start_date"] == task_start
    assert settings == row_resolver._EVAL_TASK_READ_SETTINGS
    assert not any(key.endswith("_limit") for key in params)


def test_continuous_mixed_filter_shape_keeps_established_streaming_path():
    """The SOS scalar route must not approximate unsupported mixed semantics."""

    sql, params = row_resolver._build_sample_query(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        salt="task-mixed",
        sampling_rate=100,
        filters={
            "filters": [
                {
                    "column_id": "final_status",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "approved",
                    },
                },
                {
                    "column_id": "status",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "OK",
                    },
                },
            ]
        },
        limit=None,
        created_at_floor=datetime(2026, 7, 30, 12, 5),
        continuous_start=datetime(2026, 7, 30, 12),
    )

    assert "latest_span_changed_since_version" not in params
    assert "_version >=" not in sql
    assert "ORDER BY" not in sql


def test_trace_task_preview_filters_final_status_on_scoped_root_row():
    sql, params = row_resolver._build_sample_query(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.TRACES,
        salt="task-final-status",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-01T00:00:00Z",
                "2026-07-30T00:00:00Z",
            ],
            "filters": [
                {
                    "column_id": "final_status",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "completed",
                    },
                }
            ],
        },
        limit=100,
    )
    compact_sql = " ".join(sql.split())

    assert "trace_id IN (SELECT trace_id FROM spans" not in compact_sql
    assert "FINAL" not in compact_sql
    assert "LIMIT 1 BY grouped_trace_id" in compact_sql
    assert "latest_parent_span_id IS NULL" in compact_sql
    assert compact_sql.index("LIMIT 1 BY grouped_trace_id") < compact_sql.rindex(
        "latest_attr_exists_0"
    )
    assert "mapContains(attrs_string, 'final_status')" in compact_sql
    assert "mapValues(attrs_string)" not in compact_sql
    assert "project_id = %(project_id)s" in compact_sql
    assert "start_time >= %(latest_root_slice_start)s" in compact_sql
    assert "start_time < %(latest_root_slice_end)s" in compact_sql
    assert "toStartOfMinute(latest_start_time) DESC" in compact_sql
    assert compact_sql.count("LIMIT %(latest_root_limit)s") == 1
    assert params["latest_root_limit"] == 200
    assert params["project_id"] == "11111111-1111-1111-1111-111111111111"


def test_trace_legacy_observation_type_keeps_required_outer_filter():
    sql, params = row_resolver._build_sample_query(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.TRACES,
        salt="task-legacy-observation-type",
        sampling_rate=100,
        filters={"observation_type": ["llm"]},
        limit=10,
    )
    compact_sql = " ".join(sql.split())

    assert compact_sql.count("ORDER BY toStartOfMinute(start_time) DESC, trace_id") == 1
    assert (
        compact_sql.count(
            "ORDER BY toStartOfMinute(eval_order_start_time) DESC, trace_id"
        )
        == 1
    )
    assert compact_sql.count("LIMIT %(id_limit)s") == 1
    assert compact_sql.count("LIMIT %(lim)s") == 1
    assert "trace_id IN (SELECT trace_id FROM spans" in compact_sql
    assert params["id_limit"] == 20
    assert params["lim"] == 20
    assert params["otypes"] == ("llm",)


@pytest.mark.parametrize(
    ("row_type", "id_column", "candidate_limit"),
    [
        (RowType.SPANS, "id", 20),
        (RowType.TRACES, "trace_id", 20),
        (RowType.SESSIONS, "session_id", 10),
    ],
)
def test_task_identity_siblings_and_created_at_reach_bounded_v2_sql(
    row_type, id_column, candidate_limit
):
    created_at = "2026-07-01T12:30:00Z"
    session_id = "22222222-2222-2222-2222-222222222222"
    sql, params = row_resolver._build_sample_query(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=row_type,
        salt="task-1",
        sampling_rate=50,
        filters={
            "span_id": ["span-1"],
            "trace_id": ["trace-1"],
            "session_id": [session_id],
            "created_at": created_at,
        },
        limit=10,
    )
    compact_sql = " ".join(sql.split())
    bound_values = {
        str(item)
        for value in params.values()
        for item in (value if isinstance(value, tuple) else ())
    }

    assert {"span-1", "trace-1", session_id} <= bound_values
    assert params["start_date"] == datetime(2026, 7, 1, 12, 30)
    if row_type == RowType.SPANS:
        assert "GROUP BY id" in compact_sql
        assert compact_sql.count("latest_span_column_value_") >= 3
        assert params["latest_span_sampling_salt"] == "task-1"
        assert params["latest_span_sampling_rate"] == 50.0
        assert params["latest_span_limit"] == candidate_limit
        assert "lim" not in params
        assert (
            "ORDER BY toStartOfMinute(latest_start_time) DESC, grouped_id ASC"
            in compact_sql
        )
        assert compact_sql.count("ORDER BY") == 1
        assert compact_sql.count("LIMIT %(latest_span_limit)s") == 1
        assert "WHERE 1 = 1" not in compact_sql
    else:
        assert "id IN" in compact_sql
        assert "trace_id IN" in compact_sql
        assert "trace_session_id" in compact_sql
        assert (
            "trace_session_id IN" in compact_sql
            or "trace_session_id) IN" in compact_sql
        )
        assert params["id_sampling_salt"] == "task-1"
        assert params["id_sampling_rate"] == 50.0
        assert params["id_limit"] == candidate_limit
    if row_type == RowType.TRACES:
        assert "lim" not in params
        assert f"ORDER BY toStartOfMinute(start_time) DESC, {id_column}" in compact_sql
        assert compact_sql.count("ORDER BY") == 1
        assert compact_sql.count("LIMIT %(id_limit)s") == 1
        assert "WHERE 1 = 1" not in compact_sql
    elif row_type == RowType.SESSIONS:
        assert params["lim"] == candidate_limit
        assert f"ORDER BY {id_column}" in compact_sql
        assert "WHERE 1 = 1" in compact_sql


def test_historical_span_caller_deduplicates_and_trims(monkeypatch):
    class FakeReader:
        def __init__(self):
            self.sql = None
            self.params = None
            self.closed = False

        def stream_query(self, sql, params, *, batch_size, settings):
            self.sql = sql
            self.params = params
            assert batch_size == 2
            assert settings == row_resolver._EVAL_TASK_READ_SETTINGS
            yield ["span-a", "span-a"]
            yield ["span-b", "span-c"]
            yield ["span-d"]

        def close(self):
            self.closed = True

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        id="task-1",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:03:00Z",
            ]
        },
        run_type=RunType.HISTORICAL,
        spans_limit=3,
    )

    assert list(row_resolver.iter_desired_rows(task, batch_size=2)) == [
        ["span-a", "span-b"],
        ["span-c"],
    ]
    assert "LIMIT 1 BY id" not in reader.sql
    assert reader.params["id_limit"] == 6
    assert "lim" not in reader.params
    assert reader.closed is True


def test_historical_span_filter_uses_exact_whole_slice_seed(monkeypatch):
    class FakeReader:
        def __init__(self):
            self.calls = []
            self.closed = False

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            assert batch_size == 2
            if len(self.calls) == 1:
                raise TimeoutError("private ClickHouse timeout detail")
            yield ["span-a", "span-b", "span-c"]

        def close(self):
            self.closed = True

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        id="task-prompt-slug",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:03:00Z",
            ],
            "filters": [
                {
                    "column_id": "prompt_slug",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "synthetic_prompt_v2",
                    },
                }
            ],
        },
        run_type=RunType.HISTORICAL,
        spans_limit=3,
    )

    assert list(row_resolver.iter_desired_rows(task, batch_size=2)) == [
        ["span-a", "span-b"],
        ["span-c"],
    ]
    assert len(reader.calls) == 2
    seed_sql, seed_params, seed_settings = reader.calls[1]
    assert seed_params["latest_span_slice_start"] == datetime(2026, 7, 30, 12)
    assert seed_params["latest_span_slice_end"] == datetime(2026, 7, 30, 12, 3)
    assert seed_params["latest_span_limit"] == 4
    assert "FINAL" not in seed_sql
    assert "GROUP BY id" in seed_sql
    assert "prompt_slug" in seed_sql
    assert seed_settings["max_result_rows"] == 4

    assert reader.closed is True


def test_prompt_slug_in_compiles_and_hydrates_preview_mapping_path():
    """The reported task filter remains visible after point hydration."""
    from tracer.services.clickhouse.v2.query_builders.span_list import (
        SpanListQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.span_selectors import (
        flatten_span_attributes_into_entry,
    )

    expected_slug = "agent_2_identity_disclosure"
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    "2026-07-24T00:00:00Z",
                    "2026-07-31T00:00:00Z",
                ],
            },
        },
        {
            "column_id": "prompt_slug",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": [expected_slug],
            },
        },
    ]
    builder = SpanListQueryBuilderV2(
        project_id="11111111-1111-1111-1111-111111111111",
        filters=filters,
        page_number=0,
        page_size=10,
    )

    assert builder.supports_latest_candidate_page() is True
    classifier_sql, classifier_params = (
        builder.build_latest_attribute_candidate_matches(["span-prompt-slug"])
    )
    assert "latest_attr_value_0" in classifier_sql
    assert "latest_attr_exists_0" in classifier_sql
    assert any(
        value == (expected_slug,)
        for key, value in classifier_params.items()
        if key.startswith("latest_attr_param_")
    )

    hydration_sql, hydration_params = builder.build_preview_hydration_query(
        ["span-prompt-slug"]
    )
    assert "mapFilter(" in hydration_sql
    assert "AS attrs_string" in hydration_sql
    assert hydration_params["preview_text_keys"] == ("prompt_slug",)

    entry = {"span_id": "span-prompt-slug"}
    flatten_span_attributes_into_entry(
        entry,
        {
            "attrs_string": {"prompt_slug": expected_slug},
            "attrs_number": {},
            "attrs_bool": {},
            "attributes_extra": "{}",
        },
    )
    assert entry["prompt_slug"] == expected_slug


def test_historical_span_probe_prevents_older_attribute_match_resurfacing(
    monkeypatch,
):
    """A newer key clear rejects the id once, including in older windows."""

    class FakeReader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if len(self.calls) == 1:
                raise TimeoutError("whole-window budget")
            if "cross_slice_span_ids" in params:
                if "span-stale" in params["cross_slice_span_ids"]:
                    yield ["span-stale"]
                return
            if "candidate_span_ids" in params:
                if params["candidate_span_ids"] == ("span-stale",):
                    # Full-window argMax sees the newer key-clearing version.
                    return
                yield ["span-valid"]
                return
            if params.get("latest_span_after_id") == "span-stale":
                yield ["span-valid"]
                return
            if params["latest_span_slice_start"] == datetime(2026, 7, 30, 12, 5):
                yield ["span-stale"]
            else:
                # The older physical version still has the matching value, but
                # the id was marked seen before the newer probe rejected it.
                yield ["span-stale", "span-valid"]

        def close(self):
            pass

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        id="task-no-resurrection",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:10:00Z",
            ],
            "filters": [
                {
                    "column_id": "final_status",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "Rechazado",
                    },
                }
            ],
        },
        run_type=RunType.HISTORICAL,
        spans_limit=1,
    )

    assert list(row_resolver.iter_desired_rows(task)) == [["span-valid"]]
    probes = [
        params["candidate_span_ids"]
        for _, params, _ in reader.calls
        if "candidate_span_ids" in params
    ]
    assert probes == [("span-stale",)]
    assert all(
        params["candidate_start_date"] == datetime(2026, 7, 30, 12)
        and params["candidate_end_date"] == datetime(2026, 7, 30, 12, 10)
        for _, params, _ in reader.calls
        if "candidate_span_ids" in params
    )


@pytest.mark.integration
def test_historical_span_fallback_real_ch_rejects_stale_versions_and_tombstones(
    row_resolver_ch,
):
    """The actual fallback SQL classifies every seed over the full window."""
    client = row_resolver_ch
    project = uuid.uuid4()
    other_project = uuid.uuid4()
    window_start = datetime(2026, 7, 30, 12, tzinfo=UTC)
    old_time = window_start + timedelta(minutes=2)
    new_time = window_start + timedelta(minutes=7)
    window_end = window_start + timedelta(minutes=10)

    def row(
        project_id,
        span_id,
        when,
        final_status,
        *,
        deleted=0,
        version=1,
    ):
        return [
            project_id,
            f"trace-{span_id}",
            span_id,
            when,
            ({"final_status": final_status} if final_status is not None else {}),
            {},
            {},
            deleted,
            version,
        ]

    client.insert(
        "spans",
        [
            row(project, "span-valid", old_time, "Rechazado"),
            row(project, "span-cleared", old_time, "Rechazado"),
            row(project, "span-cleared", new_time, None, version=2),
            row(project, "span-nonmatch", old_time, "Rechazado"),
            row(project, "span-nonmatch", new_time, "Aprobado", version=2),
            row(project, "span-tombstone", old_time, "Rechazado"),
            row(
                project,
                "span-tombstone",
                new_time,
                "Rechazado",
                deleted=1,
                version=2,
            ),
            row(other_project, "span-other-project", old_time, "Rechazado"),
        ],
        column_names=[
            "project_id",
            "trace_id",
            "id",
            "start_time",
            "attrs_string",
            "attrs_number",
            "attrs_bool",
            "is_deleted",
            "_version",
        ],
    )

    class ForcedFallbackReader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if len(self.calls) == 1:
                raise TimeoutError("force the bounded fallback")
            result = client.query(sql, parameters=params, settings=settings)
            values = [str(row[0]) for row in result.result_rows]
            for offset in range(0, len(values), batch_size):
                yield values[offset : offset + batch_size]

    filters = {
        "date_range": [window_start, window_end],
        "filters": [
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "Rechazado",
                },
            }
        ],
    }
    reader = ForcedFallbackReader()
    resolved = row_resolver._resolve_bounded_historical_span_ids(
        reader,
        sql="SELECT id FROM spans",
        params={
            "start_date": window_start,
            "end_date": window_end,
            "id_limit": 20,
        },
        project_id=str(project),
        salt="real-ch-task",
        sampling_rate=100,
        filters=filters,
        limit=10,
        batch_size=25,
        row_type=RowType.SPANS,
    )

    assert resolved == ["span-valid"]
    assert all("FINAL" not in sql for sql, _, _ in reader.calls[1:])
    probe_params = [
        params for _, params, _ in reader.calls if "candidate_span_ids" in params
    ]
    assert probe_params
    assert all(
        params["candidate_start_date"] == window_start for params in probe_params
    )
    assert all(params["candidate_end_date"] == window_end for params in probe_params)


def test_historical_root_filter_uses_exact_whole_slice_seed(monkeypatch):
    class FakeReader:
        def __init__(self):
            self.calls = []
            self.closed = False

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if len(self.calls) == 1:
                raise TimeoutError("private ClickHouse timeout detail")

            yield ["trace-a", "trace-b", "trace-c"]

        def close(self):
            self.closed = True

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.TRACES,
        id="task-final-status",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:03:00Z",
            ],
            "filters": [
                {
                    "column_id": "final_status",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "status_rejected",
                    },
                }
            ],
        },
        run_type=RunType.HISTORICAL,
        spans_limit=3,
    )

    assert list(row_resolver.iter_desired_rows(task, batch_size=2)) == [
        ["trace-a", "trace-b"],
        ["trace-c"],
    ]
    assert len(reader.calls) == 2
    seed_sql, seed_params, seed_settings = reader.calls[1]
    assert seed_params["latest_root_slice_start"] == datetime(2026, 7, 30, 12)
    assert seed_params["latest_root_slice_end"] == datetime(2026, 7, 30, 12, 3)
    assert seed_params["latest_root_limit"] == 4
    assert "FINAL" not in seed_sql and "final_status" in seed_sql
    assert seed_settings["max_result_rows"] == 4
    assert reader.closed is True


def test_historical_time_only_trace_seed_is_exact_for_whole_slice(monkeypatch):
    class FakeReader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if len(self.calls) == 1:
                raise TimeoutError("whole-window budget")
            yield ["trace-a"]

        def close(self):
            pass

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.TRACES,
        id="task-time-only-trace",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:03:00Z",
            ]
        },
        run_type=RunType.HISTORICAL,
        spans_limit=1,
    )

    assert list(row_resolver.iter_desired_rows(task)) == [["trace-a"]]
    assert len(reader.calls) == 2
    seed_sql, seed_params, seed_settings = reader.calls[1]
    assert "FINAL" not in seed_sql
    assert "latest_parent_span_id" in seed_sql
    assert "latest_is_deleted = 0" in seed_sql
    assert seed_params["latest_root_limit"] == 2
    assert seed_settings["max_result_rows"] == 2


def test_historical_trace_cross_slice_probe_catches_root_that_became_child(
    monkeypatch,
):
    """Any later physical row forces verification, even when it is non-root."""

    class FakeReader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if len(self.calls) == 1:
                raise TimeoutError("whole-window budget")
            if "cross_slice_trace_ids" in params:
                # The latest physical version changed the former root into a
                # child in the adjacent slice. A root-only history probe would
                # miss it and incorrectly accept the older matching root.
                assert "parent_span_id IS NULL" not in sql
                assert "parent_span_id = ''" not in sql
                yield ["trace-root-became-child"]
                return
            if "candidate_trace_ids" in params:
                # Full-window latest-state resolution correctly finds no
                # canonical root satisfying the saved root predicate.
                return
            if params["latest_root_slice_start"] == datetime(2026, 7, 30, 12):
                yield ["trace-root-became-child"]

        def close(self):
            pass

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.TRACES,
        id="task-root-became-child",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:10:00Z",
            ],
            "filters": [
                {
                    "column_id": "final_status",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "Rechazado",
                    },
                }
            ],
        },
        run_type=RunType.HISTORICAL,
        spans_limit=1,
    )

    assert list(row_resolver.iter_desired_rows(task)) == []
    cross_calls = [
        (sql, params)
        for sql, params, _ in reader.calls
        if "cross_slice_trace_ids" in params
    ]
    assert len(cross_calls) == 1
    cross_sql, cross_params = cross_calls[0]
    assert cross_params["cross_slice_trace_ids"] == ("trace-root-became-child",)
    assert "parent_span_id" not in cross_sql
    assert any("candidate_trace_ids" in params for _, params, _ in reader.calls)


@pytest.mark.parametrize(
    ("date_range", "expected_seed_start"),
    [
        (
            ["2026-07-30T12:00:00Z", "2026-07-30T12:03:00Z"],
            datetime(2026, 7, 30, 12),
        ),
        (
            ["2026-05-01T00:00:00Z", "2026-05-08T00:00:00Z"],
            datetime(2026, 5, 7, 23, 55),
        ),
    ],
)
def test_historical_trace_any_span_filter_verifies_original_window(
    monkeypatch,
    date_range,
    expected_seed_start,
):
    """A child outside the root minute/day is still matched by the full probe."""

    class FakeReader:
        def __init__(self):
            self.calls = []
            self.closed = False

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if "id_limit" in params:
                raise TimeoutError("whole-window budget")
            if "candidate_trace_ids" not in params:
                yield ["trace-with-remote-child"]
                return
            yield ["trace-with-remote-child"]

        def close(self):
            self.closed = True

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.TRACES,
        id="task-any-span-attribute",
        sampling_rate=100,
        filters={
            "date_range": date_range,
            "filters": [
                {
                    "column_id": "synthetic_child_attribute",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "synthetic_value",
                    },
                }
            ],
        },
        run_type=RunType.HISTORICAL,
        spans_limit=1,
    )

    assert list(row_resolver.iter_desired_rows(task)) == [["trace-with-remote-child"]]

    expected_call_count = 3 if len(reader.calls) == 3 else 2
    assert len(reader.calls) == expected_call_count
    seed_index = 1 if expected_call_count == 3 else 0
    seed_sql, seed_params, seed_settings = reader.calls[seed_index]
    assert "FINAL" not in seed_sql
    assert "GROUP BY trace_id, id" in seed_sql
    assert "LIMIT 1 BY grouped_trace_id" in seed_sql
    assert "synthetic_child_attribute" not in seed_sql
    assert seed_params["latest_root_slice_start"] == expected_seed_start
    assert seed_params["latest_root_slice_end"] == datetime.fromisoformat(
        date_range[1].replace("Z", "")
    )
    assert seed_params["project_id"] == str(task.project_id)
    assert seed_settings["max_execution_time"] <= 0.75
    assert seed_settings["max_threads"] == 2
    assert seed_settings["max_result_rows"] == 2
    assert seed_settings["use_skip_indexes_if_final"] == 1

    probe_sql, probe_params, probe_settings = reader.calls[seed_index + 1]
    assert "synthetic_child_attribute" in probe_sql
    assert "FINAL" not in probe_sql
    assert probe_sql.count("trace_id IN %(candidate_trace_ids)s") >= 1
    assert probe_params["candidate_trace_ids"] == ("trace-with-remote-child",)
    assert probe_params["candidate_start_date"] == datetime.fromisoformat(
        date_range[0].replace("Z", "")
    )
    assert probe_params["candidate_end_date"] == datetime.fromisoformat(
        date_range[1].replace("Z", "")
    )
    assert probe_settings["max_execution_time"] <= 0.75
    assert probe_settings["max_threads"] == 2
    assert probe_settings["max_result_rows"] == 1
    assert probe_settings["use_skip_indexes_if_final"] == 1
    assert reader.closed is True


def test_historical_trace_negative_membership_verifies_remote_child(monkeypatch):
    """NOT IN must see a disqualifying child outside the root slice ±1 day."""

    class FakeReader:
        def __init__(self):
            self.calls = []
            self.seed_calls = 0

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if "candidate_trace_ids" not in params:
                self.seed_calls += 1
                if self.seed_calls == 1:
                    yield ["trace-clean", "trace-disqualified"]
                return
            # The full-window verifier excludes the candidate whose old child
            # carries the forbidden end-user value.
            assert "trace_id NOT IN (SELECT" in sql
            yield ["trace-clean"]

        def close(self):
            pass

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.TRACES,
        id="task-negative-child-filter",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-05-01T00:00:00Z",
                "2026-05-08T00:00:00Z",
            ],
            "filters": [
                {
                    "column_id": "user_id",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "text",
                        "filter_op": "not_equals",
                        "filter_value": "forbidden-user",
                    },
                }
            ],
        },
        run_type=RunType.HISTORICAL,
        spans_limit=2,
    )

    assert list(row_resolver.iter_desired_rows(task)) == [["trace-clean"]]
    assert len(reader.calls) > 2
    seed_sql, seed_params, _ = reader.calls[0]
    probe_sql, probe_params, _ = reader.calls[1]
    assert "forbidden-user" not in seed_sql
    assert seed_params["latest_root_slice_start"] == datetime(2026, 5, 7, 23, 55)
    assert "trace_id NOT IN (SELECT" in probe_sql
    assert "trace_id IN %(candidate_trace_ids)s" in probe_sql
    assert probe_params["candidate_trace_ids"] == (
        "trace-clean",
        "trace-disqualified",
    )
    assert probe_params["candidate_start_date"] == datetime(2026, 5, 1)
    assert probe_params["candidate_end_date"] == datetime(2026, 5, 8)


def test_trace_candidate_probe_rejects_rows_yielded_before_error(monkeypatch):
    """A driver error after a partial block cannot become a false success."""

    class FakeReader:
        def __init__(self):
            self.calls = 0
            self.closed = False

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("whole-window budget")
            if self.calls == 2:
                yield ["trace-partial"]
                return
            yield ["trace-partial"]
            raise TimeoutError("probe failed after yielding a partial block")

        def close(self):
            self.closed = True

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.TRACES,
        id="task-partial-probe",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:01:00Z",
            ],
            "filters": [
                {
                    "column_id": "child_key",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "match",
                    },
                }
            ],
        },
        run_type=RunType.HISTORICAL,
        spans_limit=1,
    )

    with pytest.raises(
        row_resolver.EvalTaskReadBudgetExceeded,
        match="Evaluation task row selection exceeded its read budget",
    ):
        list(row_resolver.iter_desired_rows(task))

    assert reader.calls == 3
    assert reader.closed is True


def test_span_classifier_proactively_chunks_before_any_read():
    calls = []

    class Reader:
        def stream_query(self, sql, params, *, batch_size, settings):
            ids = tuple(params["candidate_span_ids"])
            calls.append((ids, dict(settings)))
            yield list(ids)

    candidate_ids = [f"span-{index:02d}" for index in range(25)]
    matching = row_resolver._verify_span_candidates(
        Reader(),
        candidate_ids=candidate_ids,
        project_id="11111111-1111-1111-1111-111111111111",
        filters={"filters": []},
        start_date=datetime(2026, 7, 17, tzinfo=UTC),
        end_date=datetime(2026, 7, 31, tzinfo=UTC),
        batch_size=25,
        deadline=row_resolver.monotonic() + 10,
    )

    assert matching == set(candidate_ids)
    assert [len(batch) for batch, _ in calls] == [25]
    assert calls[0][1]["max_memory_usage"] == 512 * 1024 * 1024
    assert calls[0][1]["max_bytes_to_read"] == 2 * 1024 * 1024 * 1024


def test_historical_span_selector_resolves_1063_direct_write_rows_boundedly():
    """Customer-scale task creation avoids one classifier per small batch."""

    expected_ids = [f"span-{index:04d}" for index in range(1063)]

    class Reader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if "cross_slice_span_ids" in params:
                # Direct-write rows have no physical history outside their
                # locally classified minute, so no full-window Map classifier
                # is necessary.
                return
            if "candidate_span_ids" in params:
                raise AssertionError("direct-write candidates need no global probe")

            slice_start = params["latest_span_slice_start"]
            slice_end = params["latest_span_slice_end"]
            page_limit = params["latest_span_limit"]
            if slice_end - slice_start > timedelta(minutes=1):
                # One sentinel beyond the 256-id cap forces refinement of only
                # this dense five-minute frontier.
                yield [f"coarse-{index:04d}" for index in range(page_limit)]
                return

            after_id = params.get("latest_span_after_id")
            start_index = 0 if after_id is None else expected_ids.index(after_id) + 1
            yield expected_ids[start_index : start_index + page_limit]

    reader = Reader()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    end = datetime(2026, 7, 31, tzinfo=UTC)
    resolved = row_resolver._resolve_bounded_historical_span_ids(
        reader,
        sql="SELECT id FROM spans",
        params={"start_date": start, "end_date": end, "id_limit": 2126},
        project_id="11111111-1111-1111-1111-111111111111",
        salt="customer-task-1063",
        sampling_rate=100,
        filters={
            "date_range": [start, end],
            "filters": [
                {
                    "column_id": "final_status",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "Rechazado",
                    },
                }
            ],
        },
        limit=1063,
        batch_size=25,
        row_type=RowType.SPANS,
    )

    assert resolved == expected_ids
    assert len(reader.calls) == 11
    seed_calls = [call for call in reader.calls if "latest_span_slice_start" in call[1]]
    cross_calls = [call for call in reader.calls if "cross_slice_span_ids" in call[1]]
    assert len(seed_calls) == 6
    assert len(cross_calls) == 5
    assert all(
        params["latest_span_sampling_salt"] == "customer-task-1063"
        and params["latest_span_sampling_rate"] == 100.0
        for _, params, _ in seed_calls
    )
    assert all(
        len(params["cross_slice_span_ids"]) <= 256
        and settings["max_memory_usage"] == 512 * 1024 * 1024
        and settings["max_bytes_to_read"] == 2 * 1024 * 1024 * 1024
        for _, params, settings in cross_calls
    )


def test_trace_classifier_proactively_chunks_before_any_read(monkeypatch):
    calls = []

    def fake_build_sample_query(**kwargs):
        ids = tuple(kwargs["candidate_trace_ids"])
        return "SELECT trace_id", {"candidate_trace_ids": ids}

    monkeypatch.setattr(row_resolver, "_build_sample_query", fake_build_sample_query)

    class Reader:
        def stream_query(self, sql, params, *, batch_size, settings):
            ids = tuple(params["candidate_trace_ids"])
            calls.append((ids, dict(settings)))
            yield list(ids)

    candidate_ids = [f"trace-{index:02d}" for index in range(25)]
    matching = row_resolver._verify_trace_candidates(
        Reader(),
        candidate_ids=candidate_ids,
        project_id="11111111-1111-1111-1111-111111111111",
        salt="task",
        sampling_rate=100,
        filters={"filters": []},
        batch_size=25,
        deadline=row_resolver.monotonic() + 10,
    )

    assert matching == set(candidate_ids)
    assert [len(batch) for batch, _ in calls] == [25]
    assert calls[0][1]["max_memory_usage"] == 512 * 1024 * 1024
    assert calls[0][1]["max_bytes_to_read"] == 2 * 1024 * 1024 * 1024


def test_read_attempt_budget_is_local_to_each_selector_invocation(monkeypatch):
    class Reader:
        def __init__(self):
            self.calls = 0

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls += 1
            yield ["span-a"]

    monkeypatch.setattr(row_resolver, "_EVAL_TASK_MAX_READ_ATTEMPTS", 1)
    reader = Reader()
    kwargs = {
        "sql": "SELECT id FROM spans LIMIT 2",
        "params": {
            "start_date": datetime(2026, 7, 30, 12, tzinfo=UTC),
            "end_date": datetime(2026, 7, 30, 12, 3, tzinfo=UTC),
            "id_limit": 2,
        },
        "project_id": "11111111-1111-1111-1111-111111111111",
        "salt": "task",
        "sampling_rate": 100,
        "filters": {"filters": []},
        "limit": 1,
        "batch_size": 25,
        "row_type": RowType.SPANS,
    }

    assert row_resolver._resolve_bounded_historical_span_ids(reader, **kwargs) == [
        "span-a"
    ]
    assert row_resolver._resolve_bounded_historical_span_ids(reader, **kwargs) == [
        "span-a"
    ]
    assert reader.calls == 2


def test_trace_candidate_probe_splits_and_reverifies_partial_batch(monkeypatch):
    """A failed multi-ID probe may split, but must re-read every accepted ID."""

    class FakeReader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if len(self.calls) == 1:
                raise TimeoutError("whole-window budget")
            if len(self.calls) == 2:
                yield ["trace-a", "trace-b"]
                return
            if len(self.calls) == 3:
                yield ["trace-a"]
                raise TimeoutError("combined candidate probe budget")
            yield list(params["candidate_trace_ids"])

        def close(self):
            pass

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.TRACES,
        id="task-split-probe",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:01:00Z",
            ],
            "filters": [
                {
                    "column_id": "child_key",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "match",
                    },
                }
            ],
        },
        run_type=RunType.HISTORICAL,
        spans_limit=2,
    )

    assert list(row_resolver.iter_desired_rows(task)) == [["trace-a", "trace-b"]]
    assert len(reader.calls) == 5
    assert reader.calls[2][1]["candidate_trace_ids"] == ("trace-a", "trace-b")
    assert reader.calls[3][1]["candidate_trace_ids"] == ("trace-a",)
    assert reader.calls[4][1]["candidate_trace_ids"] == ("trace-b",)
    assert all(
        call[2]["max_execution_time"] <= 0.75
        and call[2]["use_skip_indexes_if_final"] == 1
        for call in reader.calls[1:]
    )


def test_historical_fallback_proves_empty_future_tail_before_slicing(monkeypatch):
    now = datetime(2026, 5, 17, 3)

    class FakeReader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if "future_tail_start" in params:
                return
            if "cross_slice_span_ids" in params:
                return
            yield ["span-now"]

        def close(self):
            pass

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    monkeypatch.setattr(row_resolver.timezone, "now", lambda: now)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        id="task-future-toolbar-end",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-05-10T02:17:00Z",
                "2026-05-17T06:41:00Z",
            ]
        },
        run_type=RunType.HISTORICAL,
        spans_limit=1,
    )

    assert list(row_resolver.iter_desired_rows(task)) == [["span-now"]]
    assert len(reader.calls) == 3
    tail_query, tail_params, tail_settings = reader.calls[0]
    assert "FROM spans" in tail_query
    assert "FINAL" not in tail_query
    assert "parent_span_id" not in tail_query
    assert tail_params["future_tail_start"] == now + timedelta(minutes=5)
    assert tail_params["future_tail_end"] == datetime(2026, 5, 17, 6, 41)
    assert tail_settings["max_execution_time"] <= 0.1
    assert tail_settings["max_threads"] == 1
    seed_sql, seed_params, _ = reader.calls[1]
    assert "FINAL" not in seed_sql
    assert seed_params["latest_span_slice_start"] == now
    assert seed_params["latest_span_slice_end"] == now + timedelta(minutes=5)
    cross_sql, cross_params, cross_settings = reader.calls[2]
    assert "FINAL" not in cross_sql
    assert cross_params["cross_slice_span_ids"] == ("span-now",)
    assert cross_settings["max_memory_usage"] == 512 * 1024 * 1024


def test_historical_fallback_rejects_future_skewed_physical_span(monkeypatch):
    now = datetime(2026, 5, 17, 3)

    class FakeReader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params)))
            yield ["future-skewed-span"]

        def close(self):
            pass

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    monkeypatch.setattr(row_resolver.timezone, "now", lambda: now)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        id="task-future-skew",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-05-10T02:17:00Z",
                "2026-05-17T06:41:00Z",
            ]
        },
        run_type=RunType.HISTORICAL,
        spans_limit=1,
    )

    with pytest.raises(row_resolver.EvalTaskReadBudgetExceeded):
        list(row_resolver.iter_desired_rows(task))

    assert len(reader.calls) == 1


def test_whole_window_and_forced_fallback_select_identical_order(monkeypatch):
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        id="task-load-independent-order",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:02:00Z",
            ]
        },
        run_type=RunType.HISTORICAL,
        spans_limit=3,
    )

    class FakeReader:
        def __init__(self, *, force_fallback):
            self.force_fallback = force_fallback
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params)))
            if len(self.calls) == 1:
                if self.force_fallback:
                    raise TimeoutError("wide query timeout")
                # Canonical whole-window order: newest minute, then id.
                yield ["span-b", "span-c", "span-a"]
                return
            yield ["span-b", "span-c", "span-a"]

        def close(self):
            pass

    whole_reader = FakeReader(force_fallback=False)
    monkeypatch.setattr(row_resolver, "get_reader", lambda: whole_reader)
    whole_ids = [
        row_id for batch in row_resolver.iter_desired_rows(task) for row_id in batch
    ]

    fallback_reader = FakeReader(force_fallback=True)
    monkeypatch.setattr(row_resolver, "get_reader", lambda: fallback_reader)
    fallback_ids = [
        row_id for batch in row_resolver.iter_desired_rows(task) for row_id in batch
    ]

    assert whole_ids == fallback_ids == ["span-b", "span-c", "span-a"]
    assert (
        whole_reader.calls[0][0].count("ORDER BY toStartOfMinute(start_time) DESC, id")
        == 1
    )
    assert len(fallback_reader.calls) == 2
    seed_sql, seed_params = fallback_reader.calls[1]
    assert "FINAL" not in seed_sql
    assert seed_params["latest_span_slice_start"] == datetime(2026, 7, 30, 12)
    assert seed_params["latest_span_slice_end"] == datetime(2026, 7, 30, 12, 2)


def test_historical_span_slice_keysets_within_busy_minute(monkeypatch):
    class FakeReader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params)))
            if len(self.calls) == 1:
                raise TimeoutError("wide query timeout")
            if params["latest_span_limit"] == 3:
                # The sentinel proves this five-minute candidate window is
                # saturated. These provisional ids must be discarded.
                yield ["coarse-a", "coarse-b", "coarse-c"]
            elif params.get("latest_span_after_id") == "span-b":
                yield ["span-c", "span-d"]
            else:
                yield ["span-a", "span-b"]

        def close(self):
            pass

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    monkeypatch.setattr(row_resolver, "_EVAL_TASK_SLICE_PAGE_SIZE", 2)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        id="task-busy-minute",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:01:00Z",
            ],
            "filters": [
                {
                    "column_id": "arbitrary.string.key",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "contains",
                        "filter_value": "needle",
                    },
                }
            ],
        },
        run_type=RunType.HISTORICAL,
        spans_limit=4,
    )

    assert list(row_resolver.iter_desired_rows(task, batch_size=10)) == [
        ["span-a", "span-b", "span-c", "span-d"]
    ]
    assert len(reader.calls) == 4
    page_two_sql, page_two_params = reader.calls[3]
    assert "FINAL" not in page_two_sql
    assert "GROUP BY id" in page_two_sql
    assert "grouped_id > %(latest_span_after_id)s" in page_two_sql
    assert page_two_params["latest_span_after_id"] == "span-b"
    assert all("candidate_span_ids" not in params for _, params in reader.calls)


def test_whole_window_candidate_cap_with_enough_unique_ids_does_not_slice(
    monkeypatch,
):
    class FakeReader:
        def __init__(self):
            self.calls = 0

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls += 1
            assert params["id_limit"] == 6
            assert "lim" not in params
            yield ["span-a", "span-a", "span-b", "span-b", "span-c", "span-c"]

        def close(self):
            pass

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        id="task-complete-prefix",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:03:00Z",
            ]
        },
        run_type=RunType.HISTORICAL,
        spans_limit=3,
    )

    assert list(row_resolver.iter_desired_rows(task)) == [
        ["span-a", "span-b", "span-c"]
    ]
    assert reader.calls == 1


def test_historical_span_deadline_fails_explicitly_without_partial_rows(
    monkeypatch,
):
    class FakeReader:
        def __init__(self):
            self.calls = 0
            self.closed = False

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("DB::Exception private timeout")
            if self.calls == 2:
                raise TimeoutError("dense five-minute window")
            raise AssertionError("deadline should stop before a third query")
            yield  # pragma: no cover - keep this a generator

        def close(self):
            self.closed = True

    clock = iter([0.0, 0.1, 0.2, 1.1])
    monkeypatch.setattr(row_resolver, "monotonic", lambda: next(clock))
    monkeypatch.setattr(row_resolver, "_EVAL_TASK_TOTAL_READ_SECONDS", 1.0)
    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        id="task-deadline",
        sampling_rate=100,
        filters={
            "date_range": [
                "2026-07-30T12:00:00Z",
                "2026-07-30T12:02:00Z",
            ]
        },
        run_type=RunType.HISTORICAL,
        spans_limit=5,
    )

    with pytest.raises(row_resolver.EvalTaskReadBudgetExceeded) as exc_info:
        list(row_resolver.iter_desired_rows(task, batch_size=2))

    assert str(exc_info.value) == row_resolver._SAFE_READ_BUDGET_MESSAGE
    assert "DB::Exception" not in str(exc_info.value)
    assert reader.calls == 2
    assert reader.closed is True


def test_sparse_fourteen_day_fallback_widens_instead_of_querying_every_five_minutes():
    class EmptyReader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            return
            yield  # pragma: no cover - keep this function a generator

    reader = EmptyReader()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    end = start + timedelta(days=14)

    resolved = row_resolver._resolve_bounded_historical_span_ids(
        reader,
        sql="SELECT trace_id FROM spans",
        params={"start_date": start, "end_date": end, "lim": 20},
        project_id="11111111-1111-1111-1111-111111111111",
        salt="sparse-window",
        sampling_rate=100,
        filters={"date_range": [start, end], "filters": []},
        limit=10,
        batch_size=25,
        row_type=RowType.TRACES,
    )

    assert resolved == []
    assert len(reader.calls) <= 26
    seed_windows = [
        params["latest_root_slice_end"] - params["latest_root_slice_start"]
        for _, params, _ in reader.calls
        if "latest_root_slice_start" in params
    ]
    assert seed_windows[0] == timedelta(minutes=5)
    assert max(seed_windows) == timedelta(days=2)


def test_fallback_enforces_a_hard_read_attempt_cap_before_the_wall_deadline(
    monkeypatch,
):
    class DenseReader:
        def __init__(self):
            self.calls = 0

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls += 1
            if self.calls == 1:
                # Saturate the five-minute coarse seed, forcing one-minute
                # keyset refinement without accepting provisional ids.
                yield [f"coarse-{index:03d}" for index in range(11)]
                return
            if self.calls == 2:
                yield ["span-a"]
                return
            raise AssertionError("read cap must stop before a third query")

    reader = DenseReader()
    monkeypatch.setattr(row_resolver, "_EVAL_TASK_MAX_READ_ATTEMPTS", 2)
    start = datetime(2026, 7, 30, tzinfo=UTC)
    end = start + timedelta(days=1)

    with pytest.raises(row_resolver.EvalTaskReadBudgetExceeded):
        row_resolver._resolve_bounded_historical_span_ids(
            reader,
            sql="SELECT id FROM spans",
            params={"start_date": start, "end_date": end, "id_limit": 20},
            project_id="11111111-1111-1111-1111-111111111111",
            salt="attempt-cap",
            sampling_rate=100,
            filters={
                "date_range": [start, end],
                "filters": [
                    {
                        "column_id": "final_status",
                        "filter_config": {
                            "col_type": "SPAN_ATTRIBUTE",
                            "filter_type": "text",
                            "filter_op": "equals",
                            "filter_value": "approved",
                        },
                    }
                ],
            },
            limit=10,
            batch_size=25,
            row_type=RowType.SPANS,
        )

    assert reader.calls == 2


def test_mixed_system_attribute_and_sibling_filters_share_one_scalar_plan():
    session_id = "22222222-2222-2222-2222-222222222222"
    filters = {
        "date_range": [
            "2026-07-30T00:00:00Z",
            "2026-07-31T00:00:00Z",
        ],
        "trace_id": ["trace-target"],
        "session_id": [session_id],
        "observation_type": ["llm"],
        "filters": [
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "Rechazado",
                },
            },
            {
                "column_id": "status",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "OK",
                },
            },
            {
                "column_id": "cost",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 1.5,
                },
            },
        ],
    }

    assert row_resolver._span_candidate_verification_is_supported(filters) is True
    seed_sql, seed_params = row_resolver._build_span_candidate_seed_query(
        project_id="11111111-1111-1111-1111-111111111111",
        salt="mixed-task",
        sampling_rate=100,
        filters=filters,
        start=datetime(2026, 7, 30, 23, 55),
        end=datetime(2026, 7, 31),
        after_id=None,
        limit=25,
    )
    match_sql, match_params = row_resolver._build_span_candidate_match_query(
        candidate_ids=["span-target"],
        project_id="11111111-1111-1111-1111-111111111111",
        filters=filters,
        start_date=datetime(2026, 7, 30),
        end_date=datetime(2026, 7, 31),
    )

    for sql in (seed_sql, match_sql):
        compact = " ".join(sql.split())
        assert "FINAL" not in compact
        assert "mapContains(attrs_string, 'final_status')" in compact
        assert "argMax(tuple(status), _version).1" in compact
        assert "argMax(tuple(cost), _version).1" in compact
        assert "argMax(trace_id, _version)" in compact
        assert "argMax(tuple(trace_session_id), _version).1" in compact
        assert "argMax(observation_type, _version)" in compact
    assert seed_params["latest_attr_param_0"] == "rechazado"
    assert seed_params["latest_span_column_value_param_1"] == "ok"
    assert seed_params["latest_span_column_value_param_2"] == 1.5
    assert match_params["latest_span_column_value_param_3"] == ("trace-target",)
    assert match_params["latest_span_column_value_param_4"] == (session_id,)
    assert match_params["latest_span_column_value_param_5"] == ("llm",)


@pytest.mark.integration
def test_real_ch_stopped_merges_mixed_system_and_sibling_filter_is_latest_exact(
    row_resolver_ch,
):
    client = row_resolver_ch
    project = uuid.uuid4()
    session_id = uuid.uuid4()
    other_session_id = uuid.uuid4()
    window_start = datetime(2026, 7, 30, 12, tzinfo=UTC)
    old_time = window_start + timedelta(minutes=2)
    new_time = window_start + timedelta(minutes=7)
    window_end = window_start + timedelta(minutes=10)

    def row(
        span_id,
        when,
        *,
        trace_id="trace-target",
        session=session_id,
        observation_type="llm",
        status="OK",
        final_status="Rechazado",
        version=1,
    ):
        return [
            project,
            trace_id,
            span_id,
            when,
            session,
            observation_type,
            status,
            ({"final_status": final_status} if final_status is not None else {}),
            {},
            {},
            0,
            version,
        ]

    client.insert(
        "spans",
        [
            row("span-valid", old_time),
            row("span-stale-status", old_time),
            row("span-stale-status", new_time, status="ERROR", version=2),
            row("span-wrong-trace", old_time, trace_id="trace-other"),
            row("span-wrong-session", old_time, session=other_session_id),
            row("span-wrong-type", old_time, observation_type="tool"),
        ],
        column_names=[
            "project_id",
            "trace_id",
            "id",
            "start_time",
            "trace_session_id",
            "observation_type",
            "status",
            "attrs_string",
            "attrs_number",
            "attrs_bool",
            "is_deleted",
            "_version",
        ],
    )

    class Reader:
        def __init__(self):
            self.calls = []

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if len(self.calls) == 1:
                raise TimeoutError("force the bounded mixed-filter plan")
            result = client.query(sql, parameters=params, settings=settings)
            values = [str(row[0]) for row in result.result_rows]
            for offset in range(0, len(values), batch_size):
                yield values[offset : offset + batch_size]

    filters = {
        "date_range": [window_start, window_end],
        "trace_id": ["trace-target"],
        "session_id": [str(session_id)],
        "observation_type": ["llm"],
        "filters": [
            {
                "column_id": "final_status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "Rechazado",
                },
            },
            {
                "column_id": "status",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "OK",
                },
            },
        ],
    }
    reader = Reader()
    resolved = row_resolver._resolve_bounded_historical_span_ids(
        reader,
        sql="SELECT id FROM spans",
        params={
            "start_date": window_start,
            "end_date": window_end,
            "id_limit": 20,
        },
        project_id=str(project),
        salt="mixed-real-ch-task",
        sampling_rate=100,
        filters=filters,
        limit=10,
        batch_size=25,
        row_type=RowType.SPANS,
    )

    assert resolved == ["span-valid"]
    assert any("cross_slice_span_ids" in params for _, params, _ in reader.calls)
    assert any("candidate_span_ids" in params for _, params, _ in reader.calls)
    assert all("FINAL" not in sql for sql, _, _ in reader.calls)


@pytest.mark.integration
def test_real_ch_prompt_slug_task_selector_rejects_key_clear_and_tombstone(
    row_resolver_ch,
    monkeypatch,
):
    """The exact reported prompt_slug survives the bounded task selector.

    Merges remain stopped by the module fixture, so stale physical matches stay
    present.  The selector must still return the newest matching row while
    rejecting an older match superseded by either a key clear or a tombstone.
    """
    client = row_resolver_ch
    project = uuid.uuid4()
    expected_slug = "agent_2_identity_disclosure"
    window_start = datetime(2026, 7, 30, 12, tzinfo=UTC)
    old_time = window_start + timedelta(minutes=2)
    new_time = window_start + timedelta(minutes=7)
    window_end = window_start + timedelta(minutes=10)

    def row(
        span_id,
        when,
        *,
        prompt_slug=expected_slug,
        is_deleted=0,
        version=1,
    ):
        attrs_string = {} if prompt_slug is None else {"prompt_slug": prompt_slug}
        return [
            project,
            f"trace-{span_id}",
            span_id,
            when,
            None,
            "llm",
            "OK",
            attrs_string,
            {},
            {},
            is_deleted,
            version,
        ]

    client.insert(
        "spans",
        [
            row("span-valid", old_time),
            row("span-key-cleared", old_time),
            row("span-key-cleared", new_time, prompt_slug=None, version=2),
            row("span-tombstoned", old_time),
            row("span-tombstoned", new_time, is_deleted=1, version=2),
            row("span-latest-match", old_time, prompt_slug="other", version=1),
            row("span-latest-match", new_time, version=2),
            row("span-never-matched", old_time, prompt_slug="other"),
        ],
        column_names=[
            "project_id",
            "trace_id",
            "id",
            "start_time",
            "trace_session_id",
            "observation_type",
            "status",
            "attrs_string",
            "attrs_number",
            "attrs_bool",
            "is_deleted",
            "_version",
        ],
    )

    class Reader:
        def __init__(self):
            self.calls = []
            self.closed = False

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls.append((sql, dict(params), dict(settings)))
            if len(self.calls) == 1:
                raise TimeoutError("force the bounded prompt_slug selector")
            result = client.query(sql, parameters=params, settings=settings)
            values = [str(result_row[0]) for result_row in result.result_rows]
            for offset in range(0, len(values), batch_size):
                yield values[offset : offset + batch_size]

        def close(self):
            self.closed = True

    reader = Reader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    task = SimpleNamespace(
        project_id=project,
        row_type=RowType.SPANS,
        id="customer-prompt-slug-task",
        sampling_rate=100,
        filters={
            "date_range": [window_start, window_end],
            "filters": [
                {
                    "column_id": "prompt_slug",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": expected_slug,
                    },
                }
            ],
        },
        run_type=RunType.HISTORICAL,
        spans_limit=10,
    )

    assert list(row_resolver.iter_desired_rows(task, batch_size=25)) == [
        ["span-latest-match", "span-valid"]
    ]
    assert reader.closed is True
    assert any(
        "latest_span_slice_start" in params and "prompt_slug" in sql
        for sql, params, _ in reader.calls
    )
    assert any("cross_slice_span_ids" in params for _, params, _ in reader.calls)
    assert any("candidate_span_ids" in params for _, params, _ in reader.calls)
    assert all("FINAL" not in sql for sql, _, _ in reader.calls)


def test_unsupported_mutable_span_shape_fails_closed_after_whole_window_budget():
    class FailingReader:
        def __init__(self):
            self.calls = 0

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls += 1
            raise TimeoutError("whole-window budget")
            yield  # pragma: no cover - keep this function a generator

    reader = FailingReader()
    start = datetime(2026, 7, 30, tzinfo=UTC)
    end = start + timedelta(days=1)

    with pytest.raises(row_resolver.EvalTaskReadBudgetExceeded):
        row_resolver._resolve_bounded_historical_span_ids(
            reader,
            sql="SELECT id FROM spans",
            params={"start_date": start, "end_date": end, "id_limit": 20},
            project_id="11111111-1111-1111-1111-111111111111",
            salt="unsupported-datetime-filter",
            sampling_rate=100,
            filters={
                "date_range": [start, end],
                "filters": [
                    {
                        "column_id": "end_time",
                        "filter_config": {
                            "col_type": "SYSTEM_METRIC",
                            "filter_type": "datetime",
                            "filter_op": "greater_than",
                            "filter_value": "2026-07-30T12:00:00Z",
                        },
                    }
                ],
            },
            limit=10,
            batch_size=25,
            row_type=RowType.SPANS,
        )

    assert reader.calls == 1


def test_historical_span_programming_error_is_not_misreported_as_budget(
    monkeypatch,
):
    class FakeReader:
        def stream_query(self, sql, params, *, batch_size, settings):
            raise ValueError("invalid compiled SQL")
            yield  # pragma: no cover - keep this a generator

        def close(self):
            pass

    monkeypatch.setattr(row_resolver, "get_reader", FakeReader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        id="task-programming-error",
        sampling_rate=100,
        filters={},
        run_type=RunType.HISTORICAL,
        spans_limit=5,
    )

    with pytest.raises(ValueError, match="invalid compiled SQL"):
        list(row_resolver.iter_desired_rows(task))


@pytest.mark.parametrize("row_type", [RowType.SPANS, RowType.TRACES])
def test_large_historical_span_trace_limit_fails_before_clickhouse(
    monkeypatch, row_type
):
    class FakeReader:
        def __init__(self):  # pragma: no cover - construction is the failure
            pytest.fail("an unsupported task must not open ClickHouse")

    monkeypatch.setattr(row_resolver, "get_reader", FakeReader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=row_type,
        id="task-large-stream",
        sampling_rate=100,
        filters={},
        run_type=RunType.HISTORICAL,
        spans_limit=row_resolver._EVAL_TASK_BUFFERED_ID_LIMIT + 1,
    )

    with pytest.raises(row_resolver.EvalTaskReadBudgetExceeded) as exc_info:
        list(row_resolver.iter_desired_rows(task, batch_size=2))

    message = str(exc_info.value)
    assert str(row_resolver._EVAL_TASK_BUFFERED_ID_LIMIT) in message
    assert "ClickHouse" not in message


def test_large_historical_session_limit_keeps_existing_streaming_contract(monkeypatch):
    class FakeReader:
        def stream_query(self, sql, params, *, batch_size, settings):
            yield ["session-a", "session-b"]

        def close(self):
            pass

    monkeypatch.setattr(row_resolver, "get_reader", FakeReader)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SESSIONS,
        id="task-large-session-stream",
        sampling_rate=100,
        filters={},
        run_type=RunType.HISTORICAL,
        spans_limit=row_resolver._EVAL_TASK_BUFFERED_ID_LIMIT + 1,
    )

    assert list(row_resolver.iter_desired_rows(task, batch_size=2)) == [
        ["session-a", "session-b"]
    ]


def test_continuous_span_resolution_keeps_single_streaming_query(monkeypatch):
    class FakeReader:
        def __init__(self):
            self.calls = 0
            self.closed = False

        def stream_query(self, sql, params, *, batch_size, settings):
            self.calls += 1
            assert "eval_slice_start" not in params
            assert "SELECT DISTINCT id" not in sql
            assert settings == row_resolver._EVAL_TASK_READ_SETTINGS
            yield ["span-new"]

        def close(self):
            self.closed = True

    reader = FakeReader()
    monkeypatch.setattr(row_resolver, "get_reader", lambda: reader)
    started_at = datetime(2026, 7, 30, 12)
    task = SimpleNamespace(
        project_id="11111111-1111-1111-1111-111111111111",
        row_type=RowType.SPANS,
        id="task-continuous",
        sampling_rate=100,
        filters={},
        run_type=RunType.CONTINUOUS,
        spans_limit=50,
        continuous_cursor=None,
        start_time=started_at,
        created_at=started_at,
    )

    assert list(row_resolver.iter_desired_rows(task)) == [["span-new"]]
    assert reader.calls == 1
    assert reader.closed is True
