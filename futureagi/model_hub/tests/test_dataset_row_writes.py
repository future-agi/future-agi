"""Dataset writes must not acknowledge values that they silently discard."""

from unittest.mock import MagicMock, patch

import pytest
from django.db import DatabaseError

from ai_tools.base import ToolContext
from ai_tools.registry import registry as falcon_registry
from mcp_server.api_executor import APIExecutionError, MCPRequestContext, executor
from mcp_server.generated_registry import registry as mcp_registry
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

pytestmark = pytest.mark.django_db


def cells(value):
    return {"cells": [{"column_name": "input", "value": value}]}


@pytest.fixture
def dataset(user, workspace):
    dataset = Dataset.objects.create(
        name="Row write regression",
        organization=user.organization,
        workspace=workspace,
        user=user,
    )
    column = Column.objects.create(
        dataset=dataset,
        name="input",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, row=row, column=column, value="existing")
    return dataset


@pytest.fixture
def billing():
    with patch(
        "model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request",
        return_value=MagicMock(status="created"),
    ) as billing:
        yield billing


@pytest.fixture(params=["rest", "falcon", "mcp"])
def write_rows(request, auth_client, user, workspace, dataset, billing):
    """Exercise the actual public endpoint and both generated tool entry points."""

    def write(rows):
        arguments = {"dataset_id": str(dataset.id), "rows": rows}
        if request.param == "rest":
            response = auth_client.post(
                f"/model-hub/develops/{dataset.id}/add_rows/",
                {"rows": rows},
                format="json",
            )
            return response.status_code, str(response.data)
        if request.param == "falcon":
            result = falcon_registry.get("add_dataset_rows").run(
                arguments,
                ToolContext(
                    user=user, organization=user.organization, workspace=workspace
                ),
            )
            if result.is_error:
                return (
                    400 if result.error_code == "VALIDATION_ERROR" else 500
                ), result.content
            return 200, result.content
        try:
            result = executor.execute_sync(
                mcp_registry.get("add_dataset_rows"),
                arguments,
                MCPRequestContext(user, user.organization, workspace),
            )
        except APIExecutionError as exc:
            return exc.status_code, str(exc)
        return 200, str(result)

    return write


def saved_values(dataset):
    return list(
        Cell.objects.filter(dataset=dataset)
        .order_by("row__order")
        .values_list("value", flat=True)
    )


@pytest.mark.parametrize(
    "invalid",
    [
        {"input": "legacy populated row"},
        {"cells": [{"column_name": "input", "values": "misspelled value"}]},
        {"cells": [{"columnName": "input", "value": "wrong alias"}]},
        {"cells": [{"column_name": "missing", "value": "must not disappear"}]},
        {
            "cells": [
                {"column_name": "input", "value": "first"},
                {"column_name": "input", "value": "lost second"},
            ]
        },
        {"cells": "not a list"},
        {"cells": [], "input": "unexpected extra data"},
    ],
)
def test_invalid_batch_preserves_existing_data_and_does_not_charge(
    write_rows, dataset, billing, invalid
):
    status, content = write_rows([cells("valid first row"), invalid])
    assert status == 400, content
    assert Row.objects.filter(dataset=dataset).count() == 1
    assert saved_values(dataset) == ["existing"]
    billing.assert_not_called()


@pytest.mark.parametrize("column_state", ["deleted", "experiment", "other_dataset"])
def test_unwritable_column_rejects_whole_batch(
    write_rows, dataset, billing, user, workspace, column_state
):
    owner = dataset
    if column_state == "other_dataset":
        owner = Dataset.objects.create(
            name="Other", organization=user.organization, workspace=workspace, user=user
        )
    Column.objects.create(
        dataset=owner,
        name="unwritable",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT.value
        if column_state == "experiment"
        else SourceChoices.OTHERS.value,
        deleted=column_state == "deleted",
    )
    status, content = write_rows(
        [cells("first"), {"cells": [{"column_name": "unwritable", "value": "lost"}]}]
    )
    assert status == 400, content
    assert "unwritable" in content
    assert saved_values(dataset) == ["existing"]
    assert Row.objects.filter(dataset=dataset).count() == 1
    billing.assert_not_called()


def test_75_rows_preserve_exact_values(write_rows, dataset):
    expected = [f"trace-{i}: input with unicode \u2713" for i in range(75)]
    status, content = write_rows([cells(value) for value in expected])
    assert status == 200, content
    assert Row.objects.filter(dataset=dataset).count() == 76
    assert saved_values(dataset) == ["existing", *expected]


def test_intentional_empty_rows_and_falsy_values_remain_valid(write_rows, dataset):
    status, content = write_rows(
        [{}, {"cells": []}, cells(""), cells(None), cells(0), cells(False)]
    )
    assert status == 200, content
    assert saved_values(dataset) == ["existing", "", "", "", None, "0", "False"]


def test_legacy_sdk_row_order_remains_accepted(write_rows, dataset):
    # The product's CURL_ADD_ROWS_REQUEST includes order; the API has always
    # appended in list order instead of using these values as stored positions.
    status, content = write_rows(
        [
            {"order": 99, **cells("first")},
            {"order": 1, **cells("second")},
        ]
    )
    assert status == 200, content
    assert saved_values(dataset) == ["existing", "first", "second"]
    assert list(
        Row.objects.filter(dataset=dataset)
        .order_by("order")
        .values_list("order", flat=True)
    ) == [0, 1, 2]


def test_failed_second_write_rolls_back_the_batch(write_rows, dataset):
    real_create = Cell.objects.bulk_create
    calls = 0

    def fail_second(objects, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise DatabaseError("injected persistence failure")
        return real_create(objects, **kwargs)

    with patch.object(Cell.objects, "bulk_create", side_effect=fail_second):
        status, content = write_rows([cells("first"), cells("second")])
    assert status == 500, content
    assert calls == 2
    assert Row.objects.filter(dataset=dataset).count() == 1
    assert saved_values(dataset) == ["existing"]
