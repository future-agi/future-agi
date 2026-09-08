from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog.codec import (
    MAX_DEFINITION_JSON_BYTES,
    CatalogCodecError,
    canonical_json,
    canonical_json_sha256,
    casefold_text,
    framed_sha256,
    like_contains_pattern,
    stable_property_id,
)


def test_stable_property_ids_are_namespaced_and_uuid_canonical() -> None:
    assert (
        stable_property_id(
            "system_attribute",
            "llm.model_name",
            primary_source="traces",
        )
        == "system_attribute:traces:llm.model_name"
    )
    assert (
        stable_property_id("custom_attribute", "customer.plan")
        == "custom_attribute:customer.plan"
    )
    assert (
        stable_property_id(
            "eval_config",
            "82E4BDFC-FB55-482D-A7A0-28E8755BF66A",
        )
        == "eval_config:82e4bdfc-fb55-482d-a7a0-28e8755bf66a"
    )
    assert (
        stable_property_id(
            "dataset_column",
            UUID("9ff81177-4efd-41fd-8df0-2e0d2d325a12"),
        )
        == "dataset_column:9ff81177-4efd-41fd-8df0-2e0d2d325a12"
    )


@pytest.mark.parametrize(
    ("kind", "key", "primary_source"),
    [
        ("unknown", "x", ""),
        ("system_attribute", "x", "trace:span"),
        ("custom_attribute", "x", "traces"),
        ("annotation", "not-a-uuid", ""),
        ("annotation", "00000000-0000-0000-0000-000000000000", ""),
        ("custom_attribute", "bad\nkey", ""),
    ],
)
def test_stable_property_id_rejects_ambiguous_or_invalid_components(
    kind: str,
    key: str,
    primary_source: str,
) -> None:
    with pytest.raises(CatalogCodecError):
        stable_property_id(kind, key, primary_source=primary_source)


def test_casefold_contract_does_not_normalize_unicode() -> None:
    assert casefold_text("Straße") == "strasse"
    assert casefold_text("İtem") == "i\u0307tem"
    assert casefold_text("é") != casefold_text("e\u0301")


def test_like_contains_pattern_escapes_clickhouse_wildcards() -> None:
    assert like_contains_pattern("") == "%"
    assert like_contains_pattern("a%b_c\\d") == r"%a\%b\_c\\d%"


def test_canonical_json_is_sorted_utf8_and_bounded_by_bytes() -> None:
    payload = canonical_json({"z": "東京", "a": [True, None, 3]})
    assert payload == '{"a":[true,null,3],"z":"東京"}'
    assert canonical_json_sha256(payload) == (
        "6ea2dfa51eab90a54faf1bbe0118a6b4083a24646c34f8bbd48366c2769ba0e6"
    )

    exactly_full = canonical_json({"x": "a" * (MAX_DEFINITION_JSON_BYTES - 8)})
    assert len(exactly_full.encode()) == MAX_DEFINITION_JSON_BYTES
    with pytest.raises(CatalogCodecError, match="exceeds 32768"):
        canonical_json({"x": "a" * (MAX_DEFINITION_JSON_BYTES - 7)})


@pytest.mark.parametrize(
    "payload",
    [
        {"number": math.nan},
        {"number": math.inf},
        {"bad": object()},
        {1: "non-string key"},
        {"surrogate": "\ud800"},
    ],
)
def test_canonical_json_rejects_non_json_or_invalid_unicode(payload: object) -> None:
    with pytest.raises(CatalogCodecError):
        canonical_json(payload)  # type: ignore[arg-type]


def test_framed_hash_has_no_delimiter_ambiguity() -> None:
    assert framed_sha256("catalog.test", "a|b", "c") != framed_sha256(
        "catalog.test", "a", "b|c"
    )
    assert framed_sha256("catalog.test", 1, None, False) == (
        "7dfeca3b6143ebdbf52b6dd6cd4d1addaca975b7fb2e4a3d8568df02ba0f5d32"
    )


def test_finite_floats_use_fixed_minimal_number_contract() -> None:
    payload = canonical_json(
        {
            "fraction": 0.125,
            "large": 1e20,
            "negative_zero": -0.0,
            "small": 1e-7,
        }
    )
    assert payload == (
        '{"fraction":0.125,"large":100000000000000000000,'
        '"negative_zero":0,"small":0.0000001}'
    )
    assert canonical_json_sha256(payload) == (
        "73e89e1cb1b04782a7b6a3f0ac53dca9a4d58327b5393f9b145fd86d238acc6b"
    )


def test_python_consumes_shared_go_codec_fixture() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "fi-collector/pkg/propertycatalog/testdata/codec_v1_fixtures.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["format"] == "futureagi.property-catalog-codec-fixtures"
    assert fixture["version"] == 1
    for example in fixture["canonical_json"]:
        canonical = example["canonical"]
        assert canonical_json(json.loads(canonical)) == canonical, example["name"]
        assert canonical_json_sha256(canonical) == example["sha256"]
    for example in fixture["casefold"]:
        assert casefold_text(example["source"]) == example["folded"]
