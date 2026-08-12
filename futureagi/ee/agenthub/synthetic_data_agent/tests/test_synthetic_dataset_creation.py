"""End-to-end test for the ``SyntheticDataAgent``-backed dataset creation flow.

Extracted from ``futureagi/model_hub/tests/test_dataset_creation_import_api.py``
because the ``/model-hub/develops/create-synthetic-dataset/`` endpoint runs
``SyntheticDataAgent`` (``ee/agenthub/synthetic_data_agent``); the OSS
short-circuit returns a "feature not available" response, so the assertions
here can only hold when the EE package is installed.

Helpers are duplicated (rather than imported) because keeping the source of
truth co-located with the test avoids a cross-tree import that would break
whenever the OSS repo is checked out standalone.
"""

from types import SimpleNamespace

import pytest
from model_hub.models.develop_dataset import Column, Dataset, Row
from rest_framework import status


class _SuccessfulResourceCallLog:
    status = "created"

    def save(self):
        return None


def _patch_usage(monkeypatch, module_path):
    calls = []

    def record_usage(*args, **kwargs):
        calls.append((args, kwargs))
        return _SuccessfulResourceCallLog()

    monkeypatch.setattr(
        f"{module_path}.log_and_deduct_cost_for_resource_request",
        record_usage,
    )
    return calls


def _synthetic_create_payload(name, num_rows=10, columns=None, regenerate=None):
    payload = {
        "num_rows": num_rows,
        "columns": columns
        or [
            {
                "name": "answer",
                "data_type": "text",
                "description": "Answer",
                "property": "answer",
            }
        ],
        "dataset": {
            "name": name,
            "description": "Dataset",
            "objective": "Generate rows",
            "patterns": "",
        },
    }
    if regenerate is not None:
        payload["regenerate"] = regenerate
    return payload


def _allow_synthetic_entitlement(monkeypatch):
    monkeypatch.setattr(
        "ee.usage.services.entitlements.Entitlements.check_feature",
        lambda *args, **kwargs: SimpleNamespace(allowed=True, reason=""),
    )


@pytest.mark.django_db
def test_create_synthetic_dataset_sets_workspace_and_does_not_charge_invalid_request(
    auth_client, workspace, monkeypatch
):
    _allow_synthetic_entitlement(monkeypatch)
    usage_calls = _patch_usage(
        monkeypatch,
        "model_hub.views.datasets.create.synthetic",
    )
    queued_tasks = []
    monkeypatch.setattr(
        "model_hub.views.datasets.create.synthetic.create_synthetic_dataset.delay",
        lambda *args, **kwargs: queued_tasks.append((args, kwargs)),
    )

    invalid_response = auth_client.post(
        "/model-hub/develops/create-synthetic-dataset/",
        _synthetic_create_payload("Invalid Synthetic Dataset", num_rows=9),
        format="json",
    )

    assert invalid_response.status_code == status.HTTP_400_BAD_REQUEST
    assert usage_calls == []
    assert queued_tasks == []

    response = auth_client.post(
        "/model-hub/develops/create-synthetic-dataset/",
        _synthetic_create_payload("Workspace Synthetic Dataset"),
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    dataset_id = response.json()["result"]["data"]["id"]
    dataset = Dataset.no_workspace_objects.get(id=dataset_id)
    assert dataset.workspace_id == workspace.id
    assert dataset.synthetic_dataset_config["dataset"]["name"] == dataset.name
    assert Row.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 10
    assert (
        Column.no_workspace_objects.filter(dataset=dataset, deleted=False).count() == 1
    )
    assert len(usage_calls) == 2
    assert queued_tasks
