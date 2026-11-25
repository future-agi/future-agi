"""The reader: one place that turns a saved call into evidence for a judge.

Ten kinds, each from one named source. For each call and kind the reader says
``present`` (and hands it over, with the source read and a digest of exactly
what it read), ``not_captured`` with a cause taken from stored data only, or
``not_applicable``. It fills nothing in, never cuts instructions, says every
cut it makes, and refuses a call from another organisation or workspace.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from unittest.mock import patch

import pytest

from model_hub.models.choices import DatasetSourceChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from simulate.models import AgentDefinition, Scenarios
from simulate.models.chat_message import ChatMessageModel
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, CallTranscript, TestExecution
from simulate.services import call_evidence as reader
from simulate.services.call_evidence import (
    EMPTY_TOOL_RECORD,
    Cause,
    Kind,
    RunKind,
    State,
    read_call_evidence,
    run_kind_of,
)
from simulate.services.canonical_json import canonical_digest

# --- fixtures (copied, not imported, from the other simulate tests) ------------


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="You are a refunds agent. Never refund over 100.",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


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
def scenario(db, organization, user, workspace, agent_definition):
    dataset = Dataset.no_workspace_objects.create(
        name="Scenario rows",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    return Scenarios.objects.create(
        name="Refund over the limit",
        description="A caller asks for a refund of 150.",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    run_test = RunTest.objects.create(
        name="Run",
        description="Run under test",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    return run_test


@pytest.fixture
def agent_version(db, agent_definition):
    return agent_definition.create_version(
        description="You are a refunds agent. Never refund over 100.",
        commit_message="v1",
    )


@pytest.fixture
def execution(db, run_test, simulator_agent, agent_definition, agent_version, scenario):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        agent_version=agent_version,
        scenario_ids=[str(scenario.id)],
    )


def _call(execution, scenario, **fields):
    defaults = {
        "phone_number": "+15551230000",
        "status": CallExecution.CallStatus.COMPLETED,
        "simulation_call_type": CallExecution.SimulationCallType.VOICE,
    }
    return CallExecution.objects.create(
        test_execution=execution, scenario=scenario, **{**defaults, **fields}
    )


@pytest.fixture
def voice_call(execution, scenario):
    return _call(
        execution,
        scenario,
        provider_call_data={"vapi": {"id": "v1"}},
        call_metadata={"call_direction": "outbound"},
        recording_url="https://example.com/r.mp3",
    )


@pytest.fixture
def text_call(execution, scenario):
    return _call(
        execution,
        scenario,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        phone_number=None,
    )


def _turn(call, role, content, at):
    return CallTranscript.objects.create(
        call_execution=call,
        speaker_role=role,
        content=content,
        start_time_ms=at,
        end_time_ms=at + 500,
    )


def _message(call, role, *, messages=(), content=None):
    return ChatMessageModel.objects.create(
        call_execution=call,
        role=role,
        messages=list(messages),
        content=content if content is not None else [],
        session_id="s1",
        organization=call.test_execution.run_test.organization,
        workspace=call.test_execution.run_test.workspace,
    )


def _read(call, *kinds, **options):
    run_test = call.test_execution.run_test
    return read_call_evidence(
        call,
        kinds,
        organization_id=run_test.organization_id,
        workspace_id=run_test.workspace_id,
        **options,
    )


def _one(call, kind, **options):
    return _read(call, kind, **options)[kind]


# A hosted trial, as the reader sees it once it has found the call's job.
HOSTED = reader.HostedTrial(
    job_id=uuid.uuid4(),
    connector="phone",
    artifact_level="traces",
    attempts=1,
    trials=1,
    environment_version_id=None,
)


def _hosted(**changes):
    return patch.object(reader, "hosted_trial", return_value=replace(HOSTED, **changes))


# --- the kind of run decides the sources --------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("provider_call_data", "call_type", "hosted", "expected"),
    [
        ({"vapi": {"id": "v"}}, "voice", None, RunKind.PROVIDER_VOICE_VAPI),
        ({"retell": {"id": "r"}}, "voice", None, RunKind.PROVIDER_VOICE_RETELL),
        ({"bland": {"id": "b"}}, "voice", None, RunKind.PROVIDER_VOICE_OTHER),
        ({}, "text", None, RunKind.CHAT),
        ({"livekit": {}}, "text", HOSTED, RunKind.HOSTED_CHAT),
        ({"livekit": {}}, "voice", HOSTED, RunKind.HOSTED_VOICE),
    ],
)
def test_each_call_has_one_kind_of_run(
    execution, scenario, provider_call_data, call_type, hosted, expected
):
    call = _call(
        execution,
        scenario,
        provider_call_data=provider_call_data,
        simulation_call_type=call_type,
    )
    with patch.object(reader, "hosted_trial", return_value=hosted):
        assert run_kind_of(call) == expected


# --- tenant check at every read -----------------------------------------------


@pytest.mark.django_db
def test_a_call_from_another_workspace_or_organisation_is_refused(voice_call):
    run_test = voice_call.test_execution.run_test
    with pytest.raises(reader.CallNotInWorkspace):
        read_call_evidence(
            voice_call,
            [Kind.TRANSCRIPT],
            organization_id=run_test.organization_id,
            workspace_id=uuid.uuid4(),
        )
    with pytest.raises(reader.CallNotInWorkspace):
        read_call_evidence(
            voice_call,
            [Kind.TRANSCRIPT],
            organization_id=uuid.uuid4(),
            workspace_id=run_test.workspace_id,
        )


# --- transcript ----------------------------------------------------------------


@pytest.mark.django_db
def test_transcript_is_turns_only_each_with_its_speaker(voice_call):
    _turn(voice_call, "assistant", "Hello, refunds desk.", 0)
    _turn(voice_call, "tool_calls", '{"name": "lookup"}', 600)
    _turn(voice_call, "user", "I want 150 back.", 1200)

    evidence = _one(voice_call, Kind.TRANSCRIPT)

    assert evidence.state == State.PRESENT
    assert evidence.source == "call_transcript"
    assert evidence.content == [
        {"speaker": "agent", "text": "Hello, refunds desk."},
        {"speaker": "customer", "text": "I want 150 back."},
    ]
    assert evidence.digest == canonical_digest(evidence.content)
    assert evidence.cut is None


@pytest.mark.django_db
def test_speakers_are_not_swapped_on_an_inbound_call_with_no_provider_record(
    execution, scenario
):
    """The case left from TH-8376: a call marked inbound whose provider data
    names no provider. Its rows are stored the platform's way round."""
    call = _call(
        execution,
        scenario,
        provider_call_data={},
        call_metadata={"call_direction": "inbound"},
    )
    _turn(call, "assistant", "Refunds desk.", 0)
    _turn(call, "user", "Hi.", 600)

    evidence = _one(call, Kind.TRANSCRIPT)

    assert [turn["speaker"] for turn in evidence.content] == ["agent", "customer"]


@pytest.mark.django_db
def test_a_text_call_reads_its_chat_messages(text_call):
    _message(text_call, "user", messages=["Refund me 150."])
    _message(text_call, "assistant", messages=["I can refund up to 100."])

    evidence = _one(text_call, Kind.TRANSCRIPT)

    assert evidence.state == State.PRESENT
    assert evidence.source == "chat_messages"
    assert evidence.content == [
        {"speaker": "customer", "text": "Refund me 150."},
        {"speaker": "agent", "text": "I can refund up to 100."},
    ]


@pytest.mark.django_db
def test_a_call_that_ended_before_any_turn_has_no_transcript(voice_call):
    evidence = _one(voice_call, Kind.TRANSCRIPT)

    assert (evidence.state, evidence.cause) == (State.NOT_CAPTURED, Cause.NO_RECORD)
    assert evidence.content is None


@pytest.mark.django_db
def test_a_cut_is_made_by_the_reader_and_said(voice_call):
    _turn(voice_call, "assistant", "a" * 300, 0)
    _turn(voice_call, "user", "b" * 300, 600)

    evidence = _one(voice_call, Kind.TRANSCRIPT, max_chars=200)

    text = json.dumps(evidence.content)
    assert len(evidence.content) <= 2
    assert "cut by the reader" in text
    assert evidence.cut["kept_chars"] <= 200
    assert evidence.cut["total_chars"] > 200
    assert evidence.digest == canonical_digest(evidence.content)


# --- instructions: whole sources only, never cut ----------------------------


@pytest.mark.django_db
def test_instructions_come_whole_from_the_agent_version(voice_call, agent_version):
    evidence = _one(voice_call, Kind.AGENT_INSTRUCTIONS)

    assert evidence.state == State.PRESENT
    assert evidence.source == "agent_version"
    assert evidence.content == "You are a refunds agent. Never refund over 100."


@pytest.mark.django_db
def test_no_whole_instructions_is_said_and_nothing_else_is_read(
    voice_call, agent_version
):
    """Pass line 12, first half."""
    agent_version.configuration_snapshot = {
        **agent_version.configuration_snapshot,
        "description": "",
    }
    agent_version.description = ""
    agent_version.save()
    voice_call.call_metadata = {"agent_prompt": "a fragment of the prompt"}
    voice_call.save()

    evidence = _one(voice_call, Kind.AGENT_INSTRUCTIONS)

    assert (evidence.state, evidence.cause) == (
        State.NOT_CAPTURED,
        Cause.NO_WHOLE_INSTRUCTIONS,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("connector", ["vapi", "retell", "retell_chat"])
def test_a_hosted_provider_agent_has_no_whole_instructions(voice_call, connector):
    """Its stored prompt may be the contract's excerpt."""
    with _hosted(connector=connector):
        evidence = _one(voice_call, Kind.AGENT_INSTRUCTIONS)

    assert evidence.cause == Cause.NO_WHOLE_INSTRUCTIONS


@pytest.mark.django_db
def test_instructions_that_would_not_reach_the_model_whole_are_not_cut(voice_call):
    evidence = _one(voice_call, Kind.AGENT_INSTRUCTIONS, max_chars=10)

    assert (evidence.state, evidence.cause) == (State.NOT_CAPTURED, Cause.TOO_LARGE)
    assert evidence.content is None


# --- scenario ----------------------------------------------------------------


@pytest.mark.django_db
def test_scenario_is_the_authored_scenario(voice_call, scenario):
    evidence = _one(voice_call, Kind.SCENARIO)

    assert evidence.state == State.PRESENT
    assert evidence.source == "scenario"
    assert evidence.content["name"] == "Refund over the limit"
    assert evidence.content["description"] == "A caller asks for a refund of 150."


# --- tool calls: one source for each kind of run ------------------------------


@pytest.mark.django_db
def test_vapi_tool_calls_are_the_transcript_rows_with_tool_roles(voice_call):
    _turn(voice_call, "assistant", "Let me check.", 0)
    _turn(voice_call, "tool_calls", '[{"name": "lookup_order"}]', 600)
    _turn(voice_call, "tool_call_result", '{"amount": 150}', 900)

    evidence = _one(voice_call, Kind.TOOL_CALLS)

    assert evidence.state == State.PRESENT
    assert evidence.source == "call_transcript_tool_rows"
    assert [row["role"] for row in evidence.content] == [
        "tool_calls",
        "tool_call_result",
    ]


@pytest.mark.django_db
def test_vapi_turns_with_no_tool_rows_are_an_empty_record(voice_call):
    """Pass line 11: an empty record is present, sent as a plain statement."""
    _turn(voice_call, "assistant", "Hello.", 0)

    evidence = _one(voice_call, Kind.TOOL_CALLS)

    assert evidence.state == State.PRESENT
    assert evidence.content == EMPTY_TOOL_RECORD
    assert evidence.content.strip()


@pytest.mark.django_db
def test_no_tool_record_is_not_none_called(voice_call):
    """Pass line 11: no record is not_captured, never an empty value."""
    evidence = _one(voice_call, Kind.TOOL_CALLS)

    assert (evidence.state, evidence.cause) == (State.NOT_CAPTURED, Cause.NO_RECORD)


@pytest.mark.django_db
def test_tool_calls_are_never_worked_out_from_the_transcript(voice_call):
    _turn(voice_call, "assistant", "I called lookup_order and it said 150.", 0)

    evidence = _one(voice_call, Kind.TOOL_CALLS)

    assert evidence.content == EMPTY_TOOL_RECORD


@pytest.mark.django_db
def test_retell_tool_calls_come_only_from_its_own_transcript(execution, scenario):
    call = _call(
        execution,
        scenario,
        provider_call_data={
            "retell": {
                "transcript_with_tool_calls": [
                    {"role": "agent", "content": "One moment."},
                    {"role": "tool_call_invocation", "name": "lookup_order"},
                    {"role": "tool_call_result", "content": "150"},
                ]
            }
        },
    )
    _turn(call, "unknown", "lookup_order", 0)

    evidence = _one(call, Kind.TOOL_CALLS)

    assert evidence.source == "retell_transcript_with_tool_calls"
    assert [row["role"] for row in evidence.content] == [
        "tool_call_invocation",
        "tool_call_result",
    ]


@pytest.mark.django_db
def test_retell_with_no_tool_transcript_has_no_record(execution, scenario):
    call = _call(execution, scenario, provider_call_data={"retell": {"id": "r"}})

    assert _one(call, Kind.TOOL_CALLS).cause == Cause.NO_RECORD


LOOKUP = {
    "id": "c1",
    "type": "function",
    "function": {"name": "lookup_order", "arguments": '{"order": "7"}'},
}


@pytest.mark.django_db
def test_chat_tool_calls_are_chat_message_content(text_call):
    _message(text_call, "user", messages=["Refund 150."])
    _message(
        text_call,
        "assistant",
        content=[
            {"role": "assistant", "content": None, "tool_calls": [LOOKUP]},
            {"role": "tool", "tool_call_id": "c1", "content": "150"},
            {"role": "assistant", "content": "That order was 150."},
        ],
    )

    evidence = _one(text_call, Kind.TOOL_CALLS)

    assert evidence.state == State.PRESENT
    assert evidence.source == "chat_messages"
    assert evidence.content == [
        {"role": "assistant", "content": None, "tool_calls": [LOOKUP]},
        {"role": "tool", "tool_call_id": "c1", "content": "150"},
    ]


@pytest.mark.django_db
def test_a_chat_with_messages_and_no_tools_is_an_empty_record(text_call):
    _message(text_call, "assistant", messages=["Hello."])

    assert _one(text_call, Kind.TOOL_CALLS).content == EMPTY_TOOL_RECORD


@pytest.mark.django_db
def test_another_voice_provider_has_no_tool_source(execution, scenario):
    call = _call(execution, scenario, provider_call_data={"bland": {"id": "b"}})

    assert _one(call, Kind.TOOL_CALLS).cause == Cause.NO_RECORD


@pytest.mark.django_db
def test_hosted_tool_record_from_the_tool_trace(execution, scenario):
    call = _call(
        execution,
        scenario,
        provider_call_data={"livekit": {"tool_calls": [{"name": "lookup_order"}]}},
    )
    with _hosted():
        evidence = _one(call, Kind.TOOL_CALLS)

    assert evidence.state == State.PRESENT
    assert evidence.source == "hosted_tool_trace"
    assert evidence.content == [{"name": "lookup_order"}]
    assert evidence.marks == ()


@pytest.mark.django_db
def test_hosted_empty_tool_trace_is_an_empty_record(execution, scenario):
    call = _call(
        execution, scenario, provider_call_data={"livekit": {"tool_calls": []}}
    )
    with _hosted():
        assert _one(call, Kind.TOOL_CALLS).content == EMPTY_TOOL_RECORD


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("level", "cause"),
    [("traces", Cause.NOT_UPLOADED), ("metadata-only", Cause.LEVEL_FORBIDS)],
)
def test_hosted_without_a_tool_trace_says_why(execution, scenario, level, cause):
    call = _call(execution, scenario, provider_call_data={"livekit": {}})
    with _hosted(artifact_level=level):
        assert _one(call, Kind.TOOL_CALLS).cause == cause


@pytest.mark.django_db
@pytest.mark.parametrize(("attempts", "trials"), [(2, 1), (1, 3)])
def test_a_hosted_tool_record_may_be_from_another_attempt(
    execution, scenario, attempts, trials
):
    call = _call(
        execution,
        scenario,
        provider_call_data={"livekit": {"tool_calls": [{"name": "lookup_order"}]}},
    )
    with _hosted(attempts=attempts, trials=trials):
        evidence = _one(call, Kind.TOOL_CALLS)

    assert evidence.state == State.PRESENT
    assert "may_be_from_another_attempt" in evidence.marks


@pytest.mark.django_db
def test_hosted_chat_falls_back_to_tool_items_in_its_messages(text_call):
    _message(
        text_call,
        "assistant",
        content=[{"role": "assistant", "content": None, "tool_calls": [LOOKUP]}],
    )
    with _hosted(connector="livekit"):
        evidence = _one(text_call, Kind.TOOL_CALLS)

    assert evidence.source == "chat_messages"
    assert evidence.content != EMPTY_TOOL_RECORD


# --- world state at the start: only on a sealed seed ------------------------


@pytest.mark.django_db
def test_world_start_does_not_apply_without_an_environment(voice_call):
    assert _one(voice_call, Kind.WORLD_STATE_START).state == State.NOT_APPLICABLE


@pytest.mark.django_db
@pytest.mark.parametrize(
    "start",
    [
        {"state": "differs", "against": "sealed_seed"},
        {"state": "unknown", "against": "sealed_seed"},
        {"state": "seeded", "against": "first_trial"},
        None,
    ],
)
def test_world_start_is_only_read_from_a_sealed_seed(execution, scenario, start):
    """Pass line 12, second half."""
    trial = {"recorded": True} | ({"start": start} if start else {})
    call = _call(execution, scenario, call_metadata={"harness_trial_state": trial})
    with _hosted(environment_version_id=uuid.uuid4()):
        evidence = _one(call, Kind.WORLD_STATE_START)

    assert (evidence.state, evidence.cause) == (
        State.NOT_CAPTURED,
        Cause.START_NOT_SEALED,
    )


@pytest.mark.django_db
def test_world_start_on_a_sealed_seed_is_the_seeded_tables(execution, scenario):
    trial = {
        "recorded": True,
        "start": {"state": "seeded", "against": "sealed_seed", "tables": ["orders"]},
    }
    call = _call(execution, scenario, call_metadata={"harness_trial_state": trial})
    seed = {
        "store.json": json.dumps(
            {"rows": {"orders": [{"id": 7, "amount": 150}], "users": [{"id": 1}]}}
        ).encode()
    }
    with (
        _hosted(environment_version_id=uuid.uuid4()),
        patch.object(reader, "environment_files", return_value=seed),
    ):
        evidence = _one(call, Kind.WORLD_STATE_START)

    assert evidence.state == State.PRESENT
    assert evidence.source == "environment_seed"
    assert evidence.content == {"orders": [{"id": 7, "amount": 150}]}


# --- world state at the end ----------------------------------------------------


END = "sha256:" + "a" * 64


@pytest.mark.django_db
def test_world_end_with_no_file_for_the_trial_quotes_the_runs_notice(
    execution, scenario
):
    trial = {
        "recorded": True,
        "notices": [
            {
                "code": "evidence_incomplete",
                "message": "2 pieces of evidence were not uploaded",
            }
        ],
    }
    call = _call(execution, scenario, call_metadata={"harness_trial_state": trial})
    with _hosted():
        evidence = _one(call, Kind.WORLD_STATE_END)

    assert (evidence.state, evidence.cause) == (
        State.NOT_CAPTURED,
        Cause.NOT_UPLOADED,
    )
    assert evidence.note == "2 pieces of evidence were not uploaded"


@pytest.mark.django_db
def test_world_end_the_level_forbids(execution, scenario):
    call = _call(execution, scenario, call_metadata={"harness_trial_state": {}})
    with _hosted(artifact_level="metadata-only"):
        assert _one(call, Kind.WORLD_STATE_END).cause == Cause.LEVEL_FORBIDS


@pytest.mark.django_db
def test_world_end_that_was_kept_and_is_gone(execution, scenario):
    call = _call(
        execution,
        scenario,
        call_metadata={"harness_trial_state": {"end_state_artifact": END}},
    )
    with _hosted(), patch.object(reader, "stored_artifact", return_value=None):
        assert _one(call, Kind.WORLD_STATE_END).cause == Cause.NO_LONGER_KEPT


@pytest.mark.django_db
def test_world_end_is_read_when_it_is_the_world_state_text(execution, scenario):
    body = {
        "schema": "futureagi.world-state.v1",
        "tables": {"orders": [{"id": 7, "refunded": 150}]},
        "skipped": [],
        "action_log": [],
    }
    call = _call(
        execution,
        scenario,
        call_metadata={"harness_trial_state": {"end_state_artifact": END}},
    )
    with (
        _hosted(),
        patch.object(
            reader,
            "stored_artifact",
            return_value=json.dumps(body).encode(),
        ),
    ):
        evidence = _one(call, Kind.WORLD_STATE_END)

    assert evidence.state == State.PRESENT
    assert evidence.source == "hosted_world_state"
    assert evidence.content == body


@pytest.mark.django_db
def test_world_end_the_reader_cannot_hand_over_yet(execution, scenario):
    call = _call(
        execution,
        scenario,
        call_metadata={"harness_trial_state": {"end_state_artifact": END}},
    )
    with (
        _hosted(),
        patch.object(reader, "stored_artifact", return_value=b"SQLite format 3\x00"),
    ):
        assert _one(call, Kind.WORLD_STATE_END).cause == Cause.NOT_READABLE_YET


# --- world changes -----------------------------------------------------------


@pytest.mark.django_db
def test_world_changes_say_they_were_cut_at_ingestion(execution, scenario):
    trial = {
        "recorded": True,
        "records": {
            "changes": [{"table": "orders", "before": {"refunded": 0}}],
            "skipped": 0,
            "truncated": 3,
        },
    }
    call = _call(
        execution,
        scenario,
        call_metadata={
            "harness_trial_state": trial,
            "harness_world_actions": [{"action": "refund", "at_ms": 900}],
        },
    )
    with _hosted():
        evidence = _one(call, Kind.WORLD_CHANGES)

    assert evidence.state == State.PRESENT
    assert evidence.content["changes"] == trial["records"]["changes"]
    assert evidence.content["actions"] == [{"action": "refund", "at_ms": 900}]
    assert "cut_at_ingestion" in evidence.marks


@pytest.mark.django_db
def test_a_world_that_keeps_no_records(execution, scenario):
    call = _call(
        execution,
        scenario,
        call_metadata={"harness_trial_state": {"recorded": False}},
    )
    with _hosted():
        assert _one(call, Kind.WORLD_CHANGES).cause == Cause.WORLD_NOT_RECORDED


@pytest.mark.django_db
def test_world_changes_do_not_apply_to_a_call_with_no_environment(voice_call):
    assert _one(voice_call, Kind.WORLD_CHANGES).state == State.NOT_APPLICABLE


# --- check results, audio, agent traces -------------------------------------


@pytest.mark.django_db
def test_check_results_are_the_calls_own_verdicts(voice_call):
    voice_call.eval_outputs = {"e1": {"status": "completed", "output": "Passed"}}
    voice_call.save()

    evidence = _one(voice_call, Kind.CHECK_RESULTS)

    assert evidence.state == State.PRESENT
    assert evidence.content["verdicts"] == voice_call.eval_outputs


@pytest.mark.django_db
def test_a_call_never_graded_has_no_check_results(voice_call):
    assert _one(voice_call, Kind.CHECK_RESULTS).cause == Cause.NO_RECORD


@pytest.mark.django_db
def test_audio_is_the_calls_recordings(voice_call):
    evidence = _one(voice_call, Kind.AUDIO)

    assert evidence.state == State.PRESENT
    assert evidence.content == {"recording_url": "https://example.com/r.mp3"}


@pytest.mark.django_db
def test_audio_does_not_apply_to_a_text_call(text_call):
    assert _one(text_call, Kind.AUDIO).state == State.NOT_APPLICABLE


@pytest.mark.django_db
def test_agent_traces_only_for_a_code_agent_on_a_hosted_run(voice_call):
    with patch.object(reader, "run_traces_source", return_value=None):
        assert _one(voice_call, Kind.AGENT_TRACES).state == State.NOT_APPLICABLE


@pytest.mark.django_db
def test_agent_traces_are_the_agents_own_spans_for_the_call(voice_call):
    from simulate.services.run_traces_v3 import RunTrace

    trace = RunTrace(
        trace_id="t1",
        name="respond",
        start_time=None,
        status="OK",
        latency_ms=12,
        call_execution_id=str(voice_call.id),
    )
    with (
        patch.object(reader, "run_traces_source", return_value=object()),
        patch.object(reader, "read_run_traces", return_value=[trace]),
    ):
        evidence = _one(voice_call, Kind.AGENT_TRACES)

    assert evidence.state == State.PRESENT
    assert evidence.content[0]["trace_id"] == "t1"


# --- every kind, every time ---------------------------------------------------


@pytest.mark.django_db
def test_every_kind_gets_an_answer_and_only_present_ones_carry_content(voice_call):
    evidence = _read(voice_call, *Kind)

    assert set(evidence) == set(Kind)
    for kind, one in evidence.items():
        assert one.kind == kind
        if one.state == State.PRESENT:
            assert one.digest == canonical_digest(one.content)
        else:
            assert one.content is None and one.digest is None
        assert (one.cause is None) == (one.state != State.NOT_CAPTURED)
