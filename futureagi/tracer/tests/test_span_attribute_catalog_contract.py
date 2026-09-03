from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    extract_table_name,
    rewrite_for_replicated,
    split_statements,
)
from tracer.services.clickhouse.v2.attribute_catalog_codec import encode_catalog_scalar

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATHS = (
    REPO_ROOT
    / "futureagi/tracer/services/clickhouse/v2/schema/025_property_catalog_data.sql",
    REPO_ROOT
    / "futureagi/tracer/services/clickhouse/v2/schema/026_property_catalog_state.sql",
)
FIXTURE_PATH = (
    REPO_ROOT / "fi-collector/pkg/attributecatalog/testdata/canonical_fixtures.json"
)


def _ddl_statements() -> list[str]:
    return [
        statement
        for schema_path in SCHEMA_PATHS
        for statement in split_statements(schema_path.read_text())
    ]


def test_catalog_schema_is_additive_and_independent_of_spans() -> None:
    statements = _ddl_statements()
    assert [extract_table_name(stmt) for stmt in statements] == [
        "property_definition_catalog",
        "span_attribute_value_catalog",
        "property_catalog_checkpoints",
        "property_catalog_activations",
    ]
    executable = "\n".join(statements).lower()
    assert "alter table" not in executable
    assert "materialized view" not in executable
    assert re.search(r"\bfrom\s+spans\b", executable) is None
    assert "occurrence" not in executable
    assert re.search(r"\bfinal\b", executable) is None
    assert "span_attribute_key_catalog" not in executable


def test_catalog_schema_pins_scale_and_identity_invariants() -> None:
    statements = _ddl_statements()
    assert len(statements) == 4
    assert sum("ENGINE = MergeTree" in stmt for stmt in statements) == 1
    assert sum("ENGINE = AggregatingMergeTree" in stmt for stmt in statements) == 1
    assert (
        sum("ENGINE = ReplacingMergeTree(_version)" in stmt for stmt in statements) == 2
    )
    assert all(
        "PARTITION BY cityHash64(workspace_id) % 64" in stmt for stmt in statements
    )
    assert all(
        "PARTITION BY (cityHash64(workspace_id) % 64, catalog_epoch)" not in stmt
        for stmt in statements
    )
    assert all("catalog_epoch" in stmt.partition("ORDER BY")[2] for stmt in statements)
    assert "binding_id            FixedString(64)" in statements[0]
    assert "role                  Enum8" in statements[0]
    assert "value_fingerprint FixedString(64)" in statements[1]
    assert "SimpleAggregateFunction(anyLast, String)" in statements[1]
    assert "ngrambf_v1" in statements[0]
    assert "ngrambf_v1" in statements[1]

    for statement in statements:
        table = extract_table_name(statement)
        rewritten = rewrite_for_replicated(
            statement,
            table_name=table,
            cluster="cluster",
            zk_prefix="/clickhouse/tables/ch25",
        )
        assert "Replicated" in rewritten
        assert "ON CLUSTER 'cluster'" in rewritten
        assert f"'/clickhouse/tables/ch25/{{shard}}/{table}'" in rewritten
        assert "'{replica}'" in rewritten


def test_catalog_checkpoint_contract_is_restartable_and_gap_explicit() -> None:
    checkpoint = _ddl_statements()[2]
    assert re.search(r"\bsource_version_fence\s+UInt64\b", checkpoint)
    assert re.search(r"\bsource_cursor\s+String\b", checkpoint)
    assert re.search(r"\bwatermark\s+String\b", checkpoint)
    assert all(
        re.search(rf"\b{column}\s+UInt64\b", checkpoint)
        for column in (
            "source_rows",
            "processed_rows",
            "definition_rows",
            "value_rows",
            "gap_count",
            "poison_count",
            "conflict_count",
        )
    )
    assert re.search(r"\bgap_reasons\s+Array\(String\)", checkpoint)
    assert re.search(r"\brun_id\s+UUID\b", checkpoint)
    assert re.search(r"\bworker_id\s+String\b", checkpoint)
    assert re.search(r"\berror\s+String\b", checkpoint)
    assert re.search(r"\bstarted_at\s+DateTime64\(6, 'UTC'\)", checkpoint)
    assert re.search(r"\bupdated_at\s+DateTime64\(6, 'UTC'\)", checkpoint)
    assert re.search(
        r"\bfinished_at\s+Nullable\(DateTime64\(6, 'UTC'\)\)", checkpoint
    )
    assert re.search(r"\b_version\s+UInt64\b", checkpoint)
    assert all(
        f"'{status}'" in checkpoint
        for status in (
            "pending",
            "running",
            "complete",
            "gap",
            "failed",
        )
    )
    assert "workspace_id," in checkpoint.partition("ORDER BY")[2]
    assert "producer_stream_id" in checkpoint.partition("ORDER BY")[2]


def test_catalog_activation_contract_is_one_state_per_workspace_revision() -> None:
    activation = _ddl_statements()[3]
    assert re.search(r"\bcatalog_epoch\s+UInt16\b", activation)
    assert re.search(r"\bqualified_at\s+Nullable\(DateTime64\(6, 'UTC'\)\)", activation)
    assert re.search(r"\bupdated_at\s+DateTime64\(6, 'UTC'\)", activation)
    assert re.search(r"\blifecycle_mode\s+Enum8", activation)
    assert re.search(r"\blineage_anchor_revision\s+UInt64\b", activation)
    assert re.search(r"\bactivation_sequence\s+UInt64\b", activation)
    assert all(
        f"'{status}'" in activation for status in ("building", "active", "disabled")
    )
    assert re.search(r"\b_version\s+UInt64\b", activation)
    order = activation.partition("ORDER BY")[2]
    assert "organization_id" in order
    assert "workspace_id" in order
    assert "catalog_revision" in order
    assert "build_token" in order


def test_python_codec_matches_shared_golden_fixtures() -> None:
    document = json.loads(FIXTURE_PATH.read_text(), parse_float=Decimal)
    for fixture in document["fixtures"]:
        encoded = encode_catalog_scalar(fixture["value"])
        assert encoded.kind == fixture["kind"], fixture["name"]
        assert encoded.value_json == fixture["value_json"], fixture["name"]
        assert encoded.search_text == fixture["search_text"], fixture["name"]
        assert encoded.fingerprint == fixture["fingerprint"], fixture["name"]
        assert re.fullmatch(r"[0-9a-f]{64}", encoded.fingerprint)


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        float("nan"),
        float("inf"),
        Decimal("1e5000"),
        Decimal("1e-5000"),
    ],
)
def test_python_codec_rejects_non_selectable_or_non_finite_values(
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        encode_catalog_scalar(value)
