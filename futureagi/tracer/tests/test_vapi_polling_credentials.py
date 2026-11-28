"""Vapi polling must follow hosted agents' canonical, versioned credentials.

Real unsaved Django models and the canonical decrypting reader are used here;
only version lookup, HTTP and storage are mocked. No provider account is needed.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import pytest
import requests

from agentcc.services.credential_manager import encrypt_token
from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from tracer.models.observability_provider import ProviderChoices
from tracer.services.observability_providers import ObservabilityService
from tracer.utils import observability_provider as scheduler

pytestmark = pytest.mark.unit

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 2, tzinfo=UTC)
CALL = {"id": "synthetic-vapi-call", "assistantId": "synthetic-assistant"}


@pytest.fixture
def agent():
    # Hosted connection creation leaves the nullable legacy api_key unset.
    definition = AgentDefinition(provider="vapi", assistant_id="synthetic-assistant")
    AgentDefinition.credentials_legacy.related.set_cached_value(definition, None)
    return definition


@pytest.fixture
def versions():
    with (
        patch.object(
            AgentDefinition, "active_version", new_callable=PropertyMock
        ) as active,
        patch.object(
            AgentDefinition, "latest_version", new_callable=PropertyMock
        ) as latest,
    ):
        active.return_value = None
        latest.return_value = None
        yield active, latest


def version_with_key(agent, key):
    version = AgentVersion(agent_definition=agent)
    if key is None:
        AgentVersion.credentials.related.set_cached_value(version, None)
    else:
        ProviderCredentials(
            agent_version=version,
            provider_type=ProviderCredentials.ProviderType.VAPI,
            api_key=encrypt_token(key),
        )
    return version


@pytest.fixture
def provider(agent):
    return SimpleNamespace(
        id="synthetic-provider",
        provider=ProviderChoices.VAPI,
        agent_definition=agent,
        last_fetched_at=START,
    )


@pytest.fixture
def transport():
    with patch("tracer.services.observability_providers.requests.get") as get:
        get.return_value.json.return_value = [CALL]
        yield get


@pytest.fixture
def storage():
    with (
        patch.object(scheduler, "process_and_store_logs") as store,
        patch.object(scheduler, "_advance_watermark") as advance,
        patch.object(scheduler.timezone, "now", return_value=END),
    ):
        store.return_value = scheduler.StoreOutcome(1, 0, 0)
        yield store, advance


def test_hosted_connection_polls_with_encrypted_version_key(
    agent, versions, provider, transport, storage
):
    active, _ = versions
    active.return_value = version_with_key(agent, "synthetic-hosted-key")
    assert agent.api_key is None

    outcome = scheduler._poll_other_provider(provider, start_time=None, end_time=None)

    assert outcome.stored == 1
    assert transport.call_args.kwargs["headers"] == {
        "Authorization": "Bearer synthetic-hosted-key"
    }
    assert transport.call_args.kwargs["params"] == {
        "assistantId": "synthetic-assistant",
        "limit": 100,
        "updatedAtGt": START.isoformat(),
        "updatedAtLe": END.isoformat(),
    }
    store, advance = storage
    store.assert_called_once_with([CALL], provider)
    advance.assert_called_once_with(provider.id, END)


def test_rotation_uses_new_default_after_old_key_is_revoked(
    agent, versions, provider, transport
):
    active, latest = versions
    old = version_with_key(agent, "synthetic-old-key")
    rotated = version_with_key(agent, "synthetic-rotated-key")
    agent.api_key = "synthetic-old-key"
    active.return_value = old
    latest.return_value = rotated
    revoked = False

    def get(_url, *, headers, **_kwargs):
        if revoked and headers["Authorization"] == "Bearer synthetic-old-key":
            raise requests.HTTPError("synthetic revoked credential")
        return Mock(json=lambda: [CALL], raise_for_status=lambda: None)

    transport.side_effect = get
    # A draft must not replace the active/default version's credentials.
    assert ObservabilityService.get_call_logs(provider) == [CALL]
    assert (
        transport.call_args.kwargs["headers"]["Authorization"]
        == "Bearer synthetic-old-key"
    )

    # Manage versions activates the replacement; no AgentDefinition key edit.
    active.return_value = rotated
    revoked = True
    assert ObservabilityService.get_call_logs(provider) == [CALL]
    assert (
        transport.call_args.kwargs["headers"]["Authorization"]
        == "Bearer synthetic-rotated-key"
    )


def test_latest_version_is_used_when_no_version_is_active(
    agent, versions, provider, transport
):
    _, latest = versions
    latest.return_value = version_with_key(agent, "synthetic-latest-key")
    assert ObservabilityService.get_call_logs(provider) == [CALL]
    assert (
        transport.call_args.kwargs["headers"]["Authorization"]
        == "Bearer synthetic-latest-key"
    )


def test_canonical_legacy_credential_fallback_is_preserved(
    agent, versions, provider, transport
):
    active, _ = versions
    active.return_value = version_with_key(agent, None)
    ProviderCredentials(
        agent_definition=agent,
        provider_type=ProviderCredentials.ProviderType.VAPI,
        api_key=encrypt_token("synthetic-encrypted-legacy-key"),
    )
    assert ObservabilityService.get_call_logs(provider) == [CALL]
    assert (
        transport.call_args.kwargs["headers"]["Authorization"]
        == "Bearer synthetic-encrypted-legacy-key"
    )


def test_pre_versioning_agent_can_still_use_legacy_column(
    agent, versions, provider, transport
):
    agent.api_key = "synthetic-pre-versioning-key"
    assert ObservabilityService.get_call_logs(provider) == [CALL]
    assert (
        transport.call_args.kwargs["headers"]["Authorization"]
        == "Bearer synthetic-pre-versioning-key"
    )


@pytest.mark.parametrize("legacy_key", [None, "synthetic-stale-key"])
def test_missing_versioned_key_does_not_fall_back_to_stale_column_or_advance(
    agent, versions, provider, transport, storage, legacy_key
):
    active, _ = versions
    active.return_value = version_with_key(agent, "")
    agent.api_key = legacy_key
    with pytest.raises(ValueError, match="Vapi API key is not configured"):
        scheduler._poll_other_provider(provider, start_time=None, end_time=None)
    transport.assert_not_called()
    store, advance = storage
    store.assert_not_called()
    advance.assert_not_called()


def test_missing_agent_does_not_advance(provider, transport, storage):
    provider.agent_definition = None
    with pytest.raises(ValueError, match="Vapi observability needs a linked agent"):
        scheduler._poll_other_provider(provider, start_time=None, end_time=None)
    transport.assert_not_called()
    storage[1].assert_not_called()


def test_missing_assistant_never_lists_calls_for_the_entire_account(
    agent, versions, provider, transport, storage
):
    active, _ = versions
    active.return_value = version_with_key(agent, "synthetic-valid-key")
    agent.api_key = "synthetic-stale-key"
    agent.assistant_id = None
    with pytest.raises(ValueError, match="Vapi assistant id is not configured"):
        scheduler._poll_other_provider(provider, start_time=None, end_time=None)
    transport.assert_not_called()
    storage[1].assert_not_called()


def test_undecryptable_versioned_key_does_not_use_legacy_or_advance(
    agent, versions, provider, transport, storage
):
    active, _ = versions
    version = version_with_key(agent, "synthetic-current-key")
    active.return_value = version
    agent.api_key = "synthetic-stale-key"
    with (
        patch.object(
            ProviderCredentials,
            "get_api_key",
            side_effect=ValueError("synthetic-secret"),
        ),
        pytest.raises(ValueError, match="^Vapi credential could not be decrypted$"),
    ):
        scheduler._poll_other_provider(provider, start_time=None, end_time=None)
    transport.assert_not_called()
    storage[1].assert_not_called()


def test_http_auth_failure_does_not_advance(
    agent, versions, provider, transport, storage
):
    active, _ = versions
    active.return_value = version_with_key(agent, "synthetic-invalid-key")
    transport.return_value.raise_for_status.side_effect = requests.HTTPError(
        "synthetic 401"
    )
    with pytest.raises(requests.HTTPError):
        scheduler._poll_other_provider(provider, start_time=None, end_time=None)
    storage[0].assert_not_called()
    storage[1].assert_not_called()


def test_successful_empty_window_can_advance(
    agent, versions, provider, transport, storage
):
    active, _ = versions
    active.return_value = version_with_key(agent, "synthetic-current-key")
    transport.return_value.json.return_value = []
    scheduler._poll_other_provider(provider, start_time=None, end_time=None)
    transport.assert_called_once()
    storage[0].assert_called_once_with([], provider)
    storage[1].assert_called_once_with(provider.id, END)
