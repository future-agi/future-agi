"""Assignment-only tag semantics; no ORM, socket or service fixtures required."""

import json

import pytest

from tracer.services.entity_tags import (
    TagInputError,
    TagReadUnavailable,
    effective_tags,
    normalize_manual_tags,
    source_tag_names,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ([], []),
        (["one", "two"], [{"name": "one"}, {"name": "two"}]),
        (["Case", "case", "Case"], [{"name": "Case"}, {"name": "case"}]),
        ([" padded "], [{"name": " padded "}]),
        (
            [{"name": "rich", "color": "#Ab12EF"}],
            [{"name": "rich", "color": "#Ab12EF"}],
        ),
        (
            ["one", {"name": "one", "color": "#112233"}],
            [{"name": "one", "color": "#112233"}],
        ),
    ],
)
def test_manual_tags_preserve_exact_names_and_assignment_colors(raw, expected):
    assert normalize_manual_tags(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "tag",
        {},
        [True],
        [1],
        [None],
        [""],
        ["   "],
        [{"name": 1}],
        [{"name": "tag", "color": "red"}],
        [{"name": "tag", "color": "#fff"}],
        [{"name": "tag", "extra": 1}],
        [{"name": "tag", "color": None}],
        ["x" * 257],
        ["é" * 257],
        ["nul\x00name"],
        ["line\nbreak"],
        ["x"] * 101,
        [{"name": "x", "color": "#111111"}, {"name": "x", "color": "#222222"}],
    ],
)
def test_invalid_manual_input_is_rejected_not_silently_dropped(raw):
    with pytest.raises(TagInputError):
        normalize_manual_tags(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ()),
        ("[]", ()),
        ("not-json", ()),
        ('["one","one","Case","case"]', ("one", "Case", "case")),
        ('[{"name":"rich","color":"#112233"},"plain",null,7]', ("rich", "plain")),
        (json.dumps(json.dumps(["encoded"])), ("encoded",)),
        ([" padded ", "'quoted'", "a,b"], (" padded ", "'quoted'", "a,b")),
    ],
)
def test_legacy_and_rich_source_columns_are_normalized(raw, expected):
    assert source_tag_names({"tags": raw}) == expected


def test_dedicated_column_does_not_mask_sdk_attributes():
    row = {
        "tags": '["column","shared"]',
        "attrs_string": {"tag.tags": '["sdk","shared"]'},
        "span_attributes": '{"tag.tags":["extra","sdk"]}',
    }
    assert source_tag_names(row) == ("column", "shared", "sdk", "extra")
    row["tags"] = "[]"
    assert source_tag_names(row) == ("sdk", "shared", "extra")
    row["tags"] = "malformed"
    assert source_tag_names(row) == ("sdk", "shared", "extra")


def test_flat_and_nested_attribute_shapes_keep_all_source_names():
    assert source_tag_names(
        {
            "attrs_string": {"tag.tags": '"[\\"encoded\\"]"'},
            "attributes_extra": {"tag.tags": ["flat"], "tag": {"tags": ["nested"]}},
        }
    ) == ("encoded", "flat", "nested")


def test_absent_assignment_differs_from_explicit_empty_assignment():
    row = {"tags": '["sdk"]'}
    legacy = [{"name": "old-manual", "color": "#112233"}]
    assert effective_tags(row, manual_tags=None, legacy_tags=legacy) == {
        "tags": [{"name": "sdk"}, {"name": "old-manual", "color": "#112233"}],
        "source_tag_names": ["sdk"],
    }
    assert effective_tags(row, manual_tags=[], legacy_tags=legacy) == {
        "tags": [{"name": "sdk"}],
        "source_tag_names": ["sdk"],
    }


def test_source_owned_name_can_be_colored_but_not_hidden_by_manual_clear():
    row = {"tags": '["sdk"]'}
    assert effective_tags(row, manual_tags=[{"name": "sdk", "color": "#112233"}])[
        "tags"
    ] == [{"name": "sdk", "color": "#112233"}]
    assert effective_tags(row, manual_tags=[])["tags"] == [{"name": "sdk"}]
    row["attrs_string"] = {"tag.tags": '["later-sdk"]'}
    assert effective_tags(row, manual_tags=["ui"])["tags"] == [
        {"name": "sdk"},
        {"name": "later-sdk"},
        {"name": "ui"},
    ]


def test_malformed_legacy_items_do_not_erase_sources():
    assert effective_tags(
        {"tags": '["sdk"]'}, manual_tags=None, legacy_tags=[None, {"name": "legacy"}]
    )["tags"] == [{"name": "sdk"}, {"name": "legacy"}]


def test_oversized_source_fails_explicitly_instead_of_hiding_names():
    with pytest.raises(TagReadUnavailable):
        source_tag_names({"tags": json.dumps(["x" * (1024 * 1024)])})


@pytest.mark.parametrize("container", ["mapping", "json", "double-json"])
def test_large_non_tag_attributes_never_exhaust_the_tag_budget(container):
    # Long-context prompts, retrieval documents and images live beside tag.tags
    # in the same maps. Only the tag value itself is budgeted.
    large = "x" * (2 * 1024 * 1024)
    extra = {"input.value": large, "tag.tags": ["overflow"]}
    if container != "mapping":
        extra = json.dumps(extra)
    if container == "double-json":
        extra = json.dumps(extra)
    row = {
        "tags": "[]",
        "attrs_string": {"output.value": large, "tag.tags": '["sdk"]'},
        "attributes_extra": extra,
        "span_attributes": json.dumps({"llm.prompt": large}),
    }
    assert source_tag_names(row) == ("sdk", "overflow")


def test_oversized_tag_value_inside_an_attribute_map_stays_unavailable():
    with pytest.raises(TagReadUnavailable):
        source_tag_names(
            {"attrs_string": {"tag.tags": json.dumps(["x" * (1024 * 1024)])}}
        )


def test_manual_payload_byte_cap_is_independent_of_name_and_count_caps():
    with pytest.raises(TagInputError, match="byte limit"):
        normalize_manual_tags(["\U0001f600" * 256] * 100)


@pytest.mark.parametrize("manual", [[None], [{"name": "bad", "color": "red"}], "[]"])
def test_invalid_stored_assignment_is_unavailable_not_absent(manual):
    with pytest.raises(TagReadUnavailable, match="tag_assignment_invalid"):
        effective_tags({"tags": '["sdk"]'}, manual_tags=manual, legacy_tags=["old"])


def test_normalization_does_not_mutate_source_or_manual_input():
    row = {"tags": [{"name": "sdk", "color": "#111111"}]}
    manual = [{"name": "sdk", "color": "#222222"}, {"name": "ui"}]
    effective = effective_tags(row, manual_tags=manual)
    assert effective["tags"] == [
        {"name": "sdk", "color": "#222222"},
        {"name": "ui"},
    ]
    effective["tags"][0]["color"] = "#333333"
    effective["tags"][1]["name"] = "changed"
    assert row == {"tags": [{"name": "sdk", "color": "#111111"}]}
    assert manual == [{"name": "sdk", "color": "#222222"}, {"name": "ui"}]
