"""A user stop: the gateway waits, bounded, for the guest to seal the call in flight before deleting the sandbox."""

import logging
import uuid
from types import SimpleNamespace

import pytest
from django.core.cache import cache

from simulate.models import HostedHarnessExecution
from simulate.services import hosted_harness_gateway as gateway_module
from simulate.services.hosted_harness_gateway import HostedHarnessGateway


class _InFlight:
    """Stands in for the in-flight query: answers `exists()` from a script, counting each poll."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.polls = 0

    def filter(self, **_kwargs):
        return self

    def exists(self):
        self.polls += 1
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class _Attempt(SimpleNamespace):
    def refresh_from_db(self, fields=None):
        if self.flip_terminal_after is not None:
            self.flip_terminal_after -= 1
            if self.flip_terminal_after <= 0:
                self.terminal_event_received = True


def _attempt(**fields):
    return _Attempt(
        **{
            "id": uuid.uuid4(),
            "job_id": uuid.uuid4(),
            "terminal_event_received": False,
            "terminal_stage": "canceled",
            "flip_terminal_after": None,
            **fields,
        }
    )


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 0.0}
    monkeypatch.setattr(gateway_module.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(
        gateway_module.time,
        "sleep",
        lambda seconds: now.__setitem__("t", now["t"] + seconds),
    )
    cache.clear()
    return now


def _gateway():
    return object.__new__(HostedHarnessGateway)


def test_cancel_waits_for_the_guest_seal_then_deletes(monkeypatch, clock):
    # The receipt lands on the third poll (4 s in): the wait ends there.
    flights = _InFlight([True, True, True, False])
    monkeypatch.setattr(HostedHarnessExecution, "objects", flights)
    _gateway()._await_guest_seal(_attempt())
    assert clock["t"] == 4

    # The guest's terminal event ends the wait too.
    cache.clear()
    clock["t"] = 0.0
    monkeypatch.setattr(HostedHarnessExecution, "objects", _InFlight([True]))
    _gateway()._await_guest_seal(_attempt(flip_terminal_after=2))
    assert clock["t"] == 2


@pytest.mark.django_db
def test_cancel_seal_runs_after_the_signal_and_before_the_sandbox_is_deleted(
    organization, monkeypatch
):
    from unittest.mock import patch

    from simulate.services.hosted_harness import create_hosted_job
    from simulate.tests.test_hosted_harness_gateway import _Daytona, _payload

    # Hosted runs reach every model through AgentCC: configured here as the gateway tests configure it.
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", "harness-virtual-key")
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="cancel-seal-order"
    )
    client = _Daytona()
    gateway = _gateway()
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")
    monkeypatch.setattr(
        "simulate.services.phone_telephony.cleanup_hosted_phone_rooms", lambda _: None
    )
    timeouts = []
    original_exec = client.sandbox.process.exec

    def exec_recording(command, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return original_exec(command, **kwargs)

    monkeypatch.setattr(client.sandbox.process, "exec", exec_recording)
    order = []
    monkeypatch.setattr(
        gateway, "_await_guest_seal", lambda _attempt: order.append("seal")
    )
    monkeypatch.setattr(
        gateway,
        "_delete_and_record",
        lambda _attempt, **_kwargs: order.append("delete") or job,
    )

    gateway.cancel(job, reason="user_canceled")

    assert order == ["seal", "delete"]
    assert timeouts[-1] == gateway_module._CANCEL_SIGNAL_TIMEOUT_SECONDS == 10


def test_cancel_deletes_after_the_seal_timeout(monkeypatch, clock, caplog):
    monkeypatch.setattr(gateway_module, "_CANCEL_SEAL_SECONDS", 0.1)
    monkeypatch.setattr(HostedHarnessExecution, "objects", _InFlight([True]))
    # `terminal_stage == "canceled"` alone (set by cancel itself) never ends the wait.
    with caplog.at_level(logging.WARNING):
        _gateway()._await_guest_seal(_attempt())
    assert "hosted_cancel_seal_timeout" in caplog.text

    # The platform cap after the env read stays strictly above the guest's own bound.
    monkeypatch.setenv("HARNESS_CANCEL_SEAL_SECONDS", "999")
    assert (
        gateway_module._cancel_seal_seconds() == gateway_module._CANCEL_SEAL_CAP_SECONDS
    )
    assert (
        gateway_module._cancel_seal_seconds()
        > gateway_module._GUEST_CANCEL_SEAL_SECONDS
    )
    assert gateway_module._CANCEL_SIGNAL_TIMEOUT_SECONDS == 10


def test_cancel_with_no_call_in_flight_does_not_wait(monkeypatch, clock):
    flights = _InFlight([False])
    monkeypatch.setattr(HostedHarnessExecution, "objects", flights)
    _gateway()._await_guest_seal(_attempt())
    assert (flights.polls, clock["t"]) == (1, 0.0)


def test_a_retried_cancel_skips_the_seal_wait(monkeypatch, clock):
    attempt = _attempt()
    cache.set(f"hosted-cancel-seal:{attempt.id}", True)
    flights = _InFlight([True])
    monkeypatch.setattr(HostedHarnessExecution, "objects", flights)
    _gateway()._await_guest_seal(attempt)
    assert clock["t"] == 0.0


def test_the_seal_still_gets_its_wait_when_the_cache_is_down(monkeypatch, clock):
    class Down:
        def get(self, *_args, **_kwargs):
            raise ConnectionError("cache down")

        set = get

    import django.core.cache

    monkeypatch.setattr(django.core.cache, "cache", Down())
    monkeypatch.setattr(
        HostedHarnessExecution, "objects", _InFlight([True, True, False])
    )
    _gateway()._await_guest_seal(_attempt())
    # The receipt landed one poll into the wait: the wait ran instead of being skipped for the cache.
    assert clock["t"] == 2
