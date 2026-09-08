from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from clickhouse_driver import Client
from django.test import override_settings

from tracer.services.clickhouse import eval_logger_table as eval_logger_table_config
from tracer.services.clickhouse import query_service as query_service_config
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.query_service import AnalyticsQueryService

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ch_client():
    host = os.environ.get("CH25_HOST", "127.0.0.1")
    port = int(os.environ.get("CH25_NATIVE_PORT", "19000"))
    client = Client(host=host, port=port, connect_timeout=3)
    try:
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"CH25 unavailable on {host}:{port}: {exc!r}")
    try:
        yield client
    finally:
        client.disconnect_connection()


@pytest.fixture()
def eval_table(ch_client, monkeypatch):
    table = f"_test_eval_status_{uuid.uuid4().hex[:10]}_v2"
    monkeypatch.setattr(
        eval_logger_table_config,
        "SUPPORTED_EVAL_LOGGER_TABLES",
        eval_logger_table_config.SUPPORTED_EVAL_LOGGER_TABLES | {table},
    )
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            id UUID,
            trace_id Nullable(UUID),
            observation_span_id Nullable(String),
            custom_eval_config_id UUID,
            target_type LowCardinality(String),
            output_bool Nullable(UInt8),
            output_float Nullable(Float64),
            output_str_list String,
            output_str Nullable(String),
            eval_explanation Nullable(String),
            error UInt8,
            error_message Nullable(String),
            output_metadata String,
            status LowCardinality(String),
            skipped_reason Nullable(String),
            created_at DateTime64(6, 'UTC'),
            updated_at DateTime64(6, 'UTC'),
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = MergeTree
        ORDER BY (custom_eval_config_id, created_at, id)
        """
    )
    try:
        yield table
    finally:
        ch_client.execute(f"DROP TABLE IF EXISTS {table}")


@pytest.fixture()
def spans_table(ch_client, monkeypatch):
    table = f"_test_eval_detail_spans_{uuid.uuid4().hex[:10]}"
    ch_client.execute(
        f"""
        CREATE TABLE {table} (
            project_id UUID,
            trace_id String,
            id String,
            is_deleted UInt8,
            _version UInt64
        ) ENGINE = MergeTree
        ORDER BY (project_id, trace_id, id, _version)
        """
    )
    monkeypatch.setattr(query_service_config, "_SPANS_TABLE", table)
    try:
        yield table
    finally:
        ch_client.execute(f"DROP TABLE IF EXISTS {table}")


def _service() -> AnalyticsQueryService:
    service = AnalyticsQueryService()
    service._ch_client = ClickHouseClient(
        host=os.environ.get("CH25_HOST", "127.0.0.1"),
        port=int(os.environ.get("CH25_NATIVE_PORT", "19000")),
        database="default",
    )
    return service


def test_eval_status_result_and_detail_use_latest_live_candidate_on_ch25(
    ch_client,
    eval_table,
    spans_table,
):
    trace_id = uuid.uuid4()
    foreign_trace_id = uuid.uuid4()
    collision_trace_id = uuid.uuid4()
    project_id = uuid.uuid4()
    foreign_project_id = uuid.uuid4()
    config_id = uuid.uuid4()
    live_id = uuid.uuid4()
    tombstoned_id = uuid.uuid4()
    now = datetime.now(UTC).replace(microsecond=0)
    rows = [
        # One row updated in-place from pending to completed. Only v2 counts.
        (
            live_id,
            trace_id,
            "span-live",
            config_id,
            "span",
            None,
            None,
            "[]",
            None,
            None,
            0,
            None,
            "{}",
            "pending",
            None,
            now - timedelta(minutes=5),
            now - timedelta(minutes=5),
            0,
            1,
        ),
        (
            live_id,
            trace_id,
            "span-live",
            config_id,
            "span",
            1,
            None,
            "[]",
            None,
            "passed",
            0,
            None,
            '{"input_data":"ok"}',
            "completed",
            None,
            now - timedelta(minutes=5),
            now - timedelta(minutes=1),
            0,
            2,
        ),
        # Newer foreign-project row with the same external span/config IDs.
        # The authorized project+trace anchor must keep it invisible.
        (
            uuid.uuid4(),
            foreign_trace_id,
            "span-live",
            config_id,
            "span",
            0,
            None,
            "[]",
            None,
            "foreign",
            0,
            None,
            "{}",
            "completed",
            None,
            now - timedelta(minutes=3),
            now,
            0,
            1,
        ),
        # Newest version is a tombstone. An inner live filter would resurrect v1.
        (
            tombstoned_id,
            trace_id,
            "span-deleted",
            config_id,
            "span",
            0,
            None,
            "[]",
            None,
            "old",
            0,
            None,
            "{}",
            "completed",
            None,
            now - timedelta(minutes=4),
            now - timedelta(minutes=4),
            0,
            1,
        ),
        (
            tombstoned_id,
            trace_id,
            "span-deleted",
            config_id,
            "span",
            0,
            None,
            "[]",
            None,
            "old",
            0,
            None,
            "{}",
            "completed",
            None,
            now - timedelta(minutes=4),
            now,
            1,
            2,
        ),
    ]
    ch_client.execute(f"INSERT INTO {eval_table} VALUES", rows)
    ch_client.execute(
        f"INSERT INTO {spans_table} VALUES",
        [
            (project_id, str(trace_id), "span-live", 0, 1),
            # Same span id in another project is a valid global collision and
            # must not make the authorized project's identity ambiguous.
            (foreign_project_id, str(foreign_trace_id), "span-live", 0, 1),
            # Latest version tombstones this requested span.
            (project_id, str(trace_id), "span-deleted", 0, 1),
            (project_id, str(trace_id), "span-deleted", 1, 2),
            # Two live traces with one external span id inside one project are
            # ambiguous; fail closed rather than selecting either eval row.
            (project_id, str(trace_id), "span-collision", 0, 1),
            (project_id, str(collision_trace_id), "span-collision", 0, 1),
        ],
    )

    with override_settings(CH25_EVAL_LOGGER_TABLE=eval_table):
        service = _service()
        try:
            children = service.get_children_eval_metrics_ch(
                ["span-live", "span-deleted"]
            )
            detail = service.get_eval_detail_ch(
                "span-live", str(config_id), project_id=str(project_id)
            )
            foreign_project_detail = service.get_eval_detail_ch(
                "span-live", str(config_id), project_id=str(foreign_project_id)
            )
            deleted_detail = service.get_eval_detail_ch(
                "span-deleted", str(config_id), project_id=str(project_id)
            )
            collision_detail = service.get_eval_detail_ch(
                "span-collision", str(config_id), project_id=str(project_id)
            )
            scores = service.get_trace_eval_scores_ch([str(trace_id)], [str(config_id)])
        finally:
            service.ch_client.close()

    child_span_ids = [row["span_id"] for row in children]
    assert child_span_ids.count("span-live") == 2
    assert "span-deleted" not in child_span_ids
    assert detail is not None
    assert detail["output_bool"] == 1
    assert detail["eval_explanation"] == "passed"
    assert foreign_project_detail is not None
    assert foreign_project_detail["output_bool"] == 0
    assert foreign_project_detail["eval_explanation"] == "foreign"
    assert deleted_detail is None
    assert collision_detail is None
    assert len(scores) == 1
    assert scores[0]["bool_count"] == 1
    assert scores[0]["bool_score"] == 100.0
    assert scores[0]["pending_count"] == 0
