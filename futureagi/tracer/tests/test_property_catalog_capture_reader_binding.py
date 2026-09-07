"""The actual scanner and native adapter share one exact capture table."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    NativeSourceClient,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    SharedCatalogDeadline,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import (
    CanonicalSpanSourceReader,
)

DATABASE = "property_catalog_capture_0123456789abcdef01234567"
TABLE = "spans_0123456789abcdef0123456789abcdef"
PROJECT = "7db1b1f1-e13f-4810-a843-030092b61990"


def setup():
    calls = []

    def read(sql, params, **options):
        calls.append((sql, params, options))
        assert f"`{DATABASE}`.`{TABLE}`" in sql
        assert options["settings"]["readonly"] == 2
        if "AS audit_generation" in sql:
            row = {"audit_generation": 1}
        elif "AS occupied_hours" in sql:
            return [], [("project_id_text", "String"), ("occupied_hours", "Array")], 0
        else:
            row = {"source_count": 0, "state_conflict_count": 0}
            row.update(
                {f"audit_h{i}_{op}": 0 for i in range(1, 5) for op in ("xor", "sum")}
            )
        return [tuple(row.values())], [(k, "UInt64") for k in row], 0

    driver = SimpleNamespace(
        database=DATABASE, server_enforced_readonly=True, execute_read=read
    )
    client = NativeSourceClient(
        driver,
        source_database=DATABASE,
        source_table=TABLE,
        catalog_database="property_catalog_dev",
    )
    reader = CanonicalSpanSourceReader(
        client,
        source_database=DATABASE,
        source_table=TABLE,
        catalog_database="property_catalog_dev",
        deadline=SharedCatalogDeadline(wall_ms=10_000),
    )
    return client, reader, calls


def test_freeze_values_and_independent_audit_bind_the_same_capture():
    _, reader, calls = setup()
    start = datetime(2025, 1, 1, tzinfo=UTC)
    frozen = reader.freeze(
        project_ids=[PROJECT], since=start, until=start + timedelta(days=1)
    )
    assert reader.read_page(frozen, cursor=None).terminal
    proofs = reader.audit_components(frozen)
    assert len(proofs) == 1
    assert proofs[0].proof.count == 0
    assert len(calls) >= 3
    assert all("`spans`" not in sql for sql, _, _ in calls)


@pytest.mark.parametrize("table", ["spans", "foreign_capture", TABLE + "_other"])
def test_native_client_cannot_escape_to_source_or_another_capture(table):
    client, _, calls = setup()
    with pytest.raises(RuntimeError, match="exact bound source table"):
        client.query(
            f"SELECT * FROM `{DATABASE}`.`{table}`", {}, timeout_ms=1000, settings={}
        )
    assert not calls


def test_client_cannot_change_table_after_binding():
    client, reader, calls = setup()
    client.source_table = "spans"
    start = datetime(2025, 1, 1, tzinfo=UTC)
    with pytest.raises(RuntimeError, match="table identity changed"):
        reader.freeze(
            project_ids=[PROJECT], since=start, until=start + timedelta(days=1)
        )
    assert not calls


@pytest.mark.parametrize(
    "table", ["spans; DROP TABLE source", "other.table", "`spans`", "", None]
)
def test_unsafe_table_names_never_reach_native(table):
    client, _, calls = setup()
    with pytest.raises(ValueError):
        CanonicalSpanSourceReader(
            client,
            source_database=DATABASE,
            source_table=table,
            catalog_database="property_catalog_dev",
            deadline=SharedCatalogDeadline(wall_ms=10_000),
        )
    assert not calls
