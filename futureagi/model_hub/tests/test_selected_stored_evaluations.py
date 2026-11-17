"""The two scoring doors retain their selected stored output and row scope."""

import asyncio
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from unittest.mock import AsyncMock, Mock, patch
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.db import transaction

from model_hub.models.choices import CellStatus, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentRun,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.models.run_prompt import PromptVersion
from model_hub.services.experiment_run_writes import _RUN_KEY
from model_hub.services.experiment_submit import (
    SubmissionCancelled,
    admit_submission,
    cancel_pending_submission,
    native_admission_ready,
    stage_submission,
    state_of,
)
from model_hub.tests import test_http_stored_evaluations as http
from model_hub.tests import test_native_prompt_create_bridge as native
from tfc.temporal.experiments import workflows
from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]
tenants = native.tenants
native_fixture_evaluator = native.native_fixture_evaluator
own_key_route = native.own_key_route


def _world(actor, *, subset=False, producer_change=None, legacy=False):
    world, links = native.check_world(actor)
    experiment = world["experiment"]
    experiment.refresh_from_db()
    rows = [str(link.snapshot_row_id) for link in links]
    with transaction.atomic():
        first = stage_submission(
            experiment,
            {},
            "create",
            authorized_workspace=actor["workspace"],
            subset_rows={str(world["config"].id): rows[:1]} if subset else None,
        )
    with patch(native.START, return_value="selected-output"):
        admit_submission(str(experiment.id), first["admission_id"])

    @contextmanager
    def observed_mock_request():
        # complete_current mocks the provider, so supply its observation explicitly.
        yield (
            {}
            if legacy
            else {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "original"},
                        ],
                    }
                ]
            }
        )

    with patch(
        "model_hub.utils.model_endpoints.capture_llm_messages", observed_mock_request
    ):
        native.complete_current(experiment, "selected output")
    parent = NativeRunBinding.objects.get(snapshot__experiment=experiment)
    config = world["config"]
    if producer_change == "prompt_version":
        config.prompt_version = PromptVersion.no_workspace_objects.create(
            original_template=config.prompt_template,
            template_version="newer-selected-output",
            prompt_config_snapshot={
                **deepcopy(config.prompt_version.prompt_config_snapshot),
                "messages": [{"role": "user", "content": "Newer instructions"}],
            },
            variable_names=deepcopy(config.prompt_version.variable_names),
        )
    elif producer_change == "model":
        config.model = "gpt-4o"
    elif producer_change == "model_params":
        config.model_params = {**config.model_params, "temperature": 0.8}
    if producer_change:
        config.save(update_fields=[producer_change])
    with transaction.atomic():
        second = stage_submission(
            experiment, {}, "rerun", authorized_workspace=actor["workspace"]
        )
    with patch(native.START, return_value="newer-output"):
        admit_submission(str(experiment.id), second["admission_id"])
    native.complete_current(experiment, "newer output")
    newer = NativeRunBinding.objects.exclude(pk=parent.pk).get(
        snapshot__experiment=experiment
    )
    metric, _ = native.attach_executable_native_eval(world)
    return world, links, parent, newer, metric


def _sibling(actor, world):
    experiment = world["experiment"]
    variant = ExperimentDatasetTable.no_workspace_objects.create(
        name="Unselected", experiment=experiment
    )
    config = ExperimentPromptConfig.no_workspace_objects.create(
        experiment_dataset=variant,
        prompt_version=world["config"].prompt_version,
        prompt_template=world["config"].prompt_template,
        name="Unselected",
        model=world["config"].model,
    )
    output = Column.no_workspace_objects.create(
        dataset=world["snapshot"],
        name="Unselected",
        data_type="text",
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id),
    )
    variant.columns.add(output)
    with transaction.atomic():
        state = stage_submission(
            experiment,
            {},
            "update",
            authorized_workspace=actor["workspace"],
            rerun_prompt_config_ids=[str(config.id)],
        )
    with patch(native.START, return_value="sibling-output"):
        admit_submission(str(experiment.id), state["admission_id"])
    native.complete_current(experiment, "unselected source output")
    return NativeRunBinding.objects.get(variant=variant), output


def _body(door, world, links, parent, metric):
    # Exact shapes emitted by Results Score with and leaderboard Re-score.
    selection = (
        {
            "cells": [
                {
                    "column_id": str(world["output_column"].id),
                    "run_id": str(parent.run_id),
                    "example_id": str(links[0].version_item.example_id),
                }
            ]
        }
        if door == "results"
        else {"source_ids": [str(parent.run_id)]}
    )
    return {"user_eval_metric_ids": [str(metric.id)], **selection}


def _post(actor, world, body, key="stored-selection"):
    return native.subset_post(actor, world["experiment"], body, key=key)


def _stored_state(experiment):
    queries = {
        "experiment": ExperimentsTable.no_workspace_objects.filter(pk=experiment.pk),
        "snapshot": Dataset.no_workspace_objects.filter(
            pk=experiment.snapshot_dataset_id
        ),
        "bindings": NativeRunBinding.objects.filter(snapshot__experiment=experiment),
        "runs": ExperimentRun.no_workspace_objects.filter(experiment=experiment),
        "items": native.ExperimentRunItem.no_workspace_objects.filter(
            run__experiment=experiment
        ),
        "cells": Cell.no_workspace_objects.filter(
            dataset_id=experiment.snapshot_dataset_id
        ),
        "columns": Column.no_workspace_objects.filter(
            dataset_id=experiment.snapshot_dataset_id
        ),
    }
    return {
        name: list(query.order_by("id").values()) for name, query in queries.items()
    }


@contextmanager
def _no_scoring():
    with (
        patch(
            "model_hub.services.experiment_submit.admit_submission",
            side_effect=AssertionError("Refused selections must not be admitted"),
        ) as admission,
        patch(
            "model_hub.views.eval_runner.EvaluationRunner._run_evaluation",
            side_effect=AssertionError("Refused selections must not invoke evaluators"),
        ) as evaluator,
    ):
        yield
    assert admission.call_count == 0
    assert evaluator.call_count == 0


@pytest.mark.parametrize("door", ["results", "leaderboard"])
@pytest.mark.parametrize("change", ["prompt_version", "model", "model_params"])
def test_older_producer_configuration_scores_only_selected_evidence(
    tenants, door, change
):
    _score_selected(tenants, door, producer_change=change)


@pytest.mark.parametrize("door", ["results", "leaderboard"])
@pytest.mark.parametrize("change", ["prompt_version", "model", "model_params"])
@pytest.mark.parametrize("reference", ["mapping", "list", "dynamic"])
def test_older_prompt_context_is_refused_before_admission(
    tenants, door, change, reference
):
    actor = tenants["a"]
    world, links, parent, newer, metric = _world(
        actor, producer_change=change, legacy=True
    )
    assert (
        parent.run.metadata["native_identity"]["native_config_hash"]
        != (newer.run.metadata["native_identity"]["native_config_hash"])
    )
    if reference == "mapping":
        metric.config["mapping"]["input"] = "PROMPT_CHAIN"
    elif reference == "list":
        metric.config["mapping"]["input"] = ["prompt_chain"]
    else:
        metric.config["config"] = {"rule_prompt": "Judge {{prompt_chain}}"}
    metric.save(update_fields=["config"])
    body = _body(door, world, links, parent, metric)
    before = _stored_state(world["experiment"])
    with _no_scoring(), http._scoring_only() as starts:
        response, start = _post(actor, world, body)
    assert response.status_code == 400
    assert response.data["result"] == (
        "this evaluator needs the run's original prompt settings, "
        "which weren't recorded; re-run the experiment to score it "
        "with its current configuration"
    )
    assert start.call_count == 0
    assert [call.call_count for call in starts] == [0, 0]
    assert _stored_state(world["experiment"]) == before


@pytest.mark.parametrize("door", ["results", "leaderboard"])
def test_scoring_door_uses_selected_output_and_exact_rows(tenants, door):
    _score_selected(tenants, door)


def _score_selected(tenants, door, *, producer_change=None):
    actor = tenants["a"]
    world, links, parent, newer, metric = _world(
        actor, subset=door == "leaderboard", producer_change=producer_change
    )
    parent_hash = parent.run.metadata["native_identity"]["native_config_hash"]
    if producer_change:
        assert (
            parent_hash != (newer.run.metadata["native_identity"]["native_config_hash"])
        )
    sibling, sibling_output = _sibling(actor, world)
    body = _body(door, world, links, parent, metric)
    rows = [str(link.snapshot_row_id) for link in links]
    parents = [parent.run_id, newer.run_id, sibling.run_id]
    before = list(
        native.ExperimentRunItem.no_workspace_objects.filter(run_id__in=parents)
        .order_by("id")
        .values()
    )
    unselected = list(
        Cell.no_workspace_objects.filter(dataset=world["snapshot"], row_id=rows[1])
        .order_by("id")
        .values()
    )
    outputs = list(
        Cell.no_workspace_objects.filter(
            column__in=[world["output_column"], sibling_output]
        )
        .order_by("id")
        .values()
    )
    with ExitStack() as stack:
        starts = stack.enter_context(http._scoring_only())
        blocked = [
            stack.enter_context(
                patch(
                    path, side_effect=AssertionError("Producer execution is forbidden")
                )
            )
            for path in (
                native.PROVIDER,
                "model_hub.services.http_experiment_source.run_http_snapshot_row",
                "model_hub.services.http_experiment_source.execute_and_persist_http_row",
                "model_hub.services.http_experiment_source.prepare_http_experiment",
                "model_hub.services.http_experiment_source.bind_http_submission",
            )
        ]
        response, start = _post(actor, world, body)
        assert response.status_code == 200, response.content
        assert start.call_count == 1
        state = state_of(world["experiment"])
        assert list(state["execution_plan"]["targets"]) == [str(parent.run_id)]
        target = state["execution_plan"]["targets"][str(parent.run_id)]
        assert target["execution_mode"] == "eval_only"
        assert target["evaluator_rows"] == {rows[0]: [str(metric.id)]}
        source = target["evaluation_sources"][rows[0]]
        selected_item = parent.run.items.get(
            example_id=links[0].version_item.example_id
        )
        assert source["run_id"] == str(parent.run_id)
        assert source["native_config_hash"] == parent_hash
        assert source["item_id"] == str(selected_item.id)
        assert (
            source["receipt_hash"]
            == selected_item.metadata[_RUN_KEY]["result_receipt"]["payload_hash"]
        )
        assert state["workflow_args"]["prompt_config_ids"] == []
        assert state["workflow_args"]["agent_config_ids"] == []
        context = state["workflow_args"]["native_runs"][str(parent.run_id)]
        prepared = native.activities._setup_prompt_v2_sync(
            str(world["experiment"].id),
            str(world["snapshot"].id),
            str(world["config"].id),
            native_context=context,
        )
        assert prepared["row_ids"] == [rows[0]]
        with native.native_eval_transport(score=0.25) as (engine, _):
            native.row(world["experiment"], context, prepared, rows[0])
            native.finish(context, prepared)
            native.row(world["experiment"], context, prepared, rows[0])
        assert engine.run.call_count == 1
        assert engine.run.call_args.kwargs["output"] == "selected output"
        assert engine.run.call_args.kwargs["input"] == "input-0"
        for mock in blocked:
            assert mock.call_count == 0
        assert [call.call_count for call in starts] == [0, 0]
    child = NativeRunBinding.objects.get(run_id=context["run_id"])
    assert list(child.run.items.values_list("example_id", "output")) == [
        (links[0].version_item.example_id, "selected output")
    ]
    assert child.run.items.get().score_refs == [
        {
            "metric_id": str(metric.id),
            "evaluator_version_id": str(metric.pinned_version_id),
            "status": "assessed",
            "value": 0.25,
        }
    ]
    assert before == list(
        native.ExperimentRunItem.no_workspace_objects.filter(run_id__in=parents)
        .order_by("id")
        .values()
    )
    assert unselected == list(
        Cell.no_workspace_objects.filter(dataset=world["snapshot"], row_id=rows[1])
        .order_by("id")
        .values()
    )
    assert outputs == list(
        Cell.no_workspace_objects.filter(
            column__in=[world["output_column"], sibling_output]
        )
        .order_by("id")
        .values()
    )
    replay, start = _post(actor, world, deepcopy(body))
    assert replay.status_code == 200
    assert start.call_count == 0
    assert state_of(world["experiment"])["admission_id"] == state["admission_id"]
    assert (
        NativeRunBinding.objects.filter(
            snapshot__experiment=world["experiment"]
        ).count()
        == 4
    )
    changed = _body(door, world, links, newer, metric)
    conflict, start = _post(actor, world, changed)
    assert conflict.status_code == 409
    assert start.call_count == 0
    assert state_of(world["experiment"])["admission_id"] == state["admission_id"]


def _base_evaluation(world, links, metric):
    # Both the classic base runner and native runner can resolve this snapshot ID.
    metric.config = deepcopy(metric.config)
    metric.config["mapping"]["input"] = links[0].snapshot.column_map[
        str(world["source_column"].id)
    ]
    metric.save(update_fields=["config"])
    base = Column.no_workspace_objects.create(
        dataset=world["snapshot"],
        name="Base output",
        data_type="text",
        source=SourceChoices.OTHERS.value,
        status="Completed",
    )
    scores = Column.no_workspace_objects.create(
        dataset=world["snapshot"],
        name=metric.name,
        data_type="float",
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
        status="Completed",
    )
    for index, link in enumerate(links):
        for column, value in ((base, f"base output {index}"), (scores, "0.75")):
            Cell.no_workspace_objects.create(
                dataset=world["snapshot"],
                column=column,
                row_id=link.snapshot_row_id,
                value=value,
                status="pass",
            )
    world["experiment"].column = base
    world["experiment"].save(update_fields=["column"])
    return base, scores


def _parent_dispatch(world, args):
    """Run the real parent and sync workers; replace only Temporal transport."""
    activity_calls, children = [], []
    args = {key: value for key, value in args.items() if key != "admission_id"}

    def execute(name, payload):
        activity_calls.append((name, payload))
        if name == "setup_experiment_activity":
            assert payload.admission_only is True
            assert (
                native_admission_ready(payload.experiment_id, payload.native_runs)
                is True
            )
            return {"status": "READY"}
        if name == "mark_experiment_running_activity":
            return native.activities._mark_experiment_running_sync(
                payload.experiment_id, payload.refuse_if_stopped
            )
        if name == "analyze_dependencies_activity":
            return native.activities._analyze_dependencies_sync(
                payload.experiment_id, payload.eval_template_ids
            )
        if name == "resolve_edt_columns_activity":
            return native.activities._resolve_edt_columns_sync(
                payload.experiment_id, payload.edt_ids
            )
        if name == "run_base_evaluation_activity":
            return native.activities._run_base_evaluation_sync(
                payload.experiment_id,
                payload.eval_template_id,
                row_ids=payload.row_ids,
            )
        if name == "check_experiment_status_activity":
            return native.activities._check_experiment_status_sync(payload.entity_id)
        raise AssertionError(f"Unexpected parent activity: {name}")

    def run_child(payload):
        context = payload.native_context
        prepared = native.activities._setup_prompt_v2_sync(
            payload.experiment_id,
            payload.dataset_id,
            payload.experiment_prompt_config_id,
            native_context=context,
        )
        for row_id in prepared["row_ids"]:
            assert (
                native.row(world["experiment"], context, prepared, row_id)["status"]
                == "COMPLETED"
            )
        native.finish(context, prepared)
        return {"completed_rows": len(prepared["row_ids"]), "failed_rows": 0}

    async def activity(name, payload, **kwargs):
        return await sync_to_async(execute, thread_sensitive=True)(name, payload)

    async def child(run, payload, **kwargs):
        assert run == workflows.ProcessPromptV2Workflow.run
        children.append(payload)
        return asyncio.create_task(
            sync_to_async(run_child, thread_sensitive=True)(payload)
        )

    with (
        patch.object(workflows.workflow, "execute_activity", side_effect=activity),
        patch.object(workflows.workflow, "start_child_workflow", side_effect=child),
        patch(
            "tfc.utils.distributed_state.evaluation_tracker.should_cancel",
            return_value=False,
        ),
    ):
        result = async_to_sync(workflows.RerunCellsV2Workflow().run)(
            RerunCellsV2WorkflowInput(**args)
        )
    return result, activity_calls, children


@pytest.mark.parametrize("door", ["results", "leaderboard"])
def test_selected_parent_dispatch_preserves_base_evaluation_cells(tenants, door):
    actor = tenants["a"]
    world, links, parent, newer, metric = _world(actor, subset=door == "leaderboard")
    base, scores = _base_evaluation(world, links, metric)
    before = list(
        Cell.no_workspace_objects.filter(column__in=[base, scores])
        .order_by("id")
        .values()
    )
    parents = list(
        native.ExperimentRunItem.no_workspace_objects.filter(
            run_id__in=[parent.run_id, newer.run_id]
        )
        .order_by("id")
        .values()
    )
    body = _body(door, world, links, parent, metric)
    with http._scoring_only(), native.native_eval_transport(score=0.25) as (engine, _):
        response, start = _post(actor, world, body)
        assert response.status_code == 200, response.content
        assert start.call_count == 1
        args = start.call_args.kwargs
        context = args["native_runs"][str(parent.run_id)]
        result, calls, children = _parent_dispatch(world, args)
    assert result.total_rows_processed == 1
    assert result.failed_rows == 0
    assert [name for name, _ in calls] == [
        "setup_experiment_activity",
        "mark_experiment_running_activity",
        "analyze_dependencies_activity",
        "check_experiment_status_activity",
    ]
    assert [child.native_context for child in children] == [context]
    assert [child.experiment_prompt_config_id for child in children] == [
        str(world["config"].id)
    ]
    assert [
        (call.kwargs["input"], call.kwargs["output"])
        for call in engine.run.call_args_list
    ] == [("input-0", "selected output")]
    assert (
        list(
            Cell.no_workspace_objects.filter(column__in=[base, scores])
            .order_by("id")
            .values()
        )
        == before
    )
    assert (
        list(
            native.ExperimentRunItem.no_workspace_objects.filter(
                run_id__in=[parent.run_id, newer.run_id]
            )
            .order_by("id")
            .values()
        )
        == parents
    )
    child = NativeRunBinding.objects.get(run_id=context["run_id"])
    assert list(child.run.items.values_list("example_id", "output")) == [
        (links[0].version_item.example_id, "selected output")
    ]
    assert child.run.items.get().score_refs == [
        {
            "metric_id": str(metric.id),
            "evaluator_version_id": str(metric.pinned_version_id),
            "status": "assessed",
            "value": 0.25,
        }
    ]


@pytest.mark.parametrize("mode", ["base_cell", "all_evaluations"])
def test_intentional_base_evaluation_still_dispatches(tenants, mode):
    actor = tenants["a"]
    world, links, _, _, metric = _world(actor)
    base, scores = _base_evaluation(world, links, metric)
    rows = [str(link.snapshot_row_id) for link in links]
    body = (
        {"cells": [{"column_id": str(scores.id), "row_id": rows[0]}]}
        if mode == "base_cell"
        else {"user_eval_metric_ids": [str(metric.id)]}
    )
    untouched = list(
        Cell.no_workspace_objects.filter(column=scores, row_id=rows[1]).values()
    )
    base_before = list(
        Cell.no_workspace_objects.filter(column=base).order_by("id").values()
    )
    with http._scoring_only(), native.native_eval_transport(score=0.25) as (engine, _):
        response, start = _post(actor, world, body)
        assert response.status_code == 200, response.content
        assert start.call_count == 1
        result, calls, children = _parent_dispatch(world, start.call_args.kwargs)
    expected_rows = rows[:1] if mode == "base_cell" else sorted(rows)
    base_calls = [p for name, p in calls if name == "run_base_evaluation_activity"]
    assert [
        (p.experiment_id, p.dataset_id, p.eval_template_id, p.row_ids)
        for p in base_calls
    ] == [
        (
            str(world["experiment"].id),
            str(world["snapshot"].id),
            str(metric.id),
            expected_rows,
        )
    ]
    assert result.total_rows_processed == (0 if mode == "base_cell" else 2)
    assert result.failed_rows == 0
    assert len(children) == (0 if mode == "base_cell" else 1)
    assert sorted(call.kwargs["output"] for call in engine.run.call_args_list) == (
        ["base output 0"]
        if mode == "base_cell"
        else ["base output 0", "base output 1", "newer output", "newer output"]
    )
    assert list(
        Cell.no_workspace_objects.filter(column=scores, row_id__in=expected_rows)
        .order_by("row_id")
        .values_list("value", "status")
    ) == [("0.25", "pass") for _ in expected_rows]
    if mode == "base_cell":
        assert (
            list(
                Cell.no_workspace_objects.filter(column=scores, row_id=rows[1]).values()
            )
            == untouched
        )
    assert (
        list(Cell.no_workspace_objects.filter(column=base).order_by("id").values())
        == base_before
    )


def test_unconfirmed_selection_reuses_receipts_and_rejects_changed_rows(tenants):
    actor = tenants["a"]
    world, links, parent, _, metric = _world(actor)
    body = _body("results", world, links, parent, metric)
    response, start = native.subset_post(
        actor,
        world["experiment"],
        body,
        key="recover-selection",
        side_effect=RuntimeError("Acknowledgement unavailable"),
    )
    assert response.status_code == 503
    assert start.call_count == 1
    pending = state_of(world["experiment"])
    assert pending["state"] == "pending"
    response, start = _post(actor, world, body, key="recover-selection")
    assert response.status_code == 200
    assert start.call_count == 1
    accepted = state_of(world["experiment"])
    assert accepted["admission_id"] == pending["admission_id"]
    assert accepted["execution_plan"] == pending["execution_plan"]
    assert accepted["request_hash"] == pending["request_hash"]
    assert (
        NativeRunBinding.objects.filter(
            snapshot__experiment=world["experiment"]
        ).count()
        == 3
    )
    changed = deepcopy(body)
    changed["cells"][0]["example_id"] = str(links[1].version_item.example_id)
    response, start = _post(actor, world, changed, key="recover-selection")
    assert response.status_code == 409
    assert start.call_count == 0
    assert state_of(world["experiment"])["execution_plan"] == pending["execution_plan"]


@pytest.mark.parametrize("door", ["results", "leaderboard"])
def test_selected_run_must_belong_to_the_authorized_experiment(tenants, door):
    world, links, parent, _, metric = _world(tenants["a"])
    foreign, _, foreign_parent, _, _ = _world(tenants["b"])
    body = _body(door, world, links, foreign_parent, metric)
    before = state_of(world["experiment"])
    response, start = _post(tenants["a"], world, body)
    assert response.status_code == 400
    assert start.call_count == 0
    assert state_of(world["experiment"]) == before
    assert (
        NativeRunBinding.objects.filter(
            snapshot__experiment=world["experiment"]
        ).count()
        == 2
    )
    assert (
        NativeRunBinding.objects.filter(
            snapshot__experiment=foreign["experiment"]
        ).count()
        == 2
    )


def test_selected_row_outside_parent_plan_is_refused_before_admission(tenants):
    world, links, parent, _, metric = _world(tenants["a"], subset=True)
    body = _body("results", world, links, parent, metric)
    body["cells"][0]["example_id"] = str(links[1].version_item.example_id)
    before = state_of(world["experiment"])
    response, start = _post(tenants["a"], world, body)
    assert response.status_code == 400
    assert start.call_count == 0
    assert state_of(world["experiment"]) == before


def test_missing_selected_receipt_never_falls_back_to_newer_output(tenants):
    world, links, parent, _, metric = _world(tenants["a"])
    body = _body("results", world, links, parent, metric)
    parent.run.items.filter(example_id=links[0].version_item.example_id).update(
        deleted=True
    )
    before = state_of(world["experiment"])
    response, start = _post(tenants["a"], world, body)
    assert response.status_code == 400
    assert start.call_count == 0
    assert state_of(world["experiment"]) == before
    assert (
        NativeRunBinding.objects.filter(
            snapshot__experiment=world["experiment"]
        ).count()
        == 2
    )


@pytest.mark.parametrize("door", ["results", "leaderboard"])
def test_selected_http_output_is_refused_before_admission(tenants, door):
    world = http._terminal(tenants)
    parent = http._run(world)
    binding = parent.native_binding
    body = {"user_eval_metric_ids": [str(world.metric.id)]}
    if door == "results":
        body["cells"] = [
            {
                "column_id": str(binding.output_column_id),
                "run_id": str(parent.id),
                "example_id": str(world.links[0].version_item.example_id),
            }
        ]
    else:
        body["source_ids"] = [str(parent.id)]
    before = state_of(world.experiment)
    stored_before = _stored_state(world.experiment)
    with http._scoring_only() as starts, _no_scoring():
        response = world.actor["client"].post(
            f"/model-hub/experiments/v2/{world.experiment.id}/rerun-cells/",
            body,
            format="json",
            HTTP_IDEMPOTENCY_KEY="http-selection",
        )
    assert response.status_code == 400
    assert response.data["result"] == (
        "Selected HTTP stored-output scoring is not supported by this endpoint. "
        "Use Run evaluations for stored HTTP outputs instead."
    )
    assert [start.call_count for start in starts] == [0, 0]
    assert state_of(world.experiment) == before
    assert _stored_state(world.experiment) == stored_before


def _dispatch_selected(world, state):
    """Real parent dispatch and worker writes; replace only Temporal transport."""
    import asyncio

    from asgiref.sync import async_to_sync, sync_to_async

    from tfc.temporal.experiments import workflows

    experiment = world["experiment"]
    children = []

    def child_work(input):
        context = input.native_context
        assert input.experiment_prompt_config_id == str(world["config"].id)
        prepared = native.activities._setup_prompt_v2_sync(
            input.experiment_id,
            input.dataset_id,
            input.experiment_prompt_config_id,
            native_context=context,
        )
        assert prepared["status"] == "READY"
        for row in prepared["row_ids"]:
            assert native.row(experiment, context, prepared, row) == {
                "row_id": row,
                "column_id": str(world["output_column"].id),
                "status": "COMPLETED",
            }
        native.finish(context, prepared)
        return {"completed_rows": len(prepared["row_ids"]), "failed_rows": 0}

    async def start_child(function, input, **kwargs):
        assert function == workflows.ProcessPromptV2Workflow.run
        children.append((kwargs["id"], deepcopy(input.native_context)))
        return asyncio.create_task(sync_to_async(child_work)(input))

    async def activity(name, input, **kwargs):
        if name == "setup_experiment_activity":
            ready = await sync_to_async(native_admission_ready)(
                input.experiment_id, input.native_runs
            )
            assert ready is True
            return {"status": "READY"}
        if name == "mark_experiment_running_activity":
            return await sync_to_async(native.activities._mark_experiment_running_sync)(
                input.experiment_id, refuse_if_stopped=input.refuse_if_stopped
            )
        if name == "analyze_dependencies_activity":
            return await sync_to_async(native.activities._analyze_dependencies_sync)(
                input.experiment_id, input.eval_template_ids
            )
        if name == "resolve_edt_columns_activity":
            return await sync_to_async(native.activities._resolve_edt_columns_sync)(
                input.experiment_id, input.edt_ids
            )
        if name in {
            "run_base_evaluation_activity",
            "process_batch_template_eval_activity",
        }:
            raise AssertionError("Only the selected native children may evaluate")
        if name == "check_experiment_status_activity":
            return await sync_to_async(native.activities._check_experiment_status_sync)(
                input.entity_id
            )
        raise AssertionError(f"Unexpected activity: {name}")

    with (
        patch.object(workflows.workflow, "execute_activity", activity),
        patch.object(workflows.workflow, "start_child_workflow", start_child),
    ):
        result = async_to_sync(workflows.RerunCellsV2Workflow().run)(
            workflows.RerunCellsV2WorkflowInput(
                experiment_id=str(experiment.id), **state["workflow_args"]
            )
        )
    return result, children


@pytest.mark.parametrize("door", ["results", "leaderboard"])
def test_overlapping_selected_runs_dispatch_independent_receipts_and_replay(
    tenants, door
):
    actor = tenants["a"]
    world, links, parent, newer, metric = _world(
        actor, subset=True, producer_change="model_params"
    )
    experiment = world["experiment"]
    # Neither selection is the newest run; a third execution must never be used.
    with transaction.atomic():
        latest = stage_submission(
            experiment, {}, "rerun", authorized_workspace=actor["workspace"]
        )
    with patch(native.START, return_value="unselected-latest"):
        admit_submission(str(experiment.id), latest["admission_id"])
    native.complete_current(experiment, "unselected latest output")
    original_ids = list(
        NativeRunBinding.objects.filter(snapshot__experiment=experiment).values_list(
            "run_id", flat=True
        )
    )
    before = list(
        native.ExperimentRunItem.no_workspace_objects.filter(run_id__in=original_ids)
        .order_by("id")
        .values()
    )
    output_cells = list(
        Cell.no_workspace_objects.filter(column=world["output_column"])
        .order_by("id")
        .values()
    )
    body = _body(door, world, links, parent, metric)
    other = _body(door, world, links, newer, metric)
    selector = "cells" if door == "results" else "source_ids"
    body[selector].extend(other[selector])
    experiment.column = world["source_column"]
    experiment.save(update_fields=["column"])
    base_column = Column.no_workspace_objects.create(
        dataset=world["snapshot"],
        name="Existing base score",
        data_type="float",
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=str(metric.id),
    )
    for link in links:
        Cell.no_workspace_objects.create(
            dataset=world["snapshot"],
            column=base_column,
            row_id=link.snapshot_row_id,
            value="0.75",
            status=CellStatus.PASS.value,
        )
    base_before = list(base_column.cell_set.order_by("id").values())
    with http._scoring_only() as starts, patch(native.PROVIDER) as producer:
        response, start = _post(actor, world, body)
        assert response.status_code == 200, response.content
        assert start.call_count == 1
        state = state_of(experiment)
        contexts = state["workflow_args"]["native_runs"]
        assert sorted(contexts) == sorted([str(parent.run_id), str(newer.run_id)])
        assert state["workflow_args"]["prompt_config_ids"] == []
        assert state["workflow_args"]["agent_config_ids"] == []
        expected_rows = {
            str(parent.run_id): [str(links[0].snapshot_row_id)],
            str(newer.run_id): sorted(
                str(link.snapshot_row_id)
                for link in (links if door == "leaderboard" else links[:1])
            ),
        }
        for selected, context in contexts.items():
            execution = str(
                uuid5(UUID(state["admission_id"]), f"native-evaluation:{selected}")
            )
            assert context["execution_id"] == execution
            assert context["plan_id"] == state["admission_id"]
            assert context["target_key"] == selected
            assert context["config_id"] == str(world["config"].id)
            binding = NativeRunBinding.objects.get(run_id=context["run_id"])
            assert str(binding.execution_id) == execution
            assert context["run_id"] == str(
                uuid5(
                    NAMESPACE_URL,
                    f"futureagi:native-run:{parent.variant_id}:{execution}",
                )
            )
            assert binding.variant_id == parent.variant_id
            target = state["execution_plan"]["targets"][selected]
            assert target["snapshot_row_ids"] == expected_rows[selected]
            assert target["evaluator_rows"] == {
                row: [str(metric.id)] for row in expected_rows[selected]
            }
            assert {ref["run_id"] for ref in target["evaluation_sources"].values()} == {
                selected
            }
            for row, ref in target["evaluation_sources"].items():
                link = next(link for link in links if str(link.snapshot_row_id) == row)
                item = native.ExperimentRunItem.no_workspace_objects.get(
                    run_id=selected, example_id=link.version_item.example_id
                )
                assert ref["item_id"] == str(item.id)
                assert (
                    ref["receipt_hash"]
                    == item.metadata[_RUN_KEY]["result_receipt"]["payload_hash"]
                )
        assert native_admission_ready(str(experiment.id), contexts) is True
        for field in ("target_key", "plan_id", "execution_id", "config_id"):
            altered = deepcopy(contexts)
            first, second = sorted(altered)
            altered[first][field] = (
                altered[second][field]
                if field in {"target_key", "execution_id"}
                else "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            )
            with pytest.raises(native.RunWriteError):
                native_admission_ready(str(experiment.id), altered)
        with native.native_eval_transport(score=0.25) as (engine, _):
            result, children = _dispatch_selected(world, state)
            count = 3 if door == "leaderboard" else 2
            assert result.total_rows_processed == count
            assert result.failed_rows == 0
            assert sorted(
                call.kwargs["output"] for call in engine.run.call_args_list
            ) == ["newer output"] * (count - 1) + ["selected output"]
            assert engine.run.call_count == count
            expected_children = [
                (f"rerun-eval-v2-{experiment.id}-{state['admission_id']}-{key}", value)
                for key, value in sorted(contexts.items())
            ]
            assert children == expected_children
            # Redelivery of each completed worker and HTTP replay reuse both children.
            replay_result, replay_children = _dispatch_selected(world, state)
            assert replay_result.total_rows_processed == count
            assert replay_children == expected_children
            replay, start = _post(actor, world, deepcopy(body))
            assert replay.status_code == 200
            assert start.call_count == 0
            assert engine.run.call_count == count
        assert producer.call_count == 0
        assert [call.call_count for call in starts] == [0, 0]
    for selected, context in contexts.items():
        run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
        assert run.status == "completed"
        expected_output = (
            "selected output" if selected == str(parent.run_id) else ("newer output")
        )
        assert sorted(
            str(example) for example in run.items.values_list("example_id", flat=True)
        ) == sorted(
            str(link.version_item.example_id)
            for link in links
            if str(link.snapshot_row_id) in expected_rows[selected]
        )
        assert sorted(run.items.values_list("output", flat=True)) == [
            expected_output
        ] * len(expected_rows[selected])
        assert list(run.items.values_list("score_refs", flat=True)) == [
            [
                {
                    "metric_id": str(metric.id),
                    "evaluator_version_id": str(metric.pinned_version_id),
                    "status": "assessed",
                    "value": 0.25,
                }
            ]
        ] * len(expected_rows[selected])
    assert list(base_column.cell_set.order_by("id").values()) == base_before
    assert state_of(experiment)["execution_plan"] == state["execution_plan"]
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 5
    assert before == list(
        native.ExperimentRunItem.no_workspace_objects.filter(run_id__in=original_ids)
        .order_by("id")
        .values()
    )
    assert output_cells == list(
        Cell.no_workspace_objects.filter(column=world["output_column"])
        .order_by("id")
        .values()
    )


@pytest.mark.parametrize("outcome", ["recover", "stop"])
def test_pending_selected_children_share_admission_recovery_and_stop(tenants, outcome):
    actor = tenants["a"]
    world, _, parent, newer, metric = _world(actor, subset=True)
    experiment = world["experiment"]
    body = {
        "source_ids": [str(parent.run_id), str(newer.run_id)],
        "user_eval_metric_ids": [str(metric.id)],
    }
    before = list(
        native.ExperimentRunItem.no_workspace_objects.filter(
            run_id__in=[parent.run_id, newer.run_id]
        )
        .order_by("id")
        .values()
    )
    response, start = native.subset_post(
        actor,
        experiment,
        body,
        key="pending-selection",
        side_effect=RuntimeError("Acknowledgement unavailable"),
    )
    assert response.status_code == 503
    assert start.call_count == 1
    pending = state_of(experiment)
    contexts = pending["workflow_args"]["native_runs"]
    assert sorted(contexts) == sorted(body["source_ids"])
    assert native_admission_ready(str(experiment.id), contexts) is False
    if outcome == "recover":
        response, start = _post(actor, world, body, key="pending-selection")
        assert response.status_code == 200
        assert start.call_count == 1
        assert native_admission_ready(str(experiment.id), contexts) is True
        assert state_of(experiment)["execution_plan"] == pending["execution_plan"]
        expected_status = "queued"
    else:
        stopped = cancel_pending_submission(str(experiment.id))
        assert stopped["admission_id"] == pending["admission_id"]
        assert stopped["state"] == "cancelled"
        with pytest.raises(SubmissionCancelled):
            native_admission_ready(str(experiment.id), contexts)
        response, start = _post(actor, world, body, key="pending-selection")
        assert response.status_code == 409
        assert start.call_count == 0
        expected_status = "cancelled"
    assert (
        list(
            NativeRunBinding.objects.filter(
                run_id__in=[context["run_id"] for context in contexts.values()]
            )
            .order_by("run_id")
            .values_list("run__status", flat=True)
        )
        == [expected_status] * 2
    )
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == 4
    assert before == list(
        native.ExperimentRunItem.no_workspace_objects.filter(
            run_id__in=[parent.run_id, newer.run_id]
        )
        .order_by("id")
        .values()
    )


@pytest.mark.parametrize("corruption", ["producer_identity", "missing_receipt"])
def test_selected_receipt_integrity_is_required_after_configuration_changes(
    tenants, corruption
):
    world, links, parent, _, metric = _world(
        tenants["a"], producer_change="model_params"
    )
    if corruption == "producer_identity":
        parent.run.metadata["native_identity"]["native_config_hash"] = "altered"
        parent.run.save(update_fields=["metadata"])
    else:
        item = parent.run.items.get(example_id=links[0].version_item.example_id)
        item.metadata[_RUN_KEY].pop("result_receipt")
        item.save(update_fields=["metadata"])
    before = state_of(world["experiment"])
    with (
        native.native_eval_transport() as (engine, _),
        patch(native.PROVIDER) as producer,
    ):
        response, start = _post(
            tenants["a"], world, _body("results", world, links, parent, metric)
        )
    assert response.status_code == 400
    assert start.call_count == 0
    assert engine.run.call_count == 0
    assert producer.call_count == 0
    assert state_of(world["experiment"]) == before
    assert (
        NativeRunBinding.objects.filter(
            snapshot__experiment=world["experiment"]
        ).count()
        == 2
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("stopped", [False, True])
def test_selected_sibling_controls_target_their_own_workflows(
    tenants, reverse, stopped
):
    from asgiref.sync import async_to_sync
    from temporalio.client import WorkflowExecutionStatus

    from model_hub.models.experiment_run_control import ExperimentRunControl
    from model_hub.services import experiment_run_control as controls
    from tfc.temporal.experiments.run_control_activities import _bind

    actor = tenants["a"]
    world, _, parent, newer, metric = _world(actor, subset=True)
    experiment = world["experiment"]
    body = {
        "source_ids": [str(parent.run_id), str(newer.run_id)],
        "user_eval_metric_ids": [str(metric.id)],
    }
    response, start = _post(actor, world, body)
    assert response.status_code == 200
    assert start.call_count == 1
    state = state_of(experiment)
    contexts = state["workflow_args"]["native_runs"]
    expected = {}
    ordered = sorted(contexts, reverse=reverse)
    for selected in ordered:
        context = contexts[selected]
        workflow_id = (
            f"rerun-eval-v2-{experiment.id}-{state['admission_id']}-{selected}"
        )
        workflow_run_id = uuid4()
        _bind(
            {
                "experiment_id": str(experiment.id),
                "config_id": context["config_id"],
                "kind": "prompt",
                "workflow_id": workflow_id,
                "workflow_run_id": str(workflow_run_id),
            }
        )
        expected[context["run_id"]] = (workflow_id, str(workflow_run_id))
    # A later variant-level record must not steal either canonical child.
    _bind(
        {
            "experiment_id": str(experiment.id),
            "config_id": str(world["config"].id),
            "kind": "prompt",
            "workflow_id": "unrelated-workflow",
            "workflow_run_id": str(uuid4()),
        }
    )
    ids = sorted(expected)
    for chosen in ([ids[0]], [ids[1]], ids):
        entries = controls._named_runs(experiment, chosen)
        assert {
            key: (entry["binding"].workflow_id, str(entry["binding"].workflow_run_id))
            for key, entry in entries.items()
        } == {key: expected[key] for key in chosen}
        handles = {}
        for key in chosen:
            workflow_id, workflow_run_id = expected[key]
            handle = Mock()
            handle.describe = AsyncMock(
                return_value=Mock(status=WorkflowExecutionStatus.RUNNING)
            )
            handle.query = AsyncMock(
                return_value={
                    "experiment_id": str(experiment.id),
                    "config_id": str(world["config"].id),
                    "workflow_run_id": workflow_run_id,
                    "status": "RUNNING",
                }
            )
            handle.signal = AsyncMock()
            handles[workflow_id] = handle
        client = Mock()
        client.get_workflow_handle.side_effect = (
            lambda workflow_id, _handles=handles, **kwargs: _handles[workflow_id]
        )
        with patch.object(controls, "get_client", AsyncMock(return_value=client)):
            result = async_to_sync(controls._execute)(
                str(experiment.id), [entries[key] for key in chosen], stopped
            )
        assert [row["run_id"] for row in result["runs"]] == chosen
        assert [
            (call.args, call.kwargs)
            for call in client.get_workflow_handle.call_args_list
        ] == [((expected[key][0],), {"run_id": expected[key][1]}) for key in chosen]
        for handle in handles.values():
            assert handle.signal.call_count == 1
            assert handle.signal.call_args.args == ("set_run_stopped", stopped)
    assert (
        controls._named_runs(experiment, [str(parent.run_id)])[str(parent.run_id)][
            "binding"
        ]
        is None
    )
    ExperimentRunControl.objects.filter(workflow_id=expected[ids[0]][0]).delete()
    assert controls._named_runs(experiment, [ids[0]])[ids[0]]["binding"] is None
    child = ExperimentRun.no_workspace_objects.get(pk=ids[1])
    child.metadata["native_identity"]["selected_parent_run_id"] = str(uuid4())
    child.save(update_fields=["metadata"])
    assert controls._named_runs(experiment, [ids[1]])[ids[1]]["binding"] is None


@pytest.mark.parametrize("door", ["results", "leaderboard"])
def test_scored_parent_retains_original_producer_context_requirement(tenants, door):
    actor = tenants["a"]
    world, links, parent, _, metric = _world(
        actor, producer_change="prompt_version", legacy=True
    )
    response, start = _post(actor, world, _body(door, world, links, parent, metric))
    assert response.status_code == 200
    assert start.call_count == 1
    state = state_of(world["experiment"])
    with native.native_eval_transport(score=0.25) as (engine, _):
        _dispatch_selected(world, state)
    assert engine.run.call_count == (1 if door == "results" else 2)
    context = state["workflow_args"]["native_runs"][str(parent.run_id)]
    scored = NativeRunBinding.objects.get(run_id=context["run_id"])
    metric.config["mapping"]["input"] = "prompt_chain"
    metric.save(update_fields=["config"])
    before = _stored_state(world["experiment"])
    with _no_scoring():
        response, start = _post(
            actor, world, _body(door, world, links, scored, metric), key="score-again"
        )
    assert response.status_code == 400
    assert response.data["result"] == (
        "this evaluator needs the run's original prompt settings, "
        "which weren't recorded; re-run the experiment to score it "
        "with its current configuration"
    )
    assert start.call_count == 0
    assert _stored_state(world["experiment"]) == before


@pytest.mark.parametrize("door", ["results", "leaderboard"])
def test_current_producer_prompt_mapping_remains_admissible(tenants, door):
    actor = tenants["a"]
    world, links, parent, newer, metric = _world(actor)
    assert (
        parent.run.metadata["native_identity"]["native_config_hash"]
        == (newer.run.metadata["native_identity"]["native_config_hash"])
    )
    metric.config["mapping"]["input"] = "prompt_chain"
    metric.save(update_fields=["config"])
    with http._scoring_only() as starts:
        response, start = _post(actor, world, _body(door, world, links, parent, metric))
    assert response.status_code == 200
    assert start.call_count == 1
    assert [call.call_count for call in starts] == [0, 0]
    state = state_of(world["experiment"])
    assert list(state["execution_plan"]["targets"]) == [str(parent.run_id)]


def test_legacy_control_records_keep_latest_variant_ownership(tenants):
    from model_hub.services import experiment_run_control as controls
    from tfc.temporal.experiments.run_control_activities import _bind

    world, _, parent, newer, _ = _world(tenants["a"])
    workflow_run_id = uuid4()
    _bind(
        {
            "experiment_id": str(world["experiment"].id),
            "config_id": str(world["config"].id),
            "kind": "prompt",
            "workflow_id": "legacy-current",
            "workflow_run_id": str(workflow_run_id),
        }
    )
    entries = controls._named_runs(
        world["experiment"], [str(parent.run_id), str(newer.run_id)]
    )
    assert entries[str(parent.run_id)]["binding"] is None
    current = entries[str(newer.run_id)]["binding"]
    assert (current.workflow_id, current.workflow_run_id) == (
        "legacy-current",
        workflow_run_id,
    )


@pytest.mark.parametrize("door", ["results", "leaderboard"])
def test_current_stored_scoring_setup_marks_completed_variant_running(tenants, door):
    from tfc.temporal.experiments.run_control_activities import _bind

    actor = tenants["a"]
    world, links, parent, newer, metric = _world(actor, subset=True)
    experiment = world["experiment"]
    world["variant"].refresh_from_db()
    assert world["variant"].status == "Completed"
    assert (
        parent.run.metadata["native_identity"]["native_config_hash"]
        == (newer.run.metadata["native_identity"]["native_config_hash"])
    )
    parents = [parent.run_id, newer.run_id]
    before = list(
        native.ExperimentRunItem.no_workspace_objects.filter(run_id__in=parents)
        .order_by("id")
        .values()
    )
    outputs = Cell.no_workspace_objects.filter(column=world["output_column"])
    output_before = list(outputs.order_by("id").values())
    response, start = _post(actor, world, _body(door, world, links, parent, metric))
    assert response.status_code == 200
    assert start.call_count == 1
    state = state_of(experiment)
    context = state["workflow_args"]["native_runs"][str(parent.run_id)]
    child = NativeRunBinding.objects.get(run_id=context["run_id"])
    assert child.run.metadata["native_identity"]["stored_evidence_only"] is True
    assert (
        native.activities._mark_experiment_running_sync(
            str(experiment.id), refuse_if_stopped=True
        )["status"]
        == "READY"
    )
    _bind(
        {
            "experiment_id": str(experiment.id),
            "config_id": context["config_id"],
            "kind": "prompt",
            "workflow_id": (
                f"rerun-eval-v2-{experiment.id}-{state['admission_id']}-{parent.run_id}"
            ),
            "workflow_run_id": str(uuid4()),
        }
    )
    world["variant"].refresh_from_db()
    assert world["variant"].status == "Completed"
    with (
        patch(
            "model_hub.services.native_run_binding.native_prompt_config",
            side_effect=AssertionError("Stored scoring must not resolve the producer"),
        ) as resolve,
        patch(
            "model_hub.views.experiment_runner._render_native_messages",
            side_effect=AssertionError("Stored scoring must not render the producer"),
        ) as render,
        patch(native.PROVIDER) as producer,
        native.native_eval_transport(score=0.25) as (engine, _),
    ):
        prepared = native.activities._setup_prompt_v2_sync(
            str(experiment.id),
            str(world["snapshot"].id),
            context["config_id"],
            native_context=context,
        )
        world["variant"].refresh_from_db()
        child.run.refresh_from_db()
        assert world["variant"].status == "Running"
        assert child.run.status == "queued"
        assert child.run.completed_at is None
        assert child.run.items.count() == 0
        assert prepared["messages"] == []
        assert prepared["run_prompt_config"] is None
        assert prepared["row_ids"] == [str(links[0].snapshot_row_id)]
        assert list(outputs.order_by("id").values()) == output_before
        assert native.row(experiment, context, prepared, prepared["row_ids"][0]) == {
            "row_id": str(links[0].snapshot_row_id),
            "column_id": str(world["output_column"].id),
            "status": "COMPLETED",
        }
        native.finish(context, prepared)
    assert resolve.call_count == render.call_count == producer.call_count == 0
    assert engine.run.call_count == 1
    assert engine.run.call_args.kwargs["input"] == "input-0"
    assert engine.run.call_args.kwargs["output"] == "selected output"
    item = child.run.items.get()
    assert item.status == "completed"
    assert item.output == "selected output"
    assert item.score_refs == [
        {
            "metric_id": str(metric.id),
            "evaluator_version_id": str(metric.pinned_version_id),
            "status": "assessed",
            "value": 0.25,
        }
    ]
    assert list(outputs.order_by("id").values()) == output_before
    assert (
        list(
            native.ExperimentRunItem.no_workspace_objects.filter(run_id__in=parents)
            .order_by("id")
            .values()
        )
        == before
    )


@pytest.mark.parametrize("door", ["results", "leaderboard"])
@pytest.mark.parametrize("failure", ["template", "media"])
@pytest.mark.parametrize("expected_only", [False, True])
def test_historical_scoring_never_resolves_or_renders_current_prompt(
    tenants, door, failure, expected_only
):
    actor = tenants["a"]
    world, links, parent, newer, metric = _world(
        actor, subset=True, producer_change="model_params"
    )
    config = world["config"]
    content = (
        "{{ missing | unknown_filter }}"
        if failure == "template"
        else [{"type": "image_url", "image_url": {"url": "{{missing_media}}"}}]
    )
    config.prompt_version = PromptVersion.no_workspace_objects.create(
        original_template=config.prompt_template,
        template_version="unrenderable-current-prompt",
        prompt_config_snapshot={
            **deepcopy(config.prompt_version.prompt_config_snapshot),
            "messages": [{"role": "user", "content": content}],
        },
        variable_names=deepcopy(config.prompt_version.variable_names),
    )
    config.save(update_fields=["prompt_version"])
    # Expected data is also a captured column value, not a live Cell lookup.
    metric.config["mapping"]["expected_output"] = str(world["source_column"].id)
    if expected_only:
        metric.config["mapping"] = {"expected_output": str(world["source_column"].id)}
        pin = metric.pinned_version
        pin.config_snapshot = {
            **pin.config_snapshot,
            "required_keys": ["expected_output"],
        }
        pin.save(update_fields=["config_snapshot"])
        metric.template.config = dict(pin.config_snapshot)
        metric.template.save(update_fields=["config"])
    metric.save(update_fields=["config"])
    parents = [parent.run_id, newer.run_id]
    before = list(
        native.ExperimentRunItem.no_workspace_objects.filter(run_id__in=parents)
        .order_by("id")
        .values()
    )
    response, start = _post(actor, world, _body(door, world, links, parent, metric))
    assert response.status_code == 200, response.content
    assert start.call_count == 1
    context = state_of(world["experiment"])["workflow_args"]["native_runs"][
        str(parent.run_id)
    ]
    child = NativeRunBinding.objects.get(run_id=context["run_id"])
    identity = child.run.metadata["native_identity"]
    assert identity["stored_evidence_only"] is True
    assert (
        identity["selected_producer_config_hash"]
        == (parent.run.metadata["native_identity"]["native_config_hash"])
    )
    assert identity["native_config_hash"] != identity["selected_producer_config_hash"]
    Cell.no_workspace_objects.filter(
        dataset__in=[world["dataset"], world["snapshot"]]
    ).update(value="changed after admission")
    with (
        patch(
            "model_hub.services.native_run_binding.native_prompt_config",
            side_effect=ValueError("Current producer cannot resolve"),
        ) as resolve,
        patch(
            "model_hub.views.experiment_runner._render_native_messages",
            side_effect=ValueError(f"Current {failure} cannot render"),
        ) as render,
        patch(native.PROVIDER) as producer,
        native.native_eval_transport(score=0.25) as (engine, _),
    ):
        prepared = native.activities._setup_prompt_v2_sync(
            str(world["experiment"].id),
            str(world["snapshot"].id),
            str(config.id),
            native_context=context,
        )
        assert prepared["messages"] == []
        assert prepared["row_ids"] == [str(links[0].snapshot_row_id)]
        assert native.row(
            world["experiment"], context, prepared, prepared["row_ids"][0]
        ) == {
            "row_id": str(links[0].snapshot_row_id),
            "column_id": str(world["output_column"].id),
            "status": "COMPLETED",
        }
        native.finish(context, prepared)
    assert resolve.call_count == render.call_count == producer.call_count == 0
    assert engine.run.call_count == 1
    expected_values = {"expected_output": "input-0"}
    if not expected_only:
        expected_values.update(input="input-0", output="selected output")
    assert {key: engine.run.call_args.kwargs[key] for key in expected_values} == (
        expected_values
    )
    if expected_only:
        assert set(engine.run.call_args.kwargs) & {"input", "output"} == set()
    item = child.run.items.get()
    assert item.status == "completed"
    assert item.input == parent.run.items.get().input
    assert item.output == "selected output"
    assert item.score_refs == [
        {
            "metric_id": str(metric.id),
            "evaluator_version_id": str(metric.pinned_version_id),
            "status": "assessed",
            "value": 0.25,
        }
    ]
    assert (
        list(
            native.ExperimentRunItem.no_workspace_objects.filter(run_id__in=parents)
            .order_by("id")
            .values()
        )
        == before
    )


def _persist_phase(experiment, execution, phase):
    from model_hub.services.experiment_run_state import persist_run_state

    persist_run_state(
        {
            "experiment_id": str(experiment.id),
            "workflow_run_id": str(execution.workflow_run_id),
            "config_id": str(execution.config_id),
            "phase": phase,
        }
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("stop_index", [0, 1])
def test_selected_sibling_phases_keep_each_other_runnable(tenants, reverse, stop_index):
    from model_hub.services import experiment_run_control as controls
    from tfc.temporal.experiments.run_control_activities import _bind

    actor = tenants["a"]
    world, links, parent, newer, metric = _world(actor)
    experiment = world["experiment"]
    before = list(
        native.ExperimentRunItem.no_workspace_objects.filter(
            run_id__in=[parent.run_id, newer.run_id]
        )
        .order_by("id")
        .values()
    )
    response, start = _post(
        actor,
        world,
        {
            "source_ids": [str(parent.run_id), str(newer.run_id)],
            "user_eval_metric_ids": [str(metric.id)],
        },
    )
    assert response.status_code == 200
    assert start.call_count == 1
    state = state_of(experiment)
    contexts = state["workflow_args"]["native_runs"]
    ordered = sorted(contexts, reverse=reverse)
    executions, prepared = {}, {}
    assert (
        native.activities._mark_experiment_running_sync(
            str(experiment.id), refuse_if_stopped=True
        )["status"]
        == "READY"
    )

    def statuses(expected):
        experiment.refresh_from_db()
        world["variant"].refresh_from_db()
        assert experiment.status == expected
        assert world["variant"].status == expected

    for index, selected in enumerate(ordered):
        context = contexts[selected]
        _bind(
            {
                "experiment_id": str(experiment.id),
                "config_id": context["config_id"],
                "kind": "prompt",
                "workflow_id": (
                    f"rerun-eval-v2-{experiment.id}-{state['admission_id']}-{selected}"
                ),
                "workflow_run_id": str(uuid4()),
            }
        )
        execution = controls._named_runs(experiment, [context["run_id"]])[
            context["run_id"]
        ]["binding"]
        executions[selected] = execution
        prepared[selected] = native.activities._setup_prompt_v2_sync(
            str(experiment.id),
            str(world["snapshot"].id),
            str(world["config"].id),
            native_context=context,
        )
        statuses("Running")
        if index == 0:
            # The queued sibling counts even before its control activity binds.
            _persist_phase(experiment, execution, "STOPPED")
            statuses("Running")
            _persist_phase(experiment, execution, "RUNNING")
    selected, sibling = ordered[stop_index], ordered[1 - stop_index]

    def phases(expected):
        for key, execution in executions.items():
            execution.refresh_from_db()
            assert execution.phase == expected[key]
        ids = [contexts[key]["run_id"] for key in ordered]
        owned = controls._named_runs(experiment, ids)
        assert {
            key: owned[contexts[key]["run_id"]]["binding"].phase for key in ordered
        } == expected
        response = actor["client"].get(f"/model-hub/experiments/v2/{experiment.id}/")
        assert response.status_code == 200
        rows = {row["run_id"]: row for row in response.data["result"]["runs"]}
        for key in ordered:
            canonical = ExperimentRun.no_workspace_objects.get(
                pk=contexts[key]["run_id"]
            )
            assert rows[str(canonical.id)]["status"] == (
                "cancelled" if expected[key] == "STOPPED" else canonical.status
            )
            assert canonical.completed_at is None

    _persist_phase(experiment, executions[selected], "STOPPED")
    phases({selected: "STOPPED", sibling: "RUNNING"})
    statuses("Running")
    with native.native_eval_transport(score=0.25) as (engine, _):
        assert native.row(
            experiment,
            contexts[sibling],
            prepared[sibling],
            str(links[0].snapshot_row_id),
        ) == {
            "row_id": str(links[0].snapshot_row_id),
            "column_id": str(world["output_column"].id),
            "status": "COMPLETED",
        }
        assert engine.run.call_count == 1
        _persist_phase(experiment, executions[sibling], "STOPPED")
        phases({selected: "STOPPED", sibling: "STOPPED"})
        statuses("Cancelled")
        # Resuming either exact child leaves the other stopped and runs its row.
        for key, other in ((selected, sibling), (sibling, selected)):
            _persist_phase(experiment, executions[key], "RUNNING")
            phases({key: "RUNNING", other: "STOPPED"})
            statuses("Running")
            assert native.row(
                experiment,
                contexts[key],
                prepared[key],
                str(links[1].snapshot_row_id),
            ) == {
                "row_id": str(links[1].snapshot_row_id),
                "column_id": str(world["output_column"].id),
                "status": "COMPLETED",
            }
            _persist_phase(experiment, executions[key], "STOPPED")
            phases({key: "STOPPED", other: "STOPPED"})
            statuses("Cancelled")
        assert engine.run.call_count == 3
    assert (
        list(
            native.ExperimentRunItem.no_workspace_objects.filter(
                run_id__in=[parent.run_id, newer.run_id]
            )
            .order_by("id")
            .values()
        )
        == before
    )


def test_legacy_phase_persistence_keeps_latest_execution_ownership(tenants):
    from model_hub.models.experiment_run_control import ExperimentRunControl
    from tfc.temporal.experiments.run_control_activities import _bind

    world, _, _, _, _ = _world(tenants["a"])
    experiment = world["experiment"]
    executions = []
    for name in ("previous", "current"):
        execution_id = str(uuid4())
        _bind(
            {
                "experiment_id": str(experiment.id),
                "config_id": str(world["config"].id),
                "kind": "prompt",
                "workflow_id": f"legacy-{name}",
                "workflow_run_id": execution_id,
            }
        )
        executions.append(
            ExperimentRunControl.objects.get(workflow_run_id=execution_id)
        )
    previous, current = executions
    _persist_phase(experiment, current, "RUNNING")
    _persist_phase(experiment, previous, "STOPPED")
    previous.refresh_from_db()
    assert previous.phase == "RUNNING"
    for phase, expected in (("STOPPED", "Cancelled"), ("RUNNING", "Running")):
        _persist_phase(experiment, current, phase)
        experiment.refresh_from_db()
        world["variant"].refresh_from_db()
        current.refresh_from_db()
        assert current.phase == phase
        assert experiment.status == expected
        assert world["variant"].status == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("messages", [{"role": "user", "content": "tampered"}]),
        ("schema", "unsupported"),
        ("native_config_hash", "0" * 64),
        ("prompt_version_id", str(UUID(int=1))),
        ("messages", None),
    ],
)
def test_invalid_prompt_evidence_refuses_selection(tenants, field, value):
    world, links, parent, _, metric = _world(
        tenants["a"], subset=True, producer_change="prompt_version"
    )
    item = parent.run.items.get()
    item.metadata[_RUN_KEY]["result_receipt"]["prompt_evidence"][field] = value
    item.save(update_fields=["metadata"])
    metric.config["mapping"]["input"] = "prompt_chain"
    metric.save(update_fields=["config"])
    before = _stored_state(world["experiment"])
    with _no_scoring():
        response, start = _post(
            tenants["a"], world, _body("results", world, links, parent, metric)
        )
    assert response.status_code == 400
    assert response.data["result"] == (
        "this evaluator needs the run's original prompt settings, "
        "which weren't recorded; re-run the experiment to score it "
        "with its current configuration"
    )
    assert start.call_count == 0
    assert _stored_state(world["experiment"]) == before


@pytest.mark.parametrize("door", ["results", "leaderboard"])
def test_saved_prompt_and_output_survive_repeated_rescoring(tenants, door):
    world, links, parent, _, metric = _world(
        tenants["a"], subset=True, producer_change="prompt_version"
    )
    original = list(parent.run.items.values())
    evidence = original[0]["metadata"][_RUN_KEY]["result_receipt"]["prompt_evidence"]
    metric.config["mapping"]["input"] = "prompt_chain"
    metric.save(update_fields=["config"])
    with (
        patch(native.PROVIDER, side_effect=AssertionError("No prompt provider")) as p,
        patch(
            "model_hub.views.experiment_runner._render_native_messages",
            side_effect=AssertionError("Live rendering changed"),
        ) as render,
        native.native_eval_transport(score=0.25) as (engine, _),
    ):
        for number in range(2):
            response, start = _post(
                tenants["a"],
                world,
                _body(door, world, links, parent, metric),
                key=f"saved-prompt-{number}",
            )
            assert response.status_code == 200, response.content
            assert start.call_count == 1
            state = state_of(world["experiment"])
            _dispatch_selected(world, state)
            context = state["workflow_args"]["native_runs"][str(parent.run_id)]
            child = NativeRunBinding.objects.get(run_id=context["run_id"])
            item = child.run.items.get()
            assert item.metadata[_RUN_KEY]["result_receipt"]["prompt_evidence"] == (
                evidence
            )
            assert item.score_refs == [
                {
                    "metric_id": str(metric.id),
                    "evaluator_version_id": str(metric.pinned_version_id),
                    "status": "assessed",
                    "value": 0.25,
                }
            ]
            assert (
                child.run.metadata["native_identity"]["selected_producer_config_hash"]
                == evidence["native_config_hash"]
            )
            assert list(parent.run.items.values()) == original
            parent, original = child, list(child.run.items.values())
    assert p.call_count == render.call_count == 0
    assert [
        (call.kwargs["input"], call.kwargs["output"])
        for call in engine.run.call_args_list
    ] == [
        ("user: original", "selected output"),
        ("user: original", "selected output"),
    ]


@pytest.mark.parametrize("field", ["messages", "output", "score_refs"])
def test_pinned_prompt_receipt_is_reverified_before_scoring(tenants, field):
    from model_hub.services.experiment_run_writes import RunWriteError

    world, links, parent, _, metric = _world(
        tenants["a"], subset=True, producer_change="prompt_version"
    )
    metric.config["mapping"]["input"] = "prompt_chain"
    metric.save(update_fields=["config"])
    response, _ = _post(
        tenants["a"], world, _body("results", world, links, parent, metric)
    )
    assert response.status_code == 200
    context = state_of(world["experiment"])["workflow_args"]["native_runs"][
        str(parent.run_id)
    ]
    item = parent.run.items.get()
    if field == "messages":
        item.metadata[_RUN_KEY]["result_receipt"]["prompt_evidence"][field] = []
        item.save(update_fields=["metadata"])
    else:
        setattr(item, field, "changed" if field == "output" else [{"value": 1}])
        item.save(update_fields=[field])
    with native.native_eval_transport() as (engine, calls):
        with pytest.raises(RunWriteError):
            prepared = native.activities._setup_prompt_v2_sync(
                str(world["experiment"].id),
                str(world["snapshot"].id),
                str(world["config"].id),
                native_context=context,
            )
            native.row(
                world["experiment"], context, prepared, str(links[0].snapshot_row_id)
            )
    assert engine.run.call_count == calls.call_count == 0
    assert (
        native.ExperimentRunItem.no_workspace_objects.filter(
            run_id=context["run_id"]
        ).count()
        == 0
    )
