"""The engine step: one typed question over the calls a person chose.

An ask is launched once, by a person, after the check before launch. It saves one
template per typed question the way Instant Eval does (its tag, its model), writes
one pending result row per call and question, and queues one job. The job grades
each row at most once, through the shared evaluator's entry (``run_eval_func``),
at most four at a time for one organisation, and writes only its result rows:
nothing on the call or the run. Only the decision model's network call is faked
here, so the evaluator, its size check, its log row and its billing are the real
ones.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import TestCase
from django.utils import timezone

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios, SimulateAsk, SimulateEvalResult
from simulate.models.hosted_harness import HostedHarnessJob
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services import ask_engine as engine
from simulate.services.ask_engine import (
    MAX_GRADING_AT_ONCE_PER_ORG,
    TEMPLATE_TAG,
    TYPED_QUESTION_MODEL,
    grade_row,
    launch_ask,
    result_counts,
    run_ask,
    typed_question,
)
from simulate.services.ask_selection import (
    LaunchRefused,
    SelectionKind,
    freeze_selection,
)
from simulate.services.call_evidence import Kind

STATE = SimulateEvalResult.State
LONG_TAIL_MARKER = "MARKER-AT-THE-VERY-END-7f3a"

# --- fixtures (copied, not imported, from the other simulate tests) ------------


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="You are a refunds agent.",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_version(db, agent_definition):
    version = agent_definition.create_version(
        description="You are a refunds agent. Never refund over 100.",
        commit_message="v1",
    )
    # The reader takes the instructions from the version's snapshot.
    version.configuration_snapshot = {
        **(version.configuration_snapshot or {}),
        "description": "You are a refunds agent. Never refund over 100.",
    }
    version.save(update_fields=["configuration_snapshot"])
    return version


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Simulator",
        prompt="You are a caller.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def scenario(db, organization, workspace, agent_definition):
    return Scenarios.objects.create(
        name="Refund",
        description="Refund.",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.GRAPH,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
    )


@pytest.fixture
def run(
    db,
    organization,
    workspace,
    agent_definition,
    agent_version,
    simulator_agent,
    scenario,
):
    run_test = RunTest.objects.create(
        name="Run",
        description="Run under test",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=2,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        agent_version=agent_version,
        scenario_ids=[str(scenario.id)],
    )


@pytest.fixture
def calls(run, scenario):
    return [
        CallExecution.objects.create(
            test_execution=run,
            scenario=scenario,
            phone_number=f"+1555000000{index}",
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            provider_call_data={"vapi": {"id": f"v{index}"}},
            eval_outputs={"own-eval": {"output": "Passed", "status": "completed"}},
            call_metadata={"eval_started": True, "eval_completed": True},
        )
        for index in range(2)
    ]


@pytest.fixture
def selection(organization, workspace, user, run, calls):
    return freeze_selection(
        kind=SelectionKind.RUNS,
        organization_id=organization.id,
        workspace_id=workspace.id,
        run_ids=[str(run.id)],
        note="Every call",
        asked_by_id=user.id,
    )


def _question(key="q1", kinds=(Kind.AGENT_INSTRUCTIONS,)):
    return typed_question(
        key,
        "Do the agent's instructions forbid refunds over 100?",
        kinds=kinds,
    )


def _answer(choice="Yes"):
    return {
        "model": "jev-1.14.0",
        "answers": {
            "q1": {
                "type": "choice",
                "choice": choice,
                "probabilities": {"Yes": 0.9, "No": 0.1}
                if choice == "Yes"
                else {"Yes": 0.1, "No": 0.9},
                "confidence": 0.8,
            }
        },
        "usage": {"input_tokens": 120, "output_tokens": 1},
    }


class FakeDecisionModel:
    """The decision model's network call, and only that. Records each request."""

    def __init__(self, answer=None, error=None):
        self.requests = []
        self.answer = answer or _answer()
        self.error = error

    def __call__(self, client, payload, timeout):
        self.requests.append(json.loads(json.dumps(payload)))
        if self.error is not None:
            raise self.error
        return self.answer


@pytest.fixture
def decision_model(settings):
    """A Jev answer for every request; the org's key, capability and credits pass."""
    settings.JEV_MAX_RETRIES = 0
    fake = FakeDecisionModel()
    with (
        patch(
            "ee.jev.client.JevSystemOneClient._request",
            autospec=True,
            side_effect=fake,
        ),
        patch(
            "evaluations.engine.instance._get_typesafe_api_key",
            return_value="fixture-org-key",
        ),
        patch("tfc.capabilities.service.check_or_raise"),
        # The shared evaluator's feedback lookup closes every database
        # connection when it is done, which ends the test's transaction.
        patch(
            "model_hub.views.eval_runner.EvaluationRunner.get_few_shot_examples",
            return_value=[],
        ),
    ):
        yield fake


@pytest.fixture
def gate_passes():
    """The check before launch's gate: capability, key and credits pass."""
    with (
        patch("simulate.services.ask_selection._check_capability"),
        patch("simulate.services.ask_selection._check_key"),
        patch("simulate.services.ask_selection._check_credits"),
        patch("simulate.services.ask_selection._credits", return_value=None),
    ):
        yield


@pytest.fixture
def queued():
    with patch.object(engine.run_ask, "apply_async") as apply_async:
        yield apply_async


def _launch(selection, organization, workspace, user, *questions):
    # The job is queued once the launch commits, so run what waits on the commit.
    with TestCase.captureOnCommitCallbacks(execute=True):
        return launch_ask(
            selection,
            list(questions) or [_question()],
            organization=organization,
            workspace_id=workspace.id,
            asked_by=user,
            accepted_credits=None,
        )


def _rows(ask):
    return list(SimulateEvalResult.objects.filter(ask=ask).order_by("created_at", "id"))


def _run_ask(ask):
    # The job's own function, without the queue wrapper: that wrapper closes the
    # database connection, which would end the test's transaction.
    run_ask._original_func(str(ask.id))


@pytest.fixture(autouse=True)
def _grade_on_one_thread(monkeypatch):
    # Threads open their own connections, which can't see the test's rows.
    monkeypatch.setattr(engine, "GRADING_THREADS", 1)


def _grade_all(ask):
    for row in _rows(ask):
        grade_row(str(row.id))


# --- launching --------------------------------------------------------------------


@pytest.mark.django_db
def test_launch_writes_one_pending_row_per_call_and_question_and_queues_one_job(
    organization, workspace, user, selection, calls, gate_passes, queued
):
    ask = _launch(
        selection,
        organization,
        workspace,
        user,
        _question("q1"),
        _question("q2", kinds=(Kind.AGENT_INSTRUCTIONS, Kind.SCENARIO)),
    )

    rows = _rows(ask)
    assert len(rows) == 4
    assert {(str(r.call_execution_id), r.question_key) for r in rows} == {
        (str(call.id), key) for call in calls for key in ("q1", "q2")
    }
    assert {r.state for r in rows} == {STATE.PENDING}
    assert {r.origin for r in rows} == {SimulateEvalResult.Origin.ASK}
    assert all(r.claimed_at is None for r in rows)
    assert {r.organization_id for r in rows} == {organization.id}
    assert {r.workspace_id for r in rows} == {workspace.id}
    assert ask.selection["call_ids"] == [str(call.id) for call in calls]
    assert ask.asked_by == user
    assert ask.status == SimulateAsk.Status.RUNNING
    queued.assert_called_once()
    assert queued.call_args.kwargs["args"] == (str(ask.id),)


@pytest.mark.django_db
def test_a_typed_question_is_saved_as_instant_eval_saves_one(
    organization, workspace, user, selection, gate_passes, queued
):
    """It appears in the organisation's eval library, as Instant Eval's do."""
    ask = _launch(selection, organization, workspace, user)

    (question,) = ask.questions
    template = EvalTemplate.objects.get(id=question["eval_template_id"])
    assert template.organization_id == organization.id
    assert template.eval_tags == [TEMPLATE_TAG] == ["instant-eval"]
    assert template.model == TYPED_QUESTION_MODEL == "jev-latest"
    assert template.visible_ui is True
    assert template.choices == ["Yes", "No"]
    assert template.choice_scores == {"Yes": 1, "No": 0}
    assert template.config["rule_prompt"] == question["criteria"]
    assert template.config["jev_mapping"]["question_type"] == "choice"
    # Every input is named in the criteria as sent.
    assert "{{agent_instructions}}" in question["criteria"]
    assert question["criteria"].startswith(
        "Do the agent's instructions forbid refunds over 100?"
    )


@pytest.mark.django_db
def test_a_launch_the_check_refuses_writes_nothing_and_queues_nothing(
    organization, workspace, user, selection, queued
):
    with (
        patch("simulate.services.ask_selection._check_capability"),
        patch(
            "simulate.services.ask_selection._check_key",
            side_effect=LaunchRefused("JEV_KEY_MISSING", "No key"),
        ),
        patch("simulate.services.ask_selection._credits", return_value=None),
        pytest.raises(LaunchRefused) as refused,
    ):
        _launch(selection, organization, workspace, user)

    assert refused.value.code == "JEV_KEY_MISSING"
    assert not SimulateAsk.objects.exists()
    assert not SimulateEvalResult.objects.filter(origin="ask").exists()
    assert not EvalTemplate.objects.filter(eval_tags=[TEMPLATE_TAG]).exists()
    queued.assert_not_called()


@pytest.mark.django_db
def test_a_launch_whose_cost_changed_since_it_was_shown_is_refused(
    organization, workspace, user, selection, queued
):
    with (
        patch("simulate.services.ask_selection._check_capability"),
        patch("simulate.services.ask_selection._check_key"),
        patch("simulate.services.ask_selection._check_credits"),
        patch("simulate.services.ask_selection._credits", return_value=4.0),
        pytest.raises(LaunchRefused) as refused,
    ):
        launch_ask(
            selection,
            [_question()],
            organization=organization,
            workspace_id=workspace.id,
            asked_by=user,
            accepted_credits=2.0,
        )

    assert refused.value.code == "cost_changed"
    assert not SimulateAsk.objects.exists()
    queued.assert_not_called()


@pytest.mark.django_db
def test_a_question_that_reads_audio_is_refused_for_a_decision_model():
    with pytest.raises(LaunchRefused) as refused:
        typed_question("q1", "Was the caller angry?", kinds=(Kind.AUDIO,))

    assert refused.value.code == "audio_unsupported"


@pytest.mark.django_db
@pytest.mark.parametrize("mark", ["{{ x }}", "{% if x %}", "{# note #}"])
def test_a_question_with_template_braces_is_refused(mark):
    # The question starts the template's instructions; its braces would be read
    # as variables or template code.
    with pytest.raises(LaunchRefused) as refused:
        typed_question("q1", f"Did the agent say {mark}?", kinds=(Kind.TRANSCRIPT,))

    assert refused.value.code == "question_invalid"


@pytest.mark.django_db
def test_a_queue_that_refuses_the_job_leaves_infrastructure_errors_not_fails(
    organization, workspace, user, selection, gate_passes
):
    """Pass line 7, second half."""
    with patch.object(
        engine.run_ask, "apply_async", side_effect=RuntimeError("queue down")
    ):
        ask = _launch(selection, organization, workspace, user)

    rows = _rows(ask)
    assert {r.state for r in rows} == {STATE.INFRASTRUCTURE_ERROR}
    assert {r.cause for r in rows} == {"queue_refused"}
    assert {r.label for r in rows} == {None}
    ask.refresh_from_db()
    assert ask.status == SimulateAsk.Status.FINISHED
    counts = result_counts(ask)["q1"]
    assert counts["infrastructure_error"] == 2
    assert counts["answered"] == 0
    assert counts["rate"] == {"answered": 0, "labels": {}}


# --- grading ----------------------------------------------------------------------


@pytest.mark.django_db
def test_a_grading_writes_the_answer_on_its_row_through_the_shared_evaluator(
    organization, workspace, user, selection, calls, gate_passes, queued, decision_model
):
    ask = _launch(selection, organization, workspace, user)

    _grade_all(ask)

    rows = _rows(ask)
    assert {r.state for r in rows} == {STATE.ANSWERED}
    assert {r.label for r in rows} == {"Yes"}
    assert {r.score for r in rows} == {1.0}
    assert {r.confidence for r in rows} == {0.8}
    assert all(r.reason for r in rows)
    assert all(r.evaluator_log_id for r in rows)
    assert all(r.finished_at for r in rows)
    assert len({r.question_version for r in rows}) == 1
    evidence = rows[0].evidence["agent_instructions"]
    assert evidence["source"] == "stated_by_the_user"
    assert evidence["digest"]
    assert len(decision_model.requests) == 2
    # Variables only: the state holds the kind the question reads, nothing else.
    assert decision_model.requests[0]["state"] == (
        "You are a refunds agent. Never refund over 100."
    )


@pytest.mark.django_db
def test_a_marker_at_the_end_of_long_instructions_reaches_the_model_whole(
    organization,
    workspace,
    user,
    selection,
    agent_version,
    gate_passes,
    queued,
    decision_model,
):
    """Pass line 1, for the decision model: nothing on the path cuts."""
    long_text = ("Refund rules, paragraph after paragraph. " * 1500) + LONG_TAIL_MARKER
    agent_version.configuration_snapshot = {
        **agent_version.configuration_snapshot,
        "description": long_text,
    }
    agent_version.save(update_fields=["configuration_snapshot"])
    ask = _launch(selection, organization, workspace, user)

    grade_row(str(_rows(ask)[0].id))

    (sent,) = decision_model.requests
    assert sent["state"] == long_text
    assert sent["state"].endswith(LONG_TAIL_MARKER)
    instructions = json.dumps(sent["questions"]["q1"]["instructions"])
    assert LONG_TAIL_MARKER in instructions
    assert _rows(ask)[0].state == STATE.ANSWERED


@pytest.mark.django_db
def test_an_input_too_large_for_the_model_is_unmeasured_and_never_sent(
    organization,
    workspace,
    user,
    selection,
    agent_version,
    gate_passes,
    queued,
    decision_model,
):
    """Instructions are never cut: too large is unmeasured, and costs nothing."""
    agent_version.configuration_snapshot = {
        **agent_version.configuration_snapshot,
        # Words, not one unbroken run: the shared URL-password redaction is slow
        # on a single 200,000-character token.
        "description": "Refund only with a receipt. " * 8_000,
    }
    agent_version.save(update_fields=["configuration_snapshot"])
    ask = _launch(selection, organization, workspace, user)

    _grade_all(ask)

    rows = _rows(ask)
    assert {r.state for r in rows} == {STATE.UNMEASURED}
    assert {r.cause for r in rows} == {"too_large"}
    assert {r.evaluator_log_id for r in rows} == {None}
    assert decision_model.requests == []
    # Refused before the evaluator: it writes no usage log row of its own.
    from ee.usage.models.usage import APICallLog

    assert not APICallLog.objects.filter(organization=organization).exists()


@pytest.mark.django_db
def test_a_kind_not_captured_when_graded_is_unmeasured_with_no_evaluator_call(
    organization,
    workspace,
    user,
    selection,
    agent_version,
    gate_passes,
    queued,
    decision_model,
):
    ask = _launch(selection, organization, workspace, user)
    agent_version.configuration_snapshot = {
        **agent_version.configuration_snapshot,
        "description": "",
    }
    agent_version.description = ""
    agent_version.save()

    _grade_all(ask)

    rows = _rows(ask)
    assert {r.state for r in rows} == {STATE.UNMEASURED}
    assert {r.cause for r in rows} == {"no_whole_instructions"}
    assert decision_model.requests == []


@pytest.mark.django_db
def test_an_evaluator_failure_is_a_grading_error_never_a_fail(
    organization, workspace, user, selection, gate_passes, queued, decision_model
):
    """Pass line 7, first half."""
    from ee.jev.client import JevGatewayError

    decision_model.error = JevGatewayError("JEV_PROVIDER_ERROR")
    ask = _launch(selection, organization, workspace, user)

    _grade_all(ask)

    rows = _rows(ask)
    assert {r.state for r in rows} == {STATE.GRADING_ERROR}
    assert {r.cause for r in rows} == {"JEV_PROVIDER_ERROR"}
    assert {r.label for r in rows} == {None}
    assert {r.score for r in rows} == {None}
    assert "TypeSafe" in rows[0].reason
    counts = result_counts(ask)["q1"]
    assert counts["grading_error"] == 2
    assert counts["rate"] == {"answered": 0, "labels": {}}


@pytest.mark.django_db
def test_credits_that_run_out_stop_the_ask_and_cancel_what_has_not_run(
    organization, workspace, user, selection, gate_passes, queued, decision_model
):
    from ee.usage.exceptions import UsageLimitExceeded

    ask = _launch(selection, organization, workspace, user)
    first, second = _rows(ask)
    refusal = UsageLimitExceeded.__new__(UsageLimitExceeded)
    with patch(
        "simulate.services.ask_engine.run_eval_func", side_effect=refusal
    ) as evaluator:
        grade_row(str(first.id))
        # The stop cancels what has not run, before anything else is graded.
        second.refresh_from_db()
        assert (second.state, second.cause) == (STATE.CANCELLED, "credits")
        grade_row(str(second.id))

    assert evaluator.call_count == 1
    rows = _rows(ask)
    assert {r.state for r in rows} == {STATE.CANCELLED}
    assert {r.cause for r in rows} == {"credits"}
    ask.refresh_from_db()
    assert ask.status == SimulateAsk.Status.STOPPED
    assert ask.stop_cause == "credits"


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["insufficient_credits", "resource_limit"])
def test_a_usage_log_that_refuses_for_credits_stops_the_ask_too(
    organization,
    workspace,
    user,
    selection,
    gate_passes,
    queued,
    decision_model,
    status,
):
    # The shared evaluator's other refusal: its usage log row is not created as
    # processing, and it raises ValueError("API call not allowed : ", status).
    ask = _launch(selection, organization, workspace, user)
    first, second = _rows(ask)
    with patch(
        "simulate.services.ask_engine.run_eval_func",
        side_effect=ValueError("API call not allowed : ", status),
    ):
        grade_row(str(first.id))

    rows = _rows(ask)
    assert {r.state for r in rows} == {STATE.CANCELLED}
    assert {r.cause for r in rows} == {"credits"}
    ask.refresh_from_db()
    assert ask.status == SimulateAsk.Status.STOPPED


@pytest.mark.django_db
def test_a_failure_of_the_step_itself_still_closes_the_claimed_row(
    organization, workspace, user, selection, gate_passes, queued, decision_model
):
    ask = _launch(selection, organization, workspace, user)
    row = _rows(ask)[0]

    with patch.object(engine, "_too_large", side_effect=RuntimeError("boom")):
        assert grade_row(str(row.id)) == "done"

    row.refresh_from_db()
    assert (row.state, row.cause) == (STATE.GRADING_ERROR, "step_failed")
    assert "boom" not in (row.reason or "")
    assert decision_model.requests == []


# --- at most once, and at most four at a time --------------------------------------


@pytest.mark.django_db
def test_a_grading_delivered_twice_charges_once_and_leaves_one_row(
    organization, workspace, user, selection, gate_passes, queued, decision_model
):
    """Pass line 10."""
    from ee.usage.models.usage import APICallLog

    ask = _launch(selection, organization, workspace, user)
    row = _rows(ask)[0]
    logs_before = APICallLog.objects.count()

    grade_row(str(row.id))
    grade_row(str(row.id))

    assert len(decision_model.requests) == 1
    assert APICallLog.objects.count() == logs_before + 1
    assert (
        SimulateEvalResult.objects.filter(
            ask=ask, call_execution_id=row.call_execution_id, question_key="q1"
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_the_claim_itself_refuses_a_row_another_delivery_took(
    organization, workspace, user, selection, gate_passes, queued
):
    """Two deliveries that both read the row unclaimed: only one claim holds."""
    ask = _launch(selection, organization, workspace, user)
    row = _rows(ask)[0]
    stale = SimulateEvalResult.objects.get(id=row.id)

    first = engine._claim(stale)
    second = engine._claim(stale)

    assert first not in ("taken", "busy")
    assert second == "taken"


@pytest.mark.django_db
def test_a_row_taken_by_a_worker_that_died_becomes_an_infrastructure_error(
    organization, workspace, user, selection, gate_passes, queued, decision_model
):
    ask = _launch(selection, organization, workspace, user)
    row = _rows(ask)[0]
    SimulateEvalResult.objects.filter(id=row.id).update(
        claimed_at=timezone.now() - engine.GRADING_GIVE_UP - timedelta(seconds=1)
    )

    _run_ask(ask)

    row.refresh_from_db()
    assert row.state == STATE.INFRASTRUCTURE_ERROR
    assert row.cause == "worker_died"
    # It is not run again by itself; only the row nobody had taken was graded.
    assert len(decision_model.requests) == 1


@pytest.mark.django_db
def test_rows_still_waiting_when_the_job_runs_out_of_time_are_not_left_pending(
    organization,
    workspace,
    user,
    selection,
    gate_passes,
    queued,
    decision_model,
    monkeypatch,
):
    ask = _launch(selection, organization, workspace, user)
    # The organisation has no free place, and the job's time is already up.
    monkeypatch.setattr(engine, "MAX_GRADING_AT_ONCE_PER_ORG", 0)
    monkeypatch.setattr(engine, "RUN_TIME_LIMIT_SECONDS", 300)

    _run_ask(ask)

    rows = _rows(ask)
    assert {(r.state, r.cause) for r in rows} == {
        (STATE.INFRASTRUCTURE_ERROR, "not_run")
    }
    assert decision_model.requests == []
    ask.refresh_from_db()
    assert ask.status == SimulateAsk.Status.FINISHED


@pytest.mark.django_db
def test_an_ask_whose_job_ended_is_settled_when_read(
    organization, workspace, user, selection, gate_passes, queued
):
    ask = _launch(selection, organization, workspace, user)
    first, second = _rows(ask)
    SimulateEvalResult.objects.filter(ask=ask).update(
        created_at=timezone.now() - engine.SETTLE_AFTER - timedelta(seconds=1)
    )
    SimulateEvalResult.objects.filter(id=first.id).update(
        claimed_at=timezone.now() - engine.GRADING_GIVE_UP - timedelta(seconds=1)
    )

    engine.settle_ask(ask.id)

    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.state, first.cause) == (STATE.INFRASTRUCTURE_ERROR, "worker_died")
    assert (second.state, second.cause) == (STATE.INFRASTRUCTURE_ERROR, "job_ended")
    ask.refresh_from_db()
    assert ask.status == SimulateAsk.Status.FINISHED


@pytest.mark.django_db
def test_a_recent_ask_is_left_to_its_job_when_read(
    organization, workspace, user, selection, gate_passes, queued
):
    ask = _launch(selection, organization, workspace, user)

    engine.settle_ask(ask.id)

    assert {r.state for r in _rows(ask)} == {STATE.PENDING}
    ask.refresh_from_db()
    assert ask.status == SimulateAsk.Status.RUNNING


@pytest.mark.django_db
def test_no_more_than_four_gradings_run_at_once_for_one_organisation(
    organization, workspace, user, selection, gate_passes, queued, decision_model
):
    ask = _launch(selection, organization, workspace, user)
    other = _launch(selection, organization, workspace, user)
    in_flight = _rows(other)
    # Four rows of the organisation's other ask are being graded right now.
    for row in in_flight[:2]:
        SimulateEvalResult.objects.filter(id=row.id).update(claimed_at=timezone.now())
    busy = [
        SimulateEvalResult.objects.create(
            organization=organization,
            workspace=workspace,
            origin=SimulateEvalResult.Origin.ASK,
            ask=other,
            call_execution=in_flight[0].call_execution,
            question_key=f"busy-{index}",
            state=STATE.PENDING,
            claimed_at=timezone.now(),
        )
        for index in range(MAX_GRADING_AT_ONCE_PER_ORG - 2)
    ]
    assert len(busy) + 2 == MAX_GRADING_AT_ONCE_PER_ORG

    taken = grade_row(str(_rows(ask)[0].id))

    assert taken == "busy"
    assert _rows(ask)[0].claimed_at is None
    assert decision_model.requests == []


# --- what an ask never touches --------------------------------------------------------


@pytest.mark.django_db
def test_an_ask_changes_nothing_on_the_call_or_the_run(
    organization,
    workspace,
    user,
    selection,
    run,
    calls,
    gate_passes,
    queued,
    decision_model,
):
    """Pass line 3: asking twice gives two sets of rows and leaves the calls alone."""

    def snapshot():
        run.refresh_from_db()
        return (
            run.status,
            run.eval_explanation_summary_status,
            [
                (
                    c.status,
                    c.eval_outputs,
                    c.call_metadata,
                    c.overall_score,
                )
                for c in CallExecution.objects.filter(
                    id__in=[x.id for x in calls]
                ).order_by("id")
            ],
        )

    before = snapshot()
    first = _launch(selection, organization, workspace, user)
    _grade_all(first)
    second = _launch(selection, organization, workspace, user)
    _grade_all(second)

    assert snapshot() == before
    assert len(_rows(first)) == len(_rows(second)) == 2
    assert {r.state for r in _rows(first) + _rows(second)} == {STATE.ANSWERED}


@pytest.mark.django_db
def test_an_ask_creates_no_hosted_job_no_call_and_no_call_workflow(
    organization, workspace, user, selection, gate_passes, queued, decision_model
):
    """Pass line 5: the only job an ask starts is its own grading job."""
    jobs, calls_before = HostedHarnessJob.objects.count(), CallExecution.objects.count()

    ask = _launch(selection, organization, workspace, user)
    _grade_all(ask)

    assert HostedHarnessJob.objects.count() == jobs
    assert CallExecution.objects.count() == calls_before
    queued.assert_called_once()
    assert queued.call_args.kwargs["args"] == (str(ask.id),)


@pytest.mark.django_db
def test_every_result_is_a_call_of_the_selection_and_the_counts_add_up(
    organization,
    workspace,
    user,
    selection,
    agent_version,
    gate_passes,
    queued,
    decision_model,
):
    """Pass line 4."""
    ask = _launch(selection, organization, workspace, user)
    first, second = _rows(ask)
    grade_row(str(first.id))
    counts = result_counts(ask)["q1"]
    assert counts["requested"] == 2
    assert counts["pending"] == 1

    from ee.jev.client import JevGatewayError

    decision_model.error = JevGatewayError("JEV_PROVIDER_ERROR")
    grade_row(str(second.id))
    _run_ask(ask)

    counts = result_counts(ask)["q1"]
    six = (
        "answered",
        "unmeasured",
        "not_applicable",
        "grading_error",
        "infrastructure_error",
        "cancelled",
    )
    assert counts["pending"] == 0
    assert sum(counts[name] for name in six) == counts["requested"] == 2
    assert counts["rate"] == {"answered": 1, "labels": {"Yes": 1}}
    assert {str(r.call_execution_id) for r in _rows(ask)} <= set(
        ask.selection["call_ids"]
    )
    ask.refresh_from_db()
    assert ask.status == SimulateAsk.Status.FINISHED
