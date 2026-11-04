"""Provider Sync for a Vapi or Retell agent: what is kept of the provider's
profile, the document it maps to, and how a Sync decides there is a new version."""

import copy
from unittest.mock import patch

import pytest

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
)
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services import agent_versions
from simulate.services.agent_setup import AgentStoreError
from simulate.services.harness_agent_versions import create_agent_version
from simulate.services.provider_profile import (
    _safe,
    _secret_key,
    profile_digest,
    profile_document,
)

pytestmark = pytest.mark.django_db

Trigger = HarnessAgentVersionRuntime.Trigger
READ = "simulate.services.provider_profile.read_profile"
STORE = "simulate.services.agent_setup.store_agent"

VAPI = {
    "name": "Harbor Hotels",
    "model": {
        "provider": "openai",
        "model": "gpt-4o",
        "maxTokens": 250,
        "temperature": 0.3,
        "messages": [{"role": "system", "content": "Book rooms politely."}],
        "tools": [{"type": "function", "function": {"name": "check_rooms"}}],
    },
    "voice": {"provider": "11labs", "voiceId": "v-1"},
}


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


@pytest.fixture
def agent(organization, workspace):
    definition = AgentDefinition.objects.create(
        agent_name="harbor",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        provider="vapi",
        assistant_id="asst-1",
        inbound=True,
        organization=organization,
        workspace=workspace,
    )
    create_agent_version(
        definition,
        parent=None,
        env=None,
        source={"kind": "provider"},
        agent_payload={"connector": "vapi", "config": {"assistant_id": "asst-1"}},
        trigger=Trigger.CONNECT,
    )
    return definition


def _versions(definition):
    return list(
        AgentVersion.no_workspace_objects.filter(agent_definition=definition)
        .order_by("version_number")
        .values_list("version_number", flat=True)
    )


def _parent_row(definition):
    return HarnessAgentVersionRuntime.no_workspace_objects.get(
        agent_version__agent_definition=definition,
        agent_version__version_number=1,
    )


def _sync(definition, profile):
    with patch(READ, return_value=_safe(copy.deepcopy(profile))):
        return agent_versions.sync_version(definition)


class TestSecretFilter:
    @pytest.mark.parametrize(
        "key",
        [
            "X-API-Key",
            "x-api-key",
            "apiKey",
            "api_key",
            "password",
            "secretToken",
            "accessToken",
            "authorization",
            "privateKey",
            "cookie",
        ],
    )
    def test_a_secret_key_is_redacted(self, key):
        assert _secret_key(key)
        assert _safe("value", key) == "[redacted]"

    @pytest.mark.parametrize(
        "key", ["maxTokens", "max_tokens", "tokenizer", "keywords", "model"]
    )
    def test_an_ordinary_key_is_kept(self, key):
        assert not _secret_key(key)
        assert _safe(250, key) == 250

    def test_every_header_value_is_redacted(self):
        kept = _safe({"server": {"headers": {"X-Custom": "abc", "Accept": "json"}}})

        assert kept == {
            "server": {"headers": {"X-Custom": "[redacted]", "Accept": "[redacted]"}}
        }

    def test_a_url_loses_its_query(self):
        kept = _safe({"url": "https://hooks.example.com/a?token=abc"})

        assert kept == {"url": "https://hooks.example.com/a"}

    def test_a_max_tokens_change_moves_the_digest(self):
        more = copy.deepcopy(VAPI)
        more["model"]["maxTokens"] = 4000

        assert profile_digest(_safe(VAPI)) != profile_digest(_safe(more))


class TestDocument:
    def test_a_vapi_profile_maps_its_prompt_tools_and_model(self):
        document = profile_document("vapi", "asst-1", _safe(VAPI), modality="voice")

        assert document["kind"] == "hosted"
        assert document["provider_ref"] == {
            "provider": "vapi",
            "id": "asst-1",
            "version": None,
        }
        assert document["behaviour"][0]["instructions"] == "Book rooms politely."
        assert [tool["name"] for tool in document["tools"]] == ["check_rooms"]
        assert document["model"]["max_tokens"] == 250
        assert document["setup_hash"].startswith("sh1:")


class TestSync:
    def test_a_failed_first_store_is_tried_again(self, agent):
        before = _parent_row(agent).source

        with patch(STORE, side_effect=AgentStoreError("agent_invalid")):
            assert _sync(agent, VAPI).version_number == 1
        assert not AgentVersionSetup.no_workspace_objects.filter(
            agent_version__agent_definition=agent
        ).exists()

        assert _sync(agent, VAPI).version_number == 1
        assert AgentVersionSetup.no_workspace_objects.filter(
            agent_version__agent_definition=agent, agent_version__version_number=1
        ).exists()
        assert _parent_row(agent).source == before

    def test_the_first_look_leaves_the_parent_identity_alone(self, agent):
        row = _parent_row(agent)
        before = (row.source, row.updated_at)

        _sync(agent, VAPI)
        _sync(agent, VAPI)

        row.refresh_from_db()
        assert (row.source, row.updated_at) == before
        assert "profile_digest" not in row.source
        assert _versions(agent) == [1]

    def test_a_change_at_the_provider_makes_a_version(self, agent):
        _sync(agent, VAPI)
        changed = copy.deepcopy(VAPI)
        changed["model"]["messages"][0]["content"] = "Book rooms and upsell."

        version = _sync(agent, changed)

        assert version.version_number == 2
        assert version.description == "Book rooms and upsell."
        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version=version
        )
        assert runtime.trigger == Trigger.PROVIDER_SYNC
        assert runtime.source["profile_digest"] == profile_digest(_safe(changed))
        assert AgentVersionSetup.no_workspace_objects.filter(
            agent_version=version
        ).exists()

    def test_the_same_profile_again_makes_nothing(self, agent):
        _sync(agent, VAPI)
        changed = copy.deepcopy(VAPI)
        changed["voice"]["voiceId"] = "v-2"
        _sync(agent, changed)

        again = _sync(agent, changed)

        assert again.version_number == 2
        assert _versions(agent) == [1, 2]
