"""Runs of agent versions: run payloads, picks, warnings, credential changes, reruns."""

import json
import logging
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from simulate.models import (
    AgentVersion,
    CallExecution,
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HostedHarnessAttempt,
    HostedHarnessJob,
    HostedHarnessSecret,
)
from simulate.services.harness_agent_versions import (
    agent_version_secret_ref,
    preflight_agent_version,
)
from simulate.services.harness_provider import HostedHarnessProvider
from simulate.services.harness_versioning.assembly import bundle_key
from simulate.services.harness_versioning.run_warnings import preview_run
from simulate.services.hosted_harness import (
    HostedHarnessError,
    VersionPicks,
    create_selected_harness_run,
)
from simulate.services.hosted_harness_gateway import PlatformSecretResolver
from simulate.tests.test_harness_agent_versions import (
    OLD,
    SECRETS,
    URL,
    Y_COMMIT,
    _a1,
    _blob,
    _create,
    _diverged,
    _environment,
    _no_values,
    _non_secret_fields,
    _passing,
    _preflight,
    _side,
)
from simulate.tests.test_harness_agent_versions import (
    scheduled as scheduled,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_assembly import (
    _command,
    _launch,
    _sealed_authoring,
)
from simulate.tests.test_harness_versioning_assembly import (
    gateway as gateway,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_picks import _ready

pytestmark = pytest.mark.django_db

SECRET_VALUES = "/simulate/api/harness-jobs/secret-values/"
SECRET_FILES = "/simulate/api/harness-jobs/secret-files/"
COMMIT = "2" * 40
NAMES_ONLY = (
    "GOOGLE_APPLICATION_CREDENTIALS_JSON",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "LIVEKIT_URL",
    "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    settings.HARNESS_PUBLIC_BASE_URL = "https://harness.example.test"


def _cached(user, workspace, key, storage):
    """A1 (legacy) is the default; its sealed bundle is cached for A1 × E1 × S1."""
    environment = _environment(user, workspace, key, storage, body=_sealed_authoring())
    assert environment.assembled_bundle_id is not None
    return environment


def _check(version, patches=None):
    """Preflight without activating, so the environment's default stays."""
    patches = _passing() if patches is None else patches
    for one in patches:
        one.start()
    try:
        return preflight_agent_version(version, activate=False)
    finally:
        for one in patches:
            one.stop()


def _a2(environment, **values):
    """A2: A1's set with a new variable value, passed but not the default."""
    a2 = _create(
        _a1(environment),
        environment,
        secret_values=values or {"OPENAI_API_KEY": "val-openai-new"},
    )
    assert _check(a2).status == "passed"
    return a2


def _change(version, *, source=None, **agent_payload):
    """Edit a version's side row, then cache its bundle under its new key."""
    runtime = _side(version)
    if source is not None:
        runtime.source = source
    runtime.agent_payload = {**runtime.agent_payload, **agent_payload}
    runtime.save(update_fields=["source", "agent_payload", "updated_at"])
    return runtime


def _run(environment, picks=None, key="r"):
    run, created = create_selected_harness_run(
        environment,
        scenario_keys=[_key(NAMES[0])],
        trials=1,
        idempotency_key=key,
        picks=picks,
    )
    assert created
    run.refresh_from_db()
    return run


def _pair(environment):
    return environment.environment_version, environment.suite_version


def _codes(warnings):
    return [warning["code"] for warning in warnings]


class TestRunPayload:
    def test_a_legacy_pin_runs_the_environments_payload(self, user, workspace, storage):
        environment = _cached(user, workspace, "ar-legacy", storage)

        run = _run(environment)

        assert run.agent_version_id == _a1(environment).id
        assert run.payload["agent"] == environment.payload["agent"]
        assert run.payload["source"] == environment.payload["source"]

    def test_a_legacy_pin_keeps_the_definitions_prompt(self, user, workspace, storage):
        environment = _cached(user, workspace, "ar-legacy-prompt", storage)
        a1 = _a1(environment)
        assert a1.description != a1.agent_definition.description

        run = _run(environment)

        assert run.test_execution.agent_version_id == a1.id
        calls = CallExecution.no_workspace_objects.filter(
            test_execution=run.test_execution
        )
        assert {call.call_metadata["agent_prompt"] for call in calls} == {
            a1.agent_definition.description
        }

    def test_a_legacy_pin_reads_the_prompt_its_own_payload_configures(
        self, user, workspace, storage
    ):
        environment = _cached(user, workspace, "ar-legacy-own-prompt", storage)
        a1 = _a1(environment)
        payload = json.loads(json.dumps(environment.payload))
        payload["agent"]["connector"] = "livekit"
        payload["agent"].setdefault("config", {})[
            "target_system_prompt"
        ] = "This environment's prompt"
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()
        # Another environment on the same definition built last.
        a1.agent_definition.description = "Another environment's prompt"
        a1.agent_definition.save(update_fields=["description"])

        run = _run(environment)

        assert run.test_execution.agent_version_id == a1.id
        calls = CallExecution.no_workspace_objects.filter(
            test_execution=run.test_execution
        )
        assert {call.call_metadata["agent_prompt"] for call in calls} == {
            "This environment's prompt"
        }

    def test_without_versioning_the_definitions_prompt_is_kept(
        self, user, workspace, storage, settings
    ):
        environment = _cached(user, workspace, "ar-off-prompt", storage)
        a1 = _a1(environment)
        payload = json.loads(json.dumps(environment.payload))
        payload["agent"]["connector"] = "livekit"
        payload["agent"].setdefault("config", {})[
            "target_system_prompt"
        ] = "This environment's prompt"
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()
        environment.run_test.agent_version = a1
        environment.run_test.save(update_fields=["agent_version"])
        settings.HARNESS_VERSIONING_ENABLED = False

        run = _run(environment)

        # Not pinned by the run: step 8's prompt, though the run test names A1.
        assert run.agent_version_id is None
        assert run.test_execution.agent_version_id == a1.id
        calls = CallExecution.no_workspace_objects.filter(
            test_execution=run.test_execution
        )
        assert {call.call_metadata["agent_prompt"] for call in calls} == {
            a1.agent_definition.description
        }

    def test_the_defaults_own_pre_change_version_can_be_picked(
        self, user, workspace, storage, scheduled
    ):
        environment = _cached(user, workspace, "ar-lineage-pick", storage)
        a1 = _a1(environment)
        a2 = _a2(environment)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a2
        )
        environment.refresh_from_db()

        run = _run(environment, VersionPicks(agent_version_id=a1.id))

        assert run.agent_version_id == a1.id
        assert run.payload["agent"] == environment.payload["agent"]
        assert run.payload["source"] == environment.payload["source"]

    def test_a_version_with_its_own_set_runs_its_agent_by_names_only(
        self, user, workspace, storage, scheduled
    ):
        environment = _cached(user, workspace, "ar-own", storage)
        a2 = _a2(environment)
        _change(
            a2,
            source={"repository": "acme/agent", "ref": "main", "commit_sha": COMMIT},
            config={"tier": "two"},
        )
        _ready(environment, a2.id, *_pair(environment))
        AgentVersion.no_workspace_objects.filter(id=a2.id).update(
            description="Prompt two"
        )

        run = _run(environment, VersionPicks(agent_version_id=a2.id))

        assert run.agent_version_id == a2.id
        agent = run.payload["agent"]
        assert agent["secret_refs"] == {
            name: agent_version_secret_ref(name) for name in NAMES_ONLY
        }
        assert agent["config"] == {"tier": "two"}
        assert agent["connector"] == environment.payload["agent"]["connector"]
        assert run.payload["source"] == {
            "kind": "github",
            "repository": "acme/agent",
            "ref": "main",
            "commit_sha": COMMIT,
            "visibility": "public",
        }
        assert run.test_execution.agent_version_id == a2.id
        calls = CallExecution.no_workspace_objects.filter(
            test_execution=run.test_execution
        )
        assert {call.call_metadata["agent_prompt"] for call in calls} == {"Prompt two"}
        _no_values(json.dumps(run.payload) + run.request_digest)
        # The environment's own payload is untouched.
        environment.refresh_from_db()
        assert set(environment.payload["agent"]["secret_refs"]) == set(NAMES_ONLY)
        assert not any(
            ref["key"].startswith("harness-agent-version:")
            for ref in environment.payload["agent"]["secret_refs"].values()
        )

    def test_the_names_only_refs_resolve_from_the_version_at_launch(
        self, user, workspace, storage, scheduled
    ):
        environment = _cached(user, workspace, "ar-resolve", storage)
        a2 = _a2(
            environment,
            OPENAI_API_KEY="val-openai-new",
            LIVEKIT_API_KEY="val-lk-key-new",
        )
        _ready(environment, a2.id, *_pair(environment))
        run = _run(environment, VersionPicks(agent_version_id=a2.id))

        resolved = PlatformSecretResolver().resolve(run)

        assert run.payload["agent"]["config"] == environment.payload["agent"]["config"]

        assert resolved == {
            **OLD,
            "OPENAI_API_KEY": "val-openai-new",
            "LIVEKIT_API_KEY": "val-lk-key-new",
        }
        assert resolved["LIVEKIT_URL"] == URL

    def test_a_credential_file_ref_reads_as_a_file(self):
        from simulate.services.harness_credentials import is_credential_file_ref

        assert is_credential_file_ref(
            agent_version_secret_ref("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        )
        assert not is_credential_file_ref(agent_version_secret_ref("OPENAI_API_KEY"))


class TestSharedLegacyRuns:
    def test_each_environment_runs_its_own_build_and_change(
        self, user, workspace, storage, scheduled
    ):
        from simulate.services.harness_agent_versions import change_credentials

        first, second = _diverged(
            user, workspace, storage, "ar-diverged", body=_sealed_authoring()
        )
        before = _run(second, key="before")
        assert before.payload["source"] == second.payload["source"]
        assert before.payload["metadata"]["assembly"]["cached"] is True

        v_y = change_credentials(
            second, secret_values={"OPENAI_API_KEY": "val-openai-new"}
        )
        assert _preflight(v_y).status == "passed"
        second.refresh_from_db()
        assert second.agent_version_id == v_y.id != first.agent_version_id

        run = _run(second, key="after")

        assert run.agent_version_id == v_y.id
        assert run.payload["source"] == {
            "kind": "github",
            "repository": "acme/agent",
            "ref": "main",
            "commit_sha": Y_COMMIT,
            "visibility": "private",
            "installation_id": "inst-y",
        }
        assert set(run.payload["agent"]["secret_refs"]) == {
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
            "LIVEKIT_URL",
            "OPENAI_API_KEY",
            "WEBHOOK_TOKEN",
        }
        # A value-only change keys the same: it finds what this environment's build
        # sealed under its own facts.
        assert run.payload["metadata"]["assembly"]["cached"] is True
        assert run.payload["metadata"]["assembly"]["key"] == (
            before.payload["metadata"]["assembly"]["key"]
        )
        _no_values(json.dumps(run.payload))


class TestAgentPicks:
    def _two(self, user, workspace, storage, key):
        """A2 and A3 with their own sets; A3 is the default."""
        environment = _cached(user, workspace, key, storage)
        a2 = _a2(environment)
        _change(
            a2,
            source={"repository": "acme/agent", "ref": "main", "commit_sha": COMMIT},
            config={"tier": "two"},
        )
        a3 = _create(a2, environment, secret_values={"OPENAI_API_KEY": "v3"})
        assert _check(a3).status == "passed"
        _change(a3, config={"tier": "three"})
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a3
        )
        environment.refresh_from_db()
        return environment, a2, a3

    def test_an_older_version_runs_its_own_commit_and_config(
        self, user, workspace, storage, scheduled
    ):
        environment, a2, a3 = self._two(user, workspace, storage, "ar-pick-a2")
        _ready(environment, a2.id, *_pair(environment))

        run = _run(environment, VersionPicks(agent_version_id=a2.id))

        assert run.agent_version_id == a2.id != environment.agent_version_id
        assert run.payload["agent"]["config"] == {"tier": "two"}
        assert run.payload["source"]["commit_sha"] == COMMIT
        assert run.payload["metadata"]["version_pins"]["agent_version_id"] == str(a2.id)
        assert run.payload["metadata"]["assembly"]["cached"] is True
        bundle = HarnessAssembledBundle.no_workspace_objects.get(
            id=run.assembled_bundle_id
        )
        assert bundle.agent_version_id == a2.id

    def test_an_older_version_without_a_bundle_is_not_runnable(
        self, user, workspace, storage, scheduled
    ):
        environment, a2, _ = self._two(user, workspace, storage, "ar-pick-none")

        with pytest.raises(HostedHarnessError) as refused:
            _run(environment, VersionPicks(agent_version_id=a2.id))

        assert (refused.value.code, refused.value.status_code) == (
            "version_not_runnable",
            422,
        )

    def test_the_run_endpoint_takes_the_pick(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _cached(user, workspace, "ar-pick-api", storage)
        a2 = _a2(environment)

        with patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"):
            response = auth_client.post(
                f"/simulate/api/harness-environments/{environment.id}/run/",
                {
                    "scenario_ids": [_key(NAMES[0])],
                    "trials": 1,
                    "agent_version_id": str(a2.id),
                },
                format="json",
                HTTP_IDEMPOTENCY_KEY="ar-pick-api",
            )

        assert response.status_code == 202, response.content
        assert response.json()["versions"]["agent"]["id"] == str(a2.id)
        _no_values(response.content.decode())


class TestBundleKey:
    def test_a_value_change_keeps_the_key_and_a_new_name_does_not(
        self, user, workspace, storage, scheduled
    ):
        environment = _cached(user, workspace, "ar-key", storage)
        a2 = _a2(environment)
        a3 = _create(a2, environment, secret_values={"OPENAI_API_KEY": "v3"})
        a4 = _create(a3, environment, secret_values={"EXTRA_NAME": "v"})
        pair = _pair(environment)

        assert len({_side(one).hash for one in (a2, a3, a4)}) == 3
        assert bundle_key(_side(a3), *pair) == bundle_key(_side(a2), *pair)
        assert bundle_key(_side(a4), *pair) != bundle_key(_side(a3), *pair)


class TestWarnings:
    def _picked(self, environment, version):
        _ready(environment, version.id, *_pair(environment))
        return preview_run(environment, VersionPicks(agent_version_id=version.id))[
            "warnings"
        ]

    def test_a_version_with_every_credential_warns_of_none(
        self, user, workspace, storage, scheduled
    ):
        environment = _cached(user, workspace, "ar-w-none", storage)
        a2 = _a2(environment)

        warnings = self._picked(environment, a2)

        assert not set(_codes(warnings)) & {
            "variable_missing",
            "provider_key_unavailable",
            "credential_unavailable",
        }

    def test_a_missing_variable(self, user, workspace, storage, scheduled):
        environment = _cached(user, workspace, "ar-w-var", storage)
        a2 = _a2(environment)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            required_variable_names=["OPENAI_API_KEY", "DB_URL", "API_BASE"]
        )

        [warning] = [
            one
            for one in self._picked(environment, a2)
            if one["code"] == "variable_missing"
        ]

        assert warning == {
            "code": "variable_missing",
            "message": "This agent version has no value for API_BASE, DB_URL",
            "piece": "agent",
        }

    def test_a_missing_provider_key(self, user, workspace, storage, scheduled):
        environment = _cached(user, workspace, "ar-w-provider", storage)
        a2 = _a2(environment)
        _change(a2, connector="vapi")

        [warning] = [
            one
            for one in self._picked(environment, a2)
            if one["code"] == "provider_key_unavailable"
        ]

        assert warning["message"] == "This agent version has no VAPI_API_KEY for vapi"
        assert warning["piece"] == "agent"

    def test_an_unreadable_set(self, user, workspace, storage, scheduled):
        environment = _cached(user, workspace, "ar-w-blob", storage)
        a2 = _a2(environment)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            credential_blob="enc::not-a-token"
        )

        warnings = self._picked(environment, a2)

        assert "credential_unavailable" in _codes(warnings)
        _no_values(json.dumps(warnings))

    def test_every_warning_in_a_fixed_order(self, user, workspace, storage, scheduled):
        environment = _cached(user, workspace, "ar-w-order", storage)
        a2 = _a2(environment)
        _change(a2, connector="vapi")
        HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
            required_variable_names=["DB_URL"], credential_blob="enc::not-a-token"
        )

        codes = _codes(self._picked(environment, a2))

        agent_codes = [
            code
            for code in codes
            if code
            in (
                "variable_missing",
                "provider_key_unavailable",
                "credential_unavailable",
            )
        ]
        assert agent_codes == [
            "variable_missing",
            "provider_key_unavailable",
            "credential_unavailable",
        ]

    def test_a_legacy_version_warns_of_no_key(self, user, workspace, storage):
        environment = _cached(user, workspace, "ar-w-legacy", storage)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=environment.agent_version_id
        ).update(agent_payload={"connector": "vapi"})

        codes = _codes(preview_run(environment)["warnings"])

        assert "provider_key_unavailable" not in codes
        assert "credential_unavailable" not in codes


class TestSecretValues:
    def _post(self, auth_client, body):
        return auth_client.post(SECRET_VALUES, body, format="json")

    def test_without_an_environment_nothing_is_stored(self, auth_client, user):
        before = HostedHarnessSecret.objects.count()

        response = self._post(
            auth_client, {"environment_values": {"OPENAI_API_KEY": "val-openai-new"}}
        )
        empty = self._post(
            auth_client,
            {
                "environment_id": "00000000-0000-0000-0000-000000000000",
                "environment_values": {},
            },
        )

        assert response.status_code == 400
        assert empty.status_code == 400
        assert "environment_id" in response.content.decode()
        assert "val-openai-new" not in response.content.decode()
        assert HostedHarnessSecret.objects.count() == before

    def test_with_an_environment_it_makes_the_next_agent_version(
        self,
        auth_client,
        user,
        workspace,
        storage,
        scheduled,
        caplog,
        django_capture_on_commit_callbacks,
    ):
        caplog.set_level(logging.DEBUG)
        environment = _cached(user, workspace, "ar-sv", storage)
        a1 = _a1(environment)
        secrets_before = HostedHarnessSecret.objects.count()

        with django_capture_on_commit_callbacks(execute=True):
            response = self._post(
                auth_client,
                {
                    "environment_id": str(environment.id),
                    "environment_values": {"OPENAI_API_KEY": "val-openai-new"},
                },
            )

        assert response.status_code == 202, response.content
        item = response.json()["agent_version"]
        a2 = AgentVersion.no_workspace_objects.get(id=item["id"])
        assert a2.version_number == a1.version_number + 1
        assert (item["status"], item["preflight_status"], item["is_default"]) == (
            "draft",
            "pending",
            False,
        )
        assert item["origin"] == "credentials"
        assert item["parent_id"] == str(a1.id)
        assert item["credential_names"] == list(NAMES_ONLY)
        assert item["summary"] == "1 variable changed"
        scheduled.assert_called_once()
        assert scheduled.call_args.kwargs["args"] == [str(a2.id)]
        assert _blob(a2)["env"] == {"OPENAI_API_KEY": "val-openai-new"}
        assert HostedHarnessSecret.objects.count() == secrets_before
        _no_values(response.content.decode())
        _no_values(caplog.text)
        _no_values(_non_secret_fields(_side(a2)))
        _no_values(json.dumps(a2.configuration_snapshot) + a2.commit_message)
        environment.refresh_from_db()
        assert environment.agent_version_id == a1.id

    def test_a_staged_file_goes_into_the_set_and_stays_staged(
        self, auth_client, user, workspace, storage, scheduled
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        environment = _cached(user, workspace, "ar-sv-file", storage)
        document = {
            "type": "service_account",
            "project_id": "p",
            "client_email": "c@example.test",
            "private_key": "val-adc-new",
        }
        staged = auth_client.post(
            SECRET_FILES,
            {
                "file": SimpleUploadedFile(
                    "adc.json", json.dumps(document).encode(), "application/json"
                ),
                "environment_name": "GOOGLE_APPLICATION_CREDENTIALS",
            },
            format="multipart",
        )
        assert staged.status_code == 201, staged.content
        ref = staged.json()["secret_ref"]

        response = self._post(
            auth_client,
            {
                "environment_id": str(environment.id),
                "environment_values": {},
                "files": [ref],
            },
        )

        assert response.status_code == 202, response.content
        a2 = AgentVersion.no_workspace_objects.get(
            id=response.json()["agent_version"]["id"]
        )
        [adc] = _blob(a2)["files"]
        assert adc["alias"] == "GOOGLE_APPLICATION_CREDENTIALS_JSON"
        assert "val-adc-new" in __import__("base64").b64decode(
            adc["content_b64"]
        ).decode("utf-8")
        assert HostedHarnessSecret.objects.filter(name=ref["key"]).exists()
        assert "val-adc-new" not in response.content.decode()

    def test_a_new_name_that_cannot_run_makes_no_version(
        self, auth_client, user, workspace, storage, scheduled, settings
    ):
        environment = _cached(user, workspace, "ar-sv-new-name", storage)
        body = {
            "environment_id": str(environment.id),
            "environment_values": {"NEW_API_KEY": "val-openai-new"},
        }
        before = (
            AgentVersion._base_manager.count(),
            HarnessAgentVersionRuntime._base_manager.count(),
        )

        response = self._post(auth_client, body)

        assert response.status_code == 422, response.content
        assert response.json()["error"] == "version_not_runnable"
        _no_values(response.content.decode())
        assert before == (
            AgentVersion._base_manager.count(),
            HarnessAgentVersionRuntime._base_manager.count(),
        )
        scheduled.assert_not_called()
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        assert self._post(auth_client, body).status_code == 202

    def test_a_value_only_change_runs_the_sealed_default_pair_uncached(
        self, auth_client, user, workspace, storage, scheduled, gateway
    ):
        environment = _environment(user, workspace, "ar-sv-rotate", storage)
        assert not HarnessAssembledBundle._base_manager.filter(
            job_id=environment.id, state=HarnessAssembledBundle.State.READY
        ).exists()
        key = environment.payload["metadata"]["authoring_object_key"]

        response = self._post(
            auth_client,
            {
                "environment_id": str(environment.id),
                "environment_values": {"OPENAI_API_KEY": "val-openai-new"},
            },
        )

        assert response.status_code == 202, response.content
        a2 = AgentVersion.no_workspace_objects.get(
            id=response.json()["agent_version"]["id"]
        )
        assert _preflight(a2).status == "passed"
        environment.refresh_from_db()
        assert environment.agent_version_id == a2.id
        metadata = environment.payload["metadata"]
        assert (metadata["authoring_object_key"], metadata["default_pair_sealed"]) == (
            key,
            True,
        )
        run = _run(environment)
        assert run.agent_version_id == a2.id
        assert run.payload["metadata"]["assembly"]["defaults"] is True

        with capture_logs() as logs:
            attempt = _launch(gateway, run)

        assert attempt.state == HostedHarnessAttempt.State.RUNNING
        assert [one["outcome"] for one in logs if "outcome" in one] == ["miss_fallback"]
        assert "source_fingerprint" not in _command(gateway)
        assert run.payload["metadata"]["authoring_object_key"] == key
        assert gateway.client.sandbox.fs.uploads["/work/authoring.tar.gz"] == (
            storage.objects[key]
        )

    def test_refusals(self, auth_client, user, workspace, storage, settings):
        environment = _environment(user, workspace, "ar-sv-no", storage)
        body = {
            "environment_id": str(environment.id),
            "environment_values": {"OPENAI_API_KEY": "val-openai-new"},
        }
        unknown = self._post(auth_client, {**body, "environment_id": str(user.id)})
        not_staged = self._post(
            auth_client,
            {
                **body,
                "files": [
                    {
                        "manager": "platform-vault",
                        "key": "harness-openai_api_key-x",
                        "version": "1",
                        "purpose": "target_provider",
                    }
                ],
            },
        )
        gone = self._post(
            auth_client,
            {
                **body,
                "files": [
                    {
                        "manager": "platform-vault",
                        "key": "harness-google-adc-gone",
                        "version": "1",
                        "purpose": "target_provider",
                    }
                ],
            },
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=None
        )
        unpinned = self._post(auth_client, body)
        settings.HARNESS_VERSIONING_ENABLED = False
        off = self._post(auth_client, body)

        assert unknown.status_code == 404
        assert not_staged.status_code == 422
        assert not_staged.json()["error"] == "credential_file_invalid"
        assert gone.status_code == 422
        assert gone.json()["error"] == "secret_not_found"
        for response in (unpinned, off):
            assert response.status_code == 422
            assert response.json()["error"] == "agent_versions_unavailable"
            _no_values(response.content.decode())
        assert not AgentVersion.no_workspace_objects.filter(
            agent_definition_id=_a1_definition(environment), version_number__gt=1
        ).exists()


def _a1_definition(environment):
    return (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            organization_id=environment.organization_id
        )
        .values_list("agent_definition_id", flat=True)
        .first()
    )


class TestRerunWithCredentials:
    def _source(self, environment):
        source = _run(environment, key="source")
        HostedHarnessJob.no_workspace_objects.filter(id=source.id).update(
            state=HostedHarnessJob.State.COMPLETED
        )
        source.refresh_from_db()
        return source

    def _rerun(self, user, workspace, source, values, patches=None):
        patches = _passing() if patches is None else patches
        for one in patches:
            one.start()
        try:
            with patch(
                "simulate.temporal.client.start_hosted_harness_gateway_workflow"
            ) as start:
                result = HostedHarnessProvider().rerun_saved(
                    str(source.id),
                    organization=user.organization,
                    workspace=workspace,
                    environment_values=values,
                )
        finally:
            for one in patches:
                one.stop()
        return result, start

    def test_new_values_are_checked_then_run_as_a_new_version(
        self,
        user,
        workspace,
        storage,
        scheduled,
        caplog,
        django_capture_on_commit_callbacks,
    ):
        caplog.set_level(logging.DEBUG)
        environment = _cached(user, workspace, "ar-rr", storage)
        a1 = _a1(environment)
        source = self._source(environment)
        secrets_before = HostedHarnessSecret.objects.count()

        with django_capture_on_commit_callbacks(execute=True):
            result, start = self._rerun(
                user, workspace, source, {"OPENAI_API_KEY": "val-openai-new"}
            )

        run = HostedHarnessJob.no_workspace_objects.get(id=result["job"]["job_id"])
        a2 = run.agent_version
        assert a2.agent_definition_id == a1.agent_definition_id
        assert a2.id != a1.id
        runtime = _side(a2)
        assert (runtime.preflight_status, runtime.trigger) == ("passed", "credentials")
        assert runtime.parent == _side(a1)
        assert run.payload["metadata"]["rerun_of"] == str(source.id)
        assert run.environment_version_id == source.environment_version_id
        assert run.suite_version_id == source.suite_version_id
        # Same names: the source's cached bundle is replayed.
        assert run.assembled_bundle_id == source.assembled_bundle_id
        assert run.payload["agent"]["secret_refs"] == {
            name: agent_version_secret_ref(name) for name in NAMES_ONLY
        }
        assert HostedHarnessSecret.objects.count() == secrets_before
        assert PlatformSecretResolver().resolve(run)["OPENAI_API_KEY"] == (
            "val-openai-new"
        )
        # Checked here, never queued and never made the default.
        scheduled.assert_not_called()
        environment.refresh_from_db()
        assert environment.agent_version_id == a1.id
        start.assert_called_once()
        _no_values(json.dumps(run.payload) + run.request_digest)
        _no_values(json.dumps(result))
        _no_values(caplog.text)

    def test_a_failed_check_reruns_nothing(self, user, workspace, storage, scheduled):
        environment = _cached(user, workspace, "ar-rr-fail", storage)
        source = self._source(environment)
        runs_before = HostedHarnessJob.no_workspace_objects.filter(
            environment=environment
        ).count()
        probe = {
            "provider": "openai",
            "label": "OpenAI",
            "aliases": ["OPENAI_API_KEY"],
            "ok": False,
            "message": "rejected val-openai-new",
        }

        with pytest.raises(HostedHarnessError) as refused:
            self._rerun(
                user,
                workspace,
                source,
                {"OPENAI_API_KEY": "val-openai-new"},
                _passing(return_value=[probe]),
            )

        assert (refused.value.code, refused.value.status_code) == (
            "agent_preflight_failed",
            422,
        )
        body = refused.value.as_dict()
        assert body["preflight_report"]["invalid"] == ["OPENAI_API_KEY"]
        _no_values(json.dumps(body) + refused.value.message)
        assert (
            HostedHarnessJob.no_workspace_objects.filter(
                environment=environment
            ).count()
            == runs_before
        )
        failed = _side(
            AgentVersion.no_workspace_objects.get(id=body["agent_version_id"])
        )
        assert failed.preflight_status == "failed"

    def test_the_call_rerun_endpoint_states_the_refusal(
        self, auth_client, user, workspace, storage, scheduled
    ):
        from simulate.models import TestExecution

        environment = _cached(user, workspace, "ar-rr-api", storage)
        source = self._source(environment)
        TestExecution.objects.filter(id=source.test_execution_id).update(
            status=TestExecution.ExecutionStatus.COMPLETED
        )
        refusal = HostedHarnessError(
            "agent_preflight_failed",
            "The changed credentials didn't pass their check, so nothing was rerun",
            status_code=422,
            details={"agent_version_id": "x", "preflight_report": {"invalid": []}},
        )

        with patch.object(
            HostedHarnessProvider, "rerun_saved", side_effect=refusal
        ) as rerun:
            response = auth_client.post(
                f"/simulate/test-executions/{source.test_execution_id}/rerun-calls/",
                {
                    "rerun_type": "call_and_eval",
                    "select_all": True,
                    "environment_values": {"OPENAI_API_KEY": "val-openai-new"},
                },
                format="json",
            )

        assert rerun.called, response.content
        assert response.status_code == 422
        assert response.json()["error"] == "agent_preflight_failed"

    def test_new_values_without_a_cached_bundle_replay_the_default_pair(
        self, user, workspace, storage, scheduled
    ):
        from simulate.services.harness_versioning import assembly

        environment = _environment(user, workspace, "ar-rr-uncached", storage)
        a1 = _a1(environment)
        source = self._source(environment)
        assert not HarnessAssembledBundle._base_manager.filter(
            job_id=environment.id, state=HarnessAssembledBundle.State.READY
        ).exists()

        result, _ = self._rerun(
            user, workspace, source, {"OPENAI_API_KEY": "val-openai-new"}
        )

        run = HostedHarnessJob.no_workspace_objects.get(id=result["job"]["job_id"])
        assert run.agent_version_id not in (None, a1.id)
        assert run.payload["metadata"]["assembly"]["defaults"] is True
        assert PlatformSecretResolver().resolve(run)["OPENAI_API_KEY"] == (
            "val-openai-new"
        )
        assert assembly.prepare_launch(run) is None
        assert run.payload["metadata"]["authoring_object_key"] == (
            environment.payload["metadata"]["authoring_object_key"]
        )
        environment.refresh_from_db()
        assert environment.agent_version_id == a1.id

    def test_a_new_name_that_cannot_run_makes_no_version(
        self, auth_client, user, workspace, storage, scheduled
    ):
        from simulate.models import TestExecution

        environment = _cached(user, workspace, "ar-rr-new-name", storage)
        source = self._source(environment)
        TestExecution.objects.filter(id=source.test_execution_id).update(
            status=TestExecution.ExecutionStatus.COMPLETED
        )
        before = (
            AgentVersion._base_manager.count(),
            HarnessAgentVersionRuntime._base_manager.count(),
            HostedHarnessJob.no_workspace_objects.count(),
        )

        with pytest.raises(HostedHarnessError) as refused:
            self._rerun(user, workspace, source, {"NEW_API_KEY": "val-openai-new"})
        with patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"):
            response = auth_client.post(
                f"/simulate/test-executions/{source.test_execution_id}/rerun-calls/",
                {
                    "rerun_type": "call_and_eval",
                    "select_all": True,
                    "environment_values": {"NEW_API_KEY": "val-openai-new"},
                },
                format="json",
            )

        assert (refused.value.code, refused.value.status_code) == (
            "version_not_runnable",
            422,
        )
        assert response.status_code == 422, response.content
        assert response.json()["error"] == "version_not_runnable"
        _no_values(response.content.decode())
        assert before == (
            AgentVersion._base_manager.count(),
            HarnessAgentVersionRuntime._base_manager.count(),
            HostedHarnessJob.no_workspace_objects.count(),
        )

    def test_a_source_agent_without_a_side_row_stores_values_as_before(
        self, user, workspace, storage, scheduled
    ):
        environment = _cached(user, workspace, "ar-rr-noside", storage)
        source = self._source(environment)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=source.agent_version_id
        ).delete()
        versions_before = AgentVersion.no_workspace_objects.count()

        result, _ = self._rerun(
            user, workspace, source, {"OPENAI_API_KEY": "val-openai-new"}
        )

        run = HostedHarnessJob.no_workspace_objects.get(id=result["job"]["job_id"])
        ref = run.payload["agent"]["secret_refs"]["OPENAI_API_KEY"]
        assert ref["key"].startswith("harness-openai_api_key-")
        assert AgentVersion.no_workspace_objects.count() == versions_before

    def test_a_source_agent_without_a_side_row_takes_the_environments_default(
        self, user, workspace, storage, scheduled
    ):
        environment = _cached(user, workspace, "ar-rr-default", storage)
        source = self._source(environment)
        a2 = _a2(environment, LIVEKIT_API_KEY="val-lk-key-new")
        HostedHarnessJob.no_workspace_objects.filter(id=source.id).update(
            agent_version=None
        )
        source.refresh_from_db()
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=a2
        )

        result, _ = self._rerun(
            user, workspace, source, {"OPENAI_API_KEY": "val-openai-new"}
        )

        run = HostedHarnessJob.no_workspace_objects.get(id=result["job"]["job_id"])
        assert _side(run.agent_version).parent == _side(a2)
        resolved = PlatformSecretResolver().resolve(run)
        assert resolved["OPENAI_API_KEY"] == "val-openai-new"
        assert resolved["LIVEKIT_API_KEY"] == "val-lk-key-new"
        assert _side(run.agent_version).agent_payload["requested_by_environments"] == []

    def test_without_versioning_values_are_stored_as_before(
        self, user, workspace, storage, scheduled, settings
    ):
        environment = _cached(user, workspace, "ar-rr-off", storage)
        source = self._source(environment)
        versions_before = AgentVersion.no_workspace_objects.count()
        settings.HARNESS_VERSIONING_ENABLED = False

        result, _ = self._rerun(
            user, workspace, source, {"OPENAI_API_KEY": "val-openai-new"}
        )

        run = HostedHarnessJob.no_workspace_objects.get(id=result["job"]["job_id"])
        ref = run.payload["agent"]["secret_refs"]["OPENAI_API_KEY"]
        assert ref["key"].startswith("harness-openai_api_key-")
        assert HostedHarnessSecret.objects.get(name=ref["key"]).get_value() == (
            "val-openai-new"
        )
        assert AgentVersion.no_workspace_objects.count() == versions_before
        assert "val-openai-new" not in json.dumps(run.payload)


def test_a_passed_version_is_listed_with_why_it_is_not_the_default(
    auth_client, user, workspace, storage, scheduled
):
    from simulate.services.harness_agent_versions import activate_agent_version

    environment = _cached(user, workspace, "ar-list", storage)
    a2 = _a2(environment)
    HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=a2).update(
        required_variable_names=["OPENAI_API_KEY"]
    )
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        state=HostedHarnessJob.State.RUNNING
    )
    assert activate_agent_version(a2) == []
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        state=HostedHarnessJob.State.COMPLETED
    )

    response = auth_client.get(
        f"/simulate/api/harness-environments/{environment.id}/versions/"
    )

    assert response.status_code == 200
    body = response.content.decode()
    for value in SECRETS:
        assert value not in body
    items = {one["id"]: one for one in response.json()["agent"]}
    item = items[str(a2.id)]
    assert (item["status"], item["is_default"]) == ("active", False)
    assert item["activation_outcome"] == "environment_busy"
    assert item["credential_names"] == list(NAMES_ONLY)
    assert item["required_variable_names"] == ["OPENAI_API_KEY"]
    a1 = items[str(environment.agent_version_id)]
    assert a1["credential_names"] == list(NAMES_ONLY)
    assert a1["required_variable_names"] is None
    assert a1["activation_outcome"] is None
    suite = response.json()["suite"][0]
    assert (suite["credential_names"], suite["activation_outcome"]) == (None, None)
