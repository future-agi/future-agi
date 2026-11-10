"""Dataset operation defaults agree with the existing Run Prompt default."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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
    metadata = {"column_id": "source-column", "instruction": "Extract companies"}
    if model is not None:
        metadata["language_model_id"] = model

    dynamic_columns.RerunOperationView()._rerun_entity_extraction(
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
    )
