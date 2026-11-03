"""POST versions/agent/: an environment's next agent version from an upload, a prompt
edit or a settings edit."""

import uuid
from unittest.mock import patch

import pytest
from minio.error import S3Error

from accounts.models import Workspace
from simulate.models import AgentVersion, HarnessAgentVersionRuntime, HostedHarnessJob
from simulate.services.harness_agent_changes import change_agent_source
from simulate.services.harness_agent_versions import (
    apply_agent_version,
    resnapshot_version,
)
from simulate.services.hosted_harness_gateway import _source_object_key
from simulate.tests.test_harness_agent_receiver import _owned
from simulate.tests.test_harness_agent_versions import (
    _a1,
    _defaults,
    _environment,
    _outcomes,
    _preflight,
    _side,
    _store_secrets,
)
from simulate.tests.test_harness_agent_versions import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
    _definition,
    _with_metadata,
)
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
UPLOADED = uuid.uuid4()
NUMBER = "+14155550100"
PROMPT = "Answer billing questions briefly."
FIRST = {"target_speaks_first": True}


@pytest.fixture(autouse=True)
def flags(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True


def _phone(payload):
    payload["source"] = {"kind": "provider"}
    payload["agent"]["connector"] = "phone"
    payload["agent"]["config"] = {
        "phone_number": NUMBER,
        "target_system_prompt": PROMPT,
    }


def _vapi(payload):
    payload["source"] = {"kind": "provider"}
    payload["agent"]["connector"] = "vapi"
    payload["agent"]["config"] = {"assistant_id": "asst-1"}


def _remote(payload):
    payload["source"] = {"kind": "remote", "endpoint": "https://agent.example.test/a"}


def _archive(payload):
    payload["source"] = {"kind": "archive", "archive_artifact_id": str(UPLOADED)}


def _post(auth_client, environment, body):
    return auth_client.post(
        f"{BASE}/{environment.id}/versions/agent/", body, format="json"
    )


def _made(response):
    assert response.status_code == 202, response.content
    item = response.json()["agent_version"]
    return item, AgentVersion._base_manager.get(id=item["id"])


def _refused(response, code, status=422):
    assert response.status_code == status, response.content
    if code is not None:
        assert response.json()["error"] == code
    return response.json()


class TestUpload:
    def test_a_new_upload(self, auth_client, user, workspace, storage, scheduled):
        environment = _environment(user, workspace, "src-up", storage, edit=_archive)
        new = uuid.uuid4()
        storage.objects[_source_object_key(user.organization.id, new)] = b"tar"

        item, version = _made(
            _post(auth_client, environment, {"archive_artifact_id": str(new)})
        )

        assert (item["origin"], item["status"], item["preflight_status"]) == (
            "upload",
            "draft",
            "pending",
        )
        side = _side(version)
        assert side.source_kind == "archive"
        assert side.source == {"archive_artifact_id": str(new)}
        assert side.agent_payload["requested_by_environments"] == [str(environment.id)]

    def test_refusals(self, auth_client, user, workspace, storage, scheduled):
        environment = _environment(user, workspace, "src-up-no", storage, edit=_archive)
        github = _environment(user, workspace, "src-up-gh", storage)
        elsewhere = uuid.uuid4()
        # Another organization's upload is not under this one's key.
        storage.objects[_source_object_key(uuid.uuid4(), elsewhere)] = b"tar"
        storage.objects[_source_object_key(user.organization.id, UPLOADED)] = b"tar"

        _refused(
            _post(auth_client, environment, {"archive_artifact_id": str(elsewhere)}),
            "archive_source_not_found",
        )
        _refused(
            _post(auth_client, environment, {"archive_artifact_id": str(UPLOADED)}),
            "agent_unchanged",
        )
        body = _refused(
            _post(auth_client, github, {"archive_artifact_id": str(UPLOADED)}),
            "agent_change_unsupported",
        )
        assert body["field"] == "archive_artifact_id"
        assert (
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                trigger="upload"
            ).count()
            == 0
        )

    def test_a_storage_fault_is_not_an_expired_upload(
        self, user, workspace, storage, scheduled
    ):
        environment = _environment(
            user, workspace, "src-up-fault", storage, edit=_archive
        )
        fault = S3Error(None, "AccessDenied", "denied", "key", "request", "host")
        with patch.object(storage, "stat_object", side_effect=fault):
            with pytest.raises(S3Error):
                change_agent_source(environment, archive_artifact_id=uuid.uuid4())


class TestPrompt:
    def test_a_phone_prompt_reaches_the_run(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "src-phone", storage, edit=_phone)
        a1 = _a1(environment)
        prompt = "Answer every question in one sentence."

        item, version = _made(_post(auth_client, environment, {"prompt": prompt}))

        assert item["origin"] == "prompt"
        side = _side(version)
        assert version.description == prompt
        assert version.configuration_snapshot["description"] == prompt
        assert side.prompt_hash != _side(a1).prompt_hash
        assert side.agent_payload["config"]["target_system_prompt"] == prompt
        assert side.agent_payload["config"]["phone_number"] == NUMBER
        payload = dict(environment.payload)
        apply_agent_version(payload, side, environment_id=environment.id)
        assert payload["agent"]["config"]["target_system_prompt"] == prompt

    def test_the_prompt_compared_is_the_one_runs_read(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "src-same", storage, edit=_phone)
        # The legacy version's description is not what the build reads.
        assert _a1(environment).description != PROMPT

        _refused(_post(auth_client, environment, {"prompt": PROMPT}), "agent_unchanged")
        _refused(
            _post(auth_client, environment, {"prompt": f"  {PROMPT}\n"}),
            "agent_unchanged",
        )
        _refused(_post(auth_client, environment, {"prompt": "x" * 65_537}), None, 400)
        _refused(_post(auth_client, environment, {"prompt": "  "}), None, 400)

    def test_a_code_agents_prompt_is_its_sources(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "src-gh-prompt", storage)
        body = _refused(
            _post(auth_client, environment, {"prompt": "new"}),
            "agent_change_unsupported",
        )
        assert body["field"] == "prompt"

    def test_a_vapi_prompt_is_the_evaluation_prompt(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "src-vapi", storage, edit=_vapi)

        _, version = _made(_post(auth_client, environment, {"prompt": "Judge this."}))

        assert version.description == "Judge this."
        assert version.configuration_snapshot["description"] == "Judge this."
        assert "target_system_prompt" not in (_side(version).agent_payload["config"])

        # A shared definition's description may be another environment's prompt.
        definition = version.agent_definition
        definition.description = "Another environment's prompt."
        definition.save(update_fields=["description"])
        resnapshot_version(version.id)
        version.refresh_from_db()
        assert version.configuration_snapshot["description"] == "Judge this."

    def test_a_remote_target_owns_its_prompt_and_settings(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "src-remote", storage, edit=_remote)
        for body in (
            {"prompt": "new"},
            {"agent_payload": {"config": {"target_speaks_first": True}}},
        ):
            _refused(_post(auth_client, environment, body), "agent_change_unsupported")


class TestSettings:
    def test_a_new_phone_number(self, auth_client, user, workspace, storage, scheduled):
        environment = _environment(user, workspace, "src-number", storage, edit=_phone)
        parent = _side(_a1(environment))

        item, version = _made(
            _post(
                auth_client,
                environment,
                {"agent_payload": {"config": {"phone_number": "+14155550199"}}},
            )
        )

        assert item["origin"] == "agent_payload"
        side = _side(version)
        assert side.source["phone_number"] == "+14155550199"
        assert (
            side.agent_payload["source_access"] == parent.agent_payload["source_access"]
        )
        assert version.configuration_snapshot["contact_number"] == "+14155550199"
        assert side.agent_payload["config"]["target_system_prompt"] == PROMPT

        definition = version.agent_definition
        definition.contact_number = NUMBER
        definition.save(update_fields=["contact_number"])
        resnapshot_version(version.id)
        version.refresh_from_db()
        assert version.configuration_snapshot["contact_number"] == "+14155550199"

    def test_who_speaks_first(self, auth_client, user, workspace, storage, scheduled):
        environment = _environment(user, workspace, "src-first", storage, edit=_vapi)

        _, version = _made(
            _post(
                auth_client,
                environment,
                {"agent_payload": {"config": {"target_speaks_first": True}}},
            )
        )

        assert _side(version).agent_payload["config"]["target_speaks_first"] is True

    @pytest.mark.parametrize(
        "body",
        [
            # Each beside a change it would otherwise carry.
            {"agent_payload": {"config": {**FIRST, "api_key": "sk-x"}}},
            {"agent_payload": {"config": {**FIRST, "assistant_id": "asst-2"}}},
            {"agent_payload": {"config": {**FIRST, "url": "wss://x"}}},
            {"agent_payload": {"secret_refs": {}, "config": FIRST}},
            {"agent_payload": {"call_direction": "inbound", "config": FIRST}},
            {"agent_payload": {"config": {"phone_number": "4155550100"}}},
            {"agent_payload": {"config": {}}},
            {},
            {"archive_artifact_id": str(UPLOADED), "prompt": "x"},
            {"prompt": "x", "secret_refs": {}},
        ],
    )
    def test_bad_bodies(self, auth_client, user, workspace, storage, scheduled, body):
        environment = _environment(user, workspace, "src-bad", storage, edit=_phone)
        _refused(_post(auth_client, environment, body), None, 400)
        assert _defaults(environment) == [_a1(environment).id]

    def test_unchanged_and_unsupported_settings(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "src-same-no", storage, edit=_phone)
        vapi = _environment(user, workspace, "src-vapi-no", storage, edit=_vapi)

        _refused(
            _post(
                auth_client,
                environment,
                {"agent_payload": {"config": {"phone_number": NUMBER}}},
            ),
            "agent_unchanged",
        )
        body = _refused(
            _post(
                auth_client,
                vapi,
                {"agent_payload": {"config": {"phone_number": NUMBER}}},
            ),
            "agent_change_unsupported",
        )
        assert body["field"] == "agent_payload.config.phone_number"


class TestGates:
    def test_refusals(self, auth_client, settings, user, workspace, storage, scheduled):
        environment = _environment(user, workspace, "src-gates", storage, edit=_vapi)
        body = {"prompt": "new"}

        settings.HARNESS_VERSIONING_ENABLED = False
        _refused(_post(auth_client, environment, body), "versioning_disabled", 404)
        settings.HARNESS_VERSIONING_ENABLED = True

        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
        refused = _refused(
            _post(auth_client, environment, body), "version_not_runnable"
        )
        assert refused["piece"] == "agent"
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True

        _with_metadata(environment, agent_definition_owner="connected")
        _refused(_post(auth_client, environment, body), "agent_connected")
        _with_metadata(environment, agent_definition_owner=None)

        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=None
        )
        _refused(_post(auth_client, environment, body), "agent_versions_unavailable")

    def test_another_workspaces_environment_is_not_found(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "src-ws", storage, edit=_vapi)
        other = Workspace.objects.create(
            name="Other",
            organization=user.organization,
            is_active=True,
            created_by=user,
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            workspace=other
        )
        response = _post(auth_client, environment, {"prompt": "new"})
        assert response.status_code == 404


def _two_phone_environments(user, workspace, storage, key):
    """Two phone environments of one agent, both moved off the legacy version."""
    from simulate.tests.test_harness_agent_versions import _create

    definition = _definition(user, workspace)
    _agent_version(definition)
    environments = [
        _environment(
            user, workspace, f"{key}-{n}", storage, definition=definition, edit=_phone
        )
        for n in range(2)
    ]
    for environment in environments:
        _store_secrets(user.organization, environment)
    a1 = _a1(environments[0])
    a2 = _create(a1, environments[0], secret_values={"OPENAI_API_KEY": "v2"})
    assert _preflight(a2).status == "passed"
    assert _create(a1, environments[1], secret_values={"OPENAI_API_KEY": "v2"}).id == (
        a2.id
    )
    assert _defaults(*environments) == [a2.id, a2.id]
    return environments


def test_only_the_asking_environment_moves(
    auth_client, user, workspace, storage, scheduled
):
    first, second = _two_phone_environments(user, workspace, storage, "src-ask")
    a2_id = first.agent_version_id

    _, version = _made(_post(auth_client, first, {"prompt": "Be friendly."}))
    assert _preflight(version).status == "passed"

    assert _defaults(first, second) == [version.id, a2_id]
    assert _outcomes(version) == {
        str(first.id): "activated",
        str(second.id): "not_requested",
    }


def test_one_side_row_and_one_check(
    auth_client,
    user,
    workspace,
    storage,
    scheduled,
    django_capture_on_commit_callbacks,
):
    environment = _environment(user, workspace, "src-once", storage, edit=_vapi)
    _owned(environment)

    with django_capture_on_commit_callbacks(execute=True):
        _, version = _made(_post(auth_client, environment, {"prompt": "Once."}))

    assert (
        HarnessAgentVersionRuntime._base_manager.filter(agent_version=version).count()
        == 1
    )
    assert scheduled.call_count == 1


def _listed(auth_client, environment):
    response = auth_client.get(f"{BASE}/{environment.id}/versions/")
    assert response.status_code == 200, response.content
    return response.json()


class TestRead:
    def test_the_default_carries_its_source_and_prompt(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "src-read", storage, edit=_phone)
        prompt = "Answer in one sentence."
        _, version = _made(_post(auth_client, environment, {"prompt": prompt}))

        listed = _listed(auth_client, environment)
        default, draft = (
            next(item for item in listed["agent"] if item["is_default"]),
            next(item for item in listed["agent"] if item["id"] == str(version.id)),
        )
        assert default["source"] == {"kind": "phone", "phone_number": NUMBER}
        assert default["prompt"] == PROMPT
        assert draft["source"] == {"kind": "phone", "phone_number": NUMBER}
        assert draft["prompt"] is None
        for item in (*listed["environment"], *listed["suite"]):
            assert (item["source"], item["prompt"]) == (None, None)

        assert _preflight(version).status == "passed"
        [default] = [
            item
            for item in _listed(auth_client, environment)["agent"]
            if item["is_default"]
        ]
        assert (default["id"], default["prompt"]) == (str(version.id), prompt)

    def test_a_vapi_default_reads_its_evaluation_prompt(
        self, auth_client, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "src-read-v", storage, edit=_vapi)

        [default] = [
            item
            for item in _listed(auth_client, environment)["agent"]
            if item["is_default"]
        ]
        assert default["source"] == {
            "kind": "provider",
            "provider": "vapi",
            "assistant_id": "asst-1",
        }
        assert default["prompt"] == _a1(environment).description

    @pytest.mark.parametrize(
        ("edit", "source"),
        [
            (
                lambda p: p["source"].update(
                    visibility="private",
                    installation_id="inst-9917",
                    commit_sha="a" * 40,
                ),
                {
                    "kind": "github",
                    "repository": "acme/agent",
                    "ref": "main",
                    "commit_sha": "a" * 40,
                    "visibility": "private",
                },
            ),
            (_remote, {"kind": "provider", "provider": "remote"}),
            (_archive, {"kind": "archive"}),
        ],
    )
    def test_sources_never_carry_where_or_how_they_are_reached(
        self, auth_client, user, workspace, storage, scheduled, edit, source
    ):
        environment = _environment(user, workspace, "src-read-s", storage, edit=edit)

        [default] = [
            item
            for item in _listed(auth_client, environment)["agent"]
            if item["is_default"]
        ]

        assert default["source"] == source
        assert default["prompt"] is None
        text = str(default)
        for hidden in (
            "installation_id",
            "endpoint",
            "server_url",
            "inst-9917",
            "example",
        ):
            assert hidden not in text
