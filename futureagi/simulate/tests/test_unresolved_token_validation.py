"""Behavioral regression test for PR #328 (template validation, issues #309/#312).

create_call_execution_records built each call's system prompt from the simulator agent's
template and launched the call even when {{tokens}} were left unresolved (e.g. the dataset
row lacks a column the prompt references) -- the call would run against garbled input with
no signal. The fix validates the built prompt and fails the CallExecution instead: status
FAILED, ended_reason naming the missing columns, and a FAILED CreateCallExecution so the
UI surfaces it.

Exercises the REAL create_call_execution_records activity against the real ORM (fixtures
and patches mirror TestCreateCallExecutionRecordsActivity in test_temporal_activities.py).
A prompt with unresolved {{tokens}} must produce a FAILED CallExecution whose ended_reason
names the tokens; a token-free prompt must NOT be failed. The first case fails if the
fix's validation block is removed.
"""
import importlib.util
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgiref.sync import sync_to_async

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Scenarios
from simulate.models.agent_version import AgentVersion
from simulate.models.run_test import CreateCallExecution, RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution

VAPI_API_KEY = os.environ.get("VAPI_API_KEY", "test-api-key-for-testing")


@pytest.fixture(autouse=True)
def _fake_ee_modules():
    """create_call_execution_records imports ee.voice.* (the private enterprise tree)
    unconditionally at entry, so in the public repo it cannot execute at all. Install a
    minimal fake ee tree -- the same approach this PR's formal_tests use (_make_module) --
    but ONLY when ee is not importable, so the fake is inert in the internal environment.
    None of the faked functions matter to the assertions: the no-rows flow never calls
    generate_dynamic_prompt, and voice selection is peripheral to token validation."""
    if importlib.util.find_spec("ee") is not None:
        yield
        return
    installed = []

    def make(name, **attrs):
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        installed.append(name)
        return mod

    ee = make("ee")
    ee.voice = make("ee.voice")
    ee.voice.utils = make("ee.voice.utils")
    ee.voice.utils.prompt_builder = make(
        "ee.voice.utils.prompt_builder",
        generate_dynamic_prompt=lambda **kw: kw.get("prompt_template", ""),
    )
    ee.voice.constants = make("ee.voice.constants")
    ee.voice.constants.voice_mapper = make(
        "ee.voice.constants.voice_mapper",
        select_voice_id=lambda persona_data, provider=None: "marissa",
    )
    ee.voice.constants.voice_catalog = make(
        "ee.voice.constants.voice_catalog",
        resolve_voice_id=lambda name, voice_descriptor=None: "test-voice-id",
    )
    yield
    for name in installed:
        sys.modules.pop(name, None)


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Token Validation Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Agent for #328 unresolved-token regression test",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def agent_version(db, agent_definition, organization, workspace):
    return AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        version_number=1,
        version_name="v1",
        status=AgentVersion.StatusChoices.ACTIVE,
        configuration_snapshot={
            "contact_number": "+15551234567",
            "assistant_id": "test-assistant-id",
            "api_key": VAPI_API_KEY,
            "workspace_id": str(agent_definition.workspace_id),
        },
    )


@pytest.fixture
def simulator_agent_factory(db, organization, workspace):
    def make(prompt):
        return SimulatorAgent.objects.create(
            name="Token Validation Simulator",
            prompt=prompt,
            voice_provider="elevenlabs",
            voice_name="marissa",
            model="gpt-4",
            organization=organization,
            workspace=workspace,
            conversation_speed=1.0,
            interrupt_sensitivity=0.5,
            finished_speaking_sensitivity=0.5,
            max_call_duration_in_minutes=15,
            initial_message_delay=0,
            initial_message="Hello",
        )

    return make


@pytest.fixture
def dataset_for_scenario(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Token Validation Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    col = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(col.id)]
    dataset.save()
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=col, row=row, value="Test situation")
    return dataset


def _make_stack(
    prompt, organization, workspace, agent_definition, agent_version,
    dataset_for_scenario, simulator_agent_factory,
):
    """Scenario + RunTest + TestExecution wired to a simulator agent with `prompt`."""
    simulator_agent = simulator_agent_factory(prompt)
    scenario = Scenarios.objects.create(
        name="Token Validation Scenario",
        description="Scenario for #328 regression test",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        status=StatusType.COMPLETED.value,
    )
    run_test = RunTest.objects.create(
        name="Token Validation Run",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.PENDING,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        agent_version=agent_version,
    )
    return simulator_agent, scenario, test_execution


async def _run_create_calls(test_execution, scenario, simulator_agent):
    from simulate.temporal.activities.test_execution import (
        create_call_execution_records,
    )
    from simulate.temporal.types.activities import CreateCallRecordsInput
    from tfc.temporal.common.heartbeat import Heartbeater

    scenarios_data = [
        {
            "id": str(scenario.id),
            "name": scenario.name,
            "dataset_id": str(scenario.dataset_id),
            "row_ids": [],  # no rows -> row_data={} -> the raw template must validate
        }
    ]
    with (
        patch("temporalio.activity.info"),
        patch.object(
            Heartbeater,
            "__aenter__",
            new=AsyncMock(return_value=MagicMock(details=None)),
        ),
        patch.object(Heartbeater, "__aexit__", new=AsyncMock(return_value=None)),
    ):
        return await create_call_execution_records(
            CreateCallRecordsInput(
                test_execution_id=str(test_execution.id),
                scenarios=scenarios_data,
                simulator_agent={"id": str(simulator_agent.id)},
            )
        )


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unresolved_tokens_fail_the_call_execution(
    organization, workspace, agent_definition, agent_version,
    dataset_for_scenario, simulator_agent_factory,
):
    simulator_agent, scenario, test_execution = await sync_to_async(_make_stack)(
        "Talk to {{customer_name}} about {{missing_topic}}.",
        organization, workspace, agent_definition, agent_version,
        dataset_for_scenario, simulator_agent_factory,
    )

    result = await _run_create_calls(test_execution, scenario, simulator_agent)

    call_execution = await CallExecution.objects.aget(test_execution=test_execution)
    assert call_execution.status == CallExecution.CallStatus.FAILED, (
        "a call whose prompt still contains {{tokens}} must be FAILED, not launched"
    )
    assert "customer_name" in (call_execution.ended_reason or "")
    assert "missing_topic" in (call_execution.ended_reason or "")
    # The UI-facing record must surface the failure too.
    failed_creates = await sync_to_async(
        CreateCallExecution.objects.filter(
            call_execution=call_execution,
            status=CreateCallExecution.CallStatus.FAILED,
        ).count
    )()
    assert failed_creates == 1
    # And the failed call must not ALSO be processed as launchable: exactly ONE
    # CreateCallExecution in total (the FAILED one -- no stray ONGOING sibling), and
    # its id appears exactly once in the returned call list. Guards the `continue`
    # that ends the validation-failure block against falling through.
    total_creates = await sync_to_async(
        CreateCallExecution.objects.filter(call_execution=call_execution).count
    )()
    assert total_creates == 1
    assert len(result.call_ids) == len(set(result.call_ids))


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_token_free_prompt_is_not_failed(
    organization, workspace, agent_definition, agent_version,
    dataset_for_scenario, simulator_agent_factory,
):
    # Near-miss: validation must only fail prompts that actually carry unresolved tokens.
    simulator_agent, scenario, test_execution = await sync_to_async(_make_stack)(
        "You are a helpful test caller with a fully resolved prompt.",
        organization, workspace, agent_definition, agent_version,
        dataset_for_scenario, simulator_agent_factory,
    )

    result = await _run_create_calls(test_execution, scenario, simulator_agent)

    call_execution = await CallExecution.objects.aget(test_execution=test_execution)
    assert call_execution.status != CallExecution.CallStatus.FAILED
    assert "Unresolved template variables" not in (call_execution.ended_reason or "")
