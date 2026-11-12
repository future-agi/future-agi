"""W11/F9: daily admission caps may only turn on with a live spend ledger.

All gateway responses are synthetic at the HTTP transport boundary. The real
writer, transaction, alias normalization, serializers and endpoints remain in
use. Postgres-backed.
"""

import asyncio
import time
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from django.core.cache import cache
from django.db import connection

from accounts.models import Organization
from agentcc.models.cap_activation import AgentccCapActivation, AgentccCapPolicyRevision
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.org_config import AgentccOrgConfigSerializer
from agentcc.services import cap_ledger_status, gateway_client, org_config_writer
from agentcc.services.org_config_writer import (
    UnknownConfigSections,
    write_config_version,
)
from agentcc.views import org_config as org_config_view

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.django_db]

UPDATE_URL = "/agentcc/gateways/default/update-config/"
CREATE_URL = "/agentcc/org-configs/"
READ_URL = "/agentcc/org-configs/active/"
UPDATE_PUSH = "agentcc.views.gateway.AgentccGatewayViewSet._push_current_config"
VERSION_PUSH = (
    "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
)
MESSAGE = (
    "Daily caps can't be turned on in this release. "
    "Budgets in Gateway > Budgets still limit spend."
)
NONREADY = [
    pytest.param({"admission_cap_ledger": "absent"}, None, "absent", id="absent"),
    pytest.param(
        {"admission_cap_ledger": "unavailable"}, None, "unavailable", id="unavailable"
    ),
    pytest.param({}, None, "unknown", id="old-gateway-missing-field"),
    pytest.param(None, httpx.ReadTimeout("synthetic timeout"), "unknown", id="timeout"),
]


def sections(enabled=True, *, alias=False):
    budgets = {
        "enabled": True,
        "admission_caps_enabled": enabled,
        "fail_mode": "closed",
        "no_max_tokens": "clamp",
        "agents": {"agent-a": {"limit": 0.05, "period": "daily"}},
        "key_ids": {"key-a": {"limit": 0.05, "period": "daily"}},
    }
    if alias:
        budgets["admissionCapsEnabled"] = budgets.pop("admission_caps_enabled")
    return {
        "budgets": budgets,
        "routing": {"agents": {"key_bindings": {"key-a": "agent-a"}}},
    }


def saved_config(org, *, enabled=False, active=True, version=1, alias=False):
    config = AgentccOrgConfig(
        organization=org,
        version=version,
        is_active=active,
        **sections(enabled, alias=alias),
    )
    config.disable_auto_workspace_assignment = True
    config.save()
    return config


def snapshot(org):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=org)
        .order_by("version")
        .values("pk", "version", "is_active", "budgets", "routing")
    )


def assert_refusal(response, status):
    assert response.status_code == 409, response.content
    body = response.json()
    assert body["code"] == "admission_cap_ledger_unavailable"
    assert body["message"] == MESSAGE
    # True on cloud and self-hosted: it blames no setup and names no one to ask.
    message = body["message"].lower()
    assert "deployment" not in message and "administrator" not in message
    assert body["ledger_status"] == status


def assert_no_stamp(org):
    assert not AgentccCapActivation.objects.filter(organization=org).exists()
    assert not AgentccCapPolicyRevision.objects.filter(organization=org).exists()


def read_status(client):
    response = client.get(READ_URL)
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert "admission_cap_ledger" in result, result
    assert response.json()["admission_cap_ledger"] == result["admission_cap_ledger"]
    return result["admission_cap_ledger"]


@pytest.fixture(autouse=True)
def ready_cap_ledger(ready_cap_ledger, monkeypatch):
    """Override the shared ready default: W11 exercises the real status reader."""
    monkeypatch.setattr(cap_ledger_status, "get_ledger_status", ready_cap_ledger)


@pytest.fixture(autouse=True)
def isolated_read_cache(settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "w11-admission-cap-ledger",
        }
    }
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def probe(monkeypatch):
    state = SimpleNamespace(
        body={"admission_cap_ledger": "ready"}, error=None, status=200, calls=[]
    )
    monkeypatch.setattr(
        gateway_client, "AGENTCC_GATEWAY_INTERNAL_URL", "http://ledger.example.com"
    )
    monkeypatch.setattr(gateway_client, "AGENTCC_ADMIN_TOKEN", "")

    def handle_request(_transport, request):
        assert request.method == "GET", request.method
        assert request.url.path == "/readyz", request.url.path
        state.calls.append(request)
        if state.error is not None:
            raise state.error
        return httpx.Response(state.status, json=state.body)

    async def handle_async_request(transport, request):
        return handle_request(transport, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", handle_async_request
    )
    return state


@pytest.mark.parametrize("body,error,ledger_status", NONREADY)
@pytest.mark.parametrize("alias", [False, True], ids=["canonical", "alias"])
@pytest.mark.parametrize(
    "has_active", [False, True], ids=["first-config", "off-config"]
)
def test_update_off_to_on_refused_atomically(
    auth_client, organization, probe, body, error, ledger_status, alias, has_active
):
    if has_active:
        saved_config(organization)
    before = snapshot(organization)
    probe.body, probe.error = body, error
    with patch(UPDATE_PUSH, return_value=True) as push:
        response = auth_client.post(UPDATE_URL, sections(alias=alias), format="json")
    assert_refusal(response, ledger_status)
    assert snapshot(organization) == before
    assert_no_stamp(organization)
    push.assert_not_called()
    assert len(probe.calls) == 1


@pytest.mark.parametrize(
    "url,push_path", [(UPDATE_URL, UPDATE_PUSH), (CREATE_URL, VERSION_PUSH)]
)
def test_ready_allows_activation(auth_client, organization, probe, url, push_path):
    saved_config(organization)
    with patch(push_path, return_value=True) as push:
        response = auth_client.post(url, sections(), format="json")
    assert response.status_code == 200, response.content
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    assert active.version == 2
    assert active.budgets["admission_caps_enabled"] is True
    assert active.budgets["policy_revision"] > 0
    assert active.budgets["agents"]["agent-a"]["activated_at"]
    push.assert_called_once()
    assert probe.calls


@pytest.mark.parametrize("body,error,ledger_status", NONREADY)
def test_create_off_to_on_uses_same_refusal(
    auth_client, organization, probe, body, error, ledger_status
):
    saved_config(organization)
    before = snapshot(organization)
    probe.body, probe.error = body, error
    with patch(VERSION_PUSH, return_value=True) as push:
        response = auth_client.post(CREATE_URL, sections(), format="json")
    assert_refusal(response, ledger_status)
    assert snapshot(organization) == before
    assert_no_stamp(organization)
    push.assert_not_called()


# Restoring a version (activate) is never refused for daily caps and never
# asks the gateway for its ledger: test_restore_daily_caps.py.


@pytest.mark.parametrize(
    "enabled,patch_body,expected",
    [
        (True, {"budgets": {"admission_caps_enabled": False}}, False),
        (True, {"budgets": {"admissionCapsEnabled": False}}, False),
        (True, {"budgets": {"agents": {"agent-a": {"limit": 0.04}}}}, True),
        (True, {"cache": {"enabled": False}}, True),
        (False, {"cache": {"enabled": False}}, False),
        (False, {"budgets": {"agents": {"agent-a": {"limit": 0.04}}}}, False),
    ],
    ids=[
        "disable",
        "disable-alias",
        "limit-on",
        "unrelated-on",
        "unrelated-off",
        "limit-off",
    ],
)
def test_nonactivating_updates_do_not_probe_or_refuse(
    auth_client, organization, probe, enabled, patch_body, expected
):
    saved_config(organization, enabled=enabled)
    probe.body = {"admission_cap_ledger": "absent"}
    with patch(UPDATE_PUSH, return_value=True) as push:
        response = auth_client.post(UPDATE_URL, deepcopy(patch_body), format="json")
    assert response.status_code == 200, response.content
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    assert active.budgets["admission_caps_enabled"] is expected
    push.assert_called_once()
    assert probe.calls == []


@pytest.mark.parametrize("target_enabled", [False, True], ids=["on-to-off", "on-to-on"])
def test_nonactivating_version_switch_is_allowed(
    auth_client, organization, probe, target_enabled
):
    saved_config(organization, enabled=True)
    target = saved_config(organization, enabled=target_enabled, active=False, version=2)
    probe.body = {"admission_cap_ledger": "absent"}
    with patch(VERSION_PUSH, return_value=True) as push:
        response = auth_client.post(
            f"{CREATE_URL}{target.pk}/activate/", {}, format="json"
        )
    assert response.status_code == 200, response.content
    target.refresh_from_db()
    assert target.is_active
    assert target.budgets["admission_caps_enabled"] is target_enabled
    push.assert_called_once()


@pytest.mark.parametrize(
    "body,error,ledger_status",
    [
        pytest.param({"admission_cap_ledger": "ready"}, None, "ready", id="ready"),
        *NONREADY,
    ],
)
def test_active_read_exposes_runtime_status_without_persisting(
    auth_client, organization, probe, body, error, ledger_status
):
    saved_config(organization)
    before = snapshot(organization)
    probe.body, probe.error = body, error
    assert read_status(auth_client) == ledger_status
    assert snapshot(organization) == before
    assert_no_stamp(organization)
    assert len(probe.calls) == 1


@pytest.mark.parametrize(
    "body,error,ledger_status",
    [
        pytest.param({"admission_cap_ledger": "ready"}, None, "ready", id="ready"),
        *NONREADY,
    ],
)
def test_active_without_saved_config_reports_ledger_in_envelope(
    auth_client, organization, probe, body, error, ledger_status
):
    assert snapshot(organization) == []
    probe.body, probe.error = body, error
    for _ in range(2):
        response = auth_client.get(READ_URL)
        assert response.status_code == 200, response.content
        envelope = response.json()
        assert envelope["result"] is None
        assert envelope["admission_cap_ledger"] == ledger_status
    assert len(probe.calls) == 1, "The null-result read must use the same read cache"
    assert snapshot(organization) == []
    assert_no_stamp(organization)


@pytest.mark.parametrize("value", [None, "READY", "future-status", True, {}, []])
def test_invalid_signal_is_unknown(auth_client, organization, probe, value):
    saved_config(organization)
    probe.body = {"admission_cap_ledger": value}
    assert read_status(auth_client) == "unknown"


@pytest.mark.parametrize("body", [None, [], "ready"])
def test_nonobject_readyz_is_unknown(auth_client, organization, probe, body):
    saved_config(organization)
    probe.body = body
    assert read_status(auth_client) == "unknown"


@pytest.mark.parametrize("status", [401, 503])
def test_error_response_is_unknown_even_if_body_claims_ready(
    auth_client, organization, probe, status
):
    saved_config(organization)
    probe.status = status
    assert read_status(auth_client) == "unknown"


def test_probe_timeout_is_at_most_two_seconds(auth_client, organization, probe):
    saved_config(organization)
    assert read_status(auth_client) == "ready"
    assert len(probe.calls) == 1
    timeouts = probe.calls[0].extensions["timeout"]
    assert timeouts
    assert all(value is not None and 0 < value <= 2 for value in timeouts.values())


def test_reads_cache_then_expire_within_sixty_seconds(
    auth_client, organization, probe, monkeypatch
):
    saved_config(organization)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    assert read_status(auth_client) == "ready"
    probe.body = {"admission_cap_ledger": "absent"}
    assert read_status(auth_client) == "ready"
    assert len(probe.calls) == 1
    now += 61
    assert read_status(auth_client) == "absent"
    assert len(probe.calls) == 2


@pytest.mark.parametrize("path", ["update"])
def test_activation_bypasses_stale_ready_cache(auth_client, organization, probe, path):
    saved_config(organization)
    target = None
    if path == "activate":
        target = saved_config(organization, enabled=True, active=False, version=2)
    before = snapshot(organization)
    assert read_status(auth_client) == "ready"
    probe.body = {"admission_cap_ledger": "absent"}
    url = UPDATE_URL if path == "update" else f"{CREATE_URL}{target.pk}/activate/"
    payload = sections() if path == "update" else {}
    with (
        patch(UPDATE_PUSH, return_value=True) as update_push,
        patch(VERSION_PUSH, return_value=True) as version_push,
    ):
        response = auth_client.post(url, payload, format="json")
    assert_refusal(response, "absent")
    assert snapshot(organization) == before
    assert_no_stamp(organization)
    update_push.assert_not_called()
    version_push.assert_not_called()
    assert len(probe.calls) == 2


def test_activation_bypasses_cached_absent(auth_client, organization, probe):
    saved_config(organization)
    probe.body = {"admission_cap_ledger": "absent"}
    assert read_status(auth_client) == "absent"
    probe.body = {"admission_cap_ledger": "ready"}
    with patch(UPDATE_PUSH, return_value=True) as push:
        response = auth_client.post(UPDATE_URL, sections(), format="json")
    assert response.status_code == 200, response.content
    push.assert_called_once()
    assert len(probe.calls) == 2


def test_read_cache_is_scoped_to_gateway(auth_client, organization, probe, monkeypatch):
    saved_config(organization)
    assert read_status(auth_client) == "ready"
    monkeypatch.setattr(
        gateway_client,
        "AGENTCC_GATEWAY_INTERNAL_URL",
        "http://other-ledger.example.com",
    )
    probe.body = {"admission_cap_ledger": "absent"}
    assert read_status(auth_client) == "absent"
    assert len(probe.calls) == 2


def test_read_cache_is_scoped_to_organization(organization, probe):
    first = saved_config(organization)
    second_org = Organization.objects.create(name="Other synthetic ledger organization")
    second = saved_config(second_org)
    first_data = AgentccOrgConfigSerializer(first).data
    assert first_data.get("admission_cap_ledger") == "ready"
    probe.body = {"admission_cap_ledger": "absent"}
    second_data = AgentccOrgConfigSerializer(second).data
    assert second_data.get("admission_cap_ledger") == "absent"
    assert len(probe.calls) == 2


def test_runtime_field_is_read_only_and_not_a_config_section(organization):
    serializer = AgentccOrgConfigSerializer()
    assert "admission_cap_ledger" in serializer.fields
    assert serializer.fields["admission_cap_ledger"].read_only
    with pytest.raises(UnknownConfigSections) as caught:
        write_config_version(organization, {"admission_cap_ledger": "ready"})
    assert caught.value.sections == ("admission_cap_ledger",)
    assert snapshot(organization) == []


class TrickleReadyBody(httpx.SyncByteStream, httpx.AsyncByteStream):
    """A valid ready response whose idle gaps never reach HTTPX's timeout."""

    def __init__(self):
        self.chunks = 0
        self.closed = False

    def __iter__(self):
        for _ in range(10):
            time.sleep(0.3)
            self.chunks += 1
            yield b" "
        yield b'{"admission_cap_ledger":"ready"}'

    async def __aiter__(self):
        for _ in range(10):
            await asyncio.sleep(0.3)
            self.chunks += 1
            yield b" "
        yield b'{"admission_cap_ledger":"ready"}'

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


@pytest.fixture
def trickle_ready_body(monkeypatch, probe):
    body = TrickleReadyBody()

    def handle_request(_transport, request):
        assert request.method == "GET"
        assert request.url.path == "/readyz"
        probe.calls.append(request)
        return httpx.Response(200, stream=body)

    async def handle_async_request(transport, request):
        return handle_request(transport, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", handle_async_request
    )
    return body


def test_trickling_readyz_has_a_total_deadline_and_closes(trickle_ready_body):
    client = gateway_client.GatewayClient("http://ledger.example.com")
    started = time.monotonic()
    status = client.admission_cap_ledger_status()
    elapsed = time.monotonic() - started
    assert status == "unknown", (status, elapsed)
    # Allow scheduling/cleanup overhead, not the three-second valid response.
    assert elapsed <= 2.5, elapsed
    assert 1 <= trickle_ready_body.chunks < 10
    assert trickle_ready_body.closed


def test_trickling_readyz_cannot_commit_activation(
    auth_client, organization, trickle_ready_body
):
    saved_config(organization)
    before = snapshot(organization)
    with patch(UPDATE_PUSH, return_value=True) as push:
        response = auth_client.post(UPDATE_URL, sections(), format="json")
    assert_refusal(response, "unknown")
    assert snapshot(organization) == before
    assert_no_stamp(organization)
    push.assert_not_called()
    assert trickle_ready_body.closed


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("path", ["update", "create"])
@pytest.mark.parametrize("ledger_status", ["ready", "absent"])
def test_activation_probes_before_any_org_lock_or_atomic_block(
    auth_client, organization, probe, monkeypatch, path, ledger_status
):
    saved_config(organization)
    url, payload = UPDATE_URL, sections()
    if path == "create":
        url = CREATE_URL
    elif path == "activate":
        target = saved_config(organization, enabled=True, active=False, version=2)
        url, payload = f"{CREATE_URL}{target.pk}/activate/", {}
    before = snapshot(organization)
    probe.body = {"admission_cap_ledger": ledger_status}
    events = []
    reader = cap_ledger_status.get_ledger_status
    lock_org = org_config_writer.lock_org

    def observed_reader(org_id, *, fresh=False):
        if fresh:
            events.append(("probe", connection.in_atomic_block))
        return reader(org_id, fresh=fresh)

    def observed_lock(org_id):
        lock_org(org_id)
        events.append(("lock", connection.in_atomic_block))

    monkeypatch.setattr(cap_ledger_status, "get_ledger_status", observed_reader)
    monkeypatch.setattr(org_config_writer, "lock_org", observed_lock)
    monkeypatch.setattr(org_config_view, "lock_org", observed_lock)
    with (
        patch(UPDATE_PUSH, return_value=True) as update_push,
        patch(VERSION_PUSH, return_value=True) as version_push,
    ):
        response = auth_client.post(url, payload, format="json")
    if ledger_status == "ready":
        assert response.status_code == 200, response.content
        assert update_push.call_count + version_push.call_count == 1
    else:
        assert_refusal(response, "absent")
        assert snapshot(organization) == before
        assert_no_stamp(organization)
        update_push.assert_not_called()
        version_push.assert_not_called()
    assert events[0] == ("probe", False), events
    assert [event for event in events if event[0] == "probe"] == [("probe", False)]
    assert any(event[0] == "lock" for event in events), events


@pytest.mark.parametrize("path", ["update"])
def test_transition_discovered_under_lock_refuses_without_a_late_probe(
    auth_client, organization, probe, monkeypatch, path
):
    active = saved_config(organization, enabled=True)
    url, payload = UPDATE_URL, sections()
    if path == "activate":
        target = saved_config(organization, enabled=True, active=False, version=2)
        url, payload = f"{CREATE_URL}{target.pk}/activate/", {}
    before = snapshot(organization)
    lock_org = org_config_writer.lock_org

    def changed_active_at_lock(org_id):
        lock_org(org_id)
        # Model the locked read differing from the unlocked preflight. This
        # injected change rolls back with a refusal; no second DB worker needed.
        AgentccOrgConfig.no_workspace_objects.filter(pk=active.pk).update(
            budgets=sections(enabled=False)["budgets"]
        )

    monkeypatch.setattr(org_config_writer, "lock_org", changed_active_at_lock)
    with (
        patch(UPDATE_PUSH, return_value=True) as update_push,
        patch(VERSION_PUSH, return_value=True) as version_push,
    ):
        response = auth_client.post(url, payload, format="json")
    assert_refusal(response, "unknown")
    assert snapshot(organization) == before
    assert_no_stamp(organization)
    assert probe.calls == []
    update_push.assert_not_called()
    version_push.assert_not_called()
