"""Append generation is atomic, scoped and repeatable without replacing data."""

import json

import pytest

from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

CREATE = "/model-hub/develops/create-dataset-manually/"


def path(dataset, action):
    return f"/model-hub/develops/{dataset.id}/{action}/"


@pytest.fixture
def grid(auth_client):
    response = auth_client.post(
        CREATE,
        {
            "dataset_name": "Generation target",
            "number_of_rows": 1,
            "columns": [{"name": "question", "type": "text", "role": "input"}],
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    return Dataset.objects.get(id=response.json()["result"]["dataset_id"])


@pytest.fixture
def config(grid):
    return {
        "generation_mode": "append",
        "model": "gpt-4o-mini",
        "num_rows": 2,
        "columns": [
            {
                "name": "question",
                "data_type": "text",
                "description": "A question",
                "property": [],
            }
        ],
        "dataset": {
            "name": grid.name,
            "description": "Refund questions",
            "objective": "Support",
            "patterns": "Short",
        },
    }


@pytest.fixture
def model(monkeypatch):
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    calls = []

    def complete(self, streaming=False):
        calls.append(self)
        return json.dumps({"rows": [{"question": "first"}, {"question": "second"}]}), {}

    monkeypatch.setattr(RunPrompt, "litellm_response", complete)
    return calls


@pytest.mark.django_db
def test_append_and_regenerate_preserve_original_rows_and_schema(
    auth_client, grid, config, model
):
    before_rows = set(Row.objects.filter(dataset=grid).values_list("id", flat=True))
    before_columns = list(
        Column.objects.filter(dataset=grid).values_list("id", flat=True)
    )
    first = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert first.status_code == 200, first.content
    saved = auth_client.get(
        path(grid, "synthetic-config"), {"generation_mode": "append"}
    )
    assert saved.status_code == 200, saved.content
    assert saved.json()["result"]["data"] == config
    second = auth_client.put(
        path(grid, "update-synthetic-config"),
        {**config, "regenerate": True},
        format="json",
    )
    assert second.status_code == 200, second.content
    assert Row.objects.filter(dataset=grid).count() == 5
    assert before_rows <= set(
        Row.objects.filter(dataset=grid).values_list("id", flat=True)
    )
    assert (
        list(Column.objects.filter(dataset=grid).values_list("id", flat=True))
        == before_columns
    )
    assert list(
        Cell.objects.filter(dataset=grid)
        .exclude(row_id__in=before_rows)
        .order_by("row__order")
        .values_list("value", flat=True)
    ) == ["first", "second", "first", "second"]
    assert len(model) == 2
    assert model[0].messages == model[1].messages
    assert str(model[0].organization_id) == str(grid.organization_id)
    assert str(model[0].workspace_id) == str(grid.workspace_id)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "patch",
    [
        {"num_rows": 0},
        {"num_rows": 101},
        {"columns": []},
        {"fill_existing_rows": True},
        {"kb_id": "00000000-0000-0000-0000-000000000001"},
    ],
)
def test_invalid_generation_does_not_write_or_call_model(
    auth_client, grid, config, model, patch
):
    response = auth_client.post(
        path(grid, "add_synthetic_data"), {**config, **patch}, format="json"
    )
    assert response.status_code == 400, response.content
    assert Row.objects.filter(dataset=grid).count() == 1
    assert model == []


@pytest.mark.django_db
def test_bad_model_output_leaves_rows_and_config_unchanged(
    auth_client, grid, config, monkeypatch
):
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    monkeypatch.setattr(
        RunPrompt,
        "litellm_response",
        lambda *a, **k: ('{"rows":[{"question":42}]}', {}),
    )
    response = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert response.status_code == 502, response.content
    assert Row.objects.filter(dataset=grid).count() == 1
    grid.refresh_from_db()
    assert not (grid.dataset_config or {}).get("row_generation")


@pytest.mark.django_db
def test_failed_cell_write_rolls_back_append(
    auth_client, grid, config, model, monkeypatch
):
    reached = []

    def fail(*args, **kwargs):
        reached.append(True)
        raise RuntimeError("Controlled cell persistence failure")

    monkeypatch.setattr(Cell.objects, "bulk_create", fail)
    response = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert response.status_code == 500
    assert reached == [True]
    assert Row.objects.filter(dataset=grid).count() == 1
    grid.refresh_from_db()
    assert not (grid.dataset_config or {}).get("row_generation")


@pytest.mark.django_db
def test_regeneration_rejects_changed_configuration(auth_client, grid, config, model):
    assert (
        auth_client.post(
            path(grid, "add_synthetic_data"), config, format="json"
        ).status_code
        == 200
    )
    response = auth_client.put(
        path(grid, "update-synthetic-config"),
        {**config, "num_rows": 3, "regenerate": True},
        format="json",
    )
    assert response.status_code == 400
    assert Row.objects.filter(dataset=grid).count() == 3
    assert len(model) == 1


@pytest.mark.django_db
def test_schema_change_during_completion_does_not_append(
    auth_client, grid, config, monkeypatch
):
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    def complete(*args, **kwargs):
        Column.objects.filter(dataset=grid).update(name="renamed")
        return json.dumps({"rows": [{"question": "first"}, {"question": "second"}]}), {}

    monkeypatch.setattr(RunPrompt, "litellm_response", complete)
    response = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert response.status_code == 400, response.content
    assert Row.objects.filter(dataset=grid).count() == 1
    grid.refresh_from_db()
    assert not (grid.dataset_config or {}).get("row_generation")


@pytest.mark.parametrize(
    "content",
    [
        '{"rows":[{"question":42},{"question":"valid"}]}',
        '{"rows":[{"question":"first","extra":true},{"question":"second"}]}',
        '{"rows":[]}',
        "not-json",
    ],
)
def test_invalid_response_shapes_are_not_accepted(content):
    from model_hub.services.row_generation import GenerationOutputError, validate_output

    with pytest.raises(GenerationOutputError):
        validate_output(
            content,
            {"num_rows": 2, "columns": [{"name": "question", "data_type": "text"}]},
        )


@pytest.mark.parametrize(
    "kind,value,invalid",
    [
        ("text", "text", None),
        ("integer", 12, True),
        ("float", 1.5, "1.5"),
        ("boolean", False, 0),
        ("json", {"nested": [1, True]}, []),
        ("array", ["a", 2], {}),
        ("datetime", "2026-10-28T00:00:00Z", "not-a-date"),
    ],
)
def test_generated_values_retain_the_declared_type(kind, value, invalid):
    from model_hub.services.row_generation import GenerationOutputError, validate_output

    config = {"num_rows": 1, "columns": [{"name": "value", "data_type": kind}]}
    assert validate_output(json.dumps({"rows": [{"value": value}]}), config) == [
        {"value": value}
    ]
    with pytest.raises(GenerationOutputError):
        validate_output(json.dumps({"rows": [{"value": invalid}]}), config)


KINDS = ["text", "integer", "float", "boolean", "json", "array", "datetime"]
# Falsy, zero and empty structured values are real values, not missing cells.
GENERATED = [
    {
        "text": "",
        "integer": 0,
        "float": 0.0,
        "boolean": False,
        "json": {},
        "array": [],
        "datetime": "2026-10-28T00:00:00Z",
    },
    {
        "text": "naïve ✓",
        "integer": 12,
        "float": 2,
        "boolean": True,
        "json": {"nested": [1, True, None], "label": "é"},
        "array": ["a", 2, False, None],
        "datetime": "2026-10-28T09:30:00+05:30",
    },
]
# Canonical dataset encodings: lowercase booleans, float text and JSON documents.
STORED = [
    ["", "0", "0.0", "false", "{}", "[]", "2026-10-28T00:00:00Z"],
    [
        "naïve ✓",
        "12",
        "2.0",
        "true",
        '{"nested": [1, true, null], "label": "é"}',
        '["a", 2, false, null]',
        "2026-10-28T09:30:00+05:30",
    ],
]


@pytest.fixture
def typed_grid(auth_client):
    response = auth_client.post(
        CREATE,
        {
            "dataset_name": "Typed generation target",
            "number_of_rows": 1,
            "columns": [
                {"name": kind, "type": kind, "role": "metadata"} for kind in KINDS
            ],
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    return Dataset.objects.get(id=response.json()["result"]["dataset_id"])


@pytest.mark.django_db
def test_generated_values_are_stored_and_read_in_canonical_encodings(
    auth_client, typed_grid, monkeypatch
):
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    monkeypatch.setattr(
        RunPrompt,
        "litellm_response",
        lambda *a, **k: (json.dumps({"rows": GENERATED}), {}),
    )
    config = {
        "generation_mode": "append",
        "model": "gpt-4o-mini",
        "num_rows": 2,
        "columns": [
            {"name": kind, "data_type": kind, "description": kind, "property": []}
            for kind in KINDS
        ],
        "dataset": {
            "name": typed_grid.name,
            "description": "Typed values",
            "objective": "",
            "patterns": "",
        },
    }
    original = set(Row.objects.filter(dataset=typed_grid).values_list("id", flat=True))
    first = auth_client.post(
        path(typed_grid, "add_synthetic_data"), config, format="json"
    )
    assert first.status_code == 200, first.content
    second = auth_client.put(
        path(typed_grid, "update-synthetic-config"),
        {**config, "regenerate": True},
        format="json",
    )
    assert second.status_code == 200, second.content
    assert original <= set(
        Row.objects.filter(dataset=typed_grid).values_list("id", flat=True)
    )
    generated = [
        str(row)
        for row in Row.objects.filter(dataset=typed_grid)
        .exclude(id__in=original)
        .order_by("order")
        .values_list("id", flat=True)
    ]
    assert len(generated) == 4

    columns = {
        column.name: str(column.id)
        for column in Column.objects.filter(dataset=typed_grid, deleted=False)
    }
    stored = {
        (str(row), str(column)): value
        for row, column, value in Cell.objects.filter(
            dataset=typed_grid, row_id__in=generated
        ).values_list("row_id", "column_id", "value")
    }
    assert len(stored) == len(generated) * len(KINDS)
    expected = STORED * 2
    assert [
        [stored[(row, columns[kind])] for kind in KINDS] for row in generated
    ] == expected

    table = auth_client.get(
        path(typed_grid, "get-dataset-table"),
        {"current_page_index": 0, "page_size": 25},
    )
    assert table.status_code == 200, table.content
    read = {row["row_id"]: row for row in table.json()["result"]["table"]}
    assert [
        [read[row][columns[kind]]["cell_value"] for kind in KINDS] for row in generated
    ] == expected

    # Structured cells round-trip to the values the model returned.
    for row, values in zip(generated, GENERATED * 2, strict=True):
        for kind in ("json", "array"):
            assert json.loads(read[row][columns[kind]]["cell_value"]) == values[kind]
