"""Agent versions of an environment: credential sets, preflight, activation, defaults."""

import copy
import inspect
import json
import logging
import uuid
from unittest.mock import patch

import pytest

from simulate.models import (
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HostedHarnessJob,
    HostedHarnessSecret,
)
from simulate.models.agent_definition import ProviderCredentials
from simulate.services import harness_agent_versions as service
from simulate.services.harness_agent_versions import (
    CredentialFile,
    activate_agent_version,
    agent_version_secret_ref,
    create_agent_version,
    discard_agent_version,
    preflight_agent_version,
    retry_preflight,
)
from simulate.services.harness_versioning.backfill import backfill_environment
from simulate.services.harness_versioning.backfill_check import check_environment
from simulate.services.harness_versioning.versions import (
    move_default,
    record_authoring,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    provision_scenarios,
    register_attempt,
)
from simulate.services.hosted_harness_gateway import PlatformSecretResolver
from simulate.tasks import harness_agent_versions as tasks
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_provider import _v1_payload
from simulate.tests.test_harness_versioning_assembly import (
    _environment as _cached_environment,
)
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
    _definition,
    _legacy,
)
from simulate.tests.test_harness_versioning_backfill import _run as _backfill_run
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_picks import _ready, _second_agent
from simulate.tests.test_harness_versioning_record import _authoring

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
URL = "wss://lk.example.test"
OLD = {
    "LIVEKIT_URL": URL,
    "LIVEKIT_API_KEY": "val-lk-key-old",
    "LIVEKIT_API_SECRET": "val-lk-secret-old",
    "OPENAI_API_KEY": "val-openai-old",
    "GOOGLE_APPLICATION_CREDENTIALS_JSON": '{"type":"service_account","k":"val-adc"}',
}
SECRETS = (
    "val-lk-key-old",
    "val-lk-secret-old",
    "val-openai-old",
    "val-adc",
    "val-openai-new",
    "val-lk-key-new",
)
_REFS = {
    "OPENAI_API_KEY": "harness-openai_api_key",
    "GOOGLE_APPLICATION_CREDENTIALS_JSON": "harness-google-adc-agent",
}
# Another build of the same agent: its commit, installation and one variable differ.
Y_COMMIT = "c" * 40
Y_VALUES = {"WEBHOOK_TOKEN": "val-webhook-y"}


def _other_build(payload):
    payload["source"].update(
        commit_sha=Y_COMMIT, visibility="private", installation_id="inst-y"
    )
    refs = payload["agent"]["secret_refs"]
    refs.pop("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    refs["WEBHOOK_TOKEN"] = {
        "manager": "platform-vault",
        "key": "harness-webhook-y",
        "version": "1",
        "purpose": "target_provider",
    }


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


@pytest.fixture
def scheduled():
    with patch.object(tasks.preflight_harness_agent_version, "apply_async") as mocked:
        yield mocked


@pytest.fixture
def no_adoption(monkeypatch):
    def refuse(_version):
        raise AssertionError("legacy credentials must never be adopted")

    monkeypatch.setattr(
        "simulate.services.agent_definition._adopt_legacy_credentials", refuse
    )


def _store_secrets(organization, environment):
    for alias, ref in environment.payload["agent"]["secret_refs"].items():
        HostedHarnessSecret.objects.get_or_create(
            organization=organization,
            name=ref["key"],
            version=ref.get("version") or "1",
            defaults={"encrypted_value": {**OLD, **Y_VALUES}[alias]},
        )


def _environment(
    user, workspace, key, storage, *, definition=None, body=None, edit=None
):
    """Backfilled to A1 (legacy) with LiveKit keys, a variable and a credential file;
    ``edit`` changes the payload it was built with."""
    environment = _legacy(
        user, workspace, key, storage, definition=definition, body=body
    )
    payload = dict(environment.payload)
    agent = dict(payload["agent"])
    refs = dict(agent["secret_refs"])
    for alias, name in _REFS.items():
        refs[alias] = {
            "manager": "platform-vault",
            "key": name,
            "version": "1",
            "purpose": "target_provider",
        }
    agent["secret_refs"] = refs
    payload["agent"] = agent
    if edit is not None:
        payload = copy.deepcopy(payload)
        edit(payload)
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        payload=payload
    )
    environment.refresh_from_db()
    _store_secrets(user.organization, environment)
    backfill_environment(environment.id)
    environment.refresh_from_db()
    assert environment.agent_version_id is not None
    return environment


def _a1(environment):
    return AgentVersion.no_workspace_objects.get(id=environment.agent_version_id)


def _side(version):
    return HarnessAgentVersionRuntime.no_workspace_objects.get(agent_version=version)


def _blob(version):
    return json.loads(_side(version).get_credentials())


def _create(parent, environment, **kwargs):
    kwargs.setdefault("trigger", "credentials")
    return create_agent_version(
        parent.agent_definition, parent=parent, env=environment, **kwargs
    )


def _passing(**extra):
    return [
        patch(
            "simulate.services.harness_provider._preflight_source_connectors",
            return_value=([], [], 3),
        ),
        patch(
            "simulate.services.harness_provider._preflight_credential_probe",
            **({"return_value": []} | extra),
        ),
        patch(
            "simulate.services.hosted_harness_gateway.platform_dialer_status",
            return_value={"available": True, "missing": []},
        ),
    ]


def _preflight(version, patches=None):
    patches = _passing() if patches is None else patches
    for one in patches:
        one.start()
    try:
        return preflight_agent_version(version)
    finally:
        for one in patches:
            one.stop()


def _no_values(text):
    for value in SECRETS:
        assert value not in text


def _non_secret_fields(runtime):
    return json.dumps(
        {
            "agent_payload": runtime.agent_payload,
            "source": runtime.source,
            "fingerprints": runtime.credential_fingerprints,
            "names": runtime.credential_names,
            "summary": runtime.summary,
            "diff": runtime.diff,
            "report": runtime.preflight_report,
            "hash": runtime.hash,
        }
    )


class TestCreate:
    def test_a_legacy_parent_is_resolved_into_the_new_set(
        self,
        user,
        workspace,
        storage,
        scheduled,
        no_adoption,
        caplog,
        django_capture_on_commit_callbacks,
    ):
        caplog.set_level(logging.INFO)
        environment = _environment(user, workspace, "av-legacy", storage)
        a1 = _a1(environment)

        with django_capture_on_commit_callbacks(execute=True):
            a2 = _create(
                a1,
                environment,
                secret_values={"OPENAI_API_KEY": "val-openai-new"},
                user=user,
            )

        assert (a2.version_number, a2.status) == (a1.version_number + 1, "draft")
        assert a2.description == a1.description
        assert a2.commit_message == "Credentials changed: 1 variable changed"
        blob = _blob(a2)
        assert blob["env"] == {"OPENAI_API_KEY": "val-openai-new"}
        [adc] = blob["files"]
        assert adc["alias"] == "GOOGLE_APPLICATION_CREDENTIALS_JSON"
        runtime = _side(a2)
        assert runtime.preflight_status == "pending"
        assert runtime.trigger == "credentials"
        assert runtime.parent == _side(a1)
        assert runtime.credential_names == [
            "GOOGLE_APPLICATION_CREDENTIALS_JSON",
            "OPENAI_API_KEY",
        ]
        assert set(runtime.credential_fingerprints) == {
            *runtime.credential_names,
            "LIVEKIT_URL",
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
        }
        assert "legacy_secret_refs" not in runtime.agent_payload
        assert runtime.diff["variables"] == {
            "added": [],
            "removed": [],
            "changed": ["OPENAI_API_KEY"],
        }
        # Provider keys live in the version's own provider row, never the blob.
        creds = ProviderCredentials.objects.get(agent_version=a2)
        assert creds.provider_type == "livekit"
        assert creds.server_url == URL
        assert creds.get_api_key() == "val-lk-key-old"
        assert creds.get_api_secret() == "val-lk-secret-old"
        assert a2.configuration_snapshot["livekit_url"] == URL
        # Only the id crosses into Temporal.
        scheduled.assert_called_once()
        assert scheduled.call_args.kwargs["args"] == [str(a2.id)]
        assert scheduled.call_args.kwargs["task_id"] == (
            f"harness-agent-preflight-{a2.id}"
        )
        _no_values(_non_secret_fields(runtime))
        _no_values(json.dumps(a2.configuration_snapshot) + a2.commit_message)
        _no_values(caplog.text)
        _no_values(runtime.credential_blob)

    def test_the_definitions_legacy_provider_row_is_never_moved(
        self, user, workspace, storage, scheduled, no_adoption
    ):
        environment = _environment(user, workspace, "av-adopt", storage)
        a1 = _a1(environment)
        legacy = ProviderCredentials.objects.create(
            agent_definition=a1.agent_definition, provider_type="vapi", api_key="x"
        )

        a2 = _create(
            a1, environment, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )

        legacy.refresh_from_db()
        assert (legacy.agent_definition_id, legacy.agent_version_id) == (
            a1.agent_definition_id,
            None,
        )
        assert ProviderCredentials.objects.get(agent_version=a2).id != legacy.id

    def test_the_parents_provider_row_is_copied_and_a_key_change_is_its_own(
        self, user, workspace, storage, scheduled, no_adoption
    ):
        environment = _environment(user, workspace, "av-copy", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v2"}
        )
        a3 = _create(a2, environment, secret_values={"OPENAI_API_KEY": "v3"})
        a4 = _create(
            a3, environment, secret_values={"LIVEKIT_API_KEY": "val-lk-key-new"}
        )

        rows = {
            version.id: ProviderCredentials.objects.get(agent_version=version)
            for version in (a2, a3, a4)
        }
        assert len({row.id for row in rows.values()}) == 3
        assert rows[a3.id].get_api_key() == "val-lk-key-old"
        assert rows[a4.id].get_api_key() == "val-lk-key-new"
        assert rows[a4.id].get_api_secret() == "val-lk-secret-old"
        assert "LIVEKIT_API_KEY" not in _blob(a4)["env"]
        assert _blob(a4)["env"] == _blob(a3)["env"]
        assert _side(a4).hash != _side(a3).hash
        assert _side(a4).credential_set_hash == _side(a3).credential_set_hash
        assert _side(a4).diff["variables"]["changed"] == ["LIVEKIT_API_KEY"]

    def test_the_same_set_returns_the_existing_version(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-dedupe", storage)
        a1 = _a1(environment)
        a2 = _create(
            a1, environment, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )
        count = AgentVersion.no_workspace_objects.filter(
            agent_definition=a1.agent_definition
        ).count()

        again = _create(
            a2, environment, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )

        assert again.id == a2.id
        assert (
            AgentVersion.no_workspace_objects.filter(
                agent_definition=a1.agent_definition
            ).count()
            == count
        )

    def test_numbering_continues_after_a_discard(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-number", storage)
        a1 = _a1(environment)
        a2 = _create(
            a1, environment, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )
        discard_agent_version(a2)

        a3 = _create(
            a1, environment, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )

        assert a3.id != a2.id
        assert a3.version_number == a2.version_number + 1
        assert AgentVersion.no_workspace_objects.filter(id=a2.id).exists()

    def test_files_and_sets_over_the_caps_are_refused(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-caps", storage)
        a1 = _a1(environment)
        count = AgentVersion.no_workspace_objects.count()
        big = b"x" * (service.MAX_CREDENTIAL_FILE_BYTES + 1)
        near = b"x" * service.MAX_CREDENTIAL_FILE_BYTES

        for files in (
            [CredentialFile("BIG_JSON", big)],
            [CredentialFile("ONE_JSON", near), CredentialFile("TWO_JSON", near)],
        ):
            with pytest.raises(HostedHarnessError) as refused:
                _create(a1, environment, secret_files=files)
            assert (refused.value.code, refused.value.status_code) == (
                "credential_blob_too_large",
                413,
            )
        assert AgentVersion.no_workspace_objects.count() == count

    def test_numbers_skip_soft_deleted_versions(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-number-deleted", storage)
        a1 = _a1(environment)
        gone = AgentVersion.no_workspace_objects.create(
            agent_definition=a1.agent_definition,
            organization=a1.organization,
            workspace=a1.workspace,
            version_number=a1.version_number + 5,
            description="gone",
            commit_message="gone",
        )
        AgentVersion._base_manager.filter(id=gone.id).update(deleted=True)

        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})

        assert a2.version_number == gone.version_number + 1

    def test_a_legacy_parent_takes_provider_keys_from_the_refs_only(
        self, user, workspace, storage, scheduled, no_adoption
    ):
        environment = _environment(user, workspace, "av-own-row", storage)
        a1 = _a1(environment)
        ProviderCredentials.objects.create(
            agent_version=a1,
            provider_type="livekit",
            api_key="val-row-key",
            api_secret="val-row-secret",
            server_url="wss://row.example.test",
            agent_name="rider",
        )
        payload = copy.deepcopy(environment.payload)
        payload["agent"]["secret_refs"].pop("LIVEKIT_API_SECRET")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()

        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})

        row = ProviderCredentials.objects.get(agent_version=a2)
        assert (row.get_api_key(), row.server_url) == ("val-lk-key-old", URL)
        assert row.get_api_secret() in ("", None)
        assert row.agent_name == "rider"
        assert "LIVEKIT_API_SECRET" not in _side(a2).credential_fingerprints

    def test_refs_of_two_providers_keep_the_other_in_the_set(
        self, user, workspace, storage, scheduled, no_adoption
    ):
        environment = _environment(user, workspace, "av-two-families", storage)
        payload = copy.deepcopy(environment.payload)
        payload["agent"]["secret_refs"]["VAPI_API_KEY"] = {
            "manager": "platform-vault",
            "key": "harness-vapi",
            "version": "1",
            "purpose": "target_provider",
        }
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        HostedHarnessSecret.objects.create(
            organization=user.organization,
            name="harness-vapi",
            version="1",
            encrypted_value="val-vapi",
        )
        environment.refresh_from_db()

        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v2"}
        )

        assert _blob(a2)["env"] == {"OPENAI_API_KEY": "v2", "VAPI_API_KEY": "val-vapi"}
        row = ProviderCredentials.objects.get(agent_version=a2)
        assert (row.provider_type, row.get_api_key()) == ("livekit", "val-lk-key-old")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a2
        )
        environment.refresh_from_db()
        assert PlatformSecretResolver().resolve(environment)["VAPI_API_KEY"] == (
            "val-vapi"
        )

    def test_a_value_only_change_keeps_the_legacy_bundle_key(
        self, user, workspace, storage, scheduled
    ):
        from simulate.services.harness_versioning.assembly import bundle_key

        environment = _environment(user, workspace, "av-parity", storage)
        a1 = _a1(environment)
        a2 = _create(
            a1, environment, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )
        pair = (environment.environment_version, environment.suite_version)

        assert _side(a2).hash != _side(a1).hash
        assert bundle_key(_side(a2), *pair) == bundle_key(_side(a1), *pair)
        a3 = _create(a2, environment, secret_values={"NEW_NAME": "v"})
        assert bundle_key(_side(a3), *pair) != bundle_key(_side(a1), *pair)

    def test_a_shared_legacy_parent_takes_the_asking_environments_facts(
        self, user, workspace, storage, scheduled
    ):
        first, second = _shared(user, workspace, storage, "av-own-facts")
        a1 = _a1(first)
        payload = copy.deepcopy(second.payload)
        payload["source"].update(
            commit_sha="c" * 40, visibility="private", installation_id="inst-y"
        )
        payload["agent"]["config"] = {"voice": "y-voice", "api_key": "val-config-y"}
        HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(
            payload=payload
        )
        second.refresh_from_db()

        a2 = _create(a1, second, secret_values={"OPENAI_API_KEY": "v2"})

        shared, runtime = _side(a1), _side(a2)
        assert "commit_sha" not in shared.source
        assert runtime.source == {
            "repository": "acme/agent",
            "ref": "main",
            "commit_sha": "c" * 40,
        }
        assert runtime.source_fingerprint is None
        assert runtime.agent_payload["config"] == {
            "voice": "y-voice",
            "api_key": "[redacted]",
        }
        assert runtime.agent_payload["source_access"] == {
            "kind": "github",
            "visibility": "private",
            "installation_id": "inst-y",
        }
        # Compared with what this environment ran, so only the value changed.
        assert runtime.diff["source"] is False
        assert runtime.summary == "1 variable changed"
        assert "val-config-y" not in json.dumps(runtime.agent_payload)

    def test_a_shared_legacy_parent_takes_the_asking_environments_prompt(
        self, user, workspace, storage, scheduled
    ):
        first, second = _shared(user, workspace, storage, "av-own-prompt")
        a1 = _a1(first)
        payload = copy.deepcopy(second.payload)
        payload["agent"]["connector"] = "livekit"
        payload["agent"]["config"] = {"target_system_prompt": "Second's prompt"}
        HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(
            payload=payload
        )
        second.refresh_from_db()
        # The first environment's build wrote the shared version's description.
        AgentVersion.no_workspace_objects.filter(id=a1.id).update(
            description="First's prompt"
        )
        a1.refresh_from_db()

        own = _create(a1, second, secret_values={"OPENAI_API_KEY": "v2"})
        other = _create(a1, first, secret_values={"OPENAI_API_KEY": "v3"})

        assert own.description == "Second's prompt"
        assert other.description == "First's prompt"
        # The bundle key's prompt stays the legacy row's.
        assert _side(own).prompt_hash == _side(a1).prompt_hash

    def test_a_replaced_name_is_not_read_from_its_stored_secret(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-replaced", storage)
        a1 = _a1(environment)
        HostedHarnessSecret.objects.filter(
            organization=user.organization, name="harness-openai_api_key"
        ).delete()

        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "val-new"})

        assert _blob(a2)["env"] == {"OPENAI_API_KEY": "val-new"}
        assert _side(a2).diff["variables"]["changed"] == ["OPENAI_API_KEY"]
        # A name the request leaves as it was is still read.
        with pytest.raises(HostedHarnessError) as refused:
            _create(a1, environment, secret_values={"OTHER_KEY": "v"})
        assert (refused.value.code, refused.value.details) == (
            "credential_missing",
            {"name": "OPENAI_API_KEY"},
        )
        assert "OPENAI_API_KEY" in refused.value.message

    def test_a_version_checked_by_its_caller_is_asked_for_by_no_environment(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-unasked", storage)
        a1 = _a1(environment)

        a2 = _create(
            a1, environment, secret_values={"OPENAI_API_KEY": "v2"}, activate=False
        )

        assert _side(a2).agent_payload["requested_by_environments"] == []
        assert _side(a2).agent_payload["created_for_environment"] == str(environment.id)
        scheduled.assert_not_called()
        assert _preflight(a2).status == "passed"
        assert _defaults(environment) == [a1.id]
        assert _outcomes(a2) == {str(environment.id): "rerun_only"}

    def test_a_check_by_its_caller_records_that_it_moves_no_default(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-unasked-check", storage)
        a2 = _create(
            _a1(environment),
            environment,
            secret_values={"OPENAI_API_KEY": "v2"},
            activate=False,
        )
        patches = _passing()
        for one in patches:
            one.start()
        try:
            result = preflight_agent_version(a2, activate=False)
        finally:
            for one in patches:
                one.stop()

        assert result.status == "passed"
        # Recorded, so nothing waits on a default move that never comes.
        assert _outcomes(a2) == {str(environment.id): "rerun_only"}

    def test_redacted_config_takes_the_run_environments_own_value(
        self, user, workspace, storage, scheduled, caplog
    ):
        from simulate.services.harness_agent_versions import apply_agent_version

        caplog.set_level(logging.INFO)
        environment = _environment(user, workspace, "av-redacted", storage)
        payload = copy.deepcopy(environment.payload)
        payload["agent"]["config"] = {
            "voice": "alloy",
            "auth": {"api_key": "val-config-key"},
        }
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v2"}
        )
        assert _side(a2).agent_payload["config"] == {
            "voice": "alloy",
            "auth": {"api_key": "[redacted]"},
        }

        own = copy.deepcopy(environment.payload)
        apply_agent_version(own, _side(a2), environment_id=environment.id)
        other = copy.deepcopy(environment.payload)
        other["agent"]["config"] = {"auth": {"api_key": "val-config-other"}}
        apply_agent_version(other, _side(a2), environment_id=uuid.uuid4())
        bare = copy.deepcopy(environment.payload)
        bare["agent"]["config"] = {"voice": "alloy"}
        apply_agent_version(bare, _side(a2), environment_id=uuid.uuid4())

        assert own["agent"]["config"] == {
            "voice": "alloy",
            "auth": {"api_key": "val-config-key"},
        }
        assert other["agent"]["config"] == {
            "voice": "alloy",
            "auth": {"api_key": "val-config-other"},
        }
        assert bare["agent"]["config"] == {
            "voice": "alloy",
            "auth": {"api_key": "[redacted]"},
        }
        assert "harness_agent_version_config_redacted" in caplog.text
        assert "auth.api_key" in caplog.text
        assert "val-config" not in caplog.text

    def test_another_environments_pick_runs_its_own_redacted_values(
        self, user, workspace, storage, scheduled, settings
    ):
        from simulate.services.hosted_harness import (
            VersionPicks,
            create_selected_harness_run,
        )

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        first, second = _shared(user, workspace, storage, "av-redacted-pick")
        for environment, value in ((first, "val-config-a"), (second, "val-config-b")):
            payload = copy.deepcopy(environment.payload)
            payload["agent"]["config"] = {"auth": {"api_key": value}}
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
                payload=payload
            )
            environment.refresh_from_db()
        a2 = _create(_a1(first), first, secret_values={"OPENAI_API_KEY": "v2"})
        assert _preflight(a2).status == "passed"
        assert _defaults(first, second) == [a2.id, _a1(second).id]

        run, _ = create_selected_harness_run(
            second,
            scenario_keys=[_key(NAMES[0])],
            trials=1,
            idempotency_key="av-redacted-pick",
            picks=VersionPicks(agent_version_id=a2.id),
        )

        assert run.agent_version_id == a2.id
        assert run.payload["agent"]["config"] == {"auth": {"api_key": "val-config-b"}}
        assert "val-config-a" not in json.dumps(run.payload)

    @pytest.mark.parametrize("flag", [False, True])
    def test_an_unpinned_run_never_takes_another_environments_version(
        self, user, workspace, storage, scheduled, settings, flag
    ):
        from simulate.models import CallExecution, TestExecution
        from simulate.services.hosted_harness import create_selected_harness_run

        first, second = _shared(user, workspace, storage, f"av-unpinned-{flag}")
        a1 = _a1(first)
        v_x = service.change_credentials(first, secret_values={"OPENAI_API_KEY": "v2"})
        assert a1.agent_definition.latest_version.id == v_x.id
        settings.HARNESS_VERSIONING_ENABLED = flag
        if flag:
            # Not yet versioned: no environment or suite version to pin.
            HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(
                environment_version=None, suite_version=None
            )
            second.refresh_from_db()

        run, _ = create_selected_harness_run(
            second,
            scenario_keys=[_key(NAMES[0])],
            trials=1,
            idempotency_key=f"av-unpinned-{flag}",
        )

        assert "version_pins" not in run.payload["metadata"]
        execution = TestExecution.no_workspace_objects.get(
            execution_metadata__harness_job_id=str(run.id)
        )
        assert second.run_test.agent_version_id is None
        assert execution.agent_version_id == a1.id
        calls = CallExecution.no_workspace_objects.filter(test_execution=execution)
        assert calls.exists()
        assert set(calls.values_list("agent_version_id", flat=True)) == {a1.id}

    @pytest.mark.parametrize("flag", [False, True])
    def test_a_reprovision_that_flips_direction_is_the_next_unpinned_version(
        self, user, workspace, storage, scheduled, settings, flag
    ):
        from simulate.models import CallExecution, TestExecution
        from simulate.services.hosted_harness import (
            _record_target_agent_facts,
            create_selected_harness_run,
        )

        first, second = _shared(user, workspace, storage, f"av-flip-{flag}")
        service.change_credentials(first, secret_values={"OPENAI_API_KEY": "v2"})
        definition = _a1(first).agent_definition
        inbound = not definition.inbound
        payload = copy.deepcopy(second.payload)
        payload["agent"]["call_direction"] = "inbound" if inbound else "outbound"
        HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(
            payload=payload
        )
        second.refresh_from_db()
        before, prompt = definition.latest_version.id, definition.description

        _record_target_agent_facts(second, definition, {"agent_prompt": prompt})

        definition.refresh_from_db()
        assert definition.description == prompt
        build = definition.latest_version
        assert build.id != before
        assert build.configuration_snapshot["inbound"] is inbound
        settings.HARNESS_VERSIONING_ENABLED = flag
        if flag:
            HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(
                environment_version=None, suite_version=None
            )
            second.refresh_from_db()

        run, _ = create_selected_harness_run(
            second,
            scenario_keys=[_key(NAMES[0])],
            trials=1,
            idempotency_key=f"av-flip-{flag}",
        )

        execution = TestExecution.no_workspace_objects.get(
            execution_metadata__harness_job_id=str(run.id)
        )
        assert execution.agent_version_id == build.id
        calls = CallExecution.no_workspace_objects.filter(test_execution=execution)
        assert calls.exists()
        assert set(calls.values_list("agent_version_id", flat=True)) == {build.id}


class TestPreflight:
    def test_a_failure_moves_nothing(self, user, workspace, storage, scheduled):
        environment = _environment(user, workspace, "av-fail", storage)
        a1 = _a1(environment)
        a2 = _create(
            a1, environment, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )
        failing = [
            patch(
                "simulate.services.harness_provider._preflight_source_connectors",
                side_effect=HostedHarnessError(
                    "github_repository_not_found", "no such repository"
                ),
            ),
            patch(
                "simulate.services.harness_provider._preflight_credential_probe",
                return_value=[],
            ),
        ]

        result = _preflight(a2, failing)

        assert result.status == "failed"
        runtime = _side(a2)
        assert runtime.preflight_report["error"] == "github_repository_not_found"
        source = next(
            c for c in runtime.preflight_report["checks"] if c["id"] == "source"
        )
        assert source["status"] == "failed"
        environment.refresh_from_db()
        assert environment.agent_version_id == a1.id

    def test_a_failed_probe_names_its_aliases_only(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-probe", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v"}
        )
        probe = {
            "provider": "livekit",
            "label": "LiveKit",
            "aliases": ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"],
            "ok": False,
            "message": "rejected val-lk-key-old",
        }

        result = _preflight(a2, _passing(return_value=[probe]))

        assert result.status == "failed"
        report = _side(a2).preflight_report
        assert report["invalid"] == ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
        _no_values(json.dumps(report))

    def test_retry_checks_a_failed_version_again(
        self, user, workspace, storage, scheduled, django_capture_on_commit_callbacks
    ):
        environment = _environment(user, workspace, "av-retry", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v"}
        )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="failed"
        )
        scheduled.reset_mock()

        with django_capture_on_commit_callbacks(execute=True):
            retry_preflight(a2)

        assert _side(a2).preflight_status == "pending"
        assert scheduled.call_args.kwargs["args"] == [str(a2.id)]
        with patch.object(service, "activate_agent_version") as activate:
            assert _preflight(a2).status == "passed"
        activate.assert_called_once()

    @pytest.mark.parametrize("status", ["failed", "pending"])
    def test_a_retry_dates_the_check_from_its_restart(
        self, auth_client, user, workspace, storage, scheduled, status
    ):
        from datetime import timedelta

        from django.utils import timezone

        environment = _environment(user, workspace, f"av-retry-{status}", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v"}
        )
        assert _side(a2).check_started_at is not None
        old = timezone.now() - timedelta(hours=1)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status=status, check_started_at=old
        )

        retry_preflight(a2)

        started = _side(a2).check_started_at
        assert started > old
        listed = auth_client.get(f"{BASE}/{environment.id}/versions/").json()
        [item] = [one for one in listed["agent"] if one["id"] == str(a2.id)]
        assert item["check_started_at"] == started.isoformat()
        assert {one["check_started_at"] for one in listed["environment"]} == {None}

    def test_a_retry_that_repeats_a_live_version_names_it(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-retry-dup", storage)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="failed"
        )
        a3 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        assert a3.id != a2.id

        with pytest.raises(HostedHarnessError) as refused:
            retry_preflight(a2)

        assert (refused.value.code, refused.value.status_code) == (
            "agent_version_duplicate",
            409,
        )
        assert f"v{a3.version_number}" in refused.value.message
        assert refused.value.details["agent_version_id"] == str(a3.id)
        assert _side(a2).preflight_status == "failed"
        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/agent/{a2.id}/retry/"
        )
        assert response.status_code == 409, response.content
        assert response.json()["error"] == "agent_version_duplicate"

    def test_a_credentials_only_version_reuses_its_parents_source_scan(
        self, user, workspace, storage, scheduled
    ):
        from simulate.services.hosted_harness_gateway import HostedSourceAcquirer

        environment = _environment(user, workspace, "av-scan-reuse", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v2"}
        )
        gateway = "simulate.services.hosted_harness_gateway"
        acquire = patch.object(
            HostedSourceAcquirer, "acquire", return_value=(b"archive", "a" * 40)
        )
        detect = patch(
            f"{gateway}.detect_source_credentials", return_value=(["livekit"], [], 7)
        )
        probe = patch(
            "simulate.services.harness_provider._preflight_credential_probe",
            return_value=[],
        )

        with acquire as acquired, detect, probe:
            assert preflight_agent_version(a2, activate=False).status == "passed"
            assert acquired.call_count == 1
            a3 = _create(a2, environment, secret_values={"OPENAI_API_KEY": "v3"})
            assert preflight_agent_version(a3, activate=False).status == "passed"
            assert acquired.call_count == 1
            a4 = _create(
                a2,
                environment,
                source={**environment.payload["source"], "commit_sha": "d" * 40},
                trigger="push",
            )
            assert preflight_agent_version(a4, activate=False).status == "passed"
            assert acquired.call_count == 2

        scan = {
            "detected_connectors": ["livekit"],
            "required_files": [],
            "scanned_files": 7,
        }
        assert _side(a2).preflight_report["source_scan"] == scan
        assert _side(a3).preflight_report["source_scan"] == scan

    def test_a_passed_version_is_not_retried(self, user, workspace, storage):
        environment = _environment(user, workspace, "av-no-retry", storage)

        with pytest.raises(HostedHarnessError) as refused:
            retry_preflight(_a1(environment))

        assert refused.value.code == "version_not_retryable"

    def test_a_version_discarded_while_checked_stays_discarded(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-discarded", storage)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v"})
        discard_agent_version(a2)

        assert _preflight(a2).status == "discarded"
        environment.refresh_from_db()
        assert environment.agent_version_id == a1.id

    def test_a_superseded_version_does_not_move_back(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-superseded", storage)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        a3 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v3"})
        assert _preflight(a3).status == "passed"
        environment.refresh_from_db()
        assert environment.agent_version_id == a3.id

        assert _preflight(a2).status == "passed"

        environment.refresh_from_db()
        assert environment.agent_version_id == a3.id
        assert activate_agent_version(a2) == []
        assert _outcomes(a2) == {str(environment.id): "superseded"}

    def test_activation_is_skipped_without_a_runnable_pair(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-unprepared", storage)
        a1 = _a1(environment)
        # A new name changes the bundle key, and nothing is cached under the new one.
        a2 = _create(a1, environment, secret_values={"NEW_NAME": "v2"})

        assert _preflight(a2).status == "passed"

        environment.refresh_from_db()
        assert environment.agent_version_id == a1.id
        assert _outcomes(a2) == {str(environment.id): "rollback_unavailable"}

    def test_a_value_only_change_activates_onto_the_sealed_default_pair(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-rotate", storage)
        a1 = _a1(environment)
        before = environment.payload["metadata"]
        assert not HarnessAssembledBundle._base_manager.filter(
            job_id=environment.id, state=HarnessAssembledBundle.State.READY
        ).exists()
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})

        assert _preflight(a2).status == "passed"

        environment.refresh_from_db()
        assert environment.agent_version_id == a2.id
        assert _outcomes(a2) == {str(environment.id): "activated"}
        metadata = environment.payload["metadata"]
        assert metadata["authoring_object_key"] == before["authoring_object_key"]
        assert metadata["authoring_revision"] == before["authoring_revision"]
        assert metadata["default_pair_sealed"] is True
        name = f"{environment.environment_version_id}:{environment.suite_version_id}"
        assert metadata["default_pairs"][name]["agent_version_id"] == str(a2.id)

    def test_an_unexpected_error_fails_the_check_without_its_text(
        self, user, workspace, storage, scheduled, caplog
    ):
        caplog.set_level(logging.INFO)
        environment = _environment(user, workspace, "av-crash", storage)
        a1 = _a1(environment)
        a2 = _create(
            a1, environment, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )
        crashing = [
            patch(
                "simulate.services.harness_provider._preflight_source_connectors",
                side_effect=RuntimeError("boom val-openai-new"),
            )
        ]

        result = _preflight(a2, crashing)

        assert result.status == "failed"
        runtime = _side(a2)
        assert runtime.preflight_status == "failed"
        assert runtime.preflight_report["error"] == "preflight_error"
        assert "RuntimeError" in caplog.text
        _no_values(json.dumps(runtime.preflight_report) + caplog.text)
        assert _defaults(environment) == [a1.id]

    def test_a_private_source_is_cloned_with_the_installation_token(
        self, user, workspace, storage, scheduled, monkeypatch, caplog
    ):
        from contextlib import nullcontext
        from pathlib import Path
        from types import SimpleNamespace

        caplog.set_level(logging.INFO)
        environment = _environment(user, workspace, "av-private", storage)
        a1 = _a1(environment)
        payload = copy.deepcopy(environment.payload)
        payload["source"].update(visibility="private", installation_id="inst-42")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()
        # A row backfilled before source access was kept: the environment's is used.
        side = _side(a1)
        assert side.agent_payload["source_access"]["visibility"] == "public"
        side.agent_payload.pop("source_access")
        HarnessAgentVersionRuntime.no_workspace_objects.filter(id=side.id).update(
            agent_payload=side.agent_payload
        )
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        installations, headers = [], []

        def credential(installation_id):
            installations.append(installation_id)
            return nullcontext("installation-secret-token")

        def run(command, **kwargs):
            if kwargs.get("env", {}).get("GIT_CONFIG_VALUE_0"):
                headers.append(kwargs["env"]["GIT_CONFIG_VALUE_0"])
            if command[:2] == ["git", "init"]:
                path = Path(command[-1])
                path.mkdir(parents=True)
                (path / "agent.py").write_text("print('agent')", encoding="utf-8")
            if "rev-parse" in command:
                return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        gateway = "simulate.services.hosted_harness_gateway"
        monkeypatch.setattr(
            f"{gateway}.GitHubAppTokenProvider.from_settings",
            lambda: SimpleNamespace(credential=credential),
        )
        monkeypatch.setattr(f"{gateway}.subprocess.run", run)
        monkeypatch.setattr(f"{gateway}.shutil.which", lambda _: "/usr/bin/git")
        probe = patch(
            "simulate.services.harness_provider._preflight_credential_probe",
            return_value=[],
        )

        result = _preflight(a2, [probe])

        assert installations == ["inst-42"]
        assert headers == ["Authorization: Bearer installation-secret-token"] * 4
        source = next(c for c in result.report["checks"] if c["id"] == "source")
        assert source["status"] == "passed"
        assert _side(a2).agent_payload["source_access"] == {
            "kind": "github",
            "visibility": "private",
            "installation_id": "inst-42",
        }
        text = json.dumps(_side(a2).preflight_report) + caplog.text
        assert "installation-secret-token" not in text

    def test_a_remote_target_is_neither_scanned_nor_probed(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-remote", storage)
        a2 = _create(
            _a1(environment),
            environment,
            source={"kind": "remote", "endpoint": "https://agent.example.test/talk"},
            agent_payload={"connector": "livekit", "mode": "connect_only"},
            trigger="push",
        )
        assert _side(a2).source_kind == "livekit"
        refuse = patch(
            "simulate.services.harness_credential_probes.probe_all",
            side_effect=AssertionError("a remote target is never probed"),
        )

        with refuse:
            result = preflight_agent_version(a2, activate=False)

        assert result.status == "passed"
        checks = {c["id"]: c["status"] for c in result.report["checks"]}
        assert (checks["source"], checks["credentials_present"]) == (
            "skipped",
            "skipped",
        )
        assert result.report["invalid"] == []


def _shared(user, workspace, storage, key, count=2):
    """Environments of one agent, all backfilled onto the same legacy A1."""
    definition = _definition(user, workspace)
    _agent_version(definition)
    environments = [
        _environment(user, workspace, f"{key}-{n}", storage, definition=definition)
        for n in range(count)
    ]
    assert len({one.agent_version_id for one in environments}) == 1
    return environments


def _outcomes(version):
    return {
        entry["environment_id"]: entry["outcome"]
        for entry in _side(version).preflight_report.get("activation") or []
    }


def _defaults(*environments):
    for environment in environments:
        environment.refresh_from_db()
    return [environment.agent_version_id for environment in environments]


def _on_a2(user, workspace, storage, key, count=2):
    """Every environment moved from legacy A1 to A2: the first by its check, the
    others by asking for the same set."""
    environments = _shared(user, workspace, storage, key, count)
    a1 = _a1(environments[0])
    a2 = _create(a1, environments[0], secret_values={"OPENAI_API_KEY": "v2"})
    assert _preflight(a2).status == "passed"
    for environment in environments[1:]:
        assert (
            _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"}).id == a2.id
        )
    assert _defaults(*environments) == [a2.id] * count
    return environments, a1, a2


class TestActivation:
    def test_a_legacy_parent_moves_only_the_environment_that_asked(
        self, user, workspace, storage, scheduled, settings, caplog
    ):
        caplog.set_level(logging.INFO)
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        first, second = _shared(user, workspace, storage, "av-pass")
        a1 = _a1(first)
        a2 = _create(a1, first, secret_values={"OPENAI_API_KEY": "val-openai-new"})
        probe = patch(
            "simulate.services.harness_provider._preflight_credential_probe",
            return_value=[],
        )

        with probe as probed:
            result = _preflight(a2, [_passing()[0]])

        assert result.status == "passed"
        values = probed.call_args.args[0]["credential_values"]
        assert values["LIVEKIT_API_KEY"] == "val-lk-key-old"
        assert values["OPENAI_API_KEY"] == "val-openai-new"
        runtime = _side(a2)
        assert runtime.preflight_status == "passed"
        assert runtime.preflighted_at is not None
        assert runtime.preflight_report["error"] is None
        assert result.report["activation"] == [
            {"environment_id": str(first.id), "outcome": "activated"}
        ]
        _no_values(json.dumps(runtime.preflight_report))
        assert _defaults(first, second) == [a2.id, a1.id]
        first.refresh_from_db()
        # A value-only change keeps the sealed default pair.
        assert first.payload["metadata"]["default_pair_sealed"] is True
        assert "harness_agent_version_activated" in caplog.text
        _no_values(caplog.text)

    @pytest.mark.parametrize("first_lands", ["first", "second"])
    def test_a_second_change_builds_on_the_first_still_checking(
        self, user, workspace, storage, scheduled, first_lands
    ):
        [environment] = _shared(
            user, workspace, storage, f"av-two-changes-{first_lands}", count=1
        )
        first = service.change_credentials(
            environment, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )
        second = service.change_credentials(
            environment, secret_values={"LIVEKIT_API_KEY": "val-lk-key-new"}
        )

        assert _side(second).parent == _side(first)
        order = (first, second) if first_lands == "first" else (second, first)
        for version in order:
            assert _preflight(version).status == "passed"
        assert _defaults(environment) == [second.id]
        assert _blob(second)["env"]["OPENAI_API_KEY"] == "val-openai-new"
        row = ProviderCredentials.objects.get(agent_version=second)
        assert row.get_api_key() == "val-lk-key-new"

    def test_a_change_after_a_failed_one_builds_on_the_default(
        self, user, workspace, storage, scheduled
    ):
        [environment] = _shared(user, workspace, storage, "av-after-failed", count=1)
        a1 = _a1(environment)
        first = service.change_credentials(
            environment, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )
        probe = {
            "provider": "openai",
            "label": "OpenAI",
            "aliases": ["OPENAI_API_KEY"],
            "ok": False,
            "message": "rejected",
        }
        failing = _passing(return_value=[probe])
        assert _preflight(first, failing).status == "failed"

        second = service.change_credentials(
            environment, secret_values={"LIVEKIT_API_KEY": "val-lk-key-new"}
        )

        assert _side(second).parent == _side(a1)
        assert _blob(second)["env"]["OPENAI_API_KEY"] == "val-openai-old"

    def test_a_change_after_a_hand_move_builds_on_the_new_default(
        self, user, workspace, storage, scheduled
    ):
        [environment] = _shared(user, workspace, storage, "av-after-move", count=1)
        a1 = _a1(environment)
        a2 = service.change_credentials(
            environment, secret_values={"OPENAI_API_KEY": "val-openai-v2"}
        )
        assert _preflight(a2).status == "passed"
        pending = service.change_credentials(
            environment, secret_values={"OPENAI_API_KEY": "val-openai-foo"}
        )
        move_default(environment, "agent", a1.id, expected_default_id=a2.id)
        environment.refresh_from_db()

        second = service.change_credentials(
            environment, secret_values={"LIVEKIT_API_KEY": "val-lk-key-new"}
        )

        assert _side(second).parent == _side(a1)
        assert _preflight(second).status == "passed"
        assert _preflight(pending).status == "passed"
        assert _defaults(environment) == [second.id]
        assert _blob(second)["env"]["OPENAI_API_KEY"] == "val-openai-old"
        row = ProviderCredentials.objects.get(agent_version=second)
        assert row.get_api_key() == "val-lk-key-new"

    def test_a_reruns_check_activates_for_those_waiting(
        self, user, workspace, storage, scheduled, django_capture_on_commit_callbacks
    ):
        [environment] = _shared(user, workspace, storage, "av-rerun-waiting", count=1)
        a1 = _a1(environment)
        asked = service.change_credentials(
            environment, secret_values={"OPENAI_API_KEY": "v2"}
        )
        scheduled.reset_mock()
        # The asker's check was lost; a rerun with the same values checks it.
        rerun = _create(
            a1, environment, secret_values={"OPENAI_API_KEY": "v2"}, activate=False
        )
        assert rerun.id == asked.id
        patches = _passing()
        for one in patches:
            one.start()
        try:
            with django_capture_on_commit_callbacks(execute=True):
                assert preflight_agent_version(asked, activate=False).status == (
                    "passed"
                )
        finally:
            for one in patches:
                one.stop()

        scheduled.assert_called_once()
        assert scheduled.call_args.kwargs["args"] == [str(asked.id)]
        assert _defaults(environment) == [a1.id]
        assert _preflight(asked).status == "passed"
        assert _defaults(environment) == [asked.id]
        assert _outcomes(asked) == {str(environment.id): "activated"}

    def test_each_environment_changes_from_its_own_refs(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        first, second = _shared(user, workspace, storage, "av-own-refs")
        a1 = _a1(first)
        payload = copy.deepcopy(second.payload)
        payload["agent"]["secret_refs"]["OPENAI_API_KEY"]["key"] = "harness-openai-y"
        HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(
            payload=payload
        )
        HostedHarnessSecret.objects.create(
            organization=user.organization,
            name="harness-openai-y",
            version="1",
            encrypted_value="val-openai-y",
        )
        second.refresh_from_db()

        a2 = _create(a1, second, secret_values={"LIVEKIT_API_KEY": "val-lk-key-new"})

        assert _blob(a2)["env"] == {"OPENAI_API_KEY": "val-openai-y"}
        assert _preflight(a2).status == "passed"
        assert _defaults(first, second) == [a1.id, a2.id]
        assert _outcomes(a2) == {str(second.id): "activated"}

    def test_a_reused_passed_version_becomes_the_askers_default(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        (first, second), _, a2 = _on_a2(user, workspace, storage, "av-reuse")

        assert _outcomes(a2) == {
            str(first.id): "activated",
            str(second.id): "activated",
        }
        assert _side(a2).agent_payload["requested_by_environments"] == [
            str(first.id),
            str(second.id),
        ]

    def test_a_reused_version_is_made_default_over_a_newer_one(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-reuse-old", storage)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        a3 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v3"})
        assert _preflight(a3).status == "passed"
        assert _preflight(a2).status == "passed"
        assert _defaults(environment) == [a3.id]

        again = _create(
            a1, environment, secret_values={"OPENAI_API_KEY": "v2"}, user=user
        )

        assert again.id == a2.id
        assert _defaults(environment) == [a2.id]
        moved = environment.payload["metadata"][service.HAND_MOVE_KEY]
        assert (moved["by"], moved["to"]) == (str(user.id), str(a2.id))

    def test_resubmitting_the_defaults_values_stands_over_an_earlier_request(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-reuse-default", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v2"}
        )
        assert _preflight(a2).status == "passed"
        a3 = _create(a2, environment, secret_values={"OPENAI_API_KEY": "v3"})

        again = _create(
            a2, environment, secret_values={"OPENAI_API_KEY": "v2"}, user=user
        )
        assert again.id == a2.id
        environment.refresh_from_db()
        moved = environment.payload["metadata"][service.HAND_MOVE_KEY]
        assert (moved["by"], moved["to"]) == (str(user.id), str(a2.id))
        assert _preflight(a3).status == "passed"

        assert _defaults(environment) == [a2.id]
        assert _outcomes(a3) == {str(environment.id): "default_moved_by_hand"}

    def test_a_later_request_of_an_older_pending_version_wins(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-asked-later", storage)
        a1 = _a1(environment)
        b = _create(a1, environment, secret_values={"OPENAI_API_KEY": "b"})
        c = _create(a1, environment, secret_values={"OPENAI_API_KEY": "c"})
        assert _preflight(c).status == "passed"
        assert _defaults(environment) == [c.id]

        # Asked for again from c: the same set as b, still checked.
        assert _create(c, environment, secret_values={"OPENAI_API_KEY": "b"}).id == (
            b.id
        )
        assert _preflight(b).status == "passed"

        assert _defaults(environment) == [b.id]
        assert _outcomes(b) == {str(environment.id): "activated"}

    def test_the_asking_environment_takes_its_newer_version_over_an_older_one(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-newer", storage)
        a1 = _a1(environment)
        v1 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v1"})
        v2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})

        assert _preflight(v1).status == "passed"
        assert _defaults(environment) == [v1.id]
        assert _preflight(v2).status == "passed"

        assert _defaults(environment) == [v2.id]
        assert _outcomes(v2) == {str(environment.id): "activated"}

    def test_an_environment_that_joined_a_pending_version_takes_it(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        first, second = _shared(user, workspace, storage, "av-joined")
        a1 = _a1(first)
        a2 = _create(a1, second, secret_values={"OPENAI_API_KEY": "v2"})
        assert _preflight(a2).status == "passed"
        assert _defaults(first, second) == [a1.id, a2.id]
        pending = _create(a1, first, secret_values={"OPENAI_API_KEY": "v3"})

        assert _create(a2, second, secret_values={"OPENAI_API_KEY": "v3"}).id == (
            pending.id
        )
        assert _preflight(pending).status == "passed"

        assert _defaults(first, second) == [pending.id, pending.id]
        assert _outcomes(pending) == {
            str(first.id): "activated",
            str(second.id): "activated",
        }
        # Asked for here too, so its lineage reaches its own legacy version.
        assert service.agent_pick_allowed(second, a1.id, _side(a1))

    def test_only_the_environment_that_asked_moves_off_a_shared_parent(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        (first, second), a1, a2 = _on_a2(user, workspace, storage, "av-lineage")
        [left] = _shared(user, workspace, storage, "av-lineage-left", count=1)
        a3 = _create(a2, first, secret_values={"OPENAI_API_KEY": "v3"})

        assert _preflight(a3).status == "passed"

        assert _defaults(first, second, left) == [a3.id, a2.id, _a1(left).id]
        assert _outcomes(a3) == {
            str(second.id): "not_requested",
            str(first.id): "activated",
        }

        # Asking for the same set later moves it too.
        assert _create(a2, second, secret_values={"OPENAI_API_KEY": "v3"}).id == a3.id
        assert _defaults(first, second) == [a3.id, a3.id]
        assert _outcomes(a3)[str(second.id)] == "activated"

    def test_a_pending_version_asked_for_again_is_checked(
        self, user, workspace, storage, scheduled, django_capture_on_commit_callbacks
    ):
        first, second = _shared(user, workspace, storage, "av-join-pending")
        a1 = _a1(first)
        with django_capture_on_commit_callbacks(execute=True):
            a2 = _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"})
        with django_capture_on_commit_callbacks(execute=True):
            again = _create(a1, second, secret_values={"OPENAI_API_KEY": "v2"})

        assert again.id == a2.id
        assert [call.kwargs["args"] for call in scheduled.call_args_list] == [
            [str(a2.id)],
            [str(a2.id)],
        ]
        assert _side(a2).agent_payload["requested_by_environments"] == [
            str(first.id),
            str(second.id),
        ]

    def test_a_check_that_lost_the_race_still_activates(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-lost-race", storage)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        report = {"checks": [], "missing": [], "invalid": [], "error": None}

        def raced(*_args):
            # Another run of the check passes the version first.
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version=a2
            ).update(preflight_status="passed", preflight_report=report)
            return True, report

        with patch.object(service, "_run_preflight", side_effect=raced):
            result = preflight_agent_version(a2)

        assert result.status == "passed"
        assert _defaults(environment) == [a2.id]
        assert _outcomes(a2) == {str(environment.id): "activated"}

    def test_a_version_the_environment_cannot_pick_is_not_made_its_default(
        self, user, workspace, storage, scheduled, settings
    ):
        from accounts.models.workspace import Workspace

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-not-bound", storage)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        elsewhere = Workspace.objects.create(
            name="Elsewhere",
            organization=user.organization,
            is_active=True,
            created_by=user,
        )
        AgentVersion.no_workspace_objects.filter(id=a2.id).update(workspace=elsewhere)

        assert _preflight(a2).status == "passed"

        assert _defaults(environment) == [a1.id]
        assert _outcomes(a2) == {str(environment.id): "not_bound"}

    def test_a_rerun_version_passing_on_retry_moves_no_default(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        (first, second), _a1_, a2 = _on_a2(user, workspace, storage, "av-rerun")
        a3 = _create(a2, first, secret_values={"OPENAI_API_KEY": "v3"}, activate=False)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a3).update(
            preflight_status="failed"
        )

        retry_preflight(a3)

        assert _preflight(a3).status == "passed"
        assert _defaults(first, second) == [a2.id, a2.id]
        assert _outcomes(a3) == {
            str(first.id): "rerun_only",
            str(second.id): "rerun_only",
        }

    def test_a_storage_error_is_retried_after_the_others_move(
        self, user, workspace, storage, scheduled, settings
    ):
        from minio.error import S3Error

        from simulate.services.harness_versioning import versions

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        (first, second), _, a2 = _on_a2(user, workspace, storage, "av-s3")
        a3 = _create(a2, first, secret_values={"OPENAI_API_KEY": "v3"})
        assert _create(a2, second, secret_values={"OPENAI_API_KEY": "v3"}).id == a3.id
        real = versions.move_agent

        def flaky(locked, version, **kwargs):
            if locked.id == first.id:
                raise S3Error(None, "InternalError", "down", "k", "r", "h")
            return real(locked, version, **kwargs)

        with patch.object(versions, "move_agent", side_effect=flaky):
            with pytest.raises(S3Error):
                _preflight(a3)

        assert _side(a3).preflight_status == "passed"
        assert _defaults(first, second) == [a2.id, a3.id]
        assert _outcomes(a3) == {str(first.id): "failed", str(second.id): "activated"}

        # The activity runs again: the version stays passed and the rest moves.
        assert _preflight(a3).status == "passed"

        assert _defaults(first, second) == [a3.id, a3.id]
        assert _outcomes(a3) == {
            str(first.id): "activated",
            str(second.id): "already_default",
        }

    def test_the_activity_checks_and_activates_by_id(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-activity", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v2"}
        )
        patches = [
            *_passing(),
            # A test runs inside a transaction a connection reset would end.
            patch.object(tasks, "close_old_connections"),
            patch("tfc.temporal.drop_in.decorator.close_old_connections"),
        ]
        for one in patches:
            one.start()
        try:
            status = tasks.preflight_harness_agent_version(str(a2.id))
            missing = tasks.preflight_harness_agent_version(str(uuid.uuid4()))
        finally:
            for one in patches:
                one.stop()

        assert (status, missing) == ("passed", "missing")
        assert _defaults(environment) == [a2.id]
        assert _outcomes(a2) == {str(environment.id): "activated"}


def _diverged(user, workspace, storage, key, *, body=None):
    """Two environments on one shared legacy A1; the second was built at another
    commit, through another installation, with another variable."""
    definition = _definition(user, workspace)
    _agent_version(definition)
    first = _environment(
        user, workspace, f"{key}-x", storage, definition=definition, body=body
    )
    second = _environment(
        user,
        workspace,
        f"{key}-y",
        storage,
        definition=definition,
        body=body,
        edit=_other_build,
    )
    assert first.agent_version_id == second.agent_version_id
    return first, second


class TestSharedLegacyRow:
    """A legacy row records only the first environment's facts; every environment
    pinned to it reads its own."""

    def test_a_value_only_change_runs_on_the_environments_own_facts(
        self, user, workspace, storage, scheduled
    ):
        first, second = _diverged(user, workspace, storage, "av-diverged")
        a1 = _a1(first)
        assert "commit_sha" not in _side(a1).source

        v_y = service.change_credentials(
            second, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )

        runtime = _side(v_y)
        assert runtime.source["commit_sha"] == Y_COMMIT
        assert runtime.agent_payload["source_access"] == {
            "kind": "github",
            "visibility": "private",
            "installation_id": "inst-y",
        }
        assert "WEBHOOK_TOKEN" in runtime.credential_names
        assert "GOOGLE_APPLICATION_CREDENTIALS_JSON" not in runtime.credential_names
        assert _blob(v_y)["env"] == {
            "OPENAI_API_KEY": "val-openai-new",
            "WEBHOOK_TOKEN": "val-webhook-y",
        }
        assert runtime.diff["variables"] == {
            "added": [],
            "removed": [],
            "changed": ["OPENAI_API_KEY"],
        }
        assert _preflight(v_y).status == "passed"
        assert _defaults(first, second) == [a1.id, v_y.id]
        assert _outcomes(v_y) == {str(second.id): "activated"}

    def test_each_environment_lists_its_own_variables(
        self, auth_client, user, workspace, storage
    ):
        first, second = _diverged(user, workspace, storage, "av-diverged-list")

        def names(environment):
            response = auth_client.get(f"{BASE}/{environment.id}/versions/")
            assert response.status_code == 200, response.content
            [default] = [one for one in response.json()["agent"] if one["is_default"]]
            return set(default["credential_names"])

        assert {"GOOGLE_APPLICATION_CREDENTIALS_JSON", "OPENAI_API_KEY"} <= names(first)
        assert "WEBHOOK_TOKEN" not in names(first)
        assert {"WEBHOOK_TOKEN", "OPENAI_API_KEY"} <= names(second)
        assert "GOOGLE_APPLICATION_CREDENTIALS_JSON" not in names(second)

    def test_another_environments_version_does_not_replay_its_default_pair(
        self, user, workspace, storage, scheduled
    ):
        from simulate.services.harness_versioning.assembly import bundle_key
        from simulate.services.harness_versioning.backfill import effective_runtime
        from simulate.services.hosted_harness import (
            VersionPicks,
            replays_default_pair,
            resolve_run_pins,
        )

        first, second = _diverged(user, workspace, storage, "av-diverged-pick")
        a1 = _a1(first)
        v_x = service.change_credentials(
            first, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )
        assert _preflight(v_x).status == "passed"
        assert _defaults(first, second) == [v_x.id, a1.id]

        def pair(environment):
            return environment.environment_version_id, environment.suite_version_id

        assert replays_default_pair(first, a1.id, *pair(first))
        assert not replays_default_pair(second, v_x.id, *pair(second))
        with pytest.raises(HostedHarnessError) as refused:
            resolve_run_pins(second, picks=VersionPicks(agent_version_id=v_x.id))
        assert refused.value.code == "version_not_runnable"
        versions = (second.environment_version, second.suite_version)
        assert bundle_key(_side(v_x), *versions) == bundle_key(
            effective_runtime(first, _side(a1)), *versions
        )
        assert bundle_key(_side(v_x), *versions) != bundle_key(
            effective_runtime(second, _side(a1)), *versions
        )

    def test_a_compare_reads_the_shared_row_as_the_environments_own(
        self, auth_client, user, workspace, storage, scheduled
    ):
        first, second = _diverged(user, workspace, storage, "av-diverged-diff")
        a1 = _a1(first)
        v_y = service.change_credentials(
            second, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )

        response = auth_client.get(
            f"{BASE}/{second.id}/versions/agent/{v_y.id}/diff/",
            {"against": str(a1.id)},
        )

        assert response.status_code == 200, response.content
        diff = response.json()["diff"]
        assert Y_COMMIT not in json.dumps(diff.get("source") or {})
        assert diff["variables"]["removed"] == []
        assert "GOOGLE_APPLICATION_CREDENTIALS_JSON" not in json.dumps(diff)

    def test_one_change_through_two_installations_makes_two_versions(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        first, second = _shared(user, workspace, storage, "av-installations")
        for environment, installation in ((first, "inst-x"), (second, "inst-y")):
            payload = copy.deepcopy(environment.payload)
            payload["source"].update(visibility="private", installation_id=installation)
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
                payload=payload
            )
            environment.refresh_from_db()
        a1 = _a1(first)

        v_x = _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"})
        v_y = _create(a1, second, secret_values={"OPENAI_API_KEY": "v2"})

        assert v_x.id != v_y.id
        assert _side(v_y).agent_payload["source_access"]["installation_id"] == "inst-y"
        assert _side(v_x).agent_payload["requested_by_environments"] == [str(first.id)]
        assert _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"}).id == v_x.id

    def test_a_default_without_a_side_row_changes_from_the_environments_agent(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-rowless", storage)
        a1 = _a1(environment)
        bare = _agent_version(
            a1.agent_definition, status="draft", description=a1.description
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=bare
        )
        environment.refresh_from_db()

        version = service.change_credentials(
            environment, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )

        parent = _side(bare)
        assert parent.preflight_status == "legacy"
        runtime = _side(version)
        assert runtime.parent == parent
        assert runtime.source == _side(a1).source
        assert (
            runtime.agent_payload["connector"]
            == environment.payload["agent"]["connector"]
        )
        blob = _blob(version)
        assert blob["env"] == {"OPENAI_API_KEY": "val-openai-new"}
        assert [one["alias"] for one in blob["files"]] == [
            "GOOGLE_APPLICATION_CREDENTIALS_JSON"
        ]
        provider = ProviderCredentials.objects.get(agent_version=version)
        assert provider.provider_type == "livekit"
        assert _preflight(version).status == "passed"
        assert _defaults(environment) == [version.id]

    def test_a_hand_move_made_while_a_check_ran_stands(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-hand-move", storage)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        assert _preflight(a2).status == "passed"
        assert _defaults(environment) == [a2.id]
        a3 = _create(a2, environment, secret_values={"OPENAI_API_KEY": "v3"})
        move_default(environment, "agent", a1.id, expected_default_id=a2.id)

        assert _preflight(a3).status == "passed"

        assert _defaults(environment) == [a1.id]
        assert _outcomes(a3) == {str(environment.id): "default_moved_by_hand"}

    def test_a_hand_move_back_to_an_earlier_request_stands(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-hand-back", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "2"}
        )
        assert _preflight(a2).status == "passed"
        a3 = _create(a2, environment, secret_values={"OPENAI_API_KEY": "v3"})
        assert _preflight(a3).status == "passed"
        a4 = _create(a3, environment, secret_values={"OPENAI_API_KEY": "v4"})
        move_default(environment, "agent", a2.id, expected_default_id=a3.id, user=user)

        assert _preflight(a4).status == "passed"

        assert _defaults(environment) == [a2.id]
        assert _outcomes(a4) == {str(environment.id): "default_moved_by_hand"}
        moved = environment.payload["metadata"][service.HAND_MOVE_KEY]
        assert (moved["by"], moved["to"]) == (str(user.id), str(a2.id))

    def test_a_retried_activation_never_undoes_a_later_hand_move(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-hand-retry", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "2"}
        )
        assert _preflight(a2).status == "passed"
        a3 = _create(a2, environment, secret_values={"OPENAI_API_KEY": "v3"})
        assert _preflight(a3).status == "passed"
        move_default(environment, "agent", a2.id, expected_default_id=a3.id)

        # The activity runs again, as after another environment's transient error.
        assert _preflight(a3).status == "passed"

        assert _defaults(environment) == [a2.id]
        assert _outcomes(a3) == {str(environment.id): "default_moved_by_hand"}

    def test_requests_made_from_one_default_both_activate_in_turn(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "av-siblings", storage)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        a3 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v3"})

        assert _preflight(a2).status == "passed"
        assert _preflight(a3).status == "passed"

        assert _defaults(environment) == [a3.id]
        assert _outcomes(a3) == {str(environment.id): "activated"}

    def test_the_identity_lookup_locks_the_row_it_finds(
        self, user, workspace, storage, scheduled
    ):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        first, second = _shared(user, workspace, storage, "av-dedupe-lock")
        a1 = _a1(first)
        a2 = _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"})

        with CaptureQueriesContext(connection) as queries:
            again = _create(a1, second, secret_values={"OPENAI_API_KEY": "v2"})

        assert again.id == a2.id
        assert any(
            '"simulate_harness_agent_version_runtime"."hash" =' in query["sql"]
            and "FOR UPDATE OF" in query["sql"]
            for query in queries.captured_queries
        )

    def test_a_new_row_repeating_a_live_identity_is_a_conflict(
        self, user, workspace, storage, scheduled
    ):
        from django.db import IntegrityError

        environment = _environment(user, workspace, "av-retry-race", storage)
        a1 = _a1(environment)
        numbers = AgentVersion._base_manager.filter(
            agent_definition_id=a1.agent_definition_id
        ).count()

        def repeated(*args, **kwargs):
            raise IntegrityError("uniq_harness_agent_runtime_hash")

        with patch.object(HarnessAgentVersionRuntime, "save", repeated):
            with pytest.raises(HostedHarnessError) as refused:
                _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})

        assert (refused.value.code, refused.value.status_code) == (
            "agent_version_duplicate",
            409,
        )
        assert (
            AgentVersion._base_manager.filter(
                agent_definition_id=a1.agent_definition_id
            ).count()
            == numbers
        )


class TestDiscard:
    def test_a_version_another_environment_waits_for_is_kept(
        self, user, workspace, storage, scheduled
    ):
        first, second = _shared(user, workspace, storage, "av-discard-shared")
        a1 = _a1(first)
        a2 = _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"})
        assert _create(a1, second, secret_values={"OPENAI_API_KEY": "v2"}).id == a2.id

        with pytest.raises(HostedHarnessError) as refused:
            discard_agent_version(a2, by_environment=first.id)

        assert (refused.value.code, refused.value.status_code) == (
            "version_requested_elsewhere",
            422,
        )
        assert _side(a2).preflight_status == "pending"
        alone = _create(a1, first, secret_values={"OPENAI_API_KEY": "v3"})
        discard_agent_version(alone, by_environment=first.id)
        assert _side(alone).preflight_status == "discarded"

    def test_a_discard_saves_its_status_and_decision_once(
        self, user, workspace, storage, scheduled
    ):
        first, second = _shared(user, workspace, storage, "av-discard-once")
        a2 = _create(_a1(first), first, secret_values={"OPENAI_API_KEY": "v2"})
        real = HarnessAgentVersionRuntime.save
        with patch.object(
            HarnessAgentVersionRuntime, "save", autospec=True, side_effect=real
        ) as save:
            discard_agent_version(a2, by_environment=first.id)
            discard_agent_version(a2, by_environment=first.id)
            discard_agent_version(a2, by_environment=second.id)

        assert [call.kwargs["update_fields"] for call in save.call_args_list] == [
            ["preflight_status", "agent_payload", "updated_at"],
            ["agent_payload", "updated_at"],
        ]
        assert _side(a2).agent_payload["discarded_by"] == [
            str(first.id),
            str(second.id),
        ]

    def test_an_environments_default_cannot_be_discarded(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "av-in-use", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "2"}
        )
        assert _preflight(a2).status == "passed"

        with pytest.raises(HostedHarnessError) as refused:
            discard_agent_version(a2)

        assert (refused.value.code, refused.value.status_code) == (
            "agent_version_in_use",
            422,
        )
        assert _side(a2).preflight_status == "passed"

    def test_a_shared_legacy_version_is_never_discarded(
        self, user, workspace, storage, scheduled, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        first, second = _shared(user, workspace, storage, "av-discard-legacy")
        a1 = _a1(first)
        for environment, value in ((first, "vx"), (second, "vy")):
            mine = _create(a1, environment, secret_values={"OPENAI_API_KEY": value})
            assert _preflight(mine).status == "passed"
        assert a1.id not in _defaults(first, second)

        for by_environment in (first.id, None):
            with pytest.raises(HostedHarnessError) as refused:
                discard_agent_version(a1, by_environment=by_environment)
            assert (refused.value.code, refused.value.status_code) == (
                "version_shared",
                422,
            )

        assert _side(a1).preflight_status == "legacy"
        # The other environment can still roll back to it.
        move_default(
            second, "agent", a1.id, expected_default_id=second.agent_version_id
        )
        assert _defaults(second) == [a1.id]

    @pytest.mark.parametrize("state", ["completed", "failed", "canceled"])
    def test_a_requester_that_cannot_be_moved_any_more_does_not_hold_it(
        self, user, workspace, storage, scheduled, settings, state
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        first, second = _shared(user, workspace, storage, "av-discard-stale")
        a1 = _a1(first)
        a2 = _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"})
        assert _create(a1, second, secret_values={"OPENAI_API_KEY": "v2"}).id == a2.id
        HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(
            state=HostedHarnessJob.State.RUNNING
        )
        assert _preflight(a2).status == "passed"
        assert _outcomes(a2)[str(second.id)] == "environment_busy"
        move_default(first, "agent", a1.id, expected_default_id=a2.id)

        with pytest.raises(HostedHarnessError) as refused:
            discard_agent_version(a2, by_environment=first.id)
        assert refused.value.code == "version_requested_elsewhere"

        # Its build finished or stopped: nothing will move it to the version.
        HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(state=state)
        discard_agent_version(a2, by_environment=first.id)
        assert _side(a2).preflight_status == "discarded"

    def test_a_deleted_or_long_failed_requester_does_not_hold_it(
        self, user, workspace, storage, scheduled
    ):
        from datetime import timedelta

        from django.utils import timezone

        first, second, third = _shared(
            user, workspace, storage, "av-discard-gone", count=3
        )
        a1 = _a1(first)
        a2 = _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"})
        for environment in (second, third):
            assert (
                _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"}).id
                == a2.id
            )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="passed",
            preflighted_at=timezone.now(),
            preflight_report={
                "activation": [
                    {"environment_id": str(first.id), "outcome": "not_bound"},
                    {"environment_id": str(second.id), "outcome": "failed"},
                    {"environment_id": str(third.id), "outcome": "environment_busy"},
                ]
            },
        )
        HostedHarnessJob.no_workspace_objects.filter(id=third.id).update(
            state=HostedHarnessJob.State.RUNNING
        )

        with pytest.raises(HostedHarnessError) as refused:
            discard_agent_version(a2, by_environment=first.id)
        assert refused.value.code == "version_requested_elsewhere"

        # Deleted while busy, it is never moved; the other is still retried.
        HostedHarnessJob.no_workspace_objects.filter(id=third.id).update(deleted=True)
        with pytest.raises(HostedHarnessError) as refused:
            discard_agent_version(a2, by_environment=first.id)
        assert refused.value.code == "version_requested_elsewhere"

        # Its retries ran out long ago.
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflighted_at=timezone.now() - timedelta(hours=2)
        )
        discard_agent_version(a2, by_environment=first.id)
        assert _side(a2).preflight_status == "discarded"

    def test_the_api_discards_and_lists(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-api-discard", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v2"}
        )

        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/agent/{a2.id}/discard/"
        )

        assert response.status_code == 200, response.content
        item = next(one for one in response.json()["agent"] if one["id"] == str(a2.id))
        assert (item["status"], item["preflight_status"]) == ("discarded", "discarded")
        assert AgentVersion.no_workspace_objects.filter(id=a2.id).exists()
        refused = auth_client.post(
            f"{BASE}/{environment.id}/versions/agent/{environment.agent_version_id}"
            "/discard/"
        )
        assert refused.status_code == 422
        assert refused.json()["error"] == "version_shared"

    def test_another_agents_version_is_not_found(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, "av-api-other", storage)
        other = _agent_version(_definition(user, workspace, name="Other"))

        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/agent/{other.id}/retry/"
        )

        assert response.status_code == 404
        assert response.json()["error"] == "version_not_found"

    def test_the_api_retries_with_202_and_the_id_only(
        self,
        auth_client,
        user,
        workspace,
        storage,
        scheduled,
        django_capture_on_commit_callbacks,
    ):
        environment = _environment(user, workspace, "av-api-retry", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v2"}
        )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="failed", preflight_report={"error": "x"}
        )
        scheduled.reset_mock()

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.post(
                f"{BASE}/{environment.id}/versions/agent/{a2.id}/retry/"
            )

        assert response.status_code == 202, response.content
        item = next(one for one in response.json()["agent"] if one["id"] == str(a2.id))
        assert item["preflight_status"] == "pending"
        assert scheduled.call_args.kwargs["args"] == [str(a2.id)]


class TestScopedActions:
    def _post(self, auth_client, environment, version, action):
        return auth_client.post(
            f"{BASE}/{environment.id}/versions/agent/{version.id}/{action}/"
        )

    def test_another_environments_change_is_refused(
        self, auth_client, user, workspace, storage, scheduled
    ):
        first, second = _shared(user, workspace, storage, "av-scoped")
        a2 = _create(_a1(first), first, secret_values={"OPENAI_API_KEY": "v2"})
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="failed"
        )

        retried = self._post(auth_client, second, a2, "retry")
        discarded = self._post(auth_client, second, a2, "discard")
        listed = auth_client.get(f"{BASE}/{second.id}/versions/").json()["agent"]
        own = auth_client.get(f"{BASE}/{first.id}/versions/").json()["agent"]

        for response in (retried, discarded):
            assert response.status_code == 422, response.content
            assert response.json()["error"] == "version_not_yours"
        assert _side(a2).preflight_status == "failed"
        flags = {one["id"]: one["requested_here"] for one in listed}
        assert flags[str(a2.id)] is False
        assert {one["id"]: one["requested_here"] for one in own}[str(a2.id)] is True
        assert self._post(auth_client, first, a2, "retry").status_code == 202

    def test_the_default_lineage_is_the_environments_own(
        self, auth_client, user, workspace, storage, scheduled
    ):
        first, second = _shared(user, workspace, storage, "av-lineage")
        a1 = _a1(first)
        a2 = _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"})
        a3 = _create(a2, first, secret_values={"OPENAI_API_KEY": "v3"})
        # Its checks move the first environment along, so it waits for neither.
        assert _preflight(a2).status == "passed"
        assert _preflight(a3).status == "passed"
        assert _defaults(first) == [a3.id]
        HostedHarnessJob.no_workspace_objects.filter(id=second.id).update(
            agent_version=a3
        )

        retried = self._post(auth_client, second, a2, "retry")
        discarded = self._post(auth_client, second, a2, "discard")
        in_use = self._post(auth_client, second, a3, "discard")
        listed = auth_client.get(f"{BASE}/{second.id}/versions/").json()["agent"]

        # Its lineage stops at the version the first environment made: what that
        # descends from is the first environment's.
        for response in (retried, discarded):
            assert response.status_code == 422, response.content
            assert response.json()["error"] == "version_not_yours"
        assert _side(a2).preflight_status == "passed"
        assert in_use.json()["error"] == "agent_version_in_use"
        second.refresh_from_db()
        assert not service.agent_pick_allowed(second, a1.id, _side(a1))
        assert {one["id"]: one["pickable"] for one in listed}[str(a1.id)] is False

    def test_discard_is_offered_only_where_the_server_takes_it(
        self, auth_client, user, workspace, storage, scheduled
    ):
        first, second = _shared(user, workspace, storage, "av-discardable")
        a1 = _a1(first)
        a2 = _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"})
        assert _create(a1, second, secret_values={"OPENAI_API_KEY": "v2"}).id == a2.id
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="failed"
        )

        def flags(environment):
            listed = auth_client.get(f"{BASE}/{environment.id}/versions/").json()
            return {one["id"]: one["discardable"] for one in listed["agent"]}

        # Joined, not made, here: the server keeps it for the first environment.
        assert flags(second)[str(a2.id)] is False
        assert self._post(auth_client, second, a2, "discard").json()["error"] == (
            "version_shared"
        )
        assert flags(first) == {str(a1.id): False, str(a2.id): True}
        assert self._post(auth_client, first, a2, "discard").status_code == 200


class TestVersionsList:
    def test_only_the_default_or_a_passed_version_is_pickable(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-pickable", storage)
        a1 = _a1(environment)
        bare = _agent_version(a1.agent_definition, description=a1.description)
        passed = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        pending = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v3"})
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version=passed
        ).update(preflight_status="passed")
        listed = auth_client.get(f"{BASE}/{environment.id}/versions/").json()
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=passed
        )
        moved = auth_client.get(f"{BASE}/{environment.id}/versions/").json()

        def flags(body):
            return {one["id"]: one["pickable"] for one in body["agent"]}

        assert {
            str(one.id): flags(listed)[str(one.id)]
            for one in (a1, bare, passed, pending)
        } == {
            str(a1.id): True,
            str(bare.id): False,
            str(passed.id): True,
            str(pending.id): False,
        }
        # The default's own pre-change legacy version still runs as itself; an
        # older legacy version outside its lineage doesn't.
        older = _agent_version(a1.agent_definition, description="older")
        service.legacy_side_row(environment, older)
        moved = auth_client.get(f"{BASE}/{environment.id}/versions/").json()
        assert flags(moved)[str(a1.id)] is True
        assert flags(moved)[str(passed.id)] is True
        assert flags(moved)[str(older.id)] is False
        assert flags(moved)[str(bare.id)] is False
        assert {one["pickable"] for one in listed["environment"]} == {None}

    def test_agent_items_carry_the_report_but_no_value(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-list", storage)
        a2 = _create(
            _a1(environment),
            environment,
            secret_values={"OPENAI_API_KEY": "val-openai-new"},
        )
        _preflight(a2)

        response = auth_client.get(f"{BASE}/{environment.id}/versions/")

        assert response.status_code == 200
        _no_values(response.content.decode())
        item = next(one for one in response.json()["agent"] if one["id"] == str(a2.id))
        assert item["preflight_report"]["error"] is None
        assert item["preflighted_at"] is not None
        legacy = next(
            one
            for one in response.json()["agent"]
            if one["id"] == str(environment.agent_version_id)
        )
        assert (legacy["preflight_report"], legacy["preflighted_at"]) == (None, None)
        suite = response.json()["suite"][0]
        assert (suite["preflight_report"], suite["preflighted_at"]) == (None, None)


class TestResolver:
    def _today(self, environment):
        return {
            alias: OLD[alias] for alias in environment.payload["agent"]["secret_refs"]
        }

    def test_a_legacy_pin_resolves_exactly_as_before(self, user, workspace, storage):
        environment = _environment(user, workspace, "av-res-legacy", storage)
        assert _side(_a1(environment)).credential_blob is None

        assert PlatformSecretResolver().resolve(environment) == self._today(environment)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=None
        )
        environment.refresh_from_db()
        assert PlatformSecretResolver().resolve(environment) == self._today(environment)

    def test_a_pinned_version_reads_its_set_and_provider_row(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-res-pinned", storage)
        a2 = _create(
            _a1(environment),
            environment,
            secret_values={
                "OPENAI_API_KEY": "val-openai-new",
                "LIVEKIT_API_KEY": "val-lk-key-new",
            },
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a2
        )
        environment.refresh_from_db()

        resolved = PlatformSecretResolver().resolve(environment)

        assert resolved == {
            **self._today(environment),
            "OPENAI_API_KEY": "val-openai-new",
            "LIVEKIT_API_KEY": "val-lk-key-new",
        }

    def test_without_versioning_an_environment_reads_its_stored_refs(
        self, user, workspace, storage, scheduled, settings
    ):
        environment = _environment(user, workspace, "av-res-off", storage)
        a2 = _create(
            _a1(environment),
            environment,
            secret_values={"OPENAI_API_KEY": "val-openai-new"},
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a2
        )
        environment.refresh_from_db()
        settings.HARNESS_VERSIONING_ENABLED = False

        # As its unpinned runs read them, and as step 8 did.
        assert PlatformSecretResolver().resolve(environment) == self._today(environment)

    def test_names_only_refs_resolve_from_the_set(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-res-names", storage)
        a2 = _create(
            _a1(environment), environment, secret_values={"OPENAI_API_KEY": "v2"}
        )
        payload = dict(environment.payload)
        payload["agent"] = {
            **payload["agent"],
            "secret_refs": {
                "OPENAI_API_KEY": agent_version_secret_ref("OPENAI_API_KEY")
            },
        }
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a2, payload=payload
        )
        environment.refresh_from_db()

        assert PlatformSecretResolver().resolve(environment) == {"OPENAI_API_KEY": "v2"}

        payload["agent"]["secret_refs"]["MISSING_KEY"] = agent_version_secret_ref(
            "MISSING_KEY"
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()
        with pytest.raises(HostedHarnessError) as refused:
            PlatformSecretResolver().resolve(environment)
        assert refused.value.code == "credential_unavailable"

    def test_an_unreadable_set_is_unavailable_without_its_value(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-res-broken", storage)
        a2 = _create(
            _a1(environment),
            environment,
            secret_values={"OPENAI_API_KEY": "val-openai-new"},
        )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            credential_blob="enc::not-a-token"
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a2
        )
        environment.refresh_from_db()

        with pytest.raises(HostedHarnessError) as refused:
            PlatformSecretResolver().resolve(environment)

        assert (refused.value.code, refused.value.status_code) == (
            "credential_unavailable",
            422,
        )
        _no_values(refused.value.message)
        assert refused.value.__cause__ is None


class TestAgentPin:
    def _build(self, user, workspace, key):
        environment, _ = create_hosted_job(
            user.organization,
            _v1_payload(scenario_count=1),
            idempotency_key=key,
            workspace=workspace,
        )
        attempt = register_attempt(
            environment.id, endpoint_base_url="https://harness.example.test"
        ).attempt
        provision_scenarios(
            attempt,
            {
                "operation": "provision",
                "name": "Suite",
                "modality": "voice",
                "agent_prompt": "Help riders book a ride.",
                "personas": [
                    {
                        "scenario_key": _key(NAMES[0]),
                        "name": NAMES[0],
                        "role": "customer",
                        "situation": "s",
                        "outcome": "o",
                        "persona": {"name": NAMES[0]},
                    }
                ],
            },
        )
        environment.refresh_from_db()
        return environment

    def test_a_fresh_build_is_pinned_at_provision(self, user, workspace):
        environment = self._build(user, workspace, "av-pin-fresh")

        assert environment.agent_version_id is not None
        version = _a1(environment)
        assert version.agent_definition_id == environment.run_test.agent_definition_id
        runtime = _side(version)
        assert (runtime.preflight_status, runtime.trigger) == ("legacy", "creation")
        assert runtime.credential_blob is None
        assert set(runtime.agent_payload["legacy_secret_refs"]) == {
            "LIVEKIT_URL",
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
        }

    def test_without_versioning_nothing_is_pinned_until_recorded(
        self, user, workspace, settings, storage
    ):
        settings.HARNESS_VERSIONING_ENABLED = False
        environment = self._build(user, workspace, "av-pin-off")
        assert environment.agent_version_id is None
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state=HostedHarnessJob.State.COMPLETED
        )
        environment.refresh_from_db()
        settings.HARNESS_VERSIONING_ENABLED = True

        record_authoring(environment, _authoring(names=NAMES[:1]), origin="authoring")

        environment.refresh_from_db()
        assert environment.agent_version_id is not None
        assert environment.environment_version.agent_version_id == (
            environment.agent_version_id
        )
        assert _side(_a1(environment)).preflight_status == "legacy"
        pairs = environment.payload["metadata"]["default_pairs"]
        assert {entry["agent_version_id"] for entry in pairs.values()} == {
            str(environment.agent_version_id)
        }

    def _unpinned(self, user, workspace, key, storage, definition):
        environment = _legacy(user, workspace, key, storage, definition=definition)
        assert environment.agent_version_id is None
        return environment

    def _fill(self, environment):
        from django.db import transaction

        with transaction.atomic():
            locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=environment.id
            )
            filled = service.fill_agent_pin(locked)
            locked.save(update_fields=["agent_version", "updated_at"])
        environment.refresh_from_db()
        return filled

    def test_a_concurrent_side_row_does_not_abort_the_pin(
        self, user, workspace, storage
    ):
        from simulate.services.harness_versioning import backfill

        definition = _definition(user, workspace)
        version = _agent_version(definition)
        environment = self._unpinned(
            user, workspace, "av-pin-race", storage, definition
        )
        real = backfill.side_row_plan

        def racing(models, job, candidate):
            _, mine = real(models, job, candidate)
            _, theirs = real(models, job, candidate)
            theirs.save()
            return None, mine

        with patch.object(backfill, "side_row_plan", side_effect=racing):
            assert self._fill(environment) is True

        assert environment.agent_version_id == version.id
        assert (
            HarnessAgentVersionRuntime._base_manager.filter(
                agent_version_id=version.id
            ).count()
            == 1
        )

    @pytest.mark.parametrize("checked", [False, True])
    def test_another_environments_credential_set_is_never_pinned(
        self, user, workspace, storage, scheduled, caplog, checked
    ):
        caplog.set_level(logging.INFO)
        definition = _definition(user, workspace)
        _agent_version(definition)
        first = _environment(
            user, workspace, f"av-pin-set-1-{checked}", storage, definition=definition
        )
        a1 = _a1(first)
        a2 = _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"})
        if checked:
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version=a2
            ).update(preflight_status="passed")
        # With no legacy version left to fall back on.
        AgentVersion._base_manager.filter(id=a1.id).update(deleted=True)
        environment = self._unpinned(
            user, workspace, f"av-pin-set-2-{checked}", storage, definition
        )

        assert self._fill(environment) is False

        assert environment.agent_version_id is None
        assert "harness_agent_pin_skipped" in caplog.text
        assert str(a2.id) in caplog.text
        # The backfill passes it over too, for its runs as well, and its check says so.
        run = _backfill_run(environment)
        assert run.agent_version_id is None
        report = check_environment(environment.id)
        assert report["facts"]["agent_source"] == "none"
        assert report["facts"]["agent_version_id"] is None
        assert report["facts"]["runs"]["agent_pins_to_fill"] == 0
        assert backfill_environment(environment.id).agent_pinned is False
        environment.refresh_from_db()
        run.refresh_from_db()
        assert (environment.agent_version_id, run.agent_version_id) == (None, None)

    def test_the_newest_legacy_version_stands_in_for_another_environments_set(
        self, user, workspace, storage, scheduled
    ):
        definition = _definition(user, workspace)
        _agent_version(definition)
        first = _environment(
            user, workspace, "av-pin-fallback-1", storage, definition=definition
        )
        a1 = _a1(first)
        a2 = _create(a1, first, secret_values={"OPENAI_API_KEY": "v2"})
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="passed"
        )
        AgentVersion._base_manager.filter(id=a1.id).update(status="archived")
        filled = self._unpinned(
            user, workspace, "av-pin-fallback-2", storage, definition
        )
        backfilled = self._unpinned(
            user, workspace, "av-pin-fallback-3", storage, definition
        )

        assert self._fill(filled) is True
        assert filled.agent_version_id == a1.id

        report = check_environment(backfilled.id)
        assert report["facts"]["agent_version_id"] == str(a1.id)
        assert backfill_environment(backfilled.id).agent_pinned is True
        backfilled.refresh_from_db()
        assert backfilled.agent_version_id == a1.id

    def test_a_legacy_version_of_a_shared_agent_is_pinned(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        _agent_version(definition)
        first = _environment(
            user, workspace, "av-pin-legacy-1", storage, definition=definition
        )
        environment = self._unpinned(
            user, workspace, "av-pin-legacy-2", storage, definition
        )

        assert self._fill(environment) is True

        assert environment.agent_version_id == first.agent_version_id


class TestErrorTrackerScrubbing:
    def test_locals_holding_values_are_named_for_the_scrubber(self):
        from simulate.services.harness_credentials import mint_secret_refs
        from simulate.services.harness_provider import (
            HostedHarnessProvider,
            _environment_agent_maker,
            _mint_sandbox_values,
            _preflight_credential_probe,
            _rerun_credentials_agent,
            _secret_ref_minter,
            run_preflight_checks,
        )
        from simulate.views.harness_job import HarnessJobViewSet
        from tfc.logging.sentry import _is_sensitive_key

        stored_values_view = inspect.unwrap(HarnessJobViewSet.secret_values)
        staged_files_view = inspect.unwrap(HarnessJobViewSet.secret_file_upload)
        holders = {
            service._decoded_set: (
                "secret_text",
                "secret_document",
                "secret_set",
                "secret_item",
            ),
            service._version_values: ("secret_set", "pinned_secrets"),
            service._resolve_refs: ("secret_set", "provider_secrets", "secret_value"),
            service.submitted_credentials: (
                "secret_values",
                "secret_set",
                "provider_secrets",
            ),
            _environment_agent_maker: ("secret_values", "secret_submitted"),
            _secret_ref_minter: ("secret_values",),
            _mint_sandbox_values: ("secret_values", "secret_value"),
            mint_secret_refs: ("secret_values", "secret_value"),
            service.create_agent_version: (
                "secret_values",
                "secret_files",
                "secret_set",
                "provider_secrets",
                "secret_blob",
            ),
            service.preflight_agent_version: ("secret_set", "provider_secrets"),
            service._run_preflight: ("secret_payload", "secret_probe", "secret_checks"),
            PlatformSecretResolver.resolve: (
                "pinned_secrets",
                "environment_secrets",
                "resolved_secrets",
            ),
            service.staged_credential_files: ("secret_files", "secret_value"),
            service.change_credentials: ("secret_values", "secret_files"),
            HarnessJobViewSet._agent_credentials: ("secret_values", "secret_files"),
            _rerun_credentials_agent: ("secret_values",),
            run_preflight_checks: (
                "secret_payload",
                "secret_readiness",
                "secret_probe",
                "secret_checks",
            ),
            _preflight_credential_probe: (
                "secret_payload",
                "submitted_secrets",
                "secret_values",
                "secret_value",
            ),
            HostedHarnessProvider.rerun_saved: ("environment_values", "secret_value"),
            HostedHarnessProvider.create: ("secret_values",),
            staged_files_view: ("secret_content", "secret_document"),
        }
        for function, names in holders.items():
            code = function.__code__
            assert set(names) <= {*code.co_varnames, *code.co_cellvars}, function
            assert all(_is_sensitive_key(name) for name in names), names
        for function in (HostedHarnessProvider.rerun_saved, stored_values_view):
            assert "value" not in function.__code__.co_varnames, function
        assert not {"content", "document"} & set(staged_files_view.__code__.co_varnames)
        # The frame that raises credential_unavailable holds no value.
        assert service.credential_set.__code__.co_varnames == ("runtime",)
        scrubbed = {"secret_set": "x", "pinned_secrets": {"LIVEKIT_URL": "u"}}
        from tfc.logging.sentry import _scrub

        assert _scrub(scrubbed) == {
            "secret_set": "[Filtered]",
            "pinned_secrets": "[Filtered]",
        }


class TestAgentDefaultMove:
    def _setup(self, user, workspace, storage, key):
        environment = _cached_environment(user, workspace, key, storage)
        return environment, _a1(environment), _second_agent(environment)

    def test_a_cached_bundle_reseals_the_default_pair(self, user, workspace, storage):
        environment, a1, a2 = self._setup(user, workspace, storage, "av-mv-cached")
        cached = HarnessAssembledBundle.no_workspace_objects.get(
            id=environment.assembled_bundle_id
        )
        row = _ready(
            environment,
            a2.id,
            environment.environment_version,
            environment.suite_version,
        )
        HarnessAssembledBundle._base_manager.filter(id=row.id).update(
            object_key=cached.object_key, sha256=cached.sha256
        )

        move_default(environment, "agent", a2.id, expected_default_id=a1.id)

        environment.refresh_from_db()
        assert environment.agent_version_id == a2.id
        metadata = environment.payload["metadata"]
        assert metadata["default_pair_sealed"] is True
        assert metadata["sealed_bundle_object_key"] == cached.object_key
        name = f"{environment.environment_version_id}:{environment.suite_version_id}"
        assert metadata["default_pairs"][name]["agent_version_id"] == str(a2.id)

    def test_with_certifying_on_the_pair_is_unsealed(
        self, user, workspace, storage, settings
    ):
        environment, a1, a2 = self._setup(user, workspace, storage, "av-mv-bare")
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        payload = dict(environment.payload)
        metadata = dict(payload["metadata"])
        metadata["default_pairs"] = {
            **metadata.get("default_pairs", {}),
            "older:pair": {"key": "k", "revision": "r", "sealed": True, "at": "0"},
        }
        payload["metadata"] = metadata
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()

        move_default(environment, "agent", a2.id, expected_default_id=a1.id)

        environment.refresh_from_db()
        metadata = environment.payload["metadata"]
        assert environment.agent_version_id == a2.id
        assert metadata["default_pair_sealed"] is False
        assert "sealed_bundle_object_key" not in metadata
        # An entry kept before agents were named is the outgoing agent's.
        assert metadata["default_pairs"]["older:pair"]["agent_version_id"] == str(a1.id)

    def test_without_a_bundle_or_certifying_nothing_moves(
        self, user, workspace, storage
    ):
        environment, a1, a2 = self._setup(user, workspace, storage, "av-mv-none")
        before = environment.payload

        with pytest.raises(HostedHarnessError) as refused:
            move_default(environment, "agent", a2.id, expected_default_id=a1.id)

        assert refused.value.code == "rollback_unavailable"
        environment.refresh_from_db()
        assert (environment.agent_version_id, environment.payload) == (a1.id, before)

    def test_versions_with_one_bundle_key_move_without_a_cached_bundle(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "av-mv-same-key", storage)
        a1 = _a1(environment)
        a2 = _create(a1, environment, secret_values={"OPENAI_API_KEY": "v2"})
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="passed"
        )
        assert not HarnessAssembledBundle._base_manager.filter(
            job_id=environment.id, state=HarnessAssembledBundle.State.READY
        ).exists()
        key = environment.payload["metadata"]["authoring_object_key"]
        name = f"{environment.environment_version_id}:{environment.suite_version_id}"

        for version, current in ((a2, a1), (a1, a2)):
            move_default(
                environment, "agent", version.id, expected_default_id=current.id
            )

            environment.refresh_from_db()
            metadata = environment.payload["metadata"]
            assert environment.agent_version_id == version.id
            assert metadata["authoring_object_key"] == key
            assert metadata["default_pair_sealed"] is True
            assert metadata["default_pairs"][name]["agent_version_id"] == str(
                version.id
            )

    def test_another_workspaces_versions_are_neither_listed_nor_pickable(
        self, user, workspace, storage
    ):
        from accounts.models.workspace import Workspace
        from simulate.services.harness_versioning.read import _agent_versions
        from simulate.services.hosted_harness import agent_version_pickable

        environment, a1, a2 = self._setup(user, workspace, storage, "av-mv-ws")
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=user.organization,
            is_active=True,
            created_by=user,
        )
        stray = _agent_version(a1.agent_definition)
        AgentVersion.no_workspace_objects.filter(id=stray.id).update(
            workspace=other_workspace
        )

        assert agent_version_pickable(environment, a2.id)
        assert not agent_version_pickable(environment, stray.id)
        listed = {version.id for version in _agent_versions(environment)}
        assert {a1.id, a2.id} <= listed
        assert stray.id not in listed
        # The environment's own pin stays listed wherever it lives.
        AgentVersion.no_workspace_objects.filter(id=a1.id).update(
            workspace=other_workspace
        )
        assert a1.id in {version.id for version in _agent_versions(environment)}

    def test_an_older_legacy_version_cannot_be_made_the_default(
        self, user, workspace, storage
    ):
        environment, a1, _ = self._setup(user, workspace, storage, "av-mv-older")
        older = _agent_version(a1.agent_definition, description="older")
        bare = _agent_version(a1.agent_definition, description="bare")
        service.legacy_side_row(environment, older)

        for version in (older, bare):
            with pytest.raises(HostedHarnessError) as refused:
                move_default(
                    environment, "agent", version.id, expected_default_id=a1.id
                )
            assert (
                refused.value.code,
                refused.value.message,
                refused.value.status_code,
            ) == (
                "version_not_pickable",
                "This older agent version can't be made the default",
                422,
            )
        environment.refresh_from_db()
        assert environment.agent_version_id == a1.id

    def test_unpickable_versions_are_refused(self, user, workspace, storage):
        environment, a1, a2 = self._setup(user, workspace, storage, "av-mv-pick")
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            preflight_status="pending"
        )
        other = _agent_version(_definition(user, workspace, name="Other"))

        for version_id in (a2.id, other.id, uuid.uuid4()):
            with pytest.raises(HostedHarnessError) as refused:
                move_default(
                    environment, "agent", version_id, expected_default_id=a1.id
                )
            assert (refused.value.code, refused.value.status_code) == (
                "version_not_pickable",
                422,
            )

    def test_the_api_moves_conflicts_and_repeats(
        self, auth_client, user, workspace, storage, settings
    ):
        environment, a1, a2 = self._setup(user, workspace, storage, "av-mv-api")
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        path = f"{BASE}/{environment.id}/versions/agent/{a2.id}/default/"

        stale = auth_client.post(
            path, {"expected_default_id": str(a2.id)}, format="json"
        )
        moved = auth_client.post(
            path, {"expected_default_id": str(a1.id)}, format="json"
        )
        environment.refresh_from_db()
        after = environment.payload
        again = auth_client.post(
            path, {"expected_default_id": str(a1.id)}, format="json"
        )

        assert stale.status_code == 409
        assert stale.json()["error"] == "version_conflict"
        assert moved.status_code == 200, moved.content
        default = next(one for one in moved.json()["agent"] if one["is_default"])
        assert default["id"] == str(a2.id)
        assert again.status_code == 200
        environment.refresh_from_db()
        assert environment.payload == after
