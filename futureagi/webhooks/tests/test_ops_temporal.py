"""WT-25, the restart half (DESIGN.md sections 13.1 and 17): on a real Temporal server (the CLI dev server that
``WorkflowEnvironment.start_local`` runs, as FA's other workflow tests do), with no dispatcher loop running while
events queue, one run of ``WebhooksHealthWorkflow`` (what the ``webhooks-health`` schedule starts every 60 s) starts
``webhooks-dispatcher-1``; that loop's real ``webhooks_dispatch_loop`` activity drains the backlog to a real receiver;
a second health run leaves the running loop alone ("use existing").

The one substitution is the dispatcher's sender: ``build_dispatcher`` is the test receiver's dispatcher (the address
rules never admit loopback; ``webhooks.tests.receiver``). The workflows, activities, worker, schedules' workflow
classes and the database are the production ones.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import close_old_connections, transaction
from temporalio.client import WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tfc.temporal.common import client as client_module
from webhooks import config
from webhooks.models import Delivery, DispatcherBeat, Event
from webhooks.publish import publish
from webhooks.temporal import QUEUE
from webhooks.temporal import activities as dispatch_activities
from webhooks.temporal.activities import dispatch_loop, ensure_dispatchers
from webhooks.temporal.ops_activities import health_pass
from webhooks.temporal.workflows import (
    WebhooksDispatcherWorkflow,
    WebhooksHealthWorkflow,
)
from webhooks.tests import evidence
from webhooks.tests.factories import T0, label_event_data, make_endpoint, use_test_keys
from webhooks.tests.receiver import Receiver, real_dispatcher


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


def _count(**kw) -> int:
    try:
        return Delivery.objects.filter(**kw).count()
    finally:
        close_old_connections()


@pytest.mark.django_db(transaction=True)
def test_wt25_health_workflow_starts_the_missing_loop_and_it_drains_the_backlog(
    organization, workspace, monkeypatch, receiver
):
    use_test_keys(monkeypatch)
    monkeypatch.setattr(config, "is_hosted", lambda: False)
    monkeypatch.delenv("FI_WEBHOOKS_ENABLED", raising=False)
    monkeypatch.delenv("WEBHOOKS_DISPATCHER_LOOPS", raising=False)
    ep = make_endpoint(
        organization, workspace, url=receiver.url("/ok"), rate_limit_per_s=100
    )
    with transaction.atomic():
        for i in range(10):
            publish(
                type="prompt.label.changed",
                organization_id=organization.id,
                workspace_id=workspace.id,
                data=label_event_data(),
                source_key=f"k{i}",
                source_hash="h",
                producer="test",
                occurred_at=T0,
            )

    def build(heartbeat=lambda: None):
        d = real_dispatcher(receiver)
        d.heartbeat = heartbeat
        return d

    monkeypatch.setattr(dispatch_activities, "build_dispatcher", build)

    async def main():
        env = await WorkflowEnvironment.start_local()
        try:

            async def the_env_client():
                return env.client

            monkeypatch.setattr(client_module, "get_client", the_env_client)
            with ThreadPoolExecutor(max_workers=8) as pool:
                worker = Worker(
                    env.client,
                    task_queue=QUEUE,
                    workflows=[WebhooksDispatcherWorkflow, WebhooksHealthWorkflow],
                    activities=[dispatch_loop, ensure_dispatchers, health_pass],
                    activity_executor=pool,
                    # As FA's deployed and embedded workers run them (start_temporal_worker, embedded.py).
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    graceful_shutdown_timeout=timedelta(seconds=20),
                )
                async with worker:
                    assert await asyncio.to_thread(_count, status="succeeded") == 0
                    out = await env.client.execute_workflow(
                        WebhooksHealthWorkflow.run,
                        id="scheduled-webhooks-health-wt25",
                        task_queue=QUEUE,
                        execution_timeout=timedelta(seconds=60),
                    )
                    seen["first_run"] = out
                    assert out["dispatchers_ensured"] == 1
                    loop = env.client.get_workflow_handle("webhooks-dispatcher-1")
                    first = await loop.describe()
                    seen["loop_status"] = first.status.name
                    seen["loop_run_id"] = first.run_id
                    assert first.status == WorkflowExecutionStatus.RUNNING
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        if await asyncio.to_thread(_count, status="succeeded") == 10:
                            break
                        await asyncio.sleep(0.5)
                    assert await asyncio.to_thread(_count, status="succeeded") == 10
                    # A second health run finds the loop running and leaves it alone.
                    again = await env.client.execute_workflow(
                        WebhooksHealthWorkflow.run,
                        id="scheduled-webhooks-health-wt25-2",
                        task_queue=QUEUE,
                        execution_timeout=timedelta(seconds=60),
                    )
                    seen["second_run"] = again
                    seen["loop_run_id_after_second_run"] = (
                        await loop.describe()
                    ).run_id
                    assert again["dispatchers_ensured"] == 1
                    assert seen["loop_run_id_after_second_run"] == first.run_id
                    await loop.cancel()
                    await asyncio.sleep(2)
        finally:
            await env.shutdown()

    seen: dict = {}
    asyncio.run(main())
    evidence.write(
        "WT-25",
        temporal="the Temporal CLI dev server (WorkflowEnvironment.start_local)",
        workflows=seen,
        db={
            "deliveries": evidence.by_status(Delivery.objects.filter(endpoint=ep)),
            "beats": list(DispatcherBeat.objects.values_list("loop", flat=True)),
        },
        receiver=evidence.receiver_lines(receiver),
    )
    assert len(receiver.arrivals("/ok")) == 10
    assert not Event.objects.filter(fanout_state="pending").exists()
    assert DispatcherBeat.objects.filter(loop="webhooks-dispatcher-1").exists()
    assert Delivery.objects.filter(endpoint=ep, status="succeeded").count() == 10
