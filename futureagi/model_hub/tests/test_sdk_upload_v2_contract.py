"""SDK uploads preserve native row identity and explain version-only refusals."""

# Imported pytest fixtures are injected by name.
# ruff: noqa: F811

import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse

from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.native_experiment_runs import NativeExampleBinding
from model_hub.services.dataset_version_service import (
    DatasetVersionItemInput,
    create_dataset_version,
)
from model_hub.tests.test_dataset_creation_import_api import _patch_usage
from model_hub.tests.test_dataset_creation_import_api import (  # noqa: F401
    no_external_lookups,
    stub_eval_recommender,
    stub_url_resolution,
)
from model_hub.tests.test_experiment_create_admission import START, V2, payload
from model_hub.tests.test_experiment_create_dataset_version import (
    NOT_FROM_TABLE,
    ROWS_GONE,
    copied_values,
    counts,
    edit_every_cell,
    first_experiment,
    post,
    version_of,
)
from model_hub.tests.test_experiment_run_scope import (
    request_body,
    scope_world,  # noqa: F401
)
from model_hub.tests.test_experiment_version_params import (
    create_run,
    version_world,  # noqa: F401
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures(
        "urlconf_loaded_without_workspace_context", "no_external_lookups"
    ),
]


def test_uploaded_table_rows_with_bound_version_start_v2(
    auth_client, organization, workspace, user, monkeypatch
):
    _patch_usage(monkeypatch, "model_hub.views.datasets.create.empty_dataset")
    _patch_usage(monkeypatch, "model_hub.views.develop_dataset")
    created = auth_client.post(
        "/model-hub/develops/create-empty-dataset/",
        {
            "new_dataset_name": "SDK table rows with a bound version",
            "model_type": "GenerativeLLM",
            "row": 0,
            "is_sdk": True,
        },
        format="json",
    )
    assert created.status_code == 200, created.content
    dataset = Dataset.no_workspace_objects.get(
        id=created.json()["result"]["dataset_id"]
    )
    assert not Row.no_workspace_objects.filter(dataset=dataset, deleted=False).exists()

    columns = auth_client.post(
        f"/model-hub/develops/{dataset.id}/add_columns/",
        {
            "new_columns_data": [
                {"name": "question", "data_type": "text"},
                {"name": "answer", "data_type": "text"},
            ]
        },
        format="json",
    )
    assert columns.status_code == 200, columns.content
    examples = [("What is two plus two?", "Four"), ("What is three plus three?", "Six")]
    row_ids = [uuid.uuid4() for _ in examples]
    rows = auth_client.post(
        f"/model-hub/develops/{dataset.id}/add_rows/",
        {
            "rows": [
                {
                    "id": str(row_id),
                    "cells": [
                        {"column_name": "question", "value": question},
                        {"column_name": "answer", "value": answer},
                    ],
                }
                for row_id, (question, answer) in zip(row_ids, examples, strict=True)
            ]
        },
        format="json",
    )
    assert rows.status_code == 200, rows.content
    assert set(
        Row.no_workspace_objects.filter(dataset=dataset, deleted=False).values_list(
            "id", flat=True
        )
    ) == set(row_ids)

    uploaded = auth_client.post(
        reverse("dataset-version-list", kwargs={"dataset_id": dataset.id}),
        {
            "items": [
                {
                    "legacy_row_id": str(row_id),
                    "input": {"question": question},
                    "expected": {"answer": answer},
                }
                for row_id, (question, answer) in zip(row_ids, examples, strict=True)
            ]
        },
        format="json",
    )
    assert uploaded.status_code == 201, uploaded.content
    uploaded_example_ids = dict(
        DatasetVersionItem.no_workspace_objects.filter(
            dataset_version_id=uploaded.json()["result"]["id"], deleted=False
        ).values_list("legacy_row_id", "example_id")
    )
    assert set(uploaded_example_ids) == set(row_ids)
    actor = {
        "client": auth_client,
        "org": organization,
        "workspace": workspace,
        "user": user,
        "exp": {"dataset": dataset},
    }
    body = payload(actor)
    prior_binding_ids = set(NativeExampleBinding.objects.values_list("pk", flat=True))
    with patch(START, return_value="admitted") as start:
        response = auth_client.post(V2, body, format="json")
    assert response.status_code == 200, response.content
    start.assert_called_once()
    bindings = list(
        NativeExampleBinding.objects.exclude(pk__in=prior_binding_ids).select_related(
            "version_item"
        )
    )
    assert len(bindings) == len(row_ids)
    assert {binding.source_row_id for binding in bindings} == set(row_ids)
    for binding in bindings:
        assert (
            binding.version_item.example_id
            == uploaded_example_ids[binding.source_row_id]
            == DatasetVersionItem.example_id_for_legacy_row(binding.source_row_id)
        )


def test_version_only_dataset_is_refused_for_v2_and_rolled_back(
    auth_client, organization, workspace, user, monkeypatch
):
    _patch_usage(monkeypatch, "model_hub.views.datasets.create.empty_dataset")
    created = auth_client.post(
        "/model-hub/develops/create-empty-dataset/",
        {
            "new_dataset_name": "SDK version-only dataset",
            "model_type": "GenerativeLLM",
            "row": 0,
            "is_sdk": True,
        },
        format="json",
    )
    assert created.status_code == 200, created.content
    dataset = Dataset.no_workspace_objects.get(
        id=created.json()["result"]["dataset_id"]
    )
    uploaded = auth_client.post(
        reverse("dataset-version-list", kwargs={"dataset_id": dataset.id}),
        {
            "items": [
                {"input": {"question": "hello"}, "expected": {"answer": "world"}},
                {"input": {"question": "goodbye"}, "expected": {"answer": "world"}},
            ]
        },
        format="json",
    )
    assert uploaded.status_code == 201, uploaded.content
    assert DatasetVersionItem.no_workspace_objects.filter(
        dataset_version_id=uploaded.json()["result"]["id"],
        legacy_row_id__isnull=True,
        deleted=False,
    ).count() == 2
    assert not Row.no_workspace_objects.filter(dataset=dataset, deleted=False).exists()
    actor = {
        "client": auth_client,
        "org": organization,
        "workspace": workspace,
        "user": user,
        "exp": {"dataset": dataset},
    }
    body = payload(actor)
    with patch(START) as start:
        response = auth_client.post(V2, body, format="json")
    assert response.status_code == 400, response.content
    assert "native snapshot requires 1 to 10000 rows" in response.content.decode()
    assert not ExperimentsTable.no_workspace_objects.filter(dataset=dataset).exists()
    assert not Dataset.no_workspace_objects.filter(
        source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
        name=f"[Snapshot] {dataset.name}",
        organization=organization,
        workspace=workspace,
    ).exists()
    start.assert_not_called()
    assert "exist only in a dataset version" in response.content.decode()


def test_explicit_table_version_still_copies_saved_values(auth_client, scope_world):
    first = first_experiment(auth_client, scope_world)
    saved = version_of(first)
    expected = copied_values(first)
    assert len(expected) == 8
    edit_every_cell(scope_world[0])

    record = create_run(
        auth_client, request_body(scope_world, dataset_version_id=str(saved.id))
    )
    experiment = record.experiment_dataset.experiment
    assert copied_values(experiment) == expected
    assert "edited" not in copied_values(experiment)
    assert version_of(experiment).id == saved.id


@pytest.mark.parametrize("kind", ["deleted_rows", "version_only_json"])
def test_explicit_version_with_empty_table_keeps_its_own_refusal(
    auth_client, scope_world, user, kind
):
    dataset = scope_world[0]
    if kind == "deleted_rows":
        saved = version_of(first_experiment(auth_client, scope_world))
        expected_refusal = ROWS_GONE
    else:
        saved = create_dataset_version(
            dataset=dataset,
            items=[DatasetVersionItemInput(input={"question": "version only"})],
            created_by=user,
        )
        expected_refusal = NOT_FROM_TABLE
    # An empty table plus live version items must not trigger the table-only
    # guidance when the request explicitly selects that version.
    Row.no_workspace_objects.filter(dataset=dataset).update(deleted=True)
    assert not Row.no_workspace_objects.filter(dataset=dataset, deleted=False).exists()
    assert DatasetVersionItem.no_workspace_objects.filter(
        dataset_version=saved, deleted=False
    ).exists()
    before = counts()
    before_bindings = NativeExampleBinding.objects.count()

    response, start = post(
        auth_client, request_body(scope_world, dataset_version_id=str(saved.id))
    )
    assert response.status_code == 400, response.content
    assert expected_refusal in response.content.decode()
    assert "exist only in a dataset version" not in response.content.decode()
    assert "native snapshot requires 1 to 10000 rows" not in response.content.decode()
    assert counts() == before
    assert NativeExampleBinding.objects.count() == before_bindings
    start.assert_not_called()
