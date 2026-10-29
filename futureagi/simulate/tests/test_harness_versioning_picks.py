import uuid
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Workspace
from simulate.models import (
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessJob,
)
from simulate.services.harness_versioning import assembly
from simulate.services.harness_versioning.read import run_version_labels
from simulate.services.harness_versioning.run_warnings import (
    preview_run,
    run_warnings,
)
from simulate.services.harness_versioning.versions import record_authoring
from simulate.services.hosted_harness import (
    HostedHarnessError,
    VersionPicks,
    create_selected_harness_run,
    job_pins,
    resolve_run_pins,
)
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_api import _versioned
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
    _definition,
    _legacy,
)
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_record import _authoring

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
ONLY_IN_S1 = _key(NAMES[1])


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _environment(user, workspace, key, storage):
    """Backfilled to A1/E1/S1, then edited to S2 (one scenario): A1/E1/S2 are the
    defaults."""
    return _versioned(user, workspace, key, storage)


def _suite(environment, number):
    return HarnessSuiteVersion.no_workspace_objects.get(
        job=environment, version_number=number
    )


def _runtime(agent_version_id):
    return HarnessAgentVersionRuntime.no_workspace_objects.get(
        agent_version_id=agent_version_id
    )


def _ready(environment, agent_version_id, environment_version, suite_version):
    runtime = _runtime(agent_version_id)
    row, _ = HarnessAssembledBundle._base_manager.update_or_create(
        job_id=environment.id,
        assembly_key=assembly.bundle_key(runtime, environment_version, suite_version),
        defaults={
            "organization_id": environment.organization_id,
            "agent_version_id": agent_version_id,
            "environment_version_id": environment_version.id,
            "suite_version_id": suite_version.id,
            "state": HarnessAssembledBundle.State.READY,
            "object_key": f"bundles/{uuid.uuid4().hex}.tar.gz",
        },
    )
    return row


def _second_agent(environment, *, preflight_status="passed", side_row=True):
    """A2 of the environment's agent definition, with a side row like A1's."""
    a1 = AgentVersion.no_workspace_objects.get(id=environment.agent_version_id)
    a2 = _agent_version(a1.agent_definition, description="prompt v2")
    if side_row:
        runtime = _runtime(a1.id)
        runtime.pk = None
        runtime.id = uuid.uuid4()
        runtime.agent_version = a2
        runtime.hash = "sha256:" + "2" * 64
        runtime.prompt_hash = "sha256:" + "3" * 64
        runtime.preflight_status = preflight_status
        runtime.parent = None
        runtime.save()
    return a2


def _older_environment(environment, built_with):
    """E1 recorded as built with ``built_with`` once an edit made E2 the default, and
    cached with the default agent and suite."""
    e1 = environment.environment_version
    record_authoring(
        environment,
        _authoring(names=NAMES[:1], contract={"agent": "other", "tools": []}),
        origin="authoring",
    )
    environment.refresh_from_db()
    HarnessEnvironmentVersion._base_manager.filter(id=e1.id).update(
        agent_version=built_with
    )
    e1.refresh_from_db()
    _ready(environment, environment.agent_version_id, e1, environment.suite_version)
    return e1


def _create(environment, picks=None, keys=None, key="r", **kwargs):
    run, created = create_selected_harness_run(
        environment,
        scenario_keys=keys or [_key(NAMES[0])],
        trials=1,
        idempotency_key=key,
        picks=picks,
        **kwargs,
    )
    assert created
    run.refresh_from_db()
    return run


def _refusal(environment, picks, keys=None):
    with pytest.raises(HostedHarnessError) as caught:
        create_selected_harness_run(
            environment,
            scenario_keys=keys or [_key(NAMES[0])],
            trials=1,
            idempotency_key="refused",
            picks=picks,
        )
    assert caught.value.status_code == 422
    return caught.value


def _refused(environment, picks, keys=None):
    return _refusal(environment, picks, keys).code


def _writes(queries):
    return [
        query["sql"]
        for query in queries.captured_queries
        if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]


def _mark(row, state):
    HarnessAssembledBundle._base_manager.filter(id=row.id).update(state=state)


def _pins(job):
    return (job.agent_version_id, job.environment_version_id, job.suite_version_id)


def _post_run(auth_client, environment, body, key="api", path=None):
    with patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"):
        return auth_client.post(
            path or f"{BASE}/{environment.id}/run/",
            body,
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )


class TestNoPicks:
    def test_empty_picks_create_the_run_exactly_as_none(self, user, workspace, storage):
        environment = _environment(user, workspace, "pk-none", storage)

        before = _create(environment, None, key="a")
        after = _create(environment, VersionPicks(), key="b")

        assert _pins(after) == _pins(before) == _pins(environment)
        assert after.assembled_bundle_id == before.assembled_bundle_id
        for name in ("version_pins", "assembly", "scenario_edits"):
            assert after.payload["metadata"].get(name) == before.payload[
                "metadata"
            ].get(name)
        assert after.payload["agent"] == before.payload["agent"]
        assert after.payload["metadata"]["assembly"]["defaults"] is True

    def test_picking_the_defaults_is_the_same_as_no_picks(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-default", storage)

        run = _create(
            environment,
            VersionPicks(*_pins(environment)),
        )

        assert _pins(run) == _pins(environment)
        assert run.payload["metadata"]["assembly"]["defaults"] is True

    def test_the_202_adds_versions_and_warnings_only(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-202", storage)

        response = _post_run(
            auth_client, environment, {"scenario_ids": [_key(NAMES[0])], "trials": 1}
        )

        assert response.status_code == 202, response.content
        body = response.json()
        assert set(body) == {
            "environment_id",
            "job_id",
            "run_id",
            "run_test_id",
            "test_execution_id",
            "scenario_count",
            "trials",
            "total_calls",
            "state",
            "stage",
            "versions",
            "warnings",
        }
        assert body["versions"] == {
            "agent": {"id": str(environment.agent_version_id), "label": "v1"},
            "environment": {
                "id": str(environment.environment_version_id),
                "label": "v1",
            },
            "suite": {"id": str(environment.suite_version_id), "label": "v2"},
        }
        assert [one["code"] for one in body["warnings"]] == ["suite_unproved"]

    def test_with_versioning_off_the_202_has_no_versions(
        self, auth_client, user, workspace, storage, settings
    ):
        environment = _environment(user, workspace, "pk-202-off", storage)
        settings.HARNESS_VERSIONING_ENABLED = False

        response = _post_run(
            auth_client, environment, {"scenario_ids": [_key(NAMES[0])], "trials": 1}
        )

        assert response.status_code == 202, response.content
        assert response.json()["versions"] == {
            "agent": None,
            "environment": None,
            "suite": None,
        }
        assert response.json()["warnings"] == []

    def test_the_legacy_runs_endpoint_still_runs_without_picks(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-legacy", storage)

        response = _post_run(
            auth_client,
            environment,
            {"scenario_ids": [_key(NAMES[0])], "trials": 1},
            path=f"/simulate/api/harness-jobs/{environment.id}/runs/",
        )

        assert response.status_code == 202, response.content
        run = HostedHarnessJob.no_workspace_objects.get(id=response.json()["job_id"])
        assert _pins(run) == _pins(environment)


class TestNotPickable:
    def test_any_pick_with_versioning_off(self, user, workspace, storage, settings):
        environment = _environment(user, workspace, "pk-off", storage)
        settings.HARNESS_VERSIONING_ENABLED = False

        code = _refused(
            environment, VersionPicks(suite_version_id=_suite(environment, 1).id)
        )

        assert code == "version_not_pickable"

    def test_any_pick_on_an_unversioned_environment(self, user, workspace, storage):
        environment = _legacy(user, workspace, "pk-unversioned", storage)

        code = _refused(environment, VersionPicks(suite_version_id=uuid.uuid4()))

        assert code == "version_not_pickable"

    def test_another_environments_version(self, user, workspace, storage):
        environment = _environment(user, workspace, "pk-foreign-e", storage)
        other = _environment(user, workspace, "pk-foreign-e2", storage)

        for picks in (
            VersionPicks(environment_version_id=other.environment_version_id),
            VersionPicks(suite_version_id=_suite(other, 1).id),
            VersionPicks(suite_version_id=uuid.uuid4()),
        ):
            assert _refused(environment, picks) == "version_not_pickable"

    @pytest.mark.parametrize("state", ["draft", "discarded", "deleted"])
    def test_a_suite_that_is_not_active(self, user, workspace, storage, state):
        environment = _environment(user, workspace, f"pk-s-{state}", storage)
        s1 = _suite(environment, 1)
        change = {"deleted": True} if state == "deleted" else {"status": state}
        HarnessSuiteVersion._base_manager.filter(id=s1.id).update(**change)

        code = _refused(environment, VersionPicks(suite_version_id=s1.id))

        assert code == "version_not_pickable"

    def test_an_agent_of_another_definition(self, user, workspace, storage):
        environment = _environment(user, workspace, "pk-a-def", storage)
        stray = _agent_version(_definition(user, workspace, name="Neighbour"))

        code = _refused(environment, VersionPicks(agent_version_id=stray.id))

        assert code == "version_not_pickable"

    def test_a_soft_deleted_agent(self, user, workspace, storage):
        environment = _environment(user, workspace, "pk-a-gone", storage)
        a2 = _second_agent(environment)
        AgentVersion.all_objects.filter(id=a2.id).update(deleted=True)

        code = _refused(environment, VersionPicks(agent_version_id=a2.id))

        assert code == "version_not_pickable"

    @pytest.mark.parametrize("status", ["pending", "failed", "discarded"])
    def test_an_agent_whose_preflight_has_not_passed(
        self, user, workspace, storage, status
    ):
        environment = _environment(user, workspace, f"pk-a-{status}", storage)
        a2 = _second_agent(environment, preflight_status=status)

        code = _refused(environment, VersionPicks(agent_version_id=a2.id))

        assert code == "version_not_pickable"

    def test_another_agent_version_with_a_bundle_is_picked(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-a-other", storage)
        a2 = _second_agent(environment)
        _ready(
            environment,
            a2.id,
            environment.environment_version,
            environment.suite_version,
        )

        pins = resolve_run_pins(environment, picks=VersionPicks(agent_version_id=a2.id))
        run = _create(environment, VersionPicks(agent_version_id=a2.id))

        assert (pins.agent_version.id, pins.cached) == (a2.id, True)
        assert run.agent_version_id == a2.id
        assert run.test_execution.agent_version_id == a2.id

    @pytest.mark.parametrize("side_row", [False, True])
    def test_an_older_agent_version_without_its_own_set_is_not_pickable(
        self, user, workspace, storage, settings, side_row
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, f"pk-a-old-{side_row}", storage)
        a2 = _second_agent(environment, preflight_status="legacy", side_row=side_row)

        refusal = _refusal(environment, VersionPicks(agent_version_id=a2.id))

        assert (refusal.code, refusal.message, refusal.details) == (
            "version_not_pickable",
            "This older agent version can't be run on its own",
            {"piece": "agent"},
        )

    def test_every_refused_agent_pick_reads_the_same(self, user, workspace, storage):
        environment = _environment(user, workspace, "pk-a-same", storage)
        stray = _agent_version(_definition(user, workspace, name="Neighbour"))
        a2 = _second_agent(environment, preflight_status="failed")

        refusals = [
            _refusal(environment, VersionPicks(agent_version_id=agent_id)).as_dict()
            for agent_id in (stray.id, a2.id, uuid.uuid4())
        ]

        assert (
            refusals
            == [
                {
                    "error": "version_not_pickable",
                    "message": "This agent version can't be picked for this environment",
                    "retryable": False,
                    "piece": "agent",
                }
            ]
            * 3
        )

    def test_the_refusal_names_the_piece(
        self, auth_client, user, workspace, storage, settings
    ):
        environment = _environment(user, workspace, "pk-piece", storage)

        response = _post_run(
            auth_client,
            environment,
            {
                "scenario_ids": [_key(NAMES[0])],
                "trials": 1,
                "suite_version_id": str(uuid.uuid4()),
            },
        )
        not_runnable = _refusal(
            environment,
            VersionPicks(suite_version_id=_suite(environment, 1).id),
            [ONLY_IN_S1],
        )
        settings.HARNESS_VERSIONING_ENABLED = False
        whole = _refusal(
            environment, VersionPicks(suite_version_id=_suite(environment, 1).id)
        )

        assert response.status_code == 422
        assert response.json() == {
            "error": "version_not_pickable",
            "message": "This suite version can't be picked for this environment",
            "retryable": False,
            "piece": "suite",
        }
        assert set(not_runnable.as_dict()) == {"error", "message", "retryable"}
        assert whole.as_dict() == {
            "error": "version_not_pickable",
            "message": "Versions can't be picked for this environment",
            "retryable": False,
            "piece": None,
        }

    def test_nothing_is_written_when_a_pick_is_refused(self, user, workspace, storage):
        environment = _environment(user, workspace, "pk-no-write", storage)
        runs = HostedHarnessJob.no_workspace_objects.filter(
            environment=environment
        ).count()
        bundles = HarnessAssembledBundle._base_manager.count()

        _refused(environment, VersionPicks(suite_version_id=_suite(environment, 1).id))

        assert (
            HostedHarnessJob.no_workspace_objects.filter(
                environment=environment
            ).count()
            == runs
        )
        assert HarnessAssembledBundle._base_manager.count() == bundles


class TestPickedRun:
    def test_a_cached_older_suite_runs_with_its_own_scenarios(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-s1", storage)
        s1 = _suite(environment, 1)
        row = _ready(
            environment,
            environment.agent_version_id,
            environment.environment_version,
            s1,
        )

        run = _create(environment, VersionPicks(suite_version_id=s1.id), [ONLY_IN_S1])

        assert _pins(run) == (
            environment.agent_version_id,
            environment.environment_version_id,
            s1.id,
        )
        assert run.assembled_bundle_id == row.id
        metadata = run.payload["metadata"]
        assert metadata["version_pins"]["suite_version_id"] == str(s1.id)
        assert metadata["version_pins"]["suite_hash"] == s1.hash
        assert metadata["assembly"]["cached"] is True
        assert metadata["assembly"]["defaults"] is False
        assert metadata["selected_scenario_keys"] == [ONLY_IN_S1]
        assert [entry["scenario_key"] for entry in metadata["execution_manifest"]] == [
            ONLY_IN_S1
        ]

    def test_the_defaults_suite_does_not_have_the_older_scenario(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-s2", storage)

        with pytest.raises(HostedHarnessError) as caught:
            _create(environment, None, [ONLY_IN_S1])

        assert caught.value.code == "scenario_selection_unknown"
        assert caught.value.message == (
            f"{ONLY_IN_S1} is not in this environment's current scenarios; select"
            " from those it has now"
        )

    def test_a_key_outside_the_picked_suite_names_that_suite(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-s1-unknown", storage)
        s1 = _suite(environment, 1)
        _ready(
            environment,
            environment.agent_version_id,
            environment.environment_version,
            s1,
        )

        with pytest.raises(HostedHarnessError) as caught:
            _create(environment, VersionPicks(suite_version_id=s1.id), ["nowhere"])

        assert caught.value.code == "scenario_selection_unknown"
        assert caught.value.message == (
            "nowhere is not in scenario version v1; select from the scenarios that"
            " version has"
        )

    def test_a_cached_older_environment_runs_with_its_own_outputs(
        self, user, workspace, storage
    ):
        from simulate.services.hosted_harness_conversation import (
            _CHECKPOINT_STAGE_KINDS,
        )

        environment = _environment(user, workspace, "pk-e1", storage)
        e1 = environment.environment_version
        record_authoring(
            environment,
            _authoring(names=NAMES[:1], contract={"agent": "other", "tools": []}),
            origin="authoring",
        )
        environment.refresh_from_db()
        assert environment.environment_version_id != e1.id
        _ready(environment, environment.agent_version_id, e1, environment.suite_version)

        run = _create(environment, VersionPicks(environment_version_id=e1.id))

        assert _pins(run) == (
            environment.agent_version_id,
            e1.id,
            environment.suite_version_id,
        )
        metadata = run.payload["metadata"]
        assert metadata["version_pins"]["environment_hash"] == e1.hash
        assert metadata["assembly"]["defaults"] is False
        pinned = [
            output
            for output in (e1.documents or {}).get("stage_outputs") or []
            if output.get("kind") in _CHECKPOINT_STAGE_KINDS
        ]
        assert pinned
        for output in pinned:
            assert output in run.stage_outputs

    def test_picking_the_current_agent_pins_it(self, user, workspace, storage):
        environment = _environment(user, workspace, "pk-a1", storage)
        s1 = _suite(environment, 1)
        _ready(
            environment,
            environment.agent_version_id,
            environment.environment_version,
            s1,
        )

        run = _create(
            environment,
            VersionPicks(
                agent_version_id=environment.agent_version_id, suite_version_id=s1.id
            ),
            [ONLY_IN_S1],
        )

        assert _pins(run) == (
            environment.agent_version_id,
            environment.environment_version_id,
            s1.id,
        )
        assert run.test_execution.agent_version_id == environment.agent_version_id
        assert run.payload["metadata"]["version_pins"]["agent_hash"] == (
            _runtime(environment.agent_version_id).hash
        )

    def test_an_uncached_pick_is_refused_while_building_is_off(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-miss-off", storage)

        refusal = _refusal(
            environment,
            VersionPicks(suite_version_id=_suite(environment, 1).id),
            [ONLY_IN_S1],
        )

        assert refusal.code == "version_not_runnable"
        assert refusal.message == (
            "These versions haven't been run together yet and building them is"
            " turned off"
        )

    def test_a_pick_of_an_agent_never_assembled_is_refused_as_such(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-miss-noside", storage)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=environment.agent_version_id
        ).delete()

        refusal = _refusal(
            environment,
            VersionPicks(suite_version_id=_suite(environment, 1).id),
            [ONLY_IN_S1],
        )

        assert refusal.code == "version_not_runnable"
        assert refusal.message == "These versions can't be run together yet"

    def test_an_uncached_pick_is_built_when_building_is_on(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-miss-on", storage)
        s1 = _suite(environment, 1)

        run = _create(environment, VersionPicks(suite_version_id=s1.id), [ONLY_IN_S1])

        assert run.suite_version_id == s1.id
        assert run.payload["metadata"]["assembly"]["cached"] is False
        row = HarnessAssembledBundle.no_workspace_objects.get(
            id=run.assembled_bundle_id
        )
        assert row.state == "pending"

    def test_the_202_names_the_picked_versions_and_the_miss(
        self, auth_client, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-miss-202", storage)
        s1 = _suite(environment, 1)

        response = _post_run(
            auth_client,
            environment,
            {"scenario_ids": [ONLY_IN_S1], "trials": 1, "suite_version_id": str(s1.id)},
        )

        assert response.status_code == 202, response.content
        body = response.json()
        assert body["versions"]["suite"] == {"id": str(s1.id), "label": "v1"}
        assert [one["code"] for one in body["warnings"]] == [
            "suite_unproved",
            "bundle_cache_miss",
        ]

    def test_a_refused_pick_is_a_422_from_the_run_endpoint(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-422", storage)

        response = _post_run(
            auth_client,
            environment,
            {
                "scenario_ids": [ONLY_IN_S1],
                "trials": 1,
                "suite_version_id": str(_suite(environment, 1).id),
            },
        )

        assert response.status_code == 422
        assert response.json()["error"] == "version_not_runnable"

    def test_picks_win_over_a_reruns_pins(self, user, workspace, storage):
        environment = _environment(user, workspace, "pk-rerun", storage)
        source = _create(environment, None, key="source")
        s1 = _suite(environment, 1)
        _ready(
            environment,
            environment.agent_version_id,
            environment.environment_version,
            s1,
        )

        run = _create(
            environment,
            VersionPicks(suite_version_id=s1.id),
            [ONLY_IN_S1],
            key="rerun",
            rerun_of=source,
        )

        assert _pins(run) == (
            source.agent_version_id,
            source.environment_version_id,
            s1.id,
        )
        assert run.payload["metadata"]["rerun_of"] == str(source.id)

    def test_a_pick_on_a_rerun_of_a_picked_run_keeps_its_other_pins(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-rerun-kept", storage)
        a1 = AgentVersion.no_workspace_objects.get(id=environment.agent_version_id)
        s1 = _suite(environment, 1)
        _ready(environment, a1.id, environment.environment_version, s1)
        source = _create(
            environment, VersionPicks(suite_version_id=s1.id), key="source"
        )
        payload = dict(source.payload)
        payload["agent"] = {**payload["agent"], "marker": "the source's agent"}
        HostedHarnessJob.no_workspace_objects.filter(id=source.id).update(
            payload=payload
        )
        source.refresh_from_db()
        # E2 becomes the default, so the source's E1 and S1 are both off the defaults.
        e1 = _older_environment(environment, a1)
        assert environment.environment_version_id != e1.id

        run = _create(
            environment,
            VersionPicks(suite_version_id=environment.suite_version_id),
            key="rerun",
            rerun_of=source,
        )

        assert _pins(run) == (a1.id, e1.id, environment.suite_version_id)
        assert run.payload["metadata"]["rerun_of"] == str(source.id)
        # The same agent as the source, so its agent config is carried over.
        assert run.payload["agent"] == source.payload["agent"]
        assert run.payload["agent"] != environment.payload["agent"]

    def test_the_202_warns_of_an_agent_the_environment_was_not_built_with(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-202-differs", storage)
        e1 = _older_environment(environment, _second_agent(environment))

        response = _post_run(
            auth_client,
            environment,
            {
                "scenario_ids": [_key(NAMES[0])],
                "trials": 1,
                "environment_version_id": str(e1.id),
            },
        )

        assert response.status_code == 202, response.content
        assert {
            "code": "agent_differs",
            "message": (
                "Agent v1 differs from the agent that environment v1 was built with"
            ),
            "piece": "agent",
        } in response.json()["warnings"]


class TestIdempotentRepeat:
    def test_the_same_pins_return_the_run_and_write_nothing(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-idem-same", storage)
        picks = VersionPicks(suite_version_id=_suite(environment, 1).id)
        run = _create(environment, picks, [ONLY_IN_S1], key="same")
        row = HarnessAssembledBundle._base_manager.get(id=run.assembled_bundle_id)
        _mark(row, "failed")

        with CaptureQueriesContext(connection) as queries:
            again, created = create_selected_harness_run(
                environment,
                scenario_keys=[ONLY_IN_S1],
                trials=1,
                idempotency_key="same",
                picks=picks,
            )

        assert created is False
        assert again.id == run.id
        assert _writes(queries) == []
        row.refresh_from_db()
        assert row.state == "failed"

    @pytest.mark.parametrize("deleted_before", [False, True])
    def test_a_soft_deleted_agent_pin_still_replays(
        self, user, workspace, storage, deleted_before
    ):
        environment = _environment(user, workspace, "pk-idem-deleted", storage)

        def delete_agent():
            AgentVersion._base_manager.filter(id=environment.agent_version_id).update(
                deleted=True
            )

        if deleted_before:
            delete_agent()
        run = _create(environment, None, key="same")
        if not deleted_before:
            delete_agent()

        again, created = create_selected_harness_run(
            environment,
            scenario_keys=[_key(NAMES[0])],
            trials=1,
            idempotency_key="same",
        )

        assert created is False
        assert again.id == run.id

    def test_the_same_key_for_other_scenarios_is_still_a_conflict(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-idem-keys", storage)
        _create(environment, None, key="same")

        with pytest.raises(HostedHarnessError) as caught:
            create_selected_harness_run(
                environment,
                scenario_keys=[_key(NAMES[0])],
                trials=2,
                idempotency_key="same",
            )

        assert caught.value.code == "idempotency_conflict"
        assert caught.value.status_code == 409

    def test_the_same_key_for_other_pins_is_refused_without_writing(
        self, auth_client, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-idem-other", storage)
        body = {"scenario_ids": [_key(NAMES[0])], "trials": 1}
        first = _post_run(auth_client, environment, body, key="same")
        assert first.status_code == 202, first.content
        runs = HostedHarnessJob.no_workspace_objects.count()
        bundles = HarnessAssembledBundle._base_manager.count()

        with CaptureQueriesContext(connection) as queries:
            response = _post_run(
                auth_client,
                environment,
                {**body, "suite_version_id": str(_suite(environment, 1).id)},
                key="same",
            )

        assert response.status_code == 409
        assert response.json() == {
            "error": "idempotency_key_reused",
            "message": "This request key was already used for a run of other"
            " versions; start a new run",
            "retryable": False,
        }
        assert HostedHarnessJob.no_workspace_objects.count() == runs
        assert HarnessAssembledBundle._base_manager.count() == bundles
        assert not [sql for sql in _writes(queries) if "harness" in sql.lower()]

    def test_a_repeated_202_reads_as_the_run_recorded_it(
        self, auth_client, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-idem-202", storage)
        s1 = _suite(environment, 1)
        body = {
            "scenario_ids": [ONLY_IN_S1],
            "trials": 1,
            "suite_version_id": str(s1.id),
        }
        first = _post_run(auth_client, environment, body, key="same")
        assert first.status_code == 202, first.content
        run = HostedHarnessJob.no_workspace_objects.get(id=first.json()["job_id"])
        # The run prepared its bundle, and S1 has since become the default suite.
        _ready(
            environment,
            environment.agent_version_id,
            environment.environment_version,
            s1,
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            suite_version=s1
        )
        environment.refresh_from_db()

        again = _post_run(auth_client, environment, body, key="same")

        assert again.status_code == 202, again.content
        assert again.json()["job_id"] == str(run.id)
        assert again.json()["warnings"] == first.json()["warnings"]
        assert first.json()["warnings"][-1]["code"] == "bundle_cache_miss"
        assert job_pins(environment, run).defaults is False


class TestWarnings:
    def _warnings(self, environment, picks=None):
        return run_warnings(environment, resolve_run_pins(environment, picks=picks))

    def test_a_proved_default_pair_has_no_warnings(self, user, workspace, storage):
        environment = _environment(user, workspace, "pk-w-none", storage)
        HarnessSuiteValidation.no_workspace_objects.create(
            suite_version=environment.suite_version,
            environment_version=environment.environment_version,
            status="passed",
            proved_at=timezone.now(),
        )

        assert self._warnings(environment) == []

    def test_an_unproved_suite(self, user, workspace, storage):
        environment = _environment(user, workspace, "pk-w-unproved", storage)
        HarnessSuiteValidation.no_workspace_objects.create(
            suite_version=environment.suite_version,
            environment_version=environment.environment_version,
            status="partial",
            proved_at=timezone.now(),
        )

        assert self._warnings(environment) == [
            {
                "code": "suite_unproved",
                "message": "Suite v2 hasn't been proved against environment v1",
                "piece": "suite",
            }
        ]

    def test_a_version_without_a_number_is_never_named_none(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-w-unnumbered", storage)
        pins = resolve_run_pins(environment)
        pins.suite_version.version_number = None

        assert {
            "code": "suite_unproved",
            "message": "Suite an unnumbered version hasn't been proved against"
            " environment v1",
            "piece": "suite",
        } in run_warnings(environment, pins)

    def test_an_agent_other_than_the_environment_was_built_with(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-w-differs", storage)
        e1 = _older_environment(environment, _second_agent(environment))

        warnings = self._warnings(
            environment, VersionPicks(environment_version_id=e1.id)
        )

        assert {
            "code": "agent_differs",
            "message": (
                "Agent v1 differs from the agent that environment v1 was built with"
            ),
            "piece": "agent",
        } in warnings

    def test_no_differs_when_the_environment_names_no_agent(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-w-no-agent", storage)
        HarnessEnvironmentVersion._base_manager.filter(
            id=environment.environment_version_id
        ).update(agent_version=None)

        warnings = self._warnings(environment)

        assert "agent_differs" not in [one["code"] for one in warnings]

    def test_a_failed_cache_row_of_the_defaults_takes_todays_path(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-w-failed-default", storage)
        row = _ready(
            environment,
            environment.agent_version_id,
            environment.environment_version,
            environment.suite_version,
        )
        _mark(row, "failed")

        codes = [one["code"] for one in self._warnings(environment)]

        assert "bundle_cache_failed" not in codes
        assert "bundle_cache_miss" not in codes

    def test_a_failed_cache_row(self, user, workspace, storage, settings):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-w-failed", storage)
        s1 = _suite(environment, 1)
        row = _ready(
            environment,
            environment.agent_version_id,
            environment.environment_version,
            s1,
        )
        _mark(row, "failed")

        warnings = self._warnings(environment, VersionPicks(suite_version_id=s1.id))

        assert warnings[-1] == {
            "code": "bundle_cache_failed",
            "message": "The last preparation of this combination failed and will be"
            " retried",
            "piece": None,
        }

    def test_a_miss_the_run_will_prepare(self, user, workspace, storage, settings):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-w-miss", storage)

        warnings = self._warnings(
            environment, VersionPicks(suite_version_id=_suite(environment, 1).id)
        )

        assert warnings[-1] == {
            "code": "bundle_cache_miss",
            "message": "The first run of this combination prepares it, which takes"
            " several minutes",
            "piece": None,
        }

    def test_a_miss_of_the_defaults_takes_todays_path_without_a_warning(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-w-default-miss", storage)
        HarnessAssembledBundle._base_manager.filter(job=environment).delete()

        codes = [one["code"] for one in self._warnings(environment)]

        assert "bundle_cache_miss" not in codes

    def test_versioning_off_has_no_warnings(self, user, workspace, storage, settings):
        environment = _environment(user, workspace, "pk-w-off", storage)
        settings.HARNESS_VERSIONING_ENABLED = False

        assert preview_run(environment) == {
            "versions": {"agent": None, "environment": None, "suite": None},
            "warnings": [],
            "bundle_cached": None,
        }


class TestRunPlan:
    def _plan(self, auth_client, environment, **params):
        return auth_client.get(f"{BASE}/{environment.id}/run-plan/", params or None)

    def test_the_defaults(self, auth_client, user, workspace, storage):
        environment = _environment(user, workspace, "pk-plan", storage)
        _ready(
            environment,
            environment.agent_version_id,
            environment.environment_version,
            environment.suite_version,
        )

        response = self._plan(auth_client, environment)

        assert response.status_code == 200, response.content
        assert response.json() == {
            "versions": {
                "agent": {"id": str(environment.agent_version_id), "label": "v1"},
                "environment": {
                    "id": str(environment.environment_version_id),
                    "label": "v1",
                },
                "suite": {"id": str(environment.suite_version_id), "label": "v2"},
            },
            "warnings": [
                {
                    "code": "suite_unproved",
                    "message": "Suite v2 hasn't been proved against environment v1",
                    "piece": "suite",
                }
            ],
            "bundle_cached": True,
        }

    def test_a_pick_writes_nothing(
        self, auth_client, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-plan-pure", storage)
        s1 = _suite(environment, 1)
        bundles = HarnessAssembledBundle._base_manager.count()

        with CaptureQueriesContext(connection) as queries:
            response = self._plan(auth_client, environment, suite_version_id=str(s1.id))

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["versions"]["suite"] == {"id": str(s1.id), "label": "v1"}
        assert body["bundle_cached"] is False
        assert body["warnings"][-1]["code"] == "bundle_cache_miss"
        assert HarnessAssembledBundle._base_manager.count() == bundles
        writes = [
            query["sql"]
            for query in queries.captured_queries
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        assert writes == []

    def test_refusals_match_run_create(self, auth_client, user, workspace, storage):
        environment = _environment(user, workspace, "pk-plan-422", storage)

        not_runnable = self._plan(
            auth_client, environment, suite_version_id=str(_suite(environment, 1).id)
        )
        not_pickable = self._plan(
            auth_client, environment, environment_version_id=str(uuid.uuid4())
        )

        assert not_runnable.status_code == 422
        assert not_runnable.json()["error"] == "version_not_runnable"
        assert not_pickable.status_code == 422
        assert not_pickable.json()["error"] == "version_not_pickable"

    def test_versioning_off_is_not_found(
        self, auth_client, user, workspace, storage, settings
    ):
        environment = _environment(user, workspace, "pk-plan-off", storage)
        settings.HARNESS_VERSIONING_ENABLED = False

        response = self._plan(auth_client, environment)

        assert response.status_code == 404
        assert response.json()["error"] == "versioning_disabled"

    def test_another_workspace_is_not_found(
        self, auth_client, user, workspace, storage
    ):
        other = Workspace.objects.create(
            name="Other",
            organization=workspace.organization,
            is_active=True,
            created_by=user,
        )
        environment = _environment(user, workspace, "pk-plan-scope", storage)
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            workspace=other
        )

        response = self._plan(auth_client, environment)

        assert response.status_code == 404
        assert response.json() == {"detail": "Environment not found"}

    def test_a_failed_row_of_the_defaults_warns_neither_before_nor_after(
        self, auth_client, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-plan-failed", storage)
        row = _ready(
            environment,
            environment.agent_version_id,
            environment.environment_version,
            environment.suite_version,
        )
        _mark(row, "failed")

        plan = self._plan(auth_client, environment).json()
        response = _post_run(
            auth_client, environment, {"scenario_ids": [_key(NAMES[0])], "trials": 1}
        )

        assert response.status_code == 202, response.content
        assert [one["code"] for one in plan["warnings"]] == ["suite_unproved"]
        assert plan["bundle_cached"] is None
        assert response.json()["warnings"] == plan["warnings"]

    def test_a_failed_row_of_a_pick_warns_the_same_before_and_after_the_run(
        self, auth_client, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "pk-plan-failed-pick", storage)
        s1 = _suite(environment, 1)
        row = _ready(
            environment,
            environment.agent_version_id,
            environment.environment_version,
            s1,
        )
        _mark(row, "failed")
        body = {
            "scenario_ids": [ONLY_IN_S1],
            "trials": 1,
            "suite_version_id": str(s1.id),
        }

        plan = self._plan(auth_client, environment, suite_version_id=str(s1.id)).json()
        response = _post_run(auth_client, environment, body)
        repeated = _post_run(auth_client, environment, body)

        assert response.status_code == 202, response.content
        assert plan["warnings"][-1]["code"] == "bundle_cache_failed"
        assert plan["bundle_cached"] is False
        assert response.json()["warnings"] == plan["warnings"]
        assert repeated.json()["job_id"] == response.json()["job_id"]
        assert repeated.json()["warnings"] == plan["warnings"]
        row.refresh_from_db()
        assert row.state == "pending"

    @pytest.mark.parametrize("certify", [True, False])
    def test_an_uncached_default_run_is_not_assembled(
        self, auth_client, user, workspace, storage, settings, certify
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = certify
        environment = _environment(user, workspace, f"pk-plan-none-{certify}", storage)
        HarnessAssembledBundle._base_manager.filter(job=environment).delete()

        response = self._plan(auth_client, environment)

        assert response.status_code == 200, response.content
        assert response.json()["bundle_cached"] is None

    def test_an_unknown_parameter_is_refused(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-plan-unknown", storage)

        response = self._plan(auth_client, environment, agent="v1")

        assert response.status_code == 400


class TestRunLabels:
    def test_a_run_of_another_agent_is_tagged(self, user, workspace, storage):
        environment = _environment(user, workspace, "pk-label", storage)
        e1 = _older_environment(environment, _second_agent(environment))
        default = _create(environment, None, key="default")
        other = _create(
            environment, VersionPicks(environment_version_id=e1.id), key="e1"
        )

        labels = run_version_labels(
            [default.test_execution_id, other.test_execution_id]
        )

        assert labels[str(default.test_execution_id)]["agent_version"] == {
            "id": str(environment.agent_version_id),
            "label": "v1",
        }
        assert labels[str(default.test_execution_id)]["agent_differs"] is False
        assert labels[str(other.test_execution_id)]["agent_version"] == {
            "id": str(environment.agent_version_id),
            "label": "v1",
        }
        assert labels[str(other.test_execution_id)]["agent_differs"] is True

    def test_both_run_payloads_carry_the_agent_pin(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pk-label-api", storage)
        e1 = _older_environment(environment, _second_agent(environment))
        run = _create(
            environment,
            VersionPicks(
                agent_version_id=environment.agent_version_id,
                environment_version_id=e1.id,
            ),
            key="a1",
        )
        legacy = _legacy(user, workspace, "pk-label-legacy", storage)
        old = _create(legacy, None, key="old")

        rows = auth_client.get(
            f"/simulate/run-tests/{environment.run_test_id}/executions/"
        ).json()["results"]
        [row] = [one for one in rows if one["id"] == str(run.test_execution_id)]
        header = auth_client.get(
            f"/simulate/v3/test-executions/{run.test_execution_id}/calls/"
        ).json()["execution"]
        old_header = auth_client.get(
            f"/simulate/v3/test-executions/{old.test_execution_id}/calls/"
        ).json()["execution"]

        for payload in (row, header):
            assert payload["pinned_agent_version"] == {
                "id": str(environment.agent_version_id),
                "label": "v1",
            }
            assert payload["agent_differs"] is True
            assert payload["agent_version"] == "v1"
        assert old_header["pinned_agent_version"] is None
        assert old_header["agent_differs"] is False
