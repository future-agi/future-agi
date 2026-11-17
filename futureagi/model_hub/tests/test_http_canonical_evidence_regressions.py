"""HTTP startup, evaluator redelivery and canonical source identity contracts."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.db import IntegrityError, transaction
from django.urls import reverse

from model_hub.models.choices import CellStatus, OwnerChoices, StatusType
from model_hub.models.develop_dataset import Cell
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.experiments import (
    ExperimentRun,
    ExperimentRunItem,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.serializers.experiments import (
    opens_in_full_results,
    with_full_results,
)
from model_hub.services import http_experiment_source as http
from model_hub.services import http_run_recording as recording
from model_hub.services.experiment_run_writes import _RUN_KEY, RunWriteError
from model_hub.services.experiment_submit import (
    admit_submission,
    ensure_native_idle,
    stage_rerun_batch,
    stage_submission,
    state_of,
)
from model_hub.tests.test_http_canonical_evidence import (
    _accept,
    _body,
    _cells,
    _complete,
    _dispatch,
    _evaluate,
    _evaluator,
    _evidence,
    _finish,
    _reply,
    _run,
    _stage,
)
from model_hub.tests.test_http_experiment_source import entry
from model_hub.tests.test_http_experiment_source import tenants as tenants
from model_hub.views.eval_runner import EvaluationRunner
from tfc.temporal.experiments import activities, workflows
from tfc.temporal.experiments.types import RunExperimentInput
from tfc.utils import outbound_destination

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


def _stage_again(world):
    world.experiment.refresh_from_db()
    with transaction.atomic():
        submission = stage_submission(
            world.experiment,
            {},
            "rerun",
            authorized_workspace=world.actor["workspace"],
        )
        world.admission = submission["admission_id"]
        http.bind_http_submission(world.experiment, world.admission, retry=True)


@asynccontextmanager
async def _without_heartbeats():
    yield


@pytest.mark.parametrize("rerun", [False, True])
def test_http_parent_startup_cleanup_preserves_admitted_run(
    tenants, monkeypatch, rerun
):
    world = _stage(tenants, pending=not rerun)
    previous = None
    if rerun:
        previous = _complete(world)
        _stage_again(world)
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="http-startup-workflow",
    ):
        admit_submission(str(world.experiment.pk), world.admission)
    assert _run(world).status == "queued"
    calls = []

    def assert_admission_live():
        run = _run(world)
        assert run.status == "queued" and run.completed_at is None
        world.source.refresh_from_db()
        assert world.source.legacy_prompt_config["_http_execution"]["phase"] == "queued"

    async def execute_activity(name, arg, **_options):
        calls.append(
            "acknowledgement" if getattr(arg, "admission_only", False) else name
        )
        result = await getattr(activities, name)(arg)
        if name == "cleanup_running_cells_activity":
            assert result.status == "COMPLETED"
            await sync_to_async(assert_admission_live, thread_sensitive=True)()
        return result

    async def start_child(function, arg, **_options):
        assert function == workflows.ProcessHttpSourceWorkflow.run
        assert arg.admission_id == world.admission
        return asyncio.create_task(workflows.ProcessHttpSourceWorkflow().run(arg))

    def send_response(**kwargs):
        return _reply(json.loads(kwargs["data"])["question"])

    # Keep ORM activities on the test transaction's thread. The parent,
    # acknowledgement, cleanup and HTTP child all execute their real bodies.
    monkeypatch.setattr(
        activities,
        "otel_sync_to_async",
        lambda function, **_options: sync_to_async(function, thread_sensitive=True),
    )
    monkeypatch.setattr(activities, "Heartbeater", _without_heartbeats)
    monkeypatch.setattr(workflows.workflow, "patched", lambda _name: True)
    monkeypatch.setattr(
        workflows.workflow,
        "memo",
        lambda: {"experiment_submission_id": world.admission},
    )
    monkeypatch.setattr(workflows.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflows.workflow, "start_child_workflow", start_child)
    with (
        patch.object(activities, "close_old_connections"),
        patch.object(
            outbound_destination, "request", side_effect=send_response
        ) as send,
    ):
        result = async_to_sync(workflows.RunExperimentV2Workflow().run)(
            RunExperimentInput(str(world.experiment.pk), max_concurrent_rows=1)
        )
    assert calls[:3] == [
        "acknowledgement",
        "cleanup_running_cells_activity",
        "setup_experiment_activity",
    ]
    assert calls.index("cleanup_running_cells_activity") < calls.index(
        "process_http_row_activity"
    )
    assert send.call_count == len(world.links)
    assert result.status == StatusType.COMPLETED.value
    assert result.total_rows_processed == len(world.links)
    assert result.failed_rows == 0
    run = _run(world)
    assert run.status == "completed" and run.completed_at is not None
    assert ExperimentRunItem.no_workspace_objects.filter(
        run=run, status="completed"
    ).count() == len(world.links)
    world.source.refresh_from_db()
    assert world.source.legacy_prompt_config["_http_execution"]["phase"] == "finished"
    if previous is not None:
        assert run.pk != previous.pk
        previous.refresh_from_db()
        assert previous.status == "completed" and previous.completed_at is not None


def _batch(world, links):
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
    result = activities._process_batch_template_eval_sync(
        [str(link.snapshot_row_id) for link in links],
        str(world.output.pk),
        str(world.experiment.snapshot_dataset_id),
        str(world.experiment.pk),
        str(world.metric.pk),
        http_context=context,
    )
    assert result["status"] == "COMPLETED", result
    return result


def _items(world, links):
    return list(
        ExperimentRunItem.no_workspace_objects.filter(
            run=_run(world),
            example_id__in=[link.version_item.example_id for link in links],
        )
        .order_by("id")
        .values()
    )


@pytest.fixture
def evaluator_result(monkeypatch):
    # Replace only external evaluation and telemetry boundaries. Real batch
    # orchestration, row resets, reason writes and canonical commits still run.
    monkeypatch.setitem(
        sys.modules,
        "ee.usage.services.metering",
        SimpleNamespace(check_usage=lambda *_args: SimpleNamespace(allowed=True)),
    )
    with patch("model_hub.views.eval_runner.track_mixpanel_event"):
        yield


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("recorded_count", [1, 2])
@pytest.mark.parametrize("outcome", ["assessed", "not_assessed", "error"])
def test_http_evaluator_redelivery_preserves_committed_rows(
    tenants, evaluator_result, recorded_count, outcome
):
    world = _stage(tenants, evaluated=True)
    for link in world.links:
        assert _dispatch(world, link)["status"] == "COMPLETED"
    calls = {}
    original_status = (
        CellStatus.ERROR.value if outcome == "error" else CellStatus.PASS.value
    )
    original_value = {
        "assessed": "1",
        "not_assessed": "unrated",
        "error": "ERROR",
    }[outcome]

    def evaluate(runner, row, _mapping):
        key = str(row.pk)
        calls[key] = calls.get(key, 0) + 1
        first_delivery = calls[key] == 1
        response = {"reason": "original" if first_delivery else "changed"}
        status = original_status if first_delivery else CellStatus.PASS.value
        value = original_value if first_delivery else "0"
        reason_column = runner._create_reason_column(runner.dataset, "reason")
        assert reason_column is not None
        runner._create_reason_cell(
            runner.dataset, reason_column, row, response, response["reason"], status
        )
        return response, status, value

    recorded_links = world.links[:recorded_count]
    row_ids = [link.snapshot_row_id for link in recorded_links]
    with patch.object(
        EvaluationRunner, "_process_eval_result", autospec=True, side_effect=evaluate
    ) as evaluate_call:
        _batch(world, recorded_links)
        original_cells = _cells(world, row_id__in=row_ids)
        original_items = _items(world, recorded_links)
        for item in original_items:
            assert item["score_refs"][0]["status"] == outcome
            assert item["score_refs"][0]["explanation"] == "original"
        # The first activity committed, but its acknowledgement was lost.
        # Redelivery also includes unrecorded rows for the partial-batch case.
        _batch(world, world.links)
        assert evaluate_call.call_count == len(world.links)
        assert all(count == 1 for count in calls.values())
        assert _cells(world, row_id__in=row_ids) == original_cells
        assert _items(world, recorded_links) == original_items
        committed = _evidence(world)
        _batch(world, world.links)
        assert evaluate_call.call_count == len(world.links)
        assert _evidence(world) == committed
    assert Cell.no_workspace_objects.filter(
        row_id__in=row_ids, value="original", status=original_status
    ).count() == recorded_count
    _finish(world)
    assert _run(world).status == "completed"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("late_failure", [False, True])
@pytest.mark.parametrize("reason_order", ["before", "after"])
def test_http_overlapping_evaluator_deliveries_preserve_first_receipt(
    tenants, evaluator_result, late_failure, reason_order
):
    world = _stage(tenants, evaluated=True)
    for link in world.links:
        _dispatch(world, link)
    link = world.links[0]
    calls = []
    committed = {}

    def evaluate(runner, row, _mapping):
        calls.append(row.pk)
        first_to_finish = len(calls) == 2
        response = {"reason": "original" if first_to_finish else "late"}
        reason_column = runner._create_reason_column(runner.dataset, "reason")
        assert reason_column is not None

        def write_reason():
            runner._create_reason_cell(
                runner.dataset,
                reason_column,
                row,
                response,
                response["reason"],
                CellStatus.PASS.value,
            )

        if first_to_finish:
            write_reason()
            return response, CellStatus.PASS.value, "1"
        if reason_order == "before":
            write_reason()
        # The outer delivery has reset and started evaluation. Run a second
        # delivery to completion before the outer one may commit or fail.
        _batch(world, [link])
        committed.update(_evidence(world))
        if reason_order == "after":
            write_reason()
        if late_failure:
            raise RuntimeError("Late evaluator failed")
        return response, CellStatus.PASS.value, "0"

    with patch.object(
        EvaluationRunner, "_process_eval_result", autospec=True, side_effect=evaluate
    ):
        _batch(world, [link])
        assert calls == [link.snapshot_row_id, link.snapshot_row_id]
        assert _evidence(world) == committed
        _batch(world, [link])
        assert len(calls) == 2
        assert _evidence(world) == committed
    item = _items(world, [link])[0]
    assert item["score_refs"][0]["value"] == 1
    assert item["score_refs"][0]["explanation"] == "original"
    assert Cell.no_workspace_objects.filter(
        row=link.snapshot_row, value="original", status=CellStatus.PASS.value
    ).count() == 1
    assert not _run(world).metadata[_RUN_KEY].get("http_recording_error")


@pytest.mark.django_db(transaction=True)
def test_http_late_precheck_error_preserves_committed_evaluation(
    tenants, evaluator_result, monkeypatch
):
    world = _stage(tenants, evaluated=True)
    for link in world.links:
        _dispatch(world, link)
    link = world.links[0]
    runner, _score, _reason = _evaluator(world)
    calls = []
    committed = {}

    def check_usage(*_args):
        calls.append(True)
        if len(calls) > 1:
            return SimpleNamespace(allowed=True)
        # Another delivery completes after the initial receipt lookup but
        # before this invocation reports a precheck failure.
        _batch(world, [link])
        committed.update(_evidence(world))
        return SimpleNamespace(
            allowed=False,
            reason="Evaluation unavailable",
            error_code="USAGE_LIMIT_EXCEEDED",
            dimension=None,
            current_usage=0,
            limit=0,
            upgrade_cta=None,
        )

    monkeypatch.setattr(
        sys.modules["ee.usage.services.metering"], "check_usage", check_usage
    )
    with patch.object(
        EvaluationRunner,
        "_process_eval_result",
        return_value=({"reason": "original"}, CellStatus.PASS.value, "1"),
    ):
        with pytest.raises(ValueError, match="Evaluation unavailable"):
            runner.run_prompt(row_ids=[str(link.snapshot_row_id)])
    assert len(calls) == 2
    assert _evidence(world) == committed


@pytest.mark.django_db(transaction=True)
def test_http_evaluator_receipts_do_not_skip_a_new_admission(tenants, evaluator_result):
    world = _stage(tenants, evaluated=True)
    calls = []

    def evaluate(runner, row, _mapping):
        calls.append((runner.source_configs["http_context"]["admission_id"], row.pk))
        response = {"reason": "checked"}
        reason_column = runner._create_reason_column(runner.dataset, "reason")
        assert reason_column is not None
        runner._create_reason_cell(
            runner.dataset,
            reason_column,
            row,
            response,
            response["reason"],
            CellStatus.PASS.value,
        )
        return response, CellStatus.PASS.value, "1"

    with patch.object(
        EvaluationRunner, "_process_eval_result", autospec=True, side_effect=evaluate
    ):
        for link in world.links:
            _dispatch(world, link)
        _batch(world, world.links)
        _finish(world)
        previous = _run(world)
        previous_items = _items(world, world.links)
        old_admission = world.admission
        _stage_again(world)
        _accept(world)
        assert world.admission != old_admission
        for link in world.links:
            _dispatch(world, link)
        _batch(world, world.links)
        _finish(world)
    assert len(calls) == 2 * len(world.links)
    assert {admission for admission, _row in calls} == {old_admission, world.admission}
    assert _run(world).pk != previous.pk
    assert list(
        ExperimentRunItem.no_workspace_objects.filter(run=previous)
        .order_by("id")
        .values()
    ) == previous_items


def test_http_results_catalog_preserves_bound_source_identity(tenants):
    world = _stage(tenants)
    run = _complete(world)
    catalog = _body(
        world.actor["client"].get(
            reverse("dataset-results-runs", kwargs={"dataset_id": world.dataset.pk}),
            {"experiment_id": str(world.experiment.pk), "include": "config"},
        )
    )
    assert len(catalog["results"]) == 1
    header = catalog["results"][0]
    assert header["run_id"] == str(run.pk)
    assert header["source"] == "http"
    assert header["task_kind"] == "external"
    assert header["kind"] == "execution"
    assert header["config"] == {}
    assert header["native_config_id"] == str(world.source.pk)
    assert header["output_column_id"] == str(world.output.pk)
    assert header.get("prompt_config_id") is None


def test_http_history_survives_save_only_evaluator_template_replacement(tenants):
    world = _stage(tenants, evaluated=True)
    for link in world.links:
        _dispatch(world, link)
        _evaluate(world, link, 1)
    _finish(world)
    run = _run(world)
    evidence = _evidence(world)
    replacement = EvalTemplate.no_workspace_objects.create(
        name="replacement-endpoint-check",
        organization=world.actor["org"],
        workspace=world.actor["workspace"],
        owner=OwnerChoices.USER.value,
        criteria="Check {{output}}",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    EvalTemplateVersion.objects.create(
        eval_template=replacement,
        version_number=1,
        is_default=True,
        organization=world.actor["org"],
        workspace=world.actor["workspace"],
        output_type_normalized="pass_fail",
    )
    client = world.actor["client"]
    url = f"/model-hub/experiments/v2/{world.experiment.pk}/"
    config = dict(world.metric.config)
    config.pop("version_pin", None)
    with patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start:
        _body(
            client.put(
                url,
                {
                    "user_eval_metrics": [
                        {
                            "id": str(world.metric.pk),
                            "template_id": str(replacement.pk),
                            "name": world.metric.name,
                            "config": config,
                        }
                    ]
                },
                format="json",
            )
        )
    start.assert_not_called()
    world.metric.refresh_from_db()
    assert world.metric.template_id == replacement.pk
    assert world.experiment.user_eval_template_ids.get().pk == world.metric.pk
    catalog = _body(
        client.get(
            reverse("dataset-results-runs", kwargs={"dataset_id": world.dataset.pk}),
            {"experiment_id": str(world.experiment.pk)},
        )
    )
    assert [item["run_id"] for item in catalog["results"]] == [str(run.pk)]
    detail = _body(client.get(url))
    assert [item["run_id"] for item in detail["runs"]] == [str(run.pk)]
    for link in world.links:
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
        assert [item["run_id"] for item in rows["runs"]] == [str(run.pk)]
    assert _evidence(world) == evidence
    assert run.evaluator_versions[0]["evaluator_version_id"] == str(world.version.pk)


def test_http_unbound_rerun_keeps_classic_results_entry_point(tenants):
    world = _stage(tenants)
    previous = _complete(world)
    previous_admission = world.admission
    assert opens_in_full_results(world.experiment)
    with patch.object(
        recording, "bind_http_run", side_effect=RunWriteError("Binding unavailable")
    ):
        _stage_again(world)
    _accept(world)
    assert world.admission != previous_admission
    assert not NativeRunBinding.objects.filter(
        variant=world.source, execution_id=world.admission
    ).exists()

    def assert_classic_entry_point():
        assert not opens_in_full_results(world.experiment)
        annotated = with_full_results(
            ExperimentsTable.no_workspace_objects.filter(pk=world.experiment.pk)
        ).get()
        assert not opens_in_full_results(annotated)
        detail = _body(
            world.actor["client"].get(
                f"/model-hub/experiments/v2/{world.experiment.pk}/"
            )
        )
        assert detail["opens_in_full_results"] is False
        assert [item["run_id"] for item in detail["runs"]] == [str(previous.pk)]

    assert_classic_entry_point()
    for link in world.links:
        _dispatch(world, link)
    _finish(world)
    assert_classic_entry_point()
    assert NativeRunBinding.objects.filter(variant=world.source).count() == 1
    for link in world.links:
        cell = Cell.no_workspace_objects.get(row=link.snapshot_row, column=world.output)
        assert json.loads(cell.value_infos)["http_attempt_id"].startswith(
            world.admission + ":"
        )


@pytest.mark.parametrize("failure", ["versionless", "binding_validation"])
def test_http_create_keeps_classic_admission_when_binding_is_unavailable(
    tenants, caplog, failure
):
    actor = tenants["a"]
    dataset = actor["exp"]["dataset"]
    evaluator = EvalTemplate.no_workspace_objects.create(
        name="endpoint-check",
        organization=actor["org"],
        workspace=actor["workspace"],
        owner=OwnerChoices.USER.value,
        criteria="Evaluate {{output}}",
        config={"output": "score", "eval_type_id": "Equals"},
    )
    if failure == "binding_validation":
        EvalTemplateVersion.objects.create(
            eval_template=evaluator,
            version_number=1,
            is_default=True,
            organization=actor["org"],
            workspace=actor["workspace"],
            output_type_normalized="pass_fail",
        )
    source = entry()
    source["variable_mapping"] = {"question": str(dataset.column_set.get().pk)}
    body = {
        "name": "Endpoint classic fallback",
        "dataset_id": str(dataset.pk),
        "prompt_config": [source],
        "user_eval_metrics": [
            {
                "template_id": str(evaluator.pk),
                "name": "Endpoint check",
                "config": {"mapping": {"output": "output"}},
            }
        ],
    }
    bind = recording.bind_http_run

    def bind_then_refuse(*args, **kwargs):
        run = bind(*args, **kwargs)
        assert run is not None
        raise RunWriteError("Binding validation refused")

    with (
        patch(
            "tfc.temporal.experiments.start_experiment_v2_workflow",
            return_value="endpoint-classic-workflow",
        ) as start,
        patch.object(
            recording,
            "bind_http_run",
            side_effect=bind_then_refuse if failure == "binding_validation" else bind,
        ),
    ):
        _body(
            actor["client"].post("/model-hub/experiments/v2/", body, format="json")
        )
    start.assert_called_once()
    experiment = ExperimentsTable.no_workspace_objects.get(
        dataset=dataset, name=body["name"]
    )
    state = state_of(experiment)
    assert state["state"] == "accepted"
    assert not ExperimentRun.no_workspace_objects.filter(
        experiment=experiment
    ).exists()
    assert not NativeRunBinding.objects.filter(snapshot__experiment=experiment).exists()
    assert "HTTP classic execution retained; canonical bind failed" in caplog.text
    if failure == "versionless":
        assert "evaluator_version_unavailable" in caplog.text
    assert experiment.user_eval_template_ids.count() == 1
    source = experiment.experiment_datasets.get(
        legacy_prompt_config__target_type="http"
    )
    assert source.legacy_prompt_config["_http_execution"]["admission_id"] == (
        state["admission_id"]
    )
    # The canonical refusal did not remove the runnable classic source.
    from model_hub.views.experiment_runner import ExperimentRunner

    runner = ExperimentRunner(experiment.pk)
    runner.load_experiment()
    assert http.prepare_http_experiment(runner, state["admission_id"])
    plan = http.setup_http_source(
        str(experiment.pk), str(source.pk), state["admission_id"]
    )
    with patch.object(outbound_destination, "request", return_value=_reply("first")):
        result = runner.process_http_row(
            source_id=str(source.pk),
            row_id=plan["row_ids"][0],
            admission_id=state["admission_id"],
        )
    assert result["status"] == "COMPLETED"
    cell = Cell.no_workspace_objects.get(
        column_id=plan["column_id"], row_id=plan["row_ids"][0]
    )
    assert cell.status == CellStatus.PASS.value and cell.value == "answer:first"
    assert not ExperimentRun.no_workspace_objects.filter(
        experiment=experiment
    ).exists()


def test_http_superseding_admission_ends_previous_evidence(tenants):
    world = _stage(tenants)
    _dispatch(world, world.links[0])
    previous = _run(world)
    assert previous.completed_at is None
    preserved_items = _items(world, [world.links[0]])
    # HTTP admission owns replacement; optional evidence is not an edit guard.
    ensure_native_idle(world.experiment)
    _stage_again(world)
    previous.refresh_from_db()
    assert previous.status == "cancelled" and previous.completed_at is not None
    assert _run(world).pk != previous.pk
    assert _run(world).status == "draft"
    assert list(
        ExperimentRunItem.no_workspace_objects.filter(run=previous)
        .order_by("id")
        .values()
    ) == preserved_items
    _accept(world)
    _complete(world)
    previous.refresh_from_db()
    assert previous.status == "cancelled"


def test_http_stop_callback_releases_current_admission_for_rerun(tenants):
    world = _stage(tenants)
    previous = _run(world)
    with patch.object(activities, "close_old_connections"):
        result = activities._http_execution_control_sync(
            str(world.experiment.pk), world.admission, "stop"
        )
    assert result["status"] == "COMPLETED"
    world.experiment.refresh_from_db()
    assert world.experiment.status == StatusType.CANCELLED.value
    with transaction.atomic():
        admissions = stage_rerun_batch(
            [world.experiment], {"max_concurrent_rows": 1}, world.actor["workspace"]
        )
        world.admission = admissions[str(world.experiment.pk)]
        http.bind_http_submission(world.experiment, world.admission, retry=True)
    previous.refresh_from_db()
    assert previous.status == "cancelled" and previous.completed_at is not None
    _accept(world)
    assert _run(world).pk != previous.pk


@pytest.mark.parametrize("operation", ["claim", "output"])
def test_http_canonical_write_failure_preserves_classic_output(tenants, operation):
    world = _stage(tenants)
    function_name = "claim_http_item" if operation == "claim" else "record_http_output"
    original = getattr(recording, function_name)

    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise IntegrityError("Canonical storage failure")

    with patch.object(recording, function_name, side_effect=write_then_fail):
        assert _dispatch(world, world.links[0])["status"] == "COMPLETED"
    cell = Cell.no_workspace_objects.get(
        row=world.links[0].snapshot_row, column=world.output
    )
    assert cell.value == "answer:first" and cell.status == CellStatus.PASS.value
    run = _run(world)
    assert run.completed_at is None
    assert run.metadata[_RUN_KEY]["http_recording_error"]
    failed_items = ExperimentRunItem.no_workspace_objects.filter(
        run=run, example_id=world.links[0].version_item.example_id
    )
    if operation == "claim":
        assert not failed_items.exists()
    else:
        item = failed_items.get()
        assert item.status == "running"
        assert "output" not in item.metadata[_RUN_KEY]["http"]
    _dispatch(world, world.links[1])
    classic = _cells(world)
    _finish(world)
    assert _cells(world) == classic
    run = _run(world)
    assert run.status == "failed" and run.completed_at is not None
    assert not ExperimentRunItem.no_workspace_objects.filter(
        run=run, status__in=["pending", "running"]
    ).exists()


def test_http_canonical_evaluation_failure_preserves_score_and_reason(tenants):
    world = _stage(tenants, evaluated=True)
    for link in world.links:
        _dispatch(world, link)
    original = recording.record_http_evaluation

    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise IntegrityError("Canonical storage failure")

    with patch.object(recording, "record_http_evaluation", side_effect=write_then_fail):
        score, reason = _evaluate(world, world.links[0], 1)
    for column, expected in ((score, "1"), (reason, "checked")):
        cell = Cell.no_workspace_objects.get(
            row=world.links[0].snapshot_row, column=column
        )
        assert cell.value == expected and cell.status == CellStatus.PASS.value
    item = ExperimentRunItem.no_workspace_objects.get(
        run=_run(world), example_id=world.links[0].version_item.example_id
    )
    assert item.metadata[_RUN_KEY]["http"]["evaluations"] == {}
    assert item.status == "running"
    _evaluate(world, world.links[1], 0)
    classic = _cells(world)
    _finish(world)
    assert _cells(world) == classic
    assert _run(world).status == "failed" and _run(world).completed_at is not None


def test_http_canonical_finalize_failure_keeps_classic_completion(tenants):
    world = _stage(tenants)
    for link in world.links:
        _dispatch(world, link)
    classic = _cells(world)
    with patch.object(recording, "finalize_run", side_effect=IntegrityError):
        _finish(world)
    assert _cells(world) == classic
    run = _run(world)
    assert run.status == "failed" and run.completed_at is not None
    assert run.metadata[_RUN_KEY]["http_recording_error"]["operation"] == "finish"


def test_http_row_runs_serve_endpoint_header_and_trial_trace(tenants):
    world = _stage(tenants)
    for link in world.links:
        _dispatch(world, link, traced=True)
    _finish(world)
    result = _body(
        world.actor["client"].get(
            reverse(
                "dataset-row-runs",
                kwargs={
                    "dataset_id": world.dataset.pk,
                    "row_id": world.links[0].source_row_id,
                },
            )
        )
    )
    header = result["runs"][0]
    assert header["source"] == header["target_type"] == "http"
    assert header["task_kind"] == "external"
    assert header["native_config_id"] == str(world.source.pk)
    assert header["output_column_id"] == str(world.output.pk)
    question = world.inputs[str(world.links[0].source_row_id)]
    assert header["trials_detail"] == [
        {
            "trial_number": 1,
            "status": "completed",
            "trace_id": header["trace_id"],
            "output": f"answer:{question}",
            "error": None,
            "scores": {},
        }
    ]


def test_http_retry_selects_snapshot_rows_from_canonical_run_cells(tenants):
    world = _stage(tenants)
    for link in world.links:
        _dispatch(world, link)
    _finish(world)
    run = _run(world)
    link = world.links[0]
    cell = {
        "column_id": str(world.output.pk),
        "run_id": str(run.pk),
        "example_id": str(link.version_item.example_id),
    }
    assert http.select_http_retry(world.experiment, {"cells": [cell]}) == {
        str(world.source.pk): [str(link.snapshot_row_id)]
    }
    foreign = {**cell, "example_id": str(uuid4())}
    with pytest.raises(ValueError, match="Results cell not found"):
        http.select_http_retry(world.experiment, {"cells": [foreign]})
