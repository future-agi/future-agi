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
