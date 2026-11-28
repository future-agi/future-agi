"""A phone or LiveKit agent changed from the Agents page: its prompt and settings
make the next version, that version shows its own values, and its contract is kept
although nothing reads it."""

import io
import json
import tarfile
from unittest.mock import patch

import pytest

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessEnvironmentVersion,
)
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services.agent_contracts import (
    store_environment_version_contract,
    store_version_contract,
    stored_version_contract,
)
from simulate.services.agent_setup import stored_agent
from simulate.services.harness_agent_changes import edit_agent_config
from simulate.services.harness_agent_versions import create_agent_version
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_agent_overview_api import _environment, _environment_version
from simulate.tests.test_code_archives import storage as storage  # noqa: F401
from simulate.tests.test_harness_agent_versions import _preflight

pytestmark = pytest.mark.django_db

AGENTS = "/simulate/agent-definitions/"
NUDGE = "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
Trigger = HarnessAgentVersionRuntime.Trigger
SourceKind = HarnessAgentVersionRuntime.SourceKind
NUMBER = "+14155550100"
PROMPT = "You are Riley. Help callers return items."


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _agent(organization, workspace, payload, source=None, checked=True):
    definition = AgentDefinition.objects.create(
        agent_name="riley",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        target_speaks_first=False,
        organization=organization,
        workspace=workspace,
    )
    version = create_agent_version(
        definition,
        parent=None,
        env=None,
        source=source or {"kind": "provider"},
        prompt=PROMPT,
        agent_payload=payload,
        trigger=Trigger.CONNECT,
    )
    if checked:
        with patch(NUDGE):
            _preflight(version)
    return definition, version


def _phone(organization, workspace):
    return _agent(
        organization,
        workspace,
        {
            "connector": "phone",
            "config": {"phone_number": NUMBER, "target_system_prompt": PROMPT},
        },
    )


def _livekit(organization, workspace):
    return _agent(
        organization,
        workspace,
        {
            "connector": "livekit",
            "config": {
                "agent_name": "worker-a",
                "url": "wss://x.livekit.cloud",
                "target_system_prompt": PROMPT,
            },
        },
    )


def _change(client, definition, **body):
    return client.post(f"{AGENTS}{definition.id}/versions/change/", body, format="json")


def _side(version_id):
    return HarnessAgentVersionRuntime.no_workspace_objects.get(
        agent_version_id=version_id
    )


class TestRefusals:
    def test_a_bad_number_is_refused_before_anything_changes(
        self, auth_client, organization, workspace
    ):
        definition, _ = _phone(organization, workspace)

        response = _change(
            auth_client, definition, kind="manual", settings={"phone_number": "123"}
        )

        assert response.status_code == 400
        assert "settings" in response.json()["details"]
        assert AgentVersion.objects.filter(agent_definition=definition).count() == 1

    def test_a_setting_the_source_does_not_take_is_refused(
        self, auth_client, organization, workspace
    ):
        definition, _ = _phone(organization, workspace)

        response = _change(
            auth_client, definition, kind="manual", settings={"agent_name": "x"}
        )

        assert response.status_code == 422
        assert response.json()["code"] == "agent_change_unsupported"
        assert response.json()["details"]["field"] == ["settings.agent_name"]

    @pytest.mark.parametrize("body", [{}, {"settings": {}}])
    def test_a_manual_change_needs_a_prompt_or_a_setting(
        self, auth_client, organization, workspace, body
    ):
        definition, _ = _phone(organization, workspace)

        assert _change(auth_client, definition, kind="manual", **body).status_code == (
            400
        )

    def test_settings_are_not_part_of_another_kind(
        self, auth_client, organization, workspace
    ):
        definition, _ = _phone(organization, workspace)

        response = _change(
            auth_client,
            definition,
            kind="sync",
            settings={"target_speaks_first": True},
        )

        assert response.status_code == 400

    def test_the_shared_edit_refuses_a_code_agents_prompt(self):
        with pytest.raises(HostedHarnessError) as refused:
            edit_agent_config(
                SourceKind.GITHUB, {"config": {}}, description="", prompt="x"
            )

        assert refused.value.code == "agent_change_unsupported"


class TestPhone:
    def test_a_new_prompt_and_number_make_the_next_version(
        self, auth_client, organization, workspace
    ):
        definition, _ = _phone(organization, workspace)

        response = _change(
            auth_client,
            definition,
            kind="manual",
            prompt="Be Riley, briefly.",
            settings={"phone_number": "+14155550199", "target_speaks_first": True},
        )

        assert response.status_code == 202, response.content
        side = _side(response.json()["version"]["id"])
        config = side.agent_payload["config"]
        assert config["target_system_prompt"] == "Be Riley, briefly."
        assert config["phone_number"] == "+14155550199"
        assert config["target_speaks_first"] is True
        assert side.source["phone_number"] == "+14155550199"
        assert side.trigger == Trigger.PROMPT
        snapshot = side.agent_version.configuration_snapshot
        assert snapshot["contact_number"] == "+14155550199"
        assert snapshot["target_speaks_first"] is True
        assert snapshot["description"] == "Be Riley, briefly."

    def test_the_same_values_again_are_up_to_date(
        self, auth_client, organization, workspace
    ):
        definition, version = _phone(organization, workspace)

        response = _change(
            auth_client,
            definition,
            kind="manual",
            prompt=PROMPT,
            settings={"phone_number": NUMBER},
        )

        assert response.status_code == 200
        assert response.json()["up_to_date"] is True
        assert response.json()["version"]["id"] == str(version.id)


class TestLiveKit:
    def test_a_new_agent_name_is_the_one_dialled(
        self, auth_client, organization, workspace
    ):
        definition, _ = _livekit(organization, workspace)

        response = _change(
            auth_client, definition, kind="manual", settings={"agent_name": "worker-b"}
        )

        assert response.status_code == 202, response.content
        side = _side(response.json()["version"]["id"])
        assert side.source["agent_name"] == "worker-b"
        assert side.agent_payload["config"]["target_system_prompt"] == PROMPT
        assert side.trigger == Trigger.AGENT_PAYLOAD
        assert (
            side.agent_version.configuration_snapshot["livekit_agent_name"]
            == "worker-b"
        )


class TestVapi:
    def test_a_setting_keeps_the_sync_fingerprint(
        self, auth_client, organization, workspace
    ):
        definition, _ = _agent(
            organization,
            workspace,
            {"connector": "vapi", "config": {"assistant_id": "asst-1"}},
            source={"kind": "provider", "profile_digest": "sha256:d1"},
        )

        response = _change(
            auth_client,
            definition,
            kind="manual",
            settings={"target_speaks_first": True},
        )

        assert response.status_code == 202, response.content
        side = _side(response.json()["version"]["id"])
        assert side.source["profile_digest"] == "sha256:d1"
        assert side.source["assistant_id"] == "asst-1"


class TestContract:
    def test_a_livekit_version_gets_its_document_then_its_contract(
        self, organization, workspace, storage
    ):
        _, version = _livekit(organization, workspace)

        stored = store_version_contract(
            organization_id=organization.id,
            agent_version_id=version.id,
            contract={"rules": ["one"]},
        )

        assert stored is True
        assert stored_version_contract(version.id) == {"rules": ["one"]}
        document = stored_agent(version.id)
        assert document["kind"] == "endpoint"
        assert document["name"] == "worker-a"
        assert document["behaviour"][0]["instructions"] == PROMPT

    def test_a_contract_is_kept_once(self, organization, workspace, storage):
        _, version = _livekit(organization, workspace)
        kwargs = {"organization_id": organization.id, "agent_version_id": version.id}
        store_version_contract(contract={"rules": ["one"]}, **kwargs)

        assert store_version_contract(contract={"rules": ["two"]}, **kwargs) is False
        assert stored_version_contract(version.id) == {"rules": ["one"]}

    def test_a_phone_document_carries_its_number(
        self, organization, workspace, storage
    ):
        _, version = _phone(organization, workspace)

        store_version_contract(
            organization_id=organization.id,
            agent_version_id=version.id,
            contract={"rules": []},
        )

        assert stored_agent(version.id)["telephony"][0]["number"] == NUMBER

    def test_a_code_version_without_a_reading_is_left_alone(
        self, organization, workspace, storage
    ):
        _, version = _agent(
            organization,
            workspace,
            {"connector": "auto", "config": {}},
            source={
                "kind": "github",
                "repository": "acme/agent",
                "ref": "main",
                "commit_sha": "a" * 40,
            },
            checked=False,
        )

        with patch("simulate.services.agent_contracts.logger") as log:
            stored = store_version_contract(
                organization_id=organization.id,
                agent_version_id=version.id,
                contract={"rules": []},
            )

        assert stored is False
        assert not AgentVersionSetup.objects.filter(agent_version=version).exists()
        log.info.assert_called_once_with(
            "agent_contract_skipped",
            agent_version_id=str(version.id),
            reason="no_stored_agent",
        )

    def test_a_rebuilt_environment_keeps_its_contract_on_its_version(
        self, organization, workspace, storage
    ):
        definition, version = _livekit(organization, workspace)
        environment = _environment(definition, 5)
        environment_version = _environment_version(environment, 2)
        HarnessEnvironmentVersion.no_workspace_objects.filter(
            id=environment_version.id
        ).update(agent_version=version)
        storage.objects[environment_version.archive_object_key] = _archive(
            {"rules": ["sixty days"]}
        )

        assert store_environment_version_contract(environment_version.id) is True
        assert stored_version_contract(version.id) == {"rules": ["sixty days"]}


def _archive(contract):
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode="w:gz") as tar:
        data = json.dumps(contract).encode()
        member = tarfile.TarInfo("contract.json")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    return body.getvalue()
