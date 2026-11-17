"""HTTP mutation RED specifications; static authoring, not an execution receipt."""

import json
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import pytest

from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.develop_dataset import Cell, Row
from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
from model_hub.services import http_experiment_source as http
from model_hub.services.experiment_submit import state_of
from model_hub.tests.test_http_experiment_source import tenants as tenants
from model_hub.tests.test_http_runner_integration import stage_http_execution

RESULT = {"output": "answer", "error": None, "status_code": 200, "latency_ms": 1.0}


@pytest.fixture
def finished(tenants, request):
    actor, experiment, source, plan = stage_http_execution(tenants)
    failed_indices = {0} if getattr(request, "param", False) else set()
    for index, row_id in enumerate(plan["row_ids"]):
        result = RESULT
        if index in failed_indices:
            result = {
                **RESULT,
                "output": None,
                "status_code": 503,
                "error": {"code": "http_status", "message": "HTTP fixture failed"},
            }
        with patch.object(http, "execute_http_row", return_value=result):
            http.run_http_snapshot_row(
                str(experiment.pk), str(source.pk), row_id, plan["admission_id"]
            )
    http.finish_http_source(str(experiment.pk), str(source.pk), plan["admission_id"])
    experiment.status = StatusType.COMPLETED.value
    experiment.save(update_fields=["status"])
    source.refresh_from_db()
    if failed_indices:
        from model_hub.models.native_experiment_runs import NativeRunBinding

        initial = NativeRunBinding.objects.get(
            snapshot__experiment=experiment,
            variant=source,
            execution_id=plan["admission_id"],
        ).run
        assert list(initial.items.values_list("status", flat=True)) == ["failed"]
    return actor, experiment, source, plan


def put(actor, experiment, body, key=None):
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="mutation-workflow",
    ):
        return actor["client"].put(
            f"/model-hub/experiments/v2/{experiment.pk}/",
            body,
            format="json",
            **headers,
        )


def retry(actor, experiment, body, key=None):
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="mutation-workflow",
    ):
        return actor["client"].post(
            f"/model-hub/experiments/v2/{experiment.pk}/rerun-cells/",
            body,
            format="json",
            **headers,
        )


def run_selected(experiment, source, admission):
    experiment.refresh_from_db()
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    plan = http.setup_http_source(str(experiment.pk), str(source.pk), admission)
    for row_id in plan["row_ids"]:
        http.run_http_snapshot_row(
            str(experiment.pk), str(source.pk), row_id, admission
        )
    http.finish_http_source(str(experiment.pk), str(source.pk), admission)
    experiment.status = StatusType.COMPLETED.value
    experiment.save(update_fields=["status"])
    return plan


@pytest.mark.django_db
def test_http_edit_preserves_source_snapshot_history_and_does_not_send(finished):
    actor, experiment, source, plan = finished
    before = list(
        Cell.no_workspace_objects.filter(column_id=plan["column_id"]).values()
    )
    binding = NativeExperimentSnapshot.objects.get(experiment=experiment)
    old_config = deepcopy(source.legacy_prompt_config)
    body = http.public_http_source(source)
    body.pop("order")
    body["http"]["output_path"] = "$.changed"
    body["variable_mapping"] = {"question": {"kind": "constant", "value": "edited"}}
    with patch.object(http, "execute_http_row") as send:
        response = put(actor, experiment, {"prompt_config": [body], "column_id": None})
    assert response.status_code == 200, response.content
    source.refresh_from_db()
    assert source.pk == body["id"] or str(source.pk) == body["id"]
    assert source.legacy_prompt_config["http"]["output_path"] == "$.changed"
    assert (
        source.legacy_prompt_config["_http_config_history"][-1]["http"]
        == old_config["http"]
    )
    assert NativeExperimentSnapshot.objects.get(experiment=experiment).pk == binding.pk
    assert (
        list(Cell.no_workspace_objects.filter(column_id=plan["column_id"]).values())
        == before
    )
    send.assert_not_called()


@pytest.mark.django_db
def test_http_append_captures_only_new_rows_and_preserves_existing_outputs(finished):
    actor, experiment, source, old = finished
    row = Row.no_workspace_objects.create(dataset=experiment.dataset, order=9)
    original_column = experiment.dataset.column_set.get()
    Cell.no_workspace_objects.create(
        dataset=experiment.dataset,
        row=row,
        column=original_column,
        value="new captured",
    )
    old_cell = Cell.no_workspace_objects.get(
        column_id=old["column_id"], row_id=old["row_ids"][0]
    )
    old_bytes = (old_cell.value, old_cell.value_infos, old_cell.status)
    body = {"append_only": True, "row_ids": [str(actor["exp"]["row"].pk), str(row.pk)]}
    key = str(uuid4())
    response = put(actor, experiment, body, key)
    assert response.status_code == 200, response.content
    current = state_of(experiment)
    selected = current["http_plan"]["targets"][str(source.pk)]["row_ids"]
    assert len(selected) == 1 and selected[0] not in old["row_ids"]
    Cell.no_workspace_objects.filter(dataset=experiment.dataset, row=row).update(
        value="later live edit"
    )
    with patch.object(http, "execute_http_row", return_value=RESULT) as send:
        run_selected(experiment, source, current["admission_id"])
    assert send.call_count == 1
    assert send.call_args.kwargs["values"][str(original_column.pk)] == "new captured"
    assert str(send.call_args.kwargs["row_id"]) == str(row.pk)
    old_cell.refresh_from_db()
    assert (old_cell.value, old_cell.value_infos, old_cell.status) == old_bytes
    response = put(actor, experiment, body, key)
    assert response.status_code == 200
    assert state_of(experiment)["admission_id"] == current["admission_id"]


@pytest.mark.django_db
def test_http_append_new_source_runs_only_new_source(finished):
    actor, experiment, source, old = finished
    entry = http.public_http_source(source)
    entry.pop("id")
    entry.pop("order")
    entry["name"] = "Second endpoint"
    response = put(
        actor, experiment, {"append_only": True, "prompt_config": [entry]}, str(uuid4())
    )
    assert response.status_code == 200, response.content
    targets = state_of(experiment)["http_plan"]["targets"]
    assert str(source.pk) not in targets and len(targets) == 1
    source.refresh_from_db()
    assert (
        source.legacy_prompt_config["_http_execution"]["admission_id"]
        == old["admission_id"]
    )


@pytest.mark.django_db
def test_http_cell_retry_new_identity_keeps_prior_attempt_and_same_key_never_replays(
    finished,
):
    actor, experiment, source, old = finished
    body = {
        "cells": [{"column_id": old["column_id"], "row_id": old["row_ids"][0]}],
        "acknowledge_http_side_effects": True,
    }
    key = str(uuid4())
    response = retry(actor, experiment, body, key)
    assert response.status_code == 200, response.content
    admission = state_of(experiment)["admission_id"]
    assert admission != old["admission_id"]
    with patch.object(http, "execute_http_row", return_value=RESULT) as send:
        selected = run_selected(experiment, source, admission)
        http.run_http_snapshot_row(
            str(experiment.pk), str(source.pk), selected["row_ids"][0], admission
        )
    assert send.call_count == 1
    info = json.loads(
        Cell.no_workspace_objects.get(
            column_id=old["column_id"], row_id=old["row_ids"][0]
        ).value_infos
    )
    assert info["http_attempt_id"].startswith(admission + ":")
    assert info["http_attempt_history"][-1]["value_infos"][
        "http_attempt_id"
    ].startswith(old["admission_id"] + ":")
    response = retry(actor, experiment, body, key)
    assert response.status_code == 200
    assert state_of(experiment)["admission_id"] == admission


@pytest.mark.django_db
def test_http_retry_requires_explicit_side_effect_acknowledgement_and_key(finished):
    actor, experiment, source, old = finished
    body = {"source_ids": [str(source.pk)], "failed_only": True}
    response = retry(actor, experiment, body, str(uuid4()))
    assert response.status_code == 400
    assert b"side effects" in response.content
    body["acknowledge_http_side_effects"] = True
    assert retry(actor, experiment, body).status_code == 400
    assert state_of(experiment)["admission_id"] == old["admission_id"]


@pytest.mark.django_db
@pytest.mark.parametrize("finished", [True], indirect=True)
def test_http_failed_only_retry_freezes_only_failed_rows(finished):
    actor, experiment, source, old = finished
    row = Row.no_workspace_objects.create(dataset=experiment.dataset, order=10)
    Cell.no_workspace_objects.create(
        dataset=experiment.dataset,
        row=row,
        column=experiment.dataset.column_set.get(),
        value="second",
    )
    assert (
        put(
            actor,
            experiment,
            {"append_only": True, "row_ids": [str(row.pk)]},
            str(uuid4()),
        ).status_code
        == 200
    )
    with patch.object(http, "execute_http_row", return_value=RESULT):
        appended = run_selected(
            experiment, source, state_of(experiment)["admission_id"]
        )
    assert (
        Cell.no_workspace_objects.get(
            column_id=old["column_id"], row_id=old["row_ids"][0]
        ).status
        == CellStatus.ERROR.value
    )
    response = retry(
        actor,
        experiment,
        {
            "source_ids": [str(source.pk)],
            "failed_only": True,
            "acknowledge_http_side_effects": True,
        },
        str(uuid4()),
    )
    assert response.status_code == 200, response.content
    selected = state_of(experiment)["http_plan"]["targets"][str(source.pk)]["row_ids"]
    assert selected == old["row_ids"]
    assert not set(selected) & set(appended["row_ids"])
    with patch.object(http, "execute_http_row", return_value=RESULT) as send:
        run_selected(experiment, source, state_of(experiment)["admission_id"])
    assert send.call_count == 1


@pytest.mark.django_db
def test_http_failed_only_retry_includes_rows_that_failed_a_check(finished):
    from types import SimpleNamespace

    from model_hub.models.evals_metric import (
        EvalTemplate,
        EvalTemplateVersion,
        UserEvalMetric,
    )
    from model_hub.tests.test_http_canonical_evidence import _accept, _evaluate, _finish

    actor, experiment, source, old = finished
    added = [
        Row.no_workspace_objects.create(dataset=experiment.dataset, order=10 + i)
        for i in range(2)
    ]
    template = EvalTemplate.no_workspace_objects.create(
        name="HTTP pass/fail check",
        organization=actor["org"],
        workspace=actor["workspace"],
        criteria="Check {{output}}",
        model="fixture-model",
        config={"output": "Pass/Fail", "eval_type_id": "Equals"},
    )
    version = EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=1,
        is_default=True,
        organization=actor["org"],
        workspace=actor["workspace"],
        output_type_normalized="pass_fail",
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="HTTP check",
        user=actor["user"],
        dataset=experiment.snapshot_dataset,
        organization=actor["org"],
        workspace=actor["workspace"],
        template=template,
        pinned_version=version,
        source_id=str(experiment.pk),
        status=StatusType.COMPLETED.value,
        config={"mapping": {"output": "output"}, "reason_column": True},
    )
    experiment.user_eval_template_ids.add(metric)
    body = {"append_only": True, "row_ids": [str(row.pk) for row in added]}
    assert put(actor, experiment, body, str(uuid4())).status_code == 200
    world = SimpleNamespace(
        experiment=experiment,
        source=source,
        metric=metric,
        output=http._http_output_column(experiment, source),
        admission=state_of(experiment)["admission_id"],
    )
    _accept(world)
    failed_check, errored = sorted(world.plan["row_ids"])
    for link in http._http_rows(http._http_snapshot(experiment), world.source):
        row_id = str(link.snapshot_row_id)
        result = {**RESULT, "error": {"message": "unavailable"}}
        if row_id != errored:
            result = RESULT
        with patch.object(http, "execute_http_row", return_value=result):
            http.run_http_snapshot_row(
                str(experiment.pk),
                str(source.pk),
                row_id,
                world.admission,
            )
        if row_id == failed_check:
            _evaluate(world, link, 0)
    from model_hub.services.http_run_recording import _recorded_http_evaluation_rows

    assert _recorded_http_evaluation_rows(
        world.plan,
        metric.pk,
        [failed_check, errored],
    ) == {failed_check}
    _finish(world)
    body = {
        "source_ids": [str(source.pk)],
        "failed_only": True,
        "acknowledge_http_side_effects": True,
    }
    response = retry(actor, experiment, body, str(uuid4()))
    assert response.status_code == 200, response.content
    selected = state_of(experiment)["http_plan"]["targets"][str(source.pk)]["row_ids"]
    # The row whose check failed reruns with the row whose call errored; the
    # row that passed its check does not.
    assert selected == sorted([failed_check, errored])


@pytest.mark.django_db
def test_http_unknown_outcome_is_settled_not_automatically_replayed(finished):
    actor, experiment, source, old = finished
    body = {"source_ids": [str(source.pk)], "acknowledge_http_side_effects": True}
    assert retry(actor, experiment, body, str(uuid4())).status_code == 200
    admission = state_of(experiment)["admission_id"]
    experiment.refresh_from_db()
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    selected = http.setup_http_source(str(experiment.pk), str(source.pk), admission)
    with patch.object(
        http, "execute_http_row", side_effect=RuntimeError("lost worker")
    ) as send:
        with pytest.raises(RuntimeError):
            http.run_http_snapshot_row(
                str(experiment.pk), str(source.pk), selected["row_ids"][0], admission
            )
        settled = http.settle_http_row(
            str(experiment.pk), str(source.pk), selected["row_ids"][0], admission
        )
        assert settled["status"] == "FAILED"
        http.run_http_snapshot_row(
            str(experiment.pk), str(source.pk), selected["row_ids"][0], admission
        )
    assert send.call_count == 1


@pytest.mark.django_db
def test_http_cross_org_edit_append_and_retry_are_refused(finished, tenants):
    _, experiment, source, old = finished
    foreign = tenants["b"]
    body = http.public_http_source(source)
    body.pop("order")
    before = deepcopy(source.legacy_prompt_config)
    assert put(foreign, experiment, {"prompt_config": [body]}).status_code in {
        400,
        403,
        404,
    }
    assert put(
        foreign,
        experiment,
        {"append_only": True, "row_ids": [str(foreign["exp"]["row"].pk)]},
        str(uuid4()),
    ).status_code in {400, 403, 404}
    assert retry(
        foreign,
        experiment,
        {"source_ids": [str(source.pk)], "acknowledge_http_side_effects": True},
        str(uuid4()),
    ).status_code in {400, 403, 404}
    source.refresh_from_db()
    assert source.legacy_prompt_config == before
    assert state_of(experiment)["admission_id"] == old["admission_id"]


@pytest.mark.django_db
def test_http_foreign_mapping_and_row_are_rejected_without_partial_writes(
    finished, tenants
):
    actor, experiment, source, old = finished
    foreign = tenants["b"]["exp"]
    body = http.public_http_source(source)
    body.pop("order")
    body["variable_mapping"] = {"question": str(foreign["dataset"].column_set.get().pk)}
    assert put(actor, experiment, {"prompt_config": [body]}).status_code == 400
    assert (
        put(
            actor,
            experiment,
            {"append_only": True, "row_ids": [str(foreign["row"].pk)]},
            str(uuid4()),
        ).status_code
        == 400
    )
    assert state_of(experiment)["admission_id"] == old["admission_id"]


@pytest.mark.django_db
def test_http_mutation_setup_never_schedules_prompts_or_resets_unselected_cells(
    finished,
):
    from tfc.temporal.experiments.activities import _setup_experiment_sync

    actor, experiment, source, old = finished
    row = Row.no_workspace_objects.create(dataset=experiment.dataset, order=10)
    before = list(Cell.no_workspace_objects.filter(column_id=old["column_id"]).values())
    assert (
        put(
            actor,
            experiment,
            {"append_only": True, "row_ids": [str(row.pk)]},
            str(uuid4()),
        ).status_code
        == 200
    )
    admission = state_of(experiment)["admission_id"]
    with (
        patch("tfc.temporal.experiments.activities.close_old_connections"),
        patch(
            "tfc.temporal.experiments.activities._get_config_ids",
            return_value=(["untouched-prompt"], ["untouched-agent"]),
        ),
    ):
        plan = _setup_experiment_sync(str(experiment.pk), admission)
        replay = _setup_experiment_sync(str(experiment.pk), admission)
    assert plan["http_sources"] == [str(source.pk)]
    assert plan["prompt_configs"] == plan["agent_configs"] == []
    assert plan["has_base_column"] is False
    assert replay == plan
    assert (
        list(Cell.no_workspace_objects.filter(column_id=old["column_id"]).values())
        == before
    )


@pytest.mark.django_db
def test_http_retry_key_conflict_does_not_stage_another_attempt(finished):
    actor, experiment, source, old = finished
    key = str(uuid4())
    body = {"source_ids": [str(source.pk)], "acknowledge_http_side_effects": True}
    assert retry(actor, experiment, body, key).status_code == 200
    before = state_of(experiment)["admission_id"]
    body["failed_only"] = True
    assert retry(actor, experiment, body, key).status_code == 409
    assert state_of(experiment)["admission_id"] == before


@pytest.mark.django_db
def test_http_subset_worker_and_evaluation_reject_unselected_row(finished):
    from model_hub.services.http_evaluation_fence import (
        HttpEvaluationStopped,
        validate_http_eval_input,
    )

    actor, experiment, source, old = finished
    row = Row.no_workspace_objects.create(dataset=experiment.dataset, order=10)
    assert (
        put(
            actor,
            experiment,
            {"append_only": True, "row_ids": [str(row.pk)]},
            str(uuid4()),
        ).status_code
        == 200
    )
    admission = state_of(experiment)["admission_id"]
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    selected = http.setup_http_source(str(experiment.pk), str(source.pk), admission)
    with patch.object(http, "execute_http_row") as send, pytest.raises(ValueError):
        http.run_http_snapshot_row(
            str(experiment.pk), str(source.pk), old["row_ids"][0], admission
        )
    send.assert_not_called()
    context = {
        key: selected[key]
        for key in (
            "experiment_id",
            "source_id",
            "admission_id",
            "dataset_id",
            "column_id",
        )
    }
    with pytest.raises(HttpEvaluationStopped):
        validate_http_eval_input(
            context,
            str(experiment.pk),
            selected["dataset_id"],
            selected["column_id"],
            old["row_ids"],
            [],
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("retry_kind", "finished"),
    [("cell", False), ("failed_only", True)],
    indirect=["finished"],
)
def test_http_rename_retry_preserves_evaluation_columns_and_completes(
    finished, retry_kind
):
    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Column
    from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
    from model_hub.views.eval_runner import EvaluationRunner
    from model_hub.views.experiment_runner import (
        ExperimentRunner,
        check_and_update_experiment_status,
    )

    actor, experiment, source, old = finished
    # Keep a second, unselected row to pin selected-only reset/write behavior.
    added = Row.no_workspace_objects.create(dataset=experiment.dataset, order=10)
    response = put(
        actor,
        experiment,
        {"append_only": True, "row_ids": [str(added.pk)]},
        str(uuid4()),
    )
    assert response.status_code == 200, response.content
    with patch.object(http, "execute_http_row", return_value=RESULT):
        appended = run_selected(
            experiment, source, state_of(experiment)["admission_id"]
        )
    selected_id, untouched_id = old["row_ids"][0], appended["row_ids"][0]
    template = EvalTemplate.no_workspace_objects.create(
        name="HTTP rename evaluator",
        organization=actor["org"],
        workspace=actor["workspace"],
        criteria="Check {{output}}",
        model="fixture-model",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="HTTP score",
        dataset=experiment.snapshot_dataset,
        organization=actor["org"],
        workspace=actor["workspace"],
        template=template,
        source_id=str(experiment.pk),
        status=StatusType.COMPLETED.value,
        config={"mapping": {"output": "output"}, "reason_column": True},
    )
    experiment.user_eval_template_ids.add(metric)
    runner = ExperimentRunner(experiment.pk)
    runner.load_experiment()
    runner.precreate_eval_columns_for_edts([str(source.pk)])
    linked = source.columns.filter(dataset=experiment.snapshot_dataset, deleted=False)
    score = linked.get(source=SourceChoices.EXPERIMENT_EVALUATION.value)
    reason = linked.get(source=SourceChoices.EVALUATION_REASON.value)
    for column in (score, reason):
        for row_id in (selected_id, untouched_id):
            Cell.no_workspace_objects.create(
                dataset=experiment.snapshot_dataset,
                column=column,
                row_id=row_id,
                value="original result",
                status=CellStatus.PASS.value,
                value_infos=json.dumps({"reason": "original reason"}),
            )
    linked.update(status=StatusType.COMPLETED.value)
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    assert check_and_update_experiment_status(experiment.pk)
    experiment.refresh_from_db()
    assert experiment.status == StatusType.COMPLETED.value
    ids = set(linked.values_list("pk", flat=True))
    untouched = list(
        Cell.no_workspace_objects.filter(
            column__in=linked, row_id=untouched_id
        ).values()
    )

    source.refresh_from_db()
    entry = http.public_http_source(source)
    entry.pop("order")
    entry["name"] = "Renamed endpoint"
    assert put(actor, experiment, {"prompt_config": [entry]}).status_code == 200
    if retry_kind == "failed_only":
        assert (
            Cell.no_workspace_objects.get(
                column_id=old["column_id"], row_id=selected_id
            ).status
            == CellStatus.ERROR.value
        )
        body = {"source_ids": [str(source.pk)], "failed_only": True}
    else:
        body = {"cells": [{"column_id": old["column_id"], "row_id": selected_id}]}
    response = retry(
        actor, experiment, {**body, "acknowledge_http_side_effects": True}, str(uuid4())
    )
    assert response.status_code == 200, response.content
    admission_id = state_of(experiment)["admission_id"]
    runner.load_experiment()
    assert http.prepare_http_experiment(runner, admission_id)
    assert set(linked.values_list("pk", flat=True)) == ids
    identities = list(linked.values_list("dataset_id", "source", "source_id"))
    assert len(identities) == len(set(identities))
    assert (
        Cell.no_workspace_objects.filter(
            column_id__in=[score.pk, reason.pk],
            row_id=selected_id,
            status=CellStatus.RUNNING.value,
        ).count()
        == 2
    )

    plan = http.setup_http_source(str(experiment.pk), str(source.pk), admission_id)
    assert plan["row_ids"] == [selected_id]
    with patch.object(http, "execute_http_row", return_value=RESULT) as send:
        http.run_http_snapshot_row(
            str(experiment.pk), str(source.pk), selected_id, admission_id
        )
    send.assert_called_once()
    output = Column.no_workspace_objects.get(pk=old["column_id"])
    context = {
        key: plan[key]
        for key in (
            "experiment_id",
            "source_id",
            "admission_id",
            "dataset_id",
            "column_id",
        )
    }
    evaluator = EvaluationRunner(
        metric.pk,
        experiment_dataset=source,
        column=output,
        format_output=True,
        source_configs={"experiment_id": str(experiment.pk), "http_context": context},
    )
    evaluator.load_user_eval_metric()
    # The same resolution and fenced persistence used by a real evaluator batch.
    # Supply its deterministic result without calling an external evaluator.
    current_score = evaluator._create_or_update_column(
        evaluator.dataset,
        evaluator._get_column_config(evaluator.dataset),
        new_column=True,
    )
    current_reason = evaluator._create_reason_column(
        evaluator.dataset, "reason", parent_column=current_score
    )
    assert (current_score.pk, current_reason.pk) == (score.pk, reason.pk)
    assert current_score.name == "HTTP score-Renamed endpoint"
    assert current_reason.name == "HTTP score-Renamed endpoint-reason"
    row = Row.no_workspace_objects.get(pk=selected_id)
    evaluator._create_cell(
        evaluator.dataset,
        current_score,
        row,
        {"reason": "matched"},
        "1",
        CellStatus.PASS.value,
    )
    evaluator._create_reason_cell(
        evaluator.dataset,
        current_reason,
        row,
        {"reason": "matched"},
        "matched",
        CellStatus.PASS.value,
    )
    http.finish_http_source(str(experiment.pk), str(source.pk), admission_id)
    assert set(linked.values_list("pk", flat=True)) == ids
    assert Cell.no_workspace_objects.filter(
        column__in=linked, row_id=selected_id
    ).count() == len(ids)
    assert (
        not Cell.no_workspace_objects.filter(column__in=linked, row_id=selected_id)
        .exclude(
            status__in=[CellStatus.PASS.value, CellStatus.ERROR.value],
        )
        .exists()
    )
    assert (
        list(
            Cell.no_workspace_objects.filter(
                column__in=linked, row_id=untouched_id
            ).values()
        )
        == untouched
    )
    experiment.refresh_from_db()
    assert experiment.status == StatusType.RUNNING.value
    assert check_and_update_experiment_status(experiment.pk)
    experiment.refresh_from_db()
    assert experiment.status == StatusType.COMPLETED.value


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation", ["retry", "append"])
def test_http_mutation_real_start_gate_ignores_unselected_unavailable_agent(
    finished, operation
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from temporalio.client import WorkflowExecutionStatus

    from agent_playground.models import Graph, GraphVersion
    from model_hub.models.experiments import (
        ExperimentAgentConfig,
        ExperimentDatasetTable,
    )
    from model_hub.services.experiment_target_admission import AgentTargetNotQualified
    from tfc.temporal.experiments.activities import _setup_experiment_sync
    from tfc.temporal.experiments.client import start_experiment_v2_workflow

    actor, experiment, source, old = finished
    graph = Graph.no_workspace_objects.create(
        name="Unavailable unrelated agent",
        organization=actor["org"],
        workspace=actor["workspace"],
        created_by=actor["user"],
    )
    version = GraphVersion.no_workspace_objects.create(
        graph=graph, version_number=1, status="active"
    )
    agent_source = ExperimentDatasetTable.no_workspace_objects.create(
        name="Unselected agent",
        experiment=experiment,
        status=StatusType.COMPLETED.value,
    )
    agent = ExperimentAgentConfig.no_workspace_objects.create(
        experiment_dataset=agent_source,
        graph=graph,
        graph_version=version,
        name="Unselected agent",
        order=1,
    )
    GraphVersion._base_manager.filter(pk=version.pk).update(deleted=True)
    before_agent = (
        ExperimentAgentConfig.no_workspace_objects.filter(pk=agent.pk).values().get()
    )
    description = SimpleNamespace(
        status=WorkflowExecutionStatus.COMPLETED,
        memo=AsyncMock(return_value={"experiment_submission_id": old["admission_id"]}),
    )
    workflow_id = f"experiment-{experiment.pk}"

    async def start(_workflow, workflow_input, **kwargs):
        description.memo.return_value = kwargs["memo"]
        description.status = WorkflowExecutionStatus.RUNNING
        return SimpleNamespace(id=kwargs["id"])

    remote = SimpleNamespace(
        get_workflow_handle=Mock(
            return_value=SimpleNamespace(describe=AsyncMock(return_value=description))
        ),
        start_workflow=AsyncMock(side_effect=start),
    )
    with (
        patch(
            "tfc.temporal.experiments.admission.get_client",
            AsyncMock(return_value=remote),
        ),
        patch(
            "tfc.temporal.experiments.client.get_client", AsyncMock(return_value=remote)
        ),
    ):
        # Do not mock the starter, either admission gate, or their ORM reads.
        if operation == "retry":
            response = actor["client"].post(
                f"/model-hub/experiments/v2/{experiment.pk}/rerun-cells/",
                {"source_ids": [str(source.pk)], "acknowledge_http_side_effects": True},
                format="json",
                HTTP_IDEMPOTENCY_KEY=str(uuid4()),
            )
        else:
            entry = http.public_http_source(source)
            entry.pop("id")
            entry.pop("order")
            response = actor["client"].put(
                f"/model-hub/experiments/v2/{experiment.pk}/",
                {"append_only": True, "prompt_config": [entry]},
                format="json",
                HTTP_IDEMPOTENCY_KEY=str(uuid4()),
            )
        assert response.status_code == 200, response.content
        remote.start_workflow.assert_awaited_once()
        current = state_of(experiment)
        assert current["state"] == "accepted"
        assert current["workflow_id"] == workflow_id
        assert remote.start_workflow.await_args.kwargs["memo"] == {
            "experiment_submission_id": current["admission_id"]
        }
        workflow_input = remote.start_workflow.await_args.args[1]
        assert not workflow_input.native_runs
        assert not workflow_input.rerun_prompt_config_ids
        assert not workflow_input.rerun_agent_config_ids
        with patch("tfc.temporal.experiments.activities.close_old_connections"):
            plan = _setup_experiment_sync(str(experiment.pk), current["admission_id"])
        assert set(plan["http_sources"]) == set(current["http_plan"]["targets"])
        assert plan["prompt_configs"] == plan["agent_configs"] == []
        assert plan["has_base_column"] is False
        # Ordinary whole-run starts still validate and reject the revoked agent.
        with pytest.raises(AgentTargetNotQualified):
            start_experiment_v2_workflow(str(experiment.pk), admission_id=str(uuid4()))
        remote.start_workflow.assert_awaited_once()
        # An HTTP flag cannot admit a stale or fabricated plan identity.
        with pytest.raises(ValueError, match="committed admission plan"):
            start_experiment_v2_workflow(
                str(experiment.pk), admission_id=str(uuid4()), http_only=True
            )
        remote.start_workflow.assert_awaited_once()
    assert (
        ExperimentAgentConfig.no_workspace_objects.filter(pk=agent.pk).values().get()
        == before_agent
    )
    version.refresh_from_db()
    assert version.deleted is True


@pytest.mark.django_db
def test_http_append_variant_uses_captured_mapping_after_live_column_deleted(finished):
    from model_hub.models.develop_dataset import Column
    from model_hub.models.experiments import ExperimentDatasetTable

    actor, experiment, source, old = finished
    binding = NativeExperimentSnapshot.objects.get(experiment=experiment)
    original_column = experiment.dataset.column_set.get()
    captured_value = Cell.no_workspace_objects.get(
        dataset_id=experiment.snapshot_dataset_id,
        column_id=binding.column_map[str(original_column.pk)],
        row_id=old["row_ids"][0],
    ).value
    original_identity = (
        binding.pk,
        binding.dataset_version_id,
        deepcopy(binding.column_map),
    )
    prior_receipts = list(
        Cell.no_workspace_objects.filter(column_id=old["column_id"]).values()
    )
    Column.no_workspace_objects.filter(pk=original_column.pk).update(deleted=True)
    Cell.no_workspace_objects.filter(
        dataset=experiment.dataset, column=original_column
    ).update(value="changed after capture")
    entry = http.public_http_source(source)
    entry.pop("id")
    entry.pop("order")
    entry["name"] = "Captured input variant"
    response = put(
        actor, experiment, {"append_only": True, "prompt_config": [entry]}, str(uuid4())
    )
    assert response.status_code == 200, response.content
    current = state_of(experiment)
    assert str(source.pk) not in current["http_plan"]["targets"]
    assert len(current["http_plan"]["targets"]) == 1
    added_id = next(iter(current["http_plan"]["targets"]))
    added_source = ExperimentDatasetTable.no_workspace_objects.get(pk=added_id)
    assert (
        added_source.legacy_prompt_config["variable_mapping"]
        == entry["variable_mapping"]
    )
    with patch.object(http, "execute_http_row", return_value=RESULT) as send:
        plan = run_selected(experiment, added_source, current["admission_id"])
    assert plan["row_ids"] == old["row_ids"]
    send.assert_called_once()
    assert send.call_args.kwargs["values"][str(original_column.pk)] == captured_value
    binding.refresh_from_db()
    assert (
        binding.pk,
        binding.dataset_version_id,
        binding.column_map,
    ) == original_identity
    assert (
        list(Cell.no_workspace_objects.filter(column_id=old["column_id"]).values())
        == prior_receipts
    )
    # Initial creation must still reject a deleted live mapping column.
    before = ExperimentDatasetTable.no_workspace_objects.filter(
        experiment=experiment
    ).count()
    with pytest.raises(ValueError, match="mapping column is unavailable"):
        http.persist_http_source(experiment, entry)
    assert (
        ExperimentDatasetTable.no_workspace_objects.filter(
            experiment=experiment
        ).count()
        == before
    )


@pytest.mark.django_db
@pytest.mark.parametrize("invalid_mapping", ["not_captured", "deleted_snapshot_column"])
def test_http_append_variant_still_rejects_unavailable_snapshot_mapping(
    finished, invalid_mapping
):
    from model_hub.models.develop_dataset import Column
    from model_hub.models.experiments import ExperimentDatasetTable

    actor, experiment, source, old = finished
    entry = http.public_http_source(source)
    entry.pop("id")
    entry.pop("order")
    if invalid_mapping == "not_captured":
        column = Column.no_workspace_objects.create(
            dataset=experiment.dataset,
            name="Later input",
            data_type="text",
            source="OTHERS",
        )
        entry["variable_mapping"] = {"question": str(column.pk)}
    else:
        binding = NativeExperimentSnapshot.objects.get(experiment=experiment)
        Column.no_workspace_objects.filter(pk__in=binding.column_map.values()).update(
            deleted=True
        )
    before = ExperimentDatasetTable.no_workspace_objects.filter(
        experiment=experiment
    ).count()
    response = put(
        actor, experiment, {"append_only": True, "prompt_config": [entry]}, str(uuid4())
    )
    assert response.status_code == 400, response.content
    assert (
        ExperimentDatasetTable.no_workspace_objects.filter(
            experiment=experiment
        ).count()
        == before
    )
    assert state_of(experiment)["admission_id"] == old["admission_id"]


def _http_saved_bytes(experiment):
    """Include cells, metadata, bindings and admission journal in preview checks."""
    from model_hub.models.develop_dataset import Column, Dataset
    from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable

    dataset_ids = [experiment.dataset_id, experiment.snapshot_dataset_id]
    records = [
        list(
            model.no_workspace_objects.filter(dataset_id__in=dataset_ids)
            .order_by("pk")
            .values()
        )
        for model in (Row, Column, Cell)
    ]
    records.extend(
        [
            list(
                Dataset.no_workspace_objects.filter(pk__in=dataset_ids)
                .order_by("pk")
                .values()
            ),
            list(
                ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk).values()
            ),
            list(
                ExperimentDatasetTable.no_workspace_objects.filter(
                    experiment=experiment
                )
                .order_by("pk")
                .values()
            ),
            list(
                NativeExperimentSnapshot.objects.filter(experiment=experiment).values()
            ),
        ]
    )
    return json.dumps(records, sort_keys=True, default=str)


@pytest.mark.django_db
@pytest.mark.parametrize("edit_kind", ["change", "remove", "add"])
def test_http_save_only_eval_edit_keeps_mixed_source_receipts(finished, edit_kind):
    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Column
    from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
    from model_hub.models.experiments import (
        ExperimentDatasetTable,
        ExperimentPromptConfig,
    )
    from model_hub.views.experiment_runner import ExperimentRunner

    actor, experiment, source, old = finished
    prompt_source = ExperimentDatasetTable.no_workspace_objects.create(
        name="Saved prompt",
        experiment=experiment,
        status=StatusType.COMPLETED.value,
    )
    ExperimentPromptConfig.no_workspace_objects.create(
        experiment_dataset=prompt_source,
        name="Saved prompt",
        model="fixture-model",
        messages=[{"role": "user", "content": "A saved prompt"}],
    )
    output = Column.no_workspace_objects.create(
        dataset=experiment.snapshot_dataset,
        name="Saved prompt",
        data_type="text",
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(prompt_source.pk),
        status=StatusType.COMPLETED.value,
    )
    prompt_source.columns.add(output)
    Cell.no_workspace_objects.create(
        dataset=experiment.snapshot_dataset,
        row_id=old["row_ids"][0],
        column=output,
        value="prompt answer",
        status=CellStatus.PASS.value,
    )
    template = EvalTemplate.no_workspace_objects.create(
        name="Saved evaluation",
        organization=actor["org"],
        workspace=actor["workspace"],
        criteria="Check {{output}}",
        model="fixture-model",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="Saved score",
        dataset=experiment.snapshot_dataset,
        organization=actor["org"],
        workspace=actor["workspace"],
        template=template,
        source_id=str(experiment.pk),
        status=StatusType.COMPLETED.value,
        model="fixture-model",
        config={"mapping": {"output": "output"}, "reason_column": True},
    )
    experiment.user_eval_template_ids.add(metric)
    runner = ExperimentRunner(experiment.pk)
    runner.load_experiment()
    runner.precreate_eval_columns_for_edts([str(source.pk), str(prompt_source.pk)])
    eval_columns = Column.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset,
        source__in=[
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EVALUATION_REASON.value,
        ],
        deleted=False,
    )
    assert eval_columns.count() == 4
    for column in eval_columns:
        Cell.no_workspace_objects.create(
            dataset=experiment.snapshot_dataset,
            column=column,
            row_id=old["row_ids"][0],
            value="previous score or reason",
            status=CellStatus.PASS.value,
            value_infos=json.dumps(
                {"reason": "saved", "http_eval_history": [{"value": "older"}]}
            ),
        )
    eval_columns.update(status=StatusType.COMPLETED.value)
    cells = Cell.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset
    ).order_by("pk")
    before = json.dumps(list(cells.values()), sort_keys=True, default=str)
    column_ids = set(
        Column.no_workspace_objects.filter(
            dataset=experiment.snapshot_dataset
        ).values_list("pk", flat=True)
    )
    entry = {
        "id": str(metric.pk),
        "template_id": str(template.pk),
        "name": metric.name,
        "config": deepcopy(metric.config),
        "model": metric.model,
    }
    entries = [entry]
    if edit_kind == "change":
        entry["model"] = "edited-evaluator-model"
    elif edit_kind == "remove":
        entries = []
    else:
        entries.append(
            {
                "template_id": str(template.pk),
                "name": "New saved evaluator",
                "config": deepcopy(metric.config),
                "model": "fixture-model",
            }
        )
    with (
        patch.object(http, "execute_http_row") as send,
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start,
    ):
        response = actor["client"].put(
            f"/model-hub/experiments/v2/{experiment.pk}/",
            {"user_eval_metrics": entries},
            format="json",
        )
    assert response.status_code == 200, response.content
    send.assert_not_called()
    start.assert_not_called()
    assert json.dumps(list(cells.values()), sort_keys=True, default=str) == before
    # Save can defer column creation entirely; any new metadata must be idle.
    assert (
        not Column.no_workspace_objects.filter(dataset=experiment.snapshot_dataset)
        .exclude(
            pk__in=column_ids,
        )
        .exclude(status=StatusType.NOT_STARTED.value)
        .exists()
    )
    if edit_kind == "change":
        metric.refresh_from_db()
        assert metric.model == "edited-evaluator-model"
        prompt_cells = Cell.no_workspace_objects.filter(
            column__in=prompt_source.columns.all()
        ).order_by("pk")
        prompt_before = json.dumps(
            list(prompt_cells.values()), sort_keys=True, default=str
        )
        response = retry(
            actor,
            experiment,
            {
                "cells": [{"column_id": old["column_id"], "row_id": old["row_ids"][0]}],
                "acknowledge_http_side_effects": True,
            },
            str(uuid4()),
        )
        assert response.status_code == 200, response.content
        runner.load_experiment()
        assert http.prepare_http_experiment(
            runner, state_of(experiment)["admission_id"]
        )
        assert (
            json.dumps(list(prompt_cells.values()), sort_keys=True, default=str)
            == prompt_before
        )
        assert (
            Cell.no_workspace_objects.filter(
                column__in=source.columns.exclude(
                    source=SourceChoices.EXPERIMENT.value
                ),
                row_id=old["row_ids"][0],
                status=CellStatus.RUNNING.value,
            ).count()
            == 2
        )
    elif edit_kind == "remove":
        assert not experiment.user_eval_template_ids.exists()
        assert not eval_columns.exists()
    else:
        assert experiment.user_eval_template_ids.count() == 2


@pytest.mark.django_db
def test_http_composite_rename_failed_retry_preserves_score_identity(finished):
    from model_hub.models.choices import SourceChoices
    from model_hub.models.evals_metric import (
        CompositeEvalChild,
        EvalTemplate,
        UserEvalMetric,
    )
    from model_hub.views.experiment_runner import (
        ExperimentRunner,
        check_and_update_experiment_status,
    )
    from tfc.temporal.experiments.activities import _create_error_eval_cells_sync

    actor, experiment, source, old = finished
    added = Row.no_workspace_objects.create(dataset=experiment.dataset, order=10)
    response = put(
        actor,
        experiment,
        {"append_only": True, "row_ids": [str(added.pk)]},
        str(uuid4()),
    )
    assert response.status_code == 200, response.content
    with patch.object(http, "execute_http_row", return_value=RESULT):
        appended = run_selected(
            experiment, source, state_of(experiment)["admission_id"]
        )
    selected_id, untouched_id = old["row_ids"][0], appended["row_ids"][0]
    template = EvalTemplate.no_workspace_objects.create(
        name="Composite HTTP evaluation",
        organization=actor["org"],
        workspace=actor["workspace"],
        template_type="composite",
        aggregation_enabled=True,
        aggregation_function="weighted_avg",
    )
    child = EvalTemplate.no_workspace_objects.create(
        name="Composite child",
        organization=actor["org"],
        workspace=actor["workspace"],
        criteria="Check {{output}}",
        model="fixture-model",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    CompositeEvalChild.no_workspace_objects.create(parent=template, child=child)
    metric = UserEvalMetric.no_workspace_objects.create(
        name="Composite score",
        dataset=experiment.snapshot_dataset,
        organization=actor["org"],
        workspace=actor["workspace"],
        template=template,
        source_id=str(experiment.pk),
        status=StatusType.COMPLETED.value,
        config={"mapping": {}},
    )
    experiment.user_eval_template_ids.add(metric)
    runner = ExperimentRunner(experiment.pk)
    runner.load_experiment()
    runner.precreate_eval_columns_for_edts([str(source.pk)])
    linked = source.columns.filter(dataset=experiment.snapshot_dataset, deleted=False)
    score = linked.get(source=SourceChoices.EXPERIMENT_EVALUATION.value)
    for row_id in (selected_id, untouched_id):
        Cell.no_workspace_objects.create(
            dataset=experiment.snapshot_dataset,
            column=score,
            row_id=row_id,
            value="prior endpoint failure",
            status=CellStatus.ERROR.value,
            value_infos=json.dumps({"reason": "prior endpoint failure"}),
        )
    Cell.no_workspace_objects.filter(
        column_id=old["column_id"], row_id=selected_id
    ).update(
        status=CellStatus.ERROR.value,
    )
    linked.update(status=StatusType.COMPLETED.value)
    ids = set(linked.values_list("pk", flat=True))
    untouched = list(
        Cell.no_workspace_objects.filter(column__in=linked, row_id=untouched_id)
        .order_by("pk")
        .values()
    )
    source.refresh_from_db()
    entry = http.public_http_source(source)
    entry.pop("order")
    entry["name"] = "Renamed failing endpoint"
    assert put(actor, experiment, {"prompt_config": [entry]}).status_code == 200
    response = retry(
        actor,
        experiment,
        {
            "cells": [{"column_id": old["column_id"], "row_id": selected_id}],
            "acknowledge_http_side_effects": True,
        },
        str(uuid4()),
    )
    assert response.status_code == 200, response.content
    admission = state_of(experiment)["admission_id"]
    runner.load_experiment()
    assert http.prepare_http_experiment(runner, admission)
    assert set(linked.values_list("pk", flat=True)) == ids
    score.refresh_from_db()
    assert score.name == "Composite score-Renamed failing endpoint"
    plan = http.setup_http_source(str(experiment.pk), str(source.pk), admission)
    context = {
        key: plan[key]
        for key in (
            "experiment_id",
            "source_id",
            "admission_id",
            "dataset_id",
            "column_id",
        )
    }
    failure = {
        "output": None,
        "error": {"code": "http_status", "message": "Endpoint failed"},
        "status_code": 503,
        "latency_ms": 1.0,
    }
    with patch.object(http, "execute_http_row", return_value=failure) as send:
        result = http.run_http_snapshot_row(
            str(experiment.pk), str(source.pk), selected_id, admission
        )
    assert result["status"] == "FAILED"
    send.assert_called_once()
    with patch("tfc.temporal.experiments.activities.close_old_connections"):
        settled = _create_error_eval_cells_sync(
            [selected_id],
            old["column_id"],
            str(experiment.snapshot_dataset_id),
            str(experiment.pk),
            [str(metric.pk)],
            "Endpoint failed",
            http_context=context,
        )
    assert settled == {"cells_created": 1, "status": "COMPLETED"}
    http.finish_http_source(str(experiment.pk), str(source.pk), admission)
    assert linked.get(source=SourceChoices.EXPERIMENT_EVALUATION.value).pk == score.pk
    identities = list(linked.values_list("dataset_id", "source", "source_id"))
    assert len(identities) == len(set(identities))
    assert set(linked.values_list("pk", flat=True)) == ids
    assert (
        not Cell.no_workspace_objects.filter(column__in=linked, row_id=selected_id)
        .exclude(
            status__in=[CellStatus.PASS.value, CellStatus.ERROR.value],
        )
        .exists()
    )
    assert (
        list(
            Cell.no_workspace_objects.filter(column__in=linked, row_id=untouched_id)
            .order_by("pk")
            .values()
        )
        == untouched
    )
    assert check_and_update_experiment_status(experiment.pk)
    experiment.refresh_from_db()
    assert experiment.status == StatusType.COMPLETED.value


@pytest.mark.django_db
@pytest.mark.parametrize("candidate_count", [1, 2])
@pytest.mark.parametrize("source_count", [1, 2])
def test_http_append_preview_uses_original_ids_without_writes(
    finished, candidate_count, source_count
):
    actor, experiment, source, old = finished
    # Captured A remains held even when the current dataset now contains only B/C.
    Row.no_workspace_objects.filter(pk=actor["exp"]["row"].pk).update(deleted=True)
    candidates = [
        Row.no_workspace_objects.create(dataset=experiment.dataset, order=10 + i)
        for i in range(candidate_count)
    ]
    if source_count == 2:
        entry = http.public_http_source(source)
        entry.pop("id")
        entry.pop("order")
        other = http.persist_http_source(experiment, entry, order=1)
        http.create_http_output_column(experiment, other)
    before = _http_saved_bytes(experiment)
    with (
        patch.object(http, "execute_http_row") as send,
        patch.object(http, "stage_http_mutation") as stage,
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start,
    ):
        response = actor["client"].put(
            f"/model-hub/experiments/v2/{experiment.pk}/",
            {
                "append_only": True,
                "preview": True,
                "row_ids": [str(row.pk) for row in candidates],
            },
            format="json",
        )
    assert response.status_code == 200, response.content
    assert response.json() == {
        "status": True,
        "result": {
            "preview": True,
            "new_row_ids": [str(row.pk) for row in candidates],
            "request_count": candidate_count * source_count,
            "held_count": 1,
            "limit": 10000,
        },
    }
    stage.assert_not_called()
    start.assert_not_called()
    send.assert_not_called()
    assert _http_saved_bytes(experiment) == before
    # Submit the exact previewed IDs, not an aggregate-count reconstruction.
    response = put(
        actor,
        experiment,
        {
            "append_only": True,
            "row_ids": response.json()["result"]["new_row_ids"],
        },
        str(uuid4()),
    )
    assert response.status_code == 200, response.content
    targets = state_of(experiment)["http_plan"]["targets"]
    assert (
        sum(len(target["row_ids"]) for target in targets.values())
        == candidate_count * source_count
    )


@pytest.mark.django_db
def test_http_append_preview_excludes_original_and_appended_captures(finished):
    actor, experiment, source, old = finished
    captured = Row.no_workspace_objects.create(dataset=experiment.dataset, order=10)
    response = put(
        actor,
        experiment,
        {"append_only": True, "row_ids": [str(captured.pk)]},
        str(uuid4()),
    )
    assert response.status_code == 200, response.content
    with patch.object(http, "execute_http_row", return_value=RESULT):
        run_selected(experiment, source, state_of(experiment)["admission_id"])
    fresh = Row.no_workspace_objects.create(dataset=experiment.dataset, order=11)
    before = _http_saved_bytes(experiment)
    response = put(
        actor,
        experiment,
        {
            "append_only": True,
            "preview": True,
            "row_ids": [str(actor["exp"]["row"].pk), str(captured.pk), str(fresh.pk)],
        },
    )
    assert response.status_code == 200, response.content
    assert response.json()["result"] == {
        "preview": True,
        "new_row_ids": [str(fresh.pk)],
        "request_count": 1,
        "held_count": 2,
        "limit": 10000,
    }
    assert _http_saved_bytes(experiment) == before


@pytest.mark.django_db
def test_http_append_preview_reports_limit_and_real_append_refuses(finished):
    from types import SimpleNamespace

    actor, experiment, source, old = finished
    fresh = Row.no_workspace_objects.create(dataset=experiment.dataset, order=10)
    # Isolate the ceiling calculation without allocating ten thousand DB rows.
    held = [SimpleNamespace(source_row_id=uuid4()) for _ in range(10000)]
    body = {"append_only": True, "row_ids": [str(fresh.pk)]}
    before = _http_saved_bytes(experiment)
    with (
        patch.object(http, "_http_rows", return_value=held),
        patch.object(http, "execute_http_row") as send,
    ):
        response = put(actor, experiment, {**body, "preview": True})
        assert response.status_code == 200, response.content
        assert response.json()["result"] == {
            "preview": True,
            "new_row_ids": [str(fresh.pk)],
            "request_count": 1,
            "held_count": 10000,
            "limit": 10000,
        }
        refused = put(actor, experiment, body, str(uuid4()))
        assert refused.status_code == 400, refused.content
        assert b"at most 10000 rows" in refused.content
    send.assert_not_called()
    assert _http_saved_bytes(experiment) == before


@pytest.mark.django_db
def test_http_append_preview_keeps_tenant_and_row_checks(finished, tenants):
    actor, experiment, source, old = finished
    before = _http_saved_bytes(experiment)
    body = {
        "append_only": True,
        "preview": True,
        "row_ids": [str(actor["exp"]["row"].pk)],
    }
    allowed = put(actor, experiment, body)
    assert allowed.status_code == 200, allowed.content
    assert allowed.json()["result"]["new_row_ids"] == []
    assert put(tenants["b"], experiment, body).status_code in {400, 403, 404}
    body["row_ids"] = [str(tenants["b"]["exp"]["row"].pk)]
    assert put(actor, experiment, body).status_code == 400
    assert _http_saved_bytes(experiment) == before


@pytest.mark.django_db
def test_http_append_preview_request_and_response_are_typed(finished):
    from model_hub.serializers.experiments import (
        ExperimentUpdateV2ResponseSerializer,
        ExperimentUpdateV2Serializer,
        HttpAppendRowsPreviewSerializer,
    )

    actor, experiment, source, old = finished
    request = ExperimentUpdateV2Serializer(
        data={
            "append_only": True,
            "preview": True,
            "row_ids": [str(actor["exp"]["row"].pk)],
        }
    )
    assert request.is_valid(), request.errors
    assert request.validated_data["preview"] is True
    result = {
        "preview": True,
        "new_row_ids": [],
        "request_count": 0,
        "held_count": 1,
        "limit": 10000,
    }
    payload = HttpAppendRowsPreviewSerializer(data=result)
    assert payload.is_valid(), payload.errors
    envelope = ExperimentUpdateV2ResponseSerializer({"status": True, "result": result})
    assert envelope.data == {"status": True, "result": result}
    documented = ExperimentUpdateV2ResponseSerializer().fields["result"].fields
    assert set(result) <= set(documented)
    for data in (
        {"preview": True},
        {"append_only": True, "preview": True},
        {"preview": True, "row_ids": [str(actor["exp"]["row"].pk)]},
    ):
        invalid = ExperimentUpdateV2Serializer(data=data)
        assert not invalid.is_valid()


@pytest.mark.django_db
@pytest.mark.parametrize("legacy_workspace", [False, True])
def test_http_locks_only_source_then_experiment_with_default_workspace(
    tenants, legacy_workspace
):
    from types import SimpleNamespace

    from django.db import connection, transaction
    from django.test.utils import CaptureQueriesContext

    from model_hub.models.develop_dataset import Dataset
    from model_hub.models.experiments import ExperimentsTable
    from model_hub.views.experiments import _lock_http_experiment
    from tfc.middleware.workspace_context import (
        clear_workspace_context,
        set_workspace_context,
    )

    actor = tenants["a"]
    experiment = actor["exp"]["experiment"]
    if legacy_workspace:
        Dataset.no_workspace_objects.filter(pk=experiment.dataset_id).update(
            workspace=None
        )
    request = SimpleNamespace(
        organization=actor["org"],
        workspace=actor["workspace"],
        user=actor["user"],
    )
    set_workspace_context(workspace=actor["workspace"], organization=actor["org"])
    try:
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            locked = _lock_http_experiment(request, experiment.pk, actor["org"])
        assert locked.pk == experiment.pk
        locks = [query["sql"] for query in queries if "FOR UPDATE" in query["sql"]]
        assert len(locks) == 2
        for sql, model in zip(locks, (Dataset, ExperimentsTable), strict=False):
            assert " JOIN " not in sql.split(" WHERE ", 1)[0]
            assert sql.endswith(f'FOR UPDATE OF "{model._meta.db_table}"')
        foreign = tenants["b"]
        foreign_request = SimpleNamespace(
            organization=foreign["org"],
            workspace=foreign["workspace"],
            user=foreign["user"],
        )
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            with pytest.raises(ExperimentsTable.DoesNotExist):
                _lock_http_experiment(foreign_request, experiment.pk, foreign["org"])
        assert not any("FOR UPDATE" in query["sql"] for query in queries)
    finally:
        clear_workspace_context()


def test_http_preview_schema_is_retained_from_the_response_serializer():
    from drf_yasg import openapi

    from model_hub.serializers.experiments import ExperimentUpdateV2ResponseSerializer
    from tfc.utils.api_contracts import ManagementAPIAutoSchema

    components = openapi.ReferenceResolver(openapi.SCHEMA_DEFINITIONS, force_init=True)
    inspector = ManagementAPIAutoSchema(
        view=None,
        path="/model-hub/experiments/v2/{experiment_id}/",
        method="PUT",
        components=components,
        request=None,
        overrides={},
    )
    inspector.serializer_to_schema(ExperimentUpdateV2ResponseSerializer())
    definitions = components.with_scope(openapi.SCHEMA_DEFINITIONS)
    preview = definitions.get("HttpAppendRowsPreview")
    assert preview["x-client-retained"] is True
    assert preview["required"] == [
        "preview",
        "new_row_ids",
        "request_count",
        "held_count",
        "limit",
    ]
    result = definitions.get("ExperimentUpdateV2Result")
    for name, field in preview["properties"].items():
        assert result["properties"][name] == field
    assert "required" not in result


def _saved_evaluation(actor, experiment, source):
    """One saved evaluation on the HTTP source, with its columns created."""
    from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
    from model_hub.views.experiment_runner import ExperimentRunner

    template = EvalTemplate.no_workspace_objects.create(
        name="Edited evaluation",
        organization=actor["org"],
        workspace=actor["workspace"],
        criteria="Check {{output}}",
        model="fixture-model",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="Saved score",
        dataset=experiment.snapshot_dataset,
        organization=actor["org"],
        workspace=actor["workspace"],
        template=template,
        source_id=str(experiment.pk),
        status=StatusType.COMPLETED.value,
        model="fixture-model",
        config={"mapping": {"output": "output"}, "reason_column": True},
    )
    experiment.user_eval_template_ids.add(metric)
    runner = ExperimentRunner(experiment.pk)
    runner.load_experiment()
    runner.precreate_eval_columns_for_edts([str(source.pk)])
    return template, metric


def _wizard_edit_body(source, template, metric):
    """What Edit experiment sends: the HTTP source as saved and its evaluations."""
    entry = http.public_http_source(source)
    entry.pop("order")
    evaluation = {
        "id": str(metric.pk),
        "template_id": str(template.pk),
        "name": metric.name,
        "config": deepcopy(metric.config),
        "model": metric.model,
    }
    return {"prompt_config": [entry], "user_eval_metrics": [evaluation]}


@pytest.mark.django_db
@pytest.mark.parametrize("edit", ["config", "model", "name"])
def test_http_edit_of_one_evaluation_saves_it_and_runs_nothing(finished, edit):
    actor, experiment, source, plan = finished
    template, metric = _saved_evaluation(actor, experiment, source)
    cells = Cell.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset
    ).order_by("pk")
    before = json.dumps(list(cells.values()), sort_keys=True, default=str)
    body = _wizard_edit_body(source, template, metric)
    evaluation = body["user_eval_metrics"][0]
    if edit == "config":
        evaluation["config"]["config"] = {"case_sensitive": False}
    elif edit == "model":
        evaluation["model"] = "edited-evaluator-model"
    else:
        evaluation["name"] = "Renamed score"
    with (
        patch.object(http, "execute_http_row") as send,
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start,
    ):
        response = actor["client"].put(
            f"/model-hub/experiments/v2/{experiment.pk}/", body, format="json"
        )
    assert response.status_code == 200, response.content
    reply = response.json()
    assert reply["status"] is True
    assert reply["result"]["id"] == str(experiment.pk)
    send.assert_not_called()
    start.assert_not_called()
    assert json.dumps(list(cells.values()), sort_keys=True, default=str) == before
    metric.refresh_from_db()
    if edit == "config":
        assert metric.config["config"] == {"case_sensitive": False}
    elif edit == "model":
        assert metric.model == "edited-evaluator-model"
    else:
        assert metric.name == "Renamed score"


@pytest.mark.django_db
def test_http_edit_says_the_field_a_saved_evaluation_refuses(finished):
    from django.core.exceptions import ValidationError as DjangoValidationError

    from model_hub.models.evals_metric import UserEvalMetric

    actor, experiment, source, plan = finished
    template, metric = _saved_evaluation(actor, experiment, source)
    body = _wizard_edit_body(source, template, metric)
    body["user_eval_metrics"][0]["name"] = "Renamed score"
    refusal = DjangoValidationError({"name": ["Enter a shorter name."]})
    with patch.object(UserEvalMetric, "full_clean", side_effect=refusal):
        response = put(actor, experiment, body)
    assert response.status_code == 400, response.content
    text = json.dumps(response.json())
    assert "name: Enter a shorter name." in text
    assert "contact Future AGI support" not in text
    metric.refresh_from_db()
    assert metric.name == "Saved score"


@pytest.mark.django_db
def test_http_edit_fault_says_the_update_failed_and_saves_nothing(finished):
    actor, experiment, source, plan = finished
    entry = http.public_http_source(source)
    entry.pop("order")
    entry["http"]["output_path"] = "$.changed"
    with patch(
        "model_hub.views.experiments._build_and_save_v2_column_order",
        side_effect=RuntimeError("fixture fault"),
    ):
        response = put(actor, experiment, {"prompt_config": [entry]})
    assert response.status_code == 400, response.content
    text = json.dumps(response.json())
    assert "Unable to update experiment. Please try again." in text
    assert "contact Future AGI support" not in text
    assert "fixture fault" not in text
    source.refresh_from_db()
    assert source.legacy_prompt_config["http"]["output_path"] != "$.changed"


def test_validation_errors_read_as_field_and_message():
    from django.core.exceptions import ValidationError as DjangoValidationError
    from rest_framework.exceptions import ValidationError

    from model_hub.views.experiments import _validation_text

    django_error = DjangoValidationError({"name": ["Enter a shorter name."]})
    assert _validation_text(django_error) == "name: Enter a shorter name."
    nested = ValidationError({"http": {"url": ["Enter a valid URL."]}})
    assert _validation_text(nested) == "http.url: Enter a valid URL."
    assert _validation_text(ValidationError(["Select a source."])) == (
        "Select a source."
    )
    general = DjangoValidationError({"__all__": ["Pick one evaluation."]})
    assert _validation_text(general) == "Pick one evaluation."
    plain = ValueError("Column not found in experiment snapshot.")
    assert _validation_text(plain) == "Column not found in experiment snapshot."


@pytest.fixture
def scoped_http_parent(tenants):
    from model_hub.tests.test_http_canonical_evidence import (
        _dispatch,
        _evaluate,
        _finish,
        _run,
        _stage,
    )

    def create(*, failure=None, actors=tenants):
        world = _stage(actors, evaluated=failure == "check")
        for index, link in enumerate(world.links):
            status = 503 if failure == "error" and index == 0 else 200
            expected = "FAILED" if status == 503 else "COMPLETED"
            assert _dispatch(world, link, status=status)["status"] == expected
            if failure == "check":
                _evaluate(world, link, index)
        _finish(world)
        world.parent = _run(world)
        return world

    return create


def _scoped_http_append(world, scope, links=()):
    entry = http.public_http_source(world.source)
    entry.pop("id")
    entry.pop("order")
    entry["http"]["output_path"] = "$.changed"
    return {
        "append_only": True,
        "prompt_config": [entry],
        "http_selection": {
            "parent_run_id": str(world.parent.pk),
            "scope": scope,
            **(
                {"example_ids": [str(link.version_item.example_id) for link in links]}
                if scope == "rows"
                else {}
            ),
        },
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "scope,failure,index",
    [("rows", None, 1), ("failed", "check", 0), ("failed", "error", 0)],
)
def test_http_scoped_append_freezes_parent_rows_and_replays_once(
    scoped_http_parent, scope, failure, index
):
    from model_hub.tests.test_http_canonical_evidence import _evidence

    world = scoped_http_parent(failure=failure)
    chosen = world.links[index]
    body = _scoped_http_append(world, scope, [chosen])
    if failure == "error":
        # A later classic receipt must neither hide this parent's error nor
        # pull its successful row into the changed-config append.
        Cell.no_workspace_objects.filter(
            column=world.output,
            row_id=world.links[0].snapshot_row_id,
        ).update(status=CellStatus.PASS.value)
        Cell.no_workspace_objects.filter(
            column=world.output,
            row_id=world.links[1].snapshot_row_id,
        ).update(status=CellStatus.ERROR.value)
    before = _evidence(world)
    world.source.refresh_from_db()
    original = deepcopy(world.source.legacy_prompt_config)
    key = str(uuid4())
    response = put(world.actor, world.experiment, body, key)
    assert response.status_code == 200, response.content
    staged = deepcopy(state_of(world.experiment))
    targets = staged["http_plan"]["targets"]
    assert len(targets) == 1
    new_id = next(iter(targets))
    assert new_id != str(world.source.pk)
    assert targets[new_id]["row_ids"] == [str(chosen.snapshot_row_id)]
    variant = world.experiment.experiment_datasets.get(pk=new_id)
    changed = deepcopy(body)
    changed["http_selection"]["parent_run_id"] = str(uuid4())
    conflict = put(world.actor, world.experiment, changed, key)
    assert conflict.status_code == 409, conflict.content
    # Recovery uses the admitted plan, not another failed-row read.
    with patch.object(http, "select_http_retry", side_effect=AssertionError):
        replay = put(world.actor, world.experiment, body, key)
    assert replay.status_code == 200, replay.content
    assert state_of(world.experiment)["http_plan"] == staged["http_plan"]
    with patch.object(http, "execute_http_row", return_value=RESULT) as send:
        selected = run_selected(world.experiment, variant, staged["admission_id"])
        assert selected["row_ids"] == [str(chosen.snapshot_row_id)]
        replay = put(world.actor, world.experiment, body, key)
        assert replay.status_code == 200, replay.content
        http.run_http_snapshot_row(
            str(world.experiment.pk),
            new_id,
            str(chosen.snapshot_row_id),
            staged["admission_id"],
        )
    assert send.call_count == 1
    assert str(send.call_args.kwargs["row_id"]) == str(chosen.source_row_id)
    assert state_of(world.experiment)["admission_id"] == staged["admission_id"]
    assert world.experiment.experiment_datasets.filter(deleted=False).count() == 2
    world.source.refresh_from_db()
    assert world.source.legacy_prompt_config == original
    after = _evidence(world)
    for kind, records in before.items():
        ids = {record["id"] for record in records}
        assert [record for record in after[kind] if record["id"] in ids] == records


@pytest.mark.django_db
@pytest.mark.parametrize(
    "invalid",
    [
        "foreign_parent",
        "foreign_example",
        "empty",
        "no_failures",
        "mixed_rows",
        "missing_parent",
        "failed_with_examples",
    ],
)
def test_http_scoped_append_refuses_invalid_selection_without_writes(
    scoped_http_parent, tenants, invalid
):
    from model_hub.tests.test_http_canonical_evidence import _evidence

    world = scoped_http_parent()
    body = _scoped_http_append(world, "rows", [world.links[0]])
    selection = body["http_selection"]
    if invalid in {"foreign_parent", "foreign_example"}:
        foreign = scoped_http_parent(actors={"a": tenants["b"]})
        if invalid == "foreign_parent":
            selection["parent_run_id"] = str(foreign.parent.pk)
        else:
            selection["example_ids"] = [str(foreign.links[0].version_item.example_id)]
    elif invalid == "empty":
        selection["example_ids"] = []
    elif invalid == "no_failures":
        body["http_selection"] = {
            "parent_run_id": str(world.parent.pk),
            "scope": "failed",
        }
    elif invalid == "mixed_rows":
        body["row_ids"] = [str(world.links[0].source_row_id)]
    elif invalid == "missing_parent":
        selection.pop("parent_run_id")
    else:
        selection["scope"] = "failed"
    before = _evidence(world)
    with patch.object(http, "execute_http_row") as send:
        response = put(world.actor, world.experiment, body, str(uuid4()))
    assert response.status_code == 400, response.content
    assert send.call_count == 0
    assert _evidence(world) == before
    assert world.experiment.experiment_datasets.filter(deleted=False).count() == 1


@pytest.mark.django_db
def test_http_scoped_append_cannot_select_outside_parent_plan(scoped_http_parent):
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.tests.test_http_canonical_evidence import _evidence

    world = scoped_http_parent()
    body = _scoped_http_append(world, "rows", [world.links[0]])
    response = put(world.actor, world.experiment, body, str(uuid4()))
    assert response.status_code == 200, response.content
    staged = state_of(world.experiment)
    new_id = next(iter(staged["http_plan"]["targets"]))
    world.source = world.experiment.experiment_datasets.get(pk=new_id)
    with patch.object(http, "execute_http_row", return_value=RESULT):
        run_selected(world.experiment, world.source, staged["admission_id"])
    world.parent = NativeRunBinding.objects.get(variant=world.source).run
    outside = _scoped_http_append(world, "rows", [world.links[1]])
    before = _evidence(world)
    response = put(world.actor, world.experiment, outside, str(uuid4()))
    assert response.status_code == 400, response.content
    assert _evidence(world) == before
    assert world.experiment.experiment_datasets.filter(deleted=False).count() == 2
