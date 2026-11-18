import pytest
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from simulate.temporal.client import _start_hosted_harness_gateway_workflow_async


class _RecordingClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def start_workflow(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error


async def _start(monkeypatch, client, *, allow_completed_restart=False):
    async def get_client():
        return client

    monkeypatch.setattr("tfc.temporal.common.client.get_client", get_client)
    return await _start_hosted_harness_gateway_workflow_async(
        "job-1",
        "https://platform.example",
        2,
        1,
        10,
        allow_completed_restart=allow_completed_restart,
    )


@pytest.mark.asyncio
async def test_repeated_schedule_cannot_restart_a_completed_job(monkeypatch):
    client = _RecordingClient(
        WorkflowAlreadyStartedError("hosted-harness-job-1", "gateway")
    )

    assert await _start(monkeypatch, client) == "hosted-harness-job-1"
    assert (
        client.calls[0][1]["id_reuse_policy"]
        == WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY
    )


@pytest.mark.asyncio
async def test_explicit_rerun_can_restart_a_completed_job(monkeypatch):
    client = _RecordingClient()

    await _start(monkeypatch, client, allow_completed_restart=True)

    assert (
        client.calls[0][1]["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE
    )
