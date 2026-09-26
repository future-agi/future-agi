"""Deployment telemetry keeps working with every third-party key unset, honours
FUTURE_AGI_TELEMETRY_DISABLED, and never holds up startup or sign-up.

No database needed: the state machine's DB edges are patched out. The
DB-backed behaviour (claims, persistence, buffering) lives in test_sender.py.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.apps import AppConfig
from django.test import override_settings

from tfc.deployment_telemetry.models import DeploymentTelemetryState
from tfc.deployment_telemetry.transport import TelemetryClient, TelemetryResponse

NO_THIRD_PARTY_KEYS = {
    "HUBSPOT_API_TOKEN": "",
    "SLACK_WEBHOOK_CHANNEL": "",
    "ERROR_LOGS_WEBHOOK": "",
}


@override_settings(**NO_THIRD_PARTY_KEYS)
def test_signup_hook_registers_without_third_party_keys_and_without_waiting():
    """The first account triggers registration on a background thread even
    though HubSpot, Slack and the analytics keys are all unset, and the
    caller returns while registration is still in flight."""
    from accounts.utils import _fire_deployment_telemetry_registration

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_registration():
        entered.set()
        release.wait(timeout=5)
        finished.set()
        return True

    with (
        patch(
            "tfc.deployment_telemetry.config.is_self_hosted_deployment",
            return_value=True,
        ),
        patch(
            "tfc.deployment_telemetry.sender.attempt_registration",
            side_effect=slow_registration,
        ) as attempt,
    ):
        _fire_deployment_telemetry_registration()
        assert entered.wait(timeout=5), "registration never started"
        # The hook has returned while registration is still running.
        assert not finished.is_set()
        release.set()
        assert finished.wait(timeout=5)

    attempt.assert_called_once_with()


def test_registration_goes_to_the_configured_url(monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_URL", "https://telemetry.example.test/")
    response = MagicMock(status_code=201)
    response.json.return_value = {"instance_secret": "s"}

    with patch(
        "tfc.deployment_telemetry.transport.requests.post", return_value=response
    ) as post:
        result = TelemetryClient().post("/telemetry/register/", {"instance_id": "i"})

    assert result.ok is True
    assert post.call_args.args[0] == (
        "https://telemetry.example.test/telemetry/register/"
    )


def test_opted_out_registration_is_the_minimal_ping(monkeypatch):
    """FUTURE_AGI_TELEMETRY_DISABLED=true sends one registration with no user
    data, and never builds the email list at all."""
    from tfc.deployment_telemetry import sender

    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    state = SimpleNamespace(
        instance_id=uuid4(), telemetry_disabled=True, instance_secret=""
    )

    with (
        patch.object(sender, "is_self_hosted_deployment", return_value=True),
        patch.object(sender, "_claim_registration", return_value=(state, True, False)),
        patch.object(sender, "_complete_registration") as complete,
        patch.object(
            sender,
            "build_full_registration_payload",
            side_effect=AssertionError("user list built while opted out"),
        ),
        patch.object(
            sender.TelemetryClient,
            "post",
            return_value=TelemetryResponse(ok=True, data={}),
        ) as post,
    ):
        assert sender.ensure_registration() == (False, state.instance_id)

    path, payload = post.call_args.args
    assert path == "/telemetry/register/"
    assert payload["telemetry_disabled"] is True
    assert set(payload) == {
        "schema_version",
        "instance_id",
        "version",
        "deployment_type",
        "timestamp",
        "telemetry_disabled",
    }
    assert complete.call_args.args[1] == (
        DeploymentTelemetryState.RegistrationKind.MINIMAL_DISABLED
    )


def test_opted_out_cycle_collects_and_sends_no_heartbeat(monkeypatch):
    from tfc.deployment_telemetry import sender

    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")

    with (
        patch.object(sender, "is_self_hosted_deployment", return_value=True),
        patch.object(sender, "_log_disclosure"),
        patch.object(sender, "ensure_registration", return_value=(False, uuid4())),
        patch.object(sender, "clear_buffer") as clear_buffer,
        patch.object(sender, "collect_counts") as collect_counts,
        patch.object(sender, "store_window") as store_window,
        patch.object(sender, "_flush_buffer") as flush,
    ):
        assert sender.run_telemetry_cycle() == {"skipped": True, "reason": "disabled"}

    clear_buffer.assert_called_once_with()
    collect_counts.assert_not_called()
    store_window.assert_not_called()
    flush.assert_not_called()


def test_nothing_runs_at_app_startup():
    """Telemetry is driven by the Temporal schedule and the sign-up hook only.
    A ``ready()`` that phoned home would put a network call on every boot."""
    from tfc.deployment_telemetry.apps import DeploymentTelemetryConfig

    assert DeploymentTelemetryConfig.ready is AppConfig.ready
