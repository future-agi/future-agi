"""Agent-first environment creation: the environment's own agent and first version."""

import copy
import json
import logging
import threading
import uuid
from contextlib import ExitStack
from functools import partial
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient

from accounts.models.workspace import Workspace
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HostedHarnessConversation,
    HostedHarnessJob,
    HostedHarnessSecret,
)
from simulate.models.agent_definition import ProviderCredentials
from simulate.serializers.harness_job import (
    CREDENTIAL_VALUE_MAX_CHARS,
    HarnessJobCreateSerializer,
    HarnessPreflightSerializer,
)
from simulate.services import harness_agent_versions as service
from simulate.services.harness_agent_versions import (
    CreationCheck,
    SubmittedCredentials,
    agent_version_secret_ref,
    ensure_environment_agent,
    submitted_credentials,
)
from simulate.services.harness_credentials import AGENT_VERSION_FILE_KEY
from simulate.services.harness_provider import serialize_job
from simulate.services.harness_versioning import run_warnings
from simulate.services.hosted_harness import canonical_digest, create_hosted_job
from simulate.services.hosted_harness_gateway import PlatformSecretResolver
from simulate.tests.test_harness_provider import _v1_payload
from tfc.telemetry.middleware import _mask_sensitive

pytestmark = pytest.mark.django_db

URL = "/simulate/api/harness-jobs/"
PREFLIGHT_URL = "/simulate/api/harness-jobs/preflight/"
VALUES = {"OPENAI_API_KEY": "val-openai-1", "DB_URL": "val-db-1"}
SECRETS = (
    "val-openai-1",
    "val-openai-2",
    "val-db-1",
    "val-vapi-1",
    "val-vapi-2",
    "val-lk-key",
    "val-lk-secret",
    "val-adc",
    "val-vault",
)
_DEFAULT = object()


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_PROVIDER = "hosted"


@pytest.fixture
def gates():
    """Everything create checks besides credentials is stubbed: egress, telephony,
    usage, the source scan, the live probe and the workflow start."""
    with ExitStack() as stack:

        def on(target, **kwargs):
            return stack.enter_context(patch(target, **kwargs))

        on("simulate.services.harness_provider._validate_known_hosted_egress")
        on(
            "simulate.services.harness_agent_changes.validate_public_github_source",
            return_value=None,
        )
        on("simulate.services.harness_provider._validate_phone_connectivity")
        on(
            "simulate.services.hosted_harness_gateway.platform_dialer_status",
            return_value={"available": True, "missing": []},
        )
        on("simulate.services.harness_usage.require_harness_run_usage")
        on("simulate.services.harness_usage.require_harness_source_call_usage")
        yield SimpleNamespace(
            start=on("simulate.temporal.client.start_hosted_harness_gateway_workflow"),
            scan=on(
                "simulate.services.harness_provider._preflight_source_connectors",
                return_value=([], [], 3),
            ),
            probe=on(
                "simulate.services.harness_provider._preflight_credential_probe",
                return_value=[],
            ),
        )


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _body(
    connector="auto",
    *,
    config=None,
    values=None,
    refs=None,
    mode=None,
    source=_DEFAULT,
    metadata=None,
):
    payload = _v1_payload(scenario_count=2)
    payload["agent"] = {
        "connector": connector,
        "config": dict(config or {}),
        "secret_refs": copy.deepcopy(refs or {}),
    }
    if mode:
        payload["agent"]["mode"] = mode
    if source is None:
        payload.pop("source")
    elif source is not _DEFAULT:
        payload["source"] = source
    if values is not None:
        payload["credential_values"] = dict(values)
    if metadata is not None:
        payload["metadata"] = metadata
    return payload


def _vapi(assistant="asst-1", key="val-vapi-1", **config):
    return _body(
        "vapi",
        mode="connect_only",
        config={"assistant_id": assistant, **config},
        values={"VAPI_API_KEY": key},
        source=None,
    )


def _phone(number="+14155550100", prompt="You are the clinic line."):
    return _body(
        "phone",
        mode="connect_only",
        config={"phone_number": number, "target_system_prompt": prompt},
        source=None,
    )


def _livekit():
    return _body(
        "livekit",
        mode="connect_only",
        config={
            "agent_name": "lk-agent",
            "target_system_prompt": "You are LiveKit support.",
            "livekit_url": "wss://lk.example.test",
        },
        values={"LIVEKIT_API_KEY": "val-lk-key", "LIVEKIT_API_SECRET": "val-lk-secret"},
        source=None,
    )


def _post(client, workspace, body, key=None):
    return client.post(
        URL,
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )


def _job(response):
    assert response.status_code == 202, response.content
    return HostedHarnessJob.no_workspace_objects.get(
        id=response.json()["job"]["job_id"]
    )


def _side(version_id):
    return HarnessAgentVersionRuntime.no_workspace_objects.get(
        agent_version_id=version_id
    )


def _definitions(organization):
    return AgentDefinition.no_workspace_objects.filter(organization=organization)


def _no_values(text):
    for secret_value in SECRETS:
        assert secret_value not in text


def _stored_secret(organization, name, secret_value):
    HostedHarnessSecret.objects.create(
        organization=organization, name=name, version="1", encrypted_value=secret_value
    )
    return {
        "manager": "platform-vault",
        "key": name,
        "version": "1",
        "purpose": "target_provider",
    }


class TestCreate:
    def test_a_github_agent_gets_its_own_first_version_pinned(
        self, client, user, workspace, gates
    ):
        job = _job(_post(client, workspace, _body(values=VALUES)))

        version = job.agent_version
        assert version is not None
        text = json.dumps(job.payload) + job.request_digest
        _no_values(text)
        names_only = {name: agent_version_secret_ref(name) for name in sorted(VALUES)}
        assert job.payload["agent"]["secret_refs"] == names_only
        request = _body(refs=names_only)
        assert job.request_digest == canonical_digest(
            HarnessJobCreateSerializer(data=request).run_validation(request)
        )
        runtime = _side(version.id)
        assert runtime.preflight_status == "passed"
        assert runtime.trigger == "creation"
        assert runtime.agent_payload["definition_owner"] == "environment"
        assert runtime.agent_payload["requested_by_environments"] == [str(job.id)]
        assert json.loads(runtime.get_credentials())["env"] == VALUES
        assert not set(VALUES) & set(runtime.required_variable_names)
        definition = version.agent_definition
        assert definition.agent_type == "text"
        assert definition.inbound is True
        assert version.status == "active"
        metadata = job.payload["metadata"]
        assert metadata["agent_definition_owner"] == "environment"
        assert metadata["agent_definition_id"] == str(definition.id)
        assert metadata["build_agent_version_id"] == str(version.id)
        assert PlatformSecretResolver().resolve(job) == VALUES

    def test_a_replay_returns_the_job_without_reading_credentials_again(
        self, client, user, workspace, gates
    ):
        first = _job(_post(client, workspace, _body(values=VALUES), key="replay"))
        blob = _side(first.agent_version_id).credential_blob
        counts = (_definitions(user.organization).count(), AgentVersion.objects.count())
        gates.probe.reset_mock()
        gates.probe.return_value = [
            {"provider": "openai", "aliases": ["OPENAI_API_KEY"], "ok": False}
        ]

        with patch.object(service, "_resolve_refs") as resolved:
            again = _post(
                client,
                workspace,
                _body(values={**VALUES, "OPENAI_API_KEY": "val-openai-2"}),
                key="replay",
            )

        assert _job(again).id == first.id
        gates.probe.assert_not_called()
        resolved.assert_not_called()
        assert counts == (
            _definitions(user.organization).count(),
            AgentVersion.objects.count(),
        )
        assert _side(first.agent_version_id).credential_blob == blob
        other = _post(client, workspace, _body(values={"OTHER": "x"}), key="replay")
        assert other.status_code == 409
        assert other.json()["error"] == "idempotency_conflict"

    def test_a_vapi_key_goes_to_the_versions_own_provider_row(
        self, client, user, workspace, gates
    ):
        job = _job(_post(client, workspace, _vapi()))

        version = job.agent_version
        row = ProviderCredentials.objects.get(agent_version=version)
        assert row.get_api_key() == "val-vapi-1"
        assert row.assistant_id == "asst-1"
        assert "val-vapi-1" not in _side(version.id).get_credentials()
        assert job.payload["agent"]["secret_refs"] == {
            "VAPI_API_KEY": agent_version_secret_ref("VAPI_API_KEY")
        }
        assert PlatformSecretResolver().resolve(job) == {"VAPI_API_KEY": "val-vapi-1"}
        definition = version.agent_definition
        assert (definition.provider, definition.assistant_id) == ("vapi", "asst-1")
        assert definition.agent_type == "voice"
        assert version.configuration_snapshot["assistant_id"] == "asst-1"

    def test_a_credential_change_keeps_the_provider_prompt(
        self, client, user, workspace, gates
    ):
        job = _job(_post(client, workspace, _vapi()))
        definition = job.agent_version.agent_definition
        definition.description = "The assistant's prompt."
        definition.save(update_fields=["description"])

        version = service.change_credentials(
            job, secret_values={"VAPI_API_KEY": "val-vapi-2"}, activate=False
        )

        version.refresh_from_db()
        assert version.description == ""
        assert (
            version.configuration_snapshot["description"] == "The assistant's prompt."
        )


class TestSharedProviderAgent:
    def test_environments_on_one_assistant_share_its_agent(
        self, client, user, workspace, gates
    ):
        first = _job(_post(client, workspace, _vapi()))
        definition = first.agent_version.agent_definition
        AgentDefinition.no_workspace_objects.filter(id=definition.id).update(
            description="first prompt"
        )

        same = _job(_post(client, workspace, _vapi()))
        other_key = _job(_post(client, workspace, _vapi(key="val-vapi-2")))
        other_config = _job(_post(client, workspace, _vapi(target_speaks_first=True)))

        for job in (same, other_key, other_config):
            assert job.agent_version.agent_definition_id == definition.id
        definition.refresh_from_db()
        assert definition.description == "first prompt"
        assert same.agent_version_id == first.agent_version_id
        assert other_key.agent_version_id != first.agent_version_id
        assert other_config.agent_version_id not in (
            first.agent_version_id,
            other_key.agent_version_id,
        )
        first.refresh_from_db()
        assert first.agent_version_id == same.agent_version_id
        assert _side(first.agent_version_id).agent_payload[
            "requested_by_environments"
        ] == [str(first.id), str(same.id)]

    def test_other_targets_get_other_agents(self, client, user, workspace, gates):
        base = _job(_post(client, workspace, _vapi())).agent_version.agent_definition_id
        other_workspace = Workspace.objects.create(
            name="Other", organization=user.organization, created_by=user
        )
        retell = _body(
            "retell",
            mode="connect_only",
            config={"agent_id": "asst-1"},
            values={"RETELL_API_KEY": "val-vapi-1"},
            source=None,
        )
        retell_chat = copy.deepcopy(retell)
        retell_chat["agent"]["connector"] = "retell_chat"
        jobs = [
            _job(_post(client, other_workspace, _vapi())),
            _job(_post(client, workspace, retell)),
            _job(_post(client, workspace, retell_chat)),
            _job(_post(client, workspace, _vapi(phone_number="+14155550100"))),
            _job(_post(client, workspace, _vapi(phone_number="+14155550101"))),
        ]

        definitions = [job.agent_version.agent_definition_id for job in jobs]
        assert len({base, *definitions}) == 6
        chat = AgentDefinition.no_workspace_objects.get(id=definitions[2])
        assert (chat.provider, chat.agent_type) == ("retell", "text")


@pytest.mark.django_db(transaction=True)
def test_concurrent_creates_on_one_assistant_make_one_agent(user, workspace):
    """Two creations for one assistant at once still make one agent."""
    barrier = threading.Barrier(2)
    errors = []
    check = CreationCheck(report={}, required_variable_names=[])

    def create(key):
        try:
            secret_submitted = SubmittedCredentials(
                secret_set=service.CredentialSet(),
                provider_secrets={"VAPI_API_KEY": "val-vapi-1"},
            )
            payload = HarnessJobCreateSerializer(data=_vapi()).run_validation(_vapi())
            payload.pop("credential_values")
            barrier.wait(timeout=10)
            create_hosted_job(
                user.organization,
                payload,
                idempotency_key=key,
                workspace=workspace,
                on_create=partial(
                    ensure_environment_agent, submitted=secret_submitted, check=check
                ),
            )
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
        finally:
            connection.close()

    with override_settings(HARNESS_VERSIONING_ENABLED=True):
        threads = [threading.Thread(target=create, args=(k,)) for k in ("c1", "c2")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert not errors
    jobs = HostedHarnessJob.no_workspace_objects.filter(organization=user.organization)
    assert jobs.count() == 2
    assert len({job.agent_version.agent_definition_id for job in jobs}) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_same_key_creates_replay_one_job(user, workspace, gates):
    """A retry while the first create still runs replays it; credentials are
    checked once."""
    barrier = threading.Barrier(2)
    responses = []

    def slow_probe(*args, **kwargs):
        threading.Event().wait(0.5)
        return []

    gates.probe.side_effect = slow_probe

    def create():
        api = APIClient()
        api.force_authenticate(user=user)
        try:
            barrier.wait(timeout=10)
            responses.append(_post(api, workspace, _vapi(), key="same"))
        finally:
            connection.close()

    with override_settings(HARNESS_VERSIONING_ENABLED=True, HARNESS_PROVIDER="hosted"):
        threads = [threading.Thread(target=create) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert [response.status_code for response in responses] == [202, 202]
    assert len({response.json()["job"]["job_id"] for response in responses}) == 1
    assert gates.probe.call_count == 1
    assert HostedHarnessJob.no_workspace_objects.count() == 1
    assert _definitions(user.organization).count() == 1


class TestPerEnvironmentAgents:
    def test_each_phone_environment_has_its_own_agent(
        self, client, user, workspace, gates
    ):
        first = _job(_post(client, workspace, _phone()))
        second = _job(_post(client, workspace, _phone()))

        assert (
            first.agent_version.agent_definition_id
            != second.agent_version.agent_definition_id
        )
        definition = first.agent_version.agent_definition
        assert definition.description == "You are the clinic line."
        assert first.agent_version.description == "You are the clinic line."
        assert definition.contact_number == "+14155550100"

    def test_a_livekit_agent_keeps_its_name_and_never_a_url_credential(
        self, client, user, workspace, gates
    ):
        job = _job(_post(client, workspace, _livekit()))

        version = job.agent_version
        assert version.agent_definition.agent_type == "voice"
        row = ProviderCredentials.objects.get(agent_version=version)
        assert row.agent_name == "lk-agent"
        assert row.server_url == ""
        assert version.configuration_snapshot["livekit_agent_name"] == "lk-agent"
        runtime = _side(version.id)
        assert "LIVEKIT_URL" not in runtime.credential_names
        assert "LIVEKIT_URL" not in runtime.credential_fingerprints
        assert runtime.required_variable_names == [
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
        ]
        codes = {
            warning["code"] for warning in run_warnings._credential_warnings(runtime)
        }
        assert not codes & {"variable_missing", "provider_key_unavailable"}
        assert PlatformSecretResolver().resolve(job) == {
            "LIVEKIT_API_KEY": "val-lk-key",
            "LIVEKIT_API_SECRET": "val-lk-secret",
        }


class TestRequestedAgent:
    def test_an_environment_owned_agent_with_the_same_key_is_bound(
        self, client, user, workspace, gates
    ):
        first = _job(_post(client, workspace, _vapi()))
        definition_id = first.agent_version.agent_definition_id
        body = _vapi()
        body["metadata"] = {"agent_definition_id": str(definition_id)}

        job = _job(_post(client, workspace, body))

        assert job.agent_version.agent_definition_id == definition_id
        assert "requested_agent_definition_id" not in job.payload["metadata"]

    def test_a_customer_agent_is_not_bound_yet(
        self, client, user, workspace, gates, caplog
    ):
        customer = AgentDefinition.no_workspace_objects.create(
            organization=user.organization,
            workspace=workspace,
            agent_name="Customer",
            agent_type="voice",
            inbound=True,
            description="theirs",
            provider="vapi",
            assistant_id="asst-1",
        )
        customer.create_version(
            description="theirs", commit_message="theirs", status="active"
        )
        before = list(
            AgentVersion.objects.filter(agent_definition=customer).values_list(
                "id", "status"
            )
        )
        body = _vapi()
        body["metadata"] = {"agent_definition_id": str(customer.id)}

        with caplog.at_level(logging.INFO):
            job = _job(_post(client, workspace, body))

        assert job.agent_version.agent_definition_id != customer.id
        metadata = job.payload["metadata"]
        assert metadata["requested_agent_definition_id"] == str(customer.id)
        assert metadata["agent_definition_id"] != str(customer.id)
        assert "reason=connected_unavailable" in caplog.text
        customer.refresh_from_db()
        assert (customer.description, customer.assistant_id) == ("theirs", "asst-1")
        assert before == list(
            AgentVersion.objects.filter(agent_definition=customer).values_list(
                "id", "status"
            )
        )

    @pytest.mark.parametrize("case", ["other_workspace", "not_uuid", "modality"])
    def test_an_agent_that_cannot_be_bound_falls_through(
        self, client, user, workspace, gates, caplog, case
    ):
        other = Workspace.objects.create(
            name="Other", organization=user.organization, created_by=user
        )
        named = AgentDefinition.no_workspace_objects.create(
            organization=user.organization,
            workspace=other if case == "other_workspace" else workspace,
            agent_name="Named",
            agent_type="text" if case == "modality" else "voice",
            inbound=True,
            description="",
        )
        body = _vapi()
        body["metadata"] = {
            "agent_definition_id": "not-a-uuid" if case == "not_uuid" else str(named.id)
        }

        with caplog.at_level(logging.INFO):
            job = _job(_post(client, workspace, body))

        assert job.agent_version.agent_definition_id != named.id
        reason = "modality" if case == "modality" else "not_found"
        assert f"reason={reason}" in caplog.text


class TestStoredRefs:
    def test_a_staged_credential_file_lands_in_the_set(
        self, client, user, workspace, gates
    ):
        ref = _stored_secret(
            user.organization,
            "harness-google-adc-staged",
            '{"type":"service_account","k":"val-adc"}',
        )
        body = _body(values=VALUES, refs={"GOOGLE_APPLICATION_CREDENTIALS_JSON": ref})

        job = _job(_post(client, workspace, body))

        files = json.loads(_side(job.agent_version_id).get_credentials())["files"]
        assert [one["alias"] for one in files] == [
            "GOOGLE_APPLICATION_CREDENTIALS_JSON"
        ]
        refs = job.payload["agent"]["secret_refs"]
        assert (
            refs["GOOGLE_APPLICATION_CREDENTIALS_JSON"]["key"] == AGENT_VERSION_FILE_KEY
        )
        assert HostedHarnessSecret.objects.filter(
            name="harness-google-adc-staged"
        ).exists()

    def test_an_older_clients_vault_refs_are_read_into_the_set(
        self, client, user, workspace, gates
    ):
        ref = _stored_secret(user.organization, "harness-openai-old", "val-vault")

        job = _job(_post(client, workspace, _body(refs={"OPENAI_API_KEY": ref})))

        assert PlatformSecretResolver().resolve(job) == {"OPENAI_API_KEY": "val-vault"}
        assert job.payload["agent"]["secret_refs"] == {
            "OPENAI_API_KEY": agent_version_secret_ref("OPENAI_API_KEY")
        }

    def test_a_missing_stored_secret_creates_nothing(
        self, client, user, workspace, gates
    ):
        ref = {
            "manager": "platform-vault",
            "key": "harness-gone",
            "version": "1",
            "purpose": "target_provider",
        }
        counts = (_definitions(user.organization).count(), AgentVersion.objects.count())

        response = _post(client, workspace, _body(refs={"OPENAI_API_KEY": ref}))

        assert response.status_code == 422
        assert response.json()["error"] == "credential_missing"
        assert response.json()["name"] == "OPENAI_API_KEY"
        assert not HostedHarnessJob.no_workspace_objects.exists()
        assert counts == (
            _definitions(user.organization).count(),
            AgentVersion.objects.count(),
        )


class TestRefusals:
    def test_a_failed_check_creates_nothing(self, client, user, workspace, gates):
        gates.probe.return_value = [
            {
                "provider": "vapi",
                "label": "Vapi",
                "aliases": ["VAPI_API_KEY"],
                "ok": False,
                "message": "rejected val-vapi-1",
            }
        ]

        response = _post(client, workspace, _vapi())

        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "agent_preflight_failed"
        assert body["preflight_report"]["invalid"] == ["VAPI_API_KEY"]
        _no_values(json.dumps(body))
        assert not HostedHarnessJob.no_workspace_objects.exists()
        assert not _definitions(user.organization).exists()
        assert not ProviderCredentials.objects.exists()

    def test_a_failure_inside_the_creation_rolls_back_the_job(
        self, client, user, workspace, gates, caplog
    ):
        with (
            caplog.at_level(logging.WARNING),
            patch.object(
                service, "mirror_default_status", side_effect=RuntimeError("boom-msg")
            ),
        ):
            response = _post(client, workspace, _body(values=VALUES))

        assert response.status_code == 503
        assert "harness_create_failed" in caplog.text
        assert "error=RuntimeError" in caplog.text
        assert "boom-msg" not in caplog.text
        assert not HostedHarnessJob.no_workspace_objects.exists()
        assert not HostedHarnessConversation.no_workspace_objects.exists()
        assert not _definitions(user.organization).exists()
        assert not HarnessAgentVersionRuntime.no_workspace_objects.exists()

    def test_a_replay_after_the_workflow_failed_to_start_starts_it(
        self, client, user, workspace, gates
    ):
        gates.start.side_effect = RuntimeError("temporal down")
        failed = _post(client, workspace, _body(values=VALUES), key="start")
        assert failed.status_code == 503
        job = HostedHarnessJob.no_workspace_objects.get()
        assert job.agent_version_id is not None
        gates.start.side_effect = None
        gates.probe.reset_mock()

        again = _post(client, workspace, _body(values=VALUES), key="start")

        assert _job(again).id == job.id
        gates.start.assert_called()
        gates.probe.assert_not_called()
        assert HarnessAgentVersionRuntime.no_workspace_objects.count() == 1
        assert _definitions(user.organization).count() == 1

    def test_an_unclassified_source_scan_still_creates_the_agent(
        self, client, user, workspace, gates
    ):
        with patch(
            "simulate.services.harness_provider._validate_required_credential_files",
            return_value=MagicMock(),
        ):
            job = _job(_post(client, workspace, _body(values=VALUES)))

        report = _side(job.agent_version_id).preflight_report
        assert report["source_scan"] == {
            "detected_connectors": [],
            "required_files": [],
            "scanned_files": 0,
        }


class TestFlagOff:
    def test_values_are_stored_as_secrets_after_the_digest(
        self, client, user, workspace, gates, settings
    ):
        settings.HARNESS_VERSIONING_ENABLED = False

        job = _job(_post(client, workspace, _body(values=VALUES), key="off"))

        refs = job.payload["agent"]["secret_refs"]
        assert set(refs) == set(VALUES)
        assert all(ref["key"].startswith("harness-") for ref in refs.values())
        assert not any(
            ref["key"].startswith("harness-agent-version:") for ref in refs.values()
        )
        assert job.agent_version_id is None
        assert "agent_definition_owner" not in job.payload["metadata"]
        assert not _definitions(user.organization).exists()
        assert not HarnessAgentVersionRuntime.no_workspace_objects.exists()
        assert PlatformSecretResolver().resolve(job) == VALUES
        stored = HostedHarnessSecret.objects.count()

        again = _post(client, workspace, _body(values=VALUES), key="off")

        assert _job(again).id == job.id
        assert HostedHarnessSecret.objects.count() == stored


class TestSerializer:
    def _errors(self, body, serializer=HarnessJobCreateSerializer):
        found = serializer(data=body)
        assert not found.is_valid()
        _no_values(json.dumps(found.errors, default=str))
        return found.errors

    def test_values_stand_for_the_provider_credentials(self):
        valid = HarnessJobCreateSerializer(data=_vapi(phone_number="+14155550100"))
        assert valid.is_valid(), valid.errors

    @pytest.mark.parametrize(
        "values",
        [
            {"SIMULATOR_X": "val-db-1"},
            {"PATH": "val-db-1"},
            {"my-key": "val-db-1"},
            {"1ABC": "val-db-1"},
            {"OPENAI_API_KEY": ""},
            {"VAPI_API_KEY": 123},
            {f"NAME_{index}": "v" for index in range(101)},
            {"BIG": "x" * 60_000, **{f"B{i}": "x" * 60_000 for i in range(17)}},
        ],
    )
    def test_bad_values_are_refused_under_their_field(self, values):
        assert "credential_values" in self._errors(_body(values=values))

    @pytest.mark.parametrize("agent", [None, "vapi", ["vapi"]])
    def test_a_malformed_agent_is_a_400_not_a_500(self, agent):
        body = _body(values=VALUES)
        if agent is None:
            body.pop("agent")
        else:
            body["agent"] = agent
        assert "agent" in self._errors(body)

    def test_a_list_of_values_is_refused(self):
        body = _body()
        body["credential_values"] = ["val-db-1"]
        assert "credential_values" in self._errors(body)

    def test_other_refusals_still_hold(self):
        remote = _body(
            values=VALUES,
            source={"kind": "remote", "endpoint": "https://a.example.com"},
        )
        assert "agent" in self._errors(remote)
        overlap = _body(values=VALUES)
        overlap["source"]["environment_values"] = {"DB_URL": "plain"}
        assert self._errors(overlap)
        for name in (
            "agent_definition_owner",
            "build_agent_version_id",
            "requested_agent_definition_id",
        ):
            assert "metadata" in self._errors(_body(metadata={name: "x"}))


class TestPreflightParity:
    def _report(self, client, workspace, body):
        return client.post(
            PREFLIGHT_URL, body, format="json", HTTP_X_WORKSPACE_ID=str(workspace.id)
        )

    def _status(self, response, alias):
        for item in response.json()["credentials"]["requirements"]:
            if item["environment_name"] == alias:
                return item["status"]
        return None

    def test_a_typed_alias_is_configured_and_a_blank_one_is_not(
        self, client, workspace, gates
    ):
        typed = self._report(client, workspace, _vapi())
        assert typed.status_code == 200, typed.content
        assert self._status(typed, "VAPI_API_KEY") == "configured"
        response = self._report(client, workspace, _vapi(key=""))
        assert response.status_code == 200, response.content
        assert self._status(response, "VAPI_API_KEY") == "missing"

    @pytest.mark.parametrize(
        "values",
        [
            {"SIMULATOR_X": "v"},
            {"PATH": "v"},
            {"my-key": "v"},
            {f"NAME_{index}": "v" for index in range(101)},
            {"LONG": "x" * (CREDENTIAL_VALUE_MAX_CHARS + 1)},
        ],
    )
    def test_preflight_refuses_bad_values_too(self, values):
        found = HarnessPreflightSerializer(data=_body(values=values))
        assert not found.is_valid()
        assert "credential_values" in found.errors

    def test_preflight_takes_the_longest_value_create_takes(self):
        values = {"LONG": "x" * CREDENTIAL_VALUE_MAX_CHARS}
        for serializer in (HarnessPreflightSerializer, HarnessJobCreateSerializer):
            found = serializer(data=_body(values=values))
            assert found.is_valid(), list(found.errors)

    def test_preflight_refuses_values_over_one_mib(self):
        values = {f"B{i}": "x" * 11_000 for i in range(100)}
        found = HarnessPreflightSerializer(data=_body(values=values))
        assert not found.is_valid()
        assert found.errors["credential_values"] == [
            "environment values may not exceed 1 MiB"
        ]

    def test_real_refs_without_values_report_as_before(self, client, workspace, gates):
        ref = {
            "manager": "platform-vault",
            "key": "harness-vapi",
            "version": "1",
            "purpose": "target_provider",
        }
        body = _vapi()
        body.pop("credential_values")
        body["agent"]["secret_refs"] = {"VAPI_API_KEY": ref}
        response = self._report(client, workspace, body)
        assert response.status_code == 200
        assert self._status(response, "VAPI_API_KEY") == "configured"


class TestSandboxProvider:
    def test_values_are_stored_for_the_sandbox_server(
        self, client, user, workspace, settings
    ):
        settings.HARNESS_PROVIDER = "sandbox"
        with (
            patch(
                "simulate.services.harness_sandbox.HarnessSandboxClient.submit",
                return_value={"job_id": "x"},
            ) as submit,
            patch(
                "simulate.services.harness_sandbox.HarnessSandboxClient.preflight",
                return_value={},
            ) as preflight,
            patch(
                "simulate.services.harness_provider._sandbox_preflight_body",
                return_value={},
            ),
        ):
            created = _post(client, workspace, _body(values=VALUES))
            checked = client.post(
                PREFLIGHT_URL,
                _body(values=VALUES),
                format="json",
                HTTP_X_WORKSPACE_ID=str(workspace.id),
            )

        assert created.status_code == 202
        assert checked.status_code == 200
        for sent in (submit.call_args.args[0], preflight.call_args.args[0]):
            refs = sent["secret_refs"]
            assert set(refs) == set(VALUES)
            assert not any(
                ref["key"].startswith("harness-agent-version:") for ref in refs.values()
            )
            assert (
                PlatformSecretResolver().resolve(
                    SimpleNamespace(
                        organization=user.organization,
                        organization_id=user.organization_id,
                        agent_version_id=None,
                        environment_id=None,
                        payload={"agent": {"secret_refs": refs}},
                    )
                )
                == VALUES
            )


class TestSecretHygiene:
    def test_no_value_reaches_logs_bodies_or_reprs(
        self, client, user, workspace, gates, caplog
    ):
        with caplog.at_level(logging.DEBUG):
            response = _post(client, workspace, _vapi())
            job = _job(response)
            PlatformSecretResolver().resolve(job)
        _no_values(caplog.text)
        _no_values(json.dumps(response.json()))
        _no_values(json.dumps(serialize_job(job), default=str))
        masked = _mask_sensitive({"agent": {}, "credential_values": {"A": "val-db-1"}})
        _no_values(json.dumps(masked))
        submitted = submitted_credentials(
            user.organization, {}, {"VAPI_API_KEY": "val-vapi-1", "DB_URL": "val-db-1"}
        )
        _no_values(repr(submitted))
