"""Experiment evaluation deletion and rename share Dataset-before-Column order."""

import json
import threading

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from model_hub.models.choices import DataTypeChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.tests import (
    test_column_rename_concurrency,
    test_column_rename_derived_api,
)
from model_hub.tests.test_column_rename_concurrency import (
    _blocked,
    _finish,
    _snapshot,
    _wait_for,
)
from model_hub.tests.test_column_rename_derived_api import _client, _rename

workers = test_column_rename_concurrency.workers
other_tenant = test_column_rename_derived_api.other_tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def owner_client(user, organization, workspace):
    return _client(user, organization, workspace)


@pytest.fixture
def evaluation(organization, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Evaluation source", organization=organization, workspace=workspace
    )
    snapshot = Dataset.no_workspace_objects.create(
        name="Evaluation snapshot", organization=organization, workspace=workspace
    )
    template = EvalTemplate.no_workspace_objects.create(
        name="delete-rename-template",
        organization=organization,
        workspace=workspace,
        criteria="Check {{output}}",
        model="fixture-model",
    )
    experiment = ExperimentsTable.no_workspace_objects.create(
        name="Concurrent evaluation", dataset=dataset, snapshot_dataset=snapshot
    )
    metrics = [
        UserEvalMetric.no_workspace_objects.create(
            name=f"Evaluation {index}",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=template,
            source_id=str(experiment.id),
            status=StatusType.COMPLETED.value,
        )
        for index in (1, 2)
    ]
    experiment.user_eval_template_ids.add(*metrics)
    edt = ExperimentDatasetTable.no_workspace_objects.create(
        name="Evaluation output", experiment=experiment
    )
    column = Column.no_workspace_objects.create(
        name="A",
        dataset=snapshot,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"{edt.id}-coleval-sourceid-{metrics[0].id}",
    )
    sibling = Column.no_workspace_objects.create(
        name="Untouched",
        dataset=snapshot,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.no_workspace_objects.create(dataset=snapshot, order=1)
    cell = Cell.no_workspace_objects.create(
        dataset=snapshot, column=column, row=row, value="Pass"
    )
    snapshot.column_order = [str(column.id), str(sibling.id)]
    snapshot.column_config = {
        str(column.id): {"width": 200},
        str(sibling.id): {"width": 100},
    }
    snapshot.save(update_fields=["column_order", "column_config"])
    return dataset, snapshot, experiment, metrics, column, sibling, cell


def _delete(client, evaluation):
    dataset, _, experiment, metrics, *_ = evaluation
    return client.delete(
        f"/model-hub/develops/{dataset.id}/delete_user_eval/{metrics[0].id}/",
        {"delete_column": True, "experiment_id": str(experiment.id)},
        format="json",
    )


def _assert_deleted(evaluation, renamed=False):
    dataset, snapshot, _, metrics, column, sibling, cell = evaluation
    for record in (dataset, snapshot, *metrics, column, sibling, cell):
        record.refresh_from_db()
    assert column.deleted and column.deleted_at is not None
    if renamed:
        assert column.name == "B"
    assert cell.deleted and cell.deleted_at is not None
    assert cell.value == "Pass"
    assert metrics[0].deleted and metrics[0].deleted_at is not None
    assert not metrics[1].deleted
    assert not sibling.deleted and sibling.name == "Untouched"
    assert snapshot.column_order == [str(sibling.id)]
    assert snapshot.column_config == {str(sibling.id): {"width": 100}}
    assert not dataset.deleted and not snapshot.deleted


def test_delete_experiment_eval_updates_dataset_before_columns(
    owner_client, evaluation
):
    with CaptureQueriesContext(connection) as queries:
        response = _delete(owner_client, evaluation)
    assert response.status_code == 200, response.content
    updates = [query["sql"] for query in queries if query["sql"].startswith("UPDATE ")]
    dataset_update = next(
        i
        for i, sql in enumerate(updates)
        if sql.startswith('UPDATE "model_hub_dataset"')
    )
    column_update = next(
        i
        for i, sql in enumerate(updates)
        if sql.startswith('UPDATE "model_hub_column"')
    )
    assert dataset_update < column_update, updates
    _assert_deleted(evaluation)


def test_delete_experiment_eval_and_rename_do_not_deadlock(
    owner_client, evaluation, workers
):
    _, snapshot, _, _, column, sibling, _ = evaluation
    delete_client = APIClient()
    delete_client.credentials(**owner_client._credentials)
    dataset_locked = threading.Event()
    resume = threading.Event()
    database_errors = []

    def observe_execute(actor, execute, sql, params, many, context):
        try:
            return execute(sql, params, many, context)
        except Exception as exc:
            cause = exc.__cause__ or exc
            database_errors.append(
                {
                    "actor": actor,
                    "sqlstate": getattr(cause, "sqlstate", None)
                    or getattr(cause, "pgcode", None),
                    "query": sql,
                }
            )
            raise

    def pause_after_dataset_lock(execute, sql, params, many, context):
        result = observe_execute("rename", execute, sql, params, many, context)
        if (
            'FROM "model_hub_dataset"' in sql
            and ("FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql)
            and not dataset_locked.is_set()
        ):
            assert connection.in_atomic_block
            dataset_locked.set()
            assert resume.wait(15), "Rename was not released"
        return result

    def rename_request():
        with connection.execute_wrapper(pause_after_dataset_lock):
            response = _rename(owner_client, snapshot, column)
            return {"status": response.status_code, "body": response.json()}

    def observe_delete(execute, sql, params, many, context):
        return observe_execute("delete", execute, sql, params, many, context)

    def delete_request():
        with connection.execute_wrapper(observe_delete):
            response = _delete(delete_client, evaluation)
            return {"status": response.status_code, "body": response.json()}

    rename = workers(rename_request)
    try:
        assert dataset_locked.wait(10), rename[1]
        delete = workers(delete_request)
        rename_pid, delete_pid = rename[1]["pid"], delete[1]["pid"]
        assert rename_pid != delete_pid
        observations = {
            "database_errors": database_errors,
            "delete_waits_on_dataset": _wait_for(
                lambda: _blocked(delete_pid, rename_pid, "model_hub_dataset")
            ),
        }
        resume.set()

        def cycle_or_completion():
            state = _snapshot(rename_pid, delete_pid)
            blockers = {row["pid"]: row["blockers"] for row in state["activity"]}
            if delete_pid in blockers.get(
                rename_pid, []
            ) and rename_pid in blockers.get(delete_pid, []):
                return state
            if rename[2].is_set() and delete[2].is_set():
                return "finished"
            return None

        observations["after_resume"] = _wait_for(cycle_or_completion)
        rename[0].join(20)
        delete[0].join(20)
        print("DELETE_RENAME_LOCKS " + json.dumps(observations, default=str))
        print("DELETE_RENAME_RESULTS " + json.dumps([rename[1], delete[1]]))
        assert _finish(rename)["status"] == 200
        assert _finish(delete)["status"] == 200
        assert not database_errors, database_errors
        _assert_deleted(evaluation, renamed=True)
        read = owner_client.get(
            f"/model-hub/develops/{snapshot.id}/get-dataset-table/",
            {"page_size": 10, "current_page_index": 0},
        )
        assert read.status_code == 200, read.content
        visible = {
            str(item["id"]): item["name"]
            for item in read.json()["result"]["column_config"]
        }
        assert str(column.id) not in visible
        assert visible[str(sibling.id)] == "Untouched"
    finally:
        resume.set()


def test_other_tenant_cannot_delete_experiment_eval(evaluation, other_tenant):
    _, snapshot, _, metrics, column, _, cell = evaluation
    _, _, foreign_client = other_tenant
    response = _delete(foreign_client, evaluation)
    assert response.status_code in {403, 404}, response.content
    for record in (snapshot, *metrics, column, cell):
        record.refresh_from_db()
        assert not record.deleted
    assert column.name == "A"
    assert str(column.id) in snapshot.column_order
    assert str(column.id) in snapshot.column_config
