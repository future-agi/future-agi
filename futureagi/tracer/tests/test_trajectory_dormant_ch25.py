"""Live ClickHouse 25.3 proof that the trajectory store is inert while disabled.

A private database gets every v2 schema file except ``040_trajectory_items.sql``
(the layout of a hosted or OSS install where 040 was never applied). With the
read flag off, span-list SQL that names a pointer column only as a metadata key
must still execute there. A second database gets the complete schema and checks
that ClickHouse reports the six 040 ``spans`` columns exactly as the OSS
inspector expects them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings

from conftest import (
    _ch_test_apply_v2_schema,
    _ch_test_native_client,
    _ch_test_owned_database,
)
from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse.v2 import apply_schema
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.trajectory_hydration import (
    TRAJECTORY_SPAN_COLUMNS,
)

pytestmark = pytest.mark.integration

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
SCHEMA_DIR = Path(apply_schema.__file__).parent / "schema"


def _apply_pre040_schema(database: str) -> None:
    files = [
        p.name
        for p in sorted(SCHEMA_DIR.glob("*.sql"))
        if not p.name.startswith("_") and p.name != "040_trajectory_items.sql"
    ]
    # Shared helper: proves the target is the CI sidecar before applying.
    _ch_test_apply_v2_schema(database, files=files)


def _span_columns(client, database: str) -> dict[str, tuple[str, str, str]]:
    rows = client.execute(
        "SELECT name, type, default_kind, default_expression FROM system.columns "
        "WHERE database = %(db)s AND table = 'spans'",
        {"db": database},
    )
    return {name: (typ, kind, expr) for name, typ, kind, expr in rows}


def _metadata_filter(key: str, col_type: str | None) -> list[dict]:
    config = {"filter_type": "text", "filter_op": "equals", "filter_value": "inline"}
    if col_type:
        config["col_type"] = col_type
    return [{"column_id": f"metadata.{key}", "filter_config": config}]


@pytest.fixture(scope="module")
def pre040_database():
    with _ch_test_owned_database("test_traj_pre040_") as database:
        _apply_pre040_schema(database)
        with _ch_test_native_client(database=database) as client:
            assert client.execute("SELECT version()")[0][0].startswith("25.3.")
            assert not set(TRAJECTORY_SPAN_COLUMNS) & set(
                _span_columns(client, database)
            )
            yield database, client


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=False)
@pytest.mark.parametrize("col_type", [None, "SPAN_ATTRIBUTE"], ids=["normal", "attr"])
@pytest.mark.parametrize("pointer", TRAJECTORY_SPAN_COLUMNS)
def test_metadata_filter_on_pointer_name_executes_without_040(
    pre040_database, pointer, col_type
):
    """A NORMAL filter interpolates ``metadata.<pointer>`` into the SQL, so the
    word-boundary scanner sees the bare pointer name. Before the fix it packed
    ``argMax(tuple(..., <pointer>))`` and this statement failed without 040."""
    _database, client = pre040_database
    builder = SpanListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_number=0,
        page_size=50,
        filters=_metadata_filter(pointer, col_type),
        sort_params=[],
        eval_config_ids=[],
        annotation_label_ids=[],
    )
    for method in ("build", "build_count_query", "build_id_query"):
        sql, params = getattr(builder, method)()
        # Executes on a schema without the pointer columns: an unknown
        # identifier would raise here.
        client.execute(sql, params)


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=False)
def test_packed_columns_exclude_pointers_while_disabled():
    consumer = "attrs_string['metadata.payload_encoding'] = 'x' AND item_scope_id"
    packed = SpanListQueryBuilderV2._latest_state_packed_columns(consumer)
    assert not set(packed) & set(TRAJECTORY_SPAN_COLUMNS)


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=True)
def test_packed_columns_include_named_pointers_when_enabled():
    packed = SpanListQueryBuilderV2._latest_state_packed_columns(
        "payload_encoding, input_item_hashes"
    )
    assert {"payload_encoding", "input_item_hashes"} <= set(packed)
    assert "payload_envelope" not in packed


def test_applied_040_columns_match_the_oss_inspector_contract():
    with _ch_test_owned_database("test_traj_040_") as database:
        _ch_test_apply_v2_schema(database)
        with _ch_test_native_client(database=database) as client:
            actual = _span_columns(client, database)
    for name, declared in core._TRAJECTORY_SPAN_COLUMNS.items():
        assert name in actual, name
        typ, kind, expr = actual[name]
        # The exact comparison _check_table makes between server and contract.
        assert (core._tokens(typ), kind, core._tokens(expr)) == core._column(
            core._tokens(declared)
        ), (name, actual[name])
