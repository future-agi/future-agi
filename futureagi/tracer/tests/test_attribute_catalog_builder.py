from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tracer.services.clickhouse.v2.attribute_catalog_builder import (
    GAP_INVALID_BOOLEAN,
    GAP_INVALID_SCALAR,
    GAP_MAX_ARRAY_MEMBERS,
    GAP_MAX_ENCODED_BYTES,
    GAP_MAX_KEYS,
    CatalogBuildLimits,
    CatalogScope,
    build_catalog_rows,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = (
    REPO_ROOT / "fi-collector/pkg/attributecatalog/testdata/builder_fixtures.json"
)


def _fixture_result():
    document = json.loads(FIXTURE_PATH.read_text(), parse_float=Decimal)
    scope = document["scope"]
    limits = document["limits"]
    attributes = document["attributes"]
    result = build_catalog_rows(
        scope=CatalogScope(
            scope["project_id"],
            datetime.fromisoformat(scope["seen_at"].replace("Z", "+00:00")),
            scope["catalog_epoch"],
        ),
        attrs_string=attributes["strings"],
        attrs_number=attributes["numbers"],
        attrs_bool=attributes["booleans"],
        attributes_extra=attributes["extra"],
        limits=CatalogBuildLimits(**limits),
    )
    return document, result


def test_python_builder_matches_shared_golden_fixture() -> None:
    document, result = _fixture_result()
    expected = document["expected"]
    assert [
        [row.attribute_key, row.key_folded, row.attribute_type]
        for row in result.key_rows
    ] == expected["keys"]
    assert [
        [
            row.attribute_key,
            row.attribute_type,
            row.value_json,
            row.value_fingerprint,
        ]
        for row in result.value_rows
    ] == expected["values"]
    assert asdict(result.metadata) == {
        **expected["metadata"],
        "gap_reasons": tuple(expected["metadata"]["gap_reasons"]),
    }
    assert all(
        row.project_id == document["scope"]["project_id"] for row in result.key_rows
    )


def test_python_builder_caps_huge_array_at_indexed_prefix() -> None:
    members: list[object] = ["tail"] * 1_000_000
    members[:3] = ["a", "a", 3]
    result = build_catalog_rows(
        scope=CatalogScope("00000000-0000-0000-0000-000000000000", datetime.min, 0),
        attrs_string={},
        attrs_number={},
        attrs_bool={},
        attributes_extra={"array": members},
        limits=CatalogBuildLimits(1, 3, 1_000),
    )
    assert result.metadata.gap_reasons == (GAP_MAX_ARRAY_MEMBERS,)
    assert result.metadata.array_members_inspected == 3
    assert result.metadata.array_members_omitted == 999_997
    assert result.metadata.duplicate_values_skipped == 1
    assert len(result.value_rows) == 2


def test_python_builder_huge_limits_do_not_preallocate() -> None:
    result = build_catalog_rows(
        scope=CatalogScope("00000000-0000-0000-0000-000000000000", datetime.min, 0),
        attrs_string={"only": "one"},
        attrs_number={},
        attrs_bool={},
        attributes_extra={},
        limits=CatalogBuildLimits(sys.maxsize, sys.maxsize, sys.maxsize),
    )
    assert result.metadata.complete
    assert len(result.value_rows) == 1


def test_python_builder_emits_declared_key_only_attributes_without_value_gap() -> None:
    result = build_catalog_rows(
        scope=CatalogScope("00000000-0000-0000-0000-000000000000", datetime.min, 0),
        attrs_string={"oversize": ""},
        attrs_number={"latency": 1.5},
        attrs_bool={},
        attributes_extra={"oversize_array": []},
        limits=CatalogBuildLimits(10, 10, 1_000),
        key_only_attributes=frozenset(
            (("oversize", "string"), ("oversize_array", "array"))
        ),
    )

    assert result.metadata.complete
    assert result.metadata.gap_reasons == ()
    assert [(row.attribute_key, row.attribute_type) for row in result.key_rows] == [
        ("latency", "number"),
        ("oversize", "string"),
        ("oversize_array", "array"),
    ]
    assert [(row.attribute_key, row.attribute_type) for row in result.value_rows] == [
        ("latency", "number")
    ]


@pytest.mark.parametrize(
    ("strings", "numbers", "booleans", "reason"),
    [
        ({"bad": 1}, {}, {}, GAP_INVALID_SCALAR),
        ({}, {"bad": True}, {}, GAP_INVALID_SCALAR),
        ({}, {"bad": "1"}, {}, GAP_INVALID_SCALAR),
        ({}, {}, {"bad": True}, GAP_INVALID_BOOLEAN),
        ({}, {}, {"bad": 2}, GAP_INVALID_BOOLEAN),
    ],
)
def test_python_builder_reports_malformed_typed_map_values(
    strings: dict[str, object],
    numbers: dict[str, object],
    booleans: dict[str, object],
    reason: str,
) -> None:
    result = build_catalog_rows(
        scope=CatalogScope("00000000-0000-0000-0000-000000000000", datetime.min, 0),
        attrs_string=strings,  # type: ignore[arg-type]
        attrs_number=numbers,  # type: ignore[arg-type]
        attrs_bool=booleans,  # type: ignore[arg-type]
        attributes_extra={},
        limits=CatalogBuildLimits(10, 10, 1_000),
    )
    assert not result.metadata.complete
    assert result.metadata.gap_reasons == (reason,)
    assert not result.value_rows


def test_python_builder_streams_attacker_sized_value_to_byte_gap() -> None:
    result = build_catalog_rows(
        scope=CatalogScope("00000000-0000-0000-0000-000000000000", datetime.min, 0),
        attrs_string={"key": "x" * 1_000_000},
        attrs_number={},
        attrs_bool={},
        attributes_extra={},
        limits=CatalogBuildLimits(1, 0, 100),
    )
    assert result.metadata.gap_reasons == (GAP_MAX_ENCODED_BYTES,)
    assert result.metadata.truncated
    assert not result.value_rows


def test_python_builder_distinguishes_invalid_utf8_from_byte_limit() -> None:
    result = build_catalog_rows(
        scope=CatalogScope("00000000-0000-0000-0000-000000000000", datetime.min, 0),
        attrs_string={"key": "bad\ud800"},
        attrs_number={},
        attrs_bool={},
        attributes_extra={},
        limits=CatalogBuildLimits(1, 0, 10_000),
    )
    assert result.metadata.gap_reasons == (GAP_INVALID_SCALAR,)
    assert not result.metadata.truncated
    assert not result.metadata.complete

    invalid_after_ceiling = build_catalog_rows(
        scope=CatalogScope("00000000-0000-0000-0000-000000000000", datetime.min, 0),
        attrs_string={"key": ("x" * 1_000) + "\ud800"},
        attrs_number={},
        attrs_bool={},
        attributes_extra={},
        limits=CatalogBuildLimits(1, 0, 100),
    )
    assert invalid_after_ceiling.metadata.gap_reasons == (GAP_INVALID_SCALAR,)
    assert not invalid_after_ceiling.metadata.truncated


def test_python_builder_reports_all_limits_in_fixed_order() -> None:
    result = build_catalog_rows(
        scope=CatalogScope("00000000-0000-0000-0000-000000000000", datetime.min, 0),
        attrs_string={"a": "too-long", "b": "omitted"},
        attrs_number={},
        attrs_bool={},
        attributes_extra={"array": [1, 2]},
        limits=CatalogBuildLimits(2, 0, 10),
    )
    assert result.metadata.gap_reasons == (
        GAP_MAX_KEYS,
        GAP_MAX_ARRAY_MEMBERS,
        GAP_MAX_ENCODED_BYTES,
    )
    assert result.metadata.truncated
    assert not result.metadata.complete
