"""Authenticated in-place edits must keep Vapi's selected version coherent.

Only external provider/artifact HTTP and collector delivery are stubbed. The
edit route, encryption, ORM, configuration resolver and scheduled poll are real.
"""

import gzip
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models.user import OrgApiKey
from simulate.models.agent_definition import AgentDefinition, ProviderCredentials
from simulate.models.agent_version import AgentVersion
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.constants.external_endpoints import ObservabilityRoutes
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


def seed_version(
    provider, assistant, number=1, status="active", agent=None, *, with_credentials=True
):
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
    credentials = None
    if with_credentials:
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
    if route == "save-version":
        return client.post(
            f"/simulate/agent-definitions/{agent.id}/versions/create/",
            {
                **payload,
                "commit_message": "Synthetic saved version",
                "observability_enabled": True,
            },
            format="json",
        )
    if route == "edit":
        return client.put(
            f"/simulate/agent-definitions/{agent.id}/edit/", payload, format="json"
        )
    return client.patch(
        f"/simulate/api/agent-definition-operations/{agent.id}/", payload, format="json"
    )


def _configuration_state(agent):
    from django.db.models import Q

    return (
        list(AgentDefinition.no_workspace_objects.filter(pk=agent.pk).values()),
        list(
            AgentVersion.no_workspace_objects.filter(agent_definition=agent)
            .order_by("pk")
            .values()
        ),
        list(
            ProviderCredentials.no_workspace_objects.filter(
                Q(agent_definition=agent) | Q(agent_version__agent_definition=agent)
            )
            .order_by("pk")
            .values()
        ),
        list(
            ObservabilityProvider.no_workspace_objects.filter(
                pk=agent.observability_provider_id
            ).values()
        ),
    )


@contextmanager
def _assert_no_configuration_writes():
    from django.db import connection

    writes = []
    tables = tuple(
        model._meta.db_table
        for model in (
            AgentDefinition,
            AgentVersion,
            ProviderCredentials,
            ObservabilityProvider,
        )
    )

    def record_writes(execute, sql, params, many, context):
        if sql.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")) and any(
            table in sql for table in tables
        ):
            writes.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(record_writes):
        yield
    assert writes == []


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


def test_versioned_agent_without_credentials_uses_the_definitions_pair(
    monkeypatch, vapi_setup
):
    """A version with no credential row, and no legacy row, lists with the
    definition's key and assistant together, as listing did before versions
    held credentials. The snapshot's assistant is never paired with that key."""
    provider, _ = vapi_setup
    agent = AgentDefinition.objects.create(
        agent_name="Synthetic voice agent",
        agent_type="voice",
        provider="vapi",
        organization=provider.organization,
        workspace=provider.workspace,
        inbound=True,
        authentication_method="api_key",
        observability_provider=provider,
        assistant_id="assistant-D",
        api_key="synthetic-key-D",
    )
    AgentVersion.objects.create(
        agent_definition=agent,
        organization=provider.organization,
        workspace=provider.workspace,
        version_number=1,
        status="active",
        configuration_snapshot={"assistant_id": "assistant-S"},
        commit_message="Synthetic fixture",
    )
    assert not ProviderCredentials.objects.filter(agent_version__agent_definition=agent)
    configuration = VapiRecordingService.configuration_from_agent_definition(agent)
    assert (configuration.api_key, configuration.assistant_id) == (
        "synthetic-key-D",
        "assistant-D",
    )
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    poll(provider)
    assert requests_seen == [("Bearer synthetic-key-D", "assistant-D")]
    assert imported == [("call-for-assistant-D", "assistant-D")]
    provider.refresh_from_db()
    assert "configuration_health" not in provider.poll_state


@pytest.mark.parametrize("route", ["edit", "operations", "save-version"])
@pytest.mark.parametrize(
    "key", [None, "********", "synt...ey-A"], ids=["omitted", "masked", "display-mask"]
)
def test_save_without_credential_row_keeps_definition_pair(
    vapi_setup, monkeypatch, route, key
):
    from simulate.services.agent_setup import agent_summary

    provider, client = vapi_setup
    agent, original, _ = seed_version(provider, "assistant-A", with_credentials=False)
    assert not ProviderCredentials.objects.filter(agent_version=original).exists()
    assert not ProviderCredentials.objects.filter(agent_definition=agent).exists()
    payload = {"assistant_id": "assistant-B"}
    if key is not None:
        payload["api_key"] = key
    if route == "save-version":
        response = client.post(
            f"/simulate/agent-definitions/{agent.id}/versions/create/",
            {
                **payload,
                "commit_message": "Synthetic saved version",
                "observability_enabled": True,
            },
            format="json",
        )
        expected_status = 201
    else:
        response = update_agent(client, agent, payload, route)
        expected_status = 200
    assert response.status_code == expected_status, response.data
    clear_workspace_context()
    agent.refresh_from_db()
    selected = agent.active_version
    credentials = selected.credentials
    assert credentials.provider_type == "vapi"
    assert credentials.api_key == ""
    assert credentials.assistant_id == "assistant-B"
    assert selected.configuration_snapshot["assistant_id"] == "assistant-B"
    assert agent.api_key == "synthetic-key-A"
    assert agent.assistant_id == "assistant-B"
    summary = agent_summary(agent.id)
    assert summary.key_present
    assert summary.credential_id is None
    assert summary.provider_agent_id == "assistant-B"
    if route == "save-version":
        assert selected.pk != original.pk
        original.refresh_from_db()
        assert original.status == "archived"
        assert original.configuration_snapshot["assistant_id"] == "assistant-A"
        assert not ProviderCredentials.objects.filter(agent_version=original).exists()
    else:
        assert selected.pk == original.pk

    watermark = provider.last_fetched_at
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    assert poll(provider) == op.StoreOutcome(1, 0, 0)
    assert requests_seen == [("Bearer synthetic-key-A", "assistant-B")]
    assert imported == [("call-for-assistant-B", "assistant-B")]
    VapiRecordingService.fetch_and_parse_call_logs.assert_called_once_with(
        call_id="call-for-assistant-B", api_key="synthetic-key-A", legacy_url=None
    )
    provider.refresh_from_db()
    assert "configuration_health" not in provider.poll_state
    assert provider.last_fetched_at > watermark
    assert provider.poll_state["other"] == {"sentinel": "preserved"}


@pytest.mark.parametrize("route", ["edit", "operations", "save-version"])
@pytest.mark.parametrize(
    "key",
    [None, "********", "synt...ey-B", "synthetic-key-C"],
    ids=["omitted", "masked", "display-mask", "replacement"],
)
@pytest.mark.parametrize("send_assistant", [False, True], ids=["keep", "set"])
def test_save_empty_active_row_does_not_resurrect_stale_legacy(
    vapi_setup, monkeypatch, route, key, send_assistant
):
    provider, client = vapi_setup
    agent, original, original_credentials = seed_version(provider, "assistant-S")
    agent.api_key = "synthetic-key-B"
    agent.assistant_id = "assistant-B"
    agent.save(update_fields=["api_key", "assistant_id"])
    original_credentials.api_key = ""
    original_credentials.save()
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    legacy_ciphertext = legacy.api_key
    original_snapshot = original.configuration_snapshot.copy()
    # Both rows coexist: before save, the empty active row selects B/B, not
    # legacy A/A and not the active row/snapshot's assistant S.
    configuration = VapiRecordingService.configuration_from_agent_definition(agent)
    assert (configuration.api_key, configuration.assistant_id) == (
        "synthetic-key-B",
        "assistant-B",
    )
    payload = {"description": "Synthetic edit"}
    if key is not None:
        payload["api_key"] = key
    if send_assistant:
        payload["assistant_id"] = "assistant-B"
    response = update_agent(client, agent, payload, route)
    expected_status = 201 if route == "save-version" else 200
    assert response.status_code == expected_status, response.data
    clear_workspace_context()
    agent.refresh_from_db()
    selected = agent.active_version
    credentials = selected.credentials
    expected_row_assistant = (
        "assistant-B"
        if send_assistant
        else ("" if route == "save-version" else "assistant-S")
    )
    assert credentials.provider_type == "vapi"
    assert credentials.assistant_id == expected_row_assistant
    replacing = key == "synthetic-key-C"
    if replacing:
        assert credentials.get_api_key() == "synthetic-key-C"
        assert credentials.api_key.startswith("enc::")
    else:
        # Preserve the empty source marker rather than materializing a key
        # from either legacy or the definition alongside the wrong assistant.
        assert credentials.api_key == ""
    assert agent.api_key == ("synthetic-key-C" if replacing else "synthetic-key-B")
    assert agent.assistant_id == "assistant-B"
    legacy.refresh_from_db()
    assert legacy.agent_version_id is None
    assert legacy.agent_definition_id == agent.id
    assert legacy.api_key == legacy_ciphertext
    assert legacy.assistant_id == "assistant-A"
    if route == "save-version":
        assert selected.pk != original.pk
        original.refresh_from_db()
        original_credentials.refresh_from_db()
        assert original.status == "archived"
        assert original.configuration_snapshot == original_snapshot
        assert original_credentials.api_key == ""
        assert original_credentials.assistant_id == "assistant-S"
        assert selected.configuration_snapshot["assistant_id"] == "assistant-B"
        assert response.data["version"]["api_key"] == (
            "synt...ey-C" if replacing else "synt...ey-B"
        )
    else:
        assert selected.pk == original.pk
        assert selected.configuration_snapshot == {
            **original_snapshot,
            "assistant_id": expected_row_assistant,
        }

    expected_key = "synthetic-key-C" if replacing else "synthetic-key-B"
    expected_assistant = (
        "assistant-S"
        if replacing and not send_assistant and route != "save-version"
        else "assistant-B"
    )
    watermark = provider.last_fetched_at
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    assert poll(provider) == op.StoreOutcome(1, 0, 0)
    assert requests_seen == [(f"Bearer {expected_key}", expected_assistant)]
    assert imported == [(f"call-for-{expected_assistant}", expected_assistant)]
    VapiRecordingService.fetch_and_parse_call_logs.assert_called_once_with(
        call_id=f"call-for-{expected_assistant}", api_key=expected_key, legacy_url=None
    )
    provider.refresh_from_db()
    assert provider.last_fetched_at > watermark
    assert "configuration_health" not in provider.poll_state
    assert provider.poll_state["other"] == {"sentinel": "preserved"}


@pytest.mark.parametrize("key", ["", "synthetic-key-C"])
def test_vapi_wrong_provider_row_blocks_masked_preflight(vapi_setup, monkeypatch, key):
    provider, client = vapi_setup
    agent, version, credentials = seed_version(provider, "assistant-S")
    credentials.provider_type = "retell"
    credentials.api_key = key
    credentials.save()
    ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    upstream = Mock()
    monkeypatch.setattr(
        "tracer.services.observability_providers.requests.get", upstream
    )
    detail = client.get(
        f"/simulate/agent-definitions/{agent.id}/versions/{version.id}/"
    )
    assert detail.status_code == 200, detail.data
    assert detail.data["api_key"] is None
    assert detail.data["configuration_snapshot"]["assistant_id"] == ""
    for mask in ("synt...ey-A", "synt...ey-C"):
        preflight = client.post(
            "/tracer/observability-provider/verify_api_key/",
            {"provider": "vapi", "api_key": mask, "agent_id": str(agent.id)},
            format="json",
        )
        assert preflight.status_code == 400
    upstream.assert_not_called()
    credentials.refresh_from_db()
    assert credentials.provider_type == "retell"
    assert credentials.get_api_key() == key


@pytest.mark.parametrize("route", ["edit", "operations", "save-version"])
def test_migrated_bland_omitted_then_masked_saves_keep_legacy_key(
    vapi_setup, monkeypatch, route
):
    from simulate.services.agent_definition import resolve_api_key_for_version

    provider, client = vapi_setup
    provider.provider = "bland"
    provider.save(update_fields=["provider"])
    agent, original, _ = seed_version(provider, "assistant-S", with_credentials=False)
    agent.provider = "bland"
    agent.api_key = "synthetic-key-B"
    agent.assistant_id = "assistant-B"
    agent.contact_number = "+16505550100"
    agent.save(update_fields=["provider", "api_key", "assistant_id", "contact_number"])
    original.configuration_snapshot = {
        **original.create_snapshot(commit_message="Synthetic Bland version"),
        "assistant_id": "assistant-S",
    }
    original.save(update_fields=["configuration_snapshot"])
    # 0071 stores Bland as VAPI; 0076 links the latest version without
    # clearing agent_definition. The older active version remains rowless.
    newer = agent.create_version(description="Synthetic draft", commit_message="Draft")
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        agent_version=newer,
        provider_type=ProviderCredentials.ProviderType.VAPI,
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    legacy_ciphertext = legacy.api_key
    response = update_agent(
        client,
        agent,
        {
            "provider": "bland",
            "assistant_id": "assistant-S",
            "description": "Synthetic omitted-key edit",
        },
        route,
    )
    assert response.status_code == (201 if route == "save-version" else 200), (
        response.data
    )
    clear_workspace_context()
    agent.refresh_from_db()
    selected = agent.active_version
    assert selected.credentials.provider_type == "vapi"
    assert selected.credentials.api_key == ""
    assert resolve_api_key_for_version(selected) == "synthetic-key-A"
    assert resolve_api_key_for_version(selected, for_save=True) == "synthetic-key-A"

    requests_seen = []

    def upstream(url, *, headers, **kwargs):
        requests_seen.append((url, headers))
        return Mock(status_code=200)

    monkeypatch.setattr(
        "tracer.services.observability_providers.requests.get", upstream
    )
    for save_number in (1, 2):
        detail = client.get(
            f"/simulate/agent-definitions/{agent.id}/versions/{selected.id}/"
        )
        assert detail.status_code == 200, detail.data
        snapshot = detail.data["configuration_snapshot"]
        assert snapshot["provider"] == "bland"
        assert snapshot["assistant_id"] == "assistant-S"
        assert detail.data["api_key"] == "synt...ey-A"
        assert "synthetic-key-A" not in str(detail.data)
        preflight = {
            "provider": "bland",
            "api_key": detail.data["api_key"],
            "agent_id": str(agent.id),
        }
        key_check = client.post(
            "/tracer/observability-provider/verify_api_key/", preflight, format="json"
        )
        assistant_check = client.post(
            "/tracer/observability-provider/verify_assistant_id/",
            {**preflight, "assistant_id": snapshot["assistant_id"]},
            format="json",
        )
        assert key_check.status_code == 200, key_check.data
        assert assistant_check.status_code == 200, assistant_check.data
        assert requests_seen[-2:] == [
            (
                ObservabilityRoutes.BLAND_ME_URL.value,
                {"authorization": "synthetic-key-A"},
            ),
            (
                f"{ObservabilityRoutes.BLAND_PATHWAY_URL.value}/assistant-S",
                {"authorization": "synthetic-key-A"},
            ),
        ]
        saved = update_agent(
            client,
            agent,
            {
                "provider": "bland",
                "api_key": detail.data["api_key"],
                "assistant_id": snapshot["assistant_id"],
                "contact_number": snapshot["contact_number"],
                "description": f"Synthetic masked-key edit {save_number}",
            },
            "save-version",
        )
        assert saved.status_code == 201, saved.data
        clear_workspace_context()
        agent.refresh_from_db()
        selected = agent.active_version
        assert selected.credentials.provider_type == "vapi"
        assert selected.credentials.get_api_key() == "synthetic-key-A"
        assert selected.configuration_snapshot["assistant_id"] == "assistant-S"
        assert saved.data["version"]["api_key"] == "synt...ey-A"
    legacy.refresh_from_db()
    assert legacy.agent_version_id == newer.id
    assert legacy.agent_definition_id == agent.id
    assert legacy.api_key == legacy_ciphertext
    assert legacy.assistant_id == "assistant-A"


@pytest.mark.parametrize("route", ["edit", "operations", "save-version"])
@pytest.mark.parametrize("source", ["keyed", "legacy", "legacy-version"])
def test_vapi_read_surfaces_and_masked_carry_forward_use_selected_pair(
    vapi_setup, monkeypatch, route, source
):
    provider, client = vapi_setup
    agent, original, _ = seed_version(provider, "assistant-S", with_credentials=False)
    agent.api_key = "synthetic-key-B"
    agent.assistant_id = "assistant-B"
    agent.save(update_fields=["api_key", "assistant_id"])
    credential_version = original if source == "keyed" else None
    if source == "legacy-version":
        _, credential_version, _ = seed_version(
            provider,
            "assistant-L",
            number=2,
            status="draft",
            agent=agent,
            with_credentials=False,
        )
    credentials = ProviderCredentials.objects.create(
        agent_definition=agent if source != "keyed" else None,
        agent_version=credential_version,
        provider_type="vapi",
        api_key="synthetic-key-A",
        assistant_id="" if source == "legacy-version" else "assistant-A",
    )
    expected_assistant = "assistant-L" if source == "legacy-version" else "assistant-A"
    detail = client.get(
        f"/simulate/agent-definitions/{agent.id}/versions/{original.id}/"
    )
    assert detail.status_code == 200, detail.data
    assert detail.data["api_key"] == "synt...ey-A"
    assert detail.data["configuration_snapshot"]["assistant_id"] == expected_assistant
    # Both definition serializers also feed editable read surfaces.
    from simulate.serializers.agent_definition import AgentDefinitionSerializer
    from simulate.serializers.response.agent_definition import (
        AgentDefinitionResponseSerializer,
    )

    for serializer in (AgentDefinitionSerializer, AgentDefinitionResponseSerializer):
        representation = serializer(agent).data
        assert representation["api_key"] == "synt...ey-A"
        assert representation["assistant_id"] == expected_assistant
        assert "synthetic-key-A" not in str(representation)

    upstream = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr(
        "tracer.services.observability_providers.requests.get", upstream
    )
    preflight = client.post(
        "/tracer/observability-provider/verify_assistant_id/",
        {
            "provider": "vapi",
            "api_key": detail.data["api_key"],
            "assistant_id": detail.data["configuration_snapshot"]["assistant_id"],
            "agent_id": str(agent.id),
        },
        format="json",
    )
    assert preflight.status_code == 200, preflight.data
    assert upstream.call_args.args[0].endswith(f"/{expected_assistant}")
    assert upstream.call_args.kwargs["headers"] == {
        "Authorization": "Bearer synthetic-key-A"
    }
    # No assistant supplied: carry the selected source's identity with its key.
    saved = update_agent(client, agent, {"api_key": detail.data["api_key"]}, route)
    assert saved.status_code == (201 if route == "save-version" else 200), saved.data
    clear_workspace_context()
    agent.refresh_from_db()
    selected = agent.active_version
    assert selected.credentials.get_api_key() == "synthetic-key-A"
    assert selected.credentials.assistant_id == expected_assistant
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    assert poll(provider) == op.StoreOutcome(1, 0, 0)
    assert requests_seen == [("Bearer synthetic-key-A", expected_assistant)]
    assert imported == [(f"call-for-{expected_assistant}", expected_assistant)]
    credentials.refresh_from_db()
    if source == "legacy-version":
        assert credentials.agent_version_id == credential_version.id
        assert credentials.assistant_id == ""


@pytest.mark.parametrize("pinned", [True, False], ids=["pinned", "latest-fallback"])
@pytest.mark.parametrize(
    "source,expected_mask,expected_assistant",
    [
        ("empty-definition", "synt...ey-B", "assistant-B"),
        ("missing-legacy", "synt...ey-A", "assistant-A"),
        ("keyed-stale-snapshot", "synt...ey-C", "assistant-C"),
        ("wrong-provider", None, ""),
        ("unreadable", None, ""),
    ],
)
def test_run_test_detail_exposes_effective_masked_pair_for_ui_gate(
    vapi_setup, pinned, source, expected_mask, expected_assistant
):
    from simulate.models import RunTest

    provider, client = vapi_setup
    agent, selected, _ = seed_version(provider, "assistant-S", with_credentials=False)
    agent.api_key = "synthetic-key-B"
    agent.assistant_id = "assistant-B"
    agent.save(update_fields=["api_key", "assistant_id"])
    if pinned:
        # A pinned historical run must not silently display the latest pair.
        seed_version(provider, "assistant-other", number=2, status="draft", agent=agent)
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    credentials = None
    if source != "missing-legacy":
        row_key = "" if source == "empty-definition" else "synthetic-key-C"
        if source == "unreadable":
            row_key = "enc::invalid"
        credentials = ProviderCredentials.objects.create(
            agent_version=selected,
            provider_type="retell" if source == "wrong-provider" else "vapi",
            api_key=row_key,
            assistant_id="assistant-C",
        )
    stored_snapshot = selected.configuration_snapshot.copy()
    stored_key = credentials.api_key if credentials else None
    legacy_key = legacy.api_key
    run = RunTest.objects.create(
        name="Synthetic evaluation gate",
        agent_definition=agent,
        agent_version=selected if pinned else None,
        organization=provider.organization,
        workspace=provider.workspace,
    )
    url = f"/simulate/run-tests/{run.id}/"
    assert APIClient().get(url).status_code == 401
    response = client.get(url)
    assert response.status_code == 200, response.data
    version_data = response.data["agent_version"]
    assert str(version_data["id"]) == str(selected.id)
    assert version_data["api_key"] == expected_mask
    assert version_data["configuration_snapshot"]["assistant_id"] == expected_assistant
    assert response.data["agent_definition_detail"]["agent_type"] == "voice"
    # TestEvaluationPage's existing gate (no raw secret needed in the browser).
    opens_update_keys_dialog = not (
        version_data["api_key"]
        and version_data["configuration_snapshot"].get("assistant_id")
    )
    assert opens_update_keys_dialog is (source in ("wrong-provider", "unreadable"))
    for key in ("synthetic-key-A", "synthetic-key-B", "synthetic-key-C"):
        assert key not in str(response.data)
    if pinned and source != "unreadable":
        enabled = client.patch(
            f"{url}components/", {"enable_tool_evaluation": True}, format="json"
        )
        assert enabled.status_code == (400 if opens_update_keys_dialog else 200), (
            enabled.data
        )
        run.refresh_from_db()
        assert run.enable_tool_evaluation is not opens_update_keys_dialog
    clear_workspace_context()
    selected.refresh_from_db()
    legacy.refresh_from_db()
    assert selected.configuration_snapshot == stored_snapshot
    assert legacy.api_key == legacy_key
    assert legacy.agent_version_id is None
    if credentials:
        credentials.refresh_from_db()
        assert credentials.api_key == stored_key
        assert credentials.assistant_id == "assistant-C"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("old_provider", ["bland", "retell"])
@pytest.mark.parametrize("snapshot_provider", [True, False], ids=["authored", "legacy"])
@pytest.mark.parametrize("active", [True, False], ids=["active", "latest-fallback"])
def test_explicit_provider_change_updates_selected_version_and_runtime_reads(
    vapi_setup, update_route, old_provider, snapshot_provider, active
):
    from ee.voice.temporal.activities.voice_small import prepare_call
    from simulate.models import RunTest, Scenarios
    from simulate.models.test_execution import CallExecution, TestExecution
    from simulate.services.agent_definition import (
        resolve_provider_configuration_for_version,
    )
    from simulate.temporal.types.activities import PrepareCallInput

    provider, client = vapi_setup
    agent, older, older_credentials = seed_version(
        provider, "assistant-old-1", status="active" if active else "archived"
    )
    agent.provider = old_provider
    agent.save(update_fields=["provider"])
    _, newer, newer_credentials = seed_version(
        provider, "assistant-old-2", number=2, status="draft", agent=agent
    )
    for version, credentials in (
        (older, older_credentials),
        (newer, newer_credentials),
    ):
        version.configuration_snapshot.update(
            provider=old_provider, contact_number="+16505550100"
        )
        version.save(update_fields=["configuration_snapshot"])
        # Migration 0071 encodes Bland as VAPI; it is not authored for Vapi.
        credentials.provider_type = "vapi" if old_provider == "bland" else "retell"
        credentials.save()
    selected, untouched = (older, newer) if active else (newer, older)
    selected_credentials = older_credentials if active else newer_credentials
    untouched_credentials = newer_credentials if active else older_credentials
    if not snapshot_provider:
        del selected.configuration_snapshot["provider"]
        selected.save(update_fields=["configuration_snapshot"])
    before = selected.configuration_snapshot.copy()
    untouched_snapshot = untouched.configuration_snapshot.copy()
    untouched_key = untouched_credentials.api_key
    payload = {
        "provider": "vapi",
        "api_key": "synthetic-key-B",
        "assistant_id": "assistant-B",
    }
    assert update_agent(APIClient(), agent, payload, update_route).status_code == 401
    saved = update_agent(client, agent, payload, update_route)
    assert saved.status_code == 200, saved.data
    saved_agent = saved.data["agent"] if update_route == "edit" else saved.data
    assert saved_agent["provider"] == "vapi"
    assert saved_agent["api_key"] == "synt...ey-B"
    assert saved_agent["assistant_id"] == "assistant-B"
    clear_workspace_context()
    agent.refresh_from_db()
    selected.refresh_from_db()
    selected_credentials.refresh_from_db()
    untouched.refresh_from_db()
    untouched_credentials.refresh_from_db()
    assert selected.configuration_snapshot == {
        **before,
        "provider": "vapi",
        "assistant_id": "assistant-B",
    }
    assert selected_credentials.provider_type == "vapi"
    assert selected_credentials.get_api_key() == "synthetic-key-B"
    assert selected_credentials.assistant_id == "assistant-B"
    assert untouched.configuration_snapshot == untouched_snapshot
    assert untouched_credentials.api_key == untouched_key
    assert untouched_credentials.provider_type == (
        "vapi" if old_provider == "bland" else "retell"
    )

    detail = client.get(f"/simulate/agent-definitions/{agent.id}/")
    assert detail.status_code == 200, detail.data
    assert (detail.data["api_key"], detail.data["assistant_id"]) == (
        "synt...ey-B",
        "assistant-B",
    )
    version_detail = client.get(
        f"/simulate/agent-definitions/{agent.id}/versions/{selected.id}/"
    )
    assert version_detail.status_code == 200, version_detail.data
    assert version_detail.data["api_key"] == "synt...ey-B"
    assert version_detail.data["configuration_snapshot"]["provider"] == "vapi"
    assert (
        version_detail.data["configuration_snapshot"]["assistant_id"] == "assistant-B"
    )
    for representation in (saved.data, detail.data, version_detail.data):
        assert "synthetic-key-B" not in str(representation)
        assert "synthetic-key-A" not in str(representation)
    clear_workspace_context()
    configuration = VapiRecordingService.configuration_from_agent_definition(agent)
    assert (configuration.api_key, configuration.assistant_id) == (
        "synthetic-key-B",
        "assistant-B",
    )

    # Real prepare_call, not a mocked resolver: only the Temporal context is
    # replaced. It must route to Vapi with the same pair after either writer.
    scenario = Scenarios.objects.create(
        name="Synthetic provider switch",
        source="Synthetic scenario",
        scenario_type="script",
        organization=provider.organization,
        workspace=provider.workspace,
        agent_definition=agent,
    )
    run = RunTest.objects.create(
        name="Synthetic switched run",
        agent_definition=agent,
        agent_version=selected,
        organization=provider.organization,
        workspace=provider.workspace,
    )
    execution = TestExecution.objects.create(
        run_test=run, agent_definition=agent, agent_version=selected
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        agent_version=selected,
        call_metadata={"call_direction": "outbound"},
    )
    with patch("temporalio.activity.info"):
        prepared = async_to_sync(prepare_call)(
            PrepareCallInput(
                call_id=str(call.id), workspace_id=str(provider.workspace_id)
            )
        )
    assert prepared.error is None
    assert prepared.client_provider == "vapi"
    assert (prepared.client_api_key, prepared.client_assistant_id) == (
        "synthetic-key-B",
        "assistant-B",
    )
    assert prepared.client_uses_own_provider is True

    # Activating an unrelated historical version changes selection, not its
    # authored provider. A Bland row's VAPI encoding must still not enable Vapi.
    untouched.activate()
    agent.refresh_from_db()
    historical = resolve_provider_configuration_for_version(untouched)
    assert (historical.api_key, historical.assistant_id) == (
        "synthetic-key-A",
        untouched_snapshot["assistant_id"],
    )
    vapi_configuration = VapiRecordingService.configuration_from_agent_definition(agent)
    assert (vapi_configuration.api_key, vapi_configuration.assistant_id) == (None, None)
    omitted = update_agent(
        client, agent, {"description": "Synthetic historical edit"}, update_route
    )
    assert omitted.status_code == 200, omitted.data
    clear_workspace_context()
    selected.refresh_from_db()
    untouched.refresh_from_db()
    untouched_credentials.refresh_from_db()
    assert selected.configuration_snapshot == {
        **before,
        "provider": "vapi",
        "assistant_id": "assistant-B",
    }
    assert untouched.configuration_snapshot == untouched_snapshot
    assert untouched_credentials.api_key == untouched_key


@pytest.mark.parametrize("snapshot_provider", [True, False], ids=["authored", "legacy"])
@pytest.mark.parametrize("key", [None, "********"], ids=["omitted-key", "masked-key"])
def test_explicit_bland_to_vapi_change_does_not_reuse_encoded_bland_secret(
    vapi_setup, update_route, snapshot_provider, key
):
    provider, client = vapi_setup
    agent, version, credentials = seed_version(provider, "assistant-A")
    agent.provider = "bland"
    agent.contact_number = "+16505550100"
    agent.save(update_fields=["provider", "contact_number"])
    if snapshot_provider:
        version.configuration_snapshot["provider"] = "bland"
        version.save(update_fields=["configuration_snapshot"])
    # Disabling observability must not let an encoded Bland secret authorize
    # a provider change without a usable replacement key.
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    payload = {"provider": "vapi", "assistant_id": "assistant-B"}
    if key is not None:
        payload["api_key"] = key
    before = _configuration_state(agent)
    with _assert_no_configuration_writes():
        response = update_agent(client, agent, payload, update_route)
    assert response.status_code == 400, response.data
    assert "new API key is required" in str(response.data)
    clear_workspace_context()
    assert _configuration_state(agent) == before
    agent.refresh_from_db()
    configuration = VapiRecordingService.configuration_from_agent_definition(agent)
    assert (configuration.api_key, configuration.assistant_id) == (None, None)
    assert "synthetic-key-A" not in str(response.data)


def test_empty_vapi_row_form_roundtrip_preserves_effective_pair(
    vapi_setup, monkeypatch
):
    provider, client = vapi_setup
    agent, original, original_credentials = seed_version(provider, "assistant-S")
    # A complete hosted-voice snapshot, as consumed by VersionDetails/common.js.
    original.configuration_snapshot = original.create_snapshot(
        commit_message=original.commit_message
    )
    original.save(update_fields=["configuration_snapshot"])
    original_snapshot = original.configuration_snapshot.copy()
    agent.api_key = "synthetic-key-B"
    agent.assistant_id = "assistant-B"
    agent.save(update_fields=["api_key", "assistant_id"])
    original_credentials.api_key = ""
    original_credentials.save()
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    legacy_ciphertext = legacy.api_key
    now = timezone.now()
    calls_url = ObservabilityRoutes.VAPI_CALL_URL.value
    assistant_url = ObservabilityRoutes.VAPI_ASSISTANT_URL.value
    artifact_url = VapiRecordingService.build_artifact_url(
        "call-for-assistant-B", "call-logs"
    )
    requests_seen, imported = [], []

    def upstream(url, *, headers, params=None, **kwargs):
        requests_seen.append((url, headers["Authorization"], params))
        if url == calls_url:
            assistant = params["assistantId"]
            return Mock(
                status_code=200,
                json=lambda: [
                    {
                        "id": f"call-for-{assistant}",
                        "assistantId": assistant,
                        "status": "ended",
                        "createdAt": (now - timedelta(minutes=5)).isoformat(),
                        "endedAt": (now - timedelta(minutes=4)).isoformat(),
                        "updatedAt": (now - timedelta(minutes=3)).isoformat(),
                        "messages": [{"role": "user", "message": "Synthetic call"}],
                        "artifact": {},
                    }
                ],
            )
        if url == artifact_url:
            return Mock(status_code=200, content=gzip.compress(b""))
        assert url in (f"{calls_url}?limit=0", f"{assistant_url}/assistant-B")
        return Mock(status_code=200)

    def export(span, provider_type, call_id):
        imported.append((call_id, span.span_attributes["raw_log"]["assistantId"]))
        return 1

    # Only external HTTP and collector delivery are replaced: real auth,
    # preflight services, version views, scheduled poll and artifact parser run.
    monkeypatch.setattr(
        "tracer.services.observability_providers.requests.get", upstream
    )
    monkeypatch.setattr(op, "_export_provider_call_to_collector", export)
    selected = original
    for save_number in (1, 2):
        stored_snapshot = selected.configuration_snapshot.copy()
        detail = client.get(
            f"/simulate/agent-definitions/{agent.id}/versions/{selected.id}/"
        )
        assert detail.status_code == 200, detail.data
        snapshot = detail.data["configuration_snapshot"]
        assert snapshot["agent_type"] == "voice"
        assert snapshot["provider"] == "vapi"
        assert snapshot["observability_enabled"] is True
        assert detail.data["api_key"] == "synt...ey-B"
        assert snapshot["assistant_id"] == "assistant-B"
        assert "synthetic-key-B" not in str(detail.data)
        selected.refresh_from_db()
        assert selected.configuration_snapshot == stored_snapshot
        assert selected.credentials.api_key == ""

        # helper.jsx preflights the values common.js actually initializes, not
        # hand-entered secrets. A stale snapshot assistant would fail this check.
        preflight = {
            "provider": snapshot["provider"].lower(),
            "api_key": detail.data["api_key"] or "",
            "agent_id": str(agent.id),
        }
        key_check = client.post(
            "/tracer/observability-provider/verify_api_key/", preflight, format="json"
        )
        assert key_check.status_code == 200, key_check.data
        assistant_check = client.post(
            "/tracer/observability-provider/verify_assistant_id/",
            {**preflight, "assistant_id": snapshot["assistant_id"]},
            format="json",
        )
        assert assistant_check.status_code == 200, assistant_check.data
        assert requests_seen[-2:] == [
            (f"{calls_url}?limit=0", "Bearer synthetic-key-B", None),
            (f"{assistant_url}/assistant-B", "Bearer synthetic-key-B", None),
        ]
        request_count = len(requests_seen)
        wrong_mask = client.post(
            "/tracer/observability-provider/verify_api_key/",
            {**preflight, "api_key": "synt...ey-A"},
            format="json",
        )
        assert wrong_mask.status_code == 400
        assert len(requests_seen) == request_count

        # useAgentConfigForm.js sends the whole voice form, even for a
        # description-only edit. Keep its concurrency and empty WebRTC number.
        payload = {
            "agent_type": snapshot["agent_type"],
            "agent_name": snapshot["agent_name"],
            "languages": snapshot.get("languages", ["en"]),
            "provider": preflight["provider"],
            "api_key": preflight["api_key"],
            "assistant_id": snapshot["assistant_id"],
            "description": f"Synthetic description-only edit {save_number}",
            "knowledge_base": snapshot.get("knowledge_base") or None,
            "contact_number": "",
            "inbound": snapshot.get("inbound", True),
            "target_speaks_first": snapshot.get(
                "target_speaks_first", not snapshot.get("inbound", True)
            ),
            "commit_message": f"Synthetic form save {save_number}",
            "observability_enabled": snapshot["observability_enabled"],
            "authentication_method": "api_key",
            "livekit_max_concurrency": snapshot.get("livekit_max_concurrency", 5),
        }
        saved = client.post(
            f"/simulate/agent-definitions/{agent.id}/versions/create/",
            payload,
            format="json",
        )
        assert saved.status_code == 201, saved.data
        assert saved.data["version"]["api_key"] == "synt...ey-B"
        assert (
            saved.data["version"]["configuration_snapshot"]["assistant_id"]
            == "assistant-B"
        )
        clear_workspace_context()
        agent.refresh_from_db()
        selected = agent.active_version
        assert str(selected.id) == saved.data["version"]["id"]
        assert selected.credentials.api_key == ""
        assert (agent.api_key, agent.assistant_id) == (
            "synthetic-key-B",
            "assistant-B",
        )
        assert agent.description == payload["description"]

    original.refresh_from_db()
    original_credentials.refresh_from_db()
    legacy.refresh_from_db()
    assert original.configuration_snapshot == original_snapshot
    assert original_credentials.api_key == ""
    assert original_credentials.assistant_id == "assistant-S"
    assert legacy.agent_version_id is None
    assert legacy.api_key == legacy_ciphertext
    assert legacy.assistant_id == "assistant-A"
    watermark = provider.last_fetched_at
    assert poll(provider) == op.StoreOutcome(1, 0, 0)
    assert [(url, auth) for url, auth, _ in requests_seen[-2:]] == [
        (calls_url, "Bearer synthetic-key-B"),
        (artifact_url, "Bearer synthetic-key-B"),
    ]
    assert requests_seen[-2][2]["assistantId"] == "assistant-B"
    assert imported == [("call-for-assistant-B", "assistant-B")]
    provider.refresh_from_db()
    assert provider.last_fetched_at > watermark
    assert "configuration_health" not in provider.poll_state
    assert provider.poll_state["other"] == {"sentinel": "preserved"}


@pytest.mark.parametrize("route", ["edit", "operations", "save-version"])
@pytest.mark.parametrize("key", [None, "********"], ids=["omitted", "masked"])
def test_save_missing_active_row_still_adopts_legacy_pair(
    vapi_setup, monkeypatch, route, key
):
    provider, client = vapi_setup
    agent, _, _ = seed_version(provider, "assistant-B", with_credentials=False)
    agent.api_key = "synthetic-key-B"
    agent.save(update_fields=["api_key"])
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    payload = {"description": "Synthetic edit"}
    if key is not None:
        payload["api_key"] = key
    response = update_agent(client, agent, payload, route)
    expected_status = 201 if route == "save-version" else 200
    assert response.status_code == expected_status, response.data
    clear_workspace_context()
    agent.refresh_from_db()
    credentials = agent.active_version.credentials
    assert credentials.pk == legacy.pk
    assert credentials.get_api_key() == "synthetic-key-A"
    assert credentials.assistant_id == "assistant-A"
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    assert poll(provider) == op.StoreOutcome(1, 0, 0)
    assert requests_seen == [("Bearer synthetic-key-A", "assistant-A")]
    assert imported == [("call-for-assistant-A", "assistant-A")]


@pytest.mark.parametrize(
    "route,key,expected_status",
    [("edit", None, 200), ("edit", "********", 500), ("save-version", "********", 500)],
    ids=["omitted-edit", "masked-edit", "masked-version"],
)
def test_save_unreadable_key_does_not_use_legacy(
    vapi_setup, monkeypatch, route, key, expected_status
):
    provider, client = vapi_setup
    agent, version, credentials = seed_version(provider, "assistant-B")
    ProviderCredentials.objects.filter(pk=credentials.pk).update(api_key="enc::invalid")
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    payload = {"assistant_id": "assistant-B"}
    if key is not None:
        payload["api_key"] = key
    response = update_agent(client, agent, payload, route)
    # A masked save must not turn unreadable ciphertext into a fallback key.
    assert response.status_code == expected_status, response.data
    clear_workspace_context()
    credentials.refresh_from_db()
    legacy.refresh_from_db()
    assert credentials.agent_version_id == version.pk
    assert credentials.api_key == "enc::invalid"
    assert legacy.agent_version_id is None
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    watermark = provider.last_fetched_at
    assert poll(provider) is None
    assert requests_seen == imported == []
    provider.refresh_from_db()
    assert provider.last_fetched_at == watermark
    assert (
        provider.poll_state["configuration_health"]["reason_code"]
        == "credential_unreadable"
    )


@pytest.mark.parametrize("key", ["", "synthetic-retell-key"])
def test_noncredential_save_keeps_wrong_provider_row_authoritative(
    vapi_setup, monkeypatch, update_route, key
):
    provider, client = vapi_setup
    agent, _, credentials = seed_version(provider, "assistant-B")
    credentials.provider_type = "retell"
    credentials.api_key = key
    credentials.save()
    original_key = credentials.api_key
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-A",
        assistant_id="assistant-A",
    )
    response = update_agent(
        client, agent, {"description": "Synthetic edit"}, update_route
    )
    assert response.status_code == 200, response.data
    clear_workspace_context()
    credentials.refresh_from_db()
    legacy.refresh_from_db()
    assert credentials.provider_type == "retell"
    assert credentials.api_key == original_key
    assert legacy.agent_version_id is None
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    watermark = provider.last_fetched_at
    assert poll(provider) is None
    assert requests_seen == imported == []
    provider.refresh_from_db()
    assert provider.last_fetched_at == watermark
    assert provider.poll_state["configuration_health"]["reason_code"] == "missing_key"


def test_edit_before_snapshots_tracked_assistants_uses_the_rows_pair(
    monkeypatch, vapi_setup
):
    """An in-place edit made before snapshots tracked the assistant updated
    the version's row (key and assistant) but left the snapshot behind."""
    provider, _ = vapi_setup
    agent, version, credentials = seed_version(provider, "assistant-A")
    credentials.assistant_id = "assistant-B"
    credentials.api_key = "synthetic-key-B"
    credentials.save()
    version.refresh_from_db()
    assert version.configuration_snapshot["assistant_id"] == "assistant-A"
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    poll(provider)
    assert requests_seen == [("Bearer synthetic-key-B", "assistant-B")]
    assert imported == [("call-for-assistant-B", "assistant-B")]


def test_row_without_an_assistant_falls_back_to_its_versions_snapshot(
    monkeypatch, vapi_setup
):
    provider, _ = vapi_setup
    agent, version, credentials = seed_version(provider, "assistant-A")
    ProviderCredentials.objects.filter(pk=credentials.pk).update(assistant_id="")
    requests_seen, imported = fake_upstream(monkeypatch, timezone.now())
    poll(provider)
    assert requests_seen == [("Bearer synthetic-key-A", "assistant-A")]
    assert imported == [("call-for-assistant-A", "assistant-A")]


def _prepare_saved_version(provider, agent, version):
    from ee.voice.temporal.activities.voice_small import prepare_call
    from simulate.models import RunTest, Scenarios
    from simulate.models.test_execution import CallExecution, TestExecution
    from simulate.temporal.types.activities import PrepareCallInput

    scenario = Scenarios.objects.create(
        name="Synthetic credential provenance",
        source="Synthetic scenario",
        scenario_type="script",
        organization=provider.organization,
        workspace=provider.workspace,
        agent_definition=agent,
    )
    run = RunTest.objects.create(
        name="Synthetic credential run",
        agent_definition=agent,
        agent_version=version,
        organization=provider.organization,
        workspace=provider.workspace,
    )
    execution = TestExecution.objects.create(
        run_test=run, agent_definition=agent, agent_version=version
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        agent_version=version,
        call_metadata={"call_direction": "outbound"},
    )
    with patch("temporalio.activity.info"):
        return async_to_sync(prepare_call)(
            PrepareCallInput(
                call_id=str(call.id), workspace_id=str(provider.workspace_id)
            )
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "old_provider,new_provider", [("bland", "vapi"), ("vapi", "bland")]
)
@pytest.mark.parametrize("key_mode", ["omitted", "masked", "replacement"])
@pytest.mark.parametrize("explicit_source", [False, True])
@pytest.mark.parametrize(
    "snapshot_provider", [True, False], ids=["authored", "legacy-snapshot"]
)
def test_create_version_provider_switch_never_carries_incompatible_key(
    vapi_setup,
    monkeypatch,
    old_provider,
    new_provider,
    key_mode,
    explicit_source,
    snapshot_provider,
):
    from simulate.services.agent_definition import (
        resolve_provider_configuration_for_version,
    )

    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, original, credentials = seed_version(provider, "assistant-A")
    agent.provider = old_provider
    agent.contact_number = "+16505550100"
    agent.save(update_fields=["provider", "contact_number"])
    original.configuration_snapshot = original.create_snapshot(
        commit_message=original.commit_message
    )
    if not snapshot_provider:
        original.configuration_snapshot.pop("provider")
    original.save(update_fields=["configuration_snapshot"])
    # The same storage encoding in both directions is intentional (0071).
    assert credentials.provider_type == "vapi"
    # Separate 0076 legacy row, owned by a historical version of the old provider.
    older = agent.create_version(
        description="Synthetic legacy", commit_message="legacy"
    )
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        agent_version=older,
        provider_type="vapi",
        api_key="synthetic-key-L",
        assistant_id="assistant-L",
    )
    before = (
        original.configuration_snapshot.copy(),
        credentials.api_key,
        older.configuration_snapshot.copy(),
        legacy.api_key,
    )
    payload = {
        "agent_type": "voice",
        "provider": new_provider,
        "assistant_id": "assistant-C",
        "commit_message": "Synthetic provider change",
        "observability_enabled": False,
        "inbound": True,
        "authentication_method": "api_key",
        "contact_number": "+16505550100",
    }
    if explicit_source:
        payload["source_version_id"] = str(original.id)
    if key_mode != "omitted":
        payload["api_key"] = (
            "synt...ey-A" if key_mode == "masked" else "synthetic-key-C"
        )
        upstream = Mock(return_value=Mock(status_code=200))
        monkeypatch.setattr(
            "tracer.services.observability_providers.requests.get", upstream
        )
        preflight = {
            "provider": new_provider,
            "agent_id": str(agent.id),
            "api_key": payload["api_key"],
            **({"source_version_id": str(original.id)} if explicit_source else {}),
        }
        for action in ("verify_api_key", "verify_assistant_id"):
            check = client.post(
                f"/tracer/observability-provider/{action}/",
                {
                    **preflight,
                    **(
                        {"assistant_id": "assistant-C"}
                        if action == "verify_assistant_id"
                        else {}
                    ),
                },
                format="json",
            )
            assert check.status_code == (400 if key_mode == "masked" else 200), (
                check.data
            )
        if key_mode == "masked":
            upstream.assert_not_called()
    # The create endpoint must be safe even without a successful preflight.
    state = _configuration_state(agent)
    replacing = key_mode == "replacement"
    with nullcontext() if replacing else _assert_no_configuration_writes():
        saved = client.post(
            f"/simulate/agent-definitions/{agent.id}/versions/create/",
            payload,
            format="json",
        )
    assert saved.status_code == (201 if replacing else 400), saved.data
    clear_workspace_context()
    if not replacing:
        assert "new API key is required" in str(saved.data)
        assert _configuration_state(agent) == state
    agent.refresh_from_db()
    selected = agent.active_version
    configuration = resolve_provider_configuration_for_version(selected)
    expected_key = "synthetic-key-C" if replacing else "synthetic-key-A"
    expected_assistant = "assistant-C" if replacing else "assistant-A"
    expected_provider = new_provider if replacing else old_provider
    assert (configuration.api_key, configuration.assistant_id) == (
        expected_key,
        expected_assistant,
    )
    assert (
        selected.configuration_snapshot.get("provider", agent.provider)
        == expected_provider
    )
    assert selected.credentials.provider_type == "vapi"
    assert selected.credentials.get_api_key() == expected_key
    assert agent.api_key == expected_key
    prepared = _prepare_saved_version(provider, agent, selected)
    assert prepared.error is None
    assert prepared.client_provider == expected_provider
    assert prepared.client_api_key == expected_key
    assert prepared.client_assistant_id == expected_assistant
    original.refresh_from_db()
    credentials.refresh_from_db()
    older.refresh_from_db()
    legacy.refresh_from_db()
    assert (
        original.configuration_snapshot,
        credentials.api_key,
        older.configuration_snapshot,
        legacy.api_key,
    ) == before
    assert legacy.agent_version_id == older.pk
    assert legacy.agent_definition_id == agent.pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("key", [None, "synt...ey-A"], ids=["omitted", "masked"])
def test_inplace_vapi_to_bland_does_not_resurrect_separate_legacy(
    vapi_setup, update_route, key
):
    from simulate.services.agent_definition import (
        resolve_provider_configuration_for_version,
    )

    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, original, credentials = seed_version(provider, "assistant-A")
    agent.contact_number = "+16505550100"
    agent.save(update_fields=["contact_number"])
    original.configuration_snapshot = original.create_snapshot(
        commit_message=original.commit_message
    )
    original.save(update_fields=["configuration_snapshot"])
    legacy_owner = agent.create_version(
        description="Synthetic legacy", commit_message="legacy"
    )
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        agent_version=legacy_owner,
        provider_type="vapi",
        api_key="synthetic-key-L",
        assistant_id="assistant-L",
    )
    legacy_snapshot = legacy_owner.configuration_snapshot.copy()
    legacy_ciphertext = legacy.api_key
    payload = {"provider": "bland", "assistant_id": "pathway-B"}
    if key is not None:
        payload["api_key"] = key
    before = _configuration_state(agent)
    with _assert_no_configuration_writes():
        saved = update_agent(client, agent, payload, update_route)
    assert saved.status_code == 400, saved.data
    assert "new API key is required" in str(saved.data)
    clear_workspace_context()
    assert _configuration_state(agent) == before
    agent.refresh_from_db()
    original.refresh_from_db()
    configuration = resolve_provider_configuration_for_version(original)
    assert (configuration.api_key, configuration.assistant_id) == (
        "synthetic-key-A",
        "assistant-A",
    )
    prepared = _prepare_saved_version(provider, agent, original)
    assert prepared.error is None
    assert prepared.client_provider == "vapi"
    assert prepared.client_api_key == "synthetic-key-A"
    assert prepared.client_assistant_id == "assistant-A"
    legacy_owner.refresh_from_db()
    legacy.refresh_from_db()
    assert legacy_owner.configuration_snapshot == legacy_snapshot
    assert legacy.api_key == legacy_ciphertext
    assert legacy.agent_version_id == legacy_owner.pk
    assert legacy.agent_definition_id == agent.pk


@pytest.mark.parametrize("preflight_enabled", [True, False])
@pytest.mark.parametrize("key_mode", ["masked", "omitted"])
def test_historical_form_preflight_and_create_share_authorized_source(
    vapi_setup, monkeypatch, preflight_enabled, key_mode
):
    from simulate.services.agent_definition import (
        resolve_provider_configuration_for_version,
    )

    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, historical, credentials_a = seed_version(
        provider, "stale-snapshot-A", status="archived"
    )
    historical.configuration_snapshot = historical.create_snapshot(
        commit_message=historical.commit_message
    )
    historical.save(update_fields=["configuration_snapshot"])
    credentials_a.assistant_id = "assistant-A"
    credentials_a.save()
    _, active, credentials_b = seed_version(
        provider, "assistant-B", number=2, agent=agent
    )
    credentials_b.api_key = "synthetic-key-B"
    credentials_b.save()
    agent.api_key = "synthetic-key-B"
    agent.assistant_id = "assistant-B"
    agent.save(update_fields=["api_key", "assistant_id"])
    before = (
        historical.configuration_snapshot.copy(),
        credentials_a.api_key,
        active.configuration_snapshot.copy(),
        credentials_b.api_key,
    )
    detail = client.get(
        f"/simulate/agent-definitions/{agent.id}/versions/{historical.id}/"
    )
    assert detail.status_code == 200, detail.data
    snapshot = detail.data["configuration_snapshot"]
    assert detail.data["api_key"] == "synt...ey-A"
    assert snapshot["assistant_id"] == "assistant-A"
    upstream = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr(
        "tracer.services.observability_providers.requests.get", upstream
    )
    preflight = {
        "provider": snapshot["provider"],
        "api_key": detail.data["api_key"],
        "agent_id": str(agent.id),
        "source_version_id": detail.data["id"],
    }
    if preflight_enabled:
        for action in ("verify_api_key", "verify_assistant_id"):
            check = client.post(
                f"/tracer/observability-provider/{action}/",
                {
                    **preflight,
                    **(
                        {"assistant_id": snapshot["assistant_id"]}
                        if action == "verify_assistant_id"
                        else {}
                    ),
                },
                format="json",
            )
            assert check.status_code == 200, check.data
        assert upstream.call_count == 2
        assert all(
            call.kwargs["headers"] == {"Authorization": "Bearer synthetic-key-A"}
            for call in upstream.call_args_list
        )
        assert upstream.call_args.args[0].endswith("/assistant-A")
    else:
        upstream.assert_not_called()
    # UI-shaped version create with A selected while B is still active. A
    # missing key with an explicit source also means keep A, never copy B.
    payload = {
        "agent_type": "voice",
        "agent_name": snapshot["agent_name"],
        "languages": ["en"],
        "provider": snapshot["provider"],
        "assistant_id": snapshot["assistant_id"],
        "source_version_id": detail.data["id"],
        "description": "Synthetic historical edit",
        "knowledge_base": None,
        "contact_number": "",
        "inbound": True,
        "authentication_method": "api_key",
        "observability_enabled": False,
        "livekit_max_concurrency": 5,
        "commit_message": "Historical form save",
    }
    if key_mode == "masked":
        payload["api_key"] = detail.data["api_key"]
    saved = client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        payload,
        format="json",
    )
    assert saved.status_code == 201, saved.data
    assert saved.data["version"]["api_key"] == "synt...ey-A"
    assert (
        saved.data["version"]["configuration_snapshot"]["assistant_id"] == "assistant-A"
    )
    clear_workspace_context()
    agent.refresh_from_db()
    selected = agent.active_version
    configuration = resolve_provider_configuration_for_version(selected)
    assert (configuration.api_key, configuration.assistant_id) == (
        "synthetic-key-A",
        "assistant-A",
    )
    historical.refresh_from_db()
    active.refresh_from_db()
    credentials_a.refresh_from_db()
    credentials_b.refresh_from_db()
    assert (
        historical.configuration_snapshot,
        credentials_a.api_key,
        active.configuration_snapshot,
        credentials_b.api_key,
    ) == before
    assert historical.status == "archived"


@pytest.mark.parametrize(
    "mask,explicit_source",
    [
        ("synt...ey-B", True),
        ("synt...ey-B", False),
        ("********", True),
    ],
)
def test_create_rejects_a_mask_from_another_source_before_writing(
    vapi_setup, monkeypatch, mask, explicit_source
):
    # Legacy unversioned callers retain the generic keep-marker contract. A
    # selected-version form must use the actual displayed mask, never a sentinel.
    provider, client = vapi_setup
    agent, version, credentials = seed_version(provider, "assistant-A")
    before = (
        agent.description,
        agent.provider,
        credentials.api_key,
        version.configuration_snapshot.copy(),
    )
    payload = {
        "provider": "vapi",
        "api_key": mask,
        "assistant_id": "assistant-A",
        "description": "Must not be written",
        "commit_message": "Invalid source mask",
        "observability_enabled": False,
    }
    if explicit_source:
        payload["source_version_id"] = str(version.id)
    upstream = Mock()
    monkeypatch.setattr(
        "tracer.services.observability_providers.requests.get", upstream
    )
    for action in ("verify_api_key", "verify_assistant_id"):
        preflight = {
            "provider": "vapi",
            "api_key": mask,
            "agent_id": str(agent.id),
            **({"source_version_id": str(version.id)} if explicit_source else {}),
            **(
                {"assistant_id": "assistant-A"}
                if action == "verify_assistant_id"
                else {}
            ),
        }
        check = client.post(
            f"/tracer/observability-provider/{action}/", preflight, format="json"
        )
        assert check.status_code == 400, check.data
    saved = client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        payload,
        format="json",
    )
    assert saved.status_code == 400, saved.data
    upstream.assert_not_called()
    clear_workspace_context()
    agent.refresh_from_db()
    credentials.refresh_from_db()
    version.refresh_from_db()
    assert (
        agent.description,
        agent.provider,
        credentials.api_key,
        version.configuration_snapshot,
    ) == before
    assert agent.versions.count() == 1


@pytest.mark.parametrize(
    "scope",
    ["other-agent", "other-org", "other-workspace", "mismatched-version", "missing"],
)
@pytest.mark.parametrize("key", ["synt...ey-A", "synthetic-replacement-key"])
def test_source_version_ids_are_scoped_even_with_raw_replacement_keys(
    vapi_setup, monkeypatch, user, scope, key
):
    from accounts.models import Organization
    from accounts.models.workspace import Workspace

    provider, client = vapi_setup
    agent, version, credentials = seed_version(provider, "assistant-A")
    # Each agent needs its own OneToOne observability provider.
    foreign_provider = ObservabilityProvider.objects.create(
        project=provider.project,
        organization=provider.organization,
        workspace=provider.workspace,
        provider="vapi",
    )
    foreign_agent, foreign_version, foreign_credentials = seed_version(
        foreign_provider, "assistant-foreign"
    )
    if scope == "other-org":
        foreign_org = Organization.objects.create(name="Synthetic other organization")
        AgentDefinition.no_workspace_objects.filter(pk=foreign_agent.pk).update(
            organization=foreign_org
        )
        AgentVersion.no_workspace_objects.filter(pk=foreign_version.pk).update(
            organization=foreign_org
        )
    elif scope in ("other-workspace", "mismatched-version"):
        foreign_workspace = Workspace.objects.create(
            name="Synthetic other workspace",
            organization=provider.organization,
            created_by=user,
        )
        if scope == "other-workspace":
            AgentDefinition.no_workspace_objects.filter(pk=foreign_agent.pk).update(
                workspace=foreign_workspace
            )
        else:
            # Corrupt denormalized scope must not be sufficient just because
            # this row's parent ID happens to match the authorized agent.
            AgentVersion.no_workspace_objects.filter(pk=foreign_version.pk).update(
                agent_definition=agent, version_number=2
            )
        AgentVersion.no_workspace_objects.filter(pk=foreign_version.pk).update(
            workspace=foreign_workspace
        )
    source_id = (
        "00000000-0000-0000-0000-000000000000"
        if scope == "missing"
        else str(foreign_version.id)
    )
    before = (
        agent.description,
        credentials.api_key,
        version.configuration_snapshot.copy(),
    )
    version_count = AgentVersion.no_workspace_objects.filter(
        agent_definition=agent
    ).count()
    upstream = Mock()
    monkeypatch.setattr(
        "tracer.services.observability_providers.requests.get", upstream
    )
    for action in ("verify_api_key", "verify_assistant_id"):
        check = client.post(
            f"/tracer/observability-provider/{action}/",
            {
                "provider": "vapi",
                "api_key": key,
                "agent_id": str(agent.id),
                "source_version_id": source_id,
                **(
                    {"assistant_id": "assistant-A"}
                    if action == "verify_assistant_id"
                    else {}
                ),
            },
            format="json",
        )
        assert check.status_code == 400, check.data
    saved = client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        {
            "provider": "vapi",
            "api_key": key,
            "assistant_id": "assistant-A",
            "source_version_id": source_id,
            "description": "Must not be written",
            "commit_message": "Invalid source",
            "observability_enabled": False,
        },
        format="json",
    )
    assert saved.status_code == 400, saved.data
    upstream.assert_not_called()
    clear_workspace_context()
    agent.refresh_from_db()
    version.refresh_from_db()
    credentials.refresh_from_db()
    assert (
        agent.description,
        credentials.api_key,
        version.configuration_snapshot,
    ) == before
    assert (
        AgentVersion.no_workspace_objects.filter(agent_definition=agent).count()
        == version_count
    )


@pytest.mark.parametrize("source_key", ["", "synthetic-key-A"])
def test_historical_source_does_not_take_new_definitions_other_provider_fallback(
    vapi_setup, source_key
):
    from simulate.services.agent_definition import (
        resolve_provider_configuration_for_version,
    )

    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, historical, credentials = seed_version(
        provider, "assistant-A", status="archived"
    )
    historical.configuration_snapshot["provider"] = "vapi"
    historical.save(update_fields=["configuration_snapshot"])
    credentials.api_key = source_key
    credentials.save()
    agent.provider = "bland"
    agent.api_key = "synthetic-bland-key-B"
    agent.assistant_id = "pathway-B"
    agent.save(update_fields=["provider", "api_key", "assistant_id"])
    active = agent.create_version(
        description="Synthetic Bland", commit_message="B", status="active"
    )
    ProviderCredentials.objects.create(
        agent_definition=agent,
        agent_version=active,
        provider_type="vapi",
        api_key="synthetic-bland-key-B",
        assistant_id="pathway-B",
    )
    before = _configuration_state(agent)
    with nullcontext() if source_key else _assert_no_configuration_writes():
        saved = client.post(
            f"/simulate/agent-definitions/{agent.id}/versions/create/",
            {
                "provider": "vapi",
                "source_version_id": str(historical.id),
                "assistant_id": "assistant-A",
                "commit_message": "Historical source",
                "observability_enabled": False,
            },
            format="json",
        )
    assert saved.status_code == (201 if source_key else 400), saved.data
    clear_workspace_context()
    if not source_key:
        # A raw-empty source cannot use a definition of another provider.
        assert "selected source version has no usable API key" in str(saved.data)
        assert _configuration_state(agent) == before
    agent.refresh_from_db()
    configuration = resolve_provider_configuration_for_version(agent.active_version)
    assert configuration.api_key == (source_key or "synthetic-bland-key-B")
    assert configuration.assistant_id == ("assistant-A" if source_key else "pathway-B")
    assert agent.api_key == ("" if source_key else "synthetic-bland-key-B")


@pytest.mark.parametrize("source_id", ["not-a-uuid", None])
def test_invalid_source_id_is_not_silently_treated_as_omitted(
    vapi_setup, monkeypatch, source_id
):
    provider, client = vapi_setup
    agent, version, _ = seed_version(provider, "assistant-A")
    upstream = Mock()
    monkeypatch.setattr(
        "tracer.services.observability_providers.requests.get", upstream
    )
    for action in ("verify_api_key", "verify_assistant_id"):
        response = client.post(
            f"/tracer/observability-provider/{action}/",
            {
                "provider": "vapi",
                "api_key": "synthetic-replacement-key",
                "agent_id": str(agent.id),
                "source_version_id": source_id,
                **(
                    {"assistant_id": "assistant-A"}
                    if action == "verify_assistant_id"
                    else {}
                ),
            },
            format="json",
        )
        assert response.status_code == 400, response.data
    response = client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        {"source_version_id": source_id, "commit_message": "Invalid source"},
        format="json",
    )
    assert response.status_code == 400, response.data
    upstream.assert_not_called()
    clear_workspace_context()
    assert agent.versions.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["edit", "operations", "save-version"])
@pytest.mark.parametrize("row", ["keyed", "raw-empty", "missing"])
@pytest.mark.parametrize(
    "key",
    [None, "", "********", "synt...ey-L", "synthetic-key-C"],
    ids=["omitted", "blank", "keep-mask", "display-mask", "replacement"],
)
def test_provider_change_with_unversioned_legacy_requires_replacement(
    vapi_setup, monkeypatch, route, row, key
):
    from simulate.services.agent_definition import (
        resolve_provider_configuration_for_version,
    )

    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, original, credentials = seed_version(
        provider, "assistant-A", with_credentials=row != "missing"
    )
    agent.contact_number = "+16505550100"
    agent.save(update_fields=["contact_number"])
    original.configuration_snapshot = original.create_snapshot(
        commit_message=original.commit_message
    )
    original.save(update_fields=["configuration_snapshot"])
    if row == "raw-empty":
        credentials.api_key = ""
        credentials.save()
    unrelated = agent.create_version(
        description="Synthetic history", commit_message="History"
    )
    unrelated_snapshot = unrelated.configuration_snapshot.copy()
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-L",
        assistant_id="assistant-L",
    )
    legacy_state = (
        legacy.api_key,
        legacy.assistant_id,
        legacy.agent_definition_id,
        legacy.agent_version_id,
    )
    before = _configuration_state(agent)
    upstream = Mock()
    monkeypatch.setattr(
        "tracer.services.observability_providers.requests.get", upstream
    )
    payload = {
        "provider": "bland",
        "assistant_id": "pathway-C",
        "description": "Synthetic change",
    }
    if key is not None:
        payload["api_key"] = key
    replacing = key == "synthetic-key-C"
    with nullcontext() if replacing else _assert_no_configuration_writes():
        if route == "save-version":
            saved = client.post(
                f"/simulate/agent-definitions/{agent.id}/versions/create/",
                {
                    **payload,
                    "commit_message": "Synthetic provider change",
                    "observability_enabled": False,
                },
                format="json",
            )
        else:
            saved = update_agent(client, agent, payload, route)
    assert saved.status_code == (
        (201 if route == "save-version" else 200) if replacing else 400
    ), saved.data
    upstream.assert_not_called()
    clear_workspace_context()
    if not replacing:
        assert "new API key is required" in str(saved.data)
        assert _configuration_state(agent) == before
    agent.refresh_from_db()
    selected = agent.active_version
    expected_key = (
        "synthetic-key-C"
        if replacing
        else ("synthetic-key-L" if row == "missing" else "synthetic-key-A")
    )
    expected_assistant = (
        "pathway-C"
        if replacing
        else ("assistant-L" if row == "missing" else "assistant-A")
    )
    expected_provider = "bland" if replacing else "vapi"
    configuration = resolve_provider_configuration_for_version(selected)
    assert (configuration.api_key, configuration.assistant_id) == (
        expected_key,
        expected_assistant,
    )
    detail = client.get(
        f"/simulate/agent-definitions/{agent.id}/versions/{selected.id}/"
    )
    assert detail.status_code == 200, detail.data
    assert detail.data["configuration_snapshot"]["provider"] == expected_provider
    assert detail.data["configuration_snapshot"]["assistant_id"] == expected_assistant
    from agentcc.services.credential_manager import mask_key

    assert detail.data["api_key"] == mask_key(expected_key)
    if replacing and route == "save-version":
        assert saved.data["version"]["api_key"] == mask_key("synthetic-key-C")
        assert saved.data["version"]["api_key"] != mask_key("synthetic-key-L")
    for secret in ("synthetic-key-A", "synthetic-key-L", "synthetic-key-C"):
        assert secret not in str(saved.data)
        assert secret not in str(detail.data)
    prepared = _prepare_saved_version(provider, agent, selected)
    assert prepared.error is None
    assert prepared.client_provider == expected_provider
    assert (prepared.client_api_key, prepared.client_assistant_id) == (
        expected_key,
        expected_assistant,
    )
    unrelated.refresh_from_db()
    legacy.refresh_from_db()
    assert unrelated.configuration_snapshot == unrelated_snapshot
    assert (
        legacy.api_key,
        legacy.assistant_id,
        legacy.agent_definition_id,
        legacy.agent_version_id,
    ) == legacy_state
    if not replacing or route != "save-version":
        assert selected.pk == original.pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["edit", "operations", "save-version"])
@pytest.mark.parametrize("key", [None, "synt...ey-L"], ids=["omitted", "masked"])
def test_same_provider_bland_save_keeps_unversioned_legacy_fallback(
    vapi_setup, route, key
):
    from simulate.services.agent_definition import (
        resolve_provider_configuration_for_version,
    )

    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, original, credentials = seed_version(provider, "pathway-A")
    agent.provider = "bland"
    agent.contact_number = "+16505550100"
    agent.save(update_fields=["provider", "contact_number"])
    original.configuration_snapshot = original.create_snapshot(
        commit_message=original.commit_message
    )
    original.save(update_fields=["configuration_snapshot"])
    credentials.api_key = ""
    credentials.save()
    ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-L",
        assistant_id="legacy-pathway",
    )
    payload = {
        "provider": "bland",
        "assistant_id": "pathway-A",
        "description": "Same provider",
    }
    if key is not None:
        payload["api_key"] = key
    if route == "save-version":
        saved = client.post(
            f"/simulate/agent-definitions/{agent.id}/versions/create/",
            {
                **payload,
                "commit_message": "Synthetic same-provider save",
                "observability_enabled": False,
            },
            format="json",
        )
    else:
        saved = update_agent(client, agent, payload, route)
    assert saved.status_code == (201 if route == "save-version" else 200), saved.data
    clear_workspace_context()
    agent.refresh_from_db()
    selected = agent.active_version
    configuration = resolve_provider_configuration_for_version(selected)
    assert (configuration.api_key, configuration.assistant_id) == (
        "synthetic-key-L",
        "pathway-A",
    )
    detail = client.get(
        f"/simulate/agent-definitions/{agent.id}/versions/{selected.id}/"
    )
    assert detail.status_code == 200, detail.data
    assert detail.data["api_key"] == "synt...ey-L"
    assert detail.data["configuration_snapshot"]["assistant_id"] == "pathway-A"
    assert "synthetic-key-L" not in str(detail.data)
    prepared = _prepare_saved_version(provider, agent, selected)
    assert prepared.error is None
    assert prepared.client_provider == "bland"
    assert (prepared.client_api_key, prepared.client_assistant_id) == (
        "synthetic-key-L",
        "pathway-A",
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("source", ["empty-ciphertext", "wrong-provider", "raw-empty"])
@pytest.mark.parametrize("key_mode", ["omitted", "blank", "replacement"])
def test_historical_detail_form_preserves_closed_source_authority(
    vapi_setup, monkeypatch, source, key_mode
):
    from integrations.services.credentials import CredentialManager
    from simulate.services.agent_definition import (
        resolve_provider_configuration_for_version,
    )

    provider, client = vapi_setup
    provider.enabled = False
    provider.save(update_fields=["enabled"])
    agent, historical, credentials_a = seed_version(
        provider, "assistant-A", status="archived"
    )
    agent.contact_number = "+16505550100"
    agent.save(update_fields=["contact_number"])
    historical.configuration_snapshot = historical.create_snapshot(
        commit_message=historical.commit_message
    )
    historical.save(update_fields=["configuration_snapshot"])
    credentials_a.api_key = (
        "enc::" + CredentialManager.encrypt({"v": ""}).decode("utf-8")
        if source == "empty-ciphertext"
        else ""
    )
    if source == "wrong-provider":
        credentials_a.provider_type = "retell"
    credentials_a.save()
    agent.api_key = "synthetic-key-B"
    agent.assistant_id = "assistant-B"
    agent.save(update_fields=["api_key", "assistant_id"])
    active = agent.create_version(
        description="Synthetic active B", commit_message="B", status="active"
    )
    ProviderCredentials.objects.create(
        agent_version=active,
        provider_type="vapi",
        api_key="synthetic-key-B",
        assistant_id="assistant-B",
    )
    legacy = ProviderCredentials.objects.create(
        agent_definition=agent,
        provider_type="vapi",
        api_key="synthetic-key-L",
        assistant_id="assistant-L",
    )
    before = _configuration_state(agent)
    historical_state = (
        historical.configuration_snapshot.copy(),
        credentials_a.api_key,
        credentials_a.provider_type,
    )
    detail = client.get(
        f"/simulate/agent-definitions/{agent.id}/versions/{historical.id}/"
    )
    assert detail.status_code == 200, detail.data
    snapshot = detail.data["configuration_snapshot"]
    assert detail.data["api_key"] == ("synt...ey-B" if source == "raw-empty" else None)
    assert (
        snapshot["assistant_id"]
        == {
            "empty-ciphertext": "assistant-A",
            "wrong-provider": "",
            "raw-empty": "assistant-B",
        }[source]
    )
    # Submit the actual detail-derived form with preflight disabled. Omission
    # (also explicit blank) retains this source, not the active definition B.
    upstream = Mock()
    monkeypatch.setattr(
        "tracer.services.observability_providers.requests.get", upstream
    )
    payload = {
        "agent_type": snapshot["agent_type"],
        "agent_name": snapshot["agent_name"],
        "provider": snapshot["provider"],
        "assistant_id": snapshot["assistant_id"],
        "source_version_id": detail.data["id"],
        "languages": snapshot["languages"],
        "contact_number": snapshot["contact_number"],
        "inbound": snapshot["inbound"],
        "authentication_method": "api_key",
        "knowledge_base": None,
        "livekit_max_concurrency": snapshot["livekit_max_concurrency"],
        "description": "Synthetic historical form",
        "commit_message": "Historical form",
        "observability_enabled": False,
    }
    replacing = key_mode == "replacement"
    if key_mode == "blank":
        payload["api_key"] = ""
    elif replacing:
        payload["api_key"] = "synthetic-key-C"
        if source == "wrong-provider":
            payload["assistant_id"] = "assistant-C"
    accepted = replacing or source == "raw-empty"
    with nullcontext() if accepted else _assert_no_configuration_writes():
        saved = client.post(
            f"/simulate/agent-definitions/{agent.id}/versions/create/",
            payload,
            format="json",
        )
    assert saved.status_code == (201 if accepted else 400), saved.data
    upstream.assert_not_called()
    clear_workspace_context()
    agent.refresh_from_db()
    if not accepted:
        assert "selected source version has no usable API key" in str(saved.data)
        assert _configuration_state(agent) == before
        assert agent.active_version.pk == active.pk
        historical.refresh_from_db()
        closed = resolve_provider_configuration_for_version(historical)
        assert closed.api_key is None
        prepared_source = _prepare_saved_version(provider, agent, historical)
        assert prepared_source.error is None
        assert prepared_source.client_api_key is None
        assert prepared_source.client_assistant_id == (
            "assistant-A" if source == "empty-ciphertext" else None
        )
    selected = agent.active_version
    expected_key = "synthetic-key-C" if replacing else "synthetic-key-B"
    expected_assistant = payload["assistant_id"] if replacing else "assistant-B"
    configuration = resolve_provider_configuration_for_version(selected)
    assert (configuration.api_key, configuration.assistant_id) == (
        expected_key,
        expected_assistant,
    )
    prepared = _prepare_saved_version(provider, agent, selected)
    assert prepared.error is None
    assert prepared.client_provider == "vapi"
    assert (prepared.client_api_key, prepared.client_assistant_id) == (
        expected_key,
        expected_assistant,
    )
    if accepted:
        if not replacing:
            assert selected.credentials.api_key == ""
        saved_detail = client.get(
            f"/simulate/agent-definitions/{agent.id}/versions/{selected.id}/"
        )
        assert saved_detail.status_code == 200, saved_detail.data
        assert saved_detail.data["api_key"] == (
            "synt...ey-C" if replacing else "synt...ey-B"
        )
        assert (
            saved_detail.data["configuration_snapshot"]["assistant_id"]
            == expected_assistant
        )
    for representation in (detail.data, saved.data):
        for secret in ("synthetic-key-B", "synthetic-key-C", "synthetic-key-L"):
            assert secret not in str(representation)
    credentials_a.refresh_from_db()
    historical.refresh_from_db()
    assert (
        historical.configuration_snapshot,
        credentials_a.api_key,
        credentials_a.provider_type,
    ) == historical_state
    # A replacement may legitimately adopt the legacy row, but must replace its
    # key with C; rejected saves above leave every row and link byte-for-byte.
    if not replacing:
        legacy.refresh_from_db()
        assert legacy.agent_version_id is None
