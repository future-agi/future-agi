"""Offline Users seed/matcher contracts; synthetic typed rows, no SQL execution."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services.users_list_manager import UsersListManager
from tracer.tests.test_user_attribute_native_key_collision import PROJECT, UID, wire
from tracer.tests.test_user_latest_window_replay import builder

pytestmark = pytest.mark.unit


def leaf(key, value=None, *, op="equals", types=None, **kwargs):
    item = wire(key, value, **kwargs)
    item["filter_config"]["filter_op"] = op
    if types is not None:
        item["filter_config"]["attribute_value_types"] = types
    return item


def witness(items):
    query = builder()
    query.filters = deepcopy(items)
    return query._positive_scalar_user_witness()


def collected(items, stored):
    manager = UsersListManager(
        organization_id=PROJECT,
        allowed_project_ids=[PROJECT],
        requested_columns=[],
        filters=deepcopy(items),
    )
    row = {"end_user_id": UID, "user_id": "display", "bool_eval_pass_rate": 1}
    data = [
        {
            "end_user_id": UID,
            "attribute_key": key,
            "attribute_typed_values": [
                (kind, json.dumps(value)) for kind, value in values
            ],
        }
        for key, values in stored.items()
    ]
    # Exercise the real collector and provenance cache; intercept the service
    # before any query can reach a database. Relation membership is preclassified.
    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService") as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=data)
        attrs = manager._read_span_attributes([row], None)
    manager._apply_span_attributes([row], attrs)
    manager._relation_matching_user_ids = {UID}
    return manager, row


@pytest.mark.parametrize(
    "selected,kind",
    [(7, "number"), (True, "boolean"), ("7", "number"), ("true", "boolean")],
)
def test_mixed_picker_cannot_drop_non_string_matches(selected, kind):
    item = leaf("picker", ["Alpha", selected], op="in", types=["string", kind])
    assert witness([item]) == ("", {})
    actual = 7 if kind == "number" else True
    for stored, expected in [
        ([("string", "ALPHA")], True),
        ([(kind, actual)], True),
        ([("string", str(selected).lower())], False),
        ([], False),
    ]:
        manager, row = collected([item], {"picker": stored})
        assert manager._row_matches_filters(row) is expected
        assert (
            manager._attribute_value_types_by_user.get(UID, {}).get("picker", {})
            or not stored
        )
        anchor = leaf("tag", "Alpha")
        manager, row = collected(
            [item, anchor], {"picker": stored, "tag": [("string", "ALPHA")]}
        )
        assert witness([item, anchor]) == witness([anchor])
        assert manager._row_matches_filters(row) is expected


COMPANIONS = {
    "number": leaf("amount", 7, kind="number"),
    "boolean": leaf("enabled", True, kind="boolean"),
    "negative": leaf("state", "blocked", op="not_equals"),
    "missing": leaf("absent", op="is_null"),
    "json": leaf("payload", {"flag": True}, kind="map", op="contains"),
    "annotation": leaf("tag", "approved", source="ANNOTATION"),
    "eval": leaf("tag", "passed", source="EVAL_METRIC"),
    "native": leaf("user_id", "display", source="SYSTEM_METRIC"),
    "mixed": leaf(
        "picker", ["other", 7, True], op="in", types=["string", "number", "boolean"]
    ),
}
STORED = {
    "tag": [("string", "ALPHA"), ("number", 999)],
    "amount": [("number", 7)],
    "enabled": [("boolean", True)],
    "state": [("string", "allowed")],
    "payload": [("json", {"flag": True})],
    "picker": [("number", 7)],
    "user_id": [("string", "raw-different")],
}


@pytest.mark.parametrize("op", ["equals", "in"])
@pytest.mark.parametrize("width", [2, 5, 10])
@pytest.mark.parametrize("family", list(COMPANIONS))
def test_cross_property_and_membership_keeps_necessary_seed_superset(op, width, family):
    names = [family, *(name for name in COMPANIONS if name != family)]
    anchor = leaf("tag", "Alpha" if op == "equals" else ["Alpha", "Beta"], op=op)
    items = [anchor, *(COMPANIONS[name] for name in names[: width - 1])]
    original = deepcopy(items)
    for ordered in (items, list(reversed(items))):
        seed = witness(ordered)
        assert seed[0]
        selected = next(item for item in ordered if witness([item]) == seed)
        assert selected["filter_config"]["col_type"] == "SPAN_ATTRIBUTE"
        assert selected["filter_config"]["filter_op"] in {"equals", "in"}
        manager, row = collected(ordered, STORED)
        assert manager._row_matches_filters(row)
        assert manager._attribute_value_matches(
            row=row, key=selected["column_id"], config=selected["filter_config"]
        )
        # Every AND leaf is required, even though acquisition may use just one.
        for item in ordered:
            attrs = deepcopy(manager._attribute_values_by_user)
            key, cfg = item["column_id"], item["filter_config"]
            if cfg["col_type"] in {"ANNOTATION", "EVAL_METRIC"}:
                manager._relation_matching_user_ids.clear()
            elif cfg["col_type"] == "SYSTEM_METRIC":
                row["user_id"] = "wrong"
            elif cfg["filter_op"] == "is_null":
                manager._attribute_values_by_user[UID][key] = "present"
            else:
                manager._attribute_values_by_user[UID].pop(key, None)
            assert not manager._row_matches_filters(row), (family, width, item)
            manager._attribute_values_by_user = attrs
            manager._relation_matching_user_ids = {UID}
            row["user_id"] = "display"
    assert items == original


@pytest.mark.parametrize(
    "key,source,value",
    [
        ("tag", "ANNOTATION", "approved"),
        ("tag", "EVAL_METRIC", "passed"),
        ("user_id", "SYSTEM_METRIC", "display"),
        ("eval_score", "SYSTEM_METRIC", "1"),
    ],
)
@pytest.mark.parametrize("op", ["equals", "in"])
def test_relation_and_native_matches_do_not_require_raw_text(key, source, value, op):
    item = leaf(key, [value] if op == "in" else value, source=source, op=op)
    manager, row = collected([item], {})
    assert manager._row_matches_filters(row)
    assert witness([item]) == ("", {})
    anchor = leaf("tag", "Alpha")
    assert witness([item, anchor]) == witness([anchor])


@pytest.mark.parametrize("op", ["equals", "in"])
def test_raw_eval_score_attribute_keeps_its_own_witness(op):
    item = leaf("eval_score", ["Alpha"] if op == "in" else "Alpha", op=op)
    assert witness([item])[0]
    for stored, matches in [([], False), ([("string", "ALPHA")], True)]:
        manager, row = collected([item], {"eval_score": stored})
        assert manager._row_matches_filters(row) is matches


PICKED_JSON = '{"a":1,"b":2}'


@pytest.mark.parametrize(
    "stored,expected",
    [
        # The stored string itself, and its case variant: what every other
        # list surface admits for string storage (lowerUTF8 ... IN).
        ([("string", PICKED_JSON)], True),
        ([("string", '{"A":1,"B":2}')], True),
        # Strings that only PARSE to the same JSON are other stored strings.
        ([("string", '{"b":2,"a":1}')], False),
        ([("string", '{"a": 1, "b": 2}')], False),
        # The same JSON in json storage is not the picked string.
        ([("json", {"a": 1, "b": 2})], False),
        ([], False),
    ],
    ids=["exact", "case", "key-order", "whitespace", "json-storage", "absent"],
)
def test_a_picked_json_looking_string_matches_the_stored_string_raw(stored, expected):
    """Picker provenance means "this stored value": raw, case-insensitive.

    The picked value is the stored string the picker read from string
    storage. Comparing it after JSON canonicalisation would also admit
    strings that merely parse alike, which no deployed index can witness and
    which no other list surface admits; comparing it raw is witnessed exactly
    by the value bloom and keeps Users in step with the trace, span and
    session lists.
    """
    item = leaf("picker", [PICKED_JSON], op="in", types=["string"])
    manager, row = collected([item], {"picker": stored})
    assert manager._row_matches_filters(row) is expected


@pytest.mark.parametrize(
    "stored,expected",
    [
        ([("string", '{"b":2,"a":1}')], True),
        ([("string", '{"a": 1, "b": 2}')], True),
        ([("string", PICKED_JSON)], True),
        ([("string", '{"a":1,"b":3}')], False),
    ],
    ids=["key-order", "whitespace", "exact", "other"],
)
def test_typed_json_looking_text_still_matches_canonically(stored, expected):
    """Text a user typed keeps the canonical comparison it always had."""
    item = leaf("picker", PICKED_JSON)
    manager, row = collected([item], {"picker": stored})
    assert manager._row_matches_filters(row) is expected


@pytest.mark.parametrize(
    "picked,stored,expected",
    [
        ("true", [("string", "TRUE")], True),
        ("true", [("string", " true ")], False),
        ("true", [("boolean", True)], False),
        ("7", [("string", "7")], True),
        ("7", [("string", "7.0")], False),
        ("7", [("number", 7)], False),
    ],
    ids=[
        "bool-word-case",
        "bool-word-padded",
        "bool-storage",
        "digits",
        "digits-other-spelling",
        "number-storage",
    ],
)
def test_a_picked_boolean_or_number_looking_string_is_still_a_string(
    picked, stored, expected
):
    item = leaf("picker", [picked], op="in", types=["string"])
    manager, row = collected([item], {"picker": stored})
    assert manager._row_matches_filters(row) is expected


def test_a_picked_number_keeps_the_canonical_comparison_of_its_domain():
    item = leaf("picker", ["7"], op="in", types=["number"])
    for stored, expected in [
        ([("number", 7)], True),
        ([("number", 7.0)], True),
        ([("string", "7")], False),
    ]:
        manager, row = collected([item], {"picker": stored})
        assert manager._row_matches_filters(row) is expected


@pytest.mark.parametrize(
    "stored",
    [
        [("string", PICKED_JSON), ("string", '{"b":2,"a":1}')],
        [("string", '{"b":2,"a":1}'), ("string", PICKED_JSON)],
    ],
    ids=["picked-first", "variant-first"],
)
def test_two_raw_variants_of_one_json_are_both_collected(stored):
    """The collector keeps every stored string, not one per canonical form.

    A user holding the picked string and a key-order variant of it is a
    member by the raw rule; collapsing the two on their canonical form could
    keep only the variant and reject the user on the complete path.
    """
    item = leaf("picker", [PICKED_JSON], op="in", types=["string"])
    manager, row = collected([item], {"picker": stored})
    assert manager._row_matches_filters(row) is True
    kept = manager._attribute_values_by_user[UID]["picker"]
    assert sorted(kept) == sorted(value for _kind, value in stored)


@pytest.mark.parametrize(
    "picked,stored,expected",
    [
        # A padded boolean word in string storage beside a real boolean:
        # neither is the picked string, and pooling their storage types
        # must not make one.
        ("true", [("string", " true "), ("boolean", True)], False),
        ("true", [("boolean", True), ("string", " true ")], False),
        ("true", [("string", "true"), ("boolean", True)], True),
        # A whitespace JSON variant in string storage beside the same JSON in
        # json storage: neither is the picked string.
        (
            PICKED_JSON,
            [("string", '{"a": 1, "b": 2}'), ("json", {"a": 1, "b": 2})],
            False,
        ),
        (
            PICKED_JSON,
            [("json", {"a": 1, "b": 2}), ("string", '{"a": 1, "b": 2}')],
            False,
        ),
        (PICKED_JSON, [("string", PICKED_JSON), ("json", {"a": 1, "b": 2})], True),
    ],
    ids=[
        "padded-word-and-boolean",
        "boolean-and-padded-word",
        "exact-word-and-boolean",
        "whitespace-json-and-json-storage",
        "json-storage-and-whitespace-json",
        "exact-json-and-json-storage",
    ],
)
def test_a_picked_string_never_matches_through_another_storages_value(
    picked, stored, expected
):
    """Storage types are read back only against the value they were recorded for.

    The value map and the storage-type map are keyed by one identity (the raw
    string for a string, the canonical form otherwise); keyed by canonical
    form alone, a value of another storage lent its type to a string that
    does not match raw, and the union of two non-members was a member.
    """
    item = leaf("picker", [picked], op="in", types=["string"])
    manager, row = collected([item], {"picker": stored})
    assert manager._row_matches_filters(row) is expected


@pytest.mark.parametrize(
    "typed,stored",
    [
        ("true", [("string", " true "), ("boolean", True)]),
        (PICKED_JSON, [("string", '{"a": 1, "b": 2}'), ("json", {"a": 1, "b": 2})]),
    ],
    ids=["padded-word", "whitespace-json"],
)
def test_typed_text_still_matches_the_string_variant_canonically(typed, stored):
    """The same populations under typed text: the string variant matches canonically."""
    item = leaf("picker", typed)
    manager, row = collected([item], {"picker": stored})
    assert manager._row_matches_filters(row) is True
