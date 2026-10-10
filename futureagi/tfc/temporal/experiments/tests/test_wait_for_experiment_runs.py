"""Waiting eval reruns ignore each other and wait for output producers."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.client import WorkflowExecutionStatus

from tfc.temporal.experiments import activities, types

# Autouse fixtures in this package's conftest touch the DB on teardown.
pytestmark = pytest.mark.django_db


class FakeClient:
    def __init__(self, main_status, listed=(), fail=False):
        self.main_status = main_status
        self.listed = listed
        self.fail = fail
        self.queries = []
        self.described_ids = []

    def get_workflow_handle(self, workflow_id):
        self.described_ids.append(workflow_id)

        async def describe():
            if self.fail:
                raise RuntimeError("describe unavailable")
            return SimpleNamespace(status=self.main_status)

        return SimpleNamespace(describe=describe)

    async def list_workflows(self, *, query):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("visibility unavailable")
        for workflow_id in self.listed:
            yield SimpleNamespace(id=workflow_id)


@pytest.mark.parametrize(
    "main_status,listed,fail,expected",
    [
        (WorkflowExecutionStatus.RUNNING, [], False, ["experiment-exp"]),
        (
            WorkflowExecutionStatus.COMPLETED,
            [
                "rerun-experiment-cells-exp-evals-other",
                "rerun-experiment-cells-exp-self",
            ],
            False,
            [],
        ),
        (
            WorkflowExecutionStatus.COMPLETED,
            ["rerun-experiment-cells-exp-prompt"],
            False,
            ["rerun-experiment-cells-exp-prompt"],
        ),
        (
            WorkflowExecutionStatus.RUNNING,
            [],
            True,
            ["experiment-exp", "rerun-experiment-cells-exp-visibility-unknown"],
        ),
    ],
    ids=[
        "main-running",
        "eval-and-self-excluded",
        "prompt-rerun",
        "outage-keeps-waiting",
    ],
)
async def test_experiment_runs_in_flight(main_status, listed, fail, expected):
    helper = getattr(activities, "_experiment_runs_in_flight", None)
    assert helper is not None, "output-producing workflow check is missing"
    client = FakeClient(main_status, listed, fail)
    result = await helper(client, "exp", "rerun-experiment-cells-exp-self")
    assert result == expected
    assert client.described_ids == ["experiment-exp"]
    assert client.queries == [
        "WorkflowId STARTS_WITH \"rerun-experiment-cells-exp-\" AND ExecutionStatus = 'Running'"
    ]


async def test_not_found_main_is_absent_not_an_outage():
    from temporalio.service import RPCError, RPCStatusCode

    class NotFoundClient(FakeClient):
        def get_workflow_handle(self, workflow_id):
            async def describe():
                raise RPCError("No such workflow", RPCStatusCode.NOT_FOUND, b"")

            return SimpleNamespace(describe=describe)

    client = NotFoundClient(WorkflowExecutionStatus.COMPLETED)
    assert await activities._experiment_runs_in_flight(client, "exp", "self") == []


@pytest.mark.parametrize(
    "cancelled,max_wait,in_flight_results,status,polls",
    [
        (False, 30.0, [["experiment-exp"], []], "READY", 2),
        (True, 30.0, [["experiment-exp"], []], "CANCELLED", 2),
        (False, 0.0, [["experiment-exp"]], "TIMED_OUT", 1),
    ],
    ids=["ready-after-poll", "cancelled-after-poll", "bounded-timeout"],
)
async def test_wait_activity_loop(
    cancelled, max_wait, in_flight_results, status, polls
):
    wait_activity = getattr(activities, "wait_for_experiment_runs_activity", None)
    assert wait_activity is not None, "wait activity is missing"
    experiment_id = str(uuid.uuid4())
    with (
        patch(
            "tfc.temporal.common.client.get_client", AsyncMock(return_value=object())
        ),
        patch.object(
            activities,
            "_experiment_runs_in_flight",
            AsyncMock(side_effect=in_flight_results),
        ) as in_flight,
        patch("temporalio.activity.heartbeat") as heartbeat,
        patch(
            "model_hub.services.experiment_utils.is_experiment_cancelled",
            return_value=cancelled,
        ) as is_cancelled,
    ):
        result = await wait_activity(
            types.WaitForExperimentRunsInput(
                experiment_id=experiment_id,
                exclude_workflow_id="self",
                poll_interval_seconds=0.0,
                max_wait_seconds=max_wait,
            )
        )
    assert result.status == status
    assert result.waited_seconds >= 0.0
    assert in_flight.await_count == polls
    assert heartbeat.call_count >= 1
    if status == "TIMED_OUT":
        is_cancelled.assert_not_called()
    else:
        is_cancelled.assert_called_once_with(uuid.UUID(experiment_id))
