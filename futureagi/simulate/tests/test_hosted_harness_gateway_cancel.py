"""A user stop: the gateway waits, bounded, for the guest to seal the call in flight before deleting the sandbox."""

import logging
import uuid
from types import SimpleNamespace

import pytest
from django.core.cache import cache

from simulate.models import HostedHarnessExecution
from simulate.services import hosted_harness_gateway as gateway_module
from simulate.services.hosted_harness import create_hosted_job, register_attempt
from simulate.services.hosted_harness_gateway import HostedHarnessGateway
from simulate.tests.hosted_job_payloads import hosted_job_payload


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


class _Sandbox:
    """A live sandbox as the provider returns it: the cancel signal is written to it and run in it."""

    def __init__(self):
        self.fs = SimpleNamespace(upload_file=lambda *_args, **_kwargs: None)
        self.timeouts = []
        self.process = SimpleNamespace(
            exec=lambda _command, **kwargs: self.timeouts.append(kwargs.get("timeout"))
        )


@pytest.mark.django_db
def test_cancel_seal_runs_after_the_signal_and_before_the_sandbox_is_deleted(
    organization, monkeypatch
):
    # A real stop finds the run's attempt live on its sandbox, with no cleanup recorded yet.
    job, _ = create_hosted_job(
        organization, hosted_job_payload(), idempotency_key="cancel-seal-order"
    )
    register_attempt(
        job.id,
        endpoint_base_url="https://platform.example.com",
        provider_ref="sandbox-1",
    )
    sandbox = _Sandbox()
    gateway = _gateway()
    gateway.client = SimpleNamespace(name="daytona", get=lambda _ref: sandbox)
    monkeypatch.setattr(
        "simulate.services.phone_telephony.cleanup_hosted_phone_rooms", lambda _: None
    )
    order = []
    monkeypatch.setattr(
        gateway, "_await_guest_seal", lambda _attempt: order.append("seal")
    )
    monkeypatch.setattr(
        gateway,
        "_delete_and_record",
        lambda _attempt, **_kwargs: order.append("delete") or job,
    )

    # A stop on the request path never waits: only the cancel activity does.
    gateway.cancel(job, reason="user_canceled")
    assert order == ["delete"]
    order.clear()
    gateway.cancel(job, reason="user_canceled", await_seal=True)

    assert order == ["seal", "delete"]
    # The signal to the guest is bounded, so a hung guest never holds up the seal and the delete.
    assert sandbox.timeouts and all(sandbox.timeouts)


def test_cancel_deletes_after_the_seal_timeout(monkeypatch, clock, caplog, settings):
    settings.HARNESS_CANCEL_SEAL_SECONDS = 0.1
    monkeypatch.setattr(HostedHarnessExecution, "objects", _InFlight([True]))
    # `terminal_stage == "canceled"` alone (set by cancel itself) never ends the wait.
    with caplog.at_level(logging.WARNING):
        _gateway()._await_guest_seal(_attempt())
    assert "hosted_cancel_seal_timeout" in caplog.text


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

    monkeypatch.setattr(gateway_module, "cache", Down())
    monkeypatch.setattr(
        HostedHarnessExecution, "objects", _InFlight([True, True, False])
    )
    _gateway()._await_guest_seal(_attempt())
    # The receipt landed one poll into the wait: the wait ran instead of being skipped for the cache.
    assert clock["t"] == 2


@pytest.mark.django_db
def test_stop_closes_a_run_whose_sandbox_never_started_with_no_sandbox_provider_configured(
    organization, monkeypatch
):
    from simulate.models import HostedHarnessJob
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.services.hosted_sandbox import SandboxProviderConfigurationError

    def unconfigured():
        raise SandboxProviderConfigurationError("DAYTONA_API_KEY is required")

    monkeypatch.setattr(gateway_module, "get_sandbox_provider", unconfigured)
    job, _ = create_hosted_job(
        organization, hosted_job_payload(), idempotency_key="cancel-no-provider"
    )

    # Anything that needs a sandbox still refuses up front.
    with pytest.raises(HostedHarnessError, match="DAYTONA_API_KEY"):
        HostedHarnessGateway()

    canceled = HostedHarnessGateway(require_provider=False).cancel(
        job, reason="user_canceled"
    )
    assert (canceled.state, canceled.cancel_reason) == (
        HostedHarnessJob.State.CANCELED,
        "user_canceled",
    )
