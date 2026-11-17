"""A voice call that fails on an error shows a fixed reason for how it ended.

The reason is stored on the call, returned with the call list and shown on the
call's detail page. When reading a call's status or its data from the voice
provider fails, or the call cannot be queued, the call shows one fixed
sentence, recorded as coming from our code, and the error itself goes to the
server log.
"""

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models import AgentDefinition, CallExecution, RunTest, Scenarios
from simulate.models.test_execution import TestExecution as SimulationTestExecution
from simulate.serializers.test_execution import CallExecutionSerializer
from simulate.services import test_executor as test_executor_module
from simulate.services.test_executor import TestExecutor
from simulate.utils.ended_reason import (
    ENDED_REASON_SOURCE_KEY,
    EndedReasonSource,
    recorded_ended_reason_source,
)
from simulate.views.run_results_v3 import build_call_execution_detail

CALL_STOPPED = "The call stopped because of an error. Re-run it."
# Made-up error text, worded as a provider client error might be.
ERROR_TEXT = (
    "GET https://provider.example.test/call/provider-call-1 failed: key sk-example-0000"
)
ERROR_PARTS = ("provider.example.test", "https://", "sk-example")


def _create_voice_call(organization, workspace):
    agent_definition = AgentDefinition.objects.create(
        agent_name=f"Ended Reason Voice Agent {workspace.id}",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        description="Agent for ended reason tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name=f"Ended Reason Voice Scenario {workspace.id}",
        description="Scenario for ended reason tests",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
    )
    run_test = RunTest.objects.create(
        name=f"Ended Reason Voice Run {workspace.id}",
        description="Run for ended reason tests",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = SimulationTestExecution.objects.create(
        run_test=run_test,
        status=SimulationTestExecution.ExecutionStatus.RUNNING,
        total_scenarios=1,
        total_calls=1,
        agent_definition=agent_definition,
    )
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        status=CallExecution.CallStatus.ONGOING,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        service_provider_call_id="provider-call-1",
        call_metadata={"call_direction": "inbound"},
    )


def _raise_error(*args, **kwargs):
    raise Exception(ERROR_TEXT)


def _executor_whose_provider_fails():
    executor = TestExecutor(initialize_voice_service=False)
    executor.voice_service_manager = SimpleNamespace(get_call=_raise_error)
    return executor


def _check_status(call):
    executor = _executor_whose_provider_fails()
    # The status check alone: the call-data step after it would write the same
    # fields again.
    with patch.object(TestExecutor, "_store_complete_call_data"):
        executor.monitor_test_execution(call.test_execution)


def _store_call_data(call):
    _executor_whose_provider_fails()._store_complete_call_data(str(call.id))


@pytest.mark.integration
@pytest.mark.parametrize(
    "fail",
    [_check_status, _store_call_data],
    ids=["status-check", "call-data"],
)
def test_a_failed_voice_call_shows_only_its_fixed_reason(
    organization, workspace, fail, monkeypatch
):
    # The executor closes stale database connections between steps; here that
    # would close the test's own connection.
    monkeypatch.setattr(test_executor_module, "close_old_connections", lambda: None)
    call = _create_voice_call(organization, workspace)

    fail(call)

    call.refresh_from_db()
    assert call.status == CallExecution.CallStatus.FAILED
    assert call.ended_reason == CALL_STOPPED
    # The saved row records our code as the reason's source, for this reason.
    digest = hashlib.sha256(CALL_STOPPED.encode("utf-8")).hexdigest()[:16]
    assert call.call_metadata[ENDED_REASON_SOURCE_KEY] == f"fixed:{digest}"
    assert recorded_ended_reason_source(call) == EndedReasonSource.FIXED
    listed = CallExecutionSerializer(call).data
    assert listed["ended_reason"] == CALL_STOPPED
    # The call list returns the call's metadata too; its error is the same
    # fixed sentence.
    assert listed["call_metadata"]["error"] == CALL_STOPPED
    metadata_text = json.dumps(listed["call_metadata"], default=str)
    for part in ERROR_PARTS:
        assert part not in metadata_text
    detail = build_call_execution_detail(call, workspace=workspace)
    assert detail["ended_reason"] == CALL_STOPPED
    for answer in (listed, detail):
        text = json.dumps(answer, default=str)
        for part in ERROR_PARTS:
            assert part not in text


# The names an error's text is held in where the executor handles a failure.
ERROR_TEXT_NAMES = {"e", "error", "error_message", "error_msg"}


def test_no_end_reason_the_executor_stores_is_built_from_an_error():
    """Every end reason the executor stores is a fixed sentence, a reason the
    usage check or the provider gave, or a value it read, never an error's
    text. A failure branch that queues a call cannot be driven without the
    whole voice setup, so this reads the source."""
    tree = ast.parse(Path(test_executor_module.__file__).read_text())
    built_from_errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Attribute) and target.attr == "ended_reason"
            for target in node.targets
        ):
            value = node.value
        elif isinstance(node, ast.keyword) and node.arg == "ended_reason":
            value = node.value
        else:
            continue
        names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
        if names & ERROR_TEXT_NAMES:
            built_from_errors.append((value.lineno, ast.unparse(value)))
    assert built_from_errors == []
