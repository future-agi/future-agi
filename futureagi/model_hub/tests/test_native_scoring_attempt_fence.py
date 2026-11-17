"""Native scoring redelivery keeps its durable owner and terminal evidence."""

# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connections, transaction

from model_hub.models.develop_dataset import Cell
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services import native_run_recording
from model_hub.services.experiment_run_writes import _RUN_KEY, RunWriteError
from model_hub.services.experiment_submit import admit_submission, stage_submission
from model_hub.tests.test_native_prompt_create_bridge import (
    PROVIDER,
    START,
    attach_executable_native_eval,
    complete_current,
    native_eval_transport,
    native_fixture_evaluator,  # noqa: F401 -- imported autouse fixture
    own_key_route,  # noqa: F401 -- imported autouse fixture
    setup,
)
from model_hub.tests.test_native_run_binding import (
    bind,
    native_world,  # noqa: F401 -- imported fixture
)
from model_hub.views import experiment_runner
from tfc.middleware.workspace_context import workspace_context

pytestmark = pytest.mark.django_db(transaction=True)


class WorkerInterrupted(BaseException):
    """Model worker loss without an evaluator exception becoming a score."""


@pytest.fixture
def scoring_case(native_world):
    world = native_world
    experiment = world["experiment"]
    experiment.refresh_from_db()
    with transaction.atomic():
        stage_submission(
            experiment,
            {},
            "create",
            authorized_workspace=world["dataset"].workspace,
        )
    with patch(START, return_value="producer-workflow"):
        admit_submission(str(experiment.pk))
    complete_current(experiment, "stored output")
    parent = NativeRunBinding.objects.get(snapshot__experiment=experiment).run
    before_parent = list(parent.items.order_by("id").values())
    before_output = list(
        Cell.no_workspace_objects.filter(column=world["output_column"])
        .order_by("id")
        .values()
    )
    metric, pin = attach_executable_native_eval(world)
    row_id = str(next(iter(world["row_map"].values())).pk)
    with transaction.atomic():
        state = stage_submission(
            experiment,
            {"eval_only": str(metric.pk)},
            "update",
            authorized_workspace=world["dataset"].workspace,
            subset_rows={str(world["config"].pk): [row_id]},
            native_eval_only=True,
            evaluator_metric_ids=[str(metric.pk)],
            rerun_prompt_config_ids=[],
            rerun_agent_config_ids=[],
            rerun_eval_template_ids=[str(metric.pk)],
        )
    with patch(START, return_value="scoring-workflow"):
        admit_submission(str(experiment.pk), state["admission_id"])
    context, prepared = setup(experiment)
    run = NativeRunBinding.objects.get(run_id=context["run_id"]).run
    assert prepared["row_ids"] == [row_id]
    assert run.task_kind == "prompt"
    assert run.metadata["native_identity"]["execution_mode"] == "eval_only"
    case = SimpleNamespace(
        world=world,
        context=context,
        run=run,
        row_id=row_id,
        score={
            "metric_id": str(metric.pk),
            "evaluator_version_id": str(pin.pk),
            "status": "assessed",
            "value": 0.75,
        },
    )
    yield case
    assert list(parent.items.order_by("id").values()) == before_parent
    assert (
        list(
            Cell.no_workspace_objects.filter(column=world["output_column"])
            .order_by("id")
            .values()
        )
        == before_output
    )


def _deliver(case):
    world = case.world
    return experiment_runner._process_native_prompt_row(
        case.row_id,
        str(world["output_column"].pk),
        str(world["snapshot"].pk),
        str(world["experiment"].pk),
        case.context,
    )


def _claim_args(case):
    return {
        "dataset": case.world["dataset"],
        "run_id": case.run.pk,
        "snapshot_row_id": case.row_id,
        "attempt_key": f"native:{case.context['execution_id']}:{case.row_id}:1",
    }


def _assert_terminal(case):
    assert case.run.items.count() == 1
    item = case.run.items.get()
    assert item.status == "completed"
    assert item.output == "stored output"
    assert item.score_refs == [case.score]
    receipts = [
        entry.metadata[_RUN_KEY].get("result_receipt", {}).get("receipt_key")
        for entry in case.run.items.all()
    ]
    assert receipts == [_claim_args(case)["attempt_key"]]
    return item


@pytest.mark.parametrize("interruption", [WorkerInterrupted, RuntimeError])
def test_interrupted_scoring_reconciles_retained_outcome_without_reinvocation(
    scoring_case,
    interruption,
):
    case = scoring_case
    retained = []
    real_record = native_run_recording.record_attempt

    def interrupt_receipt(**kwargs):
        # Retain the actual worker result, not a reconstruction from classic cells.
        retained.append(kwargs)
        raise interruption("terminal receipt not persisted")

    with (
        native_eval_transport(score=0.75) as (engine, _),
        patch(PROVIDER) as producer,
    ):
        with patch.object(
            native_run_recording,
            "record_attempt",
            side_effect=interrupt_receipt,
        ):
            with pytest.raises(interruption, match="terminal receipt not persisted"):
                _deliver(case)
        assert engine.run.call_count == 1
        assert len(retained) == 1
        item = case.run.items.get()
        assert item.status == "running"
        assert item.metadata[_RUN_KEY].get("result_receipt") is None
        before_retry = list(case.run.items.values())
        for _ in range(2):
            with pytest.raises(RunWriteError) as refused:
                _deliver(case)
            assert str(refused.value) == "native attempt is already in progress"
        assert engine.run.call_count == 1
        assert list(case.run.items.values()) == before_retry

        # Explicit reconciliation uses the retained outcome with its original
        # claim. A redelivery must never manufacture or recompute that outcome.
        reconciled = real_record(**retained[0])
        assert reconciled.pk == item.pk
        assert real_record(**retained[0]).pk == item.pk
        _assert_terminal(case)
        native_run_recording.finish_native_run(
            dataset=case.world["dataset"],
            run_id=case.run.pk,
        )
        terminal = list(case.run.items.values())
        assert _deliver(case) == {"status": "COMPLETED"}
        assert list(case.run.items.values()) == terminal
        assert engine.run.call_count == 1
        assert producer.call_count == 0


def test_unknown_scoring_outcome_stays_unresolved_on_redelivery(scoring_case):
    case = scoring_case
    with (
        native_eval_transport() as (engine, _),
        patch(PROVIDER) as producer,
    ):
        engine.run.side_effect = WorkerInterrupted("outcome unavailable")
        with pytest.raises(WorkerInterrupted, match="outcome unavailable"):
            _deliver(case)
        before = list(case.run.items.values())
        for _ in range(2):
            with pytest.raises(RunWriteError) as refused:
                _deliver(case)
            assert str(refused.value) == "native attempt is already in progress"
        assert engine.run.call_count == 1
        assert producer.call_count == 0
        assert list(case.run.items.values()) == before
        assert case.run.items.count() == 1
        item = case.run.items.get()
        assert item.status == "running"
        assert item.score_refs == []
        assert item.metadata[_RUN_KEY].get("result_receipt") is None


def test_overlapping_scoring_delivery_keeps_the_original_owner(scoring_case):
    case = scoring_case
    entered, release_owner = Event(), Event()

    def deliver():
        world = case.world
        try:
            with workspace_context(
                world["dataset"].workspace,
                organization=world["dataset"].organization,
                user=world["created_by"],
            ):
                return _deliver(case)
        except RunWriteError as exc:
            return exc
        finally:
            connections.close_all()

    with (
        native_eval_transport(score=0.75) as (engine, _),
        patch(PROVIDER) as producer,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        response = engine.run.return_value

        def evaluate(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                assert release_owner.wait(30) is True
            return response

        engine.run.side_effect = evaluate
        owner = pool.submit(deliver)
        try:
            assert entered.wait(30) is True
            duplicate = pool.submit(deliver).result(timeout=30)
        finally:
            release_owner.set()
        result = owner.result(timeout=30)
        assert engine.run.call_count == 1
        assert producer.call_count == 0
        assert type(duplicate) is RunWriteError
        assert str(duplicate) == "native attempt is already in progress"
        assert result == {"status": "COMPLETED"}
        _assert_terminal(case)


@pytest.mark.parametrize("sealed", [False, True])
def test_terminal_scoring_replay_preserves_the_receipt(scoring_case, sealed):
    case = scoring_case
    with (
        native_eval_transport(score=0.75) as (engine, _),
        patch(PROVIDER) as producer,
    ):
        assert _deliver(case) == {"status": "COMPLETED"}
        _assert_terminal(case)
        if sealed:
            native_run_recording.finish_native_run(
                dataset=case.world["dataset"],
                run_id=case.run.pk,
            )
        before = list(case.run.items.values())
        assert _deliver(case) == {"status": "COMPLETED"}
        assert list(case.run.items.values()) == before
        assert engine.run.call_count == 1
        assert producer.call_count == 0


@pytest.mark.parametrize("exclusive", [False, True])
def test_scoring_identity_requires_exclusive_claims(scoring_case, exclusive):
    case = scoring_case
    args = _claim_args(case)
    first = native_run_recording.start_attempt(**args, exclusive=exclusive)
    assert first.status == "running"
    before = list(case.run.items.values())
    with pytest.raises(RunWriteError) as refused:
        native_run_recording.start_attempt(**args, exclusive=exclusive)
    assert str(refused.value) == "native attempt is already in progress"
    assert list(case.run.items.values()) == before
    with pytest.raises(RunWriteError) as wrong_key:
        native_run_recording.start_attempt(**{**args, "attempt_key": "other-owner"})
    assert str(wrong_key.value) == "native attempt is already claimed with another key"


def test_prompt_execution_retains_nonexclusive_claim_replay(native_world):
    world = native_world
    run = bind(world)
    native_run_recording.mark_native_run(
        dataset=world["dataset"],
        run_id=run.pk,
        status="queued",
    )
    args = {
        "dataset": world["dataset"],
        "run_id": run.pk,
        "snapshot_row_id": next(iter(world["row_map"].values())).pk,
        "attempt_key": "prompt-attempt",
    }
    first = native_run_recording.start_attempt(**args)
    before = list(run.items.values())
    assert native_run_recording.start_attempt(**args).pk == first.pk
    assert list(run.items.values()) == before
    assert run.items.count() == 1
