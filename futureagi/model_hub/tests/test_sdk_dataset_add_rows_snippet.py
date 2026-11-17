"""The "Add data using SDK" examples for an existing dataset.

The add-rows examples write the dataset's own data columns: the server refuses a
row that names a column the dataset does not have. A dataset without data
columns gets the add-columns example instead, and every language tab quotes
dataset and column names so any name gives code that parses.
"""

import ast
import json
import re
import shlex
import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub import constants
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Column, Dataset

AWKWARD_NAME = 'It\'s "quoted" \\ and\nsplit'
JSON_STRING = r'"(?:[^"\\\n]|\\.)*"'

SAMPLE_COLUMNS = [
    ("question", DataTypeChoices.TEXT.value),
    ("score", DataTypeChoices.INTEGER.value),
    ("rate", DataTypeChoices.FLOAT.value),
    ("helpful", DataTypeChoices.BOOLEAN.value),
    ("asked_at", DataTypeChoices.DATETIME.value),
    ("meta", DataTypeChoices.JSON.value),
    ("tags", DataTypeChoices.ARRAY.value),
]


def add_rows_code(columns, dataset_name="Support questions"):
    python, typescript, curl = constants.get_add_rows_code(
        dataset_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        dataset_name=dataset_name,
        columns=columns,
        api_key=constants.SDK_API_KEY_PLACEHOLDER,
        secret_key=constants.SDK_SECRET_KEY_PLACEHOLDER,
    )
    return python, typescript, curl


def _literal(node):
    try:
        return ast.literal_eval(node)
    except ValueError:
        return ast.unparse(node)


def python_calls(code, name):
    """Literal arguments of every call to ``name`` in a Python example."""
    calls = []
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "attr", getattr(node.func, "id", None))
        if called == name:
            calls.append(
                (
                    [_literal(arg) for arg in node.args],
                    {kw.arg: _literal(kw.value) for kw in node.keywords},
                )
            )
    return calls


def python_cells(code):
    return [
        (kwargs["column_name"], kwargs["value"])
        for _, kwargs in python_calls(code, "Cell")
    ]


def typescript_cells(code):
    pattern = rf"createCell\(\{{ column_name: ({JSON_STRING}), value: (.*?) \}}\)"
    return [
        (json.loads(name), json.loads(value))
        for name, value in re.findall(pattern, code)
    ]


def curl_payloads(code):
    words = shlex.split(code, comments=True)
    return [
        json.loads(words[index + 1])
        for index, word in enumerate(words)
        if word == "--data"
    ]


def assert_one_curl_command(code):
    """Every line of the command before --data ends in a bare line continuation."""
    lines = code.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("curl "))
    end = next(i for i, line in enumerate(lines) if line.lstrip().startswith("--data "))
    assert all(line.endswith(" \\") for line in lines[start:end]), lines[start:end]


def curl_cells(code):
    (payload,) = curl_payloads(code)
    cells = payload["rows"][0]["cells"]
    return [(cell["column_name"], cell["value"]) for cell in cells]


def test_add_rows_examples_write_the_dataset_columns():
    python, typescript, curl = add_rows_code(SAMPLE_COLUMNS)
    names = [name for name, _ in SAMPLE_COLUMNS]

    for cells in (python_cells(python), typescript_cells(typescript), curl_cells(curl)):
        assert [name for name, _ in cells] == names
        values = dict(cells)
        assert isinstance(values["question"], str) and values["question"]
        assert values["score"] == int(values["score"])
        assert isinstance(values["rate"], float)
        assert values["helpful"] is True
        assert isinstance(values["asked_at"], str) and values["asked_at"]
        assert isinstance(values["meta"], dict)
        assert isinstance(values["tags"], list)
    for code in (python, typescript, curl):
        assert "user_query" not in code
        assert "add_columns" not in code and "addColumns" not in code
    assert python_calls(python, "get_dataset_config")[0][0] == ["Support questions"]
    assert "createIfMissing: false" in typescript
    assert "/add_rows/" in curl
    assert_one_curl_command(curl)


def test_columns_without_a_sample_value_are_left_empty_and_named():
    columns = [
        ("question", DataTypeChoices.TEXT.value),
        ("photo", DataTypeChoices.IMAGE.value),
        ("clip", DataTypeChoices.AUDIO.value),
    ]
    python, typescript, curl = add_rows_code(columns)

    for cells in (python_cells(python), typescript_cells(typescript), curl_cells(curl)):
        assert dict(cells)["photo"] == ""
        assert dict(cells)["clip"] == ""
        assert dict(cells)["question"] != ""
    assert "image column: add its value" in python
    assert "audio column: add its value" in typescript
    comment = curl.splitlines()[0]
    assert comment.startswith("#") and "audio" in comment and "image" in comment


def test_a_dataset_with_only_file_columns_still_sends_a_cell():
    python, typescript, curl = add_rows_code([("photo", DataTypeChoices.IMAGE.value)])

    for cells in (python_cells(python), typescript_cells(typescript), curl_cells(curl)):
        assert cells == [("photo", "")]


def test_a_dataset_without_columns_gets_the_add_columns_example():
    python, typescript, curl = add_rows_code([])

    assert ".add_columns(" in python and ".add_rows(" in python
    assert "addColumns(" in typescript and "addRows(" in typescript
    assert "/add_columns/" in curl and "/add_rows/" in curl
    for code, mark in ((python, "#"), (typescript, "//"), (curl, "#")):
        assert code.lstrip().startswith(f"{mark} This dataset has no columns yet")
    compile(python, "<add rows example>", "exec")
    assert len(curl_payloads(curl)) == 2


def test_python_tab_quotes_awkward_names():
    python, _, _ = add_rows_code(
        [(AWKWARD_NAME, DataTypeChoices.TEXT.value)], dataset_name=AWKWARD_NAME
    )
    empty_python, _, _ = add_rows_code([], dataset_name=AWKWARD_NAME)
    add_columns_python = constants.get_python_add_columns_code(
        AWKWARD_NAME,
        constants.SDK_API_KEY_PLACEHOLDER,
        constants.SDK_SECRET_KEY_PLACEHOLDER,
    )

    assert python_calls(python, "get_dataset_config")[0][0] == [AWKWARD_NAME]
    assert python_cells(python)[0][0] == AWKWARD_NAME
    for code in (empty_python, add_columns_python):
        assert python_calls(code, "get_dataset_config")[0][0] == [AWKWARD_NAME]
        assert python_calls(code, "DatasetConfig")[0][1]["name"] == AWKWARD_NAME


def test_typescript_tab_quotes_awkward_names():
    _, typescript, _ = add_rows_code(
        [(AWKWARD_NAME, DataTypeChoices.TEXT.value)], dataset_name=AWKWARD_NAME
    )
    _, _, add_columns_typescript, _ = constants.get_curl_ts_code(
        dataset_id=uuid.uuid4(),
        api_key=constants.SDK_API_KEY_PLACEHOLDER,
        secret_key=constants.SDK_SECRET_KEY_PLACEHOLDER,
        dataset_name=AWKWARD_NAME,
    )

    (opened,) = re.findall(
        rf"Dataset\.open\(({JSON_STRING}), \{{ createIfMissing: false \}}\)", typescript
    )
    assert json.loads(opened) == AWKWARD_NAME
    assert typescript_cells(typescript)[0][0] == AWKWARD_NAME
    (named,) = re.findall(rf"const dsName = ({JSON_STRING});", add_columns_typescript)
    assert json.loads(named) == AWKWARD_NAME


def test_curl_tab_quotes_awkward_column_names():
    _, _, curl = add_rows_code(
        [(AWKWARD_NAME, DataTypeChoices.TEXT.value)], dataset_name=AWKWARD_NAME
    )

    assert curl_cells(curl)[0][0] == AWKWARD_NAME
    assert_one_curl_command(curl)


class _SuccessfulResourceCallLog:
    status = "created"

    def save(self):
        return None


def _served_code(auth_client, dataset):
    with patch(
        "model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request",
        return_value=_SuccessfulResourceCallLog(),
    ):
        response = auth_client.post(
            "/model-hub/develops/add_rows_sdk/",
            {"dataset_id": str(dataset.id)},
            format="json",
        )
    assert response.status_code == status.HTTP_200_OK, response.json()
    return response.json()["result"]["code"]


def _dataset(user, workspace, name="Add rows example dataset"):
    return Dataset.objects.create(
        name=name,
        organization=user.organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )


@pytest.mark.django_db
def test_served_add_rows_examples_use_the_dataset_data_columns(
    auth_client, user, workspace
):
    dataset = _dataset(user, workspace)
    made = {
        name: Column.objects.create(
            name=name, data_type=data_type, source=source, dataset=dataset
        )
        for name, data_type, source in [
            ("answer", DataTypeChoices.TEXT.value, SourceChoices.OTHERS.value),
            ("question", DataTypeChoices.TEXT.value, SourceChoices.OTHERS.value),
            ("photo", DataTypeChoices.IMAGE.value, SourceChoices.OTHERS.value),
            ("grade", DataTypeChoices.TEXT.value, SourceChoices.EVALUATION.value),
        ]
    }
    dataset.column_order = [
        str(made[name].id) for name in ("question", "grade", "answer", "photo")
    ]
    dataset.save(update_fields=["column_order"])

    code = _served_code(auth_client, dataset)

    expected = ["question", "answer", "photo"]
    for cells in (
        python_cells(code["python_add_row"]),
        typescript_cells(code["typescript_add_row"]),
        curl_cells(code["curl_add_row"]),
    ):
        assert [name for name, _ in cells] == expected
    assert str(dataset.id) in code["curl_add_row"]
    assert "user_query" in code["python_add_col"]


@pytest.mark.django_db
def test_served_add_rows_example_for_a_dataset_without_columns(
    auth_client, user, workspace
):
    dataset = _dataset(user, workspace, name="Empty example dataset")

    code = _served_code(auth_client, dataset)

    assert ".add_columns(" in code["python_add_row"]
    assert "addColumns(" in code["typescript_add_row"]
    assert "/add_columns/" in code["curl_add_row"]
