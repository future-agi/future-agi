from types import SimpleNamespace
from uuid import uuid4

import pytest

from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.row_activity import RowActivity
from model_hub.views import develop_dataset as views

pytestmark = pytest.mark.django_db


@pytest.fixture
def make_dataset(organization, workspace, user):
    def make(name, columns):
        dataset = Dataset.objects.create(
            name=name, organization=organization, workspace=workspace, user=user
        )
        fields = [
            Column.objects.create(
                dataset=dataset,
                name=column_name,
                data_type=data_type,
                source=SourceChoices.OTHERS.value,
            )
            for column_name, data_type in columns
        ]
        dataset.column_order = [str(column.id) for column in fields]
        dataset.column_config = {
            str(column.id): {"is_visible": True} for column in fields
        }
        dataset.save()
        return dataset, fields

    return make


def add_row(dataset, values, order=0):
    row = Row.objects.create(dataset=dataset, order=order)
    for column, value in values.items():
        Cell.objects.create(dataset=dataset, row=row, column=column, value=value)
    return row


def state():
    # Include soft-deleted records and activity, not just visible grid contents.
    return {
        model.__name__: list(manager.order_by("id").values())
        for model, manager in (
            (Dataset, Dataset.all_objects),
            (Column, Column.all_objects),
            (Row, Row.all_objects),
            (Cell, Cell.all_objects),
            (RowActivity, RowActivity.objects),
        )
    }


def merge(client, source, target, rows, **kwargs):
    return client.post(
        f"/model-hub/datasets/{source.id}/merge/",
        {
            "target_dataset_id": str(target.id),
            "row_ids": [str(row.id) for row in rows],
            **kwargs,
        },
        format="json",
    )


def install_schema(dataset, schema, mode="reject"):
    dataset.dataset_config = {"schema": {"enforce": mode, "declared": schema}}
    dataset.save(update_fields=["dataset_config"])


@pytest.mark.parametrize(
    "data_type,values",
    [
        ("integer", ["7", "9007199254740993"]),
        ("boolean", ["false", "true"]),
        ("json", ['{"a": 1}', '{"a": 2}']),
        ("array", ['["a"]', '["b"]']),
        ("image", ["https://example.invalid/a.png", "https://example.invalid/b.png"]),
    ],
)
def test_successive_merges_preserve_types_and_reuse_columns(
    auth_client, make_dataset, data_type, values
):
    source, (column,) = make_dataset("Typed source", [("value", data_type)])
    target, _ = make_dataset("Typed target", [])
    rows = [add_row(source, {column: value}, i) for i, value in enumerate(values)]

    for index, row in enumerate(rows):
        response = merge(auth_client, source, target, [row])
        assert response.status_code == 200, response.content
        assert response.json()["result"] == {
            "message": "Datasets merged successfully",
            "rows_added": 1,
            "new_columns_created": 1 if index == 0 else 0,
            "columns_mapped": 0 if index == 0 else 1,
        }

    copied = Column.objects.get(dataset=target)
    assert (copied.name, copied.data_type) == ("value", data_type)
    assert (
        list(
            Cell.objects.filter(dataset=target, column=copied)
            .order_by("row__order")
            .values_list("value", flat=True)
        )
        == values
    )
    target.refresh_from_db()
    assert target.column_order == [str(copied.id)]


def test_merge_creates_editable_cells_on_both_sides(auth_client, make_dataset):
    source, (incoming, sparse) = make_dataset(
        "Sparse source", [("incoming", "text"), ("sparse", "text")]
    )
    target, (existing,) = make_dataset("Existing grid", [("existing", "text")])
    source_row = add_row(source, {incoming: "copied"})
    original = add_row(target, {existing: "retained"})

    response = merge(auth_client, source, target, [source_row])
    assert response.status_code == 200, response.content
    copied_row = Row.objects.get(dataset=target, order=1)
    copied_column = Column.objects.get(dataset=target, name="incoming")
    assert list(
        Cell.objects.filter(dataset=target)
        .order_by("row__order", "column__name")
        .values_list("row__order", "column__name", "value")
    ) == [
        (0, "existing", "retained"),
        (0, "incoming", None),
        (0, "sparse", None),
        (1, "existing", None),
        (1, "incoming", "copied"),
        (1, "sparse", None),
    ]
    for row, column, value in (
        (original, copied_column, "filled original"),
        (copied_row, existing, "filled copy"),
    ):
        edited = auth_client.post(
            f"/model-hub/develops/{target.id}/update_cell_value/",
            {
                "row_id": str(row.id),
                "column_id": str(column.id),
                "new_value": value,
            },
            format="json",
        )
        assert edited.status_code == 200, edited.content
        assert Cell.objects.get(row=row, column=column).value == value


@pytest.mark.parametrize("refusal", [None, "limit"])
def test_new_destination_row_refusal_leaves_no_records(
    auth_client, make_dataset, monkeypatch, refusal
):
    source, (column,) = make_dataset("New copy source", [("input", "text")])
    row = add_row(source, {column: "original"})
    calls = []

    def admit(**kwargs):
        calls.append(kwargs["api_call_type"])
        if kwargs["api_call_type"] == views.APICallTypeChoices.ROW_ADD.value:
            return (
                None
                if refusal is None
                else SimpleNamespace(
                    status=views.APICallStatusChoices.RESOURCE_LIMIT.value
                )
            )
        return SimpleNamespace(status="created", save=lambda: None)

    monkeypatch.setattr(views, "log_and_deduct_cost_for_resource_request", admit)
    before = state()
    response = auth_client.post(
        f"/model-hub/datasets/{source.id}/duplicate/",
        {"name": "Refused destination", "row_ids": [str(row.id)]},
        format="json",
    )
    assert response.status_code == 429, response.content
    assert calls == [
        views.APICallTypeChoices.DATASET_ADD.value,
        views.APICallTypeChoices.ROW_ADD.value,
    ]
    assert state() == before


def test_new_destination_allowed_selection_preserves_values(
    auth_client, make_dataset, monkeypatch
):
    source, (column,) = make_dataset("Allowed source", [("count", "integer")])
    row = add_row(source, {column: "7"})
    monkeypatch.setattr(
        views,
        "log_and_deduct_cost_for_resource_request",
        lambda **kwargs: SimpleNamespace(status="created", save=lambda: None),
    )
    response = auth_client.post(
        f"/model-hub/datasets/{source.id}/duplicate/",
        {"name": "Allowed destination", "row_ids": [str(row.id)]},
        format="json",
    )
    assert response.status_code == 200, response.content
    target = Dataset.objects.get(id=response.json()["result"]["new_dataset_id"])
    assert Row.objects.filter(dataset=target).count() == 1
    assert list(
        Cell.objects.filter(dataset=target).values_list(
            "column__name", "column__data_type", "value"
        )
    ) == [("count", "integer", "7")]


@pytest.mark.parametrize("selection", ["missing", "deleted", "foreign", "repeated"])
def test_refused_selection_does_not_mutate_destination(
    auth_client, make_dataset, monkeypatch, selection
):
    source, (column,) = make_dataset("Selection source", [("new", "text")])
    target, (existing,) = make_dataset("Selection target", [("existing", "text")])
    row = add_row(source, {column: "source"})
    foreign = add_row(target, {existing: "target"})
    stale = add_row(source, {column: "removed"}, 1)
    stale.deleted = True
    stale.save()
    invalid = {
        "missing": SimpleNamespace(id=uuid4()),
        "deleted": stale,
        "foreign": foreign,
        "repeated": row,
    }[selection]
    writes = []
    original = Column.objects.bulk_create

    def record_columns(*args, **kwargs):
        writes.append("columns")
        return original(*args, **kwargs)

    monkeypatch.setattr(Column.objects, "bulk_create", record_columns)
    before = state()
    response = merge(auth_client, source, target, [row, invalid])
    assert response.status_code == 400, response.content
    assert writes == []
    assert state() == before


@pytest.mark.parametrize("mode,expected_status", [("reject", 400), ("off", 200)])
def test_merge_respects_required_destination_fields(
    auth_client, make_dataset, mode, expected_status
):
    source, (other,) = make_dataset("Other field source", [("other", "text")])
    target, (question,) = make_dataset("Required target", [("question", "text")])
    row = add_row(source, {other: "value"})
    add_row(target, {question: "allowed"})
    install_schema(
        target,
        {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
        mode,
    )
    before = state()
    response = merge(auth_client, source, target, [row])
    assert response.status_code == expected_status, response.content
    if mode == "reject":
        assert state() == before
    else:
        assert Row.objects.filter(dataset=target).count() == 2


def test_merge_validates_entire_selection_before_column_creation(
    auth_client, make_dataset, monkeypatch
):
    source, (column, extra) = make_dataset(
        "Schema source", [("question", "text"), ("extra", "integer")]
    )
    target, (question,) = make_dataset("Schema target", [("question", "text")])
    rows = [
        add_row(source, {column: "allowed", extra: "1"}),
        add_row(source, {column: "invalid", extra: "2"}, 1),
    ]
    add_row(target, {question: "allowed"})
    install_schema(
        target,
        {
            "type": "object",
            "properties": {
                "question": {"type": "string", "enum": ["allowed"]},
                "extra": {"type": ["integer", "null"]},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    )
    writes = []
    original = Column.objects.bulk_create

    def record_columns(*args, **kwargs):
        writes.append("columns")
        return original(*args, **kwargs)

    monkeypatch.setattr(Column.objects, "bulk_create", record_columns)
    before = state()
    response = merge(auth_client, source, target, rows)
    assert response.status_code == 400, response.content
    assert writes == []
    assert state() == before

    accepted = merge(auth_client, source, target, rows[:1])
    assert accepted.status_code == 200, accepted.content
    assert list(
        Cell.objects.filter(dataset=target, row__order=1)
        .order_by("column__name")
        .values_list("column__name", "column__data_type", "value")
    ) == [("extra", "integer", "1"), ("question", "text", "allowed")]


@pytest.mark.parametrize(
    "extra_type,populated,expected_status",
    [
        ("integer", True, 400),
        (["integer", "null"], True, 200),
        ("integer", False, 200),
    ],
)
def test_merge_checks_existing_rows_before_new_column_backfill(
    auth_client, make_dataset, monkeypatch, extra_type, populated, expected_status
):
    source, (incoming, extra) = make_dataset(
        "Backfill source", [("question", "text"), ("extra", "integer")]
    )
    target, (question,) = make_dataset("Backfill target", [("question", "text")])
    row = add_row(source, {incoming: "copied", extra: "7"})
    if populated:
        add_row(target, {question: "retained"})
    schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "extra": {"type": extra_type},
        },
        # Absent extra is valid, but a newly published null may not be.
        "required": ["question"],
        "additionalProperties": False,
    }
    install_schema(target, schema)
    writes = []
    original = Column.objects.bulk_create

    def record_columns(*args, **kwargs):
        writes.append("columns")
        return original(*args, **kwargs)

    monkeypatch.setattr(Column.objects, "bulk_create", record_columns)
    before = state()
    response = merge(auth_client, source, target, [row])
    assert response.status_code == expected_status, response.content
    target.refresh_from_db()
    assert target.dataset_config == {
        "schema": {"enforce": "reject", "declared": schema}
    }
    if expected_status == 400:
        assert writes == []
        assert state() == before
        assert target.column_order == [str(question.id)]
        assert target.column_config == {str(question.id): {"is_visible": True}}
        return

    assert writes == ["columns"]
    assert response.json()["result"] == {
        "message": "Datasets merged successfully",
        "rows_added": 1,
        "new_columns_created": 1,
        "columns_mapped": 1,
    }
    added = Column.objects.get(dataset=target, name="extra")
    assert added.data_type == "integer"
    assert target.column_order == [str(question.id), str(added.id)]
    assert target.column_config == {
        str(question.id): {"is_visible": True},
        str(added.id): {"is_visible": True, "is_frozen": None},
    }
    expected_cells = (
        [(0, "extra", None), (0, "question", "retained")] if populated else []
    ) + [(int(populated), "extra", "7"), (int(populated), "question", "copied")]
    assert (
        list(
            Cell.objects.filter(dataset=target)
            .order_by("row__order", "column__name")
            .values_list("row__order", "column__name", "value")
        )
        == expected_cells
    )
    assert Row.objects.filter(dataset=target).count() == 1 + int(populated)


def test_merge_refuses_ambiguous_schema_names(auth_client, make_dataset):
    source, (column,) = make_dataset("Typed collision", [("value", "integer")])
    target, (existing,) = make_dataset("Text destination", [("value", "text")])
    row = add_row(source, {column: "7"})
    add_row(target, {existing: "original"})
    install_schema(target, {"type": "object"})
    before = state()
    response = merge(auth_client, source, target, [row])
    assert response.status_code == 400, response.content
    assert state() == before


@pytest.mark.parametrize("operation", ["merge", "new"])
def test_copy_failure_in_later_batch_rolls_back_all_records(
    auth_client, make_dataset, monkeypatch, operation
):
    source, (column,) = make_dataset("Batch source", [("incoming", "text")])
    target, (existing,) = make_dataset("Batch target", [("existing", "text")])
    add_row(target, {existing: "retained"})
    rows = [Row(dataset=source, order=index) for index in range(1001)]
    Row.objects.bulk_create(rows)
    Cell.objects.bulk_create(
        [
            Cell(dataset=source, row=row, column=column, value=str(index))
            for index, row in enumerate(rows)
        ]
    )
    monkeypatch.setattr(
        views,
        "log_and_deduct_cost_for_resource_request",
        lambda **kwargs: SimpleNamespace(status="created", save=lambda: None),
    )
    original = Cell.objects.bulk_create
    inserted_batches = []

    def insert_then_fail(cells, **kwargs):
        result = original(cells, **kwargs)
        inserted_batches.append(len(cells))
        if any(cell.value == "1000" for cell in cells):
            raise RuntimeError("Synthetic later-batch failure")
        return result

    monkeypatch.setattr(Cell.objects, "bulk_create", insert_then_fail)
    before = state()
    if operation == "merge":
        response = merge(auth_client, source, target, [], selected_all_rows=True)
    else:
        response = auth_client.post(
            f"/model-hub/datasets/{source.id}/duplicate/",
            {"name": "Failed batch destination", "selected_all_rows": True},
            format="json",
        )
    assert response.status_code == 500, response.content
    assert len(inserted_batches) == (3 if operation == "merge" else 2)
    assert state() == before


def test_merge_all_rows_exclusion_uses_original_population(auth_client, make_dataset):
    dataset, (column,) = make_dataset("Self copy", [("input", "text")])
    kept = add_row(dataset, {column: "keep"})
    excluded = add_row(dataset, {column: "skip"}, 1)
    response = merge(auth_client, dataset, dataset, [excluded], selected_all_rows=True)
    assert response.status_code == 200, response.content
    assert response.json()["result"]["rows_added"] == 1
    assert list(
        Cell.objects.filter(dataset=dataset)
        .order_by("row__order")
        .values_list("value", flat=True)
    ) == ["keep", "skip", "keep"]
    assert Cell.objects.get(row=kept, column=column).value == "keep"
