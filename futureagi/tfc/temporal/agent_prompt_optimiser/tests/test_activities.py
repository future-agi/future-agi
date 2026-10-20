"""Regression tests for run_optimization_activity credential and issue plumbing.

The activity must fetch the org's API key and the analysis run's issues and
forward both to FixYourAgent.optimize_from_execution; historically they were
dropped and the teacher model authenticated with the worker's environment
credentials while issue-based scoring stayed disabled.

Tests are synchronous and drive the activity with asyncio.run; transaction=True
commits fixture rows so the activity's own connection can see them (Django
connections are context-local, so the coroutine escapes the test transaction).
"""

import asyncio

import pytest
from temporalio.exceptions import ApplicationError

import tfc.temporal.agent_prompt_optimiser.activities as activities
from simulate.models import (
    AgentOptimiser,
    AgentOptimiserRun,
    AgentPromptOptimiserRun,
    RunTest,
    TestExecution,
)

ISSUES = [{"issue": "agent ignores refund policy", "priority": "high"}]


class NoopHeartbeater:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class FakeResult:
    history = [{"trial": 0}]
    final_score = 0.5
    best_prompt = "improved"


class RecordingAgent:
    calls: list = []

    def optimize_from_execution(self, **kwargs):
        RecordingAgent.calls.append(kwargs)
        return FakeResult()


@pytest.fixture
def prompt_optimiser_run(organization, workspace):
    agent_optimiser = AgentOptimiser.no_workspace_objects.create(
        name="opt", configuration={}
    )
    agent_optimiser_run = AgentOptimiserRun.no_workspace_objects.create(
        agent_optimiser=agent_optimiser,
        status=AgentOptimiserRun.OptimiserStatus.COMPLETED,
        input_data={},
        result={"agent_level": {"actionable_recommendations": ISSUES}},
    )
    run_test = RunTest.no_workspace_objects.create(
        name="rt", organization=organization, workspace=workspace
    )
    test_execution = TestExecution.no_workspace_objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        completed_calls=1,
        agent_optimiser=agent_optimiser,
    )
    return AgentPromptOptimiserRun.no_workspace_objects.create(
        name="run",
        agent_optimiser=agent_optimiser,
        agent_optimiser_run=agent_optimiser_run,
        test_execution=test_execution,
        optimiser_type=AgentPromptOptimiserRun.OptimiserType.RANDOM_SEARCH,
        model="gemini/gemini-2.5-pro",
        status=AgentPromptOptimiserRun.Status.RUNNING,
        configuration={"num_variations": 2},
    )


@pytest.fixture
def activity_env(monkeypatch):
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    monkeypatch.setattr(activities, "Heartbeater", NoopHeartbeater)
    monkeypatch.setattr(
        activities,
        "get_full_test_execution_data",
        lambda _id: {"agent_definition_prompt": {"description": "base"}},
    )
    monkeypatch.setattr(
        activities, "get_agent_prompt_optimiser_run_steps", lambda _id: []
    )
    monkeypatch.setattr(activities, "FixYourAgent", RecordingAgent)
    RecordingAgent.calls = []


def _run_activity(run_id: str):
    return asyncio.run(activities.run_optimization_activity({"run_id": run_id}))


@pytest.mark.django_db(transaction=True)
def test_activity_forwards_org_api_key_and_issues(
    prompt_optimiser_run, activity_env, monkeypatch
):
    seen = {}

    def fake_get_api_key(model_name, organization_id, workspace_id=None):
        seen["model_name"] = model_name
        seen["organization_id"] = organization_id
        seen["workspace_id"] = workspace_id
        return "sk-user-key"

    monkeypatch.setattr(activities, "get_api_key_for_model", fake_get_api_key)

    _run_activity(str(prompt_optimiser_run.id))

    run_test = prompt_optimiser_run.test_execution.run_test
    assert seen["model_name"] == prompt_optimiser_run.model
    assert seen["organization_id"] == run_test.organization.id
    assert seen["workspace_id"] == (
        run_test.workspace.id if run_test.workspace else None
    )

    assert len(RecordingAgent.calls) == 1
    kwargs = RecordingAgent.calls[0]
    assert kwargs["api_key"] == "sk-user-key"
    assert kwargs["issues"] == ISSUES


@pytest.mark.django_db(transaction=True)
def test_activity_missing_api_key_is_non_retryable(
    prompt_optimiser_run, activity_env, monkeypatch
):
    def missing_key(**_kwargs):
        raise ValueError("API key not found for gemini/gemini-2.5-pro")

    monkeypatch.setattr(activities, "get_api_key_for_model", missing_key)

    with pytest.raises(ApplicationError) as captured:
        _run_activity(str(prompt_optimiser_run.id))

    assert captured.value.non_retryable is True
    assert "API key not found" in str(captured.value)
    assert RecordingAgent.calls == []
