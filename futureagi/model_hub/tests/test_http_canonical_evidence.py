"""HTTP execution must record canonical evidence alongside unchanged Cells.

Static, tests-first specifications; not an execution receipt. HTTP is replaced
only at outbound_destination.request. Evaluator results enter the real worker
and its fenced Cell writer, never a test-created ExperimentRunItem.

Optional top-level trace_id/root_span_id in the endpoint's JSON are canonical
metadata only; they must not change the selected output or classic Cell JSON.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import requests
from django.db import transaction
from django.urls import reverse

from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import (
    ExperimentRun,
    ExperimentRunItem,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import (
    NativeExampleBinding,
    NativeRunBinding,
)
from model_hub.services import http_experiment_source as http
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.services.experiment_run_writes import _RUN_KEY
from model_hub.services.experiment_submit import (
    admit_submission,
    ensure_native_idle,
    stage_submission,
    state_of,
)
from model_hub.services.http_evaluation_fence import HttpEvaluationStopped
from model_hub.tests.test_http_experiment_source import entry
from model_hub.tests.test_http_experiment_source import tenants as tenants
from model_hub.views.eval_runner import EvaluationRunner
from model_hub.views.experiment_runner import (
    ExperimentRunner,
    check_and_update_experiment_status,
)
from tfc.temporal.experiments import activities
from tfc.utils import outbound_destination

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

TRACE_IDS = {"first": "1" * 32, "second": "2" * 32}
SPAN_IDS = {"first": "3" * 16, "second": "4" * 16}


def _body(response):
    assert response.status_code == 200, response.content
    assert response.json()["status"] is True
    return response.json()["result"]


def _stage(tenants, *, evaluated=False, pending=False, presentation=False):
    actor = tenants["a"]
    dataset = actor["exp"]["dataset"]
    input_column = dataset.column_set.get()
    first = actor["exp"]["row"]
    Cell.no_workspace_objects.filter(row=first, column=input_column).update(
        value="first"
    )
    second = Row.no_workspace_objects.create(dataset=dataset, order=1)
    Cell.no_workspace_objects.create(
        dataset=dataset,
        column=input_column,
        row=second,
        value="second",
        status=CellStatus.PASS.value,
    )
    expected = None
    if presentation:
        input_column.name = "Question"
        input_column.save(update_fields=["name"])
        expected = Column.no_workspace_objects.create(
            dataset=dataset,
            name="Expected",
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        for row, question in ((first, "first"), (second, "second")):
            row.metadata = {"category": "captured", "split_names": ["holdout"]}
            row.save(update_fields=["metadata"])
            Cell.no_workspace_objects.create(
                dataset=dataset, row=row, column=expected, value=f"expected:{question}"
            )
    experiment = ExperimentsTable.no_workspace_objects.create(
        name="HTTP evidence",
        column=expected,
        dataset=dataset,
        user=actor["user"],
        experiment_type="llm",
        prompt_config=[],
    )
    create_dataset_snapshot(dataset, experiment)
    config = entry()
    config["variable_mapping"] = {"question": str(input_column.pk)}
    source = http.persist_http_source(experiment, config)
    output = http.create_http_output_column(experiment, source)
    metric = version = None
    if evaluated:
        template = EvalTemplate.no_workspace_objects.create(
            name="Endpoint answer check",
            organization=actor["org"],
            workspace=actor["workspace"],
            criteria="Check {{output}}",
            model="fixture-model",
            config={"output": "score", "eval_type_id": "Equals"},
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
            name="Endpoint score",
            dataset=experiment.snapshot_dataset,
            organization=actor["org"],
            workspace=actor["workspace"],
            user=actor["user"],
            template=template,
            pinned_version=version,
            source_id=str(experiment.pk),
            status=StatusType.RUNNING.value,
            config={"mapping": {"output": "output"}, "reason_column": True},
        )
        experiment.user_eval_template_ids.add(metric)
    with transaction.atomic():
        submission = stage_submission(
            experiment, {}, "create", authorized_workspace=actor["workspace"]
        )
        http.bind_http_submission(experiment, submission["admission_id"])
    links = list(
        NativeExampleBinding.objects.filter(snapshot__experiment=experiment)
        .select_related("version_item", "snapshot_row", "snapshot")
        .order_by("row_order")
    )
    assert len(links) == 2
    world = SimpleNamespace(
        actor=actor,
        dataset=dataset,
        experiment=experiment,
        source=source,
        output=output,
        metric=metric,
        version=version,
        links=links,
        admission=submission["admission_id"],
        inputs={str(first.pk): "first", str(second.pk): "second"},
        plan=None,
    )
    if not pending:
        _accept(world)
    return world


def _accept(world):
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="http-evidence-workflow",
    ):
        admit_submission(str(world.experiment.pk), world.admission)
    runner = ExperimentRunner(world.experiment.pk)
    runner.load_experiment()
    assert http.prepare_http_experiment(runner, world.admission)
    world.plan = http.setup_http_source(
        str(world.experiment.pk), str(world.source.pk), world.admission
    )
    world.experiment.refresh_from_db()
    world.source.refresh_from_db()


def _run(world, admission=None):
    bindings = NativeRunBinding.objects.filter(
        variant=world.source, execution_id=admission or world.admission
    )
    assert bindings.count() == 1, "HTTP admission must bind one canonical run"
    return bindings.select_related("run").get().run


def _cells(world, **filters):
    return list(
        Cell.no_workspace_objects.filter(
            dataset_id=world.experiment.snapshot_dataset_id, **filters
        )
        .order_by("id")
        .values()
    )


def _evidence(world):
    return {
        "runs": list(
            ExperimentRun.no_workspace_objects.filter(experiment=world.experiment)
            .order_by("id")
            .values()
        ),
        "items": list(
            ExperimentRunItem.no_workspace_objects.filter(
                run__experiment=world.experiment
            )
            .order_by("id")
            .values()
        ),
        "bindings": list(
            NativeRunBinding.objects.filter(snapshot__experiment=world.experiment)
            .order_by("id")
            .values()
        ),
        "cells": _cells(world),
    }


def _reply(question, *, traced=False, status=200):
    payload = {"reply": f"answer:{question}"}
    if traced:
        payload.update(trace_id=TRACE_IDS[question], root_span_id=SPAN_IDS[question])
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    return response


def _process(world, row_id):
    return ExperimentRunner(world.experiment.pk).process_http_row(
        source_id=str(world.source.pk),
        row_id=str(row_id),
        admission_id=world.admission,
    )


def _dispatch(world, link, *, traced=False, status=200):
    question = world.inputs[str(link.source_row_id)]
    with (
        patch.object(http, "monotonic", side_effect=[10.0, 10.125]),
        patch.object(
            outbound_destination,
            "request",
            return_value=_reply(question, traced=traced, status=status),
        ) as send,
    ):
        result = _process(world, link.snapshot_row_id)
    send.assert_called_once()
    sent = json.loads(send.call_args.kwargs["data"])
    assert sent == {"question": question, "id": str(link.source_row_id)}
    return result


def _evaluator(world):
    context = {
        key: world.plan[key]
        for key in (
            "experiment_id",
            "source_id",
            "admission_id",
            "dataset_id",
            "column_id",
        )
    }
    evaluator = EvaluationRunner(
        world.metric.pk,
        experiment_dataset=world.source,
        column=world.output,
        format_output=True,
        source_configs={
            "experiment_id": str(world.experiment.pk),
            "http_context": context,
        },
    )
    evaluator.load_user_eval_metric()
    score = world.source.columns.get(
        dataset=world.experiment.snapshot_dataset,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        deleted=False,
    )
    reason = world.source.columns.get(
        dataset=world.experiment.snapshot_dataset,
        source=SourceChoices.EVALUATION_REASON.value,
        deleted=False,
    )
    return evaluator, score, reason


def _evaluate(world, link, value):
    evaluator, score, reason = _evaluator(world)
    response = {"reason": "checked"}
    with (
        patch("model_hub.views.eval_runner.close_old_connections"),
        patch.object(
            evaluator,
            "_process_eval_result",
            return_value=(response, CellStatus.PASS.value, str(value)),
        ) as evaluate,
    ):
        evaluator._process_and_create_cell(
            link.snapshot_row,
            world.metric.config["mapping"],
            world.experiment.snapshot_dataset,
            score,
        )
    evaluate.assert_called_once()
    evaluator._create_reason_cell(
        world.experiment.snapshot_dataset,
        reason,
        link.snapshot_row,
        response,
        "checked",
        CellStatus.PASS.value,
    )
    return score, reason


def _finish(world):
    result = http.finish_http_source(
        str(world.experiment.pk), str(world.source.pk), world.admission
    )
    assert result["status"] == "COMPLETED"
    assert check_and_update_experiment_status(world.experiment.pk)
    world.experiment.refresh_from_db()
    assert world.experiment.status == StatusType.COMPLETED.value
    return result


def _complete(world):
    for link in world.links:
        assert _dispatch(world, link)["status"] == "COMPLETED"
    _finish(world)
    run = _run(world)
    assert run.status == "completed" and run.completed_at is not None
    return run


def _control(world, action, admission=None):
    with patch.object(activities, "close_old_connections"):
        return activities._http_execution_control_sync(
            str(world.experiment.pk), admission or world.admission, action
        )


def _stop(world):
    with patch(
        "tfc.temporal.experiments.cancel_all_experiment_workflows",
        return_value={
            "main_cancelled": True,
            "rerun_cancelled": 0,
            "main_confirmed": True,
            "reruns_confirmed": True,
        },
    ):
        _body(
            world.actor["client"].post(
                f"/model-hub/experiments/v2/{world.experiment.pk}/stop/",
                {},
                format="json",
            )
        )
    run = _run(world)
    assert run.status == "cancelled" and run.completed_at is not None
    _control(world, "stop")
    ensure_native_idle(world.experiment)


def _edit(world):
    world.experiment.refresh_from_db()
    world.source.refresh_from_db()
    ensure_native_idle(world.experiment)
    config = http.public_http_source(world.source)
    config.pop("order")
    config["name"] = "Edited endpoint"
    before = _evidence(world)
    with (
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start,
        patch.object(outbound_destination, "request") as send,
    ):
        _body(
            world.actor["client"].put(
                f"/model-hub/experiments/v2/{world.experiment.pk}/",
                {"prompt_config": [config]},
                format="json",
            )
        )
    start.assert_not_called()
    send.assert_not_called()
    world.source.refresh_from_db()
    assert world.source.name == "Edited endpoint"
    assert _evidence(world) == before


def _retry(world, body, key):
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="http-retry-workflow",
    ):
        return world.actor["client"].post(
            f"/model-hub/experiments/v2/{world.experiment.pk}/rerun-cells/",
            body,
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )


def test_http_admission_binds_snapshot_plan_once_before_dispatch(tenants):
    world = _stage(tenants, pending=True)
    run = _run(world)
    assert run.status == "draft" and run.completed_at is None
    assert run.task_kind == "external"
    assert run.prompt_version_id is None and run.agent_version_id is None
    assert run.dataset_version_id == world.links[0].snapshot.dataset_version_id
    assert run.experiment_id == world.experiment.pk
    expected = {(str(link.version_item.example_id), 1) for link in world.links}
    plan = run.metadata[_RUN_KEY]["plan"]["identities"]
    assert {(item["example_id"], item["trial_number"]) for item in plan} == expected
    assert not ExperimentRunItem.no_workspace_objects.filter(run=run).exists()
    with transaction.atomic():
        http.bind_http_submission(world.experiment, world.admission)
    assert _run(world).pk == run.pk
    _accept(world)
    assert _run(world).status == "queued"
    first = _evidence(world)
    http.setup_http_source(
        str(world.experiment.pk), str(world.source.pk), world.admission
    )
    assert _evidence(world) == first


@pytest.mark.parametrize("traced", [False, True])
def test_http_evidence_reaches_catalog_compare_and_every_row_run(tenants, traced):
    world = _stage(tenants, evaluated=True)
    captured_inputs = _cells(
        world, column_id__in=world.links[0].snapshot.column_map.values()
    )
    score_values = {"first": 0, "second": 1}
    for link in world.links:
        question = world.inputs[str(link.source_row_id)]
        assert _dispatch(world, link, traced=traced)["status"] == "COMPLETED"
        score, reason = _evaluate(world, link, score_values[question])
        cell = Cell.no_workspace_objects.get(row=link.snapshot_row, column=world.output)
        assert cell.value == f"answer:{question}"
        assert cell.status == CellStatus.PASS.value and cell.response_time == 125.0
        assert cell.value_infos == json.dumps(
            {
                "metadata": {"response_time": 125.0},
                "http_attempt_history": [],
                "http_attempt_id": http.http_attempt_id(
                    world.admission, world.source.pk, link.snapshot_row_id
                ),
                "http_status": 200,
                "error": None,
                "reason": None,
            }
        )
        for column, value in (
            (score, str(score_values[question])),
            (reason, "checked"),
        ):
            saved = Cell.no_workspace_objects.get(row=link.snapshot_row, column=column)
            assert saved.value == value and saved.status == CellStatus.PASS.value
            assert saved.value_infos == json.dumps({"reason": "checked"})
    classic = _cells(world)
    _finish(world)
    assert _cells(world) == classic
    assert _cells(
        world, column_id__in=world.links[0].snapshot.column_map.values()
    ) == captured_inputs
    run = _run(world)
    assert run.status == "completed" and run.completed_at is not None
    assert len(run.evaluator_versions) == 1
    pin = run.evaluator_versions[0]
    assert pin["metric_id"] == str(world.metric.pk)
    assert pin["evaluator_version_id"] == str(world.version.pk)
    items = list(ExperimentRunItem.no_workspace_objects.filter(run=run))
    assert len(items) == len(world.links)
    before_reads = _evidence(world)
    client = world.actor["client"]
    catalog = _body(
        client.get(
            reverse("dataset-results-runs", kwargs={"dataset_id": world.dataset.pk}),
            {"experiment_id": str(world.experiment.pk)},
        )
    )
    assert [item["run_id"] for item in catalog["results"]] == [str(run.pk)]
    detail = _body(client.get(f"/model-hub/experiments/v2/{world.experiment.pk}/"))
    assert [item["run_id"] for item in detail["runs"]] == [str(run.pk)]
    query = {"runs": str(run.pk), "baseline": "none"}
    url = reverse("dataset-results", kwargs={"dataset_id": world.dataset.pk})
    grid = _body(client.get(url, query))
    matrix = _body(client.get(url, {**query, "shape": "matrix"}))
    assert grid["total"] == matrix["total"] == 2
    for link in world.links:
        question = world.inputs[str(link.source_row_id)]
        expected_output = f"answer:{question}"
        example_id = str(link.version_item.example_id)
        item = next(item for item in items if str(item.example_id) == example_id)
        assert item.status == "completed" and item.trial_number == 1
        assert item.output == expected_output and item.latency_ms == 125.0
        assert item.trace_id == (TRACE_IDS[question] if traced else None)
        assert item.root_span_id == (SPAN_IDS[question] if traced else None)
        assert len(item.score_refs) == 1
        ref = item.score_refs[0]
        assert ref["metric_id"] == str(world.metric.pk)
        assert ref["evaluator_version_id"] == str(world.version.pk)
        assert ref["status"] == "assessed" and ref["value"] == score_values[question]
        expected_trace = str(UUID(TRACE_IDS[question])) if traced else None
        cell = next(row for row in grid["rows"] if row["example_id"] == example_id)
        result = cell["results"][str(run.pk)]
        assert result["output"] == expected_output
        assert result["scores"][str(world.metric.pk)]["value"] == score_values[question]
        assert result["trials_detail"][0]["trace_id"] == expected_trace
        matrix_row = next(row for row in matrix["rows"] if row["row_id"] == example_id)
        matrix_cell = matrix_row["cells"][str(run.pk)]
        assert matrix_cell["trial_numbers"] == [1]
        assert (
            matrix_cell["scores"][str(world.metric.pk)]["value"]
            == score_values[question]
        )
        assert matrix_cell["trace_ids"] == [expected_trace]
        rows = _body(
            client.get(
                reverse(
                    "dataset-row-runs",
                    kwargs={
                        "dataset_id": world.dataset.pk,
                        "row_id": link.source_row_id,
                    },
                )
            )
        )
        assert [row["run_id"] for row in rows["runs"]] == [str(run.pk)]
        row_run = rows["runs"][0]
        assert row_run["example_id"] == example_id
        assert row_run["output"] == expected_output and row_run["trials"] == 1
        assert row_run["evals"][str(world.metric.pk)]["value"] == score_values[question]
        assert row_run["trace_id"] == expected_trace
    assert _evidence(world) == before_reads


def test_http_duplicate_delivery_never_reposts_or_rewrites_evidence(tenants):
    world = _stage(tenants)
    link = world.links[0]

    def response_while_claimed(**kwargs):
        assert json.loads(kwargs["data"])["id"] == str(link.source_row_id)
        assert _process(world, link.snapshot_row_id)["status"] == "BLOCKED"
        return _reply(world.inputs[str(link.source_row_id)])

    with patch.object(
        outbound_destination, "request", side_effect=response_while_claimed
    ) as send:
        assert _process(world, link.snapshot_row_id)["status"] == "COMPLETED"
        committed = _evidence(world)
        assert _process(world, link.snapshot_row_id)["status"] == "BLOCKED"
        assert http.settle_http_row(
            str(world.experiment.pk),
            str(world.source.pk),
            str(link.snapshot_row_id),
            world.admission,
        )["status"] == "COMPLETED"
        assert _evidence(world) == committed
    send.assert_called_once()
    run = _run(world)
    item = ExperimentRunItem.no_workspace_objects.get(run=run)
    receipt = http.http_attempt_id(
        world.admission, world.source.pk, link.snapshot_row_id
    )
    assert item.metadata[_RUN_KEY]["claim_receipt"]["receipt_key"] == receipt
    assert item.metadata[_RUN_KEY]["result_receipt"]["receipt_key"] == receipt
    _dispatch(world, world.links[1])
    _finish(world)
    sealed = _evidence(world)
    with patch.object(outbound_destination, "request") as send:
        _finish(world)
        assert _process(world, link.snapshot_row_id)["status"] == "BLOCKED"
    send.assert_not_called()
    assert _evidence(world) == sealed


def test_http_explicit_retry_reuses_example_and_trial_without_duplicate_runs(tenants):
    world = _stage(tenants)
    previous = _complete(world)
    old_admission = world.admission
    old_run = ExperimentRun.no_workspace_objects.filter(pk=previous.pk).values().get()
    old_items = list(
        ExperimentRunItem.no_workspace_objects.filter(run=previous)
        .order_by("id")
        .values()
    )
    selected, untouched = world.links
    old_cell = Cell.no_workspace_objects.get(
        row=selected.snapshot_row, column=world.output
    )
    old_info = json.loads(old_cell.value_infos)
    preserved = _cells(world, row_id=untouched.snapshot_row_id)
    body = {
        "cells": [
            {
                "column_id": str(world.output.pk),
                "row_id": str(selected.snapshot_row_id),
            }
        ],
        "acknowledge_http_side_effects": True,
    }
    key = str(uuid4())
    _body(_retry(world, body, key))
    world.admission = state_of(world.experiment)["admission_id"]
    assert world.admission != old_admission
    _accept(world)
    assert world.plan["row_ids"] == [str(selected.snapshot_row_id)]
    current = _run(world)
    assert current.pk != previous.pk
    assert current.dataset_version_id == previous.dataset_version_id
    assert _dispatch(world, selected)["status"] == "COMPLETED"
    _finish(world)
    current_item = ExperimentRunItem.no_workspace_objects.get(run=current)
    previous_item = ExperimentRunItem.no_workspace_objects.get(
        run=previous, example_id=selected.version_item.example_id
    )
    assert current_item.example_id == previous_item.example_id
    assert current_item.trial_number == previous_item.trial_number == 1
    assert current_item.pk != previous_item.pk
    assert current_item.metadata[_RUN_KEY]["claim_receipt"]["receipt_key"] != (
        previous_item.metadata[_RUN_KEY]["claim_receipt"]["receipt_key"]
    )
    cell = Cell.no_workspace_objects.get(row=selected.snapshot_row, column=world.output)
    info = json.loads(cell.value_infos)
    assert info["http_attempt_history"] == [
        {
            "value": old_cell.value,
            "status": old_cell.status,
            "response_time": old_cell.response_time,
            "value_infos": {
                k: v for k, v in old_info.items() if k != "http_attempt_history"
            },
        }
    ]
    assert info["http_attempt_id"] == http.http_attempt_id(
        world.admission, world.source.pk, selected.snapshot_row_id
    )
    assert _cells(world, row_id=untouched.snapshot_row_id) == preserved
    assert (
        ExperimentRun.no_workspace_objects.filter(pk=previous.pk).values().get()
        == old_run
    )
    assert list(
        ExperimentRunItem.no_workspace_objects.filter(run=previous)
        .order_by("id")
        .values()
    ) == old_items
    before = _evidence(world)
    with patch.object(outbound_destination, "request") as send:
        _body(_retry(world, body, key))
        assert _process(world, selected.snapshot_row_id)["status"] == "BLOCKED"
        for action in ("cleanup", "fail", "stop"):
            _control(world, action, old_admission)
        with pytest.raises(ValueError):
            http.run_http_snapshot_row(
                str(world.experiment.pk),
                str(world.source.pk),
                str(selected.snapshot_row_id),
                old_admission,
            )
    send.assert_not_called()
    assert state_of(world.experiment)["admission_id"] == world.admission
    assert _evidence(world) == before
    assert NativeRunBinding.objects.filter(variant=world.source).count() == 2


def test_http_stop_seals_run_and_late_transport_completion_changes_nothing(tenants):
    world = _stage(tenants)
    link = world.links[0]
    stopped = {}

    def stop_before_response(**kwargs):
        assert json.loads(kwargs["data"])["id"] == str(link.source_row_id)
        _stop(world)
        stopped.update(_evidence(world))
        return _reply(world.inputs[str(link.source_row_id)], traced=True)

    with patch.object(
        outbound_destination, "request", side_effect=stop_before_response
    ) as send:
        assert _process(world, link.snapshot_row_id)["status"] == "BLOCKED"
    send.assert_called_once()
    assert stopped and _evidence(world) == stopped
    assert not ExperimentRunItem.no_workspace_objects.filter(
        run=_run(world), status__in=["pending", "running", "completed"]
    ).exists()
    with patch.object(outbound_destination, "request") as send:
        with pytest.raises(ValueError):
            _process(world, world.links[1].snapshot_row_id)
    send.assert_not_called()
    assert _evidence(world) == stopped
    _edit(world)


def test_http_failed_only_reruns_rows_whose_numeric_check_failed_its_pass_rule(tenants):
    from model_hub.tests.test_http_mutations import retry

    world = _stage(tenants, evaluated=True)
    failed, passed = world.links
    for link, value in ((failed, 0), (passed, 1)):
        assert _dispatch(world, link)["status"] == "COMPLETED"
        _evaluate(world, link, value)
    _finish(world)
    body = {
        "source_ids": [str(world.source.pk)],
        "failed_only": True,
        "acknowledge_http_side_effects": True,
    }
    response = retry(world.actor, world.experiment, body, str(uuid4()))
    assert response.status_code == 200, response.content
    # The score is a number under a pass/fail pin: 0 failed the check.
    plan = state_of(world.experiment)["http_plan"]["targets"][str(world.source.pk)]
    assert plan["row_ids"] == [str(failed.snapshot_row_id)]


def test_http_late_evaluation_after_stop_cannot_add_canonical_scores(tenants):
    world = _stage(tenants, evaluated=True)
    link = world.links[0]
    assert _dispatch(world, link)["status"] == "COMPLETED"
    evaluator, score, _ = _evaluator(world)
    stopped = {}

    def stop_before_score(_row, _mapping):
        _stop(world)
        stopped.update(_evidence(world))
        return {"reason": "late"}, CellStatus.PASS.value, "1"

    with (
        patch("model_hub.views.eval_runner.close_old_connections"),
        patch.object(evaluator, "_process_eval_result", side_effect=stop_before_score),
        pytest.raises(HttpEvaluationStopped),
    ):
        evaluator._process_and_create_cell(
            link.snapshot_row,
            world.metric.config["mapping"],
            world.experiment.snapshot_dataset,
            score,
        )
    assert stopped and _evidence(world) == stopped
    for item in ExperimentRunItem.no_workspace_objects.filter(run=_run(world)):
        assert not any(ref["status"] == "assessed" for ref in item.score_refs)


def test_http_stop_before_acknowledgement_ends_draft_without_dispatch(tenants):
    world = _stage(tenants, pending=True)
    assert _run(world).status == "draft"
    with patch.object(outbound_destination, "request") as send:
        _stop(world)
        with pytest.raises(ValueError):
            admit_submission(str(world.experiment.pk), world.admission)
        with pytest.raises(ValueError):
            http.setup_http_source(
                str(world.experiment.pk), str(world.source.pk), world.admission
            )
    send.assert_not_called()
    assert state_of(world.experiment)["state"] == "cancelled"
    assert not ExperimentRunItem.no_workspace_objects.filter(run=_run(world)).exists()
    _edit(world)


@pytest.mark.parametrize("failure_at", ["before_dispatch", "transport", "evaluation"])
def test_http_parent_failure_ends_bound_run_and_allows_edit(tenants, failure_at):
    world = _stage(tenants, evaluated=failure_at == "evaluation")
    if failure_at == "transport":
        with patch.object(
            outbound_destination,
            "request",
            side_effect=RuntimeError("worker interrupted"),
        ) as send:
            with pytest.raises(RuntimeError, match="worker interrupted"):
                _process(world, world.links[0].snapshot_row_id)
        send.assert_called_once()
    elif failure_at == "evaluation":
        assert _dispatch(world, world.links[0])["status"] == "COMPLETED"
    with patch.object(outbound_destination, "request") as send:
        _control(world, "cleanup")
        _control(world, "fail")
    send.assert_not_called()
    run = _run(world)
    assert run.completed_at is not None
    assert run.status in {"failed", "cancelled", "partial"}
    assert not ExperimentRunItem.no_workspace_objects.filter(
        run=run, status__in=["pending", "running"]
    ).exists()
    ended = _evidence(world)
    assert http.persist_http_result(
        source=world.source,
        row=world.links[0].snapshot_row,
        column=world.output,
        attempt_id=http.http_attempt_id(
            world.admission, world.source.pk, world.links[0].snapshot_row_id
        ),
        result={"output": "late", "error": None, "status_code": 200, "latency_ms": 1},
    ) is None
    assert _evidence(world) == ended
    _edit(world)


@pytest.mark.parametrize("outcome", ["http_error", "unknown_outcome"])
def test_http_terminal_row_errors_seal_failed_evidence_without_post_replay(
    tenants, outcome
):
    world = _stage(tenants)
    for link in world.links:
        if outcome == "http_error":
            assert _dispatch(world, link, status=503)["status"] == "FAILED"
        else:
            with patch.object(
                outbound_destination, "request", side_effect=RuntimeError
            ):
                with pytest.raises(RuntimeError):
                    _process(world, link.snapshot_row_id)
            with patch.object(outbound_destination, "request") as send:
                result = http.settle_http_row(
                    str(world.experiment.pk),
                    str(world.source.pk),
                    str(link.snapshot_row_id),
                    world.admission,
                )
            send.assert_not_called()
            assert result["status"] == "FAILED"
    classic = _cells(world)
    assert _finish(world)["failed_rows"] == 2
    assert _cells(world) == classic
    run = _run(world)
    assert run.status == "failed" and run.completed_at is not None
    items = list(ExperimentRunItem.no_workspace_objects.filter(run=run))
    assert len(items) == 2
    for item in items:
        assert item.status == "failed" and item.output is None and item.error
        assert item.trace_id is None and item.score_refs == []
    _edit(world)


def test_http_finished_run_does_not_leave_bound_execution_edit_guard(tenants):
    world = _stage(tenants)
    _complete(world)
    _edit(world)


def test_http_historical_classic_reads_never_manufacture_canonical_evidence(tenants):
    actor = tenants["a"]
    experiment = actor["exp"]["experiment"]
    source = experiment.experiment_datasets.get()
    config = entry()
    config["variable_mapping"] = {
        "question": str(experiment.dataset.column_set.get().pk)
    }
    source.legacy_prompt_config = config
    source.save(update_fields=["legacy_prompt_config"])
    before = list(
        Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset).values()
    )
    counts = (
        ExperimentRun.no_workspace_objects.count(),
        ExperimentRunItem.no_workspace_objects.count(),
        NativeRunBinding.objects.count(),
    )
    catalog = _body(
        actor["client"].get(
            reverse(
                "dataset-results-runs", kwargs={"dataset_id": experiment.dataset_id}
            ),
            {"experiment_id": str(experiment.pk)},
        )
    )
    detail = _body(actor["client"].get(f"/model-hub/experiments/v2/{experiment.pk}/"))
    rows = _body(
        actor["client"].get(
            reverse(
                "dataset-row-runs",
                kwargs={
                    "dataset_id": experiment.dataset_id,
                    "row_id": actor["exp"]["row"].pk,
                },
            )
        )
    )
    assert catalog["results"] == detail["runs"] == rows["runs"] == []
    assert counts == (
        ExperimentRun.no_workspace_objects.count(),
        ExperimentRunItem.no_workspace_objects.count(),
        NativeRunBinding.objects.count(),
    )
    assert list(
        Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset).values()
    ) == before
