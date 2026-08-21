"""Execute every attribute-catalog reader statement on real ClickHouse 25.3.

The DB-free reader tests validate admission and decoding behavior. This test
guards the SQL/parser boundary they cannot cover, including clickhouse-driver's
actual parameter interpolation and the dictionary row shape returned by the
production query executor. It skips only when the local/CI ClickHouse service
is unavailable.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tracer.services.clickhouse.attribute_reads import V2AttributeQueryExecutor
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements
from tracer.services.clickhouse.v2.attribute_catalog_codec import (
    encode_catalog_scalar,
)
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    _ACTIVATION_SQL,
    _CHECKPOINT_SQL,
    _KEY_PAGE_SQL,
    _VALUE_PAGE_SQL,
    CATALOG_QUERY_TIMEOUT_MS,
    CATALOG_READ_SETTINGS,
    CatalogActivationStatus,
    CatalogCheckpointStatus,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = (
    REPO_ROOT
    / "futureagi/tracer/services/clickhouse/v2/schema/025_span_attribute_catalog.sql"
)


def _clickhouse_host() -> str:
    host = os.environ.get("CH25_HOST") or os.environ.get("CH_HOST") or "localhost"
    return "localhost" if host == "clickhouse" else host


def _unix_microseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _insert_row(client: Any, table: str, row: dict[str, Any]) -> None:
    client.insert(table, [list(row.values())], column_names=list(row))


@pytest.fixture(scope="module")
def catalog_database():
    """Create one isolated catalog database and seed every reader table."""

    clickhouse_connect = pytest.importorskip("clickhouse_connect")
    host = _clickhouse_host()
    http_port = int(
        os.environ.get("CH25_HTTP_PORT") or os.environ.get("CH_HTTP_PORT") or 18123
    )
    tcp_port = int(
        os.environ.get("CH25_TCP_PORT") or os.environ.get("CH_PORT") or 19000
    )
    database = f"test_attribute_catalog_reader_{uuid.uuid4().hex}"
    admin = None
    native_client = None
    try:
        try:
            admin = clickhouse_connect.get_client(
                host=host,
                port=http_port,
                username="default",
                password="",
            )
            admin.query("SELECT 1")
        except Exception:
            pytest.skip("ClickHouse not available for attribute-catalog SQL tests")

        admin.command(f"CREATE DATABASE {database}")
        database_client = clickhouse_connect.get_client(
            host=host,
            port=http_port,
            username="default",
            password="",
            database=database,
        )
        try:
            for statement in split_statements(SCHEMA_PATH.read_text()):
                database_client.command(statement)

            project_id = uuid.uuid4()
            epoch = 7
            window_start = datetime(2026, 8, 1, tzinfo=UTC)
            window_end = window_start + timedelta(days=1)
            last_seen = window_end - timedelta(microseconds=1)
            encoded = encode_catalog_scalar("gpt-4o")

            _insert_row(
                database_client,
                "span_attribute_key_catalog",
                {
                    "project_id": project_id,
                    "attribute_key": "model",
                    "key_folded": "model",
                    "attribute_type": "string",
                    "first_seen": window_start,
                    "last_seen": last_seen,
                    "catalog_epoch": epoch,
                },
            )
            _insert_row(
                database_client,
                "span_attribute_value_catalog",
                {
                    "project_id": project_id,
                    "attribute_key": "model",
                    "attribute_type": "string",
                    "value_fingerprint": encoded.fingerprint,
                    "value_json": encoded.value_json,
                    "value_search_text": encoded.search_text,
                    "first_seen": window_start,
                    "last_seen": last_seen,
                    "catalog_epoch": epoch,
                },
            )
            _insert_row(
                database_client,
                "span_attribute_catalog_checkpoints",
                {
                    "project_id": project_id,
                    "catalog_epoch": epoch,
                    "window_start": window_start,
                    "window_end": window_end,
                    "source_version_fence": 101,
                    "cursor_observation_type": "",
                    "cursor_service_name": "",
                    "cursor_trace_id": "",
                    "cursor_span_id": "",
                    "status": CatalogCheckpointStatus.COMPLETE.value,
                    "source_rows": 1,
                    "processed_rows": 1,
                    "key_rows": 1,
                    "value_rows": 1,
                    "gap_count": 0,
                    "gap_reasons": [],
                    "run_id": uuid.uuid4(),
                    "worker_id": "test-worker",
                    "error": "",
                    "started_at": window_start,
                    "updated_at": window_end,
                    "finished_at": window_end,
                    "_version": 1,
                },
            )
            _insert_row(
                database_client,
                "span_attribute_catalog_activations",
                {
                    "project_id": project_id,
                    "catalog_epoch": epoch,
                    "handoff_start": window_start - timedelta(days=2),
                    "handoff_end": window_start - timedelta(days=1),
                    "writer_watermark": window_end,
                    "status": CatalogActivationStatus.ACTIVE.value,
                    "qualified_at": window_start,
                    "updated_at": window_end,
                    "_version": 1,
                },
            )
        finally:
            database_client.close()

        native_client = ClickHouseClient(
            host=host,
            port=tcp_port,
            user="default",
            password="",
            database=database,
        )
        if not native_client.ping():
            pytest.skip("ClickHouse native port unavailable for catalog SQL tests")
        yield SimpleNamespace(
            executor=V2AttributeQueryExecutor(native_client),
            project_id=str(project_id),
            epoch=epoch,
            window_start=window_start,
            window_end=window_end,
            encoded=encoded,
        )
    finally:
        if native_client is not None:
            native_client.close()
        if admin is not None:
            try:
                admin.command(f"DROP DATABASE IF EXISTS {database}")
            finally:
                admin.close()


def _execute(catalog_database, sql: str, params: dict):
    return catalog_database.executor.execute(
        sql,
        params,
        timeout_ms=CATALOG_QUERY_TIMEOUT_MS,
        settings={**CATALOG_READ_SETTINGS, "max_result_rows": 2},
    ).data


def test_all_catalog_reader_statements_execute_with_production_driver(
    catalog_database,
):
    project_ids = (catalog_database.project_id,)
    common = {
        "catalog_project_ids": project_ids,
        "catalog_epoch": catalog_database.epoch,
        "catalog_window_start_us": _unix_microseconds(catalog_database.window_start),
        "catalog_window_end_us": _unix_microseconds(catalog_database.window_end),
    }

    activation_rows = _execute(
        catalog_database,
        _ACTIVATION_SQL,
        {
            "catalog_project_ids": project_ids,
            "catalog_activation_limit": 2,
        },
    )
    assert len(activation_rows) == 1
    activation = activation_rows[0]
    assert set(activation) == {
        "project_id",
        "catalog_epoch",
        "handoff_start",
        "handoff_end",
        "writer_watermark",
        "status",
        "qualified_at",
        "state_version",
        "latest_state_variants",
    }
    assert activation["project_id"] == catalog_database.project_id
    assert activation["catalog_epoch"] == catalog_database.epoch
    assert activation["status"] == CatalogActivationStatus.ACTIVE.value
    assert activation["latest_state_variants"] == 1

    checkpoint_rows = _execute(
        catalog_database,
        _CHECKPOINT_SQL,
        {
            **common,
            "catalog_checkpoint_complete_status": (
                CatalogCheckpointStatus.COMPLETE.value
            ),
            "catalog_checkpoint_limit": 2,
        },
    )
    assert len(checkpoint_rows) == 1
    checkpoint = checkpoint_rows[0]
    assert set(checkpoint) == {
        "project_id",
        "checkpoint_count",
        "incomplete_count",
        "declared_gap_count",
        "row_mismatch_count",
        "missing_fence_count",
        "version_conflict_count",
        "coverage_start",
        "coverage_end",
        "checkpoint_fences",
        "interior_gap_count",
    }
    assert checkpoint["project_id"] == catalog_database.project_id
    assert checkpoint["checkpoint_count"] == 1
    assert checkpoint["incomplete_count"] == 0

    key_rows = _execute(
        catalog_database,
        _KEY_PAGE_SQL,
        {
            **common,
            "catalog_key_attribute_types": ("string",),
            "catalog_key_search_pattern": "%",
            "catalog_after_key_folded": "",
            "catalog_after_key": "",
            "catalog_after_key_type_rank": 0,
            "catalog_page_limit": 2,
        },
    )
    assert len(key_rows) == 1
    key = key_rows[0]
    assert set(key) == {
        "key_folded",
        "attribute_key",
        "attribute_type",
        "attribute_type_rank",
        "first_seen",
        "last_seen",
        "total_count",
    }
    assert key["key_folded"] == "model"
    assert key["attribute_key"] == "model"
    assert key["attribute_type"] == "string"
    assert key["attribute_type_rank"] == 1
    assert key["total_count"] == 1

    value_rows = _execute(
        catalog_database,
        _VALUE_PAGE_SQL,
        {
            **common,
            "catalog_attribute_key": "model",
            "catalog_attribute_types": ("string",),
            "catalog_value_search_pattern": "%",
            "catalog_after_value_type_rank": 0,
            "catalog_after_value_fingerprint": "",
            "catalog_page_limit": 2,
        },
    )
    assert len(value_rows) == 1
    value = value_rows[0]
    assert set(value) == {
        "attribute_type",
        "attribute_type_rank",
        "value_fingerprint",
        "value_json",
        "value_search_text",
        "value_json_variants",
        "value_search_variants",
        "first_seen",
        "last_seen",
    }
    assert value["attribute_type"] == "string"
    assert value["attribute_type_rank"] == 1
    assert value["value_fingerprint"] == catalog_database.encoded.fingerprint
    assert value["value_json"] == catalog_database.encoded.value_json
    assert value["value_search_text"] == catalog_database.encoded.search_text
    assert value["value_json_variants"] == 1
    assert value["value_search_variants"] == 1
