"""Default creates must retain full snapshots without materializing row scope."""

from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
from model_hub.services.experiment_run_scope import prepare_run_scope
from model_hub.services.run_metadata_public import public_run_metadata
from model_hub.tests.test_experiment_run_scope import (
    CREATE_URL,
    request_body,
    scope_world,  # noqa: F401
    snapshot_orders,
    version_world,  # noqa: F401
)
from model_hub.tests.test_experiment_version_params import create_run

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def qualified_scope(settings):
    settings.EXPERIMENT_SCOPE_ENABLED = True


@pytest.mark.parametrize("metadata", [{}, {"purpose": "api metadata only"}])
def test_metadata_only_create_keeps_plain_snapshot(auth_client, scope_world, metadata):  # noqa: F811
    # The wizard sends metadata={} even when the scope controls are untouched.
    record = create_run(auth_client, request_body(scope_world, metadata=metadata))
    experiment = record.experiment_dataset.experiment
    experiment.refresh_from_db()
    assert experiment.run_scope == {}
    # The stored metadata also carries the private native_snapshot key the
    # snapshot writer adds; what callers read is the public projection.
    assert public_run_metadata(experiment.run_metadata) == metadata
    assert snapshot_orders(experiment) == list(range(8))
    detail = auth_client.get(f"{CREATE_URL}{experiment.id}/").json()["result"]
    assert detail["run_scope"] == {}
    assert detail["metadata"] == metadata
    assert "row_ids" not in detail
    assert "sample" not in detail


@pytest.mark.parametrize("metadata", [{}, {"purpose": "api metadata only"}])
def test_metadata_only_does_not_resolve_source_ids(scope_world, metadata):  # noqa: F811
    dataset = scope_world[0]
    organization, workspace = dataset.organization, dataset.workspace
    with CaptureQueriesContext(connection) as queries:
        scope = prepare_run_scope(
            dataset, {"metadata": metadata}, organization, workspace
        )
    assert scope == {}
    assert len(queries) == 0


@pytest.mark.parametrize("scoped", [False, True])
def test_default_workspace_admits_legacy_dataset(auth_client, scope_world, scoped):  # noqa: F811
    dataset = scope_world[0]
    Dataset.no_workspace_objects.filter(pk=dataset.pk).update(workspace=None)
    dataset.refresh_from_db()
    settings = {"metadata": {}}
    if scoped:
        selected = Row.no_workspace_objects.get(dataset=dataset, order=2)
        settings["row_ids"] = [str(selected.id)]
    record = create_run(auth_client, request_body(scope_world, **settings))
    experiment = record.experiment_dataset.experiment
    assert snapshot_orders(experiment) == ([2] if scoped else list(range(8)))
    if not scoped:
        assert experiment.run_scope == {}
    # Admitted in the default workspace without relabelling any legacy tenant.
    experiment.refresh_from_db()
    snapshot = NativeExperimentSnapshot.objects.get(experiment=experiment)
    version = snapshot.dataset_version
    assert (
        experiment.dataset.workspace_id,
        experiment.snapshot_dataset.workspace_id,
        version.workspace_id,
    ) == (None, None, None)


def test_other_workspace_dataset_is_still_refused(auth_client, scope_world, user):  # noqa: F811
    dataset = scope_world[0]
    foreign_workspace = Workspace.objects.create(
        name="Nondefault workspace", organization=dataset.organization, created_by=user
    )
    Dataset.no_workspace_objects.filter(pk=dataset.pk).update(
        workspace=foreign_workspace
    )
    before = ExperimentsTable.no_workspace_objects.count()
    with patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start:
        response = auth_client.post(
            CREATE_URL, request_body(scope_world, metadata={}), format="json"
        )
    assert response.status_code == 404
    assert ExperimentsTable.no_workspace_objects.count() == before
    start.assert_not_called()
