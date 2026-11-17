"""Nested predicates select the same scoped rows in table and drawer reads."""

import json
from pathlib import Path

import pytest
from rest_framework.exceptions import ValidationError

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.services.dataset_failed_filters import validate_read_columns
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import tenants  # noqa: F401

# Also asserted against formatPath -> pathFilterEntry -> both JS read encoders.
VALUE_ACTIONS = json.loads(
    (Path(__file__).parent / "fixtures" / "nested_value_action_cases.json").read_text(
        encoding="utf-8"
    )
)


def nested_filter(column, path, kind="text", op="equals", value="enterprise"):
    return {
        "column_id": f"{column.id}::{path}",
        "filter_config": {
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        },
    }


@pytest.fixture
def nested_rows(tenants):  # noqa: F811
    a, b, clients = tenants
    dataset = a["dataset"]
    column = Column.no_workspace_objects.create(
        id=VALUE_ACTIONS["column_id"],
        dataset=dataset,
        name="metadata",
        data_type=DataTypeChoices.JSON.value,
        source=SourceChoices.OTHERS.value,
    )
    rows = a["rows"] + [
        Row.no_workspace_objects.create(dataset=dataset, order=i) for i in range(2, 8)
    ]
    values = [
        {"customer": {"tier": "enterprise", "score": 12, "active": True}},
        {
            "customer": {"tier": "basic", "score": 2, "active": False},
            "other": {"tier": "enterprise", "score": 12, "active": True},
        },
        {"customer": {"tier": "enterprise", "score": 30, "active": True}},
        {"customer": {}},
        {"customer": {"tier": None, "score": None, "active": None}},
        None,  # Missing cell, not just a missing key.
        "not JSON",
        {"customer": {"tier": "other", "score": "12", "active": "true"}},
    ]
    for row, value in zip(rows, values, strict=True):
        if value is not None:
            Cell.no_workspace_objects.create(
                dataset=dataset,
                row=row,
                column=column,
                value=value if isinstance(value, str) else json.dumps(value),
            )
    return a, b, clients["a"], column, rows


def assert_read_population(seed, filters, expected):
    a, _b, client, _column, rows = seed
    root = f"/model-hub/develops/{a['dataset'].id}"
    for direction in ("ascending", "descending"):
        indices = expected if direction == "ascending" else list(reversed(expected))
        ids = [str(rows[i].id) for i in indices]
        sort = [{"column_id": "system:order", "type": direction}]
        table = client.get(
            f"{root}/get-dataset-table/",
            {"filters": json.dumps(filters), "sort": json.dumps(sort)},
        )
        assert table.status_code == 200, table.content
        result = table.json()["result"]
        assert [row["row_id"] for row in result["table"]] == ids
        assert result["metadata"]["total_rows"] == len(ids)
        assert result["metadata"]["has_more"] is False
        for index, row_id in enumerate(ids):
            detail = client.post(
                f"{root}/get-row-data/",
                {"row_id": row_id, "filters": filters, "sort": sort},
                format="json",
            )
            assert detail.status_code == 200, detail.content
            assert detail.json()["result"]["current"]["row_id"] == row_id
            assert detail.json()["result"]["next"]["row_id"] == ids[index + 1 :]
        outside = next((i for i in range(len(rows)) if i not in indices), None)
        if outside is not None:
            detail = client.post(
                f"{root}/get-row-data/",
                {"row_id": str(rows[outside].id), "filters": filters, "sort": sort},
                format="json",
            )
            assert detail.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path,kind,op,value,expected",
    [
        ("customer.tier", "text", "equals", "enterprise", [0, 2]),
        ("customer.tier", "text", "contains", "TERPR", [0, 2]),
        ("customer.tier", "text", "not_equals", "enterprise", [1, 7]),
        ("customer.tier", "text", "in", ["BASIC", "other"], [1, 7]),
        ("customer.score", "number", "greater_than", 10, [0, 2]),
        ("customer.score", "number", "between", [10, 20], [0]),
        ("customer.score", "number", "not_between", [10, 30], [1]),
        ("customer.active", "boolean", "equals", True, [0, 2]),
        ("customer.active", "boolean", "not_equals", True, [1]),
        ("customer.active", "boolean", "equals", False, [1]),
        # Legacy actions and boolean value actions still use text predicates.
        ("customer.score", "text", "equals", "30", [2]),
        ("customer.active", "text", "equals", "true", [0, 2, 7]),
        ("customer.tier", "text", "is_null", None, [3, 4, 5, 6]),
        ("customer.tier", "text", "is_not_null", None, [0, 1, 2, 7]),
        ("customer.missing", "text", "equals", "enterprise", []),
        ("customer.missing", "text", "is_null", None, list(range(8))),
    ],
)
def test_nested_table_and_row_population_agree(
    nested_rows, path, kind, op, value, expected
):
    assert_read_population(
        nested_rows,
        [nested_filter(nested_rows[3], path, kind, op, value)],
        expected,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("case", VALUE_ACTIONS["cases"], ids=lambda case: case["name"])
def test_value_action_requests_share_nested_population(nested_rows, case):
    _a, _b, _client, column, rows = nested_rows
    Cell.no_workspace_objects.filter(column=column).update(value="{}")
    # Two matches with an intervening nonmatch exercise both neighbor directions.
    # A numerically equivalent string with a different spelling must not match.
    string_leaf = case.get("string_leaf", str(case["value"]))
    leaves = [case["value"], string_leaf, case["value"], False]
    if isinstance(case["value"], str):
        leaves[1] = "other"
    for index, leaf in enumerate(leaves):
        document = leaf
        for key in reversed(case["keys"]):
            document = {key: document}
        Cell.no_workspace_objects.filter(row=rows[index], column=column).update(
            value=json.dumps(document)
        )
    # Use the exact wire request shared with the real frontend action tests.
    assert_read_population(nested_rows, [case["request"]], [0, 2])


@pytest.mark.django_db
@pytest.mark.parametrize(
    "case",
    [case for case in VALUE_ACTIONS["cases"] if "text_value" in case],
    ids=lambda case: case["name"],
)
def test_numeric_leaves_match_number_and_text_membership_predicates(nested_rows, case):
    _a, _b, _client, column, rows = nested_rows
    Cell.no_workspace_objects.filter(column=column).update(value="{}")
    for index in (0, 2):
        Cell.no_workspace_objects.filter(row=rows[index], column=column).update(
            value=json.dumps({"customer": {"score": case["value"]}})
        )
    for kind, op, value in (
        ("number", "equals", case["value"]),
        ("text", "in", [case["text_value"]]),
    ):
        assert_read_population(
            nested_rows,
            [nested_filter(column, case["path"], kind, op, value)],
            [0, 2],
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kind,op,value,expected",
    [
        ("text", "equals", "12", [0, 1, 3]),
        ("text", "equals", "12.0", [0, 1, 2]),
        ("text", "equals", 12, [0, 1, 3]),
        ("number", "equals", "12", [0, 1]),
        ("number", "equals", "12.0", [0, 1]),
        ("number", "equals", 12, [0, 1]),
        ("text", "equals", "1e-7", [4]),
        ("number", "equals", "1e-7", [4]),
        ("text", "equals", "0", [7]),
        ("number", "equals", "0", [7]),
        ("text", "in", ["12"], [0, 1, 3]),
        ("text", "in", [12], [0, 1, 3]),
        ("text", "not_in", ["12"], [2, 4, 5, 7]),
        ("text", "not_equals", "12", [2, 4, 5, 7]),
        ("number", "not_equals", "12", [4, 7]),
        ("text", "equals", "1e-9999", []),
        ("number", "equals", "1e-9999", []),
        *[
            (kind, "equals", value, [])
            for kind in ("text", "number")
            for value in ("NaN", "Infinity", "-Infinity", "1_2", "0xC", "12x")
        ],
    ],
)
def test_numeric_leaves_compare_by_value_without_coercing_string_leaves(
    nested_rows, kind, op, value, expected
):
    a, _b, _client, column, rows = nested_rows
    leaves = [12.0, 12, "12.0", "12", 1e-7, False, None, 0.0]
    for row, leaf in zip(rows, leaves, strict=True):
        Cell.no_workspace_objects.update_or_create(
            dataset=a["dataset"],
            row=row,
            column=column,
            defaults={"value": json.dumps({"customer": {"score": leaf}})},
        )
    assert_read_population(
        nested_rows,
        [nested_filter(column, "customer.score", kind, op, value)],
        expected,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["text", "number"])
def test_nested_decimal_equality_does_not_round_adjacent_integers(nested_rows, kind):
    _a, _b, _client, column, rows = nested_rows
    Cell.no_workspace_objects.filter(column=column).update(value="{}")
    for index, value in enumerate([9007199254740992, 9007199254740993]):
        Cell.no_workspace_objects.filter(row=rows[index], column=column).update(
            value=json.dumps({"customer": {"score": value}})
        )
    assert_read_population(
        nested_rows,
        [nested_filter(column, "customer.score", kind, "equals", "9007199254740993")],
        [1],
    )


@pytest.mark.django_db
def test_value_action_survives_filter_panel_edit_in_both_readers(nested_rows):
    a, _b, _client, column, rows = nested_rows
    roundtrip = VALUE_ACTIONS["panel_roundtrip"]
    unrelated = Column.no_workspace_objects.create(
        id=roundtrip["before"][1]["column_id"],
        dataset=a["dataset"],
        name="threshold",
        data_type=DataTypeChoices.FLOAT.value,
        source=SourceChoices.OTHERS.value,
    )
    Cell.no_workspace_objects.filter(column=column).update(value="{}")
    for index, (score, threshold) in enumerate(
        [(12.0, 10), ("12.0", 10), (12.0, 2), (1e-7, 10)]
    ):
        Cell.no_workspace_objects.filter(row=rows[index], column=column).update(
            value=json.dumps({"customer": {"score": score}})
        )
        Cell.no_workspace_objects.create(
            dataset=a["dataset"],
            row=rows[index],
            column=unrelated,
            value=str(threshold),
        )
    assert Cell.no_workspace_objects.get(row=rows[0], column=column).value == (
        '{"customer": {"score": 12.0}}'
    )
    # The real DevelopFilterBox + TraceFilterPanel test emits these exact sets.
    # Opening the panel converts equals to text membership; only threshold edits.
    assert_read_population(nested_rows, roundtrip["before"], [0, 2])
    assert_read_population(nested_rows, roundtrip["after"], [0])


@pytest.mark.django_db
@pytest.mark.parametrize("data_type", ["json", "array"])
def test_array_indices_and_wildcards_read_nested_leaves(nested_rows, data_type):
    _a, _b, _client, column, rows = nested_rows
    Column.no_workspace_objects.filter(id=column.id).update(data_type=data_type)
    Cell.no_workspace_objects.filter(column=column).update(value="[]")
    for index, names in [(0, ["alpha", "beta"]), (1, ["gamma"]), (2, ["beta"])]:
        Cell.no_workspace_objects.filter(row=rows[index], column=column).update(
            value=json.dumps([{"name": name} for name in names]),
        )
    assert_read_population(
        nested_rows, [nested_filter(column, "[0].name", value="beta")], [2]
    )
    assert_read_population(
        nested_rows,
        [nested_filter(column, "[*].name", "array", "contains", ["beta"])],
        [0, 2],
    )
    assert_read_population(
        nested_rows,
        [nested_filter(column, "[*].name", "array", "not_contains", ["beta"])],
        [1],
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data_type,path",
    [
        ("json", "customer.scores"),
        ("json", "items[*].score"),
        ("json", "items[*].scores"),
        ("array", "[*].score"),
    ],
)
@pytest.mark.parametrize(
    "value,expected",
    [
        (["12.00"], [0, 2]),
        (["12.0"], [0]),
        (["12"], [0, 3]),
        ([12], [0]),
        (["1.2e1"], [0]),
        (["1e-7"], [0, 2]),
        (["0.0000001"], [0]),
        ([1e-7], [0]),
        (["9007199254740993"], [5]),
        (["Alpha"], [4]),
        (["alpha"], [5]),
        (["Al"], []),
        (["12", "1e-7"], [0, 2, 3]),
        (["NaN", "Infinity", "1_2"], []),
    ],
)
def test_nested_array_membership_uses_element_equality(
    nested_rows, data_type, path, value, expected
):
    a, _b, client, column, rows = nested_rows
    Column.no_workspace_objects.filter(id=column.id).update(data_type=data_type)
    groups = [
        [12.0, 1e-7],
        [120, "120", 1e-6],
        ["12.00", "1e-7"],
        ["12", "1e-07"],
        [9007199254740992, "Alpha"],
        [9007199254740993, "alpha"],
        [None],
        [],
    ]
    for row, leaves in zip(rows, groups, strict=True):
        if path == "customer.scores":
            document = {"customer": {"scores": leaves}}
        elif path == "items[*].scores":
            document = {"items": [{"scores": leaves}]}
        else:
            document = [{"score": leaf} for leaf in leaves]
            if path == "items[*].score":
                document = {"items": document}
        Cell.no_workspace_objects.update_or_create(
            dataset=a["dataset"],
            row=row,
            column=column,
            defaults={"value": json.dumps(document)},
        )
    # Empty arrays and null-only leaves are not negative-membership matches.
    for op, population in (
        ("contains", expected),
        ("not_contains", [index for index in range(6) if index not in expected]),
    ):
        filters = [nested_filter(column, path, "array", op, value)]
        assert_read_population(nested_rows, filters, population)
        # The count must cover the full filtered set, not just the first page.
        response = client.get(
            f"/model-hub/develops/{a['dataset'].id}/get-dataset-table/",
            {
                "filters": json.dumps(filters),
                "sort": json.dumps(
                    [{"column_id": "system:order", "type": "ascending"}]
                ),
                "page_size": 1,
                "current_page_index": 0,
            },
        )
        assert response.status_code == 200, response.content
        result = response.json()["result"]
        assert [item["row_id"] for item in result["table"]] == [
            str(rows[index].id) for index in population[:1]
        ]
        assert result["metadata"]["total_rows"] == len(population)
        assert result["metadata"]["has_more"] is (len(population) > 1)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "op,value,expected",
    [
        ("contains", "12", [0, 1, 2]),
        ("not_contains", "12", [3, 4, 5]),
        ("contains", "LPH", [3, 4, 5]),
        ("not_contains", "LPH", [0, 1, 2]),
    ],
)
def test_nested_text_contains_keeps_substring_matching(
    nested_rows, op, value, expected
):
    a, _b, _client, column, rows = nested_rows
    leaves = ["120", "12.00", "12", "alpha", "Alpha", "alphabet", None, None]
    for row, leaf in zip(rows, leaves, strict=True):
        Cell.no_workspace_objects.update_or_create(
            dataset=a["dataset"],
            row=row,
            column=column,
            defaults={"value": json.dumps({"customer": {"score": leaf}})},
        )
    assert_read_population(
        nested_rows,
        [nested_filter(column, "customer.score", "text", op, value)],
        expected,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    [
        "",
        ".tier",
        "customer..tier",
        "customer.",
        "items[-1]",
        "items[01]",
        "items[",
        "customer::tier",
        "$..tier",
        "a" * 513,
        ".".join(["a"] * 33),
        'customer["name"',
        'customer["name"]suffix',
        "customer['name']",
        r'customer["bad\q"]',
        r'customer["bad\uZZZZ"]',
        'customer["line\nname"]',
        'items[?(@.name=="Ada")]',
        "items[0:2]",
        '["a"]' * 33,
    ],
)
def test_malformed_nested_paths_are_refused(nested_rows, path):
    a, _b, client, column, rows = nested_rows
    item = nested_filter(column, path)
    with pytest.raises(ValidationError) as error:
        validate_read_columns([item], [], {str(column.id): column})
    assert error.value.detail == {"filters": "Malformed nested dataset path."}
    root = f"/model-hub/develops/{a['dataset'].id}"
    table = client.get(f"{root}/get-dataset-table/", {"filters": json.dumps([item])})
    detail = client.post(
        f"{root}/get-row-data/",
        {"row_id": str(rows[0].id), "filters": [item]},
        format="json",
    )
    assert table.status_code == 400
    assert detail.status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["foreign", "unknown", "text", "deleted"])
def test_nested_paths_require_an_accessible_json_column(nested_rows, target):
    a, b, client, column, rows = nested_rows
    columns = {str(column.id): column, str(a["column"].id): a["column"]}
    if target == "foreign":
        item = nested_filter(b["column"], "customer.tier")
    elif target == "unknown":
        item = nested_filter(column, "customer.tier")
        item["column_id"] = "00000000-0000-4000-8000-000000000000::customer.tier"
    elif target == "deleted":
        Column.no_workspace_objects.filter(id=column.id).update(deleted=True)
        columns.pop(str(column.id))
        item = nested_filter(column, "customer.tier")
    else:
        item = nested_filter(a["column"], "customer.tier")
    message = (
        "Nested dataset paths require a JSON or array column."
        if target == "text"
        else "Unknown or inaccessible dataset column."
    )
    with pytest.raises(ValidationError) as error:
        validate_read_columns([item], [], columns)
    assert error.value.detail == {"column_id": message}
    root = f"/model-hub/develops/{a['dataset'].id}"
    table = client.get(f"{root}/get-dataset-table/", {"filters": json.dumps([item])})
    detail = client.post(
        f"{root}/get-row-data/",
        {"row_id": str(rows[0].id), "filters": [item]},
        format="json",
    )
    assert table.status_code == 400
    assert detail.status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data_type,kind,op,value,stored,expected",
    [
        ("text", "text", "equals", "Alpha", ["Alpha", "Beta", "Alpha"], [0, 2]),
        ("float", "number", "greater_than", 10, ["12", "2", "30"], [0, 2]),
        ("boolean", "boolean", "equals", False, ["true", "false", "true"], [1]),
    ],
)
def test_flat_filters_and_nested_intersections_are_unchanged(
    nested_rows, data_type, kind, op, value, stored, expected
):
    a, _b, _client, nested_column, rows = nested_rows
    column = Column.no_workspace_objects.create(
        dataset=a["dataset"],
        name="flat",
        data_type=data_type,
        source=SourceChoices.OTHERS.value,
    )
    for row, text in zip(rows, stored, strict=False):
        Cell.no_workspace_objects.create(
            dataset=a["dataset"], row=row, column=column, value=text
        )
    flat = {
        "column_id": str(column.id),
        "filter_config": {
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        },
    }
    assert_read_population(nested_rows, [flat], expected)
    assert_read_population(
        nested_rows,
        [flat, nested_filter(nested_column, "customer.tier")],
        [index for index in expected if index in [0, 2]],
    )


@pytest.mark.django_db
def test_nested_filter_count_and_neighbors_span_table_pages(nested_rows):
    a, _b, client, column, rows = nested_rows
    filters = [nested_filter(column, "customer.tier")]
    root = f"/model-hub/develops/{a['dataset'].id}"
    for page, row in enumerate([rows[0], rows[2]]):
        response = client.get(
            f"{root}/get-dataset-table/",
            {
                "filters": json.dumps(filters),
                "page_size": 1,
                "current_page_index": page,
            },
        )
        assert response.status_code == 200, response.content
        result = response.json()["result"]
        assert [item["row_id"] for item in result["table"]] == [str(row.id)]
        assert result["metadata"]["total_rows"] == 2
        assert result["metadata"]["has_more"] is (page == 0)
    response = client.post(
        f"{root}/get-row-data/",
        {"row_id": str(rows[0].id), "filters": filters},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["result"]["next"]["row_id"] == [str(rows[2].id)]


@pytest.mark.django_db
@pytest.mark.parametrize("deleted_part", ["row", "cell"])
def test_nested_filters_ignore_deleted_values(nested_rows, deleted_part):
    _a, _b, _client, column, rows = nested_rows
    if deleted_part == "row":
        Row.no_workspace_objects.filter(id=rows[0].id).update(deleted=True)
    else:
        Cell.no_workspace_objects.filter(row=rows[0], column=column).update(
            deleted=True
        )
    assert_read_population(nested_rows, [nested_filter(column, "customer.tier")], [2])


@pytest.mark.django_db
def test_nested_read_failure_never_returns_unfiltered_rows(nested_rows, monkeypatch):
    from model_hub.services.bounded_dataset_read import (
        BoundedDatasetReadDeadlineExceeded,
    )
    from model_hub.views.develop_dataset import GetDatasetTableView

    a, _b, _client, column, _rows = nested_rows

    def fail(*args):
        raise BoundedDatasetReadDeadlineExceeded("Nested read timed out.")

    monkeypatch.setattr("model_hub.views.develop_dataset.apply_nested_filter", fail)
    with pytest.raises(BoundedDatasetReadDeadlineExceeded) as error:
        GetDatasetTableView()._apply_filters(
            Cell.no_workspace_objects.filter(dataset=a["dataset"]),
            Row.no_workspace_objects.filter(dataset=a["dataset"]),
            [nested_filter(column, "customer.tier")],
            [],
            {str(column.id): column},
        )
    assert str(error.value) == "Nested read timed out."


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["label:en", "*"])
def test_nested_keys_are_literal_not_query_expressions(nested_rows, key):
    _a, _b, _client, column, rows = nested_rows
    Cell.no_workspace_objects.filter(row=rows[0], column=column).update(
        value=json.dumps({"display name": {key: "Ada"}})
    )
    assert_read_population(
        nested_rows,
        [nested_filter(column, f"display name.{key}", value="Ada")],
        [0],
    )
