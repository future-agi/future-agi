"""Cancellation reaches the invoice loop, not just its heartbeat task.

The Temporal server and activity/executor boundary are real. Billing side effects
are synthetic; the optional cloud case retains the actual private service loop
but substitutes subscription queries, invoice calculation and its per-organization
transactions (no charges, writes or database).
"""

import asyncio
import contextlib
import inspect
import os
import sys
import threading
import time
from datetime import timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tfc.temporal.billing import activities
from tfc.temporal.billing.types import MonthlyClosingInput, MonthlyInvoiceInput

ORG_IDS = (
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
)


@workflow.defn
class BillingCancellationWorkflow:
    @workflow.run
    async def run(self, closing: bool):
        if closing:
            fn = activities.monthly_closing_activity
            argument = MonthlyClosingInput(period="2026-08")
        else:
            fn = activities.generate_monthly_invoices_activity
            argument = MonthlyInvoiceInput(period="2026-09")
        return await workflow.execute_activity(
            fn,
            argument,
            start_to_close_timeout=timedelta(seconds=30),
            heartbeat_timeout=timedelta(seconds=1),
            retry_policy=RetryPolicy(maximum_attempts=1),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )


def _cloud_invoice_loop(monkeypatch, do_work):
    """Pin the real service's before-org callback/except-Exception contract.

    Only data access is substituted: subscription and invoice queries, the
    engine's calculation and the service's own per-organization transactions
    (this test has no database). The calculation accepts exactly the arguments
    the real engine accepts, so the service's call to it stays checked.
    """
    module = pytest.importorskip("ee.cloud.billing.invoice_generation")
    from ee.cloud.billing.billing_engine import BillingEngine

    from ee.usage.models.usage import Invoice, OrganizationSubscription, PlanChoices

    subscriptions = Mock()
    subscriptions.select_related.return_value.iterator.return_value = iter(
        SimpleNamespace(
            organization_id=org_id,
            organization=SimpleNamespace(display_name="Synthetic organization"),
            plan=PlanChoices.PAYG,
        )
        for org_id in ORG_IDS
    )
    monkeypatch.setattr(
        OrganizationSubscription.objects, "filter", Mock(return_value=subscriptions)
    )
    invoices = Mock()
    invoices.values_list.return_value = []
    monkeypatch.setattr(Invoice.objects, "filter", Mock(return_value=invoices))

    engine_call = inspect.signature(BillingEngine.generate_invoice)

    def calculate(*args, **kwargs):
        org_id, period = engine_call.bind(*args, **kwargs).args[:2]
        assert period == "2026-09"
        do_work(org_id)
        # Exercise the real loop without creating invoices or contacting Stripe.
        return {"total": 0, "line_items": []}

    monkeypatch.setattr(BillingEngine, "generate_invoice", staticmethod(calculate))
    # Module-local, so django.db.transaction itself is untouched and any other
    # database use by the service still trips pytest-django's blocker.
    monkeypatch.setattr(
        module,
        "transaction",
        SimpleNamespace(atomic=lambda *args, **kwargs: contextlib.nullcontext()),
        raising=False,
    )
    return module, module.InvoiceGenerationService.run_for_period


def test_cloud_invoice_loop_reaches_every_organization(monkeypatch):
    """The cloud case's isolation still lets the real loop run each organization.

    The service swallows per-organization errors. If the isolation stops fitting
    it (new engine keyword arguments, a per-organization transaction without a
    database), no organization is ever worked on and the cancellation cases
    below can only time out.
    """
    entered, progress, errors = [], [], []
    _, run_for_period = _cloud_invoice_loop(monkeypatch, entered.append)

    result = run_for_period(
        period="2026-09",
        org_id=None,
        dry_run=False,
        skip_stripe=True,
        skip_email=True,
        stderr=errors.append,
        on_progress=progress.append,
    )

    assert errors == []
    assert progress == list(ORG_IDS)
    assert entered == list(ORG_IDS)
    assert (result.created, result.skipped, result.errors) == (0, 2, 0)


@pytest.mark.integration
@pytest.mark.parametrize("closing", [False, True], ids=["invoices", "closing"])
@pytest.mark.parametrize("fail_current", [False, True], ids=["finish", "failure"])
@pytest.mark.parametrize("service", ["synthetic", "cloud"])
async def test_cancellation_stops_before_next_organization(
    monkeypatch, closing, fail_current, service
):
    """Observe the executor after Temporal reports cancellation, not just history.

    Hold the current organization until cancellation is delivered. Its work must
    still finish (or unwind on failure), but the next organization must not start.
    The synthetic service mirrors the private loop's progress-error handling;
    the cloud variant verifies that contract against the real implementation.
    """
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finished = asyncio.Event()
    release = threading.Event()
    entered = []
    settled = []
    progress_errors = []
    events = []
    start_time = time.monotonic()

    def record(event, org_id=None):
        events.append((event, org_id, round(time.monotonic() - start_time, 3)))

    def do_work(org_id):
        entered.append(org_id)
        record("org_started", org_id)
        loop.call_soon_threadsafe(started.set)
        assert release.wait(15), "test did not release the current organization"
        settled.append(org_id)
        record("org_failed" if fail_current else "org_finished", org_id)
        if fail_current:
            raise ValueError("Synthetic organization failure")

    if service == "cloud":
        invoice_module, run_for_period = _cloud_invoice_loop(monkeypatch, do_work)
    else:
        invoice_module = ModuleType("ee.cloud.billing.invoice_generation")

        def run_for_period(**kwargs):
            assert kwargs["org_id"] is None
            result = SimpleNamespace(created=0, skipped=0, errors=0)
            for org_id in ORG_IDS:
                # The service calls progress BEFORE any per-organization work.
                # Ordinary callback failures are swallowed, cancellation is not.
                try:
                    kwargs["on_progress"](org_id)
                except Exception as exc:
                    progress_errors.append(exc)
                try:
                    do_work(org_id)
                except ValueError:
                    result.errors += 1
                else:
                    result.created += 1
            return result

        invoice_module.InvoiceGenerationService = SimpleNamespace()
        monkeypatch.setitem(sys.modules, invoice_module.__name__, invoice_module)

    service_output = []

    def generate(**kwargs):
        # Keep the service's lines for failure messages. Its swallowed
        # per-organization errors go to stdout when no stderr is given.
        activity_log = kwargs["stdout"]

        def stdout(line):
            service_output.append(line)
            activity_log(line)

        try:
            return run_for_period(**{**kwargs, "stdout": stdout})
        finally:
            record("executor_finished")
            loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(
        invoice_module.InvoiceGenerationService,
        "run_for_period",
        generate,
        raising=False,
    )
    reset_module = ModuleType("ee.cloud.tasks.monthly_reset")
    reset_module.run_monthly_reset = lambda **kwargs: None
    monkeypatch.setitem(sys.modules, reset_module.__name__, reset_module)

    options = {}
    if path := os.environ.get("TEMPORAL_TEST_SERVER"):
        options["test_server_existing_path"] = path
    async with await WorkflowEnvironment.start_time_skipping(**options) as env:
        queue = f"billing-cancellation-{uuid4()}"
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[BillingCancellationWorkflow],
            activities=[
                activities.generate_monthly_invoices_activity,
                activities.monthly_closing_activity,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
            max_heartbeat_throttle_interval=timedelta(milliseconds=50),
            default_heartbeat_throttle_interval=timedelta(milliseconds=50),
        ):
            with env.auto_time_skipping_disabled():
                handle = await env.client.start_workflow(
                    BillingCancellationWorkflow.run,
                    closing,
                    id=f"billing-cancellation-{uuid4()}",
                    task_queue=queue,
                )
                try:
                    # Fail fast, with the service's own output, if it ends
                    # without reaching the first organization's work.
                    waiters = [
                        asyncio.ensure_future(event.wait())
                        for event in (started, finished)
                    ]
                    await asyncio.wait(
                        waiters, timeout=10, return_when=asyncio.FIRST_COMPLETED
                    )
                    for waiter in waiters:
                        waiter.cancel()
                    if not started.is_set():
                        pytest.fail(
                            "billing never started the first organization; "
                            f"service output: {service_output}"
                        )
                    # First verify that progress reached the real server. The same
                    # heartbeat channel delivers the subsequent cancellation.
                    async with asyncio.timeout(10):
                        while True:
                            description = await handle.describe()
                            delivered = False
                            for (
                                pending
                            ) in description.raw_description.pending_activities:
                                details = await env.client.data_converter.decode(
                                    pending.heartbeat_details.payloads
                                )
                                if details == [ORG_IDS[0]]:
                                    assert pending.attempt == 1
                                    delivered = True
                            if delivered:
                                break
                            await asyncio.sleep(0.02)
                    record("cancel_requested")
                    await handle.cancel()
                    with pytest.raises(WorkflowFailureError) as exc:
                        await asyncio.wait_for(handle.result(), 10)
                    assert isinstance(exc.value.cause, CancelledError)
                    record("workflow_cancelled")
                    # Cancellation must not interrupt the current organization.
                    assert not finished.is_set()
                    assert settled == []
                finally:
                    release.set()
                    await asyncio.wait_for(finished.wait(), 10)
                    print(f"billing cancellation trace ({service}): {events}")

    assert progress_errors == []
    assert entered == [ORG_IDS[0]], (
        "billing started another organization after cancellation"
    )
    assert settled == [ORG_IDS[0]], "current organization did not settle exactly once"
