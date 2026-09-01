"""Real CH25 proofs for bounded voice paging and trace-detail replay."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver import Client
from django.test import override_settings

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse import eval_logger_table as eval_logger_table_config
from tracer.services.clickhouse.query_builders.voice_call_list import VAPI_PHONE_NUMBERS
from tracer.services.clickhouse.v2 import (
    trace_detail_reads as trace_detail_reads_module,
)
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailReadBuilder,
    TraceDetailReadUnavailable,
    read_trace_detail,
)

pytestmark = pytest.mark.integration

CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_NATIVE_PORT = int(os.environ.get("CH25_NATIVE_PORT", "19000"))


@pytest.fixture(scope="module")
def ch_client():
    client = Client(host=CH_HOST, port=CH_NATIVE_PORT, connect_timeout=3)
    try:
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"CH25 is not reachable on {CH_HOST}:{CH_NATIVE_PORT} ({exc!r})")
    return client


@pytest.fixture()
def detail_tables(ch_client, monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    spans = f"_test_voice_detail_spans_{suffix}"
    evals = f"_test_voice_detail_evals_{suffix}_v2"
    scores = f"_test_voice_detail_scores_{suffix}"
    monkeypatch.setattr(
        trace_detail_reads_module,
        "eval_logger_source",
        lambda *args, **kwargs: (evals, "is_deleted = 0"),
    )

    def test_eval_source(alias="", include_cdc_tombstone_guard=False):
        del include_cdc_tombstone_guard
        prefix = f"{alias}." if alias else ""
        return evals, f"{prefix}is_deleted = 0"

    monkeypatch.setattr(
        VoiceCallListQueryBuilderV2,
        "_EVAL_LOGGER_SOURCE",
        staticmethod(test_eval_source),
    )
    monkeypatch.setattr(
        eval_logger_table_config,
        "SUPPORTED_EVAL_LOGGER_TABLES",
        eval_logger_table_config.SUPPORTED_EVAL_LOGGER_TABLES | {evals},
    )
    ch_client.execute(
        f"""
        CREATE TABLE {spans} (
            project_id UUID,
            observation_type String,
            service_name String,
            start_time DateTime64(6, 'UTC'),
            trace_id String,
            id String,
            parent_span_id String,
            name String,
            trace_name String DEFAULT '',
            end_time Nullable(DateTime64(6, 'UTC')),
            project_version_id Nullable(UUID),
            trace_session_id Nullable(UUID),
            custom_eval_config_id Nullable(UUID),
            status String,
            status_message String,
            model String,
            provider String,
            latency_ms Int32,
            prompt_tokens Int32,
            completion_tokens Int32,
            total_tokens Int32,
            cost Float64,
            attrs_string Map(String, String),
            attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8),
            attributes_extra String,
            metadata String,
            input String,
            output String,
            tags String,
            span_events String,
            created_at DateTime64(6, 'UTC'),
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = MergeTree
        PARTITION BY toDate(start_time)
        ORDER BY (project_id, observation_type, service_name,
                  toStartOfHour(start_time), trace_id, id, _version)
        """
    )
    ch_client.execute(
        f"""
        CREATE TABLE {evals} (
            id UUID,
            trace_id Nullable(UUID),
            observation_span_id Nullable(String),
            custom_eval_config_id UUID,
            output_bool Nullable(UInt8),
            output_float Nullable(Float64),
            output_str Nullable(String),
            output_str_list String DEFAULT '[]',
            eval_explanation Nullable(String),
            error UInt8,
            error_message Nullable(String),
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = MergeTree ORDER BY (id, _version)
        """
    )
    ch_client.execute(
        f"""
        CREATE TABLE {scores} (
            id UUID,
            tracer_project_id UUID,
            trace_id Nullable(UUID),
            observation_span_id Nullable(String),
            label_id UUID,
            value String,
            deleted UInt8,
            _peerdb_is_deleted UInt8,
            _peerdb_version Int64
        ) ENGINE = MergeTree ORDER BY (id, _peerdb_version)
        """
    )
    monkeypatch.setattr(TraceDetailReadBuilder, "TABLE", spans)
    monkeypatch.setattr(TraceDetailReadBuilder, "SCORE_TABLE", scores)
    try:
        yield SimpleNamespace(spans=spans, evals=evals, scores=scores)
    finally:
        ch_client.execute(f"DROP TABLE {spans}")
        ch_client.execute(f"DROP TABLE {evals}")
        ch_client.execute(f"DROP TABLE {scores}")


class _Analytics:
    def __init__(self, client):
        self.client = client

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        # The integration table models production's typed-JSON metadata column
        # as its wire-format String representation. ``attributes_extra`` is
        # already a String in production (schema 013), so its query must run
        # unchanged to catch accidental double encoding.
        query = query.replace("toJSONString(latest_metadata)", "latest_metadata")
        rows, columns = self.client.execute(
            query,
            params,
            with_column_types=True,
            settings={
                **settings,
                "max_execution_time": max(timeout_ms / 1000, 0.1),
            },
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows]
        )


def _span_row(
    *,
    project_id,
    trace_id,
    span_id,
    started_at,
    version=1,
    deleted=0,
    parent="",
    observation_type="conversation",
    final_status="Rejected",
    input_value="",
    project_version_id=None,
    raw_log=None,
):
    return (
        project_id,
        observation_type,
        "svc",
        started_at,
        trace_id,
        span_id,
        parent,
        span_id,
        started_at + timedelta(seconds=1),
        project_version_id,
        None,
        None,
        "OK",
        "",
        "",
        "vapi",
        1000,
        1,
        2,
        3,
        0.01,
        {
            "final_status": final_status,
            **({"raw_log": raw_log} if raw_log is not None else {}),
        },
        {},
        {},
        "{}",
        "{}",
        input_value,
        "",
        "[]",
        "[]",
        started_at,
        deleted,
        version,
    )


_SPAN_COLUMNS = """
    (project_id, observation_type, service_name, start_time, trace_id, id,
     parent_span_id, name, end_time, project_version_id, trace_session_id,
     custom_eval_config_id, status, status_message, model, provider, latency_ms,
     prompt_tokens, completion_tokens, total_tokens, cost, attrs_string,
     attrs_number, attrs_bool, attributes_extra, metadata, input, output, tags,
     span_events, created_at, is_deleted, _version)
"""


def _time_filter(start, end):
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def _status_filter():
    return {
        "column_id": "final_status",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "Rejected",
        },
    }


def test_voice_page_n_and_content_use_physical_latest_identity(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000001"
    other = "00000000-0000-4000-8000-000000000002"
    start = datetime(2026, 7, 30, 10, 0)
    a_time = start.replace(microsecond=123456)
    b_time = (start + timedelta(seconds=1)).replace(microsecond=654321)
    deleted_time = start + timedelta(seconds=2)
    rows = [
        _span_row(
            project_id=project,
            trace_id="trace-a",
            span_id="shared-root",
            started_at=a_time,
            input_value="a-old",
            raw_log='{"call":"a-old"}',
        ),
        _span_row(
            project_id=project,
            trace_id="trace-a",
            span_id="shared-root",
            started_at=a_time,
            version=2,
            input_value="a-latest",
            raw_log='{"call":"a-latest"}',
        ),
        _span_row(
            project_id=project,
            trace_id="trace-b",
            span_id="shared-root",
            started_at=b_time,
            input_value="b",
            raw_log='{"call":"b"}',
        ),
        _span_row(
            project_id=project,
            trace_id="trace-deleted",
            span_id="deleted-root",
            started_at=deleted_time,
        ),
        _span_row(
            project_id=project,
            trace_id="trace-deleted",
            span_id="deleted-root",
            started_at=deleted_time,
            version=2,
            deleted=1,
        ),
        _span_row(
            project_id=project,
            trace_id="trace-miss",
            span_id="miss-root",
            started_at=start + timedelta(seconds=3),
            final_status="Approved",
        ),
        _span_row(
            project_id=other,
            trace_id="foreign",
            span_id="shared-root",
            started_at=start + timedelta(seconds=4),
        ),
    ]
    ch_client.execute(f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES", rows)

    class LocalVoiceBuilder(VoiceCallListQueryBuilderV2):
        TABLE = detail_tables.spans

    builder = LocalVoiceBuilder(
        project_id=project,
        filters=[
            _time_filter(start - timedelta(minutes=1), start + timedelta(minutes=1)),
            _status_filter(),
        ],
        page_number=1,
        page_size=1,
    )
    page = read_bounded_filter_page(
        builder=builder,
        analytics=_Analytics(ch_client),
        filters=builder.filters,
        key_field="trace_id",
        page_number=1,
        page_size=1,
        deadline_ms=5000,
    )
    assert page.complete is True
    assert [row["trace_id"] for row in page.rows] == ["trace-a"]
    selected = page.rows[0]
    query, params = builder.build_content_query(
        [selected["root_span_id"]],
        root_identities=[
            (
                project,
                selected["trace_id"],
                selected["root_span_id"],
                selected["start_time"],
            ),
            (project, "trace-b", "shared-root", b_time),
        ],
    )
    content = (
        _Analytics(ch_client)
        .execute_ch_query(
            query,
            params,
            timeout_ms=2000,
            settings={"max_threads": 1, "max_result_rows": 2},
        )
        .data
    )
    assert len(content) == 2
    assert {
        (row["trace_id"], row["span_id"], row["attrs_string"]["raw_log"])
        for row in content
    } == {
        ("trace-a", "shared-root", '{"call":"a-latest"}'),
        ("trace-b", "shared-root", '{"call":"b"}'),
    }


def test_trace_detail_replays_versions_tombstones_evals_and_annotations(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000011"
    foreign = "00000000-0000-4000-8000-000000000012"
    trace = "00000000-0000-4000-8000-000000000021"
    root_time = datetime(2026, 7, 30, 11, 0, 0, 123456)
    child_time = datetime(2026, 7, 30, 11, 0, 1, 654321)
    dead_time = datetime(2026, 7, 30, 11, 0, 2)
    recycled_old_time = datetime(2026, 7, 30, 11, 0, 3, 111111)
    recycled_live_time = datetime(2026, 7, 30, 11, 0, 4, 222222)
    project_version = "00000000-0000-4000-8000-000000000099"
    rows = [
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="root",
            started_at=root_time,
            observation_type="chain",
            input_value="old",
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="root",
            started_at=root_time,
            observation_type="chain",
            input_value="latest",
            version=2,
            project_version_id=project_version,
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="child",
            parent="root",
            started_at=child_time,
            observation_type="llm",
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="dead",
            started_at=dead_time,
            observation_type="tool",
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="dead",
            started_at=dead_time,
            observation_type="tool",
            version=2,
            deleted=1,
        ),
        _span_row(
            project_id=foreign,
            trace_id=trace,
            span_id="foreign",
            started_at=root_time,
            observation_type="chain",
        ),
        # A reused bare id is safe when the older physical identity's latest
        # state is a tombstone. Only the new physical identity may survive.
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="recycled",
            started_at=recycled_old_time,
            observation_type="tool",
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="recycled",
            started_at=recycled_old_time,
            observation_type="tool",
            version=2,
            deleted=1,
        ),
        _span_row(
            project_id=project,
            trace_id=trace,
            span_id="recycled",
            started_at=recycled_live_time,
            observation_type="tool",
            input_value="new-physical-row",
        ),
    ]
    ch_client.execute(f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES", rows)
    eval_id = str(uuid.uuid4())
    config_id = str(uuid.uuid4())
    ch_client.execute(
        f"""
        INSERT INTO {detail_tables.evals}
            (id, trace_id, observation_span_id, custom_eval_config_id,
             output_bool, output_float, output_str, eval_explanation, error,
             is_deleted, _version)
        VALUES
        """,
        [
            (eval_id, trace, "root", config_id, None, 0.2, None, "old", 0, 0, 1),
            (
                eval_id,
                trace,
                "root",
                config_id,
                None,
                0.9,
                None,
                "latest",
                0,
                0,
                2,
            ),
        ],
    )
    score_id = str(uuid.uuid4())
    label_id = str(uuid.uuid4())
    ch_client.execute(
        f"""
        INSERT INTO {detail_tables.scores}
            (id, tracer_project_id, trace_id, observation_span_id, label_id,
             value, deleted, _peerdb_is_deleted, _peerdb_version)
        VALUES
        """,
        [
            (score_id, project, trace, "child", label_id, '{"rating":1}', 0, 0, 1),
            (score_id, project, trace, "child", label_id, '{"rating":5}', 0, 0, 2),
        ],
    )

    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project],
        trace_id=trace,
        eval_config_ids_resolver=lambda selected_project: [config_id],
        deadline_ms=5000,
    )
    assert [row["id"] for row in read.spans] == ["root", "child", "recycled"]
    assert read.spans[0]["input"] == "latest"
    assert str(read.spans[0]["project_version_id"]) == project_version
    assert read.spans[2]["input"] == "new-physical-row"
    assert read.evals[0]["output_float"] == 0.9
    assert read.annotations[0]["value"] == '{"rating":5}'
    assert read.query_count == 4


def test_trace_detail_eval_rows_are_scoped_by_selected_project_configs(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000071"
    trace = "00000000-0000-4000-8000-000000000072"
    own_config = "00000000-0000-4000-8000-000000000073"
    foreign_config = "00000000-0000-4000-8000-000000000074"
    started = datetime(2026, 7, 30, 15, 0)
    ch_client.execute(
        f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="shared-span",
                started_at=started,
            )
        ],
    )
    ch_client.execute(
        f"""
        INSERT INTO {detail_tables.evals}
            (id, trace_id, observation_span_id, custom_eval_config_id,
             output_bool, output_float, output_str, eval_explanation, error,
             is_deleted, _version)
        VALUES
        """,
        [
            (
                str(uuid.uuid4()),
                trace,
                "shared-span",
                own_config,
                None,
                0.8,
                None,
                "own-project",
                0,
                0,
                1,
            ),
            (
                str(uuid.uuid4()),
                trace,
                "shared-span",
                foreign_config,
                None,
                0.1,
                None,
                "foreign-project-collision",
                0,
                0,
                1,
            ),
        ],
    )

    resolved_projects = []

    def resolve_project_configs(selected_project):
        resolved_projects.append(selected_project)
        return [own_config]

    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project],
        trace_id=trace,
        eval_config_ids_resolver=resolve_project_configs,
        deadline_ms=5000,
    )

    assert resolved_projects == [project]
    assert read.eval_config_ids == (own_config,)
    assert [row["eval_config_id"] for row in read.evals] == [own_config]
    assert read.evals[0]["eval_explanation"] == "own-project"


def test_trace_detail_without_authorized_configs_skips_eval_read(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000081"
    trace = "00000000-0000-4000-8000-000000000082"
    started = datetime(2026, 7, 30, 16, 0)
    ch_client.execute(
        f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="root",
                started_at=started,
            )
        ],
    )

    read = read_trace_detail(
        analytics=_Analytics(ch_client),
        project_ids=[project],
        trace_id=trace,
        eval_config_ids_resolver=lambda selected_project: [],
        deadline_ms=5000,
    )

    assert read.eval_config_ids == ()
    assert read.evals == ()
    # identity + content + annotations; the eval query is not issued.
    assert read.query_count == 3


def test_voice_simulator_exclusion_is_candidate_scoped_and_page_exact(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000041"
    started = datetime(2026, 7, 30, 13, 0)
    ch_client.execute(
        f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=project,
                trace_id="simulator-newest",
                span_id="sim-root",
                started_at=started + timedelta(seconds=2),
                raw_log=('{"customer":{"number":"' + VAPI_PHONE_NUMBERS[0] + '"}}'),
            ),
            _span_row(
                project_id=project,
                trace_id="real-call",
                span_id="real-root",
                started_at=started + timedelta(seconds=1),
                raw_log='{"customer":{"number":"+10000000000"}}',
            ),
        ],
    )

    class LocalVoiceBuilder(VoiceCallListQueryBuilderV2):
        TABLE = detail_tables.spans

    builder = LocalVoiceBuilder(
        project_id=project,
        filters=[
            _time_filter(started - timedelta(minutes=1), started + timedelta(minutes=1))
        ],
        page_number=0,
        page_size=1,
        remove_simulation_calls=True,
    )
    page = read_bounded_filter_page(
        builder=builder,
        analytics=_Analytics(ch_client),
        filters=builder.filters,
        key_field="trace_id",
        page_number=0,
        page_size=1,
        deadline_ms=5000,
    )

    assert page.complete is True
    assert [row["trace_id"] for row in page.rows] == ["real-call"]
    assert page.has_more is False


def test_voice_eval_hydration_replays_direct_write_versions_without_final(
    ch_client, detail_tables
):
    trace = "00000000-0000-4000-8000-000000000051"
    config = "00000000-0000-4000-8000-000000000052"
    live_eval = str(uuid.uuid4())
    deleted_eval = str(uuid.uuid4())
    ch_client.execute(
        f"""
        INSERT INTO {detail_tables.evals}
            (id, trace_id, observation_span_id, custom_eval_config_id,
             output_bool, output_float, output_str, output_str_list,
             eval_explanation, error, is_deleted, _version)
        VALUES
        """,
        [
            (live_eval, trace, "root", config, None, 0.1, None, "[]", "old", 0, 0, 1),
            (
                live_eval,
                trace,
                "root",
                config,
                None,
                0.9,
                None,
                "[]",
                "latest",
                0,
                0,
                2,
            ),
            (
                deleted_eval,
                trace,
                "root",
                config,
                1,
                None,
                None,
                "[]",
                "deleted",
                0,
                0,
                1,
            ),
            (
                deleted_eval,
                trace,
                "root",
                config,
                1,
                None,
                None,
                "[]",
                "deleted",
                0,
                1,
                2,
            ),
        ],
    )
    with override_settings(CH25_EVAL_LOGGER_TABLE=detail_tables.evals):
        builder = VoiceCallListQueryBuilderV2(
            project_id="00000000-0000-4000-8000-000000000053",
            eval_config_ids=[config],
        )
        query, params = builder.build_eval_query([trace])
    assert " FINAL" not in query
    rows = (
        _Analytics(ch_client)
        .execute_ch_query(
            query,
            params,
            timeout_ms=2000,
            settings={"max_threads": 1, "max_result_rows": 10},
        )
        .data
    )
    assert len(rows) == 1
    assert rows[0]["trace_id"] == uuid.UUID(trace)
    assert rows[0]["avg_score"] == 0.9
    assert rows[0]["eval_count"] == 1


def test_trace_detail_fails_closed_on_two_live_physical_rows_with_same_id(
    ch_client, detail_tables
):
    project = "00000000-0000-4000-8000-000000000031"
    trace = "00000000-0000-4000-8000-000000000032"
    started = datetime(2026, 7, 30, 12, 0)
    ch_client.execute(
        f"INSERT INTO {detail_tables.spans} {_SPAN_COLUMNS} VALUES",
        [
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="reused",
                started_at=started.replace(microsecond=111111),
            ),
            _span_row(
                project_id=project,
                trace_id=trace,
                span_id="reused",
                started_at=started.replace(microsecond=999999),
            ),
        ],
    )
    with pytest.raises(TraceDetailReadUnavailable, match="ambiguous_span_identity"):
        read_trace_detail(
            analytics=_Analytics(ch_client),
            project_ids=[project],
            trace_id=trace,
            deadline_ms=5000,
        )
