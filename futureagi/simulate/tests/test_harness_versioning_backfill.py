import io
import json
import tarfile
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from minio.error import S3Error

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HostedHarnessJob,
    HostedHarnessScenario,
    RunTest,
)
from simulate.models import TestExecution as Execution
from simulate.services.harness_versioning import archives, backfill, hashing
from simulate.services.harness_versioning.backfill import (
    _archive_scenarios,
    backfill_all,
    backfill_environment,
    legacy_source,
    match_members,
)
from simulate.services.harness_versioning.versions import record_authoring
from simulate.services.hosted_harness import create_selected_harness_run
from simulate.services.hosted_harness_gateway import (
    authoring_content_digest,
    rewrite_authoring_scenarios,
)
from simulate.tests.test_harness_amend_archive import NAMES, _key, _run_environment
from simulate.tests.test_harness_versioning_record import _authoring
from simulate.tests.test_hosted_harness_conversation import _Storage

pytestmark = pytest.mark.django_db

_BUNDLE_DIGEST = "sha256:" + "d" * 64
# A sealed bundle and the certificate for it.
_CERTIFIED = {
    "environment-bundle/manifest.json": json.dumps(
        {"files": [], "digest": _BUNDLE_DIGEST}
    ),
    "runtime-validation.json": json.dumps(
        {"status": "passed", "compiler": {"bundle_digest": _BUNDLE_DIGEST}}
    ),
}

KEYED = "harness-authoring/org/env/" + "a" * 64 + ".tar.gz"


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


class _RealisticStorage(_Storage):
    """In-memory storage that, like the real one, raises ``NoSuchKey`` when an object is gone."""

    def get_object(self, bucket, object_name):
        if object_name not in self.objects:
            raise S3Error(None, "NoSuchKey", "missing", object_name, "request", "host")
        return super().get_object(bucket, object_name)

    def stat_object(self, bucket, object_name):
        if object_name not in self.objects:
            raise S3Error(None, "NoSuchKey", "missing", object_name, "request", "host")
        return object_name


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


def _definition(user, workspace, name="Support agent"):
    return AgentDefinition.objects.create(
        agent_name=name,
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12345678901",
        inbound=True,
        description="support",
        provider="vapi",
        organization=user.organization,
        workspace=workspace,
        languages=["en"],
    )


def _agent_version(definition, *, status="active", description="prompt v1"):
    return AgentVersion.no_workspace_objects.create(
        agent_definition=definition,
        organization=definition.organization,
        workspace=definition.workspace,
        description=description,
        commit_message=description,
        status=status,
    )


def _with_metadata(job, **values):
    payload = dict(job.payload)
    metadata = dict(payload.get("metadata") or {})
    for name, value in values.items():
        if value is None:
            metadata.pop(name, None)
        else:
            metadata[name] = value
    payload["metadata"] = metadata
    job.payload = payload
    job.save(update_fields=["payload", "updated_at"])


def _legacy(user, workspace, key, storage, *, body=None, definition=None, **metadata):
    """An environment built before versioning: one combined archive, no versions."""
    environment = _run_environment(user, workspace, key)
    environment.refresh_from_db()
    body = _authoring() if body is None else body
    object_key = environment.payload["metadata"]["authoring_object_key"]
    if body is not False:
        storage.objects[object_key] = body
    if "authoring_revision" not in metadata:
        try:
            metadata["authoring_revision"] = (
                authoring_content_digest(body) if body else "sha256:" + "b" * 64
            )
        except Exception:  # noqa: BLE001 - bytes that are not an archive
            metadata["authoring_revision"] = "sha256:" + "9" * 64
    _with_metadata(environment, **metadata)
    if definition is not False:
        if definition is None:
            definition = _definition(user, workspace)
            _agent_version(definition)
        RunTest.objects.filter(id=environment.run_test_id).update(
            agent_definition=definition, agent_version=None
        )
    environment.refresh_from_db()
    return environment


def _versions(model, environment):
    return list(
        model.no_workspace_objects.filter(job=environment).order_by("version_number")
    )


def _run(environment, key="r", *, state=HostedHarnessJob.State.COMPLETED):
    run, _ = create_selected_harness_run(
        environment, scenario_keys=[_key(NAMES[0])], trials=1, idempotency_key=key
    )
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(state=state)
    run.refresh_from_db()
    return run


class TestFirstBackfill:
    def test_records_split_e1_s1_as_the_defaults(self, user, workspace, storage):
        environment = _legacy(user, workspace, "bf-first", storage)
        key = environment.payload["metadata"]["authoring_object_key"]

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert (result.outcome, result.layout) == ("versioned", "split")
        [e1] = _versions(HarnessEnvironmentVersion, environment)
        [s1] = _versions(HarnessSuiteVersion, environment)
        assert environment.environment_version_id == e1.id
        assert environment.suite_version_id == s1.id
        for version in (e1, s1):
            assert (version.version_number, version.status, version.origin) == (
                1,
                "active",
                "backfill",
            )
            assert (version.hash_kind, version.archive_layout) == ("tree_v1", "split")
            body = storage.objects[version.archive_object_key]
            assert archives.sha256_hex(body) == version.archive_sha256
            assert version.documents["authoring_revisions"] == [
                environment.payload["metadata"]["authoring_revision"]
            ]
        assert s1.authored_against_id == e1.id
        assert "schema.sql" in e1.documents["file_digests"]
        # The environment keeps reading the archive it always read.
        assert environment.payload["metadata"]["authoring_object_key"] == key

    def test_hashes_match_what_a_fresh_authoring_records(
        self, user, workspace, storage
    ):
        legacy = _legacy(user, workspace, "bf-hash-legacy", storage)
        fresh = _legacy(user, workspace, "bf-hash-fresh", storage, definition=False)

        backfill_environment(legacy.id)
        recorded = record_authoring(fresh, _authoring(), origin="authoring")

        legacy.refresh_from_db()
        assert legacy.environment_version.hash == recorded.environment.hash
        assert legacy.suite_version.hash == recorded.suite.hash
        assert legacy.suite_version.certify_hash == recorded.suite.certify_hash

    def test_members_are_the_live_scenario_rows_only(self, user, workspace, storage):
        environment = _legacy(user, workspace, "bf-members", storage)
        dropped = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=_key(NAMES[1])
        )
        dropped.deleted = True
        dropped.save(update_fields=["deleted", "updated_at"])

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        members = HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=environment.suite_version
        )
        assert [member.scenario_key for member in members] == [_key(NAMES[0])]
        # The suite is what the archive holds; the dropped row is noted, not counted.
        assert environment.suite_version.scenario_count == 2
        assert "1 archive scenarios without a live row" in result.notes
        member = members.get()
        assert member.number >= 1
        assert member.scenario.scenario_key == _key(NAMES[0])
        assert member.content["instruction"] == f"call about {NAMES[0]}"
        # Nothing was re-indexed: the dropped row stays dropped and untouched.
        assert HostedHarnessScenario.all_objects.get(id=dropped.id).deleted

    def test_a_second_run_changes_nothing(self, user, workspace, storage):
        environment = _legacy(user, workspace, "bf-again", storage)
        backfill_environment(environment.id)
        environment.refresh_from_db()
        before = (environment.environment_version_id, environment.suite_version_id)

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "current"
        assert (
            environment.environment_version_id,
            environment.suite_version_id,
        ) == before
        assert len(_versions(HarnessEnvironmentVersion, environment)) == 1
        assert len(_versions(HarnessSuiteVersion, environment)) == 1
        assert HarnessAgentVersionRuntime.no_workspace_objects.count() == 1

    def test_a_later_save_reuses_the_backfilled_versions(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-then-save", storage)
        backfill_environment(environment.id)
        environment.refresh_from_db()

        recorded = record_authoring(environment, _authoring(), origin="chat")

        assert recorded.environment.id == environment.environment_version_id
        assert recorded.suite.id == environment.suite_version_id
        assert len(_versions(HarnessSuiteVersion, environment)) == 1

    def test_a_scenario_edit_after_backfill_makes_s2_on_top_of_s1(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-then-edit", storage)
        backfill_environment(environment.id)
        environment.refresh_from_db()
        s1 = environment.suite_version

        rewrite_authoring_scenarios(
            environment,
            [{"scenario_key": _key(NAMES[0]), "instruction": "call about a refund"}],
        )

        environment.refresh_from_db()
        s2 = environment.suite_version
        assert (s2.version_number, s2.parent_id) == (2, s1.id)
        assert environment.environment_version.version_number == 1


class TestWithoutTheArchive:
    def test_a_missing_archive_keeps_a_combined_version_on_its_revision(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-missing", storage, body=False)
        key = environment.payload["metadata"]["authoring_object_key"]
        revision = environment.payload["metadata"]["authoring_revision"]

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert (result.outcome, result.layout) == ("versioned", "combined")
        assert "archive missing" in result.notes
        for version in (environment.environment_version, environment.suite_version):
            assert (version.hash, version.hash_kind) == (revision, "legacy_combined")
            assert version.archive_object_key == key
            assert version.archive_size == 0
        assert environment.environment_version.documents["contract"] == {}
        assert not result.bundle_recorded

    def test_a_key_without_a_revision_hashes_by_the_keys_digest(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-key-only", storage, body=False)
        _with_metadata(environment, authoring_object_key=KEYED, authoring_revision=None)

        backfill_environment(environment.id)

        environment.refresh_from_db()
        version = environment.environment_version
        assert (version.hash, version.hash_kind) == ("sha256:" + "a" * 64, "legacy_key")
        assert version.archive_sha256 == "a" * 64

    def test_a_key_without_a_revision_but_with_its_archive_is_split(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-no-revision", storage)
        _with_metadata(environment, authoring_revision=None)
        body = storage.objects[environment.payload["metadata"]["authoring_object_key"]]

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.layout == "split"
        assert environment.environment_version.documents["authoring_revisions"] == [
            authoring_content_digest(body)
        ]

    def test_an_environment_without_an_archive_is_left_alone(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-none", storage)
        _with_metadata(environment, authoring_object_key="")

        assert backfill_environment(environment.id).outcome == "no_archive"
        environment.refresh_from_db()
        assert environment.environment_version_id is None
        assert environment.agent_version_id is None

    def test_a_run_job_is_not_an_environment(self, user, workspace, storage):
        environment = _legacy(user, workspace, "bf-run-job", storage)
        run = _run(environment)

        assert backfill_environment(run.id).outcome == "not_environment"


class TestAgent:
    def test_pins_the_definitions_active_version_as_a1(self, user, workspace, storage):
        definition = _definition(user, workspace)
        _agent_version(definition, status="archived", description="old")
        active = _agent_version(definition, description="current")
        _agent_version(definition, status="draft", description="draft")
        environment = _legacy(
            user, workspace, "bf-agent", storage, definition=definition
        )

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.agent_pinned
        assert environment.agent_version_id == active.id
        assert environment.environment_version.agent_version_id == active.id
        assert result.agent_source == "run_test"
        # Empty means "use the latest version"; filling it would freeze future runs.
        assert RunTest.objects.get(id=environment.run_test_id).agent_version_id is None
        # No owner marker: an absent owner already means a legacy environment.
        assert "agent_definition_owner" not in environment.payload["metadata"]

    def test_falls_back_to_the_latest_version_without_an_active_one(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        _agent_version(definition, status="draft", description="one")
        latest = _agent_version(definition, status="draft", description="two")
        environment = _legacy(
            user, workspace, "bf-latest", storage, definition=definition
        )

        backfill_environment(environment.id)

        environment.refresh_from_db()
        assert environment.agent_version_id == latest.id

    def test_the_bound_definition_outranks_the_run_tests(
        self, user, workspace, storage
    ):
        bound = _definition(user, workspace, name="Bound")
        chosen = _agent_version(bound)
        environment = _legacy(
            user,
            workspace,
            "bf-bound",
            storage,
            agent_definition_id=str(bound.id),
        )
        _agent_version(environment.run_test.agent_definition)

        backfill_environment(environment.id)

        environment.refresh_from_db()
        assert environment.agent_version_id == chosen.id

    def test_the_side_row_keeps_refs_and_names_never_values(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        version = _agent_version(definition)
        environment = _legacy(
            user, workspace, "bf-side-row", storage, definition=definition
        )
        payload = dict(environment.payload)
        payload["source"] = {
            **payload["source"],
            "environment_values": {"API_TOKEN": "s3cr3t-value"},
        }
        refs = payload["agent"]["secret_refs"]
        environment.payload = payload
        environment.save(update_fields=["payload", "updated_at"])

        backfill_environment(environment.id)

        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version=version
        )
        assert runtime.preflight_status == "legacy"
        assert runtime.trigger == "backfill"
        assert runtime.agent_payload["legacy_secret_refs"] == refs
        assert runtime.credential_names == sorted(refs)
        assert runtime.credential_blob is None
        assert runtime.required_variable_names is None
        assert runtime.source_kind == "github"
        assert runtime.source == {"repository": "acme/agent", "ref": "main"}
        assert runtime.hash.startswith("sha256:")
        stored = json.dumps(
            [
                runtime.source,
                runtime.agent_payload,
                environment.payload["metadata"],
                list(
                    HarnessEnvironmentVersion.no_workspace_objects.values("documents")
                ),
            ],
            default=str,
        )
        assert "s3cr3t-value" not in stored

    def test_two_environments_on_one_version_share_its_side_row(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        version = _agent_version(definition)
        first = _legacy(user, workspace, "bf-share-1", storage, definition=definition)
        second = _legacy(user, workspace, "bf-share-2", storage, definition=definition)

        backfill_environment(first.id)
        backfill_environment(second.id)

        assert (
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version=version
            ).count()
            == 1
        )
        second.refresh_from_db()
        assert second.agent_version_id == version.id

    def test_two_versions_with_the_same_facts_share_their_identity_hash(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        older = _agent_version(definition, description="same")
        first = _legacy(user, workspace, "bf-same-1", storage, definition=definition)
        backfill_environment(first.id)
        newer = _agent_version(definition, description="same")
        second = _legacy(user, workspace, "bf-same-2", storage, definition=definition)

        backfill_environment(second.id)

        rows = {
            row.agent_version_id: row
            for row in HarnessAgentVersionRuntime.no_workspace_objects.all()
        }
        assert set(rows) == {older.id, newer.id}
        # Legacy rows sit outside the hash's unique constraint, so neither is salted.
        for row in rows.values():
            assert row.hash == hashing.agent_hash(backfill.runtime_identity(row))
        assert rows[older.id].hash == rows[newer.id].hash

    def test_an_environment_with_no_agent_still_gets_versions(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-no-agent", storage)
        RunTest.objects.filter(id=environment.run_test_id).update(agent_definition=None)

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "versioned"
        assert environment.agent_version_id is None
        assert "no agent version to pin" in result.notes

    def test_an_existing_default_agent_is_kept(self, user, workspace, storage):
        definition = _definition(user, workspace)
        _agent_version(definition)
        kept = _agent_version(definition, status="archived", description="kept")
        environment = _legacy(
            user, workspace, "bf-kept-agent", storage, definition=definition
        )
        environment.agent_version = kept
        environment.save(update_fields=["agent_version", "updated_at"])

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert not result.agent_pinned
        assert environment.agent_version_id == kept.id


class TestRuns:
    def test_a_run_on_the_same_archive_is_pinned_to_e1_s1_and_a1(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        a1 = _agent_version(definition)
        environment = _legacy(user, workspace, "bf-run", storage, definition=definition)
        run = _run(environment)

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        run.refresh_from_db()
        assert result.runs_pinned == 1
        assert run.environment_version_id == environment.environment_version_id
        assert run.suite_version_id == environment.suite_version_id
        assert run.agent_version_id == a1.id
        execution = Execution.objects.get(id=run.test_execution_id)
        assert execution.agent_version_id == a1.id

    def test_a_run_on_an_older_archive_keeps_only_its_revision(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-run-old", storage)
        run = _run(environment)
        _with_metadata(run, authoring_revision="sha256:" + "c" * 64)

        result = backfill_environment(environment.id)

        run.refresh_from_db()
        assert result.runs_unmatched == 1
        assert run.environment_version_id is None
        assert run.suite_version_id is None
        assert run.payload["metadata"]["version_pins"] == {
            "legacy_revision": "sha256:" + "c" * 64
        }

    def test_an_unmatched_run_takes_the_agent_version_it_ran_under(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        a1 = _agent_version(definition, description="one")
        environment = _legacy(
            user, workspace, "bf-run-agent", storage, definition=definition
        )
        run = _run(environment)
        _with_metadata(run, authoring_revision="sha256:" + "c" * 64)
        later = timezone.now() + timedelta(minutes=5)
        a2 = _agent_version(definition, description="two")
        a3 = _agent_version(definition, description="three")
        AgentVersion.objects.filter(id__in=[a2.id, a3.id]).update(created_at=later)

        backfill_environment(environment.id)

        run.refresh_from_db()
        environment.refresh_from_db()
        assert environment.agent_version_id == a3.id
        assert run.agent_version_id == a1.id

    def test_a_runs_own_agent_version_wins(self, user, workspace, storage):
        definition = _definition(user, workspace)
        _agent_version(definition)
        ran_with = _agent_version(definition, status="archived", description="ran")
        environment = _legacy(
            user, workspace, "bf-run-own", storage, definition=definition
        )
        run = _run(environment)
        Execution.objects.filter(id=run.test_execution_id).update(
            agent_version=ran_with
        )

        backfill_environment(environment.id)

        run.refresh_from_db()
        assert run.agent_version_id == ran_with.id

    def test_a_run_after_a_later_edit_pins_to_the_versions_it_replayed(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-run-edit", storage)
        backfill_environment(environment.id)
        rewrite_authoring_scenarios(
            environment,
            [{"scenario_key": _key(NAMES[0]), "instruction": "call about a refund"}],
        )
        environment.refresh_from_db()
        run = _run(environment, key="after-edit")

        backfill_environment(environment.id)

        run.refresh_from_db()
        environment.refresh_from_db()
        assert run.suite_version_id == environment.suite_version_id
        assert run.suite_version.version_number == 2
        # S2 reused E1, and E1 now also stands for the edited archive.
        assert run.environment_version_id == environment.environment_version_id


class TestReRuns:
    def test_an_archive_changed_since_the_backfill_becomes_a_new_version(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-drift", storage)
        backfill_environment(environment.id)
        environment.refresh_from_db()
        s1 = environment.suite_version
        changed = _authoring(names=NAMES[:1])
        key = "harness-authoring/drift/" + archives.sha256_hex(changed) + ".tar.gz"
        storage.objects[key] = changed
        _with_metadata(
            environment,
            authoring_object_key=key,
            authoring_revision=authoring_content_digest(changed),
        )

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "appended"
        s2 = environment.suite_version
        assert (s2.version_number, s2.origin, s2.parent_id) == (2, "backfill", s1.id)
        assert environment.environment_version.version_number == 1
        assert authoring_content_digest(changed) in (
            environment.environment_version.documents["authoring_revisions"]
        )


class TestBundle:
    def test_the_archives_sealed_bundle_seeds_the_cache(self, user, workspace, storage):
        definition = _definition(user, workspace)
        a1 = _agent_version(definition)
        environment = _legacy(
            user,
            workspace,
            "bf-bundle",
            storage,
            definition=definition,
            body=_authoring(**_CERTIFIED),
        )

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.bundle_recorded
        bundle = HarnessAssembledBundle.no_workspace_objects.get(job=environment)
        assert environment.assembled_bundle_id == bundle.id
        assert (bundle.state, bundle.agent_version_id) == ("ready", a1.id)
        assert bundle.environment_version_id == environment.environment_version_id
        assert bundle.suite_version_id == environment.suite_version_id
        assert bundle.certificate == {
            "status": "passed",
            "compiler": {"bundle_digest": _BUNDLE_DIGEST},
        }
        files = archives.read_archive(storage.objects[bundle.object_key])
        assert "environment-bundle/manifest.json" in files
        assert bundle.assembly_key.startswith("sha256:")

    def test_no_cache_row_without_a_certificate_or_an_agent(
        self, user, workspace, storage
    ):
        unsealed = _legacy(
            user, workspace, "bf-unsealed", storage, body=_authoring(sealed=False)
        )
        no_agent = _legacy(user, workspace, "bf-bundle-no-agent", storage)
        RunTest.objects.filter(id=no_agent.run_test_id).update(agent_definition=None)
        _agent_version(unsealed.run_test.agent_definition)

        assert not backfill_environment(unsealed.id).bundle_recorded
        assert not backfill_environment(no_agent.id).bundle_recorded
        assert not HarnessAssembledBundle.no_workspace_objects.exists()


class TestReviewFindings:
    def test_a_run_without_a_revision_gets_no_pin(self, user, workspace, storage):
        environment = _legacy(user, workspace, "bf-run-norev", storage)
        run = _run(environment)
        _with_metadata(run, authoring_revision=None)

        result = backfill_environment(environment.id)
        again = backfill_environment(environment.id)

        run.refresh_from_db()
        assert (result.runs_unmatched, again.runs_unmatched) == (0, 0)
        assert "version_pins" not in run.payload["metadata"]

    def test_an_unmatched_run_is_counted_once(self, user, workspace, storage):
        environment = _legacy(user, workspace, "bf-run-once", storage)
        run = _run(environment)
        _with_metadata(run, authoring_revision="sha256:" + "c" * 64)

        assert backfill_environment(environment.id).runs_unmatched == 1
        assert backfill_environment(environment.id).runs_unmatched == 0

    def test_a_run_without_its_test_execution_is_still_pinned(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-run-noexec", storage)
        run = _run(environment)
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
            test_execution=None
        )

        backfill_environment(environment.id)

        run.refresh_from_db()
        assert run.environment_version_id is not None
        assert (
            run.agent_version_id
            == environment.run_test.agent_definition.versions.get().id
        )

    def test_a_missing_object_in_real_storage_falls_back(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _legacy(user, workspace, "bf-nosuchkey", storage)

        def missing(_key):
            raise S3Error(None, "NoSuchKey", "gone", "resource", "request", "host")

        monkeypatch.setattr(archives, "get_archive", missing)

        result = backfill_environment(environment.id)

        assert (result.outcome, result.layout) == ("versioned", "combined")
        assert "archive missing" in result.notes

    def test_a_soft_deleted_side_row_is_reused(self, user, workspace, storage):
        definition = _definition(user, workspace)
        version = _agent_version(definition)
        first = _legacy(user, workspace, "bf-deleted-1", storage, definition=definition)
        backfill_environment(first.id)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version=version
        ).update(deleted=True)
        second = _legacy(
            user, workspace, "bf-deleted-2", storage, definition=definition
        )

        result = backfill_environment(second.id)

        second.refresh_from_db()
        assert result.agent_pinned
        assert second.agent_version_id == version.id
        assert HarnessAgentVersionRuntime._base_manager.count() == 1

    def test_a_drift_records_its_diff(self, user, workspace, storage):
        environment = _legacy(user, workspace, "bf-drift-diff", storage)
        backfill_environment(environment.id)
        changed = _authoring(names=NAMES[:1])
        key = "harness-authoring/drift/" + archives.sha256_hex(changed) + ".tar.gz"
        storage.objects[key] = changed
        _with_metadata(
            environment,
            authoring_object_key=key,
            authoring_revision=authoring_content_digest(changed),
        )

        assert backfill_environment(environment.id).outcome == "appended"

        environment.refresh_from_db()
        s2 = environment.suite_version
        assert [one["key"] for one in s2.diff["removed"]] == [_key(NAMES[1])]
        assert s2.summary == "1 scenario removed"


class TestBusyEnvironments:
    @pytest.mark.parametrize("state", ["running", "provisioning", "queued"])
    def test_a_running_environment_gets_versions_but_no_payload_writes(
        self, user, workspace, storage, state
    ):
        environment = _legacy(user, workspace, f"bf-busy-{state}", storage)
        HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key=_key(NAMES[1])
        ).update(deleted=True)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state=state
        )
        environment.refresh_from_db()
        payload, count = environment.payload, environment.scenario_count

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "versioned"
        assert environment.environment_version_id is not None
        assert environment.agent_version_id is not None
        assert (environment.payload, environment.scenario_count) == (payload, count)

    def test_an_extend_in_flight_lands_as_s2_on_top_of_the_backfill(
        self, user, workspace, storage
    ):
        environment = _legacy(
            user, workspace, "bf-extending", storage, scenario_extend={"to": 3}
        )
        count = environment.scenario_count

        backfill_environment(environment.id)

        environment.refresh_from_db()
        assert environment.scenario_count == count
        s1 = environment.suite_version
        _with_metadata(environment, scenario_extend=None)
        environment.refresh_from_db()

        s2 = record_authoring(
            environment, _authoring(names=NAMES), origin="extend"
        ).suite

        assert (s2.version_number, s2.parent_id, s2.origin) == (2, s1.id, "extend")
        assert s2.scenario_count == 3

    def test_a_run_still_running_gets_pins_but_no_payload_write(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-run-busy", storage)
        matched = _run(environment, "matched", state=HostedHarnessJob.State.RUNNING)
        older = _run(environment, "older", state=HostedHarnessJob.State.RUNNING)
        _with_metadata(older, authoring_revision="sha256:" + "c" * 64)
        older.refresh_from_db()
        older_payload = older.payload

        result = backfill_environment(environment.id)

        matched.refresh_from_db()
        older.refresh_from_db()
        assert (result.runs_pinned, result.runs_unmatched) == (1, 0)
        assert matched.environment_version_id is not None
        assert older.payload == older_payload
        assert older.agent_version_id is not None


class TestRaces:
    def test_an_edit_while_the_archive_is_read_is_retried(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _legacy(user, workspace, "bf-race", storage)
        read = archives.get_archive
        edits = iter(["sha256:" + "d" * 64])

        def read_then_edit(key):
            body = read(key)
            revision = next(edits, None)
            if revision:
                _with_metadata(
                    HostedHarnessJob.no_workspace_objects.get(id=environment.id),
                    authoring_revision=revision,
                )
            return body

        monkeypatch.setattr(archives, "get_archive", read_then_edit)

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "versioned"
        # The edited label does not match the bytes read, so the bytes' revision wins.
        body = storage.objects[environment.payload["metadata"]["authoring_object_key"]]
        documents = environment.environment_version.documents
        assert documents["authoring_revisions"] == [authoring_content_digest(body)]
        key = environment.payload["metadata"]["authoring_object_key"]
        assert documents["unverified_revisions"] == [["sha256:" + "d" * 64, key]]

    def test_an_environment_saved_on_every_pass_is_left_alone(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _legacy(user, workspace, "bf-race-always", storage)
        read = archives.get_archive
        counter = iter(range(100))

        def read_then_edit(key):
            body = read(key)
            _with_metadata(
                HostedHarnessJob.no_workspace_objects.get(id=environment.id),
                authoring_revision=f"sha256:{next(counter):064d}",
            )
            return body

        monkeypatch.setattr(archives, "get_archive", read_then_edit)

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "changed_during_backfill"
        assert environment.environment_version_id is None

    def test_a_storage_outage_is_retried_then_raised(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _legacy(user, workspace, "bf-outage", storage)
        calls = []

        def unavailable(_key):
            calls.append(1)
            raise S3Error(None, "InternalError", "down", "resource", "request", "host")

        monkeypatch.setattr(archives, "get_archive", unavailable)
        monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)

        with pytest.raises(S3Error):
            backfill_environment(environment.id)
        assert len(calls) == 3
        environment.refresh_from_db()
        assert environment.environment_version_id is None

    def test_a_blip_is_retried_and_then_versions(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _legacy(user, workspace, "bf-blip", storage)
        read = archives.get_archive
        failures = iter([1])

        def flaky(key):
            if next(failures, None):
                raise S3Error(None, "SlowDown", "busy", "resource", "request", "host")
            return read(key)

        monkeypatch.setattr(archives, "get_archive", flaky)
        monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)

        assert backfill_environment(environment.id).layout == "split"


class TestFlagOff:
    def test_a_backfilled_environment_records_edits_with_the_flag_off(
        self, user, workspace, storage, settings
    ):
        environment = _legacy(user, workspace, "bf-flag-off-edit", storage)
        backfill_environment(environment.id)
        environment.refresh_from_db()
        key = environment.payload["metadata"]["authoring_object_key"]
        original = storage.objects[key]
        settings.HARNESS_VERSIONING_ENABLED = False

        rewrite_authoring_scenarios(
            environment,
            [{"scenario_key": _key(NAMES[0]), "instruction": "call about a refund"}],
        )

        environment.refresh_from_db()
        assert storage.objects[key] == original
        assert environment.suite_version.version_number == 2

    def test_an_environment_without_versions_stays_legacy_with_the_flag_off(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_VERSIONING_ENABLED = False
        environment = _legacy(user, workspace, "bf-flag-off-legacy", storage)

        assert record_authoring(environment, _authoring(), origin="chat") is None


class TestBackfillAll:
    def test_counts_a_failing_environment_and_goes_on(
        self, user, workspace, storage, monkeypatch
    ):
        broken = _legacy(user, workspace, "bf-all-broken", storage)
        healthy = _legacy(user, workspace, "bf-all-healthy", storage)
        real = backfill.backfill_environment

        def flaky(job_id, **options):
            if str(job_id) == str(broken.id):
                raise RuntimeError("boom")
            return real(job_id, **options)

        monkeypatch.setattr(backfill, "backfill_environment", flaky)

        assert backfill_all() == {"error": 1, "versioned": 1}
        healthy.refresh_from_db()
        assert healthy.environment_version_id is not None

    def test_explicit_ids_are_each_reported(self, user, workspace, storage):
        environment = _legacy(user, workspace, "bf-all-ids", storage)
        run = _run(environment)
        unknown = "0b7c1a5e-7d0a-4f4e-9d3c-2f1a8b6e4c11"

        outcomes = backfill_all(job_ids=[environment.id, run.id, unknown])

        assert outcomes == {"versioned": 1, "not_environment": 1, "not_found": 1}


class TestCommand:
    def test_the_run_versions_every_environment_and_a_rerun_changes_nothing(
        self, user, workspace, storage, tmp_path
    ):
        first = _legacy(user, workspace, "bf-cmd-1", storage)
        second = _legacy(user, workspace, "bf-cmd-2", storage)
        out = io.StringIO()
        report = tmp_path / "run.json"

        call_command("harness_versions_backfill", stdout=out)
        call_command(
            "harness_versions_backfill", output=str(report), stdout=io.StringIO()
        )

        for environment in (first, second):
            environment.refresh_from_db()
            assert environment.environment_version_id is not None
            assert len(_versions(HarnessEnvironmentVersion, environment)) == 1
        assert "versioned 2" in out.getvalue()
        result = json.loads(report.read_text())
        assert result["outcomes"] == {"current": 2}
        assert {one["job_id"] for one in result["environments"]} == {
            str(first.id),
            str(second.id),
        }

    def test_the_job_filter_runs_one_environment(self, user, workspace, storage):
        chosen = _legacy(user, workspace, "bf-cmd-job-1", storage)
        other = _legacy(user, workspace, "bf-cmd-job-2", storage)

        call_command(
            "harness_versions_backfill", job=[str(chosen.id)], stdout=io.StringIO()
        )

        chosen.refresh_from_db()
        other.refresh_from_db()
        assert chosen.environment_version_id is not None
        assert other.environment_version_id is None

    def test_a_bad_job_id_is_refused(self, user, workspace, storage):
        with pytest.raises(CommandError):
            call_command("harness_versions_backfill", job=["not-a-uuid"])

    def test_a_job_id_that_cannot_be_backfilled_fails_the_run(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-cmd-unknown", storage)
        run = _run(environment)
        unknown = "0b7c1a5e-7d0a-4f4e-9d3c-2f1a8b6e4c11"
        err = io.StringIO()

        with pytest.raises(CommandError):
            call_command(
                "harness_versions_backfill",
                job=[unknown, str(run.id)],
                stdout=io.StringIO(),
                stderr=err,
            )

        assert f"{unknown}: not_found" in err.getvalue()
        assert f"{run.id}: not_environment" in err.getvalue()

    def test_an_environment_saved_on_every_pass_fails_the_run(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _legacy(user, workspace, "bf-cmd-race", storage)
        read = archives.get_archive
        counter = iter(range(100))

        def read_then_edit(key):
            body = read(key)
            _with_metadata(
                HostedHarnessJob.no_workspace_objects.get(id=environment.id),
                authoring_revision=f"sha256:{next(counter):064d}",
            )
            return body

        monkeypatch.setattr(archives, "get_archive", read_then_edit)
        err = io.StringIO()

        with pytest.raises(CommandError):
            call_command("harness_versions_backfill", stdout=io.StringIO(), stderr=err)

        assert f"{environment.id}: changed_during_backfill" in err.getvalue()

    def test_one_failing_environment_does_not_stop_the_rest(
        self, user, workspace, storage, monkeypatch, caplog
    ):
        broken = _legacy(user, workspace, "bf-cmd-broken", storage)
        healthy = _legacy(user, workspace, "bf-cmd-healthy", storage)
        real = backfill.backfill_environment

        def flaky(job_id, **options):
            if str(job_id) == str(broken.id):
                raise RuntimeError("boom")
            return real(job_id, **options)

        monkeypatch.setattr(backfill, "backfill_environment", flaky)
        err = io.StringIO()

        with pytest.raises(CommandError):
            call_command("harness_versions_backfill", stdout=io.StringIO(), stderr=err)

        healthy.refresh_from_db()
        broken.refresh_from_db()
        assert healthy.environment_version_id is not None
        assert broken.environment_version_id is None
        assert f"{broken.id}: error (RuntimeError: boom)" in err.getvalue()
        assert any(record.exc_info for record in caplog.records)


def _index_without_keys(names, **fields):
    """A scenario index as older builds wrote it: names and folders, no keys."""
    return json.dumps(
        [
            {"name": name, "folder": f"scenarios/{name}", "instruction": "index text"}
            | fields
            for name in names
        ]
    )


def _members_of(environment):
    environment.refresh_from_db()
    return list(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=environment.suite_version
        ).order_by("number")
    )


class TestMembersFromLocalShapedArchives:
    def test_an_index_without_keys_still_gives_members_their_content(
        self, user, workspace, storage
    ):
        body = _authoring(**{"scenarios.json": _index_without_keys(NAMES[:2])})
        environment = _legacy(user, workspace, "bf-no-keys", storage, body=body)

        backfill_environment(environment.id)

        members = _members_of(environment)
        assert [member.scenario_key for member in members] == [
            _key(NAMES[0]),
            _key(NAMES[1]),
        ]
        # The folder's own document wins over the index entry.
        assert [member.content["instruction"] for member in members] == [
            f"call about {NAMES[0]}",
            f"call about {NAMES[1]}",
        ]

    def test_a_row_is_matched_by_name_when_its_key_differs(
        self, user, workspace, storage
    ):
        body = _authoring(**{"scenarios.json": _index_without_keys(NAMES[:2])})
        environment = _legacy(user, workspace, "bf-by-name", storage, body=body)
        row = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=_key(NAMES[0])
        )
        row.scenario_key, row.name = "renamed-long-ago", NAMES[0]
        row.save(update_fields=["scenario_key", "name", "updated_at"])

        backfill_environment(environment.id)

        members = _members_of(environment)
        matched = next(member for member in members if member.scenario_id == row.id)
        assert matched.scenario_key == "renamed-long-ago"
        assert matched.content["instruction"] == f"call about {NAMES[0]}"
        row.refresh_from_db()
        assert (row.scenario_key, row.name) == ("renamed-long-ago", NAMES[0])

    def test_duplicate_rows_give_one_member_per_archive_scenario(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-duplicates", storage)
        for name in NAMES[:2]:
            HostedHarnessScenario.no_workspace_objects.create(
                job=environment, scenario_key=name, name=name
            )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            scenario_count=4
        )

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        members = _members_of(environment)
        assert len(members) == 2
        assert len({member.scenario_id for member in members}) == 2
        assert environment.suite_version.scenario_count == 2
        assert environment.scenario_count == 2
        assert "2 live rows without an archive scenario" in result.notes
        assert (
            HostedHarnessScenario.no_workspace_objects.filter(job=environment).count()
            == 4
        )

    def test_a_row_with_no_archive_scenario_is_not_a_member(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-orphan-row", storage)
        orphan = HostedHarnessScenario.no_workspace_objects.create(
            job=environment, scenario_key="orphan-scenario", name="orphan"
        )

        result = backfill_environment(environment.id)

        members = _members_of(environment)
        assert orphan.id not in {member.scenario_id for member in members}
        assert len(members) == 2
        assert "1 live rows without an archive scenario" in result.notes

    def test_keys_come_from_the_folder_when_nothing_else_names_one(
        self, user, workspace, storage
    ):
        folder_documents = {
            f"scenarios/{name}/scenario.json": json.dumps(
                {"name": name, "instruction": f"folder text {name}", "tests": "t"}
            )
            for name in NAMES[:2]
        }
        body = _authoring(
            **{
                "scenarios.json": json.dumps([{"name": name} for name in NAMES[:2]]),
                **folder_documents,
            }
        )
        environment = _legacy(user, workspace, "bf-folder-keys", storage, body=body)

        backfill_environment(environment.id)

        members = _members_of(environment)
        assert [member.content["instruction"] for member in members] == [
            f"folder text {NAMES[0]}",
            f"folder text {NAMES[1]}",
        ]
        assert environment.suite_version.scenario_count == 2

    def test_without_the_archive_every_live_row_is_a_member(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-rows-only", storage, body=False)
        HostedHarnessScenario.no_workspace_objects.create(
            job=environment, scenario_key="extra-row", name="extra"
        )

        backfill_environment(environment.id)

        members = _members_of(environment)
        assert len(members) == 3
        assert environment.suite_version.scenario_count == 3


def _large_suite(scenarios, files_per_scenario):
    """A combined archive the size of production's largest: many folders, small files."""
    import io
    import tarfile

    documents = [
        {"name": f"case-{index}", "scenario_key": f"case-{index}", "tests": "t"}
        for index in range(scenarios)
    ]
    entries = {
        "contract.json": json.dumps({"agent": "support", "tools": []}),
        "sub_goals.json": json.dumps({"sub_goals": []}),
        "scenarios.json": json.dumps(documents),
        **_CERTIFIED,
    }
    for document in documents:
        folder = f"scenarios/{document['name']}"
        entries[f"{folder}/scenario.json"] = json.dumps(document)
        for index in range(files_per_scenario - 1):
            entries[f"{folder}/checks/c{index}.py"] = "x"
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        folders = {"."}
        for path in sorted(entries):
            parts = path.split("/")[:-1]
            for depth in range(1, len(parts) + 1):
                folder = "/".join(parts[:depth])
                if folder not in folders:
                    folders.add(folder)
                    directory = tarfile.TarInfo(f"./{folder}")
                    directory.type = tarfile.DIRTYPE
                    archive.addfile(directory)
            data = entries[path].encode()
            member = tarfile.TarInfo(f"./{path}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


def _scenario_document(name, **fields):
    return json.dumps(
        {
            "name": name,
            "scenario_key": _key(name),
            "instruction": f"call about {name}",
            "tests": "t",
            **fields,
        }
    )


class TestProductionShapes:
    def test_a_suite_past_ten_thousand_members_is_split_with_its_bundle(
        self, user, workspace, storage
    ):
        body = _large_suite(500, 22)
        environment = _legacy(user, workspace, "prod-large", storage, body=body)

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert (result.outcome, result.layout) == ("versioned", "split")
        assert result.bundle_recorded
        assert environment.suite_version.scenario_count == 500
        assert HarnessAssembledBundle.no_workspace_objects.filter(
            job=environment
        ).exists()

    def test_an_index_without_keys_gives_the_suite_keyed_documents(
        self, user, workspace, storage
    ):
        body = _authoring(**{"scenarios.json": _index_without_keys(NAMES[:2])})
        environment = _legacy(user, workspace, "prod-keyless", storage, body=body)

        backfill_environment(environment.id)

        environment.refresh_from_db()
        members = _members_of(environment)
        assert {member.scenario_key for member in members} == {
            _key(name) for name in NAMES[:2]
        }
        assert all(member.content.get("instruction") for member in members)

    def test_a_failed_environment_with_a_stale_extend_marker(
        self, user, workspace, storage
    ):
        environment = _legacy(
            user, workspace, "prod-stale-extend", storage, scenario_extend={"to": 5}
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state=HostedHarnessJob.State.FAILED, scenario_count=5
        )
        environment.refresh_from_db()
        payload = environment.payload

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "versioned"
        assert environment.suite_version.scenario_count == 2
        assert (environment.scenario_count, environment.payload) == (5, payload)

    def test_a_certificate_without_a_sealed_bundle_records_no_bundle(
        self, user, workspace, storage
    ):
        body = _authoring(
            sealed=False, **{"runtime-validation.json": json.dumps({"ok": True})}
        )
        environment = _legacy(user, workspace, "prod-no-seal", storage, body=body)

        result = backfill_environment(environment.id)

        assert (result.layout, result.bundle_recorded) == ("split", False)
        assert not HarnessAssembledBundle.no_workspace_objects.exists()

    def test_a_key_without_a_digest_is_split_when_its_archive_exists(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "prod-plain-key", storage)
        plain = (
            f"harness-authoring/{environment.organization_id}/{environment.id}.tar.gz"
        )
        storage.objects[plain] = _authoring()
        _with_metadata(environment, authoring_object_key=plain, authoring_revision=None)

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.layout == "split"
        assert environment.environment_version.hash_kind == "tree_v1"

    def test_a_missing_key_without_a_digest_hashes_the_key(
        self, user, workspace, storage
    ):
        import hashlib

        environment = _legacy(
            user, workspace, "prod-plain-missing", storage, body=False
        )
        plain = (
            f"harness-authoring/{environment.organization_id}/{environment.id}.tar.gz"
        )
        _with_metadata(environment, authoring_object_key=plain, authoring_revision=None)

        backfill_environment(environment.id)

        environment.refresh_from_db()
        version = environment.environment_version
        assert (version.hash_kind, version.archive_sha256) == ("legacy_key", "")
        assert version.hash == "sha256:" + hashlib.sha256(plain.encode()).hexdigest()

    def test_an_archive_rewritten_in_place_is_split_from_its_current_bytes(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "prod-in-place", storage)
        stale_digest = archives.sha256_hex(b"what the key was first written with")
        key = f"harness-authoring/org/{environment.id}/{stale_digest}.tar.gz"
        storage.objects[key] = _authoring()
        _with_metadata(environment, authoring_object_key=key)

        backfill_environment(environment.id)

        environment.refresh_from_db()
        for version in (environment.environment_version, environment.suite_version):
            assert version.archive_layout == "split"
            assert version.archive_sha256 != stale_digest
            assert version.archive_sha256 == archives.sha256_hex(
                storage.objects[version.archive_object_key]
            )

    def test_without_revisions_runs_get_agent_pins_only(self, user, workspace, storage):
        definition = _definition(user, workspace)
        a1 = _agent_version(definition)
        environment = _legacy(
            user, workspace, "prod-no-revision", storage, definition=definition
        )
        _with_metadata(environment, authoring_revision=None)
        environment.refresh_from_db()
        run = _run(environment)
        body = storage.objects[environment.payload["metadata"]["authoring_object_key"]]

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        run.refresh_from_db()
        assert environment.environment_version.documents["authoring_revisions"] == [
            authoring_content_digest(body)
        ]
        assert (result.runs_pinned, result.runs_unmatched) == (0, 0)
        assert run.environment_version_id is None
        assert "version_pins" not in run.payload["metadata"]
        assert run.agent_version_id == a1.id
        assert Execution.objects.get(id=run.test_execution_id).agent_version_id == (
            a1.id
        )

    def test_a_failed_phone_environment_without_a_run_test(
        self, user, workspace, storage
    ):
        environment = _legacy(
            user, workspace, "prod-no-run-test", storage, definition=False
        )
        payload = dict(environment.payload)
        payload["agent"] = {**payload["agent"], "connector": "phone"}
        payload["source"] = {"kind": "provider"}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            run_test=None, state=HostedHarnessJob.State.FAILED, payload=payload
        )

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "versioned"
        assert environment.agent_version_id is None
        assert "no agent version to pin" in result.notes

    def test_root_dot_files_stay_only_in_the_old_archive(
        self, user, workspace, storage
    ):
        hidden = {
            ".coordinator": "x",
            ".futureagi-conversation.json": "{}",
            ".futureagi-provider-sessions.json": "{}",
        }
        body = _authoring(**hidden)
        environment = _legacy(user, workspace, "prod-dot-files", storage, body=body)
        key = environment.payload["metadata"]["authoring_object_key"]

        backfill_environment(environment.id)

        environment.refresh_from_db()
        for version in (environment.environment_version, environment.suite_version):
            files = archives.read_archive(storage.objects[version.archive_object_key])
            assert not set(hidden) & set(files)
        assert storage.objects[key] == body
        assert set(hidden) <= set(archives.read_archive(body))

    def test_a_retell_chat_suite_keeps_persona_fields_in_its_certify_hash(
        self, user, workspace, storage
    ):
        def certify(connector, personality, key):
            body = _authoring(
                **{
                    f"scenarios/{name}/scenario.json": _scenario_document(
                        name, persona={"personality": personality}
                    )
                    for name in NAMES[:2]
                }
            )
            environment = _legacy(user, workspace, key, storage, body=body)
            payload = dict(environment.payload)
            payload["agent"] = {**payload["agent"], "connector": connector}
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
                payload=payload
            )
            backfill_environment(environment.id)
            environment.refresh_from_db()
            return environment.suite_version.certify_hash

        assert certify("auto", "calm", "prod-auto-1") == certify(
            "auto", "angry", "prod-auto-2"
        )
        assert certify("retell_chat", "calm", "prod-chat-1") != certify(
            "retell_chat", "angry", "prod-chat-2"
        )

    def test_a_deleted_environment_is_not_backfilled(self, user, workspace, storage):
        environment = _legacy(user, workspace, "prod-deleted", storage)
        HostedHarnessJob.all_objects.filter(id=environment.id).update(deleted=True)

        assert backfill_all() == {}
        assert backfill_environment(environment.id).outcome == "not_found"
        assert not HarnessEnvironmentVersion._base_manager.exists()

    def test_set_agent_pins_are_kept_and_empty_ones_filled(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        a1 = _agent_version(definition)
        chosen = _agent_version(definition, status="archived", description="chosen")
        kept = _legacy(
            user, workspace, "prod-pins-kept", storage, definition=definition
        )
        kept_run = _run(kept, "kept")
        RunTest.objects.filter(id=kept.run_test_id).update(agent_version=chosen)
        Execution.objects.filter(id=kept_run.test_execution_id).update(
            agent_version=chosen
        )
        filled = _legacy(
            user, workspace, "prod-pins-filled", storage, definition=definition
        )
        filled_run = _run(filled, "filled")

        backfill_environment(kept.id)
        backfill_environment(filled.id)

        kept.refresh_from_db()
        filled.refresh_from_db()
        # The run test's own pin is the environment's A1; an empty one stays empty.
        assert kept.agent_version_id == chosen.id
        assert RunTest.objects.get(id=kept.run_test_id).agent_version_id == chosen.id
        assert (
            Execution.objects.get(id=kept_run.test_execution_id).agent_version_id
            == chosen.id
        )
        assert filled.agent_version_id == a1.id
        assert RunTest.objects.get(id=filled.run_test_id).agent_version_id is None
        # An empty run test ran the active version at the time.
        assert (
            Execution.objects.get(id=filled_run.test_execution_id).agent_version_id
            == a1.id
        )

    def test_a_mixed_production_set_twice_changes_nothing_the_second_time(
        self, user, workspace, storage
    ):
        keyless = _legacy(
            user,
            workspace,
            "prod-mix-keyless",
            storage,
            body=_authoring(**{"scenarios.json": _index_without_keys(NAMES[:2])}),
        )
        stale = _legacy(
            user, workspace, "prod-mix-stale", storage, scenario_extend={"to": 5}
        )
        HostedHarnessJob.no_workspace_objects.filter(id=stale.id).update(
            state=HostedHarnessJob.State.FAILED
        )
        orphan = _legacy(user, workspace, "prod-mix-orphan", storage, definition=False)
        HostedHarnessJob.no_workspace_objects.filter(id=orphan.id).update(run_test=None)
        large = _legacy(
            user, workspace, "prod-mix-large", storage, body=_large_suite(500, 22)
        )
        _run(keyless)

        tracked = (
            HostedHarnessJob,
            HarnessEnvironmentVersion,
            HarnessSuiteVersion,
            HarnessSuiteMember,
            HarnessAgentVersionRuntime,
            HarnessAssembledBundle,
            RunTest,
            Execution,
        )

        def rows():
            return {
                model.__name__: sorted(
                    (str(row["id"]), str(row["updated_at"]))
                    for row in model._base_manager.values("id", "updated_at")
                )
                for model in tracked
            }

        first = backfill_all()
        before = rows()
        second = backfill_all()

        assert first == {"versioned": 4}
        assert second == {"current": 4}
        assert rows() == before
        for environment in (keyless, stale, orphan, large):
            environment.refresh_from_db()
            assert environment.environment_version_id is not None


def _garbage_archive():
    return b"this is not a tar.gz"


def _non_utf8_archive():
    """A complete authoring tree plus one member whose name is not UTF-8."""
    files = archives.read_archive(_authoring())
    out = io.BytesIO()
    with tarfile.open(
        fileobj=out,
        mode="w:gz",
        format=tarfile.GNU_FORMAT,
        encoding="utf-8",
        errors="surrogateescape",
    ) as archive:
        for path, data in {**files, "handlers/\udcff.py": b"x = 1\n"}.items():
            member = tarfile.TarInfo(path)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


def _tar(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for path, text in files.items():
            data = text.encode()
            member = tarfile.TarInfo(path)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


class TestUnreadableArchives:
    @pytest.mark.parametrize(
        "body, note",
        [
            (_garbage_archive(), "archive unreadable"),
            (_authoring(names=()), "archive has no contract or no scenarios"),
            (
                _tar({"scenarios/a/scenario.json": "{}", "sub_goals.json": "{}"}),
                "archive has no contract or no scenarios",
            ),
            (_non_utf8_archive(), "archive unreadable"),
        ],
        ids=["garbage", "no-scenarios", "no-contract", "non-utf8-name"],
    )
    def test_an_archive_that_cannot_be_split_gets_no_versions(
        self, user, workspace, storage, body, note
    ):
        definition = _definition(user, workspace)
        a1 = _agent_version(definition)
        environment = _legacy(
            user,
            workspace,
            f"bf-unreadable-{len(body)}",
            storage,
            body=body,
            definition=definition,
        )
        run = _run(environment)

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "skipped_unreadable"
        assert any(one.startswith(note) for one in result.notes)
        assert environment.environment_version_id is None
        assert not HarnessEnvironmentVersion._base_manager.filter(
            job=environment
        ).exists()
        # The agent alone is pinned; nothing else is touched.
        assert environment.agent_version_id == a1.id
        run.refresh_from_db()
        assert run.agent_version_id is None
        assert "version_pins" not in run.payload["metadata"]

    def test_an_archive_over_the_caps_gets_no_versions(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _legacy(user, workspace, "bf-over-caps", storage)
        monkeypatch.setattr(archives, "MAX_ARCHIVE_FILES", 3)

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "skipped_unreadable"
        assert environment.environment_version_id is None

    def test_a_versioned_environment_keeps_its_default_when_the_new_archive_is_bad(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-bad-drift", storage)
        backfill_environment(environment.id)
        environment.refresh_from_db()
        defaults = (environment.environment_version_id, environment.suite_version_id)
        key = "harness-authoring/bad/" + "f" * 64 + ".tar.gz"
        storage.objects[key] = _garbage_archive()
        _with_metadata(
            environment,
            authoring_object_key=key,
            authoring_revision="sha256:" + "f" * 64,
        )

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "skipped_unreadable"
        assert (
            environment.environment_version_id,
            environment.suite_version_id,
        ) == defaults
        assert len(_versions(HarnessEnvironmentVersion, environment)) == 1

    def test_no_such_bucket_counts_as_missing(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _legacy(user, workspace, "bf-no-bucket", storage)

        def missing(_key):
            raise S3Error(None, "NoSuchBucket", "gone", "bucket", "request", "host")

        monkeypatch.setattr(archives, "get_archive", missing)

        result = backfill_environment(environment.id)

        assert (result.outcome, result.layout) == ("versioned", "combined")


class TestTransportErrors:
    def test_a_transport_error_is_retried_then_raised(
        self, user, workspace, storage, monkeypatch
    ):
        from urllib3.exceptions import MaxRetryError

        environment = _legacy(user, workspace, "bf-transport", storage)
        calls = []

        def unreachable(_key):
            calls.append(1)
            raise MaxRetryError(None, "/bucket/key", "connection refused")

        monkeypatch.setattr(archives, "get_archive", unreachable)
        monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)

        with pytest.raises(MaxRetryError):
            backfill_environment(environment.id)
        assert len(calls) == 3

    def test_a_dropped_connection_is_retried(
        self, user, workspace, storage, monkeypatch
    ):
        from urllib3.exceptions import ProtocolError

        environment = _legacy(user, workspace, "bf-dropped", storage)
        read = archives.get_archive
        failures = iter([1])

        def flaky(key):
            if next(failures, None):
                raise ProtocolError("connection reset")
            return read(key)

        monkeypatch.setattr(archives, "get_archive", flaky)
        monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)

        assert backfill_environment(environment.id).layout == "split"


class TestStaleRevision:
    def test_a_revision_that_does_not_match_the_bytes_is_not_pinned(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-stale-rev", storage)
        body = storage.objects[environment.payload["metadata"]["authoring_object_key"]]
        stale = "sha256:" + "e" * 64
        _with_metadata(environment, authoring_revision=stale)
        environment.refresh_from_db()
        run = _run(environment)
        assert run.payload["metadata"]["authoring_revision"] == stale

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        run.refresh_from_db()
        version = environment.environment_version
        assert version.documents["authoring_revisions"] == [
            authoring_content_digest(body)
        ]
        key = environment.payload["metadata"]["authoring_object_key"]
        assert version.documents["unverified_revisions"] == [[stale, key]]
        assert "revision label does not match the archive" in result.notes
        assert run.environment_version_id is None
        assert run.payload["metadata"]["version_pins"] == {"legacy_revision": stale}

    def test_a_rerun_after_a_stale_revision_writes_nothing(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-stale-again", storage)
        _with_metadata(environment, authoring_revision="sha256:" + "e" * 64)
        backfill_environment(environment.id)
        rows = list(
            HarnessEnvironmentVersion._base_manager.values("id", "updated_at")
        ) + list(HostedHarnessJob._base_manager.values("id", "updated_at"))

        assert backfill_environment(environment.id).outcome == "current"
        assert (
            list(HarnessEnvironmentVersion._base_manager.values("id", "updated_at"))
            + list(HostedHarnessJob._base_manager.values("id", "updated_at"))
            == rows
        )


class TestOutcomes:
    def test_a_new_revision_of_the_same_content_is_only_noted(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-noted", storage)
        backfill_environment(environment.id)
        environment.refresh_from_db()
        defaults = (environment.environment_version_id, environment.suite_version_id)
        # Same tree, plus a log the split drops but the revision counts.
        body = _authoring(**{"harness-events.jsonl": '{"event": 1}'})
        key = "harness-authoring/noted/" + archives.sha256_hex(body) + ".tar.gz"
        storage.objects[key] = body
        _with_metadata(
            environment,
            authoring_object_key=key,
            authoring_revision=authoring_content_digest(body),
        )

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "revision_noted"
        assert (
            environment.environment_version_id,
            environment.suite_version_id,
        ) == defaults
        assert authoring_content_digest(body) in (
            environment.suite_version.documents["authoring_revisions"]
        )

    def test_an_archive_back_to_older_content_moves_the_default_back(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-moved", storage)
        backfill_environment(environment.id)
        environment.refresh_from_db()
        first = (environment.environment_version_id, environment.suite_version_id)
        rewrite_authoring_scenarios(
            environment,
            [{"scenario_key": _key(NAMES[0]), "instruction": "call about a refund"}],
        )
        environment.refresh_from_db()
        assert environment.suite_version_id != first[1]
        body = _authoring(**{"harness-events.jsonl": '{"event": 2}'})
        key = "harness-authoring/moved/" + archives.sha256_hex(body) + ".tar.gz"
        storage.objects[key] = body
        _with_metadata(
            environment,
            authoring_object_key=key,
            authoring_revision=authoring_content_digest(body),
        )

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "default_moved"
        assert (
            environment.environment_version_id,
            environment.suite_version_id,
        ) == first


class TestAgentResolution:
    def test_a_run_test_pin_is_a1(self, user, workspace, storage):
        definition = _definition(user, workspace)
        _agent_version(definition)
        pinned = _agent_version(definition, status="archived", description="pinned")
        environment = _legacy(
            user, workspace, "bf-rt-pin", storage, definition=definition
        )
        RunTest.objects.filter(id=environment.run_test_id).update(agent_version=pinned)

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.agent_source == "run_test_pin"
        assert environment.agent_version_id == pinned.id
        assert RunTest.objects.get(id=environment.run_test_id).agent_version_id == (
            pinned.id
        )

    def test_a_bound_definition_from_another_organization_is_ignored(
        self, user, workspace, storage
    ):
        from accounts.models import Organization

        other = Organization.objects.create(name="Other")
        foreign = AgentDefinition.objects.create(
            agent_name="Foreign",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+12345678901",
            inbound=True,
            description="foreign",
            provider="vapi",
            organization=other,
            languages=["en"],
        )
        AgentVersion.no_workspace_objects.create(
            agent_definition=foreign,
            organization=other,
            description="foreign",
            commit_message="foreign",
            status="active",
        )
        definition = _definition(user, workspace)
        own = _agent_version(definition)
        environment = _legacy(
            user,
            workspace,
            "bf-foreign",
            storage,
            definition=definition,
            agent_definition_id=str(foreign.id),
        )

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.agent_source == "run_test"
        assert environment.agent_version_id == own.id

    def test_a_malformed_bound_id_falls_back_to_the_run_test(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        own = _agent_version(definition)
        environment = _legacy(
            user,
            workspace,
            "bf-malformed",
            storage,
            definition=definition,
            agent_definition_id="not-a-uuid",
        )

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "versioned"
        assert environment.agent_version_id == own.id

    def test_a_rerun_after_an_agent_appears_pins_it_everywhere(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        environment = _legacy(
            user, workspace, "bf-agent-later", storage, definition=definition
        )
        run = _run(environment)
        RunTest.objects.filter(id=environment.run_test_id).update(agent_definition=None)
        backfill_environment(environment.id)
        environment.refresh_from_db()
        run.refresh_from_db()
        assert environment.agent_version_id is None
        assert run.environment_version_id is not None
        assert run.agent_version_id is None
        a1 = _agent_version(definition)
        AgentVersion.objects.filter(id=a1.id).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        RunTest.objects.filter(id=environment.run_test_id).update(
            agent_definition=definition
        )

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        run.refresh_from_db()
        assert result.outcome == "current"
        assert environment.agent_version_id == a1.id
        assert environment.environment_version.agent_version_id == a1.id
        assert run.agent_version_id == a1.id
        assert Execution.objects.get(id=run.test_execution_id).agent_version_id == (
            a1.id
        )

    def test_a_ref_carrying_a_value_keeps_only_the_reference(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        version = _agent_version(definition)
        environment = _legacy(
            user, workspace, "bf-ref-value", storage, definition=definition
        )
        payload = dict(environment.payload)
        payload["agent"] = {
            **payload["agent"],
            "secret_refs": {
                "API_TOKEN": {
                    "manager": "platform-vault",
                    "key": "harness-api_token",
                    "purpose": "target_provider",
                    "value": "s3cr3t-in-a-ref",
                }
            },
            "config": {"client_secret": "s3cr3t-in-config", "voice": "calm"},
        }
        environment.payload = payload
        environment.save(update_fields=["payload", "updated_at"])

        backfill_environment(environment.id)

        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version=version
        )
        assert runtime.agent_payload["legacy_secret_refs"] == {
            "API_TOKEN": {
                "manager": "platform-vault",
                "key": "harness-api_token",
                "purpose": "target_provider",
            }
        }
        assert runtime.agent_payload["config"] == {
            "client_secret": "[redacted]",
            "voice": "calm",
        }
        assert "s3cr3t" not in json.dumps(runtime.agent_payload)


@pytest.mark.parametrize(
    "payload, kind, source",
    [
        (
            {"source": {"kind": "archive", "archive_artifact_id": "a1"}},
            "archive",
            {"archive_artifact_id": "a1"},
        ),
        (
            {
                "agent": {
                    "connector": "phone",
                    "call_direction": "outbound",
                    "config": {"phone_number": "+15550001111"},
                }
            },
            "phone",
            {"phone_number": "+15550001111", "call_direction": "outbound"},
        ),
        (
            {
                "agent": {
                    "connector": "livekit",
                    "config": {
                        "agent_name": "front-desk",
                        "url": "wss://key:secret@lk.example.com/rtc?token=t",
                    },
                }
            },
            "livekit",
            {"agent_name": "front-desk", "server_url": "wss://lk.example.com/rtc"},
        ),
        (
            {
                "source": {
                    "kind": "remote",
                    "endpoint": "https://user:pw@agent.example.com/v1?key=k",
                }
            },
            "provider",
            {"provider": "remote", "endpoint": "https://agent.example.com/v1"},
        ),
        (
            {"agent": {"connector": "vapi", "config": {"assistant_id": "as_1"}}},
            "provider",
            {"provider": "vapi", "assistant_id": "as_1"},
        ),
        (
            {"agent": {"connector": "retell", "config": {"agent_id": "ag_1"}}},
            "provider",
            {"provider": "retell", "assistant_id": "ag_1"},
        ),
        (
            {
                "source": {
                    "kind": "github",
                    "repository": "acme/agent",
                    "ref": "main",
                    "commit_sha": "a" * 40,
                    "subfolder": "agents/support",
                    "installation_id": "i-1",
                }
            },
            "github",
            {
                "repository": "acme/agent",
                "ref": "main",
                "commit_sha": "a" * 40,
                "subfolder": "agents/support",
            },
        ),
        (
            {"source": {"kind": "github", "repository": "acme/agent", "subfolder": ""}},
            "github",
            {"repository": "acme/agent"},
        ),
    ],
    ids=["archive", "phone", "livekit", "remote", "vapi", "retell", "github", "root"],
)
def test_legacy_source_by_kind(payload, kind, source):
    assert legacy_source(payload) == (kind, source)


def test_key_matches_win_over_an_earlier_name_match():
    from types import SimpleNamespace as Row

    owner = Row(id=1, scenario_key="beta", name="x")
    documents = [
        {"scenario_key": "missing", "name": "beta"},
        {"scenario_key": "beta", "name": "y"},
    ]

    match = match_members([owner], documents)

    assert [(row.id, position) for row, _, position in match.pairs] == [(1, 2)]
    assert (match.by_key, match.by_name, match.scenarios_without_row) == (1, 0, 1)


def test_an_index_entry_finds_its_folder_by_key():
    suite = {
        "scenarios.json": json.dumps(
            [{"scenario_key": "refund-request", "name": "Another title"}]
        ).encode(),
        "scenarios/refund_request/scenario.json": json.dumps(
            {"instruction": "ask for a refund"}
        ).encode(),
    }

    [scenario] = _archive_scenarios(suite)

    assert scenario["scenario_key"] == "refund-request"
    assert scenario["instruction"] == "ask for a refund"


class TestReReviewFindings:
    def test_a_run_pinned_earlier_takes_its_environment_versions_agent(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        environment = _legacy(
            user, workspace, "bf-pinned-agentless", storage, definition=definition
        )
        run = _run(environment)
        RunTest.objects.filter(id=environment.run_test_id).update(agent_definition=None)
        backfill_environment(environment.id)
        run.refresh_from_db()
        assert run.environment_version_id is not None
        assert run.agent_version_id is None
        # Created after the run started, so no version was current when it ran.
        a1 = _agent_version(definition)
        RunTest.objects.filter(id=environment.run_test_id).update(
            agent_definition=definition
        )

        backfill_environment(environment.id)

        run.refresh_from_db()
        assert run.agent_version_id == a1.id

    def test_the_time_based_agent_follows_the_runs_own_definition(
        self, user, workspace, storage
    ):
        ran_with = _definition(user, workspace, name="Ran with")
        version_then = _agent_version(ran_with, description="then")
        bound = _definition(user, workspace, name="Bound")
        _agent_version(bound, description="bound")
        environment = _legacy(
            user,
            workspace,
            "bf-run-definition",
            storage,
            definition=ran_with,
            agent_definition_id=str(bound.id),
        )
        run = _run(environment)
        assert Execution.objects.get(id=run.test_execution_id).agent_definition_id == (
            ran_with.id
        )

        result = backfill_environment(environment.id)

        run.refresh_from_db()
        assert result.agent_source == "bound_definition"
        assert run.agent_version_id == version_then.id

    def test_a_new_archive_under_the_same_stale_label_is_recorded(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "bf-stale-moved", storage)
        stale = "sha256:" + "e" * 64
        _with_metadata(environment, authoring_revision=stale)
        backfill_environment(environment.id)
        assert backfill_environment(environment.id).outcome == "current"
        changed = _authoring(names=NAMES)
        key = "harness-authoring/moved/" + archives.sha256_hex(changed) + ".tar.gz"
        storage.objects[key] = changed
        # Saved out of band: a new archive, but the same stale label.
        _with_metadata(environment, authoring_object_key=key)

        result = backfill_environment(environment.id)

        environment.refresh_from_db()
        assert result.outcome == "appended"
        assert authoring_content_digest(changed) in (
            environment.suite_version.documents["authoring_revisions"]
        )

    def test_the_agent_fill_skips_versions_that_are_not_active(
        self, user, workspace, storage
    ):
        import uuid

        definition = _definition(user, workspace)
        environment = _legacy(
            user, workspace, "bf-fill-active", storage, definition=definition
        )
        RunTest.objects.filter(id=environment.run_test_id).update(agent_definition=None)
        backfill_environment(environment.id)
        environment.refresh_from_db()
        active = environment.environment_version
        discarded = HarnessEnvironmentVersion.no_workspace_objects.get(id=active.id)
        discarded.pk = discarded.id = uuid.uuid4()
        discarded.status = "discarded"
        discarded.version_number = None
        discarded.hash = "sha256:" + "f" * 64
        discarded.save()
        stamped = active.updated_at
        a1 = _agent_version(definition)
        RunTest.objects.filter(id=environment.run_test_id).update(
            agent_definition=definition
        )

        backfill_environment(environment.id)

        active.refresh_from_db()
        discarded.refresh_from_db()
        assert active.agent_version_id == a1.id
        assert active.updated_at > stamped
        assert discarded.agent_version_id is None

    def test_blank_and_null_archive_keys_are_not_candidates(
        self, user, workspace, storage
    ):
        from simulate.services.harness_versioning.backfill import (
            default_models,
            environments_to_backfill,
            run_backfill,
        )

        good = _legacy(user, workspace, "bf-key-good", storage)
        blanks = []
        for name, value in (("null", None), ("empty", ""), ("blank", "   ")):
            environment = _legacy(user, workspace, f"bf-key-{name}", storage)
            payload = dict(environment.payload)
            payload["metadata"] = {
                **payload["metadata"],
                "authoring_object_key": value,
            }
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
                payload=payload
            )
            blanks.append(environment.id)

        candidates = environments_to_backfill(default_models())
        results = run_backfill()

        assert good.id in candidates
        assert not set(blanks) & set(candidates)
        assert "no_archive" not in {result.outcome for result in results}

    def test_the_output_file_must_not_exist(self, user, workspace, storage, tmp_path):
        environment = _legacy(user, workspace, "bf-output-exists", storage)
        existing = tmp_path / "result.json"
        existing.write_text("keep me")

        with pytest.raises(CommandError):
            call_command(
                "harness_versions_backfill",
                output=str(existing),
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

        environment.refresh_from_db()
        assert existing.read_text() == "keep me"
        assert environment.environment_version_id is None

    def test_a_bad_output_path_fails_before_any_write(
        self, user, workspace, storage, tmp_path
    ):
        environment = _legacy(user, workspace, "bf-output-bad", storage)

        with pytest.raises(CommandError):
            call_command(
                "harness_versions_backfill",
                output=str(tmp_path / "missing" / "result.json"),
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

        environment.refresh_from_db()
        assert environment.environment_version_id is None
