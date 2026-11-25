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
from simulate.models.agent_definition import ProviderCredentials
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services import agent_versions
from simulate.services.agent_contracts import stored_read_contract
from simulate.services.agent_reading_sandbox import PROFILE_PATH
from simulate.services.agent_setup import AgentStoreError, stored_agent
from simulate.services.harness_agent_versions import create_agent_version
from simulate.services.harness_design_review import (
    AWAITING_APPROVAL,
    begin_design_review,
    stored_review,
)
from simulate.services.provider_profile import (
    _safe,
    _secret_key,
    profile_bytes_for_version,
    profile_digest,
    profile_document,
)
from simulate.tasks.harness_agent_versions import (
    read_harness_agent_version_contract,
)
from simulate.tests.test_harness_design_review import CONTRACT
from simulate.tests.test_harness_design_review_reading import _received
from simulate.tests.test_hosted_harness_conversation import storage  # noqa: F401

pytestmark = pytest.mark.django_db

Trigger = HarnessAgentVersionRuntime.Trigger
READ = "simulate.services.provider_profile.read_profile"
STORE = "simulate.services.agent_setup.store_agent"
TASKS = "simulate.tasks.harness_agent_versions"
SCHEDULE_READING = f"{TASKS}.schedule_agent_contract_reading"
SCHEDULE_DESIGN_READING = f"{TASKS}.schedule_design_reading"
PREFLIGHT = "simulate.services.harness_agent_versions._schedule_preflight"
SANDBOX = "simulate.services.agent_reading_sandbox"
ACTIVITY_INFO = "tfc.logging.temporal.context.try_activity_info"
READ_CONTRACT = {**copy.deepcopy(CONTRACT), "reading_inputs": {}}
read_contract_activity = read_harness_agent_version_contract._original_func

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
            "key",
            "publicKey",
            "auth",
            "bearerToken",
            "jwt",
            "signature",
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

    @pytest.mark.parametrize("name", ["query_params", "queryParams"])
    def test_every_query_param_value_is_redacted(self, name):
        kept = _safe({"server": {name: {"code": "fn-key", "v": "1"}}})

        assert kept == {"server": {name: {"code": "[redacted]", "v": "[redacted]"}}}

    def test_a_url_loses_its_query(self):
        kept = _safe({"url": "https://hooks.example.com/a?token=abc"})

        assert kept == {"url": "https://hooks.example.com/a"}

    def test_a_tools_parameter_names_are_kept(self):
        profile = copy.deepcopy(VAPI)
        profile["model"]["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "check_rooms",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "token": {"type": "string"},
                            "apiKey": {"type": "string", "default": "sk-live-1"},
                        },
                        "required": ["token"],
                    },
                },
                "server": {
                    "url": "https://hooks.example.com/rooms?sig=abc",
                    "secret": "s3cret",
                    "headers": {"X-API-Key": "abc"},
                },
                "credentialId": "cred-1",
            }
        ]

        kept = _safe(profile)
        document = profile_document("vapi", "asst-1", kept, modality="voice")

        tool = kept["model"]["tools"][0]
        assert tool["function"]["parameters"] == {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "apiKey": {"type": "string", "default": "[redacted]"},
            },
            "required": ["token"],
        }
        assert tool["server"] == {
            "url": "https://hooks.example.com/rooms",
            "secret": "[redacted]",
            "headers": {"X-API-Key": "[redacted]"},
        }
        assert tool["credentialId"] == "[redacted]"
        assert set(document["tools"][0]["parameters"]["properties"]) == {
            "token",
            "apiKey",
        }
        assert "sk-live-1" not in str(document)

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


@pytest.fixture
def classic_agent(organization, workspace):
    definition = AgentDefinition.objects.create(
        agent_name="harbor classic",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        provider="vapi",
        assistant_id="asst-1",
        inbound=True,
        organization=organization,
        workspace=workspace,
    )
    version = definition.create_version(
        description="Book rooms politely.", commit_message="v1", status="active"
    )
    ProviderCredentials.objects.create(
        agent_version=version,
        provider_type="vapi",
        api_key="sk-vapi-key-123456",
        assistant_id="asst-1",
    )
    return definition


class TestSyncWithoutSideRow:
    def test_a_change_at_the_provider_makes_a_provider_version(self, classic_agent):
        first = _sync(classic_agent, VAPI)
        assert first.version_number == 1
        assert AgentVersionSetup.no_workspace_objects.filter(
            agent_version=first
        ).exists()
        changed = copy.deepcopy(VAPI)
        changed["model"]["messages"][0]["content"] = "Book rooms and upsell."

        version = _sync(classic_agent, changed)

        assert version.version_number == 2
        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version=version
        )
        assert runtime.source_kind == HarnessAgentVersionRuntime.SourceKind.PROVIDER
        assert runtime.source["provider"] == "vapi"
        assert runtime.source["assistant_id"] == "asst-1"
        assert runtime.source["profile_digest"] == profile_digest(_safe(changed))
        assert runtime.agent_payload["connector"] == "vapi"
        assert set(runtime.credential_fingerprints) == {"VAPI_API_KEY"}
        assert (
            ProviderCredentials.objects.get(agent_version=version).get_api_key()
            == "sk-vapi-key-123456"
        )
        assert _versions(classic_agent) == [1, 2]


@pytest.fixture
def hosted_review(settings):
    settings.HARNESS_DESIGN_REVIEW_ENABLED = True
    settings.HARNESS_SEED_CONTRACT_ENABLED = True
    settings.ALK_HOSTED_BUILDER_SESSION = True
    settings.HARNESS_HOSTED_DESIGN_REVIEW_ENABLED = True
    return settings


def _sync_queued(definition, profile, capture):
    with (
        patch(SCHEDULE_READING) as schedule,
        patch(PREFLIGHT),
        capture(execute=True),
    ):
        version = _sync(definition, profile)
    return version, [call.args[0] for call in schedule.call_args_list]


def _read(version_id, *, reading=None, crash=None):
    with (
        patch(f"{TASKS}.close_old_connections"),
        patch(ACTIVITY_INFO, return_value=None),
        patch(f"{SANDBOX}.require_harness_authoring"),
        patch(
            f"{SANDBOX}.run_reading",
            return_value=reading,
            side_effect=crash,
        ) as run,
        patch("simulate.services.harness_evals.briefing_evals", return_value=[]),
        patch(f"{TASKS}.logger") as logger,
    ):
        status = read_contract_activity(str(version_id))
    return status, run, logger


def _read_contract_of(version_id):
    return _read(version_id, reading=(None, {}, copy.deepcopy(READ_CONTRACT), None))


@pytest.mark.usefixtures("hosted_review", "storage")
class TestSyncReading:
    def test_the_first_sync_queues_a_reading_of_the_parent(
        self, agent, django_capture_on_commit_callbacks
    ):
        version, queued = _sync_queued(agent, VAPI, django_capture_on_commit_callbacks)

        assert version.version_number == 1
        assert queued == [str(version.id)]

    def test_a_changed_profile_makes_a_version_and_queues_its_reading(
        self, agent, django_capture_on_commit_callbacks
    ):
        _sync_queued(agent, VAPI, django_capture_on_commit_callbacks)
        changed = copy.deepcopy(VAPI)
        changed["model"]["messages"][0]["content"] = "Book rooms and upsell."

        version, queued = _sync_queued(
            agent, changed, django_capture_on_commit_callbacks
        )

        assert version.version_number == 2
        assert queued == [str(version.id)]

    def test_an_unchanged_profile_queues_nothing(
        self, agent, django_capture_on_commit_callbacks
    ):
        _sync_queued(agent, VAPI, django_capture_on_commit_callbacks)

        version, queued = _sync_queued(agent, VAPI, django_capture_on_commit_callbacks)

        assert version.version_number == 1
        assert queued == []

    def test_a_reading_stores_the_read_contract_beside_the_synced_document(
        self, agent, django_capture_on_commit_callbacks
    ):
        version, _ = _sync_queued(agent, VAPI, django_capture_on_commit_callbacks)
        synced = stored_agent(version.id, include_stale=True)
        pinned = profile_bytes_for_version(version)

        status, run, _logger = _read_contract_of(version.id)

        assert status == "stored"
        assert run.call_args.kwargs["files"] == {PROFILE_PATH: pinned}
        assert stored_read_contract(version.id) == READ_CONTRACT
        assert stored_agent(version.id, include_stale=True) == synced

    def test_a_failed_reading_keeps_the_synced_document(
        self, agent, django_capture_on_commit_callbacks
    ):
        version, _ = _sync_queued(agent, VAPI, django_capture_on_commit_callbacks)
        synced = stored_agent(version.id, include_stale=True)

        status, _run, logger = _read(version.id, crash=RuntimeError("sandbox died"))

        assert status == "failed"
        assert logger.warning.call_args_list[0].kwargs["exc_info"] is True
        assert stored_read_contract(version.id) is None
        assert stored_agent(version.id, include_stale=True) == synced

    def test_a_version_already_read_is_not_read_again(
        self, agent, django_capture_on_commit_callbacks
    ):
        version, _ = _sync_queued(agent, VAPI, django_capture_on_commit_callbacks)
        _read_contract_of(version.id)

        status, run, _logger = _read_contract_of(version.id)

        assert status == "skipped"
        run.assert_not_called()

    def test_a_build_of_a_synced_version_opens_its_review_at_once(
        self, agent, user, workspace, django_capture_on_commit_callbacks
    ):
        version, _ = _sync_queued(agent, VAPI, django_capture_on_commit_callbacks)
        _read_contract_of(version.id)
        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version=version
        )
        job = _received(user, workspace)

        with (
            patch(READ, return_value=_safe(copy.deepcopy(VAPI))),
            patch(SCHEDULE_DESIGN_READING) as schedule,
        ):
            begin_design_review(job, runtime, None)

        job.refresh_from_db()
        assert stored_review(job)["status"] == AWAITING_APPROVAL
        schedule.assert_not_called()
