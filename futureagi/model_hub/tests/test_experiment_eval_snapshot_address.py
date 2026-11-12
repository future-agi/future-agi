"""Deleting, editing and stopping an evaluation inside a v2 experiment.

A v2 experiment writes its evaluations on its snapshot dataset, while the
experiment page opens the evaluation drawer with the experiment's own dataset
id. The delete call it makes is

    DELETE /model-hub/develops/<experiment.dataset_id>/delete_user_eval/<metric>/
    {"delete_column": true, "experiment_id": "<experiment>"}

(frontend/src/sections/experiment-detail/ExperimentData/ExperimentDataView.jsx
passes id={experiment.dataset_id}; EvaluationDrawer.jsx sends experiment_id).
That delete must remove the evaluation, and it must never reach an evaluation
of an experiment that does not belong to the dataset in the URL. Editing
(edit_and_run_user_eval) and stopping (stop_user_eval) an evaluation from the
same drawer use the same address and the same rule.

Uses the root conftest fixtures (organization, user with memberships,
workspace, auth_client).
"""

from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401


def _dataset(organization, workspace, name):
    ds = Dataset.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    ds.column_order = []
    ds.save()
    return ds


def _metric(name, dataset, organization, workspace, template, source_id):
    return UserEvalMetric.objects.create(
        name=name,
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        template=template,
        status=StatusType.NOT_STARTED.value,
        config={},
        source_id=str(source_id),
    )


@pytest.fixture
def eval_template(db, organization, workspace):
    return EvalTemplate.objects.create(
        name="snapshot-delete-template",
        organization=organization,
        workspace=workspace,
        criteria="Evaluate the following: {{output}}",
        model="gpt-4",
    )


@pytest.fixture
def v2_experiment(db, organization, workspace, eval_template):
    """A v2 experiment: own dataset, snapshot dataset, two evals on the
    snapshot (an experiment keeps at least one), one per-EDT result column."""
    base = _dataset(organization, workspace, "Experiment dataset")
    snapshot = _dataset(organization, workspace, "Experiment snapshot")
    experiment = ExperimentsTable.objects.create(
        name="V2 experiment",
        dataset=base,
        snapshot_dataset=snapshot,
        prompt_config=[],
    )
    to_delete = _metric(
        "Exp Eval A", snapshot, organization, workspace, eval_template, experiment.id
    )
    keep = _metric(
        "Exp Eval B", snapshot, organization, workspace, eval_template, experiment.id
    )
    experiment.user_eval_template_ids.add(to_delete, keep)
    edt = ExperimentDatasetTable.objects.create(name="Run 1", experiment=experiment)
    column = Column.objects.create(
        name="Exp Eval A",
        dataset=snapshot,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"{edt.id}-coleval-sourceid-{to_delete.id}",
    )
    snapshot.column_order = [str(column.id)]
    snapshot.save()
    row = Row.objects.create(dataset=snapshot, order=0)
    cell = Cell.objects.create(dataset=snapshot, row=row, column=column, value="Pass")
    return {
        "base": base,
        "snapshot": snapshot,
        "experiment": experiment,
        "to_delete": to_delete,
        "keep": keep,
        "column": column,
        "cell": cell,
    }


def _delete(auth_client, dataset_id, metric_id, experiment_id):
    return auth_client.delete(
        f"/model-hub/develops/{dataset_id}/delete_user_eval/{metric_id}/",
        {"delete_column": True, "experiment_id": str(experiment_id)},
        format="json",
    )


@pytest.mark.django_db
def test_experiment_page_delete_removes_the_snapshot_evaluation(
    auth_client, v2_experiment
):
    w = v2_experiment
    response = _delete(auth_client, w["base"].id, w["to_delete"].id, w["experiment"].id)

    assert response.status_code == status.HTTP_200_OK, response.content
    w["to_delete"].refresh_from_db()
    assert w["to_delete"].deleted is True
    assert w["to_delete"].deleted_at is not None
    w["column"].refresh_from_db()
    assert w["column"].deleted is True
    w["cell"].refresh_from_db()
    assert w["cell"].deleted is True
    w["keep"].refresh_from_db()
    assert w["keep"].deleted is False


@pytest.mark.django_db
def test_delete_addressed_to_the_snapshot_dataset_also_works(
    auth_client, v2_experiment
):
    w = v2_experiment
    response = _delete(
        auth_client, w["snapshot"].id, w["to_delete"].id, w["experiment"].id
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    w["to_delete"].refresh_from_db()
    assert w["to_delete"].deleted is True


@pytest.mark.django_db
def test_an_experiment_of_another_dataset_is_not_reachable_through_this_one(
    auth_client, v2_experiment, organization, workspace
):
    w = v2_experiment
    other = _dataset(organization, workspace, "Some other dataset")
    response = _delete(auth_client, other.id, w["to_delete"].id, w["experiment"].id)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["result"] == "Experiment not found"
    w["to_delete"].refresh_from_db()
    assert w["to_delete"].deleted is False


@pytest.mark.django_db
def test_an_evaluation_of_another_experiment_is_not_deleted(
    auth_client, v2_experiment, organization, workspace, eval_template
):
    w = v2_experiment
    stranger = _metric(
        "Other experiment eval",
        w["snapshot"],
        organization,
        workspace,
        eval_template,
        "00000000-0000-0000-0000-000000000000",
    )
    response = _delete(auth_client, w["base"].id, stranger.id, w["experiment"].id)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["result"] == "Eval not found"
    stranger.refresh_from_db()
    assert stranger.deleted is False


@pytest.mark.django_db
def test_the_last_evaluation_of_an_experiment_is_still_kept(auth_client, v2_experiment):
    w = v2_experiment
    w["keep"].deleted = True
    w["keep"].save(update_fields=["deleted"])
    response = _delete(auth_client, w["base"].id, w["to_delete"].id, w["experiment"].id)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    w["to_delete"].refresh_from_db()
    assert w["to_delete"].deleted is False


@pytest.mark.django_db
def test_experiment_page_edit_updates_the_snapshot_evaluation(
    auth_client, v2_experiment
):
    w = v2_experiment
    with patch("model_hub.views.develop_dataset.run_evaluation_task.apply_async"):
        response = auth_client.post(
            f"/model-hub/develops/{w['base'].id}/edit_and_run_user_eval/"
            f"{w['to_delete'].id}/",
            {
                "experiment_id": str(w["experiment"].id),
                "config": {"model": "gpt-4", "prompt": "Updated prompt"},
            },
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK, response.content
    w["to_delete"].refresh_from_db()
    assert w["to_delete"].config.get("prompt") == "Updated prompt"


@pytest.mark.django_db
def test_edit_cannot_reach_an_experiment_of_another_dataset(
    auth_client, v2_experiment, organization, workspace
):
    w = v2_experiment
    other = _dataset(organization, workspace, "Some other dataset")
    with patch("model_hub.views.develop_dataset.run_evaluation_task.apply_async"):
        response = auth_client.post(
            f"/model-hub/develops/{other.id}/edit_and_run_user_eval/"
            f"{w['to_delete'].id}/",
            {
                "experiment_id": str(w["experiment"].id),
                "config": {"model": "gpt-4", "prompt": "Updated prompt"},
            },
            format="json",
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["result"] == "Eval not found"
    w["to_delete"].refresh_from_db()
    assert w["to_delete"].config.get("prompt") != "Updated prompt"


@pytest.mark.django_db
def test_experiment_page_stop_stops_the_snapshot_evaluation(auth_client, v2_experiment):
    w = v2_experiment
    w["to_delete"].status = StatusType.RUNNING.value
    w["to_delete"].save(update_fields=["status"])
    response = auth_client.post(
        f"/model-hub/develops/{w['base'].id}/stop_user_eval/{w['to_delete'].id}/",
        {"experiment_id": str(w["experiment"].id)},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    w["to_delete"].refresh_from_db()
    assert w["to_delete"].status == StatusType.ERROR.value


@pytest.mark.django_db
def test_stop_cannot_reach_an_experiment_of_another_dataset(
    auth_client, v2_experiment, organization, workspace
):
    w = v2_experiment
    w["to_delete"].status = StatusType.RUNNING.value
    w["to_delete"].save(update_fields=["status"])
    other = _dataset(organization, workspace, "Some other dataset")
    response = auth_client.post(
        f"/model-hub/develops/{other.id}/stop_user_eval/{w['to_delete'].id}/",
        {"experiment_id": str(w["experiment"].id)},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["result"] == "Eval not found"
    w["to_delete"].refresh_from_db()
    assert w["to_delete"].status == StatusType.RUNNING.value


@pytest.mark.django_db
def test_an_evaluation_on_another_organizations_dataset_is_not_reached(
    auth_client, v2_experiment, user, eval_template
):
    """The lookup widens to the experiment's other dataset; that dataset's
    evaluations are still filtered to the caller's organization."""
    w = v2_experiment
    other_org = Organization.objects.create(name="Other organization")
    other_workspace = Workspace.objects.create(
        name="Other workspace",
        organization=other_org,
        is_default=True,
        created_by=user,
    )
    foreign_snapshot = _dataset(other_org, other_workspace, "Foreign snapshot")
    foreign = _metric(
        "Foreign eval",
        foreign_snapshot,
        other_org,
        other_workspace,
        eval_template,
        w["experiment"].id,
    )
    w["experiment"].snapshot_dataset = foreign_snapshot
    w["experiment"].save(update_fields=["snapshot_dataset"])
    response = _delete(auth_client, w["base"].id, foreign.id, w["experiment"].id)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["result"] == "Eval not found"
    foreign.refresh_from_db()
    assert foreign.deleted is False


@pytest.mark.django_db
def test_a_malformed_experiment_id_is_not_found(auth_client, v2_experiment):
    w = v2_experiment
    response = _delete(auth_client, w["base"].id, w["to_delete"].id, "not-an-id")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["result"] == "Experiment not found"
    w["to_delete"].refresh_from_db()
    assert w["to_delete"].deleted is False
