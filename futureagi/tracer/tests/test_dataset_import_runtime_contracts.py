"""Observe import actions share their documented and runtime request validators."""

from unittest.mock import patch

import pytest

from model_hub.models.develop_dataset import Column, Dataset
from tracer.serializers.dataset import (
    AddToExistingDatasetObserveSerializer,
    AddToNewDatasetObserveSerializer,
)
from tracer.services.dataset_sources import DatasetSourcePreviewSerializer
from tracer.views.dataset import DatasetView

ACTIONS = [
    ("preview_sources", DatasetSourcePreviewSerializer),
    ("add_to_new_dataset", AddToNewDatasetObserveSerializer),
    ("add_to_existing_dataset", AddToExistingDatasetObserveSerializer),
]


@pytest.mark.parametrize("action, serializer_class", ACTIONS)
def test_import_input_contract_is_runtime_backed(action, serializer_class):
    contract = getattr(DatasetView, action)._swagger_auto_schema["post"]
    assert contract["request_body"] is serializer_class
    assert contract.get("runtime_request_validation") is True


@pytest.mark.django_db
@pytest.mark.parametrize("action, serializer_class", ACTIONS)
@pytest.mark.parametrize("body", [{}, {"project": "not-a-uuid"}, []])
def test_invalid_import_body_is_rejected_before_source_or_destination_access(
    auth_client, action, serializer_class, body
):
    before = (Dataset.no_workspace_objects.count(), Column.no_workspace_objects.count())
    with (
        patch("tracer.views.dataset.get_reader") as reader,
        patch("tracer.views.dataset.preview_sources") as preview,
        patch("tracer.views.dataset.create_new_cells") as submit,
    ):
        response = auth_client.post(f"/tracer/dataset/{action}/", body, format="json")
    assert response.status_code == 400
    assert (
        Dataset.no_workspace_objects.count(),
        Column.no_workspace_objects.count(),
    ) == before
    reader.assert_not_called()
    preview.assert_not_called()
    submit.assert_not_called()
