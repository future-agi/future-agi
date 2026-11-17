"""Metadata edits must never submit work or rewrite experiment evidence."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection, migrations
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentRun,
    ExperimentRunItem,
    ExperimentsTable,
)
from model_hub.serializers.experiments import (
    ExperimentCreateV2Serializer,
    ExperimentUpdateV2Serializer,
)
from model_hub.views.experiments import ExperimentsTableV2View

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


def url(experiment):
    return f"/model-hub/experiments/v2/{experiment.id}/"


@pytest.fixture
def experiment(organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name="Rename dataset", organization=organization, workspace=workspace,
        user=user,
    )
    return ExperimentsTable.no_workspace_objects.create(
        name="Original experiment", dataset=dataset, user=user,
        status="Completed", run_metadata={"note": "Keep this"},
        run_scope={"row_ids": []}, run_policy={"error_policy": "exclude"},
    )


def stored(model, pk):
    return model._base_manager.filter(pk=pk).values().get()


def test_description_default_supports_legacy_writers(experiment):
    field = ExperimentsTable._meta.get_field("description")
    assert field.default == ""
    assert field.db_default == ""
    migration = import_module("model_hub.migrations.0142_experiment_description").Migration
    operation = next(
        operation for operation in migration.operations
        if isinstance(operation, migrations.AddField) and operation.name == "description"
    )
    assert operation.field.default == ""
    assert operation.field.db_default == ""

    # Emulate an old writer's INSERT: description is absent, not supplied by ORM.
    ExperimentsTable._base_manager.filter(pk=experiment.pk).update(description="Original only")
    legacy_id = uuid.uuid4()
    fields = [field for field in ExperimentsTable._meta.concrete_fields if field.name != "description"]
    quote = connection.ops.quote_name
    columns = ", ".join(quote(field.column) for field in fields)
    expressions = []
    params = []
    for field in fields:
        if field.primary_key or field.name == "name":
            expressions.append("%s")
            value = legacy_id if field.primary_key else "Legacy writer experiment"
            params.append(field.get_db_prep_value(value, connection))
        else:
            expressions.append(quote(field.column))
    pk = ExperimentsTable._meta.pk
    params.append(pk.get_db_prep_value(experiment.pk, connection))
    table = quote(ExperimentsTable._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {table} ({columns}) SELECT {', '.join(expressions)} "
            f"FROM {table} WHERE {quote(pk.column)} = %s",
            params,
        )
    legacy = stored(ExperimentsTable, legacy_id)
    assert legacy["name"] == "Legacy writer experiment"
    assert legacy["description"] == ""
    assert stored(ExperimentsTable, experiment.pk)["description"] == "Original only"


def test_rename_preserves_runs_results_and_configuration(auth_client, experiment):
    version = DatasetVersion.no_workspace_objects.create(
        dataset=experiment.dataset,
        organization=experiment.dataset.organization,
        workspace=experiment.dataset.workspace,
    )
    run = ExperimentRun.no_workspace_objects.create(
        experiment=experiment, dataset_version=version, task_kind="external",
        status="running", ordinal=7, metadata={"name": "Original run label"},
        model_config={"model": "fixture"},
    )
    item = ExperimentRunItem.no_workspace_objects.create(
        run=run, example_id=uuid.uuid4(), status="completed",
        output={"answer": "Unchanged"}, token_usage={"total": 21}, cost=0.01,
        score_refs=[{"score": 0.9}], trace_id="kept-trace",
    )
    variant = ExperimentDatasetTable.no_workspace_objects.create(
        experiment=experiment, name="Original variant", status="Completed",
    )
    experiment.experiments_datasets.add(variant)
    before = stored(ExperimentsTable, experiment.id)
    evidence = [(model, obj.id, stored(model, obj.id)) for model, obj in (
        (ExperimentRun, run), (ExperimentRunItem, item),
        (ExperimentDatasetTable, variant), (DatasetVersion, version),
    )]
    with patch.object(
        ExperimentsTableV2View, "_put_transaction",
        side_effect=AssertionError("A rename must not enter execution admission"),
    ):
        response = auth_client.put(
            url(experiment), {"name": "  Renamed experiment  ", "description": "New notes"},
            format="json",
        )
    assert response.status_code == 200, response.data
    assert response.data["status"] is True
    assert response.data["result"]["id"] == str(experiment.id)
    assert response.data["result"]["name"] == "Renamed experiment"
    assert response.data["result"]["description"] == "New notes"
    after = stored(ExperimentsTable, experiment.id)
    assert {k: v for k, v in before.items() if k not in {"name", "description", "updated_at"}} == {
        k: v for k, v in after.items() if k not in {"name", "description", "updated_at"}
    }
    for model, pk, values in evidence:
        assert stored(model, pk) == values
    assert list(experiment.experiments_datasets.values_list("id", flat=True)) == [variant.id]


@pytest.mark.parametrize("name,message", [
    ("", "Enter an experiment name."),
    ("   \n\t", "Enter an experiment name."),
    ("x" * 256, "Use 255 characters or fewer for the experiment name."),
    (None, "Enter an experiment name."),
    (123, "Enter an experiment name as text."),
])
def test_invalid_name_has_plain_field_error(auth_client, experiment, name, message):
    before = stored(ExperimentsTable, experiment.id)
    response = auth_client.put(url(experiment), {"name": name}, format="json")
    assert response.status_code == 400
    assert response.data["details"]["name"] == [message]
    assert message in response.data["message"]
    assert stored(ExperimentsTable, experiment.id) == before


def test_duplicate_name_is_scoped_to_live_siblings(auth_client, experiment):
    ExperimentsTable.no_workspace_objects.create(name="Taken", dataset=experiment.dataset)
    before = stored(ExperimentsTable, experiment.id)
    response = auth_client.put(
        url(experiment), {"name": " Taken ", "description": "Must not save"}, format="json",
    )
    assert response.status_code == 400
    assert response.data["details"]["name"] == [
        "An experiment with this name already exists in this dataset. Choose another name."
    ]
    assert stored(ExperimentsTable, experiment.id) == before


@pytest.mark.parametrize("other_scope", ["deleted", "other_dataset", "different_case"])
def test_non_conflicting_names_are_allowed(auth_client, experiment, other_scope):
    dataset = experiment.dataset
    if other_scope == "other_dataset":
        dataset = Dataset.no_workspace_objects.create(
            name="Other data", organization=dataset.organization, workspace=dataset.workspace,
        )
    ExperimentsTable.no_workspace_objects.create(
        name="taken" if other_scope == "different_case" else "Taken", dataset=dataset,
        deleted=other_scope == "deleted",
    )
    response = auth_client.put(url(experiment), {"name": "Taken"}, format="json")
    assert response.status_code == 200, response.data
    experiment.refresh_from_db()
    assert experiment.name == "Taken"


def test_same_name_is_idempotent_and_description_can_be_cleared(auth_client, experiment):
    first = auth_client.put(
        url(experiment), {"name": "x" * 255, "description": "  Keep whitespace  "}, format="json",
    )
    assert first.status_code == 200, first.data
    before = stored(ExperimentsTable, experiment.id)
    repeat = auth_client.put(
        url(experiment), {"name": "x" * 255, "description": "  Keep whitespace  "}, format="json",
    )
    assert repeat.status_code == 200
    assert stored(ExperimentsTable, experiment.id) == before
    cleared = auth_client.put(url(experiment), {"description": ""}, format="json")
    assert cleared.status_code == 200
    assert cleared.data["result"]["name"] == "x" * 255
    assert cleared.data["result"]["description"] == ""


@pytest.mark.parametrize("description,message", [
    ("x" * 4001, "Use 4000 characters or fewer for the description."),
    (None, "Enter a description as text, or leave it empty."),
    (123, "Enter a description as text, or leave it empty."),
])
def test_invalid_description_is_atomic(auth_client, experiment, description, message):
    before = stored(ExperimentsTable, experiment.id)
    response = auth_client.put(
        url(experiment), {"name": "Not saved", "description": description}, format="json",
    )
    assert response.status_code == 400
    assert response.data["details"]["description"] == [message]
    assert stored(ExperimentsTable, experiment.id) == before


@pytest.mark.parametrize("scope", ["organization", "workspace", "deleted", "deleted_dataset", "missing"])
def test_unavailable_experiment_is_not_found(auth_client, experiment, scope):
    target = experiment.id
    if scope == "organization":
        other = Organization.objects.create(name="Other organization")
        Dataset.no_workspace_objects.filter(pk=experiment.dataset_id).update(organization=other)
    elif scope == "workspace":
        other = Workspace.no_workspace_objects.create(
            name="Other workspace", organization=experiment.dataset.organization,
            is_default=False, created_by=experiment.user,
        )
        Dataset.no_workspace_objects.filter(pk=experiment.dataset_id).update(workspace=other)
    elif scope == "deleted":
        ExperimentsTable.no_workspace_objects.filter(pk=experiment.id).update(deleted=True)
    elif scope == "deleted_dataset":
        Dataset.no_workspace_objects.filter(pk=experiment.dataset_id).update(deleted=True)
    else:
        target = uuid.uuid4()
    response = auth_client.put(
        f"/model-hub/experiments/v2/{target}/", {"name": "Not allowed"}, format="json",
    )
    assert response.status_code == 404, response.data
    assert response.data["detail"] == "Experiment not found"
    persisted = stored(ExperimentsTable, experiment.id)
    assert persisted["name"] == "Original experiment"
    if scope == "deleted":
        assert persisted["deleted"] is True


def test_read_only_member_cannot_rename(experiment, user, workspace):
    request = APIRequestFactory().put(url(experiment), {"name": "Not allowed"}, format="json")
    request.organization = experiment.dataset.organization
    request.workspace = workspace
    force_authenticate(request, user=user)
    with patch.object(type(user), "can_write_to_workspace", return_value=False):
        response = ExperimentsTableV2View.as_view()(request, experiment_id=experiment.id)
    assert response.status_code == 403
    assert stored(ExperimentsTable, experiment.id)["name"] == "Original experiment"


def test_unauthenticated_rename_is_refused(experiment):
    request = APIRequestFactory().put(url(experiment), {"name": "Not allowed"}, format="json")
    response = ExperimentsTableV2View.as_view()(request, experiment_id=experiment.id)
    assert response.status_code in {401, 403}
    assert stored(ExperimentsTable, experiment.id)["name"] == "Original experiment"


@pytest.mark.parametrize("prompt_config", [
    [],
    [{"target_type": "http", "http": {"url": "https://example.com/experiment"}}],
], ids=["prompt", "http"])
def test_metadata_and_execution_edits_are_not_silently_mixed(auth_client, experiment, prompt_config):
    response = auth_client.put(
        url(experiment), {"name": "Not saved", "prompt_config": prompt_config}, format="json",
    )
    assert response.status_code == 400
    assert "Save the name and description separately from run settings." in response.data["message"]
    assert stored(ExperimentsTable, experiment.id)["name"] == "Original experiment"


def test_rename_rejects_unknown_fields(auth_client, experiment):
    response = auth_client.put(
        url(experiment), {"name": "Not saved", "workspace_id": str(uuid.uuid4())}, format="json",
    )
    assert response.status_code == 400
    assert "workspace_id" in response.data["details"]
    assert stored(ExperimentsTable, experiment.id)["name"] == "Original experiment"


def test_metadata_fields_are_documented_without_changing_config_only_updates():
    serializer = ExperimentUpdateV2Serializer(data={"name": "Name", "description": "Notes"})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"name": "Name", "description": "Notes"}
    config = ExperimentUpdateV2Serializer(data={"column_id": None})
    assert config.is_valid(), config.errors
    assert config.validated_data == {"column_id": None}
    create_fields = ExperimentCreateV2Serializer().fields
    assert create_fields["description"].run_validation("Notes") == "Notes"
    assert create_fields["description"].required is False


def test_detail_list_and_run_catalog_read_current_experiment_name(auth_client, experiment):
    version = DatasetVersion.no_workspace_objects.create(
        dataset=experiment.dataset, organization=experiment.dataset.organization,
        workspace=experiment.dataset.workspace,
    )
    run = ExperimentRun.no_workspace_objects.create(
        experiment=experiment, dataset_version=version, task_kind="external",
        status="completed", ordinal=1, metadata={"name": "Immutable run name"},
    )
    response = auth_client.put(
        url(experiment), {"name": "Renamed title", "description": "Saved description"}, format="json",
    )
    assert response.status_code == 200, response.data
    detail = auth_client.get(url(experiment))
    assert detail.status_code == 200, detail.data
    assert detail.data["result"]["name"] == "Renamed title"
    assert detail.data["result"]["description"] == "Saved description"
    listed = auth_client.get(
        "/model-hub/experiments/v2/list/", {"dataset_id": str(experiment.dataset_id)},
    )
    assert listed.status_code == 200, listed.data
    assert any(row["id"] == str(experiment.id) and row["name"] == "Renamed title"
               and row["description"] == "Saved description" for row in listed.data["results"])
    runs = auth_client.get(
        f"/model-hub/datasets/{experiment.dataset_id}/runs/", {"search": "Renamed title"},
    )
    assert runs.status_code == 200, runs.data
    row = runs.data["result"]["results"][0]
    assert row["run_id"] == str(run.id)
    assert row["experiment_name"] == "Renamed title"
    assert row["experiment_description"] == "Saved description"
    assert row["name"] == "Immutable run name"
    assert detail.data["result"]["runs"][0]["experiment_name"] == "Renamed title"
    run.refresh_from_db()
    assert run.metadata == {"name": "Immutable run name"}


@pytest.mark.django_db(transaction=True)
def test_concurrent_renames_cannot_claim_the_same_name(experiment, user, workspace):
    if connection.vendor != "postgresql":
        pytest.skip("Row locking requires PostgreSQL")
    sibling = ExperimentsTable.no_workspace_objects.create(
        dataset=experiment.dataset, name="Sibling", user=user,
    )
    barrier = Barrier(2)

    def rename(target):
        close_old_connections()
        try:
            request = APIRequestFactory().put(url(target), {"name": "One winner"}, format="json")
            request.organization = experiment.dataset.organization
            request.workspace = workspace
            force_authenticate(request, user=user)
            barrier.wait(timeout=10)
            response = ExperimentsTableV2View.as_view()(request, experiment_id=target.id)
            return response.status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(rename, target) for target in (experiment, sibling)]
        assert sorted(f.result(timeout=30) for f in futures) == [200, 400]
    assert ExperimentsTable.no_workspace_objects.filter(
        dataset=experiment.dataset, name="One winner", deleted=False,
    ).count() == 1
