"""Update Evaluation with a re-score, and Run evaluations, on every kind of
experiment.

The evaluations drawer saves an edited evaluation and asks for a re-score with

    POST /model-hub/develops/<dataset>/edit_and_run_user_eval/<metric>/
    {"experiment_id": "<experiment>", "config": {...}, "run": true}

An experiment's evaluation is scored by its experiment, never by the dataset
evaluation runner (which has no column of its own for it and wrote error
cells telling the person to contact support):

* an experiment that calls an HTTP endpoint scores a row only when the row is
  sent again, so the edit is saved, nothing is scored, and the answer says so
  and names where rows can be sent again;
* an experiment with a snapshot is handed, after the save, to its own
  evaluation-only rerun, the door Run evaluations uses;
* an experiment from before run snapshots is left to the evaluation sweeper.

What can refuse the re-score (a run that is going, on every kind of
experiment with a snapshot, the HTTP one included; an evaluation the
experiment does not list) refuses before anything is saved. When scoring
can't start after the save, the answer says the change is saved and not
scored, naming only the rerun's own fixed sentence, and no cell is left
running. Run evaluations, and a retry on an evaluation cell, on an HTTP
endpoint's output are refused before any work.

Workflow starts are replaced and recorded; the sweeper tick is the real
execute_evaluation, with what it dispatches recorded. Uses the root conftest
fixtures (organization, user with memberships, workspace, auth_client).
"""

from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.response import Response

from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.tasks import user_evaluation
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401
from model_hub.views.experiments import ExperimentRerunCellsV2View
from tfc.middleware.workspace_context import workspace_context

CELLS_START = "tfc.temporal.experiments.start_rerun_cells_v2_workflow"

QUEUED = "Column evaluation updated and queued for processing"
HTTP_SAVED = (
    "Saved. This experiment calls an HTTP endpoint, so a row is scored with "
    "this change only when it is sent again. Retry failed rows from the Data "
    "tab or a failed cell; to score every row, create a new experiment. The "
    "scores shown are from before your change."
)
RUN_GOING = (
    "This experiment is still running. Stop it, or wait for it to finish, "
    "then run evaluations again."
)
NOT_LISTED = (
    "This evaluation isn't one of this experiment's evaluations, so it can't "
    "be re-scored here. Nothing was saved."
)
NOT_STARTED = "Scoring couldn't be started. Run the evaluation again."
SAVED_NOT_SCORED_BECAUSE = (
    "Your change to this evaluation is saved, but it wasn't scored: " + NOT_STARTED
)
SAVED_NOT_SCORED = (
    "Your change to this evaluation is saved, but it wasn't scored. "
    "Run the evaluation again."
)
HTTP_EVALUATIONS_REFUSED = (
    "Nothing was started. This experiment calls an HTTP endpoint, so a row is "
    "scored only when it is sent again. Retry failed rows from the Data tab "
    "or a failed output cell; to score every row, create a new experiment."
)
ORIGINAL_PROMPT = "Original prompt"
EDITED_PROMPT = "Updated prompt"


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


@pytest.fixture
def eval_template(db, organization, workspace):
    return EvalTemplate.objects.create(
        name="rescore-update-template",
        organization=organization,
        workspace=workspace,
        criteria="Evaluate the following: {{output}}",
        model="gpt-4",
    )


def _experiment(organization, workspace, user, eval_template, *, http, snapshot=True):
    """An experiment with one source, one evaluation and two scored rows.

    With ``http`` the source is an HTTP endpoint, stored as the New experiment
    page stores it; otherwise it is a plain run. With ``snapshot`` the
    experiment writes to its snapshot dataset, as experiments are made today;
    without it, it writes to its own dataset, as experiments from before run
    snapshots did. There is no reference column, so the dataset written to
    holds no dataset-style evaluation column for the evaluation, only the
    per-output one and its reason column.
    """
    kind = "HTTP" if http else "Prompt"
    base = _dataset(organization, workspace, f"{kind} experiment dataset")
    written = (
        _dataset(organization, workspace, f"{kind} experiment snapshot")
        if snapshot
        else base
    )
    experiment = ExperimentsTable.objects.create(
        name=f"{kind} experiment",
        dataset=base,
        snapshot_dataset=written if snapshot else None,
        user=user,
        prompt_config=[],
        status=StatusType.COMPLETED.value,
    )
    metric = UserEvalMetric.objects.create(
        name="output-judge",
        dataset=written,
        organization=organization,
        workspace=workspace,
        template=eval_template,
        status=StatusType.COMPLETED.value,
        config={"prompt": ORIGINAL_PROMPT},
        source_id=str(experiment.id),
    )
    experiment.user_eval_template_ids.add(metric)
    if http:
        source = ExperimentDatasetTable.objects.create(
            name="HTTP endpoint",
            experiment=experiment,
            legacy_prompt_config={
                "target_type": "http",
                "http": {"url": "https://endpoint.example/answer", "method": "POST"},
                "variable_mapping": {},
                "order": 0,
            },
        )
    else:
        source = ExperimentDatasetTable.objects.create(
            name="Prompt run", experiment=experiment
        )
    experiment.experiments_datasets.add(source)
    output = Column.objects.create(
        name=source.name,
        dataset=written,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(source.id),
    )
    source.columns.add(output)
    score = Column.objects.create(
        name=f"{metric.name}-{output.name}",
        dataset=written,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"{source.id}-{output.id}-sourceid-{metric.id}",
    )
    reason = Column.objects.create(
        name=f"{metric.name}-{score.name}-reason",
        dataset=written,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION_REASON.value,
        source_id=f"{score.id}-sourceid-{metric.id}",
    )
    written.column_order = [str(output.id), str(score.id), str(reason.id)]
    written.save()
    rows = []
    for order in range(2):
        row = Row.objects.create(dataset=written, order=order)
        rows.append(row)
        Cell.objects.create(
            dataset=written,
            row=row,
            column=output,
            value=f"Answer for row {order}",
            status=CellStatus.PASS.value,
        )
        Cell.objects.create(
            dataset=written,
            row=row,
            column=score,
            value="Passed",
            status=CellStatus.PASS.value,
        )
        Cell.objects.create(
            dataset=written,
            row=row,
            column=reason,
            value="The answer matches the question.",
            status=CellStatus.PASS.value,
        )
    return {
        "base": base,
        "written": written,
        "experiment": experiment,
        "metric": metric,
        "score": score,
        "reason": reason,
        "rows": rows,
    }


@pytest.fixture
def http_experiment(db, organization, workspace, user, eval_template):
    return _experiment(organization, workspace, user, eval_template, http=True)


@pytest.fixture
def prompt_experiment(db, organization, workspace, user, eval_template):
    return _experiment(organization, workspace, user, eval_template, http=False)


@pytest.fixture
def presnapshot_experiment(db, organization, workspace, user, eval_template):
    return _experiment(
        organization, workspace, user, eval_template, http=False, snapshot=False
    )


def _update(auth_client, dataset_id, metric, experiment=None, *, run=True):
    body = {"config": {"model": "gpt-4", "prompt": EDITED_PROMPT}, "run": run}
    if experiment is not None:
        body["experiment_id"] = str(experiment.id)
    with patch("model_hub.views.develop_dataset.run_evaluation_task.apply_async"):
        return auth_client.post(
            f"/model-hub/develops/{dataset_id}/edit_and_run_user_eval/{metric.id}/",
            body,
            format="json",
        )


def _run_evaluations(auth_client, w):
    return auth_client.post(
        f"/model-hub/develops/{w['base'].id}/start_evals_process/",
        {
            "user_eval_ids": [str(w["metric"].id)],
            "experiment_id": str(w["experiment"].id),
        },
        format="json",
    )


def _cells(dataset):
    return Cell.no_workspace_objects.filter(dataset=dataset, deleted=False)


def _set_running(experiment):
    ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).update(
        status=StatusType.RUNNING.value
    )


def _running(dataset):
    return _cells(dataset).filter(status=CellStatus.RUNNING.value)


def _stored(instance):
    instance.refresh_from_db()
    return instance


def swept():
    """What the evaluation sweeper's tick dispatches now, as the scheduled
    worker runs it (no request context)."""
    dispatched = []

    def dispatch(args=None, kwargs=None, **options):
        dispatched.append(dict(args[0]))

    with (
        patch.object(user_evaluation, "close_old_connections"),
        patch.object(
            user_evaluation.process_evaluation_single_task,
            "apply_async",
            side_effect=dispatch,
        ),
        workspace_context(None),
    ):
        user_evaluation.execute_evaluation._original_func()
    return dispatched


# --- an experiment that calls an HTTP endpoint ---------------------------------


@pytest.mark.django_db
def test_http_source_update_saves_and_says_it_was_not_scored(
    auth_client, http_experiment
):
    w = http_experiment
    with patch(CELLS_START, return_value="never") as started:
        response = _update(auth_client, w["base"].id, w["metric"], w["experiment"])

    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["result"] == HTTP_SAVED
    assert response.json()["result"] != QUEUED
    assert "support" not in response.json()["result"].lower()
    metric = _stored(w["metric"])
    assert metric.config.get("prompt") == EDITED_PROMPT
    assert metric.status == StatusType.COMPLETED.value
    assert not _running(w["written"]).exists()
    assert set(
        _cells(w["written"]).filter(column=w["score"]).values_list("status", "value")
    ) == {(CellStatus.PASS.value, "Passed")}
    started.assert_not_called()


@pytest.mark.django_db
def test_http_source_evaluation_never_reaches_the_dataset_runner(
    auth_client, http_experiment
):
    w = http_experiment
    _update(auth_client, w["base"].id, w["metric"], w["experiment"])

    assert str(w["metric"].id) not in {item["eval_id"] for item in swept()}
    assert not Column.no_workspace_objects.filter(
        dataset=w["written"],
        source=SourceChoices.EVALUATION.value,
        source_id=str(w["metric"].id),
        deleted=False,
    ).exists()


@pytest.mark.django_db
def test_http_source_through_the_snapshot_address_is_saved_the_same_way(
    auth_client, http_experiment
):
    w = http_experiment
    response = _update(auth_client, w["written"].id, w["metric"])

    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["result"] == HTTP_SAVED
    metric = _stored(w["metric"])
    assert metric.config.get("prompt") == EDITED_PROMPT
    assert metric.status == StatusType.COMPLETED.value
    assert not _running(w["written"]).exists()


@pytest.mark.django_db
def test_http_source_update_while_a_run_is_going_is_refused_before_anything_is_saved(
    auth_client, http_experiment
):
    w = http_experiment
    _set_running(w["experiment"])
    with patch(CELLS_START, return_value="never") as started:
        response = _update(auth_client, w["base"].id, w["metric"], w["experiment"])

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["result"] == RUN_GOING
    metric = _stored(w["metric"])
    assert metric.config.get("prompt") == ORIGINAL_PROMPT
    assert metric.status == StatusType.COMPLETED.value
    assert not _running(w["written"]).exists()
    started.assert_not_called()


@pytest.mark.django_db
def test_run_evaluations_on_http_output_is_refused_before_any_work(
    auth_client, http_experiment
):
    w = http_experiment
    with patch(CELLS_START, return_value="never") as started:
        response = _run_evaluations(auth_client, w)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["result"] == HTTP_EVALUATIONS_REFUSED
    started.assert_not_called()
    assert not _running(w["written"]).exists()
    assert _stored(w["experiment"]).status == StatusType.COMPLETED.value


@pytest.mark.django_db
def test_a_retry_on_an_http_evaluation_cell_is_refused_before_any_work(
    auth_client, http_experiment
):
    w = http_experiment
    with patch(CELLS_START, return_value="never") as started:
        response = auth_client.post(
            f"/model-hub/experiments/v2/{w['experiment'].id}/rerun-cells/",
            {
                "cells": [
                    {"column_id": str(w["score"].id), "row_id": str(w["rows"][0].id)}
                ]
            },
            format="json",
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["result"] == HTTP_EVALUATIONS_REFUSED
    started.assert_not_called()
    assert not _running(w["written"]).exists()


# --- an experiment with a snapshot: its own evaluation-only rerun ---------------


@pytest.mark.django_db
def test_update_hands_the_evaluation_to_the_experiment_rerun(
    auth_client, prompt_experiment
):
    w = prompt_experiment
    with patch(CELLS_START, return_value="evaluation-rerun") as started:
        response = _update(auth_client, w["base"].id, w["metric"], w["experiment"])

    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["result"] == QUEUED
    started.assert_called_once()
    assert started.call_args.kwargs["eval_template_ids"] == [str(w["metric"].id)]
    assert started.call_args.kwargs["eval_only"] is True
    metric = _stored(w["metric"])
    assert metric.config.get("prompt") == EDITED_PROMPT
    assert metric.status != StatusType.NOT_STARTED.value
    # The rerun, not the dataset runner, scores it.
    assert str(w["metric"].id) not in {item["eval_id"] for item in swept()}


@pytest.mark.django_db
def test_update_through_the_snapshot_address_is_handed_over_too(
    auth_client, prompt_experiment
):
    w = prompt_experiment
    with patch(CELLS_START, return_value="evaluation-rerun") as started:
        response = _update(auth_client, w["written"].id, w["metric"])

    assert response.status_code == status.HTTP_200_OK, response.content
    started.assert_called_once()
    assert _stored(w["metric"]).status != StatusType.NOT_STARTED.value


@pytest.mark.django_db
def test_a_run_that_is_going_refuses_before_anything_is_saved(
    auth_client, prompt_experiment
):
    w = prompt_experiment
    ExperimentsTable.no_workspace_objects.filter(pk=w["experiment"].pk).update(
        status=StatusType.RUNNING.value
    )
    with patch(CELLS_START, return_value="never") as started:
        response = _update(auth_client, w["base"].id, w["metric"], w["experiment"])

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["result"] == RUN_GOING
    metric = _stored(w["metric"])
    assert metric.config.get("prompt") == ORIGINAL_PROMPT
    assert metric.status == StatusType.COMPLETED.value
    assert not _running(w["written"]).exists()
    started.assert_not_called()


@pytest.mark.django_db
def test_an_evaluation_the_experiment_does_not_list_is_refused_before_saving(
    auth_client, prompt_experiment
):
    w = prompt_experiment
    w["experiment"].user_eval_template_ids.remove(w["metric"])
    with patch(CELLS_START, return_value="never") as started:
        response = _update(auth_client, w["base"].id, w["metric"], w["experiment"])

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["result"] == NOT_LISTED
    metric = _stored(w["metric"])
    assert metric.config.get("prompt") == ORIGINAL_PROMPT
    assert metric.status == StatusType.COMPLETED.value
    assert not _running(w["written"]).exists()
    started.assert_not_called()


@pytest.mark.django_db
def test_scoring_that_cannot_start_says_saved_with_its_reason_and_leaves_nothing_running(
    auth_client, prompt_experiment
):
    w = prompt_experiment
    with patch(CELLS_START, side_effect=RuntimeError("workflow service unreachable")):
        response = _update(auth_client, w["base"].id, w["metric"], w["experiment"])

    assert response.status_code == status.HTTP_409_CONFLICT, response.content
    assert response.json()["result"] == SAVED_NOT_SCORED_BECAUSE
    assert "unreachable" not in response.content.decode()
    assert _stored(w["metric"]).config.get("prompt") == EDITED_PROMPT
    assert not _running(w["written"]).exists()
    for cell in _cells(w["written"]).filter(column=w["score"]):
        assert cell.status == CellStatus.ERROR.value
        assert cell.value_infos == {"reason": NOT_STARTED}
    assert _stored(w["score"]).status == StatusType.FAILED.value
    assert _stored(w["experiment"]).status == StatusType.COMPLETED.value


@pytest.mark.django_db
@pytest.mark.parametrize(
    "hand_over",
    [
        {"side_effect": RuntimeError("relation does not exist")},
        {
            "return_value": Response(
                {
                    "status": False,
                    "code": "experiment_admission_unconfirmed",
                    "detail": "Rerun admission is unconfirmed. Retry the same "
                    "request with the same Idempotency-Key.",
                },
                status=503,
            )
        },
    ],
    ids=["an_exception", "an_answer_that_is_not_a_fixed_sentence"],
)
def test_a_hand_over_without_a_fixed_reason_says_saved_and_never_repeats_it(
    auth_client, prompt_experiment, hand_over
):
    w = prompt_experiment
    with patch.object(ExperimentRerunCellsV2View, "post", **hand_over):
        response = _update(auth_client, w["base"].id, w["metric"], w["experiment"])

    assert response.status_code == status.HTTP_409_CONFLICT, response.content
    assert response.json()["result"] == SAVED_NOT_SCORED
    body = response.content.decode()
    assert "relation does not exist" not in body
    assert "Idempotency-Key" not in body
    assert _stored(w["metric"]).config.get("prompt") == EDITED_PROMPT
    assert not _running(w["written"]).exists()


@pytest.mark.django_db
def test_run_evaluations_that_cannot_start_leaves_nothing_running(
    auth_client, prompt_experiment
):
    w = prompt_experiment
    with patch(CELLS_START, side_effect=RuntimeError("workflow service unreachable")):
        response = _run_evaluations(auth_client, w)

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["result"] == NOT_STARTED
    assert "unreachable" not in response.content.decode()
    assert not _running(w["written"]).exists()
    for cell in _cells(w["written"]).filter(column=w["score"]):
        assert cell.status == CellStatus.ERROR.value
        assert cell.value_infos == {"reason": NOT_STARTED}
    assert _stored(w["experiment"]).status == StatusType.COMPLETED.value


@pytest.mark.django_db
def test_run_evaluations_while_a_run_is_going_is_refused_before_any_work(
    auth_client, prompt_experiment
):
    w = prompt_experiment
    _set_running(w["experiment"])
    cells_before = list(
        _cells(w["written"]).order_by("id").values_list("id", "status", "value")
    )
    with patch(CELLS_START, return_value="never") as started:
        response = _run_evaluations(auth_client, w)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["result"] == RUN_GOING
    started.assert_not_called()
    assert (
        list(_cells(w["written"]).order_by("id").values_list("id", "status", "value"))
        == cells_before
    )
    assert _stored(w["score"]).status != StatusType.RUNNING.value


@pytest.mark.django_db
@pytest.mark.parametrize("cause", ["active_subset", "bound_execution"])
def test_the_native_path_refusing_a_run_that_is_going_answers_the_same_sentence(
    cause,
):
    from model_hub.services.experiment_submit import (
        ACTIVE_SUBSET_REFUSAL,
        BOUND_EXECUTION_REFUSAL,
        ExecutionKeyConflict,
    )
    from model_hub.views.experiments import _still_running_answer

    error = (
        ExecutionKeyConflict(ACTIVE_SUBSET_REFUSAL)
        if cause == "active_subset"
        else ValueError(BOUND_EXECUTION_REFUSAL)
    )
    evaluations = {"user_eval_metric_ids": ["a1b2c3d4-0000-4000-8000-000000000001"]}

    answer = _still_running_answer(evaluations, error)
    assert answer is not None
    assert answer.status_code == status.HTTP_400_BAD_REQUEST
    assert answer.data["result"] == RUN_GOING

    # A cell retry, and any other native refusal, keep their own answers.
    cells = {**evaluations, "cells": [{"column_id": "c", "row_id": "r"}]}
    assert _still_running_answer(cells, error) is None
    assert _still_running_answer(evaluations, ValueError("Columns not found.")) is None
    assert _still_running_answer({"source_ids": ["s"]}, error) is None


# --- an experiment from before run snapshots: the evaluation sweeper ------------


@pytest.mark.django_db
def test_an_experiment_from_before_snapshots_leaves_it_to_the_sweeper(
    auth_client, presnapshot_experiment
):
    w = presnapshot_experiment
    with patch(CELLS_START, return_value="never") as started:
        response = _update(auth_client, w["base"].id, w["metric"], w["experiment"])

    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["result"] == QUEUED
    started.assert_not_called()
    metric = _stored(w["metric"])
    assert metric.config.get("prompt") == EDITED_PROMPT
    assert metric.status == StatusType.EXPERIMENT_EVALUATION.value
    assert not _running(w["written"]).exists()
    dispatched = swept()
    assert {"type": "experiment", "eval_id": str(w["metric"].id)} in dispatched
    assert {"type": "single", "eval_id": str(w["metric"].id)} not in dispatched


# --- unchanged ------------------------------------------------------------------


@pytest.mark.django_db
def test_saving_without_a_run_only_saves(auth_client, prompt_experiment):
    w = prompt_experiment
    with patch(CELLS_START, return_value="never") as started:
        response = _update(
            auth_client, w["base"].id, w["metric"], w["experiment"], run=False
        )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["result"] == QUEUED
    metric = _stored(w["metric"])
    assert metric.config.get("prompt") == EDITED_PROMPT
    assert metric.status == StatusType.COMPLETED.value
    assert not _running(w["written"]).exists()
    started.assert_not_called()


@pytest.mark.django_db
def test_run_evaluations_on_an_experiment_without_an_http_source_still_starts(
    auth_client, prompt_experiment
):
    w = prompt_experiment
    with patch(CELLS_START, return_value="evaluation-rerun") as started:
        response = _run_evaluations(auth_client, w)

    assert response.status_code == status.HTTP_200_OK, response.content
    started.assert_called_once()
    assert started.call_args.kwargs["eval_only"] is True


@pytest.mark.django_db
def test_a_dataset_evaluation_is_still_queued_for_the_sweeper(
    auth_client, organization, workspace, eval_template
):
    dataset = _dataset(organization, workspace, "Plain dataset")
    metric = UserEvalMetric.objects.create(
        name="dataset-judge",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        template=eval_template,
        status=StatusType.COMPLETED.value,
        config={"prompt": ORIGINAL_PROMPT},
    )
    score = Column.objects.create(
        name=metric.name,
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(
        dataset=dataset,
        row=row,
        column=score,
        value="Passed",
        status=CellStatus.PASS.value,
    )

    with patch(CELLS_START, return_value="never") as started:
        response = _update(auth_client, dataset.id, metric)

    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["result"] == QUEUED
    assert _stored(metric).status == StatusType.NOT_STARTED.value
    assert _running(dataset).filter(column=score).count() == 1
    started.assert_not_called()
    assert {"type": "single", "eval_id": str(metric.id)} in swept()
