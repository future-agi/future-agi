"""Released dataset-create form names are normalized before strict validation."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from django.http import QueryDict

from model_hub.models.develop_dataset import Dataset
from model_hub.serializers.contracts import HuggingFaceDatasetCreateRequestSerializer
from tfc.utils.api_contracts import _validate_serializer

pytestmark = pytest.mark.django_db

URL = "/model-hub/develops/create-dataset-from-huggingface/"
MODULE = "model_hub.views.datasets.create.huggingface"


@pytest.fixture(scope="module", autouse=True)
def load_unscoped_urlconf():
    from django.urls import get_resolver
    from tfc.middleware.workspace_context import clear_workspace_context

    clear_workspace_context()
    get_resolver().url_patterns


def payload():
    return {
        "new_dataset_name": "Imported data",
        "huggingface_dataset_name": "example/sample",
        "huggingface_dataset_config": "default",
        "huggingface_dataset_split": "train",
        "model_type": "GenerativeLLM",
        "num_rows": 2,
    }


@pytest.mark.parametrize("form", [False, True])
def test_old_dataset_name_becomes_canonical_name_without_mutating_input(form):
    data = payload()
    if form:
        query = QueryDict(mutable=True)
        query.update(data)
        data = query
    serializer, errors, valid = _validate_serializer(
        HuggingFaceDatasetCreateRequestSerializer, data, reject_unknown_fields=True,
    )
    assert valid, errors
    assert serializer.validated_data["name"] == "Imported data"
    assert "new_dataset_name" not in serializer.validated_data
    assert data["new_dataset_name"] == "Imported data"
    assert "name" not in data


@pytest.fixture
def import_stubs():
    with ExitStack() as stack:
        stack.enter_context(patch(f"{MODULE}.log_and_deduct_cost_for_resource_request", None))
        stack.enter_context(patch(
            f"{MODULE}.CreateDatasetFromHuggingFaceView.get_huggingface_dataset_info",
            return_value={"num_rows": 2},
        ))
        preview = stack.enter_context(patch(
            f"{MODULE}.load_hf_dataset_with_retries",
            return_value={"features": [{"name": "text", "type": {"dtype": "string"}}]},
        ))
        activity = stack.enter_context(patch("tfc.temporal.drop_in.start_activity"))
        stack.enter_context(patch(f"{MODULE}.extra_available", return_value=False))
        yield preview, activity


@pytest.mark.parametrize("format_name", ["multipart", "json"])
def test_old_name_reaches_import_and_creates_named_dataset(
    auth_client, import_stubs, format_name,
):
    preview, activity = import_stubs
    response = auth_client.post(URL, payload(), format=format_name)
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["dataset_name"] == "Imported data"
    dataset = Dataset.objects.get(id=result["dataset_id"])
    assert dataset.name == "Imported data"
    preview.assert_called_once()
    activity.assert_called_once()


def test_preview_unavailable_remains_an_import_error_not_unknown_name(
    auth_client, import_stubs,
):
    preview, activity = import_stubs
    preview.side_effect = RuntimeError("Dataset preview unavailable")
    response = auth_client.post(URL, payload(), format="multipart")
    assert response.status_code == 400, response.data
    assert "Unknown field." not in str(response.data)
    preview.assert_called_once()
    activity.assert_not_called()
    assert not Dataset.objects.filter(name="Imported data").exists()


def test_conflicting_dataset_names_are_refused_before_import(auth_client, import_stubs):
    preview, activity = import_stubs
    response = auth_client.post(URL, {**payload(), "name": "Different name"}, format="json")
    assert response.status_code == 400, response.data
    assert "Use either name or new_dataset_name" in str(response.data)
    preview.assert_not_called()
    activity.assert_not_called()


def test_other_dataset_keys_remain_unknown(auth_client, import_stubs):
    preview, activity = import_stubs
    response = auth_client.post(URL, {**payload(), "unexpected": True}, format="multipart")
    assert response.status_code == 400, response.data
    assert "unexpected" in str(response.data)
    assert "Unknown field." in str(response.data)
    preview.assert_not_called()
    activity.assert_not_called()


def test_canonical_dataset_name_still_works(auth_client, import_stubs):
    data = payload()
    data["name"] = data.pop("new_dataset_name")
    response = auth_client.post(URL, data, format="json")
    assert response.status_code == 200, response.data
    assert response.data["result"]["dataset_name"] == data["name"]
