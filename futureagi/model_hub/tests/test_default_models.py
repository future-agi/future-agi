"""Dataset operation defaults agree with the existing Run Prompt default."""

import ast
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework import status

from model_hub.models.choices import DataTypeChoices, ModelTypes, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.run_prompt import PromptVersion
from model_hub.serializers.contracts import (
    ClassifyColumnRequestSerializer,
    ExtractEntitiesRequestSerializer,
)
from model_hub.views import dynamic_columns


@pytest.mark.parametrize(
    ("serializer_class", "operation_data"),
    [
        (ClassifyColumnRequestSerializer, {"labels": ["positive", "negative"]}),
        (ExtractEntitiesRequestSerializer, {"instruction": "Extract companies"}),
    ],
)
@pytest.mark.parametrize("model", [None, "custom-model"])
def test_dataset_operation_model_default(serializer_class, operation_data, model):
    data = {
        "column_id": "11111111-1111-4111-8111-111111111111",
        **operation_data,
    }
    if model is not None:
        data["language_model_id"] = model
    serializer = serializer_class(data=data)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["language_model_id"] == (model or "gpt-4o")


@pytest.mark.parametrize(
    ("method", "view_class", "processor", "operation_data"),
    [
        (
            "_preview_classify",
            dynamic_columns.ClassifyColumnView,
            "_classify_cell",
            {"labels": ["positive", "negative"]},
        ),
        (
            "_preview_extract_entities",
            dynamic_columns.ExtractEntitiesView,
            "_extract_entities",
            {"instruction": "Extract companies"},
        ),
    ],
)
@pytest.mark.parametrize("model", [None, "custom-model"])
def test_dataset_preview_model_default(
    monkeypatch, method, view_class, processor, operation_data, model
):
    cell = SimpleNamespace(value="Sample company")
    process = Mock(return_value=("result", {}))
    monkeypatch.setattr(dynamic_columns.Cell.objects, "get", Mock(return_value=cell))
    monkeypatch.setattr(view_class, processor, process)
    config = {"column_id": "column-1", **operation_data}
    if model is not None:
        config["language_model_id"] = model

    view = dynamic_columns.PreviewDatasetOperationView()
    getattr(view, method)(config, [SimpleNamespace(id="row-1")], "org-1", object())

    process.assert_called_once_with(
        cell=cell, model=model or "gpt-4o", **operation_data
    )


@pytest.mark.parametrize("model", [None, "custom-model"])
def test_entity_extraction_rerun_model_default(monkeypatch, model):
    enqueue = Mock()
    monkeypatch.setattr(dynamic_columns.extract_async, "delay", enqueue)
    # The rerun enqueues after commit; run the callback now, with no database.
    monkeypatch.setattr(
        dynamic_columns.transaction,
        "on_commit",
        lambda callback, *args, **kwargs: callback(),
    )
    metadata = {"column_id": "source-column", "instruction": "Extract companies"}
    if model is not None:
        metadata["language_model_id"] = model

    view = dynamic_columns.RerunOperationView()
    view.row_ids = ["row-1"]
    view._rerun_entity_extraction(
        SimpleNamespace(id="result-column"), metadata, "dataset-1"
    )

    enqueue.assert_called_once_with(
        "source-column",
        "Extract companies",
        model or "gpt-4o",
        5,
        "dataset-1",
        "result-column",
        True,
        row_ids=["row-1"],
    )


@pytest.mark.django_db
@pytest.mark.parametrize("model", [None, "custom-model"])
def test_entity_extraction_column_model_default(
    auth_client, user, workspace, monkeypatch, model
):
    enqueue = Mock()
    monkeypatch.setattr(dynamic_columns.extract_async, "delay", enqueue)
    dataset = Dataset.no_workspace_objects.create(
        name="Entity extraction dataset",
        organization=user.organization,
        workspace=workspace,
        user=user,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
        column_config={},
    )
    source_column = Column.objects.create(
        name="text",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        dataset=dataset,
    )
    payload = {
        "column_id": str(source_column.id),
        "instruction": "Extract companies",
        "new_column_name": "companies",
    }
    # Without language_model_id the request serializer supplies the default.
    if model is not None:
        payload["language_model_id"] = model

    response = auth_client.post(
        f"/model-hub/datasets/{dataset.id}/extract-entities/",
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    enqueue.assert_called_once()
    assert enqueue.call_args.args[2] == (model or "gpt-4o")
    new_column_id = response.json()["result"]["new_column_id"]
    new_column = Column.all_objects.get(id=new_column_id)
    assert new_column.metadata["language_model_id"] == (model or "gpt-4o")


@pytest.mark.django_db
@pytest.mark.parametrize("model", [None, "custom-model"])
def test_entity_extraction_rerun_with_new_config_model_default(
    auth_client,
    user,
    workspace,
    monkeypatch,
    django_capture_on_commit_callbacks,
    model,
):
    dataset = Dataset.no_workspace_objects.create(
        name="Entity rerun dataset",
        organization=user.organization,
        workspace=workspace,
        user=user,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
        column_config={},
    )
    source = Column.objects.create(
        name="text",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        dataset=dataset,
    )
    stored = {"column_id": str(source.id), "instruction": "Extract names"}
    if model is not None:
        stored["language_model_id"] = model
    column = Column.objects.create(
        name="entities",
        data_type=DataTypeChoices.ARRAY.value,
        source=SourceChoices.EXTRACTED_ENTITIES.value,
        dataset=dataset,
        metadata=stored,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    Cell.objects.create(dataset=dataset, row=row, column=source, value="Acme Inc")
    Cell.objects.create(dataset=dataset, row=row, column=column, value="[]")
    enqueue = Mock()
    monkeypatch.setattr(dynamic_columns.extract_async, "delay", enqueue)

    # A new instruction with no model: the stored model, else the default.
    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client.post(
            f"/model-hub/columns/{column.id}/rerun-operation/",
            {
                "operation_type": "extract_entities",
                "config": {"instruction": "Extract companies"},
            },
            format="json",
        )

    assert response.status_code == 200, response.content
    column.refresh_from_db()
    assert column.metadata["language_model_id"] == (model or "gpt-4o")
    enqueue.assert_called_once_with(
        str(source.id),
        "Extract companies",
        model or "gpt-4o",
        5,
        dataset.id,
        str(column.id),
        True,
        row_ids=[str(row.id)],
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")
@pytest.mark.parametrize("model", [None, "custom-model"])
def test_langchain_sdk_code_model_default(auth_client, prompt_template, model):
    configuration = {} if model is None else {"model": model}
    PromptVersion.no_workspace_objects.create(
        original_template=prompt_template,
        template_version="v1",
        prompt_config_snapshot={
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
            ],
            "configuration": configuration,
        },
        variable_names={},
        is_draft=True,
    )

    response = auth_client.get(
        f"/model-hub/prompt-templates/{prompt_template.id}/get-sdk-code/langchain/"
    )

    assert response.status_code == 200, response.content
    code = response.json()["result"]["langchain"]
    chat = next(
        node.value
        for node in ast.parse(code).body
        if isinstance(node, ast.Assign) and node.targets[0].id == "chat"
    )
    model_arg = next(kw.value for kw in chat.keywords if kw.arg == "model")
    assert ast.literal_eval(model_arg) == (model or "gpt-4o")
