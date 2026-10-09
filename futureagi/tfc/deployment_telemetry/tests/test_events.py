from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from tfc.deployment_telemetry.event_buffer import (
    load_event,
    pending_events,
    store_event,
)
from tfc.deployment_telemetry.events import (
    build_event,
    flush_events,
    record_event,
    record_request_event,
)
from tfc.deployment_telemetry.transport import compute_signature


@pytest.fixture(autouse=True)
def event_buffer_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_BUFFER_DIR", str(tmp_path / "telemetry"))
    monkeypatch.setattr(
        "tfc.deployment_telemetry.events.is_self_hosted_deployment", lambda: True
    )
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "false")


def test_event_contract_is_actor_aware_and_content_free():
    event = build_event(
        "actor_request_completed",
        instance_id=uuid4(),
        actor_type="agent",
        actor_id="agent-123",
        source="agent",
        properties={"method": "POST", "route": "agent_run", "status_code": 200},
    )

    assert event["actor_type"] == "agent"
    assert event["source"] == "agent"
    assert "prompt" not in json.dumps(event)
    assert "completion" not in json.dumps(event)

    with pytest.raises(ValueError, match="unsupported event properties"):
        build_event(
            "actor_request_completed",
            instance_id=uuid4(),
            actor_type="agent",
            actor_id="agent-123",
            source="agent",
            properties={"request_body": "secret"},
        )


def test_event_outbox_is_durable_and_deduplicated():
    event = build_event(
        "user_logged_in",
        instance_id=uuid4(),
        actor_type="human_user",
        actor_id="user-1",
        source="web",
    )
    first = store_event(event)
    second = store_event(event)

    assert first == second
    assert pending_events() == [first]
    assert load_event(first) == event


def test_event_outbox_supports_concurrent_writers():
    instance_id = uuid4()

    def write(index):
        return store_event(
            build_event(
                "user_logged_in",
                instance_id=instance_id,
                actor_type="human_user",
                actor_id=f"user-{index}",
                source="web",
            )
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(write, range(8)))

    assert len(set(paths)) == 8
    assert len(pending_events()) == 8


def test_event_flush_end_to_end_signs_and_deletes_outbox(monkeypatch):
    instance_id = uuid4()
    secret = "test-secret"
    event = build_event(
        "first_trace_received",
        instance_id=instance_id,
        actor_type="api_key",
        actor_id="key-1",
        source="sdk",
    )

    # Queue through the public API while keeping the DB boundary explicit.
    state = {"instance_id": instance_id, "instance_secret": secret}
    monkeypatch.setattr(
        "tfc.deployment_telemetry.events.get_or_create_telemetry_state",
        lambda: SimpleNamespace(instance_id=instance_id),
    )
    monkeypatch.setattr(
        "tfc.deployment_telemetry.events.DeploymentTelemetryState.objects.values",
        lambda *args: SimpleNamespace(first=lambda: state),
    )
    with patch("tfc.deployment_telemetry.events._schedule_flush"):
        assert record_event(
            "first_trace_received",
            actor_type="api_key",
            actor_id="key-1",
            source="sdk",
        )

    captured = {}

    def fake_post(url, data, headers, timeout):
        captured.update(url=url, data=data, headers=headers)
        assert headers["X-FAGI-Telemetry-Signature"] == compute_signature(secret, data)
        payload = json.loads(data)
        assert payload["instance_id"] == str(instance_id)
        assert payload["events"][0]["event_name"] == event["event_name"]
        return SimpleNamespace(status_code=202, json=lambda: {"status": "ok"})

    with patch("tfc.deployment_telemetry.transport.requests.post", fake_post):
        assert flush_events() == 1

    assert pending_events() == []
    assert captured["url"].endswith("/telemetry/events/")


def test_opt_out_drops_events_without_network(monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    with patch("tfc.deployment_telemetry.events._store_event") as store:
        assert (
            record_event(
                "user_created",
                actor_type="human_user",
                actor_id="user-1",
                source="system",
            )
            is False
        )
    store.assert_not_called()


def test_request_events_reuse_cached_install_id(monkeypatch):
    state = SimpleNamespace(instance_id=uuid4())
    state_calls = []
    monkeypatch.setattr(
        "tfc.deployment_telemetry.events.get_or_create_telemetry_state",
        lambda: state_calls.append(state) or state,
    )
    with patch("tfc.deployment_telemetry.events._schedule_flush"):
        assert record_event(
            "user_logged_in",
            actor_type="human_user",
            actor_id="user-1",
            source="web",
        )
        assert record_event(
            "user_logged_in",
            actor_type="human_user",
            actor_id="user-2",
            source="web",
        )

    assert len(state_calls) == 1


def test_request_event_attributes_api_key_actor_without_request_body():
    request = SimpleNamespace(
        path="/api/agent/run",
        method="POST",
        META={"HTTP_USER_AGENT": "futureagi-cli/1.0"},
        org_api_key=SimpleNamespace(id=42),
        user=SimpleNamespace(is_authenticated=True, id=7),
        organization=SimpleNamespace(id=9),
        workspace=SimpleNamespace(id=11),
        resolver_match=SimpleNamespace(route="api/agent/run"),
    )
    response = SimpleNamespace(status_code=201)
    with patch("tfc.deployment_telemetry.events.record_event", return_value=True) as record:
        assert record_request_event(request, response, 12.5)

    kwargs = record.call_args.kwargs
    assert kwargs["actor_type"] == "api_key"
    assert kwargs["source"] == "cli"
    assert kwargs["properties"]["method"] == "POST"
    assert "body" not in kwargs["properties"]
