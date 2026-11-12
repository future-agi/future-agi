"""Submission identity recovers ambiguous starts without replacing running work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.test import override_settings
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from tfc.temporal.experiments.admission import admit_v2_workflow


def client_with_memo(submission_id):
    description = SimpleNamespace(
        memo=AsyncMock(return_value={"experiment_submission_id": submission_id})
    )
    handle = SimpleNamespace(describe=AsyncMock(return_value=description))
    client = SimpleNamespace(
        get_workflow_handle=Mock(return_value=handle),
        start_workflow=AsyncMock(return_value=SimpleNamespace(id="experiment-fixture")),
    )
    return client, handle


@pytest.mark.asyncio
async def test_matching_memo_never_starts_another_workflow():
    client, _ = client_with_memo("submission-a")
    with patch(
        "tfc.temporal.experiments.admission.get_client", AsyncMock(return_value=client)
    ):
        result = await admit_v2_workflow(
            SimpleNamespace(experiment_id="fixture"), "submission-a", "tasks_l"
        )
    assert result == "experiment-fixture"
    client.start_workflow.assert_not_called()


# TX1-B: a new start also checks agent graph targets (ORM); pin it qualified so
# this stays a submission-identity case on a stub input with no database.
@pytest.mark.asyncio
@override_settings(DSX_CONSOLE_AGENT_EXPERIMENTS_ENABLED=True)
async def test_unrelated_running_workflow_is_not_cancelled_or_replaced():
    client, _ = client_with_memo("submission-other")
    client.start_workflow.side_effect = WorkflowAlreadyStartedError(
        "experiment-fixture", "RunExperimentV2Workflow"
    )
    with patch(
        "tfc.temporal.experiments.admission.get_client", AsyncMock(return_value=client)
    ):
        with pytest.raises(WorkflowAlreadyStartedError):
            await admit_v2_workflow(
                SimpleNamespace(experiment_id="fixture"), "submission-a", "tasks_l"
            )
    assert (
        client.start_workflow.call_args.kwargs["id_conflict_policy"]
        == WorkflowIDConflictPolicy.FAIL
    )


# TX1-B: as above; the OFF behaviour of this boundary is covered with real
# stored targets in test_experiment_agent_target_admission.py.
@pytest.mark.asyncio
@override_settings(DSX_CONSOLE_AGENT_EXPERIMENTS_ENABLED=True)
async def test_new_admission_records_submission_identity_and_recovers_start_race():
    client, handle = client_with_memo("submission-a")
    existing = await handle.describe()
    handle.describe.side_effect = [
        RPCError("not found", RPCStatusCode.NOT_FOUND, b""),
        existing,
    ]
    client.start_workflow.side_effect = WorkflowAlreadyStartedError(
        "experiment-fixture", "RunExperimentV2Workflow"
    )
    with patch(
        "tfc.temporal.experiments.admission.get_client", AsyncMock(return_value=client)
    ):
        result = await admit_v2_workflow(
            SimpleNamespace(experiment_id="fixture"), "submission-a", "tasks_l"
        )
    assert result == "experiment-fixture"
    assert client.start_workflow.call_args.kwargs["memo"] == {
        "experiment_submission_id": "submission-a"
    }
    client.start_workflow.assert_called_once()
