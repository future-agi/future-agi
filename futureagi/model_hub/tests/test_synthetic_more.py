"""Selected examples must be scoped before they are sent to a model."""

import json
import uuid

import pytest

from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.tests import test_generate_rows as base

grid = base.grid
config = base.config
model = base.model
path = base.path


@pytest.fixture
def examples(grid):
    selected = Row.objects.get(dataset=grid)
    Cell.objects.filter(row=selected).update(value="selected-example")
    other = Row.objects.create(dataset=grid, order=1)
    column = Column.objects.get(dataset=grid)
    Cell.objects.create(dataset=grid, row=other, column=column, value="not-selected")
    return selected, other


@pytest.mark.django_db
def test_selected_examples_reach_model_and_saved_config(
    auth_client, grid, config, model, examples
):
    selected, other = examples
    config["reference_row_ids"] = [str(selected.id)]
    result = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert result.status_code == 200, result.content
    payload = json.loads(model[0].messages[-1]["content"])
    assert payload["reference_rows"] == [
        {"row_id": str(selected.id), "values": {"question": "selected-example"}}
    ]
    assert "not-selected" not in json.dumps(payload)
    saved = auth_client.get(
        path(grid, "synthetic-config"), {"generation_mode": "append"}
    )
    assert saved.json()["result"]["data"] == config
    result = auth_client.put(
        path(grid, "update-synthetic-config"),
        {**config, "regenerate": True},
        format="json",
    )
    assert result.status_code == 200, result.content
    assert model[0].messages == model[1].messages
    assert Row.objects.filter(dataset=grid).count() == 6
    assert list(
        Cell.objects.filter(row__in=[selected, other])
        .order_by("row__order")
        .values_list("value", flat=True)
    ) == ["selected-example", "not-selected"]


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["missing", "different-dataset", "deleted"])
def test_unavailable_examples_are_refused_before_completion(
    auth_client, grid, config, model, examples, kind
):
    selected, _ = examples
    if kind == "missing":
        row_id = uuid.uuid4()
    elif kind == "deleted":
        Row.objects.filter(id=selected.id).update(deleted=True)
        row_id = selected.id
    else:
        target = Dataset.objects.create(
            name="Other dataset",
            organization=grid.organization,
            workspace=grid.workspace,
        )
        row_id = Row.objects.create(dataset=target, order=0).id
    config["reference_row_ids"] = [str(row_id)]
    response = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert response.status_code == 404, response.content
    assert model == []
    grid.refresh_from_db()
    assert not (grid.dataset_config or {}).get("row_generation")


@pytest.mark.django_db
@pytest.mark.parametrize("ids", [[], ["duplicate", "duplicate"], ["too-many"] * 51])
def test_reference_selection_must_be_nonempty_unique_and_bounded(
    auth_client, grid, config, model, examples, ids
):
    selected, _ = examples
    config["reference_row_ids"] = [str(selected.id) for _ in ids]
    response = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert response.status_code == 400, response.content
    assert model == []


@pytest.mark.django_db
def test_changed_source_during_generation_does_not_append(
    auth_client, grid, config, monkeypatch, examples
):
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    selected, _ = examples
    config["reference_row_ids"] = [str(selected.id)]

    def complete(*args, **kwargs):
        Cell.objects.filter(row=selected).update(value="edited-during-generation")
        return json.dumps({"rows": [{"question": "first"}, {"question": "second"}]}), {}

    monkeypatch.setattr(RunPrompt, "litellm_response", complete)
    response = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert response.status_code == 400, response.content
    assert Row.objects.filter(dataset=grid).count() == 2


@pytest.mark.django_db
def test_oversized_references_are_refused_before_model(
    auth_client, grid, config, model, examples
):
    selected, _ = examples
    Cell.objects.filter(row=selected).update(value="x" * 50001)
    config["reference_row_ids"] = [str(selected.id)]
    response = auth_client.post(path(grid, "add_synthetic_data"), config, format="json")
    assert response.status_code == 400, response.content
    assert model == []
    assert Row.objects.filter(dataset=grid).count() == 2
