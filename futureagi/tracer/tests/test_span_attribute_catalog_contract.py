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
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    CatalogActivationStatus,
    CatalogCheckpointStatus,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = (
    REPO_ROOT
    / "futureagi/tracer/services/clickhouse/v2/schema/025_span_attribute_catalog.sql"
)
FIXTURE_PATH = (
    REPO_ROOT / "fi-collector/pkg/attributecatalog/testdata/canonical_fixtures.json"
)


def _ddl_statements() -> list[str]:
    return split_statements(SCHEMA_PATH.read_text())


def test_catalog_schema_is_additive_and_independent_of_spans() -> None:
    statements = _ddl_statements()
    assert [extract_table_name(stmt) for stmt in statements] == [
        "span_attribute_key_catalog",
        "span_attribute_value_catalog",
        "span_attribute_catalog_checkpoints",
        "span_attribute_catalog_activations",
    ]
    executable = "\n".join(statements).lower()
    assert "alter table" not in executable
    assert "materialized view" not in executable
    assert re.search(r"\bfrom\s+spans\b", executable) is None
    assert "occurrence" not in executable
    assert re.search(r"\bcount\w*\s+", executable) is None
    assert re.search(r"\bfinal\b", executable) is None


def test_catalog_schema_pins_scale_and_identity_invariants() -> None:
    statements = _ddl_statements()
    assert len(statements) == 4
    assert sum("ENGINE = AggregatingMergeTree" in stmt for stmt in statements) == 2
    assert (
        sum("ENGINE = ReplacingMergeTree(_version)" in stmt for stmt in statements) == 2
    )
    assert all(
        "PARTITION BY cityHash64(project_id) % 64" in stmt for stmt in statements
    )
    assert all(
        "PARTITION BY (cityHash64(project_id) % 64, catalog_epoch)" not in stmt
        for stmt in statements
    )
    assert all(
        "catalog_epoch" in stmt.partition("ORDER BY")[2] for stmt in statements[:3]
    )
    assert "ORDER BY (project_id)" in statements[3]
    assert "value_fingerprint FixedString(64)" in statements[1]
    assert "SimpleAggregateFunction(anyLast, String)" in statements[1]
    # The conservative Unicode-parity branch is OR-ed with each ASCII LIKE
    # predicate, which prevents these indexes from excluding any granules.
    # Add a search index only with a later folded-storage contract that makes
    # the complete predicate indexable.
    assert all("ngrambf_v1" not in statement for statement in statements)

    for statement in statements:
        table = extract_table_name(statement)
        rewritten = rewrite_for_replicated(
            statement,
            table_name=table,
            cluster="default",
            zk_prefix="/clickhouse/tables",
        )
        assert "Replicated" in rewritten
        assert "ON CLUSTER 'default'" in rewritten


def test_catalog_checkpoint_contract_is_restartable_and_gap_explicit() -> None:
    checkpoint = _ddl_statements()[2]
    assert "source_version_fence       UInt64" in checkpoint
    for column in (
        "cursor_observation_type",
        "cursor_service_name",
        "cursor_trace_id",
        "cursor_span_id",
    ):
        assert re.search(rf"\b{column}\s+String\b", checkpoint)
    assert all(
        re.search(rf"\b{column}\s+UInt64\b", checkpoint)
        for column in (
            "source_rows",
            "processed_rows",
            "key_rows",
            "value_rows",
            "gap_count",
        )
    )
    assert "gap_reasons                Array(String)" in checkpoint
    assert re.search(r"\brun_id\s+UUID\b", checkpoint)
    assert re.search(r"\bworker_id\s+String\b", checkpoint)
    assert re.search(r"\berror\s+String\b", checkpoint)
    assert re.search(r"\bstarted_at\s+DateTime64\(6, 'UTC'\)", checkpoint)
    assert re.search(r"\bupdated_at\s+DateTime64\(6, 'UTC'\)", checkpoint)
    assert "finished_at                Nullable(DateTime64(6, 'UTC'))" in checkpoint
    assert re.search(r"\b_version\s+UInt64\b", checkpoint)
    assert all(f"'{status.value}'" in checkpoint for status in CatalogCheckpointStatus)
    assert (
        "ORDER BY (project_id, catalog_epoch, window_start, window_end)" in checkpoint
    )


def test_catalog_activation_contract_is_one_state_per_project() -> None:
    activation = _ddl_statements()[3]
    assert re.search(r"\bcatalog_epoch\s+UInt16\b", activation)
    for column in (
        "handoff_start",
        "handoff_end",
        "writer_watermark",
        "qualified_at",
        "updated_at",
    ):
        assert re.search(rf"\b{column}\s+DateTime64\(6, 'UTC'\)", activation)
    assert all(f"'{status.value}'" in activation for status in CatalogActivationStatus)
    assert re.search(r"\b_version\s+UInt64\b", activation)
    assert "ORDER BY (project_id)" in activation


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
