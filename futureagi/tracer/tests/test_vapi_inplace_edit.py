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
            authentication_method="api_key",
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


@pytest.fixture(params=["edit", "operations"])
def update_route(request):
    return request.param


def update_agent(client, agent, payload, route):
    if route == "edit":
        return client.put(
            f"/simulate/agent-definitions/{agent.id}/edit/", payload, format="json"
        )
    return client.patch(
        f"/simulate/api/agent-definition-operations/{agent.id}/", payload, format="json"
    )


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
    update_route,
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

    assert (
        update_agent(
            APIClient(), agent, {"assistant_id": "assistant-B"}, update_route
        ).status_code
        == 401
    )
    response = update_agent(
        client,
        agent,
        {"assistant_id": "assistant-B", "api_key": "synthetic-key-B"},
        update_route,
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
def test_edit_changes_only_the_selected_version(
    vapi_setup, monkeypatch, active, key, update_route
):
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
    response = update_agent(client, agent, payload, update_route)
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


@pytest.mark.parametrize("assistant", [None, ""], ids=["explicit-null", "empty-string"])
@pytest.mark.parametrize("key", [None, "********", "synthetic-key-B"])
@pytest.mark.parametrize("active", [True, False], ids=["active", "latest-fallback"])
@pytest.mark.parametrize("empty_snapshot", [False, True])
def test_explicit_assistant_clear_stops_selected_version_imports(
    vapi_setup, monkeypatch, assistant, key, active, empty_snapshot, update_route
):
    provider, client = vapi_setup
    agent, older, old_credentials = seed_version(
        provider, "assistant-A", status="active" if active else "archived"
    )
    # Operations requires a phone target when its web-bridge assistant is cleared.
    agent.contact_number = "+12025550123"
    agent.save(update_fields=["contact_number"])
    _, newer, new_credentials = seed_version(
        provider, "assistant-C", number=2, status="draft", agent=agent
    )
    selected, untouched = (older, newer) if active else (newer, older)
    credentials, untouched_credentials = (
        (old_credentials, new_credentials)
        if active
        else (new_credentials, old_credentials)
    )
    original_snapshot = untouched.configuration_snapshot.copy()
    original_key = untouched_credentials.api_key
    if empty_snapshot:
        # Legacy rows can lack a snapshot; null still needs to clear credentials.
        AgentVersion.objects.filter(pk=selected.pk).update(configuration_snapshot={})
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    watermark = provider.last_fetched_at
    if update_route == "operations":
        # This route permits clearing only while observability is off.
        provider.enabled = False
        provider.save(update_fields=["enabled"])
    payload = {"assistant_id": assistant}
    if key is not None:
        payload["api_key"] = key
    response = update_agent(client, agent, payload, update_route)
    assert response.status_code == 200, response.data
    clear_workspace_context()
    if update_route == "operations":
        # A later scheduled poll must reject the cleared identity, not reuse it.
        provider.enabled = True
        provider.save(update_fields=["enabled"])
    assert poll(provider) is None
    provider.refresh_from_db()
    assert requests_seen == []
    assert imported == []
    assert provider.last_fetched_at == watermark
    assert (
        provider.poll_state["configuration_health"]["reason_code"]
        == "missing_assistant_id"
    )
    assert provider.poll_state["other"] == {"sentinel": "preserved"}
    agent.refresh_from_db()
    selected.refresh_from_db()
    credentials.refresh_from_db()
    untouched.refresh_from_db()
    untouched_credentials.refresh_from_db()
    assert agent.assistant_id == assistant
    assert credentials.assistant_id == ""
    assert selected.configuration_snapshot == (
        {"assistant_id": ""}
        if empty_snapshot
        else {"assistant_id": "", "description": "preserved"}
    )
    expected_key = "synthetic-key-B" if key == "synthetic-key-B" else "synthetic-key-A"
    assert credentials.get_api_key() == expected_key
    assert untouched.configuration_snapshot == original_snapshot
    assert untouched_credentials.assistant_id == original_snapshot["assistant_id"]
    assert untouched_credentials.api_key == original_key


@pytest.mark.parametrize(
    "payload", [{"description": "Edited"}, {"api_key": "synthetic-key-B"}]
)
def test_edit_without_assistant_preserves_selected_snapshot(
    vapi_setup, monkeypatch, payload, update_route
):
    provider, client = vapi_setup
    agent, version, credentials = seed_version(provider, "assistant-A")
    agent.assistant_id = "assistant-C"
    agent.save(update_fields=["assistant_id"])
    original = version.configuration_snapshot.copy()
    response = update_agent(client, agent, payload, update_route)
    assert response.status_code == 200, response.data
    version.refresh_from_db()
    credentials.refresh_from_db()
    assert version.configuration_snapshot == original
    assert credentials.assistant_id == "assistant-A"
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    assert poll(provider) == op.StoreOutcome(1, 0, 0)
    expected_key = payload.get("api_key", "synthetic-key-A")
    assert requests_seen == [(f"Bearer {expected_key}", "assistant-A")]
    assert imported == [("call-for-assistant-A", "assistant-A")]


@pytest.mark.parametrize("assistant", ["assistant-B", None, ""])
def test_failed_snapshot_write_rolls_back_credentials_and_definition(
    vapi_setup, update_route, assistant
):
    from django.db import connection

    provider, client = vapi_setup
    agent, version, credentials = seed_version(provider, "assistant-A")
    # Keep operations validation satisfied even when the identity is cleared.
    agent.contact_number = "+12025550123"
    agent.save(update_fields=["contact_number"])
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    original_snapshot = version.configuration_snapshot.copy()
    original_key = credentials.api_key

    def fail_snapshot_write(execute, sql, params, many, context):
        if sql.startswith('UPDATE "simulate_agent_version"'):
            raise RuntimeError("Synthetic snapshot write failure")
        return execute(sql, params, many, context)

    client.raise_request_exception = False
    with connection.execute_wrapper(fail_snapshot_write):
        response = update_agent(
            client,
            agent,
            {"assistant_id": assistant, "api_key": "synthetic-key-B"},
            update_route,
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


def test_edit_repairs_empty_snapshot(vapi_setup, monkeypatch, update_route):
    provider, client = vapi_setup
    agent, version, credentials = seed_version(provider, "")
    # Bypass save()'s automatic initial snapshot to reproduce legacy empty rows.
    AgentVersion.objects.filter(pk=version.pk).update(configuration_snapshot={})
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    assert poll(provider) is None
    response = update_agent(
        client,
        agent,
        {"assistant_id": "assistant-B", "api_key": "synthetic-key-B"},
        update_route,
    )
    assert response.status_code == 200, response.data
    clear_workspace_context()
    version.refresh_from_db()
    credentials.refresh_from_db()
    assert version.configuration_snapshot == {"assistant_id": "assistant-B"}
    assert credentials.assistant_id == "assistant-B"
    assert poll(provider) == op.StoreOutcome(1, 0, 0)
    assert requests_seen == [("Bearer synthetic-key-B", "assistant-B")]
    assert imported == [("call-for-assistant-B", "assistant-B")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("key", ["********", "synthetic-key-B"])
@pytest.mark.parametrize(
    "identity,expected",
    [
        ({}, "assistant-A"),
        ({"assistant_id": None}, ""),
        ({"assistant_id": ""}, ""),
        ({"assistant_id": "assistant-B"}, "assistant-B"),
    ],
    ids=["omitted", "explicit-null", "empty-string", "replace"],
)
def test_update_serializer_synchronizes_selected_version(
    vapi_setup, key, identity, expected
):
    from simulate.serializers.agent_definition import AgentDefinitionUpdateSerializer

    provider, _ = vapi_setup
    agent, selected, credentials = seed_version(provider, "assistant-A")
    _, historical, historical_credentials = seed_version(
        provider, "assistant-C", number=2, status="draft", agent=agent
    )
    historical_snapshot = historical.configuration_snapshot.copy()
    historical_key = historical_credentials.api_key
    serializer = AgentDefinitionUpdateSerializer(
        agent, data={**identity, "api_key": key}, partial=True
    )
    assert serializer.is_valid(), serializer.errors
    # This serializer must supply its own transaction, not rely on an API view.
    serializer.save()
    agent.refresh_from_db()
    selected.refresh_from_db()
    credentials.refresh_from_db()
    historical.refresh_from_db()
    historical_credentials.refresh_from_db()
    assert selected.configuration_snapshot["assistant_id"] == expected
    assert credentials.assistant_id == expected
    assert agent.assistant_id == identity.get("assistant_id", "assistant-A")
    expected_key = "synthetic-key-A" if key == "********" else key
    assert credentials.get_api_key() == expected_key
    assert agent.api_key == expected_key
    assert historical.configuration_snapshot == historical_snapshot
    assert historical_credentials.api_key == historical_key
    assert historical_credentials.assistant_id == "assistant-C"
