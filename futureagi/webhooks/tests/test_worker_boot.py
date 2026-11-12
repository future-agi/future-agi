"""The dispatcher loops start on the deployed worker path (WH-1a review must-fix 1; DESIGN.md section 13.1).

Hosted helm and the docker entrypoint run ``manage.py start_temporal_worker``, not ``tfc.temporal.common.worker``:
this drives that command's own ``handle()`` with a fake Temporal client and worker, and checks that it starts the
loops whenever it polls ``webhooks`` (single queue and ``--all-queues``), and that a hosted ``--all-queues`` worker
leaves the queue to its dedicated deployment. Needs FA's Django settings (no database).
"""

from __future__ import annotations

import asyncio
import os
import signal

import pytest
from django.core.management import call_command

from tfc.management.commands import start_temporal_worker as cmd
from webhooks.temporal import boot


@pytest.mark.unit
def test_hosted_all_queues_worker_leaves_webhooks_to_its_own_deployment():
    registered = ["default", "webhooks", "exact_aggregation"]
    assert cmd._generic_all_queues(registered) == ["default", "webhooks"]
    assert cmd._generic_all_queues(registered, hosted=True) == ["default"]


@pytest.mark.unit
def test_dispatchers_start_only_where_the_queue_is_polled(monkeypatch):
    calls = []

    async def fake_start(client):
        calls.append(client)

    monkeypatch.setattr(boot, "start_dispatchers_safely", fake_start)
    assert asyncio.run(boot.start_dispatchers_if_polled("c1", ["default"])) is False
    polled = ["default", "webhooks"]
    assert asyncio.run(boot.start_dispatchers_if_polled("c2", polled)) is True
    assert calls == ["c2"]


class _FakeClient:
    def get_workflow_handle(self, _wid):  # the reload-dispatcher signal path
        raise RuntimeError("not running")


class _FakeWorker:
    made: list = []

    def __init__(self, **kwargs):
        _FakeWorker.made.append(kwargs["task_queue"])

    async def __aenter__(self):
        # As the pod's SIGTERM would: the command's own handler sets its shutdown event.
        os.kill(os.getpid(), signal.SIGTERM)
        return self

    async def __aexit__(self, *_exc):
        return False


@pytest.fixture
def fake_temporal(monkeypatch):
    import temporalio.client
    import temporalio.worker

    import tfc.logging.temporal
    import tfc.telemetry.temporal
    import tfc.temporal
    import tfc.temporal.common.registry as registry

    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    started = []

    async def connect(*_a, **_kw):
        return _FakeClient()

    async def fake_start(client):
        started.append(client)

    _FakeWorker.made = []
    monkeypatch.setattr(temporalio.client.Client, "connect", staticmethod(connect))
    monkeypatch.setattr(temporalio.worker, "Worker", _FakeWorker)
    monkeypatch.setattr(
        tfc.telemetry.temporal, "init_otel_for_temporal", lambda *_a: None
    )
    monkeypatch.setattr(
        tfc.telemetry.temporal, "get_interceptors_for_client", lambda: []
    )
    monkeypatch.setattr(
        tfc.logging.temporal, "configure_temporal_logging", lambda: None
    )
    monkeypatch.setattr(
        tfc.temporal, "get_workflows_and_activities_for_queue", lambda _q: ([], [])
    )
    monkeypatch.setattr(registry, "get_all_queues", lambda: ["default", "webhooks"])
    monkeypatch.setattr(registry, "get_all_workflows", lambda: [])
    monkeypatch.setattr(registry, "get_all_activities", lambda: [])
    monkeypatch.setattr(boot, "start_dispatchers_safely", fake_start)
    monkeypatch.setattr(cmd.os, "_exit", lambda _code: None)
    yield started
    for s, h in saved.items():
        signal.signal(s, h)


@pytest.mark.parametrize(
    ("argv", "hosted", "expect_start", "expect_queues"),
    [
        ({"task_queue": "webhooks"}, False, True, ["webhooks"]),
        ({"task_queue": "default"}, False, False, ["default"]),
        ({"all_queues": True}, False, True, ["default", "webhooks"]),
        ({"all_queues": True}, True, False, ["default"]),
    ],
)
def test_the_worker_command_starts_the_loops_where_it_polls_webhooks(
    fake_temporal, monkeypatch, argv, hosted, expect_start, expect_queues
):
    monkeypatch.setattr(boot, "is_hosted_install", lambda: hosted)
    call_command("start_temporal_worker", reload_dispatcher=False, **argv)
    assert _FakeWorker.made == expect_queues
    assert len(fake_temporal) == (1 if expect_start else 0)
    if expect_start:
        assert isinstance(fake_temporal[0], _FakeClient)
