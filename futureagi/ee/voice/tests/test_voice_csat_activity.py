"""Regression tests for TH-4957 — voice CSAT activity.

The activity must:
  1. Construct ``AgentEvaluator`` via the canonical EE path so it actually
     runs audio-based scoring (the previous deep ``agentic_eval.…``
     import silently raised ``ImportError`` and fell through to the VAPI
     ``successEvaluation`` fallback, which inbound calls do not populate).
  2. Forward ``call.recording_url`` to the evaluator unchanged.
  3. Loudly log any ``ImportError`` raised when resolving the evaluator,
     so the next breakage of the import surface is immediately visible
     instead of silently disabling CSAT for every call.
"""

import logging
from unittest.mock import patch

import pytest

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution
from simulate.models.test_execution import TestExecution as _TestExecution
from simulate.temporal.types.activities import CalculateVoiceCSATInput

from ee.voice.temporal.activities import voice_xl

# Force-import so patch targets resolve.
import ee.evals.llm.agent_evaluator.evaluator  # noqa: F401


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Test agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Test Simulator",
        prompt="You are a simulator.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def dataset_for_scenario(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Test Dataset",
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


@pytest.fixture
def scenario(db, organization, workspace, dataset_for_scenario, agent_definition):
    return Scenarios.objects.create(
        name="Test Scenario",
        description="desc",
        source="src",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test_obj(db, organization, workspace, agent_definition, scenario, simulator_agent):
    rt = RunTest.objects.create(
        name="Test Run",
        description="desc",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def test_execution(db, run_test_obj, simulator_agent, agent_definition):
    return _TestExecution.objects.create(
        run_test=run_test_obj,
        status=_TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


def _make_call(test_execution, scenario, **kwargs):
    defaults = dict(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        service_provider_call_id="vapi-test-1",
        call_metadata={"call_direction": "inbound"},
    )
    defaults.update(kwargs)
    return CallExecution.objects.create(**defaults)


class _FakeBatchResult:
    def __init__(self, score):
        self.eval_results = [{"data": {"result": score}, "reason": "ok"}]


class _FakeAgentEvaluator:
    """Records the URL the activity hands to the evaluator."""

    last_output_url = None

    def __init__(self, *args, **kwargs):
        pass

    def run(self, output, required_keys):
        type(self).last_output_url = output
        return _FakeBatchResult("8")


@pytest.fixture
def patch_agent_evaluator():
    _FakeAgentEvaluator.last_output_url = None
    with patch(
        "ee.evals.llm.agent_evaluator.evaluator.AgentEvaluator",
        _FakeAgentEvaluator,
    ):
        yield _FakeAgentEvaluator


@pytest.fixture(autouse=True)
def _patch_heartbeater():
    class _Stub:
        details = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    with patch("tfc.temporal.common.heartbeat.Heartbeater", return_value=_Stub()):
        yield


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_recording_url_reaches_agent_evaluator_unchanged(
    test_execution, scenario, patch_agent_evaluator
):
    """Activity must forward ``call.recording_url`` to AgentEvaluator and
    persist the returned 1-10 score on ``overall_score``."""

    recording_url = "https://storage.vapi.ai/recordings/abc-123.mp3"

    from asgiref.sync import sync_to_async

    @sync_to_async
    def _setup():
        return _make_call(
            test_execution,
            scenario,
            recording_url=recording_url,
            analysis_data=None,
        )

    call = await _setup()

    result = await voice_xl.calculate_voice_csat_score(
        CalculateVoiceCSATInput(call_id=str(call.id))
    )

    assert result.success is True
    assert result.csat_score == 8.0
    assert patch_agent_evaluator.last_output_url == recording_url

    @sync_to_async
    def _refresh():
        return CallExecution.objects.get(id=call.id)

    refreshed = await _refresh()
    assert refreshed.overall_score == 8.0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_import_error_is_logged_loudly_and_propagates(
    test_execution, scenario, caplog
):
    """A broken AgentEvaluator import disables CSAT for every call. The
    activity must emit an ERROR log so the regression is visible (this
    is exactly what silently broke after the EE split — TH-4957)."""

    from asgiref.sync import sync_to_async

    @sync_to_async
    def _setup():
        return _make_call(
            test_execution,
            scenario,
            recording_url="https://storage.vapi.ai/recordings/abc-123.mp3",
            analysis_data=None,
        )

    call = await _setup()

    # Simulate the import target being absent by making the attribute
    # access raise ImportError when the activity tries to use it.
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "ee.evals.llm.agent_evaluator.evaluator":
            raise ImportError("AgentEvaluator module disappeared")
        return real_import(name, *args, **kwargs)

    with caplog.at_level(logging.ERROR), patch(
        "builtins.__import__", side_effect=_fake_import
    ):
        result = await voice_xl.calculate_voice_csat_score(
            CalculateVoiceCSATInput(call_id=str(call.id))
        )

    assert result.success is False
    assert any(
        "CSAT AgentEvaluator import failed" in record.getMessage()
        for record in caplog.records
    ), f"Expected loud import-failure log; got: {[r.getMessage() for r in caplog.records]}"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_evaluator_runtime_failure_falls_back_to_success_evaluation(
    test_execution, scenario
):
    """If AgentEvaluator raises a non-ImportError at runtime, the
    successEvaluation mapping is still consulted as a safety net."""

    from asgiref.sync import sync_to_async

    @sync_to_async
    def _setup():
        return _make_call(
            test_execution,
            scenario,
            recording_url="https://storage.vapi.ai/recordings/abc-123.mp3",
            analysis_data={"successEvaluation": "true"},
            call_metadata={"call_direction": "outbound"},
        )

    call = await _setup()

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        def run(self, *a, **kw):
            raise RuntimeError("evaluator failed at runtime")

    with patch(
        "ee.evals.llm.agent_evaluator.evaluator.AgentEvaluator",
        _Boom,
    ):
        result = await voice_xl.calculate_voice_csat_score(
            CalculateVoiceCSATInput(call_id=str(call.id))
        )

    assert result.success is True
    assert result.csat_score == 1.0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_no_recording_no_success_eval_returns_skipped(
    test_execution, scenario
):
    """With neither a recording nor a successEvaluation the activity
    returns ``skipped=True`` cleanly (no exception)."""

    from asgiref.sync import sync_to_async

    @sync_to_async
    def _setup():
        return _make_call(
            test_execution, scenario, recording_url=None, analysis_data=None
        )

    call = await _setup()

    result = await voice_xl.calculate_voice_csat_score(
        CalculateVoiceCSATInput(call_id=str(call.id))
    )

    assert result.success is True
    assert result.skipped is True
    assert result.csat_score is None

    @sync_to_async
    def _refresh():
        return CallExecution.objects.get(id=call.id)

    refreshed = await _refresh()
    assert refreshed.overall_score is None
