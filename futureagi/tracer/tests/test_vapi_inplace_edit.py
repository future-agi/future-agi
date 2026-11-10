"""Authenticated in-place edits must keep Vapi's selected version coherent.

Only external provider/artifact HTTP and collector delivery are stubbed. The
edit route, encryption, ORM, configuration resolver and scheduled poll are real.
"""

from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models.user import OrgApiKey
from simulate.models.agent_definition import AgentDefinition, ProviderCredentials
from simulate.models.agent_version import AgentVersion
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.observability_provider import ObservabilityProvider
from tracer.models.project import Project
from tracer.utils import observability_provider as op
from tracer.utils.vapi_recording import VapiRecordingService

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def vapi_setup(user, workspace):
    project = Project.objects.create(
        name="Synthetic voice imports",
        organization=workspace.organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    provider = ObservabilityProvider.objects.create(
        project=project,
        organization=workspace.organization,
        workspace=workspace,
        provider="vapi",
        enabled=True,
        last_fetched_at=timezone.now() - timedelta(hours=1),
        poll_state={"other": {"sentinel": "preserved"}},
    )
    key = OrgApiKey.no_workspace_objects.create(
        name="Synthetic user key",
        type="user",
        user=user,
        organization=workspace.organization,
        workspace=workspace,
    )
    client = APIClient()
    client.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    clear_workspace_context()
    return provider, client


def seed_version(provider, assistant, number=1, status="active", agent=None):
    if agent is None:
        agent = AgentDefinition.objects.create(
            agent_name="Synthetic voice agent",
            agent_type="voice",
            provider="vapi",
            organization=provider.organization,
            workspace=provider.workspace,
            inbound=True,
            observability_provider=provider,
            assistant_id=assistant,
            api_key="synthetic-key-A",
        )
    version = AgentVersion.objects.create(
        agent_definition=agent,
        organization=provider.organization,
        workspace=provider.workspace,
        version_number=number,
        status=status,
        configuration_snapshot={"assistant_id": assistant, "description": "preserved"},
        commit_message="Synthetic fixture",
    )
    credentials = ProviderCredentials.objects.create(
        agent_version=version,
        provider_type="vapi",
        assistant_id=assistant,
        api_key="synthetic-key-A",
    )
    return agent, version, credentials


def poll(provider):
    clear_workspace_context()
    return op.fetch_logs_for_provider(
        provider.id,
        scheduled=True,
        start_time=None,
        end_time=None,
    )


def fake_upstream(monkeypatch, now):
    requests_seen, imported = [], []

    def listing(url, *, headers, params, **kwargs):
        assistant = params["assistantId"]
        requests_seen.append((headers["Authorization"], assistant))
        # Account-wide keys may successfully list either assistant. A mismatch
        # can silently import the wrong call and advance progress.
        call = {
            "id": f"call-for-{assistant}",
            "assistantId": assistant,
            "status": "ended",
            "createdAt": (now - timedelta(minutes=5)).isoformat(),
            "endedAt": (now - timedelta(minutes=4)).isoformat(),
            "updatedAt": (now - timedelta(minutes=3)).isoformat(),
            "messages": [{"role": "user", "message": "Synthetic call"}],
            "artifact": {},
        }
        return Mock(status_code=200, json=lambda: [call])

    def export(span, provider_type, call_id):
        imported.append((call_id, span.span_attributes["raw_log"]["assistantId"]))
        return 1

    monkeypatch.setattr("tracer.services.observability_providers.requests.get", listing)
    monkeypatch.setattr(
        VapiRecordingService, "fetch_and_parse_call_logs", Mock(return_value=[])
    )
    monkeypatch.setattr(op, "_export_provider_call_to_collector", export)
    return requests_seen, imported


@pytest.mark.parametrize(
    "initial_assistant,wait_for_cooldown",
    [("assistant-A", False), ("", False), ("", True)],
    ids=[
        "rotate-A-to-B",
        "repair-empty-before-cooldown",
        "repair-empty-after-cooldown",
    ],
)
def test_authenticated_edit_polls_new_assistant_and_key(
    vapi_setup,
    monkeypatch,
    initial_assistant,
    wait_for_cooldown,
):
    provider, client = vapi_setup
    agent, version, credentials = seed_version(provider, initial_assistant)
    now = timezone.now()
    monkeypatch.setattr(op.timezone, "now", lambda: now)
    requests_seen, imported = fake_upstream(monkeypatch, now)
    watermark = provider.last_fetched_at

    if not initial_assistant:
        assert poll(provider) is None
        provider.refresh_from_db()
        health = provider.poll_state["configuration_health"]
        assert health["reason_code"] == "missing_assistant_id"
        assert op._parse(health["next_retry_at"]) > now
        assert requests_seen == []

    path = f"/simulate/agent-definitions/{agent.id}/edit/"
    assert (
        APIClient()
        .put(path, {"assistant_id": "assistant-B"}, format="json")
        .status_code
        == 401
    )
    response = client.put(
        path,
        {"assistant_id": "assistant-B", "api_key": "synthetic-key-B"},
        format="json",
    )
    assert response.status_code == 200, response.data
    clear_workspace_context()
    agent.refresh_from_db()
    version.refresh_from_db()
    credentials.refresh_from_db()
    assert agent.assistant_id == credentials.assistant_id == "assistant-B"
    assert credentials.get_api_key() == "synthetic-key-B"
    assert credentials.api_key.startswith("enc::")

    if wait_for_cooldown:
        now += timedelta(minutes=11)
    outcome = poll(provider)
    provider.refresh_from_db()
    assert requests_seen == [("Bearer synthetic-key-B", "assistant-B")]
    assert imported == [("call-for-assistant-B", "assistant-B")]
    assert outcome == op.StoreOutcome(1, 0, 0)
    assert "configuration_health" not in provider.poll_state
    assert provider.last_fetched_at > watermark
    assert provider.poll_state["other"] == {"sentinel": "preserved"}
    assert version.configuration_snapshot == {
        "assistant_id": "assistant-B",
        "description": "preserved",
    }


@pytest.mark.parametrize("active", [True, False], ids=["active", "latest-fallback"])
@pytest.mark.parametrize("key", [None, "********", "synthetic-key-B"])
def test_edit_changes_only_the_selected_version(vapi_setup, monkeypatch, active, key):
    provider, client = vapi_setup
    agent, older, old_credentials = seed_version(
        provider, "assistant-A", status="active" if active else "archived"
    )
    _, newer, new_credentials = seed_version(
        provider, "assistant-C", number=2, status="draft", agent=agent
    )
    # The definition can differ from the selected historical version.
    agent.assistant_id = "assistant-C"
    agent.save(update_fields=["assistant_id"])
    selected, untouched = (older, newer) if active else (newer, older)
    untouched_credentials = new_credentials if active else old_credentials
    original_snapshot = untouched.configuration_snapshot.copy()
    original_key = untouched_credentials.api_key
    payload = {"assistant_id": "assistant-B"}
    if key is not None:
        payload["api_key"] = key
    response = client.put(
        f"/simulate/agent-definitions/{agent.id}/edit/", payload, format="json"
    )
    assert response.status_code == 200, response.data
    clear_workspace_context()
    selected.refresh_from_db()
    untouched.refresh_from_db()
    untouched_credentials.refresh_from_db()
    assert selected.configuration_snapshot["assistant_id"] == "assistant-B"
    assert untouched.configuration_snapshot == original_snapshot
    assert untouched_credentials.api_key == original_key
    assert untouched_credentials.assistant_id == original_snapshot["assistant_id"]
    expected_key = "synthetic-key-B" if key == "synthetic-key-B" else "synthetic-key-A"
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    assert poll(provider) == op.StoreOutcome(1, 0, 0)
    assert requests_seen == [(f"Bearer {expected_key}", "assistant-B")]
    assert imported == [("call-for-assistant-B", "assistant-B")]


@pytest.mark.parametrize(
    "payload", [{"description": "Edited"}, {"api_key": "synthetic-key-B"}]
)
def test_edit_without_assistant_preserves_selected_snapshot(vapi_setup, payload):
    provider, client = vapi_setup
    agent, version, _ = seed_version(provider, "assistant-A")
    agent.assistant_id = "assistant-C"
    agent.save(update_fields=["assistant_id"])
    original = version.configuration_snapshot.copy()
    response = client.put(
        f"/simulate/agent-definitions/{agent.id}/edit/", payload, format="json"
    )
    assert response.status_code == 200, response.data
    version.refresh_from_db()
    assert version.configuration_snapshot == original


def test_failed_snapshot_write_rolls_back_credentials_and_definition(
    vapi_setup, monkeypatch
):
    from django.db import connection

    provider, client = vapi_setup
    agent, version, credentials = seed_version(provider, "assistant-A")
    original_snapshot = version.configuration_snapshot.copy()
    original_key = credentials.api_key

    def fail_snapshot_write(execute, sql, params, many, context):
        if sql.startswith('UPDATE "simulate_agent_version"'):
            raise RuntimeError("Synthetic snapshot write failure")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(fail_snapshot_write):
        response = client.put(
            f"/simulate/agent-definitions/{agent.id}/edit/",
            {"assistant_id": "assistant-B", "api_key": "synthetic-key-B"},
            format="json",
        )
    assert response.status_code == 500
    clear_workspace_context()
    agent.refresh_from_db()
    credentials.refresh_from_db()
    version.refresh_from_db()
    assert agent.assistant_id == credentials.assistant_id == "assistant-A"
    assert credentials.api_key == original_key
    assert version.configuration_snapshot == original_snapshot


def test_historical_activation_still_isolates_assistant_and_key(
    vapi_setup, monkeypatch
):
    provider, _ = vapi_setup
    agent, older, _ = seed_version(provider, "assistant-A", status="archived")
    agent.assistant_id = "assistant-B"
    agent.save(update_fields=["assistant_id"])
    _, _, credentials = seed_version(provider, "assistant-B", number=2, agent=agent)
    credentials.api_key = "synthetic-key-B"
    credentials.save()
    older.activate()
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    assert poll(provider) == op.StoreOutcome(1, 0, 0)
    assert requests_seen == [("Bearer synthetic-key-A", "assistant-A")]
    assert imported == [("call-for-assistant-A", "assistant-A")]
    agent.refresh_from_db()
    assert agent.assistant_id == "assistant-B"
