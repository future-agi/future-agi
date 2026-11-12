"""Versioned adapter inserts without a ClickHouse server."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2 import trace_writer
from tracer.services.clickhouse.v2.adapter import (
    CH_INSERT_COLUMNS,
    EXPECTED_PG_COLUMNS,
    adapt,
    row_to_tuple,
)


@pytest.fixture
def writer(monkeypatch):
    inserts = []

    class Client:
        def insert(self, table, rows, column_names):
            assert table == "spans"
            inserts.extend(dict(zip(column_names, row, strict=True)) for row in rows)

    monkeypatch.setenv("CH25_DROP_LEGACY_CDC_CHAIN", "true")
    monkeypatch.delenv("CH25_SPAN_DUAL_WRITE", raising=False)
    monkeypatch.setattr(trace_writer, "_get_client", Client)

    def load_writer():
        # Import in the test body, not during collection/setup: the base's
        # missing producer is an explicit test failure, not a setup error.
        from tracer.services.clickhouse.v2 import span_writer

        return span_writer, inserts

    return load_writer


def _row(**overrides):
    start = datetime(2026, 11, 9, 12, tzinfo=UTC)
    return {
        "id": "generation-writer",
        "trace_id": "trace-writer",
        "project_id": "00000000-0000-4000-8000-000000000001",
        "observation_type": "llm",
        "name": "answer",
        "start_time": start,
        "created_at": start,
        "updated_at": start + timedelta(seconds=2),
        "span_attributes": {"llm.model_name": "gpt-4o", "streaming": True},
        "input": {"question": "hello"},
        "output": "hi",
        **overrides,
    }


def test_writer_reuses_adapter_and_sets_explicit_version(writer):
    module, inserts = writer()
    pg = _row()
    module.mirror_spans_to_clickhouse([pg])
    assert len(inserts) == 1
    expected = dict(zip(CH_INSERT_COLUMNS, row_to_tuple(adapt(pg)), strict=True))
    assert {key: inserts[0][key] for key in CH_INSERT_COLUMNS} == expected
    assert inserts[0]["_version"] == 1794225602000000000


def test_snapshot_detaches_mutable_model_values(writer):
    module, _ = writer()
    span = SimpleNamespace(**{**dict.fromkeys(EXPECTED_PG_COLUMNS), **_row()})
    captured = module.span_to_pg_row(span, None)
    span.input["question"] = "changed after save"
    span.span_attributes["streaming"] = False
    assert captured["input"] == {"question": "hello"}
    assert captured["span_attributes"]["streaming"] is True


def test_saved_revision_wins_over_callback_order(writer):
    module, inserts = writer()
    old = _row()
    new = _row(output="new", updated_at=old["updated_at"] + timedelta(seconds=1))
    module.mirror_spans_to_clickhouse([new])
    module.mirror_spans_to_clickhouse([old])
    assert inserts[0]["_version"] > inserts[1]["_version"]
    assert max(inserts, key=lambda row: row["_version"])["output"] == "new"
    module.mirror_spans_to_clickhouse([new])
    assert inserts[0] == inserts[2]


def test_changed_physical_key_emits_tombstone(writer):
    module, inserts = writer()
    old = _row()
    new = _row(
        start_time=old["start_time"] - timedelta(hours=1),
        updated_at=old["updated_at"] + timedelta(seconds=1),
    )
    module.mirror_spans_to_clickhouse([new], previous_spans=[old])
    assert len(inserts) == 2
    tombstone = next(row for row in inserts if row["is_deleted"] == 1)
    current = next(row for row in inserts if row["is_deleted"] == 0)
    assert tombstone["start_time"] == old["start_time"]
    assert current["start_time"] == new["start_time"]
    assert tombstone["_version"] == current["_version"]


def test_unchanged_hour_does_not_emit_tombstone(writer):
    module, inserts = writer()
    old = _row()
    new = _row(start_time=old["start_time"] + timedelta(minutes=1))
    module.mirror_spans_to_clickhouse([new], previous_spans=[old])
    assert len(inserts) == 1 and inserts[0]["is_deleted"] == 0


def test_writer_retains_soft_delete_marker(writer):
    module, inserts = writer()
    module.mirror_spans_to_clickhouse([_row(deleted=True)])
    assert inserts[0]["is_deleted"] == 1


def test_span_flag_is_independent_of_trace_override(writer, monkeypatch):
    module, inserts = writer()
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")
    module.mirror_spans_to_clickhouse([_row()])
    assert len(inserts) == 1
