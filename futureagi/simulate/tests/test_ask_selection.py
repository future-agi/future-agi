"""An ask's selection, frozen at asking, and the check before it launches.

Four ways to select (runs, chosen calls, a scenario group, a filter) become one
ordered list of call ids with the user's own description. A selection is refused
when it holds a call from another workspace (or one that does not exist: the same
refusal), a call still running, more than one agent, or more than 200 calls.
Before launch, each question's kinds are read for every call, and the counts of
ready and unmeasured calls are shown by kind and cause. The launch then passes
Instant Eval's own gate (the capability, the key for the decision model, the
credits) or is refused with the reason, before anything is spent.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models.workspace import Workspace
from model_hub.models.choices import DatasetSourceChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from simulate.models import AgentDefinition, Scenarios
from simulate.models.hosted_harness import (
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services import ask_selection as selection
from simulate.services.ask_selection import (
    MAX_QUESTIONS,
    LaunchRefused,
    Question,
    SelectionKind,
    SelectionRefused,
    check_before_launch,
    freeze_selection,
    launch_gate,
)
from simulate.services.call_evidence import Cause, Kind

STATUS = CallExecution.CallStatus

# --- fixtures (copied, not imported, from the other simulate tests) ------------


def _agent(organization, workspace, name="Agent"):
    return AgentDefinition.objects.create(
        agent_name=name,
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="You are a refunds agent.",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_definition(db, organization, workspace):
    return _agent(organization, workspace)


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


def _scenario(organization, user, workspace, agent_definition, name):
    dataset = Dataset.no_workspace_objects.create(
        name=f"{name} rows",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    return Scenarios.objects.create(
        name=name,
        description=f"{name}.",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def scenario(db, organization, user, workspace, agent_definition):
    return _scenario(organization, user, workspace, agent_definition, "Refund")


def _run(agent_definition, simulator_agent, scenario, workspace=None):
    run_test = RunTest.objects.create(
        name="Run",
        description="Run under test",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=agent_definition.organization,
        workspace=workspace or agent_definition.workspace,
    )
    run_test.scenarios.add(scenario)
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        scenario_ids=[str(scenario.id)],
    )


@pytest.fixture
def run(agent_definition, simulator_agent, scenario):
    return _run(agent_definition, simulator_agent, scenario)


def _calls(execution, scenario, count=1, status=STATUS.COMPLETED, **fields):
    return [
        CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            phone_number=f"+1555{index:07d}",
            status=status,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            **fields,
        )
        for index in range(count)
    ]


def _freeze(organization, workspace, kind, runs, **picked):
    return freeze_selection(
        kind=kind,
        organization_id=organization.id,
        workspace_id=workspace.id,
        run_ids=[str(one.id) for one in runs],
        note="Calls where the agent refunded over the limit",
        **picked,
    )


def _ids(calls):
    return tuple(str(call.id) for call in calls)


# --- the four ways become one ordered list --------------------------------------


@pytest.mark.django_db
def test_runs_become_every_call_of_those_runs_in_order(
    organization, workspace, user, run, agent_definition, simulator_agent, scenario
):
    second = _run(agent_definition, simulator_agent, scenario)
    first_calls = _calls(run, scenario, 2)
    second_calls = _calls(second, scenario, 2, status=STATUS.FAILED)

    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [second, run])

    assert frozen.call_ids == _ids(second_calls) + _ids(first_calls)
    assert frozen.agent_definition_id == str(agent_definition.id)
    assert frozen.description == {
        "kind": "runs",
        "run_ids": [str(second.id), str(run.id)],
        "note": "Calls where the agent refunded over the limit",
    }


@pytest.mark.django_db
def test_chosen_calls_keep_the_order_they_were_chosen_in(
    organization, workspace, run, scenario
):
    calls = _calls(run, scenario, 3)
    picked = [str(calls[2].id), str(calls[0].id), str(calls[2].id)]

    frozen = _freeze(
        organization, workspace, SelectionKind.CALLS, [run], call_ids=picked
    )

    assert frozen.call_ids == (str(calls[2].id), str(calls[0].id))
    assert frozen.description["call_ids"] == picked


@pytest.mark.django_db
def test_a_scenario_group_on_a_hosted_run_is_found_through_its_trials(
    organization, workspace, user, run, scenario, agent_definition
):
    """Hosted calls share the run's scenario row; their trials name the scenario."""
    other = _scenario(organization, user, workspace, agent_definition, "Other")
    job = _job(organization, workspace, run)
    calls = _calls(run, scenario, 4)
    for index, call in enumerate(calls):
        authored = scenario if index < 2 else other
        source = HostedHarnessScenario.no_workspace_objects.get_or_create(
            job=job,
            scenario_key=authored.name,
            defaults={"name": authored.name, "scenario": authored},
        )[0]
        HostedHarnessExecution.objects.create(
            job=job,
            source_scenario=source,
            execution_key=f"exec-{index}",
            trial_index=index % 2,
            call_execution=call,
        )

    frozen = _freeze(
        organization,
        workspace,
        SelectionKind.SCENARIOS,
        [run],
        scenario_ids=[str(other.id)],
    )

    assert frozen.call_ids == _ids(calls[2:])


@pytest.mark.django_db
def test_a_scenario_group_on_a_run_that_is_not_hosted_reads_the_calls_scenario(
    organization, workspace, user, run, scenario, agent_definition
):
    other = _scenario(organization, user, workspace, agent_definition, "Other")
    _calls(run, scenario, 1)
    wanted = _calls(run, other, 2)

    frozen = _freeze(
        organization, workspace, SelectionKind.SCENARIOS, [run], scenario_ids=[other.id]
    )

    assert frozen.call_ids == _ids(wanted)


@pytest.mark.django_db
def test_a_filter_becomes_the_calls_it_matches_at_asking(
    organization, workspace, run, scenario
):
    calls = _calls(run, scenario, 3)
    filters = {"call_execution_id": [str(calls[1].id)]}

    frozen = _freeze(
        organization, workspace, SelectionKind.FILTER, [run], filters=filters
    )

    assert frozen.call_ids == (str(calls[1].id),)
    assert frozen.description["filters"] == filters


@pytest.mark.django_db
def test_a_filter_keeps_the_run_pages_order(organization, workspace, run, scenario):
    calls = _calls(run, scenario, 3)
    now = timezone.now()
    for minutes, call in zip((5, 1, 9), calls, strict=True):
        CallExecution.objects.filter(id=call.id).update(
            started_at=now - timedelta(minutes=minutes)
        )

    frozen = _freeze(
        organization,
        workspace,
        SelectionKind.FILTER,
        [run],
        filters={"call_execution_id": list(_ids(calls))},
    )

    # The page shows the latest start first.
    assert frozen.call_ids == _ids([calls[1], calls[0], calls[2]])


@pytest.mark.django_db
def test_a_filter_the_run_page_does_not_take_is_refused(
    organization, workspace, run, scenario
):
    _calls(run, scenario, 1)
    with pytest.raises(SelectionRefused) as refused:
        _freeze(
            organization,
            workspace,
            SelectionKind.FILTER,
            [run],
            filters={"error_type": ["timeout"]},
        )

    assert refused.value.code == "bad_filter"


@pytest.mark.django_db
def test_the_frozen_selection_is_stored_as_json(
    organization, workspace, user, run, scenario
):
    _calls(run, scenario, 2)
    frozen = freeze_selection(
        kind=SelectionKind.RUNS,
        organization_id=organization.id,
        workspace_id=workspace.id,
        run_ids=[str(run.id)],
        note="",
        asked_by_id=user.id,
    )

    stored = json.loads(json.dumps(frozen.as_json()))

    assert stored["call_ids"] == list(frozen.call_ids)
    assert stored["asked_by_id"] == str(user.id)
    assert stored["asked_at"]


# --- refusals ---------------------------------------------------------------------


NOT_FOUND = "Some of the selected runs or calls can't be found in this workspace."


@pytest.mark.django_db
def test_a_run_from_another_workspace_is_refused_like_one_that_does_not_exist(
    organization, workspace, user, agent_definition, simulator_agent, scenario, run
):
    """Pass line 8."""
    elsewhere = Workspace.objects.create(
        name=f"ws-{uuid.uuid4().hex[:6]}", organization=organization, created_by=user
    )
    foreign = _run(agent_definition, simulator_agent, scenario, workspace=elsewhere)
    _calls(foreign, scenario, 1)
    _calls(run, scenario, 1)

    for runs in ([run, foreign], [run]):
        run_ids = [str(one.id) for one in runs]
        if len(runs) == 1:
            run_ids.append(str(uuid.uuid4()))
        with pytest.raises(SelectionRefused) as refused:
            freeze_selection(
                kind=SelectionKind.RUNS,
                organization_id=organization.id,
                workspace_id=workspace.id,
                run_ids=run_ids,
                note="",
            )
        assert (refused.value.code, refused.value.message) == ("not_found", NOT_FOUND)


@pytest.mark.django_db
@pytest.mark.parametrize("picked", ["unknown", "not-a-uuid", "another_run"])
def test_a_call_not_in_the_selected_runs_is_refused(
    organization, workspace, run, agent_definition, simulator_agent, scenario, picked
):
    _calls(run, scenario, 1)
    another = _calls(_run(agent_definition, simulator_agent, scenario), scenario, 1)[0]
    call_id = {
        "unknown": str(uuid.uuid4()),
        "not-a-uuid": "not-a-uuid",
        "another_run": str(another.id),
    }[picked]

    with pytest.raises(SelectionRefused) as refused:
        _freeze(organization, workspace, SelectionKind.CALLS, [run], call_ids=[call_id])

    assert (refused.value.code, refused.value.message) == ("not_found", NOT_FOUND)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kind", [SelectionKind.RUNS, SelectionKind.FILTER, SelectionKind.SCENARIOS]
)
def test_the_requests_workspace_context_does_not_widen_or_narrow_it(
    organization, workspace, user, run, scenario, kind
):
    from tfc.middleware.workspace_context import (
        get_current_workspace,
        set_workspace_context,
    )

    calls = _calls(run, scenario, 2)
    picked = {
        SelectionKind.RUNS: {},
        SelectionKind.FILTER: {"filters": {"call_execution_id": list(_ids(calls))}},
        SelectionKind.SCENARIOS: {"scenario_ids": [str(scenario.id)]},
    }[kind]
    elsewhere = Workspace.objects.create(
        name=f"ws-{uuid.uuid4().hex[:6]}", organization=organization, created_by=user
    )
    before = get_current_workspace()
    set_workspace_context(workspace=elsewhere)
    try:
        frozen = _freeze(organization, workspace, kind, [run], **picked)
        with pytest.raises(SelectionRefused):
            _freeze(organization, elsewhere, kind, [run], **picked)
    finally:
        set_workspace_context(workspace=before)

    # The filter keeps the run page's order; the order is tested on its own.
    assert sorted(frozen.call_ids) == sorted(_ids(calls))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [STATUS.PENDING, STATUS.REGISTERED, STATUS.ONGOING, STATUS.ANALYZING, "registered"],
)
def test_a_call_that_has_not_finished_is_refused(
    organization, workspace, run, scenario, status
):
    _calls(run, scenario, 2)
    _calls(run, scenario, 1, status=status)

    with pytest.raises(SelectionRefused) as refused:
        _freeze(organization, workspace, SelectionKind.RUNS, [run])

    assert refused.value.code == "not_finished"
    assert refused.value.message == (
        "1 of the 3 selected calls hasn't finished yet. Try again in a moment."
    )


@pytest.mark.django_db
def test_failed_and_cancelled_calls_can_be_selected(
    organization, workspace, run, scenario
):
    calls = _calls(run, scenario, 1, status=STATUS.FAILED) + _calls(
        run, scenario, 1, status=STATUS.CANCELLED
    )

    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])

    assert frozen.call_ids == _ids(calls)


@pytest.mark.django_db
def test_more_calls_than_an_ask_takes_is_refused_with_the_count(
    organization, workspace, run, scenario
):
    _calls(run, scenario, 3)

    with (
        patch.object(selection, "MAX_CALLS", 2),
        pytest.raises(SelectionRefused) as refused,
    ):
        _freeze(organization, workspace, SelectionKind.RUNS, [run])

    assert refused.value.code == "too_many_calls"
    assert (
        refused.value.message
        == "An ask can take at most 2 calls; this selection has 3."
    )


def test_the_limit_is_two_hundred_calls():
    assert selection.MAX_CALLS == 200


@pytest.mark.django_db
def test_two_agents_in_one_selection_are_refused(
    organization, workspace, run, simulator_agent, scenario
):
    another = _run(_agent(organization, workspace, "Other"), simulator_agent, scenario)
    _calls(run, scenario, 1)
    _calls(another, scenario, 1)

    with pytest.raises(SelectionRefused) as refused:
        _freeze(organization, workspace, SelectionKind.RUNS, [run, another])

    assert refused.value.code == "more_than_one_agent"


@pytest.mark.django_db
def test_an_empty_selection_is_refused(organization, workspace, run):
    with pytest.raises(SelectionRefused) as refused:
        _freeze(organization, workspace, SelectionKind.RUNS, [run])

    assert refused.value.code == "empty"


# --- the check before launch ------------------------------------------------------


def _job(organization, workspace, run, level="traces"):
    return HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest="digest",
        schema_version="1.6",
        seed=1,
        artifact_level=level,
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=1,
        payload={"agent": {"connector": "phone"}},
        test_execution=run,
    )


def _hosted_calls(organization, workspace, run, scenario, count, level, **fields):
    job = _job(organization, workspace, run, level)
    source = HostedHarnessScenario.no_workspace_objects.create(
        job=job, scenario_key="refund", name="Refund", scenario=scenario
    )
    calls = _calls(run, scenario, count, **fields)
    for index, call in enumerate(calls):
        HostedHarnessExecution.objects.create(
            job=job,
            source_scenario=source,
            execution_key=f"{job.id}-{index}",
            trial_index=index,
            call_execution=call,
        )
    return calls


TOOLS = Question(
    name="Did it look the order up?",
    kinds=(Kind.TOOL_CALLS,),
    decision_model="jev-latest",
)


@pytest.mark.django_db
def test_a_run_kept_at_metadata_only_is_unmeasured_without_any_evaluator_call(
    organization, workspace, run, agent_definition, simulator_agent, scenario
):
    """Pass line 2: ten calls from a run that kept traces, two from one that did not."""
    traced = _hosted_calls(
        organization,
        workspace,
        run,
        scenario,
        10,
        "traces",
        provider_call_data={"livekit": {"tool_calls": [{"name": "lookup_order"}]}},
    )
    second = _run(agent_definition, simulator_agent, scenario)
    bare = _hosted_calls(
        organization,
        workspace,
        second,
        scenario,
        2,
        "metadata-only",
        provider_call_data={"livekit": {}},
    )
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run, second])

    with patch("model_hub.views.utils.evals.run_eval_func") as evaluator:
        check = check_before_launch(
            frozen, [TOOLS], organization=organization, workspace_id=workspace.id
        )

    evaluator.assert_not_called()
    (question,) = check["questions"]
    assert (question["ready"], question["unmeasured"], question["not_applicable"]) == (
        10,
        2,
        0,
    )
    assert question["by_kind"]["tool_calls"] == {
        "present": 10,
        "not_applicable": 0,
        "not_captured": {Cause.LEVEL_FORBIDS.value: 2},
    }
    assert check["requested"] == len(traced) + len(bare)
    assert check["answers"] == 10


@pytest.mark.django_db
def test_every_kind_a_question_lists_is_required(
    organization, workspace, run, scenario
):
    _calls(run, scenario, 2, recording_url="https://example.com/r.mp3")
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])
    question = Question(
        name="Was it polite?",
        kinds=(Kind.AUDIO, Kind.TRANSCRIPT),
        decision_model="jev-latest",
    )

    check = check_before_launch(
        frozen, [question], organization=organization, workspace_id=workspace.id
    )

    (counted,) = check["questions"]
    assert counted["by_kind"]["audio"]["present"] == 2
    assert counted["by_kind"]["transcript"]["not_captured"] == {"no_record": 2}
    assert (counted["ready"], counted["unmeasured"]) == (0, 2)


@pytest.mark.django_db
def test_a_kind_that_does_not_apply_makes_the_call_not_applicable(
    organization, workspace, run, scenario
):
    _calls(run, scenario, 1, recording_url="https://example.com/r.mp3")
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])
    question = Question(
        name="Did the world change?",
        kinds=(Kind.AUDIO, Kind.WORLD_CHANGES),
        decision_model="jev-latest",
    )

    check = check_before_launch(
        frozen, [question], organization=organization, workspace_id=workspace.id
    )

    (counted,) = check["questions"]
    assert (counted["ready"], counted["unmeasured"], counted["not_applicable"]) == (
        0,
        0,
        1,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("count", [0, MAX_QUESTIONS + 1])
def test_an_ask_takes_one_to_five_questions(
    organization, workspace, run, scenario, count
):
    _calls(run, scenario, 1)
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])

    with pytest.raises(LaunchRefused) as refused:
        check_before_launch(
            frozen,
            [TOOLS] * count,
            organization=organization,
            workspace_id=workspace.id,
        )

    assert refused.value.code == "questions"


@pytest.mark.django_db
def test_a_question_that_reads_nothing_is_refused(
    organization, workspace, run, scenario
):
    _calls(run, scenario, 1)
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])

    with pytest.raises(LaunchRefused) as refused:
        check_before_launch(
            frozen,
            [Question(name="?", kinds=(), decision_model="jev-latest")],
            organization=organization,
            workspace_id=workspace.id,
        )

    assert refused.value.code == "questions"


@pytest.mark.django_db
def test_each_saved_contract_and_seed_is_read_once_for_the_whole_check(
    organization, workspace, run, scenario
):
    from simulate.services import call_evidence

    _calls(run, scenario, 3)
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])
    question = Question(
        name="Instructions?",
        kinds=(Kind.AGENT_INSTRUCTIONS,),
        decision_model="jev-latest",
    )
    run.agent_version = run.agent_definition.create_version(
        description="v1", commit_message="v1"
    )
    run.save()

    with patch.object(
        call_evidence, "stored_version_contract", return_value=None
    ) as contract:
        check_before_launch(
            frozen, [question], organization=organization, workspace_id=workspace.id
        )

    contract.assert_called_once()

    # Every trial of one hosted run starts from the same sealed seed.
    for call in CallExecution.objects.filter(id__in=frozen.call_ids):
        call.call_metadata = {
            "harness_trial_state": {
                "recorded": True,
                "start": {"state": "seeded", "against": "sealed_seed"},
            }
        }
        call.save(update_fields=["call_metadata"])
    trial = call_evidence.HostedTrial(
        job_id=uuid.uuid4(),
        connector="phone",
        artifact_level="traces",
        attempts=1,
        trials=1,
        environment_version_id=uuid.uuid4(),
    )
    start = Question(
        name="Start?", kinds=(Kind.WORLD_STATE_START,), decision_model="jev-latest"
    )
    with (
        patch.object(call_evidence, "hosted_trial", return_value=trial),
        patch.object(
            call_evidence,
            "_environment_files",
            return_value={"store.json": b'{"rows": {"orders": []}}'},
        ) as seed,
    ):
        check = check_before_launch(
            frozen, [start], organization=organization, workspace_id=workspace.id
        )

    seed.assert_called_once_with(trial.environment_version_id)
    assert check["questions"][0]["by_kind"]["world_state_start"]["present"] == 3


@pytest.mark.django_db
@pytest.mark.parametrize("how", ["soft", "hard"])
def test_a_call_deleted_since_freezing_refuses_the_check(
    organization, workspace, run, scenario, how
):
    calls = _calls(run, scenario, 2)
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])
    if how == "soft":
        CallExecution.objects.filter(id=calls[1].id).update(deleted=True)
    else:
        CallExecution.objects.filter(id=calls[1].id).delete()

    with pytest.raises(LaunchRefused) as refused:
        check_before_launch(
            frozen, [TOOLS], organization=organization, workspace_id=workspace.id
        )

    assert refused.value.code == "calls_gone"
    assert refused.value.message == (
        "1 of the 2 selected calls has been deleted since the ask was made."
    )


@pytest.mark.django_db
def test_credits_are_estimated_on_the_calls_that_will_be_answered(
    organization, workspace, run, scenario
):
    _calls(run, scenario, 4, recording_url="https://example.com/r.mp3")
    _calls(run, scenario, 2)
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])
    audio = Question(name="Audio?", kinds=(Kind.AUDIO,), decision_model="jev-latest")

    with patch("tracer.services.instant_eval_run.credits_per_answer", return_value=0.5):
        check = check_before_launch(
            frozen, [audio, audio], organization=organization, workspace_id=workspace.id
        )

    assert check["answers"] == 8
    assert check["credits_estimate"] == 4.0


# --- the gate: Instant Eval's own, before anything is spent ---------------------


def _gate(organization, workspace, models=("jev-latest",), answers=10):
    questions = [
        Question(name="q", kinds=(Kind.TRANSCRIPT,), decision_model=m) for m in models
    ]
    return launch_gate(
        organization=organization,
        workspace_id=workspace.id,
        questions=questions,
        answers=answers,
    )


@pytest.fixture
def billing():
    with (
        patch("tracer.services.instant_eval_run.credits_per_answer", return_value=0.5),
        patch(
            "tracer.services.instant_eval_run.check_credits", return_value=None
        ) as check,
    ):
        yield check


@pytest.fixture
def entitled():
    with patch("tfc.ee_gating.check_ee_feature") as gate:
        yield gate


@pytest.mark.django_db
def test_an_organisation_without_instant_eval_is_refused_first(
    organization, workspace, billing
):
    from tfc.ee_gating import EEFeature, FeatureUnavailable

    with (
        patch(
            "tfc.ee_gating.check_ee_feature",
            side_effect=FeatureUnavailable(EEFeature.INSTANT_EVAL),
        ) as gate,
        patch("evaluations.engine.instance._get_typesafe_api_key") as key,
        pytest.raises(LaunchRefused) as refused,
    ):
        _gate(organization, workspace)

    gate.assert_called_once_with(EEFeature.INSTANT_EVAL, org_id=str(organization.id))
    assert refused.value.code == "ENTITLEMENT_DENIED"
    key.assert_not_called()
    billing.assert_not_called()


@pytest.mark.django_db
def test_a_missing_key_is_refused_before_launch_and_nothing_is_charged(
    organization, workspace, entitled, billing
):
    """Pass line 13."""
    from agentic_eval.core_evals.run_prompt.litellm_models import (
        MissingProviderKeyError,
    )

    with (
        patch(
            "evaluations.engine.instance._get_typesafe_api_key",
            side_effect=MissingProviderKeyError("typesafe"),
        ),
        patch("tracer.ee_boundary.check_usage") as usage,
        pytest.raises(LaunchRefused) as refused,
    ):
        _gate(organization, workspace)

    assert refused.value.code == "JEV_KEY_MISSING"
    assert refused.value.details["settings_path"] == "/dashboard/settings/ai-providers"
    usage.assert_not_called()
    billing.assert_not_called()


@pytest.mark.django_db
def test_another_decision_models_missing_key_is_refused(
    organization, workspace, entitled, billing
):
    from ee.decisions.errors import DecisionGatewayError
    from ee.jev.client import JevGatewayError
    from evaluations.decision_models import DECISION_MODELS

    model = next(name for name in DECISION_MODELS if "jev" not in name)
    with (
        patch(
            "evaluations.engine.instance._get_decision_api_key",
            side_effect=DecisionGatewayError(
                "openrouter", JevGatewayError("JEV_KEY_MISSING")
            ),
        ) as key,
        pytest.raises(LaunchRefused) as refused,
    ):
        _gate(organization, workspace, models=(model,))

    assert key.call_args.args[0] == model
    assert refused.value.code == "DECISION_KEY_MISSING"
    billing.assert_not_called()


@pytest.mark.django_db
def test_an_unknown_decision_model_is_refused(
    organization, workspace, entitled, billing
):
    with pytest.raises(LaunchRefused) as refused:
        _gate(organization, workspace, models=("no-such-model",))

    assert refused.value.code == "unknown_decision_model"


@pytest.mark.django_db
def test_the_key_is_read_as_the_eval_runner_reads_it_and_never_kept(
    organization, workspace, user, entitled, billing
):
    from tfc.middleware.workspace_context import (
        get_current_workspace,
        set_workspace_context,
    )

    seen = []
    elsewhere = Workspace.objects.create(
        name=f"ws-{uuid.uuid4().hex[:6]}", organization=organization, created_by=user
    )

    def key(org_id, workspace_id=None):
        seen.append((org_id, workspace_id, get_current_workspace()))
        return "sk-secret-value"

    before = get_current_workspace()
    set_workspace_context(workspace=elsewhere)
    try:
        with patch(
            "evaluations.engine.instance._get_typesafe_api_key", side_effect=key
        ):
            result = _gate(organization, workspace)
        after = get_current_workspace()
    finally:
        set_workspace_context(workspace=before)

    assert seen == [(str(organization.id), str(workspace.id), None)]
    assert after == elsewhere
    assert "sk-secret-value" not in repr(result)


@pytest.mark.django_db
def test_credits_the_organisation_lacks_are_refused(organization, workspace, entitled):
    from types import SimpleNamespace

    with (
        patch("evaluations.engine.instance._get_typesafe_api_key", return_value="k"),
        patch("tracer.services.instant_eval_run.credits_per_answer", return_value=0.5),
        patch(
            "tracer.services.instant_eval_run.check_credits",
            return_value=SimpleNamespace(error_code="BUDGET_PAUSED", allowed=False),
        ) as check,
        pytest.raises(LaunchRefused) as refused,
    ):
        _gate(organization, workspace, answers=10)

    check.assert_called_once_with(organization, 5.0)
    assert refused.value.code == "BUDGET_PAUSED"
    assert "paused" in refused.value.message


@pytest.mark.django_db
def test_credits_that_cannot_be_checked_refuse_the_launch(
    organization, workspace, entitled
):
    from tracer.services.instant_eval_run import CreditCheckUnavailable

    with (
        patch("evaluations.engine.instance._get_typesafe_api_key", return_value="k"),
        patch(
            "tracer.services.instant_eval_run.credits_per_answer",
            side_effect=CreditCheckUnavailable(),
        ),
        pytest.raises(LaunchRefused) as refused,
    ):
        _gate(organization, workspace)

    assert refused.value.code == "credit_check_unavailable"


@pytest.mark.django_db
def test_a_launch_that_passes_every_check_is_allowed(
    organization, workspace, entitled, billing
):
    with patch("evaluations.engine.instance._get_typesafe_api_key", return_value="k"):
        assert _gate(organization, workspace, answers=10) is None

    billing.assert_called_once_with(organization, 5.0)


# --- refusals, never raw errors (review of the check and the gate) ----------------


@pytest.mark.django_db
@pytest.mark.parametrize("deleted", ["run", "run_test"])
def test_calls_whose_run_was_deleted_since_freezing_count_as_gone(
    organization, workspace, run, scenario, deleted
):
    _calls(run, scenario, 2)
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])
    if deleted == "run":
        TestExecution.objects.filter(id=run.id).update(deleted=True)
    else:
        RunTest.objects.filter(id=run.run_test_id).update(deleted=True)

    with pytest.raises(LaunchRefused) as refused:
        check_before_launch(
            frozen, [TOOLS], organization=organization, workspace_id=workspace.id
        )

    assert refused.value.code == "calls_gone"
    assert refused.value.message == (
        "2 of the 2 selected calls have been deleted since the ask was made."
    )


@pytest.mark.django_db
def test_a_selection_checked_from_another_workspace_is_refused_as_not_found(
    organization, workspace, user, run, scenario
):
    _calls(run, scenario, 1)
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])
    elsewhere = Workspace.objects.create(
        name=f"ws-{uuid.uuid4().hex[:6]}", organization=organization, created_by=user
    )

    with pytest.raises(LaunchRefused) as refused:
        check_before_launch(
            frozen, [TOOLS], organization=organization, workspace_id=elsewhere.id
        )

    assert (refused.value.code, refused.value.message) == ("not_found", NOT_FOUND)


@pytest.mark.django_db
def test_a_launch_with_no_ready_answer_is_refused_before_anything_is_asked(
    organization, workspace, entitled, billing
):
    with (
        patch("evaluations.engine.instance._get_typesafe_api_key") as key,
        pytest.raises(LaunchRefused) as refused,
    ):
        _gate(organization, workspace, answers=0)

    assert refused.value.code == "nothing_ready"
    assert refused.value.message == (
        "No selected call has the evidence these questions read."
    )
    key.assert_not_called()
    billing.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("kind", [SelectionKind.RUNS, SelectionKind.FILTER])
def test_too_many_calls_are_refused_before_they_are_loaded(
    organization, workspace, run, scenario, kind
):
    calls = _calls(run, scenario, 3)
    picked = (
        {"filters": {"call_execution_id": [str(call.id) for call in calls]}}
        if kind == SelectionKind.FILTER
        else {}
    )

    with (
        patch.object(selection, "MAX_CALLS", 2),
        patch.object(selection, "_calls_of", wraps=selection._calls_of) as loaded,
        pytest.raises(SelectionRefused) as refused,
    ):
        _freeze(organization, workspace, kind, [run], **picked)

    assert refused.value.code == "too_many_calls"
    assert refused.value.message == (
        "An ask can take at most 2 calls; this selection has 3."
    )
    loaded.assert_not_called()


@pytest.mark.django_db
def test_a_run_moved_to_another_workspace_since_freezing_is_refused_as_not_found(
    organization, workspace, user, run, scenario
):
    _calls(run, scenario, 1)
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])
    elsewhere = Workspace.objects.create(
        name=f"ws-{uuid.uuid4().hex[:6]}", organization=organization, created_by=user
    )
    RunTest.objects.filter(id=run.run_test_id).update(workspace=elsewhere)

    with pytest.raises(LaunchRefused) as refused:
        check_before_launch(
            frozen, [TOOLS], organization=organization, workspace_id=workspace.id
        )

    assert (refused.value.code, refused.value.message) == ("not_found", NOT_FOUND)


# --- round 1 review ------------------------------------------------------------------


EVERY_KIND = Question(
    name="Everything?", kinds=tuple(Kind), decision_model="jev-latest"
)


@pytest.mark.django_db
@pytest.mark.parametrize("where", ["seed", "transcript"])
def test_evidence_that_cannot_be_read_just_now_refuses_the_check_with_a_retry(
    organization, workspace, run, scenario, where
):
    from simulate.services import call_evidence

    calls = _calls(run, scenario, 2)
    for call in calls:
        call.call_metadata = {
            "harness_trial_state": {
                "recorded": True,
                "start": {"state": "seeded", "against": "sealed_seed"},
            }
        }
        call.save(update_fields=["call_metadata"])
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])
    trial = call_evidence.HostedTrial(
        job_id=uuid.uuid4(),
        connector="phone",
        artifact_level="traces",
        attempts=1,
        trials=1,
        environment_version_id=uuid.uuid4(),
    )
    kind = Kind.WORLD_STATE_START if where == "seed" else Kind.TRANSCRIPT

    def down(*_):
        raise ConnectionError("storage is down")

    with (
        patch.object(call_evidence, "hosted_trial", return_value=trial),
        patch.object(call_evidence, "_environment_files", side_effect=down),
        patch.dict(call_evidence._READERS, {Kind.TRANSCRIPT: down}),
        pytest.raises(LaunchRefused) as refused,
    ):
        check_before_launch(
            frozen,
            [Question(name="q", kinds=(kind,), decision_model="jev-latest")],
            organization=organization,
            workspace_id=workspace.id,
        )

    assert refused.value.code == "evidence_unavailable"
    assert refused.value.details == {"calls": 2}


@pytest.mark.django_db
def test_the_check_downloads_no_world_file_and_reads_no_trace(
    organization, workspace, run, scenario
):
    from simulate.models.hosted_harness import HostedHarnessArtifact
    from simulate.services import call_evidence

    calls = _hosted_calls(organization, workspace, run, scenario, 2, "traces")
    job = HostedHarnessExecution.objects.get(call_execution=calls[0]).job
    for call in calls:
        call.call_metadata = {
            "harness_trial_state": {"end_state_artifact": "sha256:" + "c" * 64}
        }
        call.save(update_fields=["call_metadata"])
    HostedHarnessArtifact.no_workspace_objects.create(
        job=job,
        sha256="c" * 64,
        kind="world_state",
        size=10,
        content_type="application/json",
        object_key="end/key",
    )
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])
    question = Question(
        name="End?",
        kinds=(Kind.WORLD_STATE_END, Kind.AGENT_TRACES),
        decision_model="jev-latest",
    )
    storage = type("S", (), {"get_object": lambda *_: pytest.fail("fetched")})()

    with (
        patch("tfc.utils.storage_client.get_storage_client", return_value=storage),
        patch.object(call_evidence, "run_traces_source", return_value=object()),
        patch.object(
            call_evidence, "read_run_traces", side_effect=AssertionError("read")
        ),
    ):
        check = check_before_launch(
            frozen, [question], organization=organization, workspace_id=workspace.id
        )

    (counted,) = check["questions"]
    assert counted["by_kind"]["world_state_end"]["present"] == 2
    assert counted["by_kind"]["agent_traces"]["present"] == 2
    assert counted["ready"] == 2


@pytest.mark.django_db
def test_what_every_call_of_a_run_shares_is_read_once(
    organization, workspace, run, scenario
):
    from simulate.services import call_evidence

    _hosted_calls(organization, workspace, run, scenario, 3, "traces")
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])

    with patch.object(call_evidence, "run_traces_source", return_value=None) as source:
        check_before_launch(
            frozen,
            [Question(name="q", kinds=(Kind.AGENT_TRACES,), decision_model="m")],
            organization=organization,
            workspace_id=workspace.id,
        )

    source.assert_called_once()


def _queries_for(organization, workspace, run, scenario, count):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _hosted_calls(
        organization,
        workspace,
        run,
        scenario,
        count,
        "traces",
        provider_call_data={"livekit": {"tool_calls": [{"name": "lookup"}]}},
    )
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])
    with CaptureQueriesContext(connection) as queries:
        check_before_launch(
            frozen, [EVERY_KIND], organization=organization, workspace_id=workspace.id
        )
    return len(queries)


# Each further call of a hosted run reads only its own rows (its trial and its
# transcript here); what the run's calls share (the job's counts, the trace source,
# the agent version, the scenario) is not read again. It was 13 before the memo.
QUERIES_PER_CALL = 2


@pytest.mark.django_db
def test_a_hosted_runs_check_costs_a_few_queries_per_call(
    organization, workspace, agent_definition, simulator_agent, scenario
):
    first = _run(agent_definition, simulator_agent, scenario)
    second = _run(agent_definition, simulator_agent, scenario)

    few = _queries_for(organization, workspace, first, scenario, 2)
    many = _queries_for(organization, workspace, second, scenario, 8)

    assert (many - few) / 6 <= QUERIES_PER_CALL, (few, many)


@pytest.mark.django_db
def test_an_unknown_question_kind_is_refused(organization, workspace, run, scenario):
    _calls(run, scenario, 1)
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])

    with pytest.raises(LaunchRefused) as refused:
        check_before_launch(
            frozen,
            [Question(name="q", kinds=("bogus",), decision_model="jev-latest")],
            organization=organization,
            workspace_id=workspace.id,
        )

    assert refused.value.code == "questions"


@pytest.mark.django_db
def test_an_unknown_selection_kind_is_refused(organization, workspace, run):
    with pytest.raises(SelectionRefused) as refused:
        _freeze(organization, workspace, "bogus", [run])

    assert refused.value.code == "bad_kind"


@pytest.mark.django_db
@pytest.mark.parametrize("how", ["calls", "scenarios", "runs"])
def test_too_many_chosen_calls_scenarios_or_runs_are_refused_before_loading(
    organization, workspace, run, scenario, how
):
    calls = _calls(run, scenario, 3)
    picked = {
        "calls": {"call_ids": [str(call.id) for call in calls]},
        "scenarios": {"scenario_ids": [str(scenario.id)]},
        "runs": {},
    }[how]
    runs = [run] if how != "runs" else [run, run, run]
    kind = {
        "calls": SelectionKind.CALLS,
        "scenarios": SelectionKind.SCENARIOS,
        "runs": SelectionKind.RUNS,
    }[how]

    with (
        patch.object(selection, "MAX_CALLS", 2),
        patch.object(selection, "_calls_of", side_effect=AssertionError("loaded")),
        pytest.raises(SelectionRefused) as refused,
    ):
        freeze_selection(
            kind=kind,
            organization_id=organization.id,
            workspace_id=workspace.id,
            run_ids=[str(one.id) for one in runs]
            if how != "runs"
            else [str(uuid.uuid4()) for _ in range(3)],
            **picked,
        )

    assert refused.value.code == (
        "too_many_runs" if how == "runs" else "too_many_calls"
    )


@pytest.mark.django_db
def test_more_chosen_ids_than_an_ask_takes_are_refused_before_they_are_read(
    organization, workspace, run
):
    with (
        patch.object(selection, "MAX_CALLS", 2),
        pytest.raises(SelectionRefused) as refused,
    ):
        _freeze(
            organization,
            workspace,
            SelectionKind.CALLS,
            [run],
            call_ids=["not-an-id"] * 2 + ["another"],
        )

    assert refused.value.code == "too_many_calls"


@pytest.mark.django_db
def test_a_gate_with_no_question_is_refused_before_any_key_is_read(
    organization, workspace, entitled, billing
):
    with (
        patch.object(selection, "_check_key", side_effect=AssertionError("key")),
        pytest.raises(LaunchRefused) as refused,
    ):
        launch_gate(
            organization=organization,
            workspace_id=workspace.id,
            questions=[],
            answers=5,
        )

    assert refused.value.code == "questions"


@pytest.mark.django_db
def test_a_kind_listed_twice_is_counted_once(organization, workspace, run, scenario):
    _calls(run, scenario, 3, recording_url="https://example.com/r.mp3")
    frozen = _freeze(organization, workspace, SelectionKind.RUNS, [run])

    check = check_before_launch(
        frozen,
        [Question(name="q", kinds=(Kind.AUDIO, Kind.AUDIO), decision_model="m")],
        organization=organization,
        workspace_id=workspace.id,
    )

    (counted,) = check["questions"]
    assert counted["by_kind"]["audio"]["present"] == 3
    assert counted["kinds"] == ["audio"]


@pytest.mark.django_db
def test_a_billing_refusal_with_no_message_of_ours_keeps_billings_reason(
    organization, workspace, entitled
):
    from types import SimpleNamespace

    with (
        patch("evaluations.engine.instance._get_typesafe_api_key", return_value="k"),
        patch("tracer.services.instant_eval_run.credits_per_answer", return_value=0.5),
        patch(
            "tracer.services.instant_eval_run.check_credits",
            return_value=SimpleNamespace(
                error_code="BUDGET_LIMIT",
                allowed=False,
                reason="Your monthly budget for evals is used up.",
            ),
        ),
        pytest.raises(LaunchRefused) as refused,
    ):
        _gate(organization, workspace, answers=10)

    assert refused.value.code == "BUDGET_LIMIT"
    assert refused.value.message == "Your monthly budget for evals is used up."


@pytest.mark.django_db
def test_the_description_keeps_the_filter_as_validated(
    organization, workspace, run, scenario
):
    calls = _calls(run, scenario, 1)

    frozen = _freeze(
        organization,
        workspace,
        SelectionKind.FILTER,
        [run],
        filters=json.dumps({"call_execution_id": [str(calls[0].id)]}),
    )

    assert frozen.description["filters"] == {"call_execution_id": [str(calls[0].id)]}


def test_the_service_reads_the_filter_serializer_from_the_serializers_package():
    import inspect

    from simulate.serializers.run_call_filters import RunCallFiltersSerializer
    from simulate.views import run_results_v3

    assert run_results_v3.RunCallFiltersSerializer is RunCallFiltersSerializer
    assert "simulate.views" not in inspect.getsource(selection)


@pytest.mark.django_db
def test_the_workspace_is_put_back_after_a_refused_key_read(
    organization, workspace, user, entitled, billing
):
    from agentic_eval.core_evals.run_prompt.litellm_models import (
        MissingProviderKeyError,
    )
    from tfc.middleware.workspace_context import (
        get_current_workspace,
        set_workspace_context,
    )

    elsewhere = Workspace.objects.create(
        name=f"ws-{uuid.uuid4().hex[:6]}", organization=organization, created_by=user
    )
    before = get_current_workspace()
    set_workspace_context(workspace=elsewhere)
    try:
        with (
            patch(
                "evaluations.engine.instance._get_typesafe_api_key",
                side_effect=MissingProviderKeyError("openai"),
            ),
            pytest.raises(LaunchRefused),
        ):
            _gate(organization, workspace)
        after = get_current_workspace()
    finally:
        set_workspace_context(workspace=before)

    assert after == elsewhere


def test_the_open_service_imports_nothing_from_ee():
    import inspect

    assert "from ee" not in inspect.getsource(selection)
