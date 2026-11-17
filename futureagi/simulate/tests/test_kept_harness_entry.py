"""A harness verdict kept under a re-grade's placeholder comes back on every
path that ends the cell without a fresh platform verdict.

The pure helpers are checked on their own; every writer is then driven
through the code that calls it in production, against Postgres, with the
evaluator mocked at its import boundary so no model is called.
"""

import copy
import uuid
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios, SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services.harness_run_evals import (
    EVAL_QUEUE_STAMP_WINDOW,
    EVAL_QUEUED_KEY,
    queue_eval_for_finished_calls,
)
from simulate.services.kept_harness_entry import (
    KEPT_KEY,
    end_without_verdict,
    pending_placeholder,
)
from simulate.services.scoring_status import (
    EVAL_PROGRESS_KEY,
    REASON_EVAL_RUN_CANCELLED,
    REASON_EVAL_TIMED_OUT_RUNNING,
    ScoringInput,
    close_due_scoring,
    derive_call_scoring,
    normalize_eval_entry,
    pending_eval_ids,
)
from simulate.services.test_executor import (
    TestExecutor,
    _run_simulate_evaluations_task,
    run_new_evals_on_call_executions_task,
)

_JOB = timedelta(minutes=10)
_START = timedelta(minutes=30)
_SUCCESS = {"output": "8", "reason": "ok", "output_type": "score"}
_NO_SKIP = SimpleNamespace(processing_skipped=False, processing_skip_reason="")
_NO_AGENT_TURN = SimpleNamespace(
    processing_skipped=True,
    processing_skip_reason=(
        "We could not find enough agent conversation to process this call."
    ),
)
_NO_TRANSCRIPT_REASON = "Call transcript is unavailable, so processing was skipped."
_CODE_CHECK_ID = str(uuid.uuid4())
_TRANSCRIPT = {
    "transcript": "Hello. Yes, order 123 shipped.",
    "voice_recording": "s3://bucket/rec.mp3",
    "assistant_recording": "s3://bucket/asst.mp3",
    "customer_recording": "s3://bucket/cust.mp3",
    "stereo_recording": "s3://bucket/stereo.mp3",
    "user_chat_transcript": "",
    "assistant_chat_transcript": "",
}


def _harness_verdict(name):
    """A suite eval's verdict as harness ingestion stores it."""
    return {
        "name": name,
        "output": "Failed",
        "output_type": "Pass/Fail",
        "reason": "The agent never confirmed the order.",
        "status": "completed",
        "source": "harness",
        "kind": "checkpoint",
        "platform_template": "task_completion",
    }


def _code_check():
    """A harness code check: no config of its own, never graded again."""
    return {
        "name": "Greets the caller",
        "output": "Passed",
        "output_type": "Pass/Fail",
        "reason": "",
        "status": "completed",
        "source": "harness",
        "kind": "code",
        "platform_template": "",
    }


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Kept Entry Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+15551239876",
        inbound=True,
        description="Agent under test for kept harness entries",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Kept Entry Simulator",
        prompt="You are a customer.",
        voice_provider="livekit",
        voice_name="alk-simulator",
        model="gpt-4o",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, simulator_agent):
    rt = RunTest.objects.create(
        name="Kept Entry Run Test",
        description="Run for kept harness entries",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
        enable_tool_evaluation=False,
    )
    rt.scenarios.add(
        Scenarios.objects.create(
            name="Kept Entry Scenario",
            description="Scenario for kept harness entries",
            source="test",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            agent_definition=agent_definition,
            simulator_agent=simulator_agent,
            status=StatusType.COMPLETED.value,
        )
    )
    return rt


@pytest.fixture
def eval_template(db, organization):
    return EvalTemplate.objects.create(
        name="Kept Entry Eval",
        config={"required_keys": ["conversation"]},
        organization=organization,
    )


@pytest.fixture
def suite_template(db, organization):
    """A built-in suite eval: the harness fills it, the platform can grade it."""
    return EvalTemplate.objects.create(
        name=f"suite_{uuid.uuid4().hex[:6]}",
        config={"required_keys": ["conversation"], "prompt": "Grade it"},
        owner="system",
        organization=organization,
    )


@pytest.fixture
def test_execution(db, run_test):
    return _run(run_test, TestExecution.ExecutionStatus.EVALUATING)


@pytest.fixture
def call_execution(db, test_execution):
    return _call(test_execution)


def _run(run_test, status):
    return TestExecution.objects.create(
        run_test=run_test,
        status=status,
        total_scenarios=1,
        total_calls=1,
        agent_definition=run_test.agent_definition,
        simulator_agent=run_test.simulator_agent,
    )


def _call(execution, *, metadata=None, eval_outputs=None, **fields):
    return CallExecution.objects.create(
        test_execution=execution,
        scenario=execution.run_test.scenarios.first(),
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        duration_seconds=120,
        call_metadata=metadata if metadata is not None else {},
        eval_outputs=eval_outputs if eval_outputs is not None else {},
        **fields,
    )


def _config(run_test, template, *, mapping):
    return SimulateEvalConfig.objects.create(
        name=f"Eval {uuid.uuid4().hex[:6]}",
        eval_template=template,
        run_test=run_test,
        mapping=mapping,
        config={},
    )


def _awaiting_regrade(call_execution, cells, **metadata):
    """The state a re-grade request leaves before its grader runs."""
    call_execution.eval_outputs = {_CODE_CHECK_ID: _code_check(), **cells}
    call_execution.call_metadata = {
        **(call_execution.call_metadata or {}),
        "eval_started": True,
        "eval_completed": False,
        **metadata,
    }
    call_execution.save(update_fields=["eval_outputs", "call_metadata"])


def _grade(call_execution, configs):
    """Run the grader for this call and these configs, as a re-grade does."""
    TestExecutor(initialize_voice_service=False)._run_simulate_evaluations(
        call_execution,
        eval_config_ids=[str(config.id) for config in configs],
        skip_existing=False,
    )


def _grade_one(eval_config, call_execution):
    return TestExecutor(initialize_voice_service=False)._run_single_simulate_evaluation(
        eval_config, call_execution, dict(_TRANSCRIPT)
    )


# --- the helpers ------------------------------------------------------------


@pytest.mark.parametrize(
    ("previous", "keeps"),
    [
        (_harness_verdict("Task completion"), True),
        ({"name": "Platform", "output": "Passed", "status": "Completed"}, False),
        (None, False),
        ("Passed", False),
        (["Passed"], False),
        (7, False),
    ],
    ids=["harness", "platform", "missing", "str", "list", "int"],
)
def test_a_placeholder_keeps_only_a_harness_entry(previous, keeps):
    before = copy.deepcopy(previous)

    placeholder = pending_placeholder(previous)

    if keeps:
        assert placeholder == {"status": "pending", KEPT_KEY: before}
        assert placeholder[KEPT_KEY] is previous
    else:
        assert placeholder == {"status": "pending"}
    assert previous == before


def test_a_placeholder_carries_a_kept_entry_forward():
    harness = _harness_verdict("Task completion")

    second = pending_placeholder(pending_placeholder(harness))

    assert second == {"status": "pending", KEPT_KEY: harness}
    assert second[KEPT_KEY] is harness
    assert pending_placeholder({"status": "pending"}) == {"status": "pending"}
    assert pending_placeholder({"status": "pending", KEPT_KEY: "x"}) == {
        "status": "pending"
    }


def test_end_without_verdict_puts_back_only_a_kept_entry():
    harness = _harness_verdict("Task completion")
    replacement = {"status": "skipped", "skipped": True}
    kept = pending_placeholder(harness)
    before = copy.deepcopy(kept)

    assert end_without_verdict(kept, replacement) is harness
    assert kept == before
    assert replacement == {"status": "skipped", "skipped": True}
    for current in (
        {"status": "pending"},
        {"status": "completed", KEPT_KEY: harness},
        {"status": "pending", KEPT_KEY: None},
        {"status": "pending", KEPT_KEY: ["x"]},
    ):
        assert end_without_verdict(current, replacement) is replacement


@pytest.mark.parametrize(
    "current",
    [None, [], [{"status": "pending"}], "pending", 7],
    ids=["none", "empty-list", "list", "str", "int"],
)
def test_end_without_verdict_never_raises(current):
    replacement = {"status": "timed_out"}
    before = copy.deepcopy(current)

    assert end_without_verdict(current, replacement) is replacement
    assert current == before


def test_a_kept_entry_reads_as_pending():
    config_id = str(uuid.uuid4())
    now = timezone.now()
    cell = pending_placeholder(_harness_verdict("Task completion"))
    outputs = {config_id: cell}
    metadata = {
        "eval_started": True,
        "eval_completed": False,
        EVAL_QUEUED_KEY: {config_id: now.isoformat()},
    }

    assert normalize_eval_entry(cell) == "pending"
    assert pending_eval_ids(metadata, outputs) == {config_id}
    scoring = derive_call_scoring(
        ScoringInput(
            call_status="completed",
            metadata=metadata,
            eval_entries=outputs,
            csat_scored=False,
            has_csat_value=False,
            anchor=now,
        ),
        visible_ids={config_id},
        runnable_ids={config_id},
        run_status="evaluating",
        now=now,
    )
    assert scoring.status == "pending"
    assert scoring.evals[config_id].status == "pending"


# --- add-eval ---------------------------------------------------------------


@pytest.mark.django_db
def test_add_eval_over_a_kept_placeholder_keeps_it(run_test, eval_template):
    execution = _run(run_test, TestExecution.ExecutionStatus.COMPLETED)
    config = _config(run_test, eval_template, mapping={"conversation": "transcript"})
    config_id = str(config.id)
    harness = _harness_verdict(config.name)
    stale = timezone.now() - EVAL_QUEUE_STAMP_WINDOW - timedelta(minutes=1)
    call = _call(
        execution,
        metadata={
            "eval_started": True,
            "eval_completed": True,
            EVAL_QUEUED_KEY: {config_id: stale.isoformat()},
        },
        eval_outputs={
            config_id: pending_placeholder(harness),
            _CODE_CHECK_ID: _code_check(),
        },
    )

    assert queue_eval_for_finished_calls(execution, config)["queued"] == 1

    call.refresh_from_db()
    assert call.eval_outputs == {
        config_id: {"status": "pending", KEPT_KEY: _harness_verdict(config.name)},
        _CODE_CHECK_ID: _code_check(),
    }


# --- the grader's writers ---------------------------------------------------


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch("simulate.services.test_executor.run_eval_func")
@pytest.mark.parametrize("route", ["no-agent-turn", "no-transcript"])
def test_a_skipped_regrade_puts_the_harness_verdict_back(
    mock_run, mock_transcript, route, run_test, call_execution, suite_template
):
    suite = _config(run_test, suite_template, mapping={})
    other = _config(run_test, suite_template, mapping={})
    _awaiting_regrade(
        call_execution,
        {
            str(suite.id): pending_placeholder(_harness_verdict(suite.name)),
            str(other.id): {"status": "pending"},
        },
    )
    if route == "no-agent-turn":
        decision, reason = _NO_AGENT_TURN, _NO_AGENT_TURN.processing_skip_reason
        mock_transcript.return_value = dict(_TRANSCRIPT)
    else:
        decision, reason = _NO_SKIP, _NO_TRANSCRIPT_REASON
        mock_transcript.return_value = {**_TRANSCRIPT, "transcript": ""}

    with patch(
        "simulate.services.test_executor.decide_processing_skip",
        lambda **_: decision,
    ):
        _grade(call_execution, [suite, other])

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(suite.id)] == _harness_verdict(suite.name)
    assert call_execution.eval_outputs[str(other.id)]["status"] == "skipped"
    assert call_execution.eval_outputs[str(other.id)]["reason"] == reason
    assert call_execution.eval_outputs[_CODE_CHECK_ID] == _code_check()
    assert call_execution.call_metadata["processing_skipped"] is True
    assert call_execution.call_metadata["eval_completed"] is True
    mock_run.assert_not_called()


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
def test_grading_that_never_started_puts_the_harness_verdict_back(
    run_test, call_execution, suite_template
):
    suite = _config(run_test, suite_template, mapping={})
    other = _config(run_test, suite_template, mapping={})
    _awaiting_regrade(
        call_execution,
        {
            str(suite.id): pending_placeholder(_harness_verdict(suite.name)),
            str(other.id): {"status": "pending"},
        },
    )

    with patch(
        "simulate.services.test_executor._run_simulate_evaluations_task.apply_async",
        side_effect=ConnectionError("temporal unavailable"),
    ):
        run_new_evals_on_call_executions_task._original_func(
            [str(call_execution.id)], [str(suite.id), str(other.id)]
        )

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(suite.id)] == _harness_verdict(suite.name)
    assert call_execution.eval_outputs[str(other.id)]["reason"] == (
        "Grading could not be started."
    )
    assert call_execution.eval_outputs[_CODE_CHECK_ID] == _code_check()
    assert call_execution.call_metadata["eval_completed"] is True


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _NO_SKIP)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch("simulate.services.test_executor.run_eval_func")
def test_a_column_left_to_the_harness_puts_its_verdict_back(
    mock_run, mock_transcript, run_test, call_execution, organization
):
    mock_transcript.return_value = dict(_TRANSCRIPT)
    claim_template = EvalTemplate.objects.create(
        name=f"claim_{uuid.uuid4().hex[:6]}",
        config={"required_keys": ["conversation"], "prompt": "Grade it"},
        owner="user",
        organization=organization,
    )
    column = _config(run_test, claim_template, mapping={})
    # Sent through the harness SDK: the call carries the harness's checks.
    call_execution.call_metadata = {"harness_evaluations": []}
    _awaiting_regrade(
        call_execution,
        {str(column.id): pending_placeholder(_harness_verdict(column.name))},
    )

    _grade(call_execution, [column])

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(column.id)] == _harness_verdict(column.name)
    assert call_execution.eval_outputs[_CODE_CHECK_ID] == _code_check()
    mock_run.assert_not_called()


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.run_eval_func")
@pytest.mark.parametrize(
    "failure", ["column-mismatch", "no-result", "exception", "raise-after-verdict"]
)
def test_a_failed_regrade_puts_the_harness_verdict_back(
    mock_run, failure, run_test, call_execution, eval_template
):
    # A column id no scenario of the test holds is the column mismatch.
    source = str(uuid.uuid4()) if failure == "column-mismatch" else "transcript"
    config = _config(run_test, eval_template, mapping={"conversation": source})
    key = str(config.id)
    _awaiting_regrade(
        call_execution, {key: pending_placeholder(_harness_verdict(config.name))}
    )
    mock_run.return_value = None if failure == "no-result" else dict(_SUCCESS)
    if failure == "exception":
        mock_run.side_effect = RuntimeError("judge unavailable")
    localizer = (
        patch(
            "model_hub.services.error_localizer_service.error_localizer_enabled",
            side_effect=RuntimeError("localizer unavailable"),
        )
        if failure == "raise-after-verdict"
        else nullcontext()
    )
    raised = nullcontext() if failure == "no-result" else pytest.raises(Exception)

    with localizer, raised:
        _grade_one(config, call_execution)

    call_execution.refresh_from_db()
    cell = call_execution.eval_outputs[key]
    if failure == "raise-after-verdict":
        assert cell["status"] == StatusType.FAILED.value
        assert cell["error"] == "error"
        assert "source" not in cell and KEPT_KEY not in cell
    else:
        assert cell == _harness_verdict(config.name)
    assert call_execution.eval_outputs[_CODE_CHECK_ID] == _code_check()


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.run_eval_func")
def test_a_first_grading_with_no_outputs_still_grades(
    mock_run, run_test, call_execution, eval_template
):
    mock_run.return_value = dict(_SUCCESS)
    config = _config(run_test, eval_template, mapping={"conversation": "transcript"})
    CallExecution.objects.filter(id=call_execution.id).update(eval_outputs=None)
    call_execution.refresh_from_db()

    _grade_one(config, call_execution)

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs == {
        str(config.id): {
            "output": "8",
            "reason": "ok",
            "output_type": "score",
            "name": config.name,
            "status": StatusType.COMPLETED.value,
        }
    }


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.run_eval_func")
def test_a_fresh_verdict_replaces_the_harness_entry(
    mock_run, run_test, call_execution, eval_template
):
    mock_run.return_value = dict(_SUCCESS)
    config = _config(run_test, eval_template, mapping={"conversation": "transcript"})
    key = str(config.id)
    _awaiting_regrade(
        call_execution, {key: pending_placeholder(_harness_verdict(config.name))}
    )

    _grade_one(config, call_execution)

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[key] == {
        "output": "8",
        "reason": "ok",
        "output_type": "score",
        "name": config.name,
        "status": StatusType.COMPLETED.value,
    }
    assert call_execution.eval_outputs[_CODE_CHECK_ID] == _code_check()


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch("simulate.services.test_executor.decide_processing_skip", lambda **_: _NO_SKIP)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch("simulate.services.test_executor.run_eval_func")
def test_a_removed_evals_placeholder_puts_its_harness_verdict_back(
    mock_run, mock_transcript, run_test, call_execution, eval_template, suite_template
):
    mock_transcript.return_value = dict(_TRANSCRIPT)
    mock_run.return_value = dict(_SUCCESS)
    graded = _config(run_test, eval_template, mapping={"conversation": "transcript"})
    kept = _config(run_test, suite_template, mapping={})
    plain = _config(run_test, suite_template, mapping={})
    _awaiting_regrade(
        call_execution,
        {
            str(graded.id): {"status": "pending"},
            str(kept.id): pending_placeholder(_harness_verdict(kept.name)),
            str(plain.id): {"status": "pending"},
        },
    )
    SimulateEvalConfig.objects.filter(id__in=[kept.id, plain.id]).update(deleted=True)

    _run_simulate_evaluations_task._original_func(
        str(call_execution.id),
        eval_config_ids=[str(graded.id), str(kept.id), str(plain.id)],
        skip_existing=False,
    )

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(kept.id)] == _harness_verdict(kept.name)
    assert str(plain.id) not in call_execution.eval_outputs
    assert call_execution.eval_outputs[str(graded.id)]["output"] == "8"
    assert call_execution.eval_outputs[_CODE_CHECK_ID] == _code_check()


# --- the sweep --------------------------------------------------------------


def _locked(call_execution):
    """The call as the sweep hands it over: its run joined in."""
    return CallExecution.objects.select_related("test_execution").get(
        id=call_execution.id
    )


@pytest.mark.django_db
def test_the_sweep_puts_the_harness_verdict_back_on_time_out(
    run_test, call_execution, suite_template
):
    suite = _config(run_test, suite_template, mapping={})
    other = _config(run_test, suite_template, mapping={})
    now = timezone.now()
    _awaiting_regrade(
        call_execution,
        {
            str(suite.id): pending_placeholder(_harness_verdict(suite.name)),
            str(other.id): {"status": "pending"},
        },
        **{
            EVAL_QUEUED_KEY: {
                str(suite.id): (now - 2 * _JOB).isoformat(),
                str(other.id): (now - 2 * _JOB).isoformat(),
            },
            EVAL_PROGRESS_KEY: (now - _JOB).isoformat(),
        },
    )
    names = {str(suite.id): suite.name, str(other.id): other.name}

    assert close_due_scoring(_locked(call_execution), {}, now, names) == "timed_out"

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(suite.id)] == _harness_verdict(suite.name)
    assert call_execution.eval_outputs[str(other.id)] == {
        "output": None,
        "reason": REASON_EVAL_TIMED_OUT_RUNNING,
        "output_type": None,
        "name": other.name,
        "status": "timed_out",
        "timestamp": now.isoformat(),
    }
    assert call_execution.eval_outputs[_CODE_CHECK_ID] == _code_check()
    assert call_execution.call_metadata["eval_completed"] is True


@pytest.mark.django_db
def test_the_sweep_puts_the_harness_verdict_back_on_cancel(
    run_test, test_execution, call_execution, suite_template
):
    suite = _config(run_test, suite_template, mapping={})
    other = _config(run_test, suite_template, mapping={})
    now = timezone.now()
    _awaiting_regrade(
        call_execution,
        {
            str(suite.id): pending_placeholder(_harness_verdict(suite.name)),
            str(other.id): {"status": "pending"},
        },
        **{EVAL_QUEUED_KEY: {str(suite.id): now.isoformat()}},
    )
    test_execution.status = TestExecution.ExecutionStatus.CANCELLING
    test_execution.save(update_fields=["status"])
    names = {str(suite.id): suite.name, str(other.id): other.name}

    assert close_due_scoring(_locked(call_execution), {}, now, names) == (
        "skipped_cancelled"
    )

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(suite.id)] == _harness_verdict(suite.name)
    assert call_execution.eval_outputs[str(other.id)]["status"] == "skipped"
    assert call_execution.eval_outputs[str(other.id)]["reason"] == (
        REASON_EVAL_RUN_CANCELLED
    )
    assert call_execution.eval_outputs[str(other.id)]["timestamp"] == now.isoformat()
    assert call_execution.eval_outputs[_CODE_CHECK_ID] == _code_check()


@pytest.mark.django_db
def test_a_crashed_grader_leaves_the_harness_verdict_to_the_sweep(
    run_test, test_execution, call_execution, suite_template
):
    from simulate.tasks.chat_sim import _sweep_stuck_scoring_pass

    suite = _config(run_test, suite_template, mapping={})
    dispatched = timezone.now()
    # What the re-grade request writes; its grader then never runs.
    _awaiting_regrade(
        call_execution,
        {str(suite.id): pending_placeholder(_harness_verdict(suite.name))},
        **{
            EVAL_QUEUED_KEY: {str(suite.id): dispatched.isoformat()},
            "csat_status": "completed",
        },
    )
    CallExecution.objects.filter(id=call_execution.id).update(
        completed_at=dispatched - timedelta(hours=1)
    )

    counts = _sweep_stuck_scoring_pass(dispatched + _START)

    call_execution.refresh_from_db()
    test_execution.refresh_from_db()
    assert call_execution.eval_outputs == {
        str(suite.id): _harness_verdict(suite.name),
        _CODE_CHECK_ID: _code_check(),
    }
    assert call_execution.call_metadata["eval_completed"] is True
    assert counts["timed_out"] == 1
    assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED


def test_a_bare_harness_verdict_is_left_alone():
    # A failed dispatch puts the harness's verdict back as the cell itself; a
    # grader that started anyway must not end it with no verdict of its own.
    harness = _harness_verdict("Task completion")
    before = copy.deepcopy(harness)

    assert end_without_verdict(harness, {"status": "skipped"}) is harness
    assert harness == before


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch("simulate.services.test_executor.run_eval_func")
@pytest.mark.parametrize("ending", ["skipped", "failed"])
def test_a_grader_that_runs_after_a_failed_dispatch_keeps_the_harness_verdict(
    mock_run, mock_transcript, ending, run_test, call_execution, suite_template
):
    suite = _config(run_test, suite_template, mapping={})
    # What the dispatch failure's restore leaves: the bare harness verdict.
    _awaiting_regrade(call_execution, {str(suite.id): _harness_verdict(suite.name)})
    mock_transcript.return_value = dict(_TRANSCRIPT)
    decision = _NO_AGENT_TURN if ending == "skipped" else _NO_SKIP
    if ending == "failed":
        mock_run.side_effect = RuntimeError("judge unavailable")

    with patch(
        "simulate.services.test_executor.decide_processing_skip",
        lambda **_: decision,
    ):
        _grade(call_execution, [suite])

    call_execution.refresh_from_db()
    assert call_execution.eval_outputs[str(suite.id)] == _harness_verdict(suite.name)


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
@patch.object(TestExecutor, "_get_call_transcript_data")
@patch("simulate.services.test_executor.run_eval_func")
def test_a_cancelled_run_puts_the_kept_verdict_back(
    mock_run, mock_transcript, run_test, suite_template
):
    execution = _run(run_test, TestExecution.ExecutionStatus.CANCELLED)
    call = _call(execution)
    suite = _config(run_test, suite_template, mapping={})
    _awaiting_regrade(
        call, {str(suite.id): pending_placeholder(_harness_verdict(suite.name))}
    )
    mock_transcript.return_value = dict(_TRANSCRIPT)

    with patch(
        "simulate.services.test_executor.decide_processing_skip",
        lambda **_: _NO_SKIP,
    ):
        _grade(call, [suite])

    call.refresh_from_db()
    assert call.eval_outputs[str(suite.id)] == _harness_verdict(suite.name)
    assert call.eval_outputs[_CODE_CHECK_ID] == _code_check()
    mock_run.assert_not_called()


@pytest.mark.django_db
@patch("simulate.services.test_executor.close_old_connections", lambda: None)
def test_a_column_mismatch_never_saves_its_error_over_the_harness_verdict(
    run_test, call_execution, eval_template
):
    # A column id no scenario of the test holds is the column mismatch. It is
    # written at the mismatch and again in the error write it raises into:
    # every save in between must hold the harness's verdict, not the error.
    config = _config(
        run_test, eval_template, mapping={"conversation": str(uuid.uuid4())}
    )
    key = str(config.id)
    harness = _harness_verdict(config.name)
    _awaiting_regrade(call_execution, {key: pending_placeholder(harness)})
    saved = []
    original_save = CallExecution.save

    def recording_save(self, *args, **kwargs):
        saved.append(copy.deepcopy((self.eval_outputs or {}).get(key)))
        return original_save(self, *args, **kwargs)

    # The grader re-raises whatever its error write caught; the test needs only
    # that it raised, as the failure tests above do.
    raised = pytest.raises(Exception)
    with patch.object(CallExecution, "save", recording_save), raised:
        _grade_one(config, call_execution)

    assert saved, "the grader saved nothing"
    assert all(cell == harness for cell in saved), saved
