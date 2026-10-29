"""Every save of an environment that has versions records versions, flag or no flag."""

import hashlib

import pytest

from simulate.models import HarnessSuiteMember, HarnessSuiteVersion, HostedHarnessJob
from simulate.services.harness_versioning import archives
from simulate.services.harness_versioning.versions import record_authoring
from simulate.tests import test_harness_versioning_record as record_tests
from simulate.tests.test_harness_amend_archive import NAMES
from simulate.tests.test_hosted_harness_conversation import _Storage

pytestmark = pytest.mark.django_db


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _Storage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


@pytest.fixture
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


def _raw_key(environment, body):
    digest = hashlib.sha256(body).hexdigest()
    return f"harness-authoring/{environment.organization_id}/{environment.id}/{digest}.tar.gz"


def _versioned_building(user, workspace, key, monkeypatch, settings):
    """A built environment whose first build recorded versions with the flag on."""
    settings.HARNESS_VERSIONING_ENABLED = True
    freeze = record_tests.TestFreezeHook()
    environment = freeze._building(user, workspace, key)
    freeze._freeze(environment, record_tests._authoring(), monkeypatch)
    assert environment.environment_version_id is not None
    return environment


def _mark_extend(environment):
    payload = dict(environment.payload)
    payload["metadata"] = {**payload["metadata"], "scenario_extend": {"count": 1}}
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        payload=payload
    )
    environment.refresh_from_db()


def _metadata(environment):
    environment.refresh_from_db()
    return environment.payload["metadata"]


def _fail_member_writes(monkeypatch):
    """Fail after the version rows are written, inside the recording's transaction."""

    def broken(*args, **kwargs):
        raise RuntimeError("member write failed")

    monkeypatch.setattr(HarnessSuiteMember.no_workspace_objects, "bulk_create", broken)


def _member_count(environment):
    return HarnessSuiteMember.no_workspace_objects.filter(
        suite_version__job=environment
    ).count()


class TestFlagOffOnAVersionedEnvironment:
    def test_an_extend_freeze_records_the_next_suite(
        self, user, workspace, storage, monkeypatch, settings
    ):
        environment = _versioned_building(
            user, workspace, "off-extend", monkeypatch, settings
        )
        s1 = environment.suite_version
        _mark_extend(environment)
        settings.HARNESS_VERSIONING_ENABLED = False
        body = record_tests._authoring(NAMES)

        record_tests.TestFreezeHook()._freeze(environment, body, monkeypatch)

        s2 = environment.suite_version
        assert (s2.version_number, s2.origin, s2.parent_id) == (2, "extend", s1.id)
        metadata = _metadata(environment)
        assert "scenario_extend" not in metadata
        assert metadata["authoring_mode"] == "fresh"
        assert metadata["authoring_object_key"] != _raw_key(environment, body)
        assert metadata["authoring_revision"] in s2.documents["authoring_revisions"]
        pair = archives.read_archive(storage.objects[metadata["authoring_object_key"]])
        assert f"scenarios/{NAMES[2]}/scenario.json" in pair

    def test_a_chat_save_records_the_next_suite(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_VERSIONING_ENABLED = True
        environment = record_tests._environment(user, workspace, "off-chat")
        first = record_authoring(
            environment, record_tests._authoring(), origin="authoring"
        )
        environment.refresh_from_db()
        settings.HARNESS_VERSIONING_ENABLED = False

        record_tests.TestChatPromote()._promote(environment, storage, NAMES)

        suite = environment.suite_version
        assert (suite.version_number, suite.origin, suite.parent_id) == (
            2,
            "chat",
            first.suite.id,
        )
        metadata = _metadata(environment)
        assert metadata["authoring_revision"] in suite.documents["authoring_revisions"]
        pair = archives.read_archive(storage.objects[metadata["authoring_object_key"]])
        assert f"scenarios/{NAMES[2]}/scenario.json" in pair

    def test_a_scenario_edit_records_the_next_suite(
        self, user, workspace, storage, settings
    ):
        from simulate.services.hosted_harness_gateway import rewrite_authoring_scenarios

        settings.HARNESS_VERSIONING_ENABLED = True
        environment = record_tests._environment(user, workspace, "off-edit")
        first = record_authoring(
            environment, record_tests._authoring(), origin="authoring"
        )
        environment.refresh_from_db()
        first_key = environment.payload["metadata"]["authoring_object_key"]
        first_bytes = storage.objects[first_key]
        settings.HARNESS_VERSIONING_ENABLED = False

        rewrite_authoring_scenarios(
            environment,
            [
                record_tests._scenario(NAMES[0], background_noise="street"),
                record_tests._scenario(NAMES[1]),
            ],
        )

        environment.refresh_from_db()
        assert environment.suite_version.parent_id == first.suite.id
        assert environment.payload["metadata"]["authoring_object_key"] != first_key
        assert storage.objects[first_key] == first_bytes


class TestFailedRecordingMovesNothing:
    def test_a_failed_extend_leaves_everything_and_the_retry_records_it(
        self, user, workspace, storage, monkeypatch, settings
    ):
        environment = _versioned_building(
            user, workspace, "fail-extend", monkeypatch, settings
        )
        _mark_extend(environment)
        before = dict(_metadata(environment))
        versions_before = (
            environment.environment_version_id,
            environment.suite_version_id,
        )
        members_before = _member_count(environment)
        real_bulk_create = HarnessSuiteMember.no_workspace_objects.bulk_create
        _fail_member_writes(monkeypatch)
        record_tests.TestFreezeHook()._freeze(
            environment, record_tests._authoring(NAMES), monkeypatch
        )

        metadata = _metadata(environment)
        assert metadata["authoring_object_key"] == before["authoring_object_key"]
        assert metadata["authoring_revision"] == before["authoring_revision"]
        assert metadata["scenario_extend"] == {"count": 1}
        assert (
            environment.environment_version_id,
            environment.suite_version_id,
        ) == versions_before
        assert len(record_tests._versions(HarnessSuiteVersion, environment)) == 1
        assert _member_count(environment) == members_before

        monkeypatch.setattr(
            HarnessSuiteMember.no_workspace_objects, "bulk_create", real_bulk_create
        )
        record_tests.TestFreezeHook()._freeze(
            environment, record_tests._authoring(NAMES), monkeypatch
        )

        assert environment.suite_version.version_number == 2
        assert environment.suite_version.origin == "extend"
        assert "scenario_extend" not in _metadata(environment)

    def test_a_failed_chat_save_rolls_the_promote_back(
        self, user, workspace, storage, monkeypatch, settings
    ):
        settings.HARNESS_VERSIONING_ENABLED = True
        environment = record_tests._environment(user, workspace, "fail-chat")
        record_authoring(environment, record_tests._authoring(), origin="authoring")
        environment.refresh_from_db()
        record_tests.TestChatPromote()._promote(environment, storage, NAMES)
        before = dict(environment.payload["metadata"])
        default = environment.suite_version_id
        members_before = _member_count(environment)
        _fail_member_writes(monkeypatch)

        # Since drafts, a chat edit is made live by Save; that is where it can fail.
        with pytest.raises(RuntimeError):
            record_tests._save_drafts(environment)

        environment.refresh_from_db()
        metadata = environment.payload["metadata"]
        assert environment.suite_version_id == default
        assert metadata["authoring_object_key"] == before["authoring_object_key"]
        assert metadata["authoring_revision"] == before["authoring_revision"]
        assert metadata.get("draft_saves") == before.get("draft_saves")
        assert [
            one.status
            for one in record_tests._versions(HarnessSuiteVersion, environment)
        ] == ["active", "draft"]
        assert _member_count(environment) == members_before


class TestUnversionedEnvironments:
    def test_a_build_with_the_flag_off_records_nothing(
        self, user, workspace, storage, monkeypatch, settings
    ):
        settings.HARNESS_VERSIONING_ENABLED = False
        freeze = record_tests.TestFreezeHook()
        environment = freeze._building(user, workspace, "off-build")
        body = record_tests._authoring()

        freeze._freeze(environment, body, monkeypatch)

        assert environment.environment_version_id is None
        assert _metadata(environment)["authoring_object_key"] == _raw_key(
            environment, body
        )
        assert not record_tests._versions(HarnessSuiteVersion, environment)

    def test_a_backfill_during_the_download_is_seen_by_the_extend(
        self, user, workspace, storage, monkeypatch, settings
    ):
        from simulate.services.harness_versioning.backfill import backfill_environment

        settings.HARNESS_VERSIONING_ENABLED = False
        freeze = record_tests.TestFreezeHook()
        environment = freeze._building(user, workspace, "backfill-mid-extend")
        freeze._freeze(environment, record_tests._authoring(), monkeypatch)
        assert environment.environment_version_id is None
        _mark_extend(environment)
        sandbox = freeze._sandbox(record_tests._authoring(NAMES))
        download = sandbox.fs.download_file

        def download_then_backfill(path, timeout=None):
            if path == "/tmp/authoring-rerun.tar.gz":
                backfill_environment(environment.id)
            return download(path, timeout)

        sandbox.fs.download_file = download_then_backfill
        monkeypatch.setattr(
            "simulate.services.harness_usage.record_sandbox_runtime",
            lambda *args, **kwargs: None,
        )
        from types import SimpleNamespace

        from simulate.services.hosted_harness_gateway import HostedHarnessGateway

        HostedHarnessGateway._sync_authoring_progress(
            SimpleNamespace(id="attempt-1", job_id=environment.id, attempt_number=1),
            sandbox,
        )

        environment.refresh_from_db()
        suites = record_tests._versions(HarnessSuiteVersion, environment)
        assert [suite.origin for suite in suites] == ["backfill", "extend"]
        assert environment.suite_version_id == suites[-1].id
        assert "scenario_extend" not in _metadata(environment)


class TestFreezeRetries:
    def test_a_versioned_freeze_stops_retrying_after_five_failures(
        self, user, workspace, storage, monkeypatch, settings
    ):
        from simulate.services.hosted_harness_gateway import FREEZE_FAILURE_LIMIT

        environment = _versioned_building(
            user, workspace, "retry-cap", monkeypatch, settings
        )
        _mark_extend(environment)
        tries = []

        def broken(key, body):
            tries.append(key)
            raise RuntimeError("storage down")

        monkeypatch.setattr(archives, "put_archive", broken)
        freeze = record_tests.TestFreezeHook()
        for _ in range(FREEZE_FAILURE_LIMIT):
            freeze._freeze(environment, record_tests._authoring(NAMES), monkeypatch)
        attempted = len(tries)
        assert attempted == FREEZE_FAILURE_LIMIT

        freeze._freeze(environment, record_tests._authoring(NAMES), monkeypatch)

        assert len(tries) == attempted
        metadata = _metadata(environment)
        assert metadata["versioning_freeze_failures"] == {"1": FREEZE_FAILURE_LIMIT}
        assert metadata["versioning_error"] == {"code": "freeze_failed", "attempt": 1}
        assert metadata["scenario_extend"] == {"count": 1}

    def test_an_unversioned_freeze_failure_is_not_counted(
        self, user, workspace, storage, monkeypatch, settings
    ):
        settings.HARNESS_VERSIONING_ENABLED = False
        freeze = record_tests.TestFreezeHook()
        environment = freeze._building(user, workspace, "retry-legacy")

        def broken(**kwargs):
            raise RuntimeError("storage down")

        monkeypatch.setattr(storage, "put_object", broken)
        freeze._freeze(environment, record_tests._authoring(), monkeypatch)

        assert "versioning_freeze_failures" not in _metadata(environment)


class TestRaces:
    def test_a_backfill_landing_during_the_legacy_store_is_kept_in_step(
        self, user, workspace, storage, monkeypatch, settings
    ):
        from simulate.services import hosted_harness_gateway as gateway
        from simulate.services.harness_versioning.backfill import backfill_environment

        settings.HARNESS_VERSIONING_ENABLED = False
        freeze = record_tests.TestFreezeHook()
        environment = freeze._building(user, workspace, "backfill-mid-store")
        freeze._freeze(environment, record_tests._authoring(), monkeypatch)
        assert environment.environment_version_id is None
        _mark_extend(environment)
        real_store = gateway.store_authoring_archive

        def backfill_then_store(job, body, **kwargs):
            backfill_environment(job.id)
            job.refresh_from_db()
            return real_store(job, body, **kwargs)

        monkeypatch.setattr(gateway, "store_authoring_archive", backfill_then_store)
        body = record_tests._authoring(NAMES)

        freeze._freeze(environment, body, monkeypatch)

        suites = record_tests._versions(HarnessSuiteVersion, environment)
        assert [suite.origin for suite in suites] == ["backfill", "extend"]
        metadata = _metadata(environment)
        assert metadata["authoring_object_key"] != _raw_key(environment, body)
        assert metadata["authoring_revision"] in (
            environment.suite_version.documents["authoring_revisions"]
        )

    def test_two_overlapping_polls_record_one_extend(
        self, user, workspace, storage, monkeypatch, settings
    ):
        from simulate.services.hosted_harness_gateway import freeze_authoring_archive

        environment = _versioned_building(
            user, workspace, "overlapping-polls", monkeypatch, settings
        )
        _mark_extend(environment)
        stale = HostedHarnessJob.no_workspace_objects.get(id=environment.id)

        freeze_authoring_archive(
            environment, record_tests._authoring(NAMES), extending=True
        )
        # The second poll read the job before the first one saved.
        freeze_authoring_archive(
            stale, record_tests._authoring(NAMES[1:]), extending=True
        )

        suites = record_tests._versions(HarnessSuiteVersion, environment)
        assert [suite.origin for suite in suites] == ["authoring", "extend"]
        environment.refresh_from_db()
        assert environment.suite_version_id == suites[-1].id
