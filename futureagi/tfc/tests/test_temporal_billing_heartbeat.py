"""Exercise the async activity / sync billing boundary with real heartbeats."""

import asyncio
import os
import sys
import threading
from dataclasses import replace
from datetime import timedelta
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tfc.temporal.billing import activities
from tfc.temporal.billing.types import MonthlyClosingInput, MonthlyInvoiceInput

ORG_ID = "00000000-0000-4000-8000-000000000001"


def _install_billing(monkeypatch, generate, reset=lambda **kwargs: None):
    """Replace only the private services; retain both real sync wrappers."""
    invoice_module = ModuleType("ee.cloud.billing.invoice_generation")
    invoice_module.InvoiceGenerationService = SimpleNamespace(run_for_period=generate)
    reset_module = ModuleType("ee.cloud.tasks.monthly_reset")
    reset_module.run_monthly_reset = reset
    monkeypatch.setitem(sys.modules, invoice_module.__name__, invoice_module)
    monkeypatch.setitem(sys.modules, reset_module.__name__, reset_module)


def _activity_call(closing):
    if closing:
        return activities.monthly_closing_activity, MonthlyClosingInput(
            period="2026-08"
        )
    return activities.generate_monthly_invoices_activity, MonthlyInvoiceInput(
        period="2026-09", org_id=ORG_ID
    )


@workflow.defn
class BillingHeartbeatWorkflow:
    @workflow.run
    async def run(self, closing: bool):
        fn, argument = _activity_call(closing)
        return await workflow.execute_activity(
            fn,
            argument,
            start_to_close_timeout=timedelta(seconds=15),
            heartbeat_timeout=timedelta(seconds=1),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@pytest.mark.integration
@pytest.mark.parametrize("closing", [False, True], ids=["invoices", "closing"])
@pytest.mark.parametrize("slow", [False, True], ids=["progress", "slow-phases"])
async def test_worker_delivers_thread_progress(monkeypatch, closing, slow):
    """Server receipt proves the real async SDK heartbeat ran with its context.

    Slow reset and a single slow organization each exceed heartbeat_timeout.
    There are no retries to conceal a timeout. No billing credentials are used.
    """
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    release = threading.Event()
    phases = []

    def hold_past_heartbeat_timeout():
        # Hold the sync service until the loop releases it after the server's
        # one-second heartbeat deadline. The loop itself must stay responsive.
        phase_release = threading.Event()
        loop.call_soon_threadsafe(loop.call_later, 1.5, phase_release.set)
        assert phase_release.wait(5), "event loop did not release the slow phase"

    def reset(**kwargs):
        assert kwargs == {"period": "2026-08"}
        assert threading.get_ident() != loop_thread
        phases.append("reset")
        if slow:
            hold_past_heartbeat_timeout()

    def generate(**kwargs):
        assert threading.get_ident() != loop_thread
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        assert activity.info().activity_type == _activity_call(closing)[0].__name__
        assert kwargs["period"] == "2026-09"
        assert kwargs["org_id"] == (None if closing else ORG_ID)
        assert not kwargs["dry_run"]
        assert not kwargs["skip_stripe"]
        assert not kwargs["skip_email"]
        phases.append("invoices")
        kwargs["on_progress"](ORG_ID)
        if slow:
            hold_past_heartbeat_timeout()
        assert release.wait(10), "test did not release the invoice service"
        return SimpleNamespace(created=1, skipped=2, errors=0)

    _install_billing(monkeypatch, generate, reset)
    # The server path is provided by CI; otherwise use the SDK's test server.
    options = {}
    if path := os.environ.get("TEMPORAL_TEST_SERVER"):
        options["test_server_existing_path"] = path
    async with await WorkflowEnvironment.start_time_skipping(**options) as env:
        queue = f"billing-heartbeat-{uuid4()}"
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[BillingHeartbeatWorkflow],
            activities=[_activity_call(closing)[0]],
            workflow_runner=UnsandboxedWorkflowRunner(),
            max_heartbeat_throttle_interval=timedelta(milliseconds=50),
            default_heartbeat_throttle_interval=timedelta(milliseconds=50),
        ):
            with env.auto_time_skipping_disabled():
                handle = await env.client.start_workflow(
                    BillingHeartbeatWorkflow.run,
                    closing,
                    id=f"billing-heartbeat-{uuid4()}",
                    task_queue=queue,
                )
                result_task = asyncio.create_task(handle.result())
                delivered = False
                try:
                    async with asyncio.timeout(10):
                        while not delivered:
                            if result_task.done():
                                # Surface the activity's actual failure on base.
                                await result_task
                                break
                            description = await handle.describe()
                            for (
                                pending
                            ) in description.raw_description.pending_activities:
                                details = await env.client.data_converter.decode(
                                    pending.heartbeat_details.payloads
                                )
                                if details == [ORG_ID]:
                                    assert pending.attempt == 1
                                    delivered = True
                            await asyncio.sleep(0.02)
                finally:
                    release.set()
                    if not delivered and not result_task.done():
                        result_task.cancel()
                        await asyncio.gather(result_task, return_exceptions=True)
                result = await asyncio.wait_for(result_task, 15)
                assert delivered, "Temporal server never received organization progress"

    assert result["status"] == "COMPLETED"
    assert result["invoices_created"] == 1
    assert result["invoices_skipped"] == 2
    assert result["errors"] == 0
    assert phases == (["reset", "invoices"] if closing else ["invoices"])


@pytest.mark.parametrize("closing", [False, True], ids=["invoices", "closing"])
async def test_billing_without_cloud_overlay(monkeypatch, closing):
    monkeypatch.setitem(sys.modules, "ee.cloud.billing.invoice_generation", None)
    monkeypatch.setitem(sys.modules, "ee.cloud.tasks.monthly_reset", None)
    fn, argument = _activity_call(closing)
    result = await ActivityEnvironment().run(fn, argument)
    assert result.status == "COMPLETED"
    assert result.invoices_created == result.invoices_skipped == result.errors == 0


@pytest.mark.parametrize("closing", [False, True], ids=["invoices", "closing"])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_heartbeat_lifecycle(monkeypatch, closing, outcome):
    """Periodic tasks end on every exit; a cancelled thread cannot heartbeat late."""
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finished = asyncio.Event()
    release = threading.Event()
    heartbeats = []
    callbacks = []

    def generate(**kwargs):
        callbacks.append(kwargs["on_progress"])
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(5), "test did not release the invoice service"
            kwargs["on_progress"](ORG_ID)
            if outcome == "failure":
                raise ValueError("synthetic billing failure")
            return SimpleNamespace(created=1, skipped=0, errors=0)
        finally:
            loop.call_soon_threadsafe(finished.set)

    _install_billing(monkeypatch, generate)
    env = ActivityEnvironment()
    env.info = replace(env.info, heartbeat_timeout=timedelta(milliseconds=120))

    def on_heartbeat(*details):
        assert asyncio.get_running_loop() is loop
        assert activity.info() == env.info
        heartbeats.append(details)

    env.on_heartbeat = on_heartbeat
    before = asyncio.all_tasks()
    fn, argument = _activity_call(closing)
    task = asyncio.create_task(env.run(fn, argument))
    try:
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.sleep(0.03)
        assert len(heartbeats) >= 2, "no periodic heartbeat during synchronous work"
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # A real sync_to_async executor thread can finish after cancellation.
            count_at_exit = len(heartbeats)
            release.set()
        else:
            release.set()
            if outcome == "failure":
                with pytest.raises(ValueError, match="synthetic billing failure"):
                    await task
            else:
                assert (await task).invoices_created == 1
            count_at_exit = len(heartbeats)
        await asyncio.wait_for(finished.wait(), 2)
        # A retained pre-organization callback cancels further work after exit
        # without dispatching any heartbeat onto the activity loop.
        with pytest.raises(
            asyncio.CancelledError, match="Billing activity has stopped"
        ):
            await asyncio.to_thread(callbacks[0], ORG_ID)
        await asyncio.sleep(0.03)
        assert len(heartbeats) == count_at_exit
        assert not (asyncio.all_tasks() - before - {task})
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(finished.wait(), 2)
