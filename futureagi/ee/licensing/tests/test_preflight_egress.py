"""Key removal during the local preflight must stop activation and check-in.

``_activate`` and ``send_heartbeat`` check the key before any instance lookup,
then do blocking local work before their POST: a select_for_update instance
lookup, and for the heartbeat the snapshot and usage collection (ClickHouse
and Postgres counts). These tests remove the key inside that work and send
through real httpx with a MockTransport that fails the test on any request
made once the key is gone. Removal by another thread is simulated by mutating
settings from inside the patched lookup.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from ee.licensing import activation_client, heartbeat, state
from ee.licensing.tests import test_keyless_egress as keyless_tests
from ee.licensing.tests.test_retained_gateway_egress import (
    KEYLESS,
    assert_denial,
    remove_key,
)

# Autouse fixture from test_keyless_egress, bound here so it applies.
isolated_install = keyless_tests.isolated_install


@pytest.fixture
def transport(monkeypatch, settings):
    attempts = []

    def handle(request):
        request.read()
        attempts.append(request)
        if not getattr(settings, "EE_LICENSE_KEY", "").strip():
            pytest.fail(
                "Forbidden HTTP after key removal during local preflight: "
                + str(request.url)
            )
        return httpx.Response(
            200,
            json={
                "access_token": "fixture-service-token",
                "scope": "enterprise",
                "gateway_url": "https://gateway.example.invalid",
                "expires_in": 3600,
            },
        )

    mock = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx.Client, "_transport_for_url", lambda self, url: mock)
    settings.EE_LICENSE_KEY = "fixture-license"
    return attempts


def remove_during(settings, removed_key, result=None):
    def blocking_work(*args, **kwargs):
        remove_key(settings, removed_key)
        return result

    return blocking_work


@KEYLESS
def test_activation_rechecks_key_after_blocking_instance_lookup(
    transport, monkeypatch, settings, removed_key
):
    monkeypatch.setattr(
        "tfc.deployment_telemetry.state.get_or_create_telemetry_state",
        remove_during(
            settings,
            removed_key,
            SimpleNamespace(instance_id=keyless_tests.INSTANCE_ID),
        ),
    )
    with pytest.raises(activation_client.ManagedServiceError) as caught:
        activation_client.get_service_token()
    assert_denial(caught.value)
    assert transport == []
    assert activation_client._cached_token is None


@KEYLESS
def test_activate_denial_after_lookup_is_typed_not_activation_failed(
    transport, monkeypatch, settings, removed_key
):
    # The broad transport handler in _activate must not turn the re-check's
    # denial into None (ACTIVATION_FAILED for managed callers).
    monkeypatch.setattr(
        "tfc.deployment_telemetry.config.get_version",
        remove_during(settings, removed_key, "1.8.0"),
    )
    with pytest.raises(activation_client.ManagedServiceError) as caught:
        activation_client._activate()
    assert_denial(caught.value)
    assert transport == []


@KEYLESS
def test_heartbeat_rechecks_key_after_usage_collection(
    transport, monkeypatch, settings, removed_key
):
    monkeypatch.setattr(
        state, "get_snapshot", lambda: SimpleNamespace(license_id="fixture-id")
    )
    monkeypatch.setattr(
        heartbeat, "collect_heartbeat_usage", remove_during(settings, removed_key, {})
    )
    assert heartbeat.send_heartbeat() is False
    assert transport == []


@KEYLESS
def test_heartbeat_rechecks_key_after_state_lookup(
    transport, monkeypatch, settings, removed_key
):
    monkeypatch.setattr(
        state, "get_snapshot", lambda: SimpleNamespace(license_id="fixture-id")
    )
    monkeypatch.setattr(
        "tfc.deployment_telemetry.state.get_or_create_telemetry_state",
        remove_during(
            settings,
            removed_key,
            SimpleNamespace(instance_id=keyless_tests.INSTANCE_ID),
        ),
    )
    assert heartbeat.send_heartbeat() is False
    assert transport == []


def test_heartbeat_skip_after_preflight_is_logged(transport, monkeypatch, settings):
    from structlog.testing import capture_logs

    monkeypatch.setattr(
        state, "get_snapshot", lambda: SimpleNamespace(license_id="fixture-id")
    )
    monkeypatch.setattr(
        heartbeat, "collect_heartbeat_usage", remove_during(settings, "", {})
    )
    with capture_logs() as logs:
        assert heartbeat.send_heartbeat() is False
    assert "enterprise_heartbeat_skipped_no_license_key" in {
        entry["event"] for entry in logs
    }
    assert transport == []


def test_licensed_preflight_controls(transport, monkeypatch):
    monkeypatch.setattr(
        state, "get_snapshot", lambda: SimpleNamespace(license_id="fixture-id")
    )
    assert activation_client.get_service_token().access_token == (
        "fixture-service-token"
    )
    assert heartbeat.send_heartbeat() is True
    assert len(transport) == 2
    activation, checkin = transport
    assert activation.url.path.endswith("/v1/self-hosted/activations")
    assert json.loads(activation.content) == {
        "instance_id": keyless_tests.INSTANCE_ID,
        "version": "1.8.0",
        "license_key_hash": activation_client.hash_key("fixture-license"),
    }
    assert checkin.url.path.endswith("/v1/enterprise/heartbeats")
    assert json.loads(checkin.content)["license_id"] == "fixture-id"
    assert "X-FutureAGI-Heartbeat-Signature" in checkin.headers


@pytest.mark.parametrize("remove", [False, True], ids=["licensed_control", "removed"])
def test_activate_window_between_key_check_and_post(settings, monkeypatch, remove):
    """Key removed during the instance lookup that precedes the activation POST."""
    settings.EE_LICENSE_KEY = "fixture-license"
    posts = []

    def lookup():
        if remove:
            settings.EE_LICENSE_KEY = ""  # removed after the up-front check
        return SimpleNamespace(instance_id="00000000-0000-0000-0000-000000000001")

    monkeypatch.setattr(
        "tfc.deployment_telemetry.state.get_or_create_telemetry_state", lookup
    )

    def post(url, **kwargs):
        posts.append(
            (url, repr(getattr(settings, "EE_LICENSE_KEY", None)), kwargs["json"])
        )
        return httpx.Response(
            200,
            json={"access_token": "t", "scope": "enterprise", "expires_in": 3600},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)
    if not remove:
        token = activation_client._activate()
        assert token is not None and token.access_token == "t"
        assert len(posts) == 1
        return
    with pytest.raises(activation_client.ManagedServiceError) as caught:
        activation_client._activate()
    assert_denial(caught.value)
    assert posts == [], f"activation POST after key removal: {posts}"


@pytest.mark.parametrize("remove", [False, True], ids=["licensed_control", "removed"])
def test_heartbeat_window_between_key_check_and_post(settings, monkeypatch, remove):
    """Key removed during usage collection that precedes the heartbeat POST."""
    settings.EE_LICENSE_KEY = "fixture-license"
    posts = []
    monkeypatch.setattr(
        state, "get_snapshot", lambda: SimpleNamespace(license_id="lic-1")
    )

    def usage():
        if remove:
            settings.EE_LICENSE_KEY = ""
        return {}

    monkeypatch.setattr(heartbeat, "collect_heartbeat_usage", usage)

    def post(url, **kwargs):
        posts.append((url, repr(getattr(settings, "EE_LICENSE_KEY", None))))
        return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    sent = heartbeat.send_heartbeat()
    if not remove:
        assert sent is True and len(posts) == 1
        return
    assert sent is False
    assert posts == [], f"heartbeat POST after key removal (sent={sent}): {posts}"
