"""Rolling an environment's default environment or suite version back (or forward)."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings

from accounts.models import Workspace
from simulate.models import (
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteVersion,
    HostedHarnessJob,
    HostedHarnessScenario,
    HostedHarnessStageOutput,
)
from simulate.services import harness_scenarios
from simulate.services.harness_provider import serialize_job
from simulate.services.harness_versioning import archives, assembly
from simulate.services.harness_versioning.backfill import backfill_environment
from simulate.services.harness_versioning.run_warnings import preview_run
from simulate.services.harness_versioning.versions import (
    DEFAULT_PAIRS_KEPT,
    _Pair,
    _remember_pair,
    move_default,
    record_authoring,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    VersionPicks,
    create_selected_harness_run,
    pinned_stage_outputs,
)
from simulate.tests import test_harness_versioning_record as record_tests
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_api import _versioned
from simulate.tests.test_harness_versioning_assembly import (
    _environment as _cached_environment,
)
from simulate.tests.test_harness_versioning_assembly import _uncache
from simulate.tests.test_harness_versioning_backfill import _legacy, _with_metadata
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_record import _authoring

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
COVERAGE = {"placed": 3, "scenarios": 3, "axes": {"task": ["a"]}}
BUSY = "The environment is being built or extended; try again when it finishes"
UNREADY = "The environment isn't ready; the default can't be changed now"


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _path(environment, piece, version_id):
    return f"{BASE}/{environment.id}/versions/{piece}/{version_id}/default/"


def _move(auth_client, environment, piece, version_id, expected, **extra):
    return auth_client.post(
        _path(environment, piece, version_id),
        {"expected_default_id": str(expected) if expected else None, **extra},
        format="json",
    )


def _rows(environment):
    """Stage output rows as a build writes them, from the default versions."""
    pinned = pinned_stage_outputs(
        environment.environment_version, environment.suite_version
    )
    for output in [
        *pinned.values(),
        {"kind": "environment", "title": "Execution environment", "data": {"a": 1}},
    ]:
        HostedHarnessStageOutput.no_workspace_objects.create(
            job=environment,
            kind=output["kind"],
            title=output["title"],
            summary=output.get("summary") or "",
            data=output["data"],
        )
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        stage_outputs=list(pinned.values())
    )
    environment.refresh_from_db()


def _built(user, workspace, key):
    """Built to E1/S1 (two scenarios) by a build that sealed its bundle."""
    environment = record_tests._environment(user, workspace, key)
    record_authoring(environment, _authoring(), origin="authoring")
    environment.refresh_from_db()
    _rows(environment)
    return environment


def _amend_drop(auth_client, environment, name):
    with patch(
        "simulate.services.hosted_harness_gateway.push_scenarios_into_live_sandbox",
        return_value=False,
    ):
        response = auth_client.post(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/amend/",
            {"changes": [{"op": "drop", "scenario": name}]},
            format="json",
        )
    assert response.status_code == 200, response.content
    # Since drafts, the amend is made live by Save. A Scenarios-tab edit is never
    # resealed, so the new pair is left for its first run to certify.
    versions = "simulate.services.harness_versioning.versions"
    with (
        override_settings(HARNESS_BUNDLE_CERTIFY_ENABLED=True),
        patch(f"{versions}._default_runtime", return_value=object()),
        patch(f"{versions}._cached_sealed", return_value=(None, "")),
    ):
        record_tests._save_drafts(environment)


def _suite(environment, number):
    return HarnessSuiteVersion.no_workspace_objects.get(
        job=environment, version_number=number
    )


def _environment_version(environment, number):
    return HarnessEnvironmentVersion.no_workspace_objects.get(
        job=environment, version_number=number
    )


def _metadata(environment):
    environment.refresh_from_db()
    return environment.payload["metadata"]


def _live_rows(environment):
    return {
        row.kind: row
        for row in HostedHarnessStageOutput.no_workspace_objects.filter(job=environment)
    }


def _live_keys(environment):
    return sorted(
        HostedHarnessScenario.no_workspace_objects.filter(job=environment).values_list(
            "scenario_key", flat=True
        )
    )


def _snapshot(environment):
    environment.refresh_from_db()
    return (
        environment.environment_version_id,
        environment.suite_version_id,
        environment.scenario_count,
        environment.stage_outputs,
        environment.payload,
        _live_keys(environment),
        {kind: row.data for kind, row in _live_rows(environment).items()},
    )


def _with_e2(environment):
    """E2 (another contract) made the default by an edit; the suite is unchanged."""
    e1 = environment.environment_version
    record_authoring(
        environment,
        _authoring(contract={"agent": "other", "tools": []}),
        origin="manual",
    )
    environment.refresh_from_db()
    assert environment.environment_version_id != e1.id
    return e1, environment.environment_version


def _forget_legacy(storage, key):
    """Drop a backfilled environment's combined archive, so its pair can't be reused."""
    del storage.objects[f"harness/environments/{key}.tar.gz"]


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


class TestApi:
    def test_rolls_the_suite_back_and_answers_with_the_versions(
        self, auth_client, user, workspace, storage
    ):
        environment = _built(user, workspace, "rb-api")
        s1 = environment.suite_version
        _amend_drop(auth_client, environment, NAMES[1])
        s2 = environment.suite_version

        response = _move(auth_client, environment, "suite", s1.id, s2.id)

        assert response.status_code == 200, response.content
        suites = {one["id"]: one for one in response.json()["suite"]}
        assert suites[str(s1.id)]["is_default"] is True
        assert suites[str(s2.id)]["is_default"] is False
        environment.refresh_from_db()
        assert environment.suite_version_id == s1.id

    def test_another_workspace_is_not_found(
        self, auth_client, user, workspace, storage
    ):
        other = Workspace.objects.create(
            name="Other",
            organization=workspace.organization,
            is_active=True,
            created_by=user,
        )
        environment = _versioned(user, workspace, "rb-scope", storage)
        s1 = _suite(environment, 1)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            workspace=other
        )

        response = _move(
            auth_client, environment, "suite", s1.id, environment.suite_version_id
        )

        assert response.status_code == 404
        assert response.json() == {"detail": "Environment not found"}

    def test_versioning_off_is_not_found(
        self, auth_client, user, workspace, storage, settings
    ):
        environment = _versioned(user, workspace, "rb-off", storage)
        settings.HARNESS_VERSIONING_ENABLED = False

        response = _move(
            auth_client,
            environment,
            "suite",
            _suite(environment, 1).id,
            environment.suite_version_id,
        )

        assert response.status_code == 404
        assert response.json()["error"] == "versioning_disabled"

    def test_unknown_fields_are_refused(self, auth_client, user, workspace, storage):
        environment = _versioned(user, workspace, "rb-fields", storage)

        response = _move(
            auth_client,
            environment,
            "suite",
            _suite(environment, 1).id,
            environment.suite_version_id,
            force=True,
        )

        assert response.status_code == 400

    def test_the_expected_default_is_required(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "rb-required", storage)

        response = auth_client.post(
            _path(environment, "suite", _suite(environment, 1).id), {}, format="json"
        )

        assert response.status_code == 400

    def test_an_unknown_piece_is_not_found(self, auth_client, user, workspace, storage):
        environment = _versioned(user, workspace, "rb-piece", storage)

        response = _move(
            auth_client,
            environment,
            "world",
            _suite(environment, 1).id,
            environment.suite_version_id,
        )

        assert response.status_code == 404
        assert response.json()["error"] == "version_not_found"

    def test_the_current_agent_default_is_a_no_op(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "rb-agent", storage)
        before = _snapshot(environment)

        response = _move(
            auth_client,
            environment,
            "agent",
            environment.agent_version_id,
            environment.agent_version_id,
        )

        assert response.status_code == 200
        assert _snapshot(environment) == before

    def test_a_stale_expected_default_is_a_conflict(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "rb-stale", storage)
        s1 = _suite(environment, 1)

        response = _move(auth_client, environment, "suite", s1.id, s1.id)

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "version_conflict"
        assert body["message"] == (
            "The default changed to v2 since you opened this; pick again"
        )
        environment.refresh_from_db()
        assert environment.suite_version_id == _suite(environment, 2).id

    def test_a_discarded_version_is_not_pickable(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "rb-discarded", storage)
        s1 = _suite(environment, 1)
        HarnessSuiteVersion._base_manager.filter(id=s1.id).update(status="discarded")

        response = _move(
            auth_client, environment, "suite", s1.id, environment.suite_version_id
        )

        assert response.status_code == 422
        assert response.json()["error"] == "version_not_pickable"
        assert response.json()["piece"] == "suite"

    def test_another_environments_version_is_not_pickable(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "rb-own", storage)
        other = _versioned(user, workspace, "rb-other", storage)

        response = _move(
            auth_client,
            environment,
            "suite",
            _suite(other, 1).id,
            environment.suite_version_id,
        )

        assert response.status_code == 422
        assert response.json()["error"] == "version_not_pickable"

    def test_the_current_default_is_a_no_op(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "rb-noop", storage)
        before = _snapshot(environment)

        response = _move(
            auth_client,
            environment,
            "suite",
            environment.suite_version_id,
            environment.suite_version_id,
        )

        assert response.status_code == 200
        assert _snapshot(environment) == before

    def test_a_repeated_click_after_the_move_is_a_no_op(
        self, auth_client, user, workspace, storage
    ):
        environment = _built(user, workspace, "rb-twice")
        s1 = environment.suite_version
        _amend_drop(auth_client, environment, NAMES[1])
        s2 = environment.suite_version
        assert _move(auth_client, environment, "suite", s1.id, s2.id).status_code == 200
        before = _snapshot(environment)

        response = _move(auth_client, environment, "suite", s1.id, s2.id)

        assert response.status_code == 200, response.content
        assert _snapshot(environment) == before

    def test_a_pair_that_was_never_prepared_is_unavailable(
        self, auth_client, user, workspace, storage
    ):
        # The backfilled archive is gone and its bundle has no certificate to cache.
        environment = _versioned(user, workspace, "rb-unprepared", storage)
        _forget_legacy(storage, "rb-unprepared")
        before = _snapshot(environment)

        response = _move(
            auth_client,
            environment,
            "suite",
            _suite(environment, 1).id,
            environment.suite_version_id,
        )

        assert response.status_code == 422
        assert response.json()["error"] == "rollback_unavailable"
        assert response.json()["message"] == (
            "This combination hasn't been prepared yet"
        )
        assert _snapshot(environment) == before

    def test_a_backfilled_pair_rolls_back_to_its_combined_archive(
        self, auth_client, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "rb-legacy", storage)

        response = _move(
            auth_client,
            environment,
            "suite",
            _suite(environment, 1).id,
            environment.suite_version_id,
        )

        assert response.status_code == 200, response.content
        metadata = _metadata(environment)
        assert metadata["authoring_object_key"] == (
            "harness/environments/rb-legacy.tar.gz"
        )
        assert metadata["default_pair_sealed"] is True

    def test_an_edit_remembers_the_backfilled_pair_it_replaces(
        self, auth_client, user, workspace, storage
    ):
        combined = "harness/environments/rb-legacy-edit.tar.gz"
        environment = _legacy(user, workspace, "rb-legacy-edit", storage)
        backfill_environment(environment.id)
        environment.refresh_from_db()
        name = f"{environment.environment_version_id}:{environment.suite_version_id}"
        assert _metadata(environment)["default_pairs"][name]["key"] == combined
        # Environments backfilled before pairs were remembered keep none.
        _with_metadata(environment, default_pairs=None)
        record_authoring(
            environment, _authoring(names=NAMES[:1]), origin="chat", user=user
        )
        environment.refresh_from_db()

        response = _move(
            auth_client,
            environment,
            "suite",
            _suite(environment, 1).id,
            environment.suite_version_id,
        )

        assert response.status_code == 200, response.content
        metadata = _metadata(environment)
        assert metadata["authoring_object_key"] == combined
        assert metadata["default_pair_sealed"] is True

    def test_a_pair_without_its_own_sealed_copy_drops_the_kept_one(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "rb-legacy-sealed", storage)
        _with_metadata(environment, sealed_bundle_object_key="harness/sealed/other")

        move_default(
            environment,
            "suite",
            _suite(environment, 1).id,
            expected_default_id=environment.suite_version_id,
        )

        metadata = _metadata(environment)
        assert metadata["default_pair_sealed"] is True
        assert "sealed_bundle_object_key" not in metadata

    @pytest.mark.parametrize(
        ("busy", "message"),
        [
            ({"state": HostedHarnessJob.State.RUNNING}, BUSY),
            ({"scenario_extend": {"id": "x"}}, BUSY),
            ({"state": HostedHarnessJob.State.FAILED}, UNREADY),
            ({"state": HostedHarnessJob.State.CANCELED}, UNREADY),
        ],
        ids=["building", "extending", "failed", "canceled"],
    )
    def test_a_busy_environment_refuses_the_move(
        self, auth_client, user, workspace, storage, busy, message
    ):
        environment = _built(
            user, workspace, f"rb-busy-{busy.get('state') or 'extend'}"
        )
        s1 = environment.suite_version
        _amend_drop(auth_client, environment, NAMES[1])
        if "state" in busy:
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
                state=busy["state"]
            )
            environment.refresh_from_db()
        else:
            _with_metadata(environment, scenario_extend=busy["scenario_extend"])
        before = _snapshot(environment)

        response = _move(
            auth_client, environment, "suite", s1.id, environment.suite_version_id
        )

        assert response.status_code == 409, response.content
        assert response.json()["error"] == "environment_busy"
        assert response.json()["message"] == message
        assert _snapshot(environment) == before

    def test_an_environment_without_a_suite_default_is_refused_clearly(
        self, auth_client, user, workspace, storage
    ):
        environment = _built(user, workspace, "rb-no-suite")
        e1, e2 = _with_e2(environment)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            suite_version=None
        )

        response = _move(auth_client, environment, "environment", e1.id, e2.id)

        assert response.status_code == 422, response.content
        assert response.json()["error"] == "version_not_pickable"
        assert response.json()["message"] == (
            "This environment has no scenario version yet"
        )


class TestSuiteRollBack:
    def test_an_amend_that_dropped_a_scenario_is_rolled_back(
        self, auth_client, user, workspace, storage
    ):
        environment = _built(user, workspace, "rb-amend")
        s1 = environment.suite_version
        s1_pair = _metadata(environment)["authoring_object_key"]
        _amend_drop(auth_client, environment, NAMES[1])
        s2 = environment.suite_version
        assert s2.scenario_count == 1
        assert _live_keys(environment) == [_key(NAMES[0])]

        move_default(environment, "suite", s1.id, expected_default_id=s2.id)

        environment.refresh_from_db()
        assert environment.suite_version_id == s1.id
        assert environment.scenario_count == 2
        assert environment.payload["scenario_count"] == 2
        listed = serialize_job(environment)["scenarios"]
        assert sorted(one["scenario_key"] for one in listed) == sorted(
            _key(name) for name in NAMES[:2]
        )
        assert all(one["scenario_id"] != "None" for one in listed)
        metadata = environment.payload["metadata"]
        assert metadata["authoring_object_key"] == s1_pair
        assert metadata["default_pair_sealed"] is True
        pinned = pinned_stage_outputs(environment.environment_version, s1)
        rows = _live_rows(environment)
        assert rows["scenarios"].data == pinned["scenarios"]["data"]
        assert rows["contract"].data == pinned["contract"]["data"]
        assert rows["environment"].data == {"a": 1}
        scenarios = next(
            one for one in environment.stage_outputs if one["kind"] == "scenarios"
        )
        assert scenarios == pinned["scenarios"]

    def test_a_larger_built_suite_is_rolled_back(self, user, workspace, storage):
        environment = _built(user, workspace, "rb-larger")
        s1 = environment.suite_version
        record_authoring(
            environment,
            _authoring(NAMES, **{"coverage.json": json.dumps(COVERAGE)}),
            origin="extend",
        )
        environment.refresh_from_db()
        s2 = environment.suite_version
        assert s2.scenario_count == 3
        # The build's poll wrote its rows and outputs.
        harness_scenarios.index_scenarios(
            environment, s2.documents["scenarios"], prune=True
        )
        HostedHarnessStageOutput.no_workspace_objects.create(
            job=environment,
            kind="coverage",
            title="Coverage",
            data=COVERAGE,
        )
        assert len(_live_keys(environment)) == 3

        move_default(environment, "suite", s1.id, expected_default_id=s2.id)

        environment.refresh_from_db()
        assert _live_keys(environment) == sorted(_key(name) for name in NAMES[:2])
        assert HostedHarnessScenario.all_objects.get(
            job=environment, scenario_key=_key(NAMES[2])
        ).deleted
        assert environment.scenario_count == 2
        assert "coverage" not in _live_rows(environment)
        assert "coverage" not in {one["kind"] for one in environment.stage_outputs}

        # Rolling forward brings the coverage row back.
        move_default(environment, "suite", s2.id, expected_default_id=s1.id)

        environment.refresh_from_db()
        assert len(_live_keys(environment)) == 3
        assert _live_rows(environment)["coverage"].data == COVERAGE
        assert environment.scenario_count == 3

    def test_a_run_at_the_new_defaults_is_pinned_to_them(
        self, auth_client, user, workspace, storage
    ):
        environment = _built(user, workspace, "rb-run")
        s1 = environment.suite_version
        _amend_drop(auth_client, environment, NAMES[1])
        move_default(
            environment,
            "suite",
            s1.id,
            expected_default_id=environment.suite_version_id,
        )
        environment.refresh_from_db()

        run, created = create_selected_harness_run(
            environment,
            scenario_keys=[_key(NAMES[1])],
            trials=1,
            idempotency_key="after-roll-back",
        )

        assert created
        assert run.suite_version_id == s1.id
        assert run.environment_version_id == environment.environment_version_id
        assert run.payload["metadata"]["authoring_object_key"] == (
            environment.payload["metadata"]["authoring_object_key"]
        )

    def test_a_failure_mid_way_changes_nothing(
        self, auth_client, user, workspace, storage, monkeypatch
    ):
        environment = _built(user, workspace, "rb-atomic")
        s1 = environment.suite_version
        _amend_drop(auth_client, environment, NAMES[1])
        before = _snapshot(environment)

        def broken(*args, **kwargs):
            raise RuntimeError("index failed")

        monkeypatch.setattr(harness_scenarios, "index_scenarios", broken)

        with pytest.raises(RuntimeError):
            move_default(
                environment,
                "suite",
                s1.id,
                expected_default_id=environment.suite_version_id,
            )

        assert _snapshot(environment) == before


class TestEnvironmentRollBack:
    def test_composes_the_pair_with_a_cached_bundle(self, user, workspace, storage):
        environment = _cached_environment(user, workspace, "rb-env-cached", storage)
        _forget_legacy(storage, "rb-env-cached")
        row = HostedHarnessJob.no_workspace_objects.get(id=environment.id)
        bundle = row.assembled_bundle
        e1, e2 = _with_e2(environment)
        suite_id = environment.suite_version_id

        move_default(environment, "environment", e1.id, expected_default_id=e2.id)

        environment.refresh_from_db()
        assert (environment.environment_version_id, environment.suite_version_id) == (
            e1.id,
            suite_id,
        )
        metadata = environment.payload["metadata"]
        assert metadata["default_pair_sealed"] is True
        assert metadata["sealed_bundle_object_key"] == bundle.object_key
        pair = archives.read_archive(storage.objects[metadata["authoring_object_key"]])
        assert json.loads(pair["contract.json"]) != {"agent": "other", "tools": []}
        assert "environment-bundle/manifest.json" in pair
        assert f"scenarios/{NAMES[0]}/scenario.json" in pair
        remembered = metadata["default_pairs"]
        assert set(remembered) == {f"{e1.id}:{suite_id}", f"{e2.id}:{suite_id}"}
        contract = next(
            one for one in environment.stage_outputs if one["kind"] == "contract"
        )
        assert (
            contract == pinned_stage_outputs(e1, environment.suite_version)["contract"]
        )
        assert contract["data"] != {"agent": "other", "tools": []}

        run = _run(environment)
        assert (run.environment_version_id, run.suite_version_id) == (e1.id, suite_id)
        assert run.payload["metadata"]["assembly"]["cached"] is True
        assert "default_pair_sealed" not in run.payload["metadata"]["assembly"]
        assert "default_pairs" not in run.payload["metadata"]
        assert "default_pair_sealed" not in run.payload["metadata"]

    def test_the_former_default_stays_pickable(
        self, user, workspace, storage, settings
    ):
        environment = _cached_environment(user, workspace, "rb-env-pick", storage)
        e1, e2 = _with_e2(environment)
        move_default(environment, "environment", e1.id, expected_default_id=e2.id)
        environment.refresh_from_db()
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True

        run = _run(environment, VersionPicks(environment_version_id=e2.id))

        assert run.environment_version_id == e2.id
        assert run.payload["metadata"]["assembly"]["defaults"] is False

    def test_an_uncached_pair_is_composed_bare_when_certifying_is_on(
        self, user, workspace, storage, settings
    ):
        environment = _cached_environment(user, workspace, "rb-env-bare", storage)
        _forget_legacy(storage, "rb-env-bare")
        e1, e2 = _with_e2(environment)
        _uncache(environment)
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True

        move_default(environment, "environment", e1.id, expected_default_id=e2.id)

        metadata = _metadata(environment)
        assert metadata["default_pair_sealed"] is False
        assert "sealed_bundle_object_key" not in metadata
        pair = archives.read_archive(storage.objects[metadata["authoring_object_key"]])
        assert not any(archives._is_sealed(path) for path in pair)
        assert preview_run(environment)["bundle_cached"] is False

        # The next run of the defaults certifies its own bundle.
        run = _run(environment)
        assert run.payload["metadata"]["assembly"]["default_pair_sealed"] is False
        plan = assembly.prepare_launch(run)
        assert plan.outcome == "miss"
        assert assembly.certifies_here(run)

        # With certifying turned off nothing can run them, and run-plan says so.
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
        with pytest.raises(HostedHarnessError) as refused:
            _run(environment, key="off")
        assert (refused.value.code, refused.value.status_code) == (
            "version_not_runnable",
            422,
        )
        assert refused.value.message == (
            "These versions haven't been prepared yet and building them is turned off"
        )
        with pytest.raises(HostedHarnessError) as planned:
            preview_run(environment)
        assert planned.value.code == "version_not_runnable"

    def test_an_uncached_pair_without_an_agent_side_row_is_unavailable(
        self, user, workspace, storage, settings
    ):
        environment = _cached_environment(user, workspace, "rb-env-noside", storage)
        _forget_legacy(storage, "rb-env-noside")
        e1, e2 = _with_e2(environment)
        _uncache(environment)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=environment.agent_version_id
        ).delete()
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        before = _snapshot(environment)

        with pytest.raises(HostedHarnessError) as refused:
            move_default(environment, "environment", e1.id, expected_default_id=e2.id)

        assert (refused.value.code, refused.value.status_code) == (
            "rollback_unavailable",
            422,
        )
        assert _snapshot(environment) == before

    def test_a_created_unsealed_run_never_takes_todays_path(
        self, user, workspace, storage, settings
    ):
        environment = _cached_environment(user, workspace, "rb-env-launch", storage)
        _forget_legacy(storage, "rb-env-launch")
        e1, e2 = _with_e2(environment)
        _uncache(environment)
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        move_default(environment, "environment", e1.id, expected_default_id=e2.id)
        environment.refresh_from_db()
        run = _run(environment)
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False

        with pytest.raises(HostedHarnessError) as refused:
            assembly.prepare_launch(run)

        assert refused.value.code == "assembly_unavailable"
        assert not assembly.falls_back(run)

    def test_an_unsealed_run_without_its_bundle_row_is_unavailable(
        self, user, workspace, storage, settings
    ):
        environment = _cached_environment(user, workspace, "rb-env-norow", storage)
        _forget_legacy(storage, "rb-env-norow")
        e1, e2 = _with_e2(environment)
        _uncache(environment)
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        move_default(environment, "environment", e1.id, expected_default_id=e2.id)
        environment.refresh_from_db()
        run = _run(environment)
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
            assembled_bundle=None
        )
        run.refresh_from_db()

        with pytest.raises(HostedHarnessError) as refused:
            assembly.prepare_launch(run)

        assert refused.value.code == "assembly_unavailable"

    def test_an_uncached_pair_is_unavailable_with_certifying_off(
        self, user, workspace, storage
    ):
        environment = _cached_environment(user, workspace, "rb-env-off", storage)
        _forget_legacy(storage, "rb-env-off")
        e1, e2 = _with_e2(environment)
        _uncache(environment)
        before = _snapshot(environment)

        with pytest.raises(HostedHarnessError) as refused:
            move_default(environment, "environment", e1.id, expected_default_id=e2.id)

        assert (refused.value.code, refused.value.status_code) == (
            "rollback_unavailable",
            422,
        )
        assert _snapshot(environment) == before

    def test_a_missing_environment_archive_is_unavailable(
        self, user, workspace, storage, settings
    ):
        environment = _cached_environment(user, workspace, "rb-env-gone", storage)
        _forget_legacy(storage, "rb-env-gone")
        e1, e2 = _with_e2(environment)
        _uncache(environment)
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        del storage.objects[e1.archive_object_key]
        before = _snapshot(environment)

        with pytest.raises(HostedHarnessError) as refused:
            move_default(environment, "environment", e1.id, expected_default_id=e2.id)

        assert refused.value.code == "rollback_unavailable"
        assert _snapshot(environment) == before

    def test_a_remembered_pair_is_reused(self, user, workspace, storage):
        environment = _cached_environment(user, workspace, "rb-env-reuse", storage)
        e1, e2 = _with_e2(environment)
        e2_pair = _metadata(environment)["authoring_object_key"]
        move_default(environment, "environment", e1.id, expected_default_id=e2.id)
        environment.refresh_from_db()
        _uncache(environment)

        move_default(environment, "environment", e2.id, expected_default_id=e1.id)

        metadata = _metadata(environment)
        assert metadata["authoring_object_key"] == e2_pair
        assert metadata["default_pair_sealed"] is True

    def test_a_remembered_pair_whose_object_is_gone_falls_through(
        self, user, workspace, storage, settings
    ):
        environment = _cached_environment(user, workspace, "rb-env-lost", storage)
        e1, e2 = _with_e2(environment)
        e2_pair = _metadata(environment)["authoring_object_key"]
        move_default(environment, "environment", e1.id, expected_default_id=e2.id)
        environment.refresh_from_db()
        del storage.objects[e2_pair]
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True

        move_default(environment, "environment", e2.id, expected_default_id=e1.id)

        metadata = _metadata(environment)
        assert metadata["authoring_object_key"] != e2_pair
        assert metadata["authoring_object_key"] in storage.objects
        assert metadata["default_pair_sealed"] is False

    def _remembered_e2(self, user, workspace, storage, key):
        """E2's pair remembered while E1 is the default."""
        environment = _cached_environment(user, workspace, key, storage)
        e1, e2 = _with_e2(environment)
        e2_pair = _metadata(environment)["authoring_object_key"]
        move_default(environment, "environment", e1.id, expected_default_id=e2.id)
        environment.refresh_from_db()
        name = f"{e2.id}:{environment.suite_version_id}"
        return environment, e1, e2, e2_pair, name

    def _set_entry(self, environment, name, **values):
        pairs = _metadata(environment)["default_pairs"]
        entry = {**pairs[name], **values}
        pairs[name] = {key: value for key, value in entry.items() if value is not None}
        _with_metadata(environment, default_pairs=pairs)

    def test_a_remembered_pair_names_the_default_agent(self, user, workspace, storage):
        environment, _, _, _, name = self._remembered_e2(
            user, workspace, storage, "rb-env-agent"
        )

        entry = _metadata(environment)["default_pairs"][name]

        assert entry["agent_version_id"] == str(environment.agent_version_id)

    def test_a_remembered_pair_of_another_agent_falls_through(
        self, user, workspace, storage, settings
    ):
        environment, e1, e2, e2_pair, name = self._remembered_e2(
            user, workspace, storage, "rb-env-other-agent"
        )
        self._set_entry(environment, name, agent_version_id=str(uuid.uuid4()))
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True

        move_default(environment, "environment", e2.id, expected_default_id=e1.id)

        metadata = _metadata(environment)
        assert metadata["authoring_object_key"] != e2_pair
        assert metadata["default_pair_sealed"] is False
        assert metadata["default_pairs"][name]["agent_version_id"] == str(
            environment.agent_version_id
        )

    def test_a_remembered_pair_without_an_agent_is_reused(
        self, user, workspace, storage
    ):
        environment, e1, e2, e2_pair, name = self._remembered_e2(
            user, workspace, storage, "rb-env-no-agent"
        )
        self._set_entry(environment, name, agent_version_id=None)
        _uncache(environment)

        move_default(environment, "environment", e2.id, expected_default_id=e1.id)

        metadata = _metadata(environment)
        assert metadata["authoring_object_key"] == e2_pair
        assert metadata["default_pair_sealed"] is True

    def test_a_remembered_pair_drops_a_sealed_copy_that_is_gone(
        self, user, workspace, storage
    ):
        environment, e1, e2, e2_pair, name = self._remembered_e2(
            user, workspace, storage, "rb-env-copy-gone"
        )
        sealed_key = _metadata(environment)["default_pairs"][name]["sealed_key"]
        del storage.objects[sealed_key]
        _uncache(environment)

        move_default(environment, "environment", e2.id, expected_default_id=e1.id)

        metadata = _metadata(environment)
        assert metadata["authoring_object_key"] == e2_pair
        assert metadata["default_pair_sealed"] is True
        assert "sealed_bundle_object_key" not in metadata

    def _unsealed_e1(self, storage, user, workspace, key, settings):
        """E1 remembered as a bare pair, then E2 made the default again."""
        environment = _cached_environment(user, workspace, key, storage)
        _forget_legacy(storage, key)
        e1, e2 = _with_e2(environment)
        cached = list(
            HarnessAssembledBundle._base_manager.filter(job=environment).values(
                "id", "state", "object_key", "certificate"
            )
        )
        _uncache(environment)
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        move_default(environment, "environment", e1.id, expected_default_id=e2.id)
        bare = _metadata(environment)["authoring_object_key"]
        move_default(environment, "environment", e2.id, expected_default_id=e1.id)
        environment.refresh_from_db()
        return environment, e1, e2, bare, cached

    def test_an_unsealed_remembered_pair_is_sealed_once_cached(
        self, user, workspace, storage, settings
    ):
        environment, e1, e2, bare, cached = self._unsealed_e1(
            storage, user, workspace, "rb-env-reseal", settings
        )
        for row in cached:
            HarnessAssembledBundle._base_manager.filter(id=row.pop("id")).update(**row)

        move_default(environment, "environment", e1.id, expected_default_id=e2.id)

        metadata = _metadata(environment)
        assert metadata["authoring_object_key"] != bare
        assert metadata["default_pair_sealed"] is True
        assert metadata["sealed_bundle_object_key"] == cached[0]["object_key"]

    def test_an_unsealed_remembered_pair_is_reused_while_certifying(
        self, user, workspace, storage, settings
    ):
        environment, e1, e2, bare, _ = self._unsealed_e1(
            storage, user, workspace, "rb-env-rebare", settings
        )

        move_default(environment, "environment", e1.id, expected_default_id=e2.id)

        metadata = _metadata(environment)
        assert metadata["authoring_object_key"] == bare
        assert metadata["default_pair_sealed"] is False

    def test_an_unsealed_remembered_pair_is_unavailable_with_certifying_off(
        self, user, workspace, storage, settings
    ):
        environment, e1, e2, _, _ = self._unsealed_e1(
            storage, user, workspace, "rb-env-unbare", settings
        )
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
        before = _snapshot(environment)

        with pytest.raises(HostedHarnessError) as refused:
            move_default(environment, "environment", e1.id, expected_default_id=e2.id)

        assert refused.value.code == "rollback_unavailable"
        assert _snapshot(environment) == before


class TestRememberedPairs:
    def test_only_the_newest_pairs_are_kept(self):
        metadata = {}
        versions = [SimpleNamespace(id=uuid.uuid4()) for _ in range(25)]
        suite = SimpleNamespace(id=uuid.uuid4())

        for number, version in enumerate(versions):
            _remember_pair(
                metadata,
                version,
                suite,
                _Pair(key=f"k{number}", revision="r", sealed=True),
            )

        kept = metadata["default_pairs"]
        assert len(kept) == DEFAULT_PAIRS_KEPT
        assert {entry["key"] for entry in kept.values()} == {
            f"k{number}" for number in range(5, 25)
        }

    def test_eviction_follows_time_not_key_order(self):
        suite = SimpleNamespace(id=uuid.uuid4())
        oldest = SimpleNamespace(id=uuid.UUID(int=2**127))
        metadata = {
            "default_pairs": {
                f"{oldest.id}:{suite.id}": {
                    "key": "oldest",
                    "revision": "r",
                    "sealed": True,
                    "at": "2000-01-01T00:00:00+00:00",
                }
            }
        }

        for number in range(DEFAULT_PAIRS_KEPT):
            _remember_pair(
                metadata,
                SimpleNamespace(id=uuid.UUID(int=number + 1)),
                suite,
                _Pair(key=f"k{number}", revision="r", sealed=True),
            )

        assert "oldest" not in {
            entry["key"] for entry in metadata["default_pairs"].values()
        }

    def test_an_outgoing_pair_never_replaces_a_remembered_one(self):
        metadata = {}
        environment = SimpleNamespace(id=uuid.uuid4())
        suite = SimpleNamespace(id=uuid.uuid4())
        _remember_pair(
            metadata, environment, suite, _Pair(key="a", revision="r", sealed=True)
        )

        _remember_pair(
            metadata,
            environment,
            suite,
            _Pair(key="b", revision="r", sealed=False),
            replace=False,
        )

        assert metadata["default_pairs"][f"{environment.id}:{suite.id}"]["key"] == "a"

    def test_a_build_remembers_its_sealed_pair(self, user, workspace, storage):
        environment = _built(user, workspace, "rb-remember")

        metadata = _metadata(environment)

        name = f"{environment.environment_version_id}:{environment.suite_version_id}"
        entry = metadata["default_pairs"][name]
        assert entry["key"] == metadata["authoring_object_key"]
        assert entry["revision"] == metadata["authoring_revision"]
        assert entry["sealed"] is True
        assert entry["sealed_key"] == metadata["sealed_bundle_object_key"]
        assert metadata["default_pair_sealed"] is True

    def test_a_build_without_a_bundle_is_recorded_unsealed(
        self, user, workspace, storage
    ):
        environment = record_tests._environment(user, workspace, "rb-bare-build")

        record_authoring(environment, _authoring(sealed=False), origin="authoring")

        metadata = _metadata(environment)
        name = f"{environment.environment_version_id}:{environment.suite_version_id}"
        assert metadata["default_pair_sealed"] is False
        assert metadata["default_pairs"][name]["sealed"] is False

    def test_an_edit_without_a_bundle_carries_the_kept_one(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "rb-bare-edit")

        record_authoring(
            environment,
            _authoring(names=NAMES[:1], sealed=False),
            origin="chat",
            user=user,
        )

        metadata = _metadata(environment)
        assert metadata["default_pair_sealed"] is True
        pair = archives.read_archive(storage.objects[metadata["authoring_object_key"]])
        assert "environment-bundle/manifest.json" in pair

    def test_an_edit_of_a_backfilled_combined_archive_stays_sealed(
        self, user, workspace, storage
    ):
        environment = _versioned(user, workspace, "rb-legacy-chat", storage)

        record_authoring(
            environment, _authoring(names=NAMES[:1]), origin="chat", user=user
        )

        assert _metadata(environment)["default_pair_sealed"] is True

    def test_an_archive_the_defaults_were_not_recorded_from_is_not_remembered(
        self, user, workspace, storage
    ):
        environment = _built(user, workspace, "rb-raced")
        name = f"{environment.environment_version_id}:{environment.suite_version_id}"
        # A legacy freeze stored another archive under the versioned defaults.
        _with_metadata(
            environment,
            default_pairs=None,
            authoring_object_key="harness/environments/raced.tar.gz",
            authoring_revision="sha256:" + "7" * 64,
        )

        record_authoring(
            environment, _authoring(names=NAMES[:1]), origin="chat", user=user
        )

        assert name not in _metadata(environment)["default_pairs"]
