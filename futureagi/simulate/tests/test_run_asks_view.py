"""Asking a question about a run's calls from its page: the check before launch,
the launch, and reading what came back.

The page sends the calls a person picked (the whole run, or the calls they
ticked) and up to five typed questions. The check shows, per question, how many
calls are ready and how many are unmeasured, and whether Instant Eval's gate would
let the ask launch; it writes nothing and calls no model. The launch runs the same
check again and is refused when what the person saw has changed. Reading an ask
returns requested and the six counts, and one row per call and question that links
back to its call. Every read and write stays inside the run's organisation and
workspace.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.test import TestCase
from rest_framework import status

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios
from simulate.models.ask import SimulateAsk
from simulate.models.eval_result_store import SimulateEvalResult
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services import ask_engine as engine
from simulate.services.ask_selection import LaunchRefused

CHECK = "/simulate/v3/test-executions/{}/asks/check/"
ASKS = "/simulate/v3/test-executions/{}/asks/"
ASK = "/simulate/v3/test-executions/{}/asks/{}/"
QUESTION = {
    "question": "Do the agent's instructions forbid refunds over 100?",
    "answer_type": "yes_no",
    "kinds": ["agent_instructions"],
}
RESULT = SimulateEvalResult.State

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


def _run(
    organization, workspace, agent_definition, agent_version, simulator_agent, scenario
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
        total_calls=3,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        agent_version=agent_version,
        scenario_ids=[str(scenario.id)],
    )


@pytest.fixture
def run(
    organization, workspace, agent_definition, agent_version, simulator_agent, scenario
):
    return _run(
        organization,
        workspace,
        agent_definition,
        agent_version,
        simulator_agent,
        scenario,
    )


def _calls(run, scenario, count, status_=CallExecution.CallStatus.COMPLETED):
    return [
        CallExecution.objects.create(
            test_execution=run,
            scenario=scenario,
            phone_number=f"+1555000000{index}",
            status=status_,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            provider_call_data={"vapi": {"id": f"v{index}"}},
            eval_outputs={"own-eval": {"output": "Passed", "status": "completed"}},
            call_metadata={"eval_started": True, "eval_completed": True},
        )
        for index in range(count)
    ]


@pytest.fixture
def calls(run, scenario):
    return _calls(run, scenario, 3)


@pytest.fixture
def gate_passes():
    """Instant Eval's gate passes and the ask is not billed per answer."""
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


@pytest.fixture
def no_model():
    """Any call to the decision model fails the test."""
    with patch(
        "ee.jev.client.JevSystemOneClient._request",
        side_effect=AssertionError("a model was called"),
    ) as request:
        yield request


def _body(selection=None, questions=None, **extra):
    return {
        "selection": selection or {"kind": "runs"},
        "questions": questions or [QUESTION],
        **extra,
    }


def _check(client, run, **body):
    return client.post(CHECK.format(run.id), _body(**body), format="json")


def _launch(client, run, *, requested, credits=None, **body):
    with TestCase.captureOnCommitCallbacks(execute=True):
        return client.post(
            ASKS.format(run.id),
            _body(
                accepted={"requested": requested, "credits_estimate": credits}, **body
            ),
            format="json",
        )


def _nothing_written():
    return (
        SimulateAsk.objects.count(),
        SimulateEvalResult.objects.filter(origin=SimulateEvalResult.Origin.ASK).count(),
        EvalTemplate.no_workspace_objects.count(),
    )


# --- the check before launch ---------------------------------------------------------


@pytest.mark.django_db
def test_the_check_counts_the_whole_run_and_writes_nothing(
    auth_client, run, calls, gate_passes, no_model
):
    before = _nothing_written()

    response = _check(auth_client, run)

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["requested"] == 3
    [question] = response.data["questions"]
    assert question["name"] == QUESTION["question"]
    assert question["kinds"] == ["agent_instructions"]
    assert question["ready"] + question["unmeasured"] + question["not_applicable"] == 3
    assert response.data["credits_estimate"] is None
    assert response.data["refusal"] is None
    assert _nothing_written() == before
    no_model.assert_not_called()


@pytest.mark.django_db
def test_the_check_counts_only_the_calls_ticked(auth_client, run, calls, gate_passes):
    response = _check(
        auth_client,
        run,
        selection={"kind": "calls", "call_ids": [str(calls[2].id), str(calls[0].id)]},
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["requested"] == 2


@pytest.mark.django_db
def test_a_call_of_another_run_is_refused_like_one_that_does_not_exist(
    auth_client,
    organization,
    workspace,
    run,
    calls,
    agent_definition,
    agent_version,
    simulator_agent,
    scenario,
    gate_passes,
):
    other = _run(
        organization,
        workspace,
        agent_definition,
        agent_version,
        simulator_agent,
        scenario,
    )
    [foreign] = _calls(other, scenario, 1)

    for call_id in (str(foreign.id), str(uuid.uuid4())):
        response = _check(
            auth_client, run, selection={"kind": "calls", "call_ids": [call_id]}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.data
        assert response.data["code"] == "not_found"


@pytest.mark.django_db
def test_a_run_of_another_workspace_is_not_found(
    auth_client, organization, user, run, calls, gate_passes
):
    from accounts.models.workspace import Workspace

    elsewhere = Workspace.objects.create(
        name=f"ws-{uuid.uuid4().hex[:6]}", organization=organization, created_by=user
    )
    RunTest.objects.filter(id=run.run_test_id).update(workspace=elsewhere)

    for response in (_check(auth_client, run), auth_client.get(ASKS.format(run.id))):
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_a_call_still_running_is_refused_with_how_many(
    auth_client, run, calls, scenario, gate_passes
):
    _calls(run, scenario, 1, status_=CallExecution.CallStatus.ONGOING)

    response = _check(auth_client, run)

    assert response.status_code == status.HTTP_409_CONFLICT, response.data
    assert response.data["code"] == "not_finished"
    assert "1 of the 4 selected calls" in response.data["message"]


@pytest.mark.django_db
def test_a_question_that_reads_audio_is_refused_before_anything_is_read(
    auth_client, run, calls, gate_passes
):
    response = _check(
        auth_client, run, questions=[{**QUESTION, "kinds": ["transcript", "audio"]}]
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
    assert response.data["code"] == "audio_unsupported"


@pytest.mark.django_db
def test_six_questions_are_refused(auth_client, run, calls, gate_passes):
    response = _check(auth_client, run, questions=[QUESTION] * 6)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data


@pytest.mark.django_db
def test_a_missing_key_shows_on_the_check_and_refuses_the_launch_unspent(
    auth_client, run, calls, queued, no_model
):
    refused = LaunchRefused(
        "no_key", "Add a key for the decision model in AI providers first."
    )
    with (
        patch("simulate.services.ask_selection._check_capability"),
        patch("simulate.services.ask_selection._check_key", side_effect=refused),
        patch("simulate.services.ask_selection._check_credits") as credits,
        patch("simulate.services.ask_selection._credits", return_value=None),
    ):
        checked = _check(auth_client, run)
        before = _nothing_written()
        launched = _launch(auth_client, run, requested=3)

    assert checked.status_code == status.HTTP_200_OK, checked.data
    assert checked.data["requested"] == 3
    assert checked.data["refusal"] == {
        "code": "no_key",
        "message": "Add a key for the decision model in AI providers first.",
    }
    assert launched.status_code == status.HTTP_409_CONFLICT, launched.data
    assert launched.data["code"] == "no_key"
    assert _nothing_written() == before
    credits.assert_not_called()
    queued.assert_not_called()
    no_model.assert_not_called()


# --- the launch -----------------------------------------------------------------------


@pytest.mark.django_db
def test_a_launch_over_the_ticked_calls_writes_one_pending_row_each_and_queues_once(
    auth_client, user, run, calls, gate_passes, queued, no_model
):
    picked = [str(calls[1].id), str(calls[0].id)]

    response = _launch(
        auth_client,
        run,
        requested=2,
        selection={"kind": "calls", "call_ids": picked, "note": "The two refunds"},
    )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    ask = SimulateAsk.objects.get(id=response.data["id"])
    assert ask.asked_by_id == user.id
    assert ask.selection["call_ids"] == picked
    assert ask.selection["description"]["run_ids"] == [str(run.id)]
    assert ask.selection["description"]["note"] == "The two refunds"
    rows = SimulateEvalResult.objects.filter(ask=ask)
    assert sorted(
        str(one) for one in rows.values_list("call_execution_id", flat=True)
    ) == (sorted(picked))
    assert set(rows.values_list("state", flat=True)) == {RESULT.PENDING}
    queued.assert_called_once()
    no_model.assert_not_called()


@pytest.mark.django_db
def test_a_launch_whose_call_count_changed_since_the_check_is_refused_unspent(
    auth_client, run, calls, gate_passes, queued
):
    before = _nothing_written()

    response = _launch(auth_client, run, requested=2)

    assert response.status_code == status.HTTP_409_CONFLICT, response.data
    assert response.data["code"] == "selection_changed"
    assert _nothing_written() == before
    queued.assert_not_called()


@pytest.mark.django_db
def test_a_launch_whose_cost_changed_is_refused_with_the_new_cost(
    auth_client, run, calls, queued
):
    with (
        patch("simulate.services.ask_selection._check_capability"),
        patch("simulate.services.ask_selection._check_key"),
        patch("simulate.services.ask_selection._check_credits"),
        patch("simulate.services.ask_selection._credits", return_value=4.0),
    ):
        response = _launch(auth_client, run, requested=3, credits=2.0)

    assert response.status_code == status.HTTP_409_CONFLICT, response.data
    assert response.data["code"] == "cost_changed"
    assert response.data["credits_estimate"] == 4.0
    queued.assert_not_called()


@pytest.mark.django_db
def test_an_ask_changes_nothing_on_the_calls_or_the_run(
    auth_client, run, calls, gate_passes, queued
):
    def state():
        return (
            TestExecution.objects.filter(id=run.id).values_list("status").first(),
            sorted(
                CallExecution.objects.filter(test_execution=run).values_list(
                    "id", "status", "eval_outputs", "call_metadata"
                )
            ),
        )

    before = state()

    response = _launch(auth_client, run, requested=3)

    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert state() == before


# --- reading ------------------------------------------------------------------------------


def _ask_with_states(auth_client, run, calls, states):
    response = _launch(auth_client, run, requested=len(calls))
    assert response.status_code == status.HTTP_201_CREATED, response.data
    ask = SimulateAsk.objects.get(id=response.data["id"])
    for call, (state, label) in zip(calls, states, strict=True):
        SimulateEvalResult.objects.filter(ask=ask, call_execution=call).update(
            state=state, label=label, reason=f"Because {label}" if label else None
        )
    return ask


@pytest.mark.django_db
def test_an_ask_reads_back_requested_the_six_counts_and_a_row_per_call(
    auth_client, run, calls, gate_passes, queued, no_model
):
    ask = _ask_with_states(
        auth_client,
        run,
        calls,
        [
            (RESULT.ANSWERED, "Yes"),
            (RESULT.UNMEASURED, None),
            (RESULT.GRADING_ERROR, None),
        ],
    )
    SimulateAsk.objects.filter(id=ask.id).update(status=SimulateAsk.Status.FINISHED)

    response = auth_client.get(ASK.format(run.id, ask.id))

    assert response.status_code == status.HTTP_200_OK, response.data
    [question] = response.data["questions"]
    counts = question["counts"]
    assert counts["requested"] == 3
    six = (
        "answered",
        "unmeasured",
        "not_applicable",
        "grading_error",
        "infrastructure_error",
        "cancelled",
    )
    assert sum(counts[one] for one in six) == counts["requested"]
    assert counts["pending"] == 0
    assert counts["rate"] == {"answered": 1, "labels": {"Yes": 1}}
    rows = response.data["results"]
    assert [row["call_execution_id"] for row in rows] == [str(one.id) for one in calls]
    assert {row["call_execution_id"] for row in rows} <= set(ask.selection["call_ids"])
    assert rows[0]["state"] == "answered"
    assert rows[0]["label"] == "Yes"
    assert rows[0]["reason"] == "Because Yes"
    assert rows[2]["state"] == "grading_error"
    assert all(row["test_execution_id"] == str(run.id) for row in rows)
    no_model.assert_not_called()


@pytest.mark.django_db
def test_a_row_names_its_call_the_way_the_runs_table_does(
    auth_client, run, calls, scenario, gate_passes, queued
):
    tagged, untagged, first_trial = calls
    CallExecution.objects.filter(id=tagged.id).update(
        call_metadata={"harness_scenario_key": "refund-angry", "harness_trial_index": 2}
    )
    CallExecution.objects.filter(id=first_trial.id).update(
        call_metadata={"harness_scenario_key": "refund-calm"}
    )
    ask = _ask_with_states(
        auth_client, run, calls, [(RESULT.PENDING, None)] * len(calls)
    )

    response = auth_client.get(ASK.format(run.id, ask.id))

    assert response.status_code == status.HTTP_200_OK, response.data
    assert [row["call_name"] for row in response.data["results"]] == [
        "refund-angry · Trial 2",
        scenario.name,
        "refund-calm",
    ]


@pytest.mark.django_db
def test_a_running_ask_counts_what_is_still_pending(
    auth_client, run, calls, gate_passes, queued
):
    ask = _ask_with_states(
        auth_client,
        run,
        calls,
        [(RESULT.ANSWERED, "No"), (RESULT.PENDING, None), (RESULT.PENDING, None)],
    )

    response = auth_client.get(ASK.format(run.id, ask.id))

    counts = response.data["questions"][0]["counts"]
    assert response.data["status"] == "running"
    assert counts["pending"] == 2
    assert counts["answered"] == 1


@pytest.mark.django_db
def test_reading_an_ask_settles_one_whose_job_died(
    auth_client, run, calls, gate_passes, queued
):
    ask = _ask_with_states(auth_client, run, calls, [(RESULT.PENDING, None)] * 3)
    with patch.object(engine, "settle_ask", wraps=engine.settle_ask) as settle:
        auth_client.get(ASK.format(run.id, ask.id))

    settle.assert_called_once_with(ask.id)


@pytest.mark.django_db
def test_a_deleted_call_drops_out_of_an_asks_rows(
    auth_client, run, calls, gate_passes, queued
):
    ask = _ask_with_states(auth_client, run, calls, [(RESULT.ANSWERED, "Yes")] * 3)
    CallExecution.objects.filter(id=calls[1].id).update(deleted=True)

    response = auth_client.get(ASK.format(run.id, ask.id))

    assert [row["call_execution_id"] for row in response.data["results"]] == [
        str(calls[0].id),
        str(calls[2].id),
    ]


@pytest.mark.django_db
def test_the_runs_asks_are_listed_newest_first_and_no_other_runs(
    auth_client,
    organization,
    workspace,
    run,
    calls,
    agent_definition,
    agent_version,
    simulator_agent,
    scenario,
    gate_passes,
    queued,
):
    other = _run(
        organization,
        workspace,
        agent_definition,
        agent_version,
        simulator_agent,
        scenario,
    )
    _calls(other, scenario, 1)
    first = _launch(auth_client, run, requested=3).data["id"]
    second = _launch(
        auth_client,
        run,
        requested=1,
        selection={"kind": "calls", "call_ids": [str(calls[0].id)]},
    ).data["id"]
    elsewhere = _launch(auth_client, other, requested=1).data["id"]

    response = auth_client.get(ASKS.format(run.id))

    assert response.status_code == status.HTTP_200_OK, response.data
    listed = [one["id"] for one in response.data["asks"]]
    assert listed == [second, first]
    assert elsewhere not in listed
    newest = response.data["asks"][0]
    assert newest["requested"] == 1
    assert newest["questions"][0]["name"] == QUESTION["question"]
    assert newest["questions"][0]["counts"]["pending"] == 1


@pytest.mark.django_db
def test_an_ask_of_another_run_is_not_found_through_this_one(
    auth_client,
    organization,
    workspace,
    run,
    calls,
    agent_definition,
    agent_version,
    simulator_agent,
    scenario,
    gate_passes,
    queued,
):
    other = _run(
        organization,
        workspace,
        agent_definition,
        agent_version,
        simulator_agent,
        scenario,
    )
    _calls(other, scenario, 1)
    elsewhere = _launch(auth_client, other, requested=1).data["id"]

    response = auth_client.get(ASK.format(run.id, elsewhere))

    assert response.status_code == status.HTTP_404_NOT_FOUND
