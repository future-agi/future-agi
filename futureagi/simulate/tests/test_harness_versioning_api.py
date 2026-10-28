import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Organization, Workspace
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HostedHarnessJob,
    RunTest,
)
from simulate.services.harness_provider import serialize_job
from simulate.services.harness_versioning.backfill import backfill_environment
from simulate.services.harness_versioning.versions import record_authoring
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
    _definition,
    _legacy,
    _RealisticStorage,
    _run,
)
from simulate.tests.test_harness_versioning_record import _authoring

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


def _versioned(user, workspace, key, storage, *, definition=None):
    """An environment backfilled to E1/S1, then edited to S2."""
    environment = _legacy(user, workspace, key, storage, definition=definition)
    backfill_environment(environment.id)
    environment.refresh_from_db()
    record_authoring(
        environment,
        _authoring(names=NAMES[:1]),
        origin="manual",
        user=user,
    )
    environment.refresh_from_db()
    return environment


def _get(auth_client, path, **params):
    return auth_client.get(path, params or None)


class TestVersionsList:
    def test_lists_all_three_pieces_newest_first(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-list", storage)

        response = _get(auth_client, f"{BASE}/{environment.id}/versions/")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"agent", "environment", "suite"}
        suites = body["suite"]
        assert [one["label"] for one in suites] == ["v2", "v1"]
        assert [one["is_default"] for one in suites] == [True, False]
        assert suites[0]["parent_id"] == suites[1]["id"]
        assert suites[0]["origin"] == "manual"
        assert suites[1]["origin"] == "backfill"
        assert suites[0]["scenario_count"] == 1
        assert suites[0]["created_by"] == {"id": str(user.id), "name": user.name}
        assert "documents" not in suites[0]
        [environment_version] = body["environment"]
        assert environment_version["label"] == "v1"
        assert environment_version["is_default"] is True
        assert environment_version["scenario_count"] is None
        assert environment_version["derived_from_agent_version_id"] == str(
            environment.agent_version_id
        )

    def test_created_by_never_shows_an_email(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-email", storage)

        body = _get(auth_client, f"{BASE}/{environment.id}/versions/").json()

        assert user.email not in str(body)

    def test_agent_versions_and_their_statuses(
        self, auth_client, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        older = _agent_version(definition, status="archived", description="old")
        current = _agent_version(definition, description="current")
        pending = _agent_version(definition, status="draft", description="next")
        discarded = _agent_version(definition, status="draft", description="gone")
        environment = _versioned(
            user, workspace, "api-agents", storage, definition=definition
        )
        # The backfill gave the default (current) a legacy side row.
        for version, state in ((pending, "pending"), (discarded, "discarded")):
            HarnessAgentVersionRuntime.no_workspace_objects.create(
                agent_version=version,
                agent_definition=definition,
                organization=definition.organization,
                workspace=workspace,
                hash=f"sha256:{state:0>64}"[:71],
                source_kind="provider",
                preflight_status=state,
                trigger="prompt",
                summary=f"{state} change",
            )

        body = _get(auth_client, f"{BASE}/{environment.id}/versions/").json()

        by_id = {one["id"]: one for one in body["agent"]}
        assert [one["id"] for one in body["agent"]] == [
            str(discarded.id),
            str(pending.id),
            str(current.id),
            str(older.id),
        ]
        assert by_id[str(current.id)]["is_default"] is True
        assert by_id[str(current.id)]["status"] == "active"
        assert by_id[str(current.id)]["preflight_status"] == "legacy"
        assert by_id[str(current.id)]["label"] == current.version_name
        assert by_id[str(older.id)]["status"] == "active"
        assert by_id[str(older.id)]["preflight_status"] is None
        assert by_id[str(pending.id)]["status"] == "draft"
        assert by_id[str(pending.id)]["summary"] == "pending change"
        assert by_id[str(discarded.id)]["status"] == "discarded"

    def test_no_versions_yet_is_empty_not_missing(
        self, auth_client, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "api-empty", storage, definition=False)
        RunTest.objects.filter(id=environment.run_test_id).update(agent_definition=None)

        response = _get(auth_client, f"{BASE}/{environment.id}/versions/")

        assert response.status_code == 200
        assert response.json() == {"agent": [], "environment": [], "suite": []}

    def test_versioning_off_is_not_found(
        self, auth_client, user, workspace, storage, settings
    ):
        environment = _versioned(user, workspace, "api-off", storage)
        settings.HARNESS_VERSIONING_ENABLED = False

        response = _get(auth_client, f"{BASE}/{environment.id}/versions/")

        assert response.status_code == 404
        assert response.json()["error"] == "versioning_disabled"

    def test_versioning_off_outranks_every_other_answer(
        self, auth_client, user, workspace, storage, settings
    ):
        environment = _versioned(user, workspace, "api-off-all", storage)
        settings.HARNESS_VERSIONING_ENABLED = False
        unknown = "00000000-0000-0000-0000-000000000000"

        paths = [
            f"{BASE}/{unknown}/versions/",
            f"{BASE}/{environment.id}/versions/suite/"
            f"{environment.suite_version_id}/diff/",
            f"{BASE}/{unknown}/versions/suite/{environment.suite_version_id}/diff/",
            f"{BASE}/{environment.id}/versions/suite/{unknown}/members/",
            f"{BASE}/{environment.id}/versions/suite/"
            f"{environment.suite_version_id}/members/",
        ]

        for path in paths:
            response = _get(auth_client, path)
            assert response.status_code == 404, path
            assert response.json()["error"] == "versioning_disabled", path

    def test_a_run_job_is_not_an_environment(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-run", storage)
        run = _run(environment)

        response = _get(auth_client, f"{BASE}/{run.id}/versions/")

        assert response.status_code == 404
        assert response.json() == {"detail": "Environment not found"}

    def test_another_workspace_is_not_found(
        self, auth_client, user, workspace, storage
    ):
        other = Workspace.objects.create(
            name="Other",
            organization=workspace.organization,
            is_active=True,
            created_by=user,
        )
        environment = _versioned(user, workspace, "api-scope", storage)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            workspace=other
        )

        response = _get(auth_client, f"{BASE}/{environment.id}/versions/")

        assert response.status_code == 404
        assert response.json() == {"detail": "Environment not found"}

    def test_another_organization_is_not_found(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-org", storage)
        other = Organization.objects.create(name="Other org for versions")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            organization=other
        )

        response = _get(auth_client, f"{BASE}/{environment.id}/versions/")

        assert response.status_code == 404

    def test_only_this_definitions_versions_are_listed(
        self, auth_client, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        own = _agent_version(definition)
        environment = _versioned(
            user, workspace, "api-own-agents", storage, definition=definition
        )
        neighbour = _definition(user, workspace, name="Neighbour")
        _agent_version(neighbour, description="neighbour")

        body = _get(auth_client, f"{BASE}/{environment.id}/versions/").json()

        assert [one["id"] for one in body["agent"]] == [str(own.id)]

    def test_a_definition_of_another_organization_is_never_listed(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-foreign", storage)
        foreign_org = Organization.objects.create(name="Foreign org for versions")
        foreign = AgentDefinition.objects.create(
            agent_name="Foreign",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+12345678901",
            inbound=True,
            description="foreign",
            provider="vapi",
            organization=foreign_org,
            languages=["en"],
        )
        stray = AgentVersion.no_workspace_objects.create(
            agent_definition=foreign,
            organization=foreign_org,
            description="foreign",
            commit_message="foreign",
            status="active",
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            agent_version=stray
        )

        body = _get(auth_client, f"{BASE}/{environment.id}/versions/").json()

        assert body["agent"] == []

    def test_a_blank_commit_message_reads_as_no_summary(
        self, auth_client, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        _agent_version(definition)
        environment = _versioned(
            user, workspace, "api-blank-message", storage, definition=definition
        )
        blank = _agent_version(definition, description="blank")
        spaced = _agent_version(definition, description="spaced")
        AgentVersion.no_workspace_objects.filter(id=blank.id).update(
            commit_message="\n"
        )
        AgentVersion.no_workspace_objects.filter(id=spaced.id).update(
            commit_message="  \n  second line"
        )

        response = _get(auth_client, f"{BASE}/{environment.id}/versions/")

        assert response.status_code == 200
        by_id = {one["id"]: one for one in response.json()["agent"]}
        assert by_id[str(blank.id)]["summary"] == ""
        assert by_id[str(spaced.id)]["summary"] == "second line"

    def test_a_blank_side_row_summary_falls_back_to_the_commit_message(
        self, auth_client, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        _agent_version(definition)
        environment = _versioned(
            user, workspace, "api-blank-side-summary", storage, definition=definition
        )
        version = _agent_version(definition, description="later")
        AgentVersion.no_workspace_objects.filter(id=version.id).update(
            commit_message="Rotated the key"
        )
        HarnessAgentVersionRuntime.no_workspace_objects.create(
            agent_version=version,
            agent_definition=definition,
            organization=definition.organization,
            workspace=workspace,
            hash="sha256:" + "e" * 64,
            source_kind="provider",
            preflight_status="passed",
            trigger="credentials",
            summary="   \n  ",
        )

        body = _get(auth_client, f"{BASE}/{environment.id}/versions/").json()

        by_id = {one["id"]: one for one in body["agent"]}
        assert by_id[str(version.id)]["summary"] == "Rotated the key"

    def test_a_draft_has_no_label_and_comes_first(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-draft", storage)
        common = {
            "job": environment,
            "organization_id": environment.organization_id,
            "workspace_id": environment.workspace_id,
            "origin": "manual",
            "archive_object_key": "harness-drafts/x.tar.gz",
            "archive_sha256": "",
            "archive_size": 0,
        }
        HarnessSuiteVersion.no_workspace_objects.create(
            status="draft", hash="sha256:" + "d" * 64, **common
        )
        HarnessEnvironmentVersion.no_workspace_objects.create(
            status="discarded", hash="sha256:" + "e" * 64, **common
        )

        body = _get(auth_client, f"{BASE}/{environment.id}/versions/").json()

        assert body["suite"][0]["label"] is None
        assert body["suite"][0]["status"] == "draft"
        assert [one["label"] for one in body["suite"][1:]] == ["v2", "v1"]
        assert body["environment"][0]["status"] == "discarded"
        assert body["environment"][0]["label"] is None

    def test_the_list_skips_large_columns_and_stays_flat(
        self, auth_client, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        _agent_version(definition)
        environment = _versioned(
            user, workspace, "api-queries", storage, definition=definition
        )
        url = f"{BASE}/{environment.id}/versions/"

        parent = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version_id=environment.agent_version_id
        )

        def child(index):
            more = _agent_version(definition, description=f"more {index}")
            HarnessAgentVersionRuntime.no_workspace_objects.create(
                agent_version=more,
                agent_definition=definition,
                organization=definition.organization,
                workspace=workspace,
                hash=f"sha256:{index:064d}",
                source_kind="provider",
                preflight_status="passed",
                trigger="prompt",
                parent=parent,
                credential_blob="secret-blob",
                agent_payload={"connector": "vapi"},
            )

        child(99)
        with CaptureQueriesContext(connection) as few:
            assert _get(auth_client, url).status_code == 200
        for index in range(4):
            child(index)
            # A build may bring a dropped scenario back; a chat save may not.
            record_authoring(
                environment,
                _authoring(names=NAMES[: 1 + index % 2]),
                origin="authoring",
            )
            environment.refresh_from_db()
        with CaptureQueriesContext(connection) as many:
            assert _get(auth_client, url).status_code == 200

        assert len(many.captured_queries) == len(few.captured_queries)
        runtime_selects = [
            query["sql"]
            for query in many.captured_queries
            if "simulate_harness_agent_version_runtime" in query["sql"]
        ]
        assert runtime_selects
        # Only the payload's asking keys, its source access and the default's prompt
        # are read, never the whole payload; only whether a row has a blob, never
        # the blob.
        runtime_selects = [
            re.sub(
                r"\"agent_payload\" -> '(requested_by_environments"
                r"|created_for_environment|definition_owner|source_access)'"
                r"|\"agent_payload\" #> '\{config,target_system_prompt\}'"
                r"|\"credential_blob\" IS NULL|\"credential_blob\" = ''",
                "",
                sql,
            )
            for sql in runtime_selects
        ]
        for column in ('"credential_blob"', '"agent_payload"'):
            assert all(column not in sql for sql in runtime_selects)
        version_selects = [
            query["sql"]
            for query in many.captured_queries
            if "simulate_harness_environment_version" in query["sql"]
            or "simulate_harness_suite_version" in query["sql"]
        ]
        assert version_selects
        assert all('"documents"' not in sql for sql in version_selects)
        assert all(
            '"configuration_snapshot"' not in query["sql"]
            for query in many.captured_queries
            if "simulate_agent_version" in query["sql"]
        )


class TestVersionDiff:
    def test_against_its_parent(self, auth_client, user, workspace, storage):
        environment = _versioned(user, workspace, "api-diff", storage)
        s2 = HarnessSuiteVersion.no_workspace_objects.get(
            id=environment.suite_version_id
        )

        body = _get(
            auth_client, f"{BASE}/{environment.id}/versions/suite/{s2.id}/diff/"
        ).json()

        assert body["summary"] == s2.summary
        assert [one["key"] for one in body["diff"]["removed"]] == [_key(NAMES[1])]

    def test_a_first_version_has_no_diff(self, auth_client, user, workspace, storage):
        environment = _versioned(user, workspace, "api-first", storage)

        response = _get(
            auth_client,
            f"{BASE}/{environment.id}/versions/environment/"
            f"{environment.environment_version_id}/diff/",
        )

        assert response.status_code == 200
        assert response.json()["diff"] is None

    def test_against_another_suite_version(self, auth_client, user, workspace, storage):
        environment = _versioned(user, workspace, "api-against", storage)
        s2 = HarnessSuiteVersion.no_workspace_objects.get(
            id=environment.suite_version_id
        )
        s1 = s2.parent

        body = _get(
            auth_client,
            f"{BASE}/{environment.id}/versions/suite/{s1.id}/diff/",
            against=str(s2.id),
        ).json()

        assert body["diff"]["added"] == [_key(NAMES[1])]
        assert body["summary"] == "1 scenario added"

    def test_environment_against_itself_has_no_changes(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-env-self", storage)
        e1 = environment.environment_version_id

        body = _get(
            auth_client,
            f"{BASE}/{environment.id}/versions/environment/{e1}/diff/",
            against=str(e1),
        ).json()

        assert body["summary"] == "No contract, sub-goal or world change"
        assert body["diff"]["files"] == {"added": [], "removed": [], "changed": []}

    def test_unknown_world_files_give_no_file_diff(
        self, auth_client, user, workspace, storage
    ):
        missing = _legacy(user, workspace, "api-nofiles", storage, body=False)
        backfill_environment(missing.id)
        missing.refresh_from_db()
        e1 = missing.environment_version_id
        record_authoring(missing, _authoring(), origin="chat")
        missing.refresh_from_db()

        body = _get(
            auth_client,
            f"{BASE}/{missing.id}/versions/environment/"
            f"{missing.environment_version_id}/diff/",
            against=str(e1),
        ).json()

        assert body["diff"]["files"] is None

    def test_a_version_of_another_environment_is_not_found(
        self, auth_client, user, workspace, storage
    ):
        first = _versioned(user, workspace, "api-other-1", storage)
        second = _versioned(user, workspace, "api-other-2", storage)

        own = _get(
            auth_client,
            f"{BASE}/{first.id}/versions/suite/{second.suite_version_id}/diff/",
        )
        against = _get(
            auth_client,
            f"{BASE}/{first.id}/versions/suite/{first.suite_version_id}/diff/",
            against=str(second.suite_version_id),
        )

        assert own.status_code == 404
        assert own.json()["error"] == "version_not_found"
        assert against.status_code == 404
        assert against.json()["error"] == "version_not_found"

    def test_an_unknown_piece_is_not_found(self, auth_client, user, workspace, storage):
        environment = _versioned(user, workspace, "api-piece", storage)

        response = _get(
            auth_client,
            f"{BASE}/{environment.id}/versions/world/"
            f"{environment.environment_version_id}/diff/",
        )

        assert response.status_code == 404
        assert response.json()["error"] == "version_not_found"

    def test_an_unknown_query_parameter_is_refused(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-param", storage)

        response = _get(
            auth_client,
            f"{BASE}/{environment.id}/versions/suite/"
            f"{environment.suite_version_id}/diff/",
            versus="x",
        )

        assert response.status_code == 400

    def test_an_agent_diff_is_names_only(self, auth_client, user, workspace, storage):
        definition = _definition(user, workspace)
        first = _agent_version(definition, description="one")
        environment = _versioned(
            user, workspace, "api-agent-diff", storage, definition=definition
        )
        # The backfill made A1 (first) a legacy row named by the payload's refs.
        legacy = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version=first
        )
        second = _agent_version(definition, description="two")
        HarnessAgentVersionRuntime.no_workspace_objects.create(
            agent_version=second,
            agent_definition=definition,
            organization=definition.organization,
            workspace=workspace,
            hash="sha256:" + "2" * 64,
            source_kind=legacy.source_kind,
            source=legacy.source,
            prompt_hash="sha256:" + "b" * 64,
            credential_names=[*legacy.credential_names, "REGION"],
            preflight_status="passed",
            trigger="prompt",
        )

        body = _get(
            auth_client,
            f"{BASE}/{environment.id}/versions/agent/{second.id}/diff/",
            against=str(first.id),
        ).json()
        alone = _get(
            auth_client, f"{BASE}/{environment.id}/versions/agent/{first.id}/diff/"
        ).json()

        assert body["diff"]["prompt"] is True
        assert body["diff"]["source"] is False
        assert body["diff"]["variables"]["added"] == ["REGION"]
        assert body["diff"]["variables"]["removed"] == []
        assert alone["diff"] is None
        assert "harness-livekit" not in str(body)

    def test_a_legacy_row_against_fingerprints_compares_names_only(
        self, auth_client, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        first = _agent_version(definition, description="one")
        environment = _versioned(
            user, workspace, "api-agent-names", storage, definition=definition
        )
        legacy = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version=first
        )
        second = _agent_version(definition, description="two")
        fingerprints = dict.fromkeys(legacy.credential_names, "sha256:" + "f" * 64)
        fingerprints["REGION"] = "sha256:" + "0" * 64
        HarnessAgentVersionRuntime.no_workspace_objects.create(
            agent_version=second,
            agent_definition=definition,
            organization=definition.organization,
            workspace=workspace,
            hash="sha256:" + "3" * 64,
            source_kind=legacy.source_kind,
            source=legacy.source,
            prompt_hash=legacy.prompt_hash,
            credential_names=sorted(fingerprints),
            credential_fingerprints=fingerprints,
            preflight_status="passed",
            trigger="credentials",
        )

        body = _get(
            auth_client,
            f"{BASE}/{environment.id}/versions/agent/{second.id}/diff/",
            against=str(first.id),
        ).json()

        assert body["diff"]["variables"] == {
            "added": ["REGION"],
            "removed": [],
            "changed": [],
        }
        assert body["diff"]["prompt"] is False


class TestSuiteMembers:
    def test_members_by_number(self, auth_client, user, workspace, storage):
        environment = _versioned(user, workspace, "api-members", storage)
        s1 = HarnessSuiteVersion.no_workspace_objects.get(
            id=environment.suite_version_id
        ).parent

        response = _get(
            auth_client, f"{BASE}/{environment.id}/versions/suite/{s1.id}/members/"
        )

        assert response.status_code == 200
        members = response.json()
        assert [one["scenario_key"] for one in members] == [
            _key(name) for name in NAMES[:2]
        ]
        assert [one["number"] for one in members] == sorted(
            one["number"] for one in members
        )
        assert set(members[0]) == {"scenario_key", "number", "name", "use_case"}
        assert members[0]["name"] == NAMES[0]

    def test_an_environment_version_id_is_not_a_suite(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-members-404", storage)

        response = _get(
            auth_client,
            f"{BASE}/{environment.id}/versions/suite/"
            f"{environment.environment_version_id}/members/",
        )

        assert response.status_code == 404
        assert response.json()["error"] == "version_not_found"


class TestVersioningFlags:
    @pytest.mark.parametrize(
        ("enabled", "nudges", "expected"),
        [
            (False, False, {"enabled": False, "nudges_enabled": False}),
            (False, True, {"enabled": False, "nudges_enabled": False}),
            (True, False, {"enabled": True, "nudges_enabled": False}),
            (True, True, {"enabled": True, "nudges_enabled": True}),
        ],
    )
    def test_the_job_detail_carries_the_flags(
        self, user, workspace, storage, settings, enabled, nudges, expected
    ):
        settings.HARNESS_VERSIONING_ENABLED = enabled
        settings.HARNESS_VERSIONING_NUDGES_ENABLED = nudges
        environment = _legacy(user, workspace, f"api-flags-{enabled}-{nudges}", storage)

        assert serialize_job(environment)["versioning"] == expected


class TestErrorHandling:
    def test_an_unknown_environment_keeps_the_existing_not_found(self, auth_client):
        response = _get(
            auth_client, f"{BASE}/00000000-0000-0000-0000-000000000000/versions/"
        )

        assert response.status_code == 404
        assert response.json() == {"detail": "Environment not found"}


class TestSecondReviewRound:
    def test_a_soft_deleted_pinned_agent_version_is_still_the_default(
        self, auth_client, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        pinned = _agent_version(definition)
        environment = _versioned(
            user, workspace, "api-pinned-deleted", storage, definition=definition
        )
        AgentVersion.objects.filter(id=pinned.id).update(deleted=True)

        body = _get(auth_client, f"{BASE}/{environment.id}/versions/").json()

        assert [(one["id"], one["is_default"]) for one in body["agent"]] == [
            (str(pinned.id), True)
        ]

    def test_a_soft_deleted_parent_side_row_is_not_reported(
        self, auth_client, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        first = _agent_version(definition, description="one")
        environment = _versioned(
            user, workspace, "api-deleted-parent", storage, definition=definition
        )
        parent = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version=first
        )
        second = _agent_version(definition, description="two")
        HarnessAgentVersionRuntime.no_workspace_objects.create(
            agent_version=second,
            agent_definition=definition,
            organization=definition.organization,
            workspace=workspace,
            hash="sha256:" + "4" * 64,
            source_kind="provider",
            preflight_status="passed",
            trigger="prompt",
            parent=parent,
        )
        HarnessAgentVersionRuntime.no_workspace_objects.filter(id=parent.id).update(
            deleted=True
        )

        body = _get(auth_client, f"{BASE}/{environment.id}/versions/").json()

        by_id = {one["id"]: one for one in body["agent"]}
        assert by_id[str(second.id)]["parent_id"] is None
        # A soft-deleted side row reads the same as no side row.
        assert by_id[str(first.id)]["preflight_status"] is None
        assert by_id[str(first.id)]["status"] == "active"

    def test_soft_deleted_versions_and_members_are_left_out(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-deleted-rows", storage)
        s2 = HarnessSuiteVersion.no_workspace_objects.get(
            id=environment.suite_version_id
        )
        s1_id = s2.parent_id
        HarnessSuiteVersion.no_workspace_objects.filter(id=s1_id).update(deleted=True)
        hidden = HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=s2
        ).first()
        HarnessSuiteMember.no_workspace_objects.filter(id=hidden.id).update(
            deleted=True
        )

        listed = _get(auth_client, f"{BASE}/{environment.id}/versions/").json()
        members = _get(
            auth_client, f"{BASE}/{environment.id}/versions/suite/{s2.id}/members/"
        ).json()
        gone = _get(
            auth_client, f"{BASE}/{environment.id}/versions/suite/{s1_id}/members/"
        )

        assert [one["id"] for one in listed["suite"]] == [str(s2.id)]
        assert hidden.scenario_key not in [one["scenario_key"] for one in members]
        assert gone.status_code == 404
        assert gone.json()["error"] == "version_not_found"

    def test_members_of_another_environments_suite_are_not_found(
        self, auth_client, user, workspace, storage
    ):
        first = _versioned(user, workspace, "api-members-a", storage)
        second = _versioned(user, workspace, "api-members-b", storage)

        response = _get(
            auth_client,
            f"{BASE}/{first.id}/versions/suite/{second.suite_version_id}/members/",
        )

        assert response.status_code == 404
        assert response.json()["error"] == "version_not_found"

    def test_members_read_no_large_columns(self, auth_client, user, workspace, storage):
        environment = _versioned(user, workspace, "api-members-light", storage)
        url = (
            f"{BASE}/{environment.id}/versions/suite/"
            f"{environment.suite_version_id}/members/"
        )

        with CaptureQueriesContext(connection) as queries:
            assert _get(auth_client, url).status_code == 200

        version_selects = [
            query["sql"]
            for query in queries.captured_queries
            if "simulate_harness_suite_version" in query["sql"]
            and "simulate_harness_suite_member" not in query["sql"]
        ]
        assert version_selects
        assert all('"documents"' not in sql for sql in version_selects)
        assert all('"snapshot_diff"' not in sql for sql in version_selects)

    def test_a_dashed_id_of_the_right_length_is_not_found(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-dashes", storage)
        dashes = "-" * 36

        diff = _get(
            auth_client, f"{BASE}/{environment.id}/versions/suite/{dashes}/diff/"
        )
        members = _get(
            auth_client, f"{BASE}/{environment.id}/versions/suite/{dashes}/members/"
        )

        for response in (diff, members):
            assert response.status_code == 404
            assert response.json()["error"] == "version_not_found"

    def test_an_agent_version_of_another_definition_is_not_found(
        self, auth_client, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        _agent_version(definition)
        environment = _versioned(
            user, workspace, "api-agent-other-def", storage, definition=definition
        )
        neighbour = _definition(user, workspace, name="Neighbour")
        foreign = _agent_version(neighbour, description="neighbour")

        response = _get(
            auth_client, f"{BASE}/{environment.id}/versions/agent/{foreign.id}/diff/"
        )

        assert response.status_code == 404
        assert response.json()["error"] == "version_not_found"

    def test_an_agent_diff_without_against_returns_the_stored_record(
        self, auth_client, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        _agent_version(definition)
        environment = _versioned(
            user, workspace, "api-agent-stored", storage, definition=definition
        )
        second = _agent_version(definition, description="two")
        stored = {"source": False, "prompt": True, "variables": {}}
        HarnessAgentVersionRuntime.no_workspace_objects.create(
            agent_version=second,
            agent_definition=definition,
            organization=definition.organization,
            workspace=workspace,
            hash="sha256:" + "5" * 64,
            source_kind="provider",
            preflight_status="passed",
            trigger="prompt",
            summary="Prompt changed",
            diff=stored,
        )

        body = _get(
            auth_client, f"{BASE}/{environment.id}/versions/agent/{second.id}/diff/"
        ).json()

        assert body == {"summary": "Prompt changed", "diff": stored}

    def test_a_suite_or_agent_against_itself_has_no_changes(
        self, auth_client, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        agent = _agent_version(definition)
        environment = _versioned(
            user, workspace, "api-self", storage, definition=definition
        )
        suite = environment.suite_version_id

        suite_body = _get(
            auth_client,
            f"{BASE}/{environment.id}/versions/suite/{suite}/diff/",
            against=str(suite),
        )
        agent_body = _get(
            auth_client,
            f"{BASE}/{environment.id}/versions/agent/{agent.id}/diff/",
            against=str(agent.id),
        )

        assert suite_body.status_code == 200
        assert suite_body.json()["diff"]["added"] == []
        assert suite_body.json()["diff"]["removed"] == []
        assert not suite_body.json()["diff"]["changed"]
        assert agent_body.status_code == 200
        assert agent_body.json()["diff"]["source"] is False
        assert agent_body.json()["diff"]["prompt"] is False

    def test_odd_documents_never_fail_a_diff(
        self, auth_client, user, workspace, storage, caplog
    ):
        environment = _versioned(user, workspace, "api-odd", storage)
        e1 = environment.environment_version_id
        s2 = HarnessSuiteVersion.no_workspace_objects.get(
            id=environment.suite_version_id
        )
        HarnessEnvironmentVersion.no_workspace_objects.filter(id=e1).update(
            documents={
                "contract": ["not", "a", "contract"],
                "sub_goal_digests": ["a", "list"],
                "file_digests": "nope",
            }
        )
        HarnessSuiteVersion.no_workspace_objects.filter(id=s2.id).update(
            documents={"scenarios": ["text", 3, None, {"name": "kept"}]}
        )
        HarnessSuiteMember.no_workspace_objects.filter(suite_version=s2).update(
            content=["not", "a", "mapping"]
        )

        with caplog.at_level("WARNING", logger="simulate.services.harness_versioning"):
            environment_diff = _get(
                auth_client,
                f"{BASE}/{environment.id}/versions/environment/{e1}/diff/",
                against=str(e1),
            )
            suite_diff = _get(
                auth_client,
                f"{BASE}/{environment.id}/versions/suite/{s2.id}/diff/",
                against=str(s2.parent_id),
            )
            members = _get(
                auth_client, f"{BASE}/{environment.id}/versions/suite/{s2.id}/members/"
            )

        assert environment_diff.status_code == 200
        assert environment_diff.json()["diff"]["files"] is None
        assert suite_diff.status_code == 200
        assert members.status_code == 200
        assert all(one["name"] == "" for one in members.json())
        warned = {
            record.getMessage()
            for record in caplog.records
            if "harness version document malformed" in record.getMessage()
        }
        for field in (
            "contract",
            "sub_goal_digests",
            "file_digests",
            "scenarios[]",
            "members.content",
        ):
            assert any(f"field={field}" in message for message in warned), field
        assert any(f"version={e1}" in message for message in warned)
        # Names and fields only: never the stored values.
        assert not any("not a contract" in message for message in warned)

    def test_the_job_detail_endpoint_carries_the_flags(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "api-detail-flags", storage)
        run = _run(environment)

        response = auth_client.get(f"/simulate/api/harness-jobs/{environment.id}/")

        assert response.status_code == 200
        assert response.json()["versioning"] == {
            "enabled": True,
            "nudges_enabled": False,
        }
        assert serialize_job(run)["versioning"]["enabled"] is True
