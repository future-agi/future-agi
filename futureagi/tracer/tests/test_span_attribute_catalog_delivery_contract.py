from __future__ import annotations

import re
from pathlib import Path

from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    extract_table_name,
    rewrite_for_replicated,
    split_statements,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = (
    REPO_ROOT
    / "futureagi/tracer/services/clickhouse/v2/schema/027_property_catalog_delivery.sql"
)


def _statements() -> list[str]:
    return split_statements(SCHEMA_PATH.read_text())


def test_delivery_schema_is_additive_and_catalog_only() -> None:
    statements = _statements()
    assert [extract_table_name(statement) for statement in statements] == [
        "property_catalog_deliveries",
        "property_catalog_source_streams",
    ]
    executable = "\n".join(statements).lower()
    assert "alter table" not in executable
    assert "drop table" not in executable
    assert "materialized view" not in executable
    assert "insert into" not in executable
    assert re.search(r"\bfrom\s+spans\b", executable) is None


def test_delivery_schema_pins_hash_chain_and_freeze_identity() -> None:
    delivery, streams = _statements()
    for field in (
        "producer_stream_id",
        "sequence",
        "envelope_format",
        "envelope_version",
        "envelope_id",
        "payload_sha256",
        "previous_payload_sha256",
        "source_batch_digest",
    ):
        assert field in delivery
    delivery_order = delivery.partition("ORDER BY")[2]
    assert "workspace_id" in delivery_order
    assert "catalog_revision" in delivery_order
    assert "build_token" in delivery_order
    assert "producer_stream_id" in delivery_order
    assert "sequence" in delivery_order
    assert "'committed' = 1" in delivery
    assert "'gap' = 2" in delivery
    assert all(f"'{mode}'" in delivery for mode in ("direct", "kafka", "reconcile"))

    for field in (
        "envelope_version",
        "first_sequence",
        "last_sequence",
        "max_contiguous_sequence",
        "last_issued_sequence",
        "fenced_sequence",
        "terminal_payload_sha256",
        "build_lease_sha256",
    ):
        assert field in streams
    stream_order = streams.partition("ORDER BY")[2]
    assert "workspace_id" in stream_order
    assert "catalog_revision" in stream_order
    assert "build_token" in stream_order
    assert "producer_stream_id" in stream_order
    assert all(
        f"'{status}'" in streams
        for status in ("open", "draining", "fenced", "complete", "gap", "failed")
    )


def test_delivery_tables_rewrite_to_observed_prod_replica_contract() -> None:
    for statement in _statements():
        table = extract_table_name(statement)
        assert table is not None
        rewritten = rewrite_for_replicated(
            statement,
            table_name=table,
            cluster="cluster",
            zk_prefix="/clickhouse/tables/ch25",
        )
        assert f"CREATE TABLE IF NOT EXISTS {table} ON CLUSTER 'cluster'" in rewritten
        expected_engine = (
            "ReplicatedMergeTree("
            if table == "property_catalog_deliveries"
            else "ReplicatedReplacingMergeTree("
        )
        assert expected_engine in rewritten
        assert f"'/clickhouse/tables/ch25/{{shard}}/{table}'" in rewritten
        assert "'{replica}'" in rewritten
        assert "PARTITION BY cityHash64(workspace_id) % 64" in rewritten
