import io
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext
from minio.error import S3Error

from simulate.models import (
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessJob,
    HostedHarnessScenario,
    RunTest,
)
from simulate.models import TestExecution as Execution
from simulate.services.harness_versioning import archives, backfill, backfill_check
from simulate.services.harness_versioning.backfill import backfill_environment
from simulate.services.harness_versioning.backfill_check import (
    check_all,
    check_environment,
)
from simulate.services.hosted_harness_gateway import (
    authoring_content_digest,
    rewrite_authoring_scenarios,
)
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
    _definition,
    _garbage_archive,
    _index_without_keys,
    _legacy,
    _non_utf8_archive,
    _RealisticStorage,
    _run,
    _tar,
    _with_metadata,
)
from simulate.tests.test_harness_versioning_record import _authoring

pytestmark = pytest.mark.django_db


class _GuardedStorage(_RealisticStorage):
    """In-memory storage that fails any write while ``forbid_writes`` is set."""

    forbid_writes = False

    def _guard(self):
        if self.forbid_writes:
            raise AssertionError("the check wrote to storage")

    def put_object(self, *args, **kwargs):
        self._guard()
        return super().put_object(*args, **kwargs)

    def make_bucket(self, *args, **kwargs):
        self._guard()
        return super().make_bucket(*args, **kwargs)

    def set_bucket_policy(self, *args, **kwargs):
        self._guard()
        return super().set_bucket_policy(*args, **kwargs)


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _GuardedStorage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)


def _snapshot():
    """Every row the check reads, as it stands."""
    return {
        "jobs": list(
            HostedHarnessJob._base_manager.order_by("id").values(
                "id",
                "payload",
                "scenario_count",
                "agent_version_id",
                "environment_version_id",
                "suite_version_id",
                "assembled_bundle_id",
                "updated_at",
            )
        ),
        "scenarios": list(
            HostedHarnessScenario._base_manager.order_by("id").values(
                "id", "deleted", "scenario_key", "updated_at"
            )
        ),
        "run_tests": list(
            RunTest._base_manager.order_by("id").values(
                "id", "agent_version_id", "updated_at"
            )
        ),
        "executions": list(
            Execution._base_manager.order_by("id").values(
                "id", "agent_version_id", "updated_at"
            )
        ),
        "counts": [
            model._base_manager.count()
            for model in (
                HarnessEnvironmentVersion,
                HarnessSuiteVersion,
                HarnessSuiteMember,
                HarnessSuiteValidation,
                HarnessAgentVersionRuntime,
                HarnessAssembledBundle,
            )
        ],
    }


def _codes(report, severity=None):
    return {
        finding["code"]
        for finding in report["findings"]
        if severity is None or finding["severity"] == severity
    }


_WRITES = ("insert ", "update ", "delete ", "for update", "for no key update")


def _writes(queries):
    return [
        query["sql"]
        for query in queries
        if any(word in query["sql"].lower() for word in _WRITES)
    ]


def _pins(environment):
    """Agent pins on the environment's runs and their test executions."""
    runs = list(
        HostedHarnessJob._base_manager.filter(environment=environment).values(
            "agent_version_id", "test_execution_id"
        )
    )
    executions = Execution._base_manager.filter(
        id__in=[run["test_execution_id"] for run in runs]
    )
    return (
        sum(run["agent_version_id"] is not None for run in runs),
        executions.exclude(agent_version=None).count(),
    )


def _counts():
    return {
        model.__name__: model._base_manager.count()
        for model in (
            HarnessEnvironmentVersion,
            HarnessSuiteVersion,
            HarnessAgentVersionRuntime,
        )
    }


def _check_then_backfill(storage, environment):
    """The check's prediction, written nothing, then what the backfill actually did."""
    before = _snapshot()
    storage.forbid_writes = True
    with CaptureQueriesContext(connection) as queries:
        report = check_environment(environment.id)
    storage.forbid_writes = False
    assert not _writes(queries.captured_queries)
    assert _snapshot() == before
    pins, counts = _pins(environment), _counts()
    result = backfill_environment(environment.id)
    environment.refresh_from_db()
    report["_effects"] = {
        "agent_pins_filled": _pins(environment)[0] - pins[0],
        "execution_pins_filled": _pins(environment)[1] - pins[1],
        "created": {name: _counts()[name] - count for name, count in counts.items()},
    }
    return report, result


def _assert_prediction(report, result, environment):
    facts, effects = report["facts"], report["_effects"]
    assert report["outcome"] == result.outcome
    assert report["layout"] == result.layout
    assert facts["agent_source"] == (result.agent_source or "none")
    if environment.suite_version_id and facts.get("suite_version_new"):
        members = HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=environment.suite_version_id
        ).count()
        assert facts["members"] == members
    if "scenario_count_after" in facts:
        assert facts["scenario_count_after"] == environment.scenario_count
    if "bundle_row_eligible" in facts:
        assert facts["bundle_row_eligible"] == result.bundle_recorded
    if "default_pair_kept" in facts:
        pair = f"{environment.environment_version_id}:{environment.suite_version_id}"
        kept = environment.payload["metadata"].get("default_pairs") or {}
        assert facts["default_pair_kept"] == (pair in kept)
    if "hash_kind" in facts:
        assert facts["hash_kind"] == environment.environment_version.hash_kind
        assert effects["created"]["HarnessSuiteVersion"] == int(
            facts["suite_version_new"]
        )
        assert effects["created"]["HarnessEnvironmentVersion"] == int(
            facts["environment_version_new"]
        )
    if "side_row" in facts:
        assert effects["created"]["HarnessAgentVersionRuntime"] == int(
            facts["side_row"] == "create"
        )
    runs = facts.get("runs", {})
    assert runs.get("would_pin", 0) == result.runs_pinned
    assert runs.get("legacy_revision", 0) == result.runs_unmatched
    assert runs.get("agent_pins_to_fill", 0) == effects["agent_pins_filled"]
    assert (
        runs.get("test_execution_pins_to_fill", 0) == effects["execution_pins_filled"]
    )
    assert (facts["agent_source"] != "none") == (
        environment.agent_version_id is not None
    )


class TestReadOnly:
    def test_the_check_writes_nothing_anywhere(self, user, workspace, storage):
        environment = _legacy(user, workspace, "chk-read-only", storage)
        _run(environment)
        old = _run(environment, "old")
        _with_metadata(old, authoring_revision="sha256:" + "c" * 64)
        before = _snapshot()
        storage.forbid_writes = True

        # The rollback would hide a write from the snapshot, so the SQL is checked too.
        with CaptureQueriesContext(connection) as queries:
            result = check_all()

        assert not _writes(queries.captured_queries)
        assert _snapshot() == before
        assert result["totals"]["environments"] == 1
        assert result["environments"][0]["outcome"] == "versioned"

    def test_the_check_sends_no_write_statement(self, user, workspace, storage):
        environment = _legacy(user, workspace, "chk-sql", storage)
        _run(environment)

        with CaptureQueriesContext(connection) as queries:
            check_all()

        assert queries.captured_queries
        assert not _writes(queries.captured_queries)

    @pytest.mark.django_db(transaction=True)
    def test_postgres_refuses_a_write_inside_the_check_transaction(self):
        if connection.vendor != "postgresql":
            pytest.skip("read-only transactions are a PostgreSQL feature")

        with pytest.raises(DatabaseError) as error:
            with backfill_check._read_only():
                HostedHarnessJob._base_manager.filter(id=None).update(scenario_count=1)

        cause = error.value.__cause__
        code = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        assert code == "25006"

    @pytest.mark.django_db(transaction=True)
    def test_the_full_check_runs_under_a_real_read_only_transaction(
        self, user, workspace, storage, tmp_path
    ):
        if connection.vendor != "postgresql":
            pytest.skip("read-only transactions are a PostgreSQL feature")
        _legacy(user, workspace, "chk-tx-missing", storage, body=False)
        _legacy(
            user,
            workspace,
            "chk-tx-keyless",
            storage,
            body=_authoring(**{"scenarios.json": _index_without_keys(NAMES[:2])}),
        )
        duplicated = _legacy(user, workspace, "chk-tx-duplicates", storage)
        HostedHarnessScenario.no_workspace_objects.create(
            job=duplicated, scenario_key=NAMES[0], name=NAMES[0]
        )
        busy = _legacy(user, workspace, "chk-tx-busy", storage)
        HostedHarnessJob.no_workspace_objects.filter(id=busy.id).update(
            state=HostedHarnessJob.State.RUNNING
        )
        no_agent = _legacy(user, workspace, "chk-tx-no-agent", storage)
        _run(no_agent)
        RunTest.objects.filter(id=no_agent.run_test_id).update(agent_definition=None)
        report = tmp_path / "check.json"
        storage.forbid_writes = True

        call_command(
            "harness_versions_backfill",
            check=True,
            output=str(report),
            stdout=io.StringIO(),
        )

        result = json.loads(report.read_text())
        assert result["totals"]["environments"] == 5
        assert "check_failed" not in result["totals"]["outcomes"]
        assert not result["errors"]
        assert not HarnessEnvironmentVersion._base_manager.exists()


class TestPredictionsMatchTheBackfill:
    def test_a_plain_environment_with_matched_and_older_runs(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "chk-plain", storage)
        _run(environment)
        old = _run(environment, "old")
        _with_metadata(old, authoring_revision="sha256:" + "c" * 64)

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["facts"]["runs"]["would_pin"] == 1
        assert report["facts"]["runs"]["legacy_revision"] == 1
        assert report["facts"]["agent_source"] == "run_test"
        assert report["facts"]["side_row"] == "create"
        assert not _codes(report, "error")

    def test_a_missing_archive_predicts_the_fallback(self, user, workspace, storage):
        environment = _legacy(user, workspace, "chk-missing", storage, body=False)

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["layout"] == "combined"
        assert "archive_missing" in _codes(report, "warning")

    def test_an_index_without_keys_is_matched_by_name(self, user, workspace, storage):
        body = _authoring(**{"scenarios.json": _index_without_keys(NAMES[:2])})
        environment = _legacy(user, workspace, "chk-no-keys", storage, body=body)
        row = HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=_key(NAMES[0])
        )
        row.scenario_key, row.name = "renamed-long-ago", NAMES[0]
        row.save(update_fields=["scenario_key", "name", "updated_at"])

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["facts"]["matched_by_name"] == 1
        assert "matched_by_name" in _codes(report, "info")
        assert not _codes(report, "error")

    def test_duplicate_rows_are_flagged_and_left_out(self, user, workspace, storage):
        environment = _legacy(user, workspace, "chk-duplicates", storage)
        for name in NAMES[:2]:
            HostedHarnessScenario.no_workspace_objects.create(
                job=environment, scenario_key=name, name=name
            )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            scenario_count=4
        )
        environment.refresh_from_db()

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["facts"]["members"] == 2
        assert report["facts"]["rows_without_scenario"] == 2
        assert report["facts"]["scenario_count_after"] == 2
        assert "rows_without_scenario" in _codes(report, "warning")

    def test_a_stale_extend_marker_is_a_warning(self, user, workspace, storage):
        environment = _legacy(
            user, workspace, "chk-stale", storage, scenario_extend={"to": 5}
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state=HostedHarnessJob.State.FAILED, scenario_count=5
        )
        environment.refresh_from_db()

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["facts"]["scenario_count_after"] == 5
        assert "stale_extend_marker" in _codes(report, "warning")

    def test_no_agent_is_a_warning(self, user, workspace, storage):
        environment = _legacy(user, workspace, "chk-no-agent", storage)
        RunTest.objects.filter(id=environment.run_test_id).update(agent_definition=None)

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["facts"]["agent_source"] == "none"
        assert "no_agent" in _codes(report, "warning")
        assert report["facts"]["bundle_row_eligible"] is False

    def test_no_sealed_bundle_means_no_bundle_row(self, user, workspace, storage):
        body = _authoring(sealed=False)
        environment = _legacy(user, workspace, "chk-no-seal", storage, body=body)

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["facts"]["bundle_row_eligible"] is False

    @pytest.mark.parametrize("certified", ["same", "other", "none"])
    def test_a_bundle_row_only_for_a_bundle_its_certificate_is_for(
        self, certified, user, workspace, storage
    ):
        digest = "sha256:" + "d" * 64
        compiler = {
            "same": {"compiler": {"bundle_digest": digest}},
            "other": {"compiler": {"bundle_digest": "sha256:" + "0" * 64}},
            "none": {},
        }[certified]
        body = _authoring(
            **{
                "environment-bundle/manifest.json": json.dumps(
                    {"files": [], "digest": digest}
                ),
                "runtime-validation.json": json.dumps({"status": "passed", **compiler}),
            }
        )
        environment = _legacy(
            user, workspace, f"chk-certified-{certified}", storage, body=body
        )

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["facts"]["bundle_row_eligible"] is (certified == "same")

    def test_a_running_environment_is_flagged_busy(self, user, workspace, storage):
        environment = _legacy(user, workspace, "chk-busy", storage)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state=HostedHarnessJob.State.RUNNING
        )
        environment.refresh_from_db()

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert "busy" in _codes(report, "warning")

    def test_a_running_run_is_counted_busy(self, user, workspace, storage):
        environment = _legacy(user, workspace, "chk-run-busy", storage)
        old = _run(environment, "old", state=HostedHarnessJob.State.RUNNING)
        _with_metadata(old, authoring_revision="sha256:" + "c" * 64)

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["facts"]["runs"]["busy"] == 1
        assert "runs_busy" in _codes(report, "warning")

    def test_the_bound_definition_is_named_as_the_source(
        self, user, workspace, storage
    ):
        bound = _definition(user, workspace, name="Bound")
        _agent_version(bound)
        environment = _legacy(
            user, workspace, "chk-bound", storage, agent_definition_id=str(bound.id)
        )

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["facts"]["agent_source"] == "bound_definition"

    def test_a_run_test_pin_is_named_as_the_source(self, user, workspace, storage):
        definition = _definition(user, workspace)
        _agent_version(definition)
        pinned = _agent_version(definition, status="archived", description="pinned")
        environment = _legacy(
            user, workspace, "chk-rt-pin", storage, definition=definition
        )
        RunTest.objects.filter(id=environment.run_test_id).update(agent_version=pinned)

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["facts"]["agent_source"] == "run_test_pin"

    def test_a_stale_revision_is_a_warning_and_its_runs_are_not_pinned(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "chk-stale-rev", storage)
        _with_metadata(environment, authoring_revision="sha256:" + "e" * 64)
        environment.refresh_from_db()
        _run(environment)

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert "revision_mismatch" in _codes(report, "warning")
        assert report["facts"]["runs"]["legacy_revision"] == 1

    def test_a_missing_revision_is_computed(self, user, workspace, storage):
        environment = _legacy(user, workspace, "chk-no-rev", storage)
        _with_metadata(environment, authoring_revision=None)
        environment.refresh_from_db()

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert "revision_computed" in _codes(report, "info")

    def test_a_new_revision_of_the_same_content_is_predicted_as_noted(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "chk-noted", storage)
        backfill_environment(environment.id)
        body = _authoring(**{"harness-events.jsonl": '{"event": 1}'})
        key = "harness-authoring/chk-noted/" + archives.sha256_hex(body) + ".tar.gz"
        storage.objects[key] = body
        _with_metadata(
            environment,
            authoring_object_key=key,
            authoring_revision=authoring_content_digest(body),
        )
        environment.refresh_from_db()

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["outcome"] == "revision_noted"

    def test_older_content_is_predicted_to_move_the_default_back(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "chk-moved", storage)
        backfill_environment(environment.id)
        environment.refresh_from_db()
        rewrite_authoring_scenarios(
            environment,
            [{"scenario_key": _key(NAMES[0]), "instruction": "call about a refund"}],
        )
        body = _authoring(**{"harness-events.jsonl": '{"event": 2}'})
        key = "harness-authoring/chk-moved/" + archives.sha256_hex(body) + ".tar.gz"
        storage.objects[key] = body
        environment.refresh_from_db()
        _with_metadata(
            environment,
            authoring_object_key=key,
            authoring_revision=authoring_content_digest(body),
        )
        environment.refresh_from_db()

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["outcome"] == "default_moved"
        assert "suite_reused" in _codes(report, "info")
        assert not _codes(report, "error")

    def test_after_the_backfill_the_check_says_current(self, user, workspace, storage):
        environment = _legacy(user, workspace, "chk-current", storage)
        backfill_environment(environment.id)
        storage.forbid_writes = True

        report = check_environment(environment.id)

        assert report["outcome"] == "current"
        assert report["facts"]["side_row"] == "exists"
        assert report["facts"]["agent_already_pinned"] is True


class TestErrors:
    def test_an_archive_over_the_caps_is_an_error(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _legacy(user, workspace, "chk-over-caps", storage)
        monkeypatch.setattr(archives, "MAX_ARCHIVE_FILES", 3)

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert "archive_over_caps" in _codes(report, "error")
        assert report["outcome"] == result.outcome == "skipped_unreadable"
        assert report["facts"]["archive"]["within_caps"] is False
        assert environment.environment_version_id is None

    @pytest.mark.parametrize(
        "body, code",
        [
            (_garbage_archive(), "archive_unreadable"),
            (_authoring(names=()), "archive_incomplete"),
            (_tar({"scenarios/a/scenario.json": "{}"}), "archive_incomplete"),
            (_non_utf8_archive(), "archive_unreadable"),
        ],
        ids=["garbage", "no-scenarios", "no-contract", "non-utf8-name"],
    )
    def test_an_archive_that_cannot_be_split_is_an_error(
        self, user, workspace, storage, body, code
    ):
        environment = _legacy(
            user, workspace, f"chk-bad-{len(body)}", storage, body=body
        )
        # The skipped path pins no run; the prediction must say so too.
        _run(environment)

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert code in _codes(report, "error")
        assert report["outcome"] == "skipped_unreadable"

    def test_a_storage_error_is_reported_without_raising(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _legacy(user, workspace, "chk-outage", storage)

        def unavailable(_key):
            raise S3Error(None, "InternalError", "down", "resource", "request", "host")

        monkeypatch.setattr(archives, "get_archive", unavailable)

        result = check_all()

        [report] = result["environments"]
        assert report["outcome"] == "would_raise"
        assert "storage_error" in _codes(report, "error")
        assert result["totals"]["errors"] == 1
        environment.refresh_from_db()
        assert environment.environment_version_id is None

    def test_an_archive_scenario_without_a_live_row_is_an_error(
        self, user, workspace, storage
    ):
        environment = _legacy(user, workspace, "chk-lost-scenario", storage)
        HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, scenario_key=_key(NAMES[1])
        ).update(deleted=True)

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert "scenarios_without_row" in _codes(report, "error")

    def test_one_failing_environment_does_not_stop_the_rest(
        self, user, workspace, storage, monkeypatch
    ):
        broken = _legacy(user, workspace, "chk-broken", storage)
        _legacy(user, workspace, "chk-healthy", storage)
        real = backfill_check.check_environment

        def flaky(job_id, models=None, **options):
            if str(job_id) == str(broken.id):
                raise RuntimeError("boom")
            return real(job_id, models, **options)

        monkeypatch.setattr(backfill_check, "check_environment", flaky)

        result = check_all()

        outcomes = sorted(one["outcome"] for one in result["environments"])
        assert outcomes == ["check_failed", "versioned"]
        assert result["totals"]["errors"] == 1

    def test_a_deleted_environment_is_skipped(self, user, workspace, storage):
        environment = _legacy(user, workspace, "chk-deleted", storage)
        HostedHarnessJob.all_objects.filter(id=environment.id).update(deleted=True)

        assert check_all()["totals"]["environments"] == 0
        report = check_environment(environment.id)
        assert report["outcome"] == "not_found"
        assert "not_backfillable" in _codes(report, "error")


class TestCommand:
    def test_exits_cleanly_when_nothing_is_wrong(self, user, workspace, storage):
        environment = _legacy(user, workspace, "chk-cmd-ok", storage)
        storage.forbid_writes = True
        out = io.StringIO()

        call_command("harness_versions_backfill", check=True, stdout=out)

        environment.refresh_from_db()
        assert environment.environment_version_id is None
        assert not HarnessEnvironmentVersion.no_workspace_objects.exists()
        lines = out.getvalue().strip().splitlines()
        assert lines[0].split()[:3] == [str(environment.id)[:8], "versioned", "split"]
        assert lines[-1].startswith("Backfill check: 1 environments")
        assert "0 errors" in lines[-1]

    def test_exits_with_an_error_when_a_finding_is_an_error(
        self, user, workspace, storage, monkeypatch
    ):
        _legacy(user, workspace, "chk-cmd-error", storage)
        monkeypatch.setattr(archives, "MAX_ARCHIVE_FILES", 3)

        with pytest.raises(CommandError):
            call_command("harness_versions_backfill", check=True, stdout=io.StringIO())

    def test_the_job_filter_and_a_bad_id(self, user, workspace, storage, tmp_path):
        chosen = _legacy(user, workspace, "chk-cmd-1", storage)
        _legacy(user, workspace, "chk-cmd-2", storage)
        report = tmp_path / "check.json"

        call_command(
            "harness_versions_backfill",
            check=True,
            job=[str(chosen.id)],
            output=str(report),
            stdout=io.StringIO(),
        )

        result = json.loads(report.read_text())
        assert [one["job_id"] for one in result["environments"]] == [str(chosen.id)]
        with pytest.raises(CommandError):
            call_command("harness_versions_backfill", check=True, job=["not-a-uuid"])

    def test_an_id_that_cannot_be_backfilled_is_reported_and_fails(
        self, user, workspace, storage, tmp_path
    ):
        environment = _legacy(user, workspace, "chk-cmd-run", storage)
        run = _run(environment)
        unknown = "0b7c1a5e-7d0a-4f4e-9d3c-2f1a8b6e4c11"
        report = tmp_path / "check.json"

        with pytest.raises(CommandError):
            call_command(
                "harness_versions_backfill",
                check=True,
                job=[str(run.id), unknown],
                output=str(report),
                stdout=io.StringIO(),
            )

        result = json.loads(report.read_text())
        assert result["totals"]["outcomes"] == {"not_environment": 1, "not_found": 1}
        assert {one["code"] for one in result["errors"]} == {"not_backfillable"}

    def test_the_json_shape(self, user, workspace, storage, tmp_path):
        _legacy(user, workspace, "chk-cmd-json", storage)
        report = tmp_path / "check.json"

        call_command(
            "harness_versions_backfill",
            check=True,
            json=True,
            output=str(report),
            stdout=io.StringIO(),
        )

        result = json.loads(report.read_text())
        assert set(result) == {"environments", "totals", "errors", "warnings"}
        assert set(result["totals"]) == {
            "environments",
            "outcomes",
            "layouts",
            "errors",
            "warnings",
        }
        [one] = result["environments"]
        assert set(one) == {
            "job_id",
            "short_id",
            "outcome",
            "layout",
            "facts",
            "findings",
        }
        assert {"state", "members", "runs", "agent_source"} <= set(one["facts"])


def test_archive_stats_count_files_apart_from_other_entries():
    body = _authoring()

    stats = archives.archive_stats(body)

    files = archives.read_archive(body)
    assert stats["files"] == len(files)
    assert stats["entries"] > stats["files"]
    assert stats["inflated_bytes"] == sum(len(data) for data in files.values())
    assert stats["within_caps"] is True


@pytest.mark.parametrize(
    "cap, value",
    [
        ("MAX_ARCHIVE_FILES", "files"),
        ("MAX_ARCHIVE_ENTRIES", "entries"),
        ("MAX_ARCHIVE_INFLATED_BYTES", "inflated_bytes"),
    ],
)
def test_archive_stats_and_read_archive_agree_at_the_caps(monkeypatch, cap, value):
    from simulate.services.hosted_harness import HostedHarnessError

    body = _authoring()
    size = archives.archive_stats(body)[value]

    monkeypatch.setattr(archives, cap, size)
    assert archives.archive_stats(body)["within_caps"] is True
    archives.read_archive(body)

    monkeypatch.setattr(archives, cap, size - 1)
    assert archives.archive_stats(body)["within_caps"] is False
    with pytest.raises(HostedHarnessError):
        archives.read_archive(body)


class TestAgentFillPredictions:
    def test_a_run_pinned_earlier_is_predicted_to_take_the_environment_agent(
        self, user, workspace, storage
    ):
        definition = _definition(user, workspace)
        environment = _legacy(
            user, workspace, "chk-pinned-agentless", storage, definition=definition
        )
        _run(environment)
        RunTest.objects.filter(id=environment.run_test_id).update(agent_definition=None)
        backfill_environment(environment.id)
        _agent_version(definition)
        RunTest.objects.filter(id=environment.run_test_id).update(
            agent_definition=definition
        )
        environment.refresh_from_db()

        report, result = _check_then_backfill(storage, environment)

        _assert_prediction(report, result, environment)
        assert report["facts"]["runs"]["agent_pins_to_fill"] == 1
