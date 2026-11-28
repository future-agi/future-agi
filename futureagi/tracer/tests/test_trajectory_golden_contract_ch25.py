"""Live ClickHouse 25.3 proof of the trajectory writer/reader contract.

The fixtures in ``fixtures/trajectory_contract/`` come from the real Go span
converter with deduplication on (fi-collector
``TestTrajectoryContractFixturesUpToDate``): ``spans.jsonl`` holds the span
rows exactly as the collector posts them, ``items.jsonl`` the trajectory_items
rows, and ``payloads.jsonl`` the input/output the dedup-off path stores for the
same spans. This module inserts the rows into a private database with the full
v2 schema and requires every read surface to return the dedup-off bytes.
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import override_settings

from conftest import (
    _ch_test_apply_v2_schema,
    _ch_test_native_client,
    _ch_test_owned_database,
    _open_ch_test_http_client,
)
from tracer.services.clickhouse.v2 import span_reader, trace_detail_reads
from tracer.services.clickhouse.v2.trajectory_hydration import (
    ENCODING_ITEMS_V1,
    hydrate_span_dicts,
)

pytestmark = pytest.mark.integration

CONTRACT_DIR = Path(__file__).parent / "fixtures" / "trajectory_contract"


def _jsonl(name: str) -> list[dict]:
    with (CONTRACT_DIR / name).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


PAYLOADS = _jsonl("payloads.jsonl")
PROJECT_ID = PAYLOADS[0]["project_id"]


class _NativeAnalytics:
    """The analytics executor shape trace detail reads use, on a native client."""

    def __init__(self, client):
        self._client = client

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        rows, columns = self._client.execute(
            query,
            params,
            settings={**settings, "max_execution_time": max(timeout_ms / 1000, 5.0)},
            with_column_types=True,
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows], complete=True
        )


def _ci_requires_clickhouse() -> bool:
    """These tests are the reader half of the writer/reader contract, so CI
    must run them: there a missing or unreachable ClickHouse fails the module
    instead of skipping it. Locally they still skip without a test server."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


@pytest.fixture(scope="module")
def contract_database():
    with ExitStack() as stack:
        try:
            database = stack.enter_context(
                _ch_test_owned_database("test_traj_contract_")
            )
            _ch_test_apply_v2_schema(database)
            http = _open_ch_test_http_client(database=database)
            stack.callback(http.close)
            native = stack.enter_context(_ch_test_native_client(database=database))
        except pytest.skip.Exception as exc:
            if _ci_requires_clickhouse():
                pytest.fail(
                    "the trajectory contract tests must run in CI, but the test"
                    f" ClickHouse was not available: {exc}",
                    pytrace=False,
                )
            raise
        for table, name in (
            ("trajectory_items", "items.jsonl"),
            ("spans", "spans.jsonl"),
        ):
            http.raw_insert(
                table,
                insert_block=(CONTRACT_DIR / name).read_bytes(),
                fmt="JSONEachRow",
            )
        assert native.execute("SELECT version()")[0][0].startswith("25.3.")
        yield database, http, native


def _reader(http) -> span_reader.CHSpanReader:
    reader = span_reader.CHSpanReader.__new__(span_reader.CHSpanReader)
    reader._client = http
    return reader


def test_fixture_rows_landed_with_pointer_columns(contract_database):
    _database, _http, native = contract_database
    extracted = sum(
        1 for p in PAYLOADS if p["input_extracted"] or p["output_extracted"]
    )
    rows = native.execute(
        "SELECT countIf(payload_encoding = %(enc)s), count() FROM spans FINAL",
        {"enc": ENCODING_ITEMS_V1},
    )
    assert rows == [(extracted, len(PAYLOADS))]
    assert native.execute("SELECT count() FROM trajectory_items")[0][0] == len(
        _jsonl("items.jsonl")
    )
    # Previews really are shorter than the content for at least one case, so
    # equality below proves hydration and not an inline copy.
    short = native.execute(
        "SELECT count() FROM spans FINAL WHERE payload_encoding = %(enc)s "
        "AND lengthUTF8(input) < JSONExtractUInt(payload_envelope, 'in', 'il')",
        {"enc": ENCODING_ITEMS_V1},
    )
    assert short[0][0] >= 1


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=True)
def test_golden_rows_hydrate_to_original(contract_database):
    """CHSpanReader (hydrate_chspans) returns the dedup-off bytes per span."""
    _database, http, _native = contract_database
    reader = _reader(http)
    for case in PAYLOADS:
        spans = reader.list_by_trace(case["trace_id"], project_id=PROJECT_ID)
        assert len(spans) == 1, case["case"]
        span = spans[0]
        assert span.input == case["input"], case["case"]
        assert span.output == case["output"], case["case"]
        assert span.input.encode("utf-8") == case["input"].encode("utf-8")
        assert span.output.encode("utf-8") == case["output"].encode("utf-8")


@override_settings(FI_TRAJECTORY_STORE_READ_ENABLED=True)
def test_contract_fixture_hydration_byte_exact_all_surfaces(contract_database):
    """hydrate_span_dicts and trace detail return the same bytes."""
    _database, _http, native = contract_database

    def query_dicts(sql, params):
        rows, columns = native.execute(sql, params, with_column_types=True)
        names = [name for name, _type in columns]
        return [dict(zip(names, row, strict=True)) for row in rows]

    rows = query_dicts(
        "SELECT id, project_id, input, output, "
        "input_item_hashes, output_item_hashes, tool_def_hashes, item_scope_id, "
        "payload_encoding, payload_envelope FROM spans FINAL "
        "WHERE project_id = %(pid)s",
        {"pid": PROJECT_ID},
    )
    by_id = {row["id"]: row for row in hydrate_span_dicts(rows, query_dicts)}
    assert len(by_id) == len(PAYLOADS)
    for case in PAYLOADS:
        row = by_id[case["span_id"]]
        assert row["input"] == case["input"], case["case"]
        assert row["output"] == case["output"], case["case"]

    analytics = _NativeAnalytics(native)
    for case in PAYLOADS:
        detail = trace_detail_reads.read_trace_detail(
            analytics=analytics,
            project_ids=[PROJECT_ID],
            trace_id=case["trace_id"],
            include_annotations=False,
        )
        assert len(detail.spans) == 1, case["case"]
        assert detail.spans[0]["input"] == case["input"], case["case"]
        assert detail.spans[0]["output"] == case["output"], case["case"]
