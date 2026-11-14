"""Held-back cases and verify: what a coding agent may read and run, and the counts it
gets back when it verifies an agent version on the cases it never saw."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from accounts.models import OrgApiKey
from simulate.models import (
    CallExecution,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HostedHarnessJob,
)
from simulate.models import TestExecution as Execution
from simulate.models.harness_versioning import VersionStatus
from simulate.selectors.held_back import held_back, verify_run, version_verify
from simulate.selectors.runs import user_environment_runs, user_executions, user_runs
from simulate.services.harness_environment import annotate_for_list, environment_row
from simulate.services.harness_verify import (
    ensure_held_back,
    pick_held_back,
    start_verify,
)
from simulate.services.harness_versioning.versions import (
    move_default,
    record_authoring,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_selected_harness_run,
    project_terminal_job_onto_run,
)
from simulate.services.hosted_harness_gateway import RUNTIME_VALIDATION_FAILED
from simulate.services.run_compare import wilson_interval
from simulate.tests.test_agent_fix_services import (
    KEYS,
    _executions,
    _receipt,
)
from simulate.tests.test_agent_fix_services import (  # noqa: F401 - fixture
    environment as environment,
)
from simulate.tests.test_harness_agent_versions import _environment
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_run_compare import _done
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_picks import _second_agent
from simulate.tests.test_harness_versioning_record import _authoring

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
VERIFY_FIELDS = {
    "state",
    "passed",
    "failed",
    "total",
    "trials",
    "held_back_pass_rate",
    "limited_validation",
}


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True


@pytest.fixture(autouse=True)
def temporal():
    """The verify run's workflow and the usage check, as mocks."""
    with (
        patch(
            "simulate.services.harness_verify.start_hosted_harness_gateway_workflow"
        ) as start,
        patch("simulate.services.harness_verify.require_harness_call_usage") as usage,
        patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"),
    ):
        yield SimpleNamespace(start=start, usage=usage)


def _suite_keys(environment):
    return sorted(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=environment.suite_version_id
        ).values_list("scenario_key", flat=True)
    )


def _api_key(user, workspace, kind, environment=None):
    return OrgApiKey.no_workspace_objects.create(
        name=kind,
        organization=user.organization,
        workspace=workspace,
        type=kind,
        **({"minted_by": user} if kind == "coding_agent" else {"user": user}),
        simulation_environment_id=environment.id if environment else None,
    )


@pytest.fixture
def keys(user, workspace, environment):
    """A coding agent's key for the environment, and an ordinary user key of the
    same person."""
    return SimpleNamespace(
        coder=_api_key(user, workspace, "coding_agent", environment),
        user=_api_key(user, workspace, "user"),
    )


def _as(api_client, key, method, path, body=None, **headers):
    return getattr(api_client, method)(
        path,
        body,
        format="json",
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        **headers,
    )


def _verify(environment, agent_version, trials=2):
    return start_verify(environment, agent_version_id=agent_version.id, trials=trials)


class TestPickHeldBack:
    def test_about_a_third_of_the_suite_every_time(self):
        keys = [f"case-{number}" for number in range(30)]

        picked = pick_held_back("env-1", keys)

        assert len(picked) == 10
        assert set(picked) <= set(keys)
        assert pick_held_back("env-1", list(reversed(keys))) == picked
        assert pick_held_back("env-2", keys) != picked

    @pytest.mark.parametrize(
        ("size", "held"), [(1, 0), (2, 1), (3, 1), (5, 2), (12, 4)]
    )
    def test_a_small_suite_still_holds_back_its_share(self, size, held):
        assert len(pick_held_back("env", [f"k{n}" for n in range(size)])) == held


class TestChoosingTheSet:
    def test_chosen_once_from_the_current_suite_and_kept(self, environment):
        drawn_from = environment.suite_version_id

        chosen = ensure_held_back(environment)

        assert chosen.suite_version_id == drawn_from
        assert len(chosen.scenario_keys) == 1
        assert set(chosen.scenario_keys) <= set(_suite_keys(environment))
        assert ensure_held_back(environment) == chosen
        environment.refresh_from_db()
        assert held_back(environment) == chosen

    def test_no_run_s_payload_carries_the_set(self, environment):
        ensure_held_back(environment)

        run, _ = create_selected_harness_run(
            environment,
            scenario_keys=_suite_keys(environment),
            trials=1,
            idempotency_key="human",
        )

        assert "verify_held_back" not in run.payload["metadata"]


def _hold(environment, keys):
    """The set as if chosen from the current suite: ``keys``, not the hash's pick."""
    metadata = {
        **environment.payload["metadata"],
        "verify_held_back": {
            "suite_version_id": str(environment.suite_version_id),
            "scenario_keys": sorted(keys),
            "seen_keys": sorted(set(_suite_keys(environment)) - set(keys)),
        },
    }
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        payload={**environment.payload, "metadata": metadata}
    )
    environment.refresh_from_db()


def _new_default_suite(environment, user, names):
    record_authoring(environment, _authoring(names=names), origin="manual", user=user)
    environment.refresh_from_db()


def _revert(environment, suite_version_id):
    """Make an older suite version the default again."""
    move_default(
        environment,
        "suite",
        suite_version_id,
        expected_default_id=environment.suite_version_id,
    )
    environment.refresh_from_db()


class TestWhenTheSuiteChanges:
    """A case the coding agent could read never becomes held back, and a held-back
    case never becomes readable."""

    ADDED = ("refund_late_order", "update_home_address", "report_lost_item")

    def test_a_third_of_the_new_cases_join_and_the_old_ones_stay(
        self, environment, user
    ):
        old_suite = environment.suite_version_id
        _hold(environment, [KEYS[1]])
        _new_default_suite(environment, user, [*NAMES[:2], *self.ADDED])
        added = [_key(name) for name in self.ADDED]

        chosen = ensure_held_back(environment)

        assert chosen.suite_version_id == environment.suite_version_id != old_suite
        joined = pick_held_back(str(environment.id), added)
        assert len(joined) == 1
        assert chosen.scenario_keys == tuple(sorted([KEYS[1], *joined]))
        assert KEYS[0] not in chosen.scenario_keys
        environment.refresh_from_db()
        assert held_back(environment) == chosen

    def test_a_dropped_held_back_case_stays_held_back_when_a_revert_brings_it_back(
        self, api_client, environment, user, keys
    ):
        old_suite = environment.suite_version_id
        _hold(environment, [KEYS[1]])
        # The new suite drops the held-back case and adds one, too few to hold back.
        _new_default_suite(environment, user, [NAMES[0], NAMES[2]])
        ensure_held_back(environment)

        with pytest.raises(HostedHarnessError) as refused:
            _verify(environment, _second_agent(environment))
        _revert(environment, old_suite)
        members = _as(
            api_client,
            keys.coder,
            "get",
            f"{BASE}/{environment.id}/versions/suite/{environment.suite_version_id}/members/",
        )

        assert refused.value.code == "held_back_empty"
        assert members.status_code == 200, members.content
        assert [row["scenario_key"] for row in members.json()] == [KEYS[0]]
        assert held_back(environment).scenario_keys == (KEYS[1],)

    def test_a_seen_case_stays_shown_when_a_revert_brings_it_back(
        self, environment, user
    ):
        _hold(environment, [KEYS[1]])
        _new_default_suite(environment, user, [*NAMES[:2], *self.ADDED])
        wide = environment.suite_version_id
        joined = ensure_held_back(environment).scenario_keys
        # The next suite drops every seen case; a revert brings them all back.
        _new_default_suite(environment, user, [NAMES[1]])
        ensure_held_back(environment)
        _revert(environment, wide)

        chosen = ensure_held_back(environment)

        assert len(joined) == 2
        assert chosen.scenario_keys == joined
        assert chosen.suite_version_id == environment.suite_version_id

    def test_a_coding_agent_reads_and_picks_only_the_default_suite(
        self, api_client, environment, user, keys
    ):
        old_suite = environment.suite_version_id
        _hold(environment, [KEYS[1]])
        _new_default_suite(environment, user, [*NAMES[:2], *self.ADDED])
        old = f"{BASE}/{environment.id}/versions/suite/{old_suite}/members/"

        coder = _as(api_client, keys.coder, "get", old)
        person = _as(api_client, keys.user, "get", old)
        plan = _as(
            api_client,
            keys.coder,
            "get",
            f"{BASE}/{environment.id}/run-plan/?suite_version_id={old_suite}",
        )
        run = _as(
            api_client,
            keys.coder,
            "post",
            f"{BASE}/{environment.id}/run/",
            {
                "scenario_ids": [KEYS[0]],
                "trials": 1,
                "suite_version_id": str(old_suite),
            },
            HTTP_IDEMPOTENCY_KEY="old-suite",
        )

        assert coder.status_code == plan.status_code == run.status_code == 404
        assert run.json()["error"] == "version_not_found", run.content
        assert person.status_code == 200, person.content
        assert [row["scenario_key"] for row in person.json()] == KEYS

    def test_a_completed_verify_stays_final_once_its_cases_are_dropped(
        self, environment, user
    ):
        _hold(environment, [KEYS[1]])
        candidate = _second_agent(environment)
        first = _verify(environment, candidate)
        HostedHarnessJob.no_workspace_objects.filter(id=first.id).update(
            state=HostedHarnessJob.State.COMPLETED
        )
        _new_default_suite(environment, user, [NAMES[0], NAMES[2]])

        assert _verify(environment, candidate).id == first.id

    def test_a_discarded_pinned_suite_does_not_stop_a_verify(self, environment, user):
        old_suite = environment.suite_version_id
        _hold(environment, [KEYS[1]])
        _new_default_suite(environment, user, [*NAMES[:2], *self.ADDED])
        HarnessSuiteVersion.no_workspace_objects.filter(id=old_suite).update(
            status=VersionStatus.DISCARDED
        )

        run = _verify(environment, _second_agent(environment))

        assert run.suite_version_id == environment.suite_version_id
        assert KEYS[1] in run.payload["metadata"]["selected_scenario_keys"]
        assert KEYS[0] not in run.payload["metadata"]["selected_scenario_keys"]


class TestVerify:
    def test_runs_the_held_back_cases_at_n_trials_pinned_to_their_suite(
        self, environment, temporal, django_capture_on_commit_callbacks
    ):
        candidate = _second_agent(environment)
        chosen = ensure_held_back(environment)

        with django_capture_on_commit_callbacks(execute=True):
            run = _verify(environment, candidate, trials=3)

        run.refresh_from_db()
        metadata = run.payload["metadata"]
        assert run.verify_of_id == environment.id
        assert run.agent_version_id == candidate.id
        assert run.suite_version_id == chosen.suite_version_id
        assert metadata["selected_scenario_keys"] == list(chosen.scenario_keys)
        assert metadata["trials"] == 3
        assert _executions(run).count() == 3 * len(chosen.scenario_keys)
        assert "coding_agent_key_id" not in metadata
        temporal.usage.assert_called_once()
        temporal.start.assert_called_once()
        assert temporal.start.call_args.args[0] == str(run.id)

    def test_trials_shrink_to_fit_the_run_cap(self, environment, settings):
        settings.HARNESS_MAX_EXECUTIONS_PER_RUN = 2
        cases = len(ensure_held_back(environment).scenario_keys)

        run = _verify(environment, _second_agent(environment), trials=5)

        assert run.payload["metadata"]["trials"] == 2 // cases
        assert _executions(run).count() == cases * (2 // cases)

    def test_a_version_is_verified_once(self, environment, temporal):
        candidate = _second_agent(environment)

        first = _verify(environment, candidate, trials=2)
        again = _verify(environment, candidate, trials=5)

        assert again.id == first.id
        assert (
            HostedHarnessJob.no_workspace_objects.filter(verify_of=environment).count()
            == 1
        )
        temporal.usage.assert_called_once()

    def test_a_completed_verify_is_final(self, environment):
        candidate = _second_agent(environment)
        first = _verify(environment, candidate)
        HostedHarnessJob.no_workspace_objects.filter(id=first.id).update(
            state=HostedHarnessJob.State.COMPLETED
        )

        assert _verify(environment, candidate, trials=5).id == first.id

    def test_a_verify_that_could_not_be_scheduled_runs_again(
        self, environment, temporal, django_capture_on_commit_callbacks
    ):
        candidate = _second_agent(environment)
        temporal.start.side_effect = RuntimeError("temporal down")
        with django_capture_on_commit_callbacks(execute=True):
            first = _verify(environment, candidate)
        first.refresh_from_db()
        assert first.failure["code"] == "scheduler_unavailable"

        temporal.start.side_effect = None
        with django_capture_on_commit_callbacks(execute=True):
            again = _verify(environment, candidate)

        assert again.id != first.id
        again.refresh_from_db()
        assert again.state == HostedHarnessJob.State.QUEUED
        assert temporal.start.call_args.args[0] == str(again.id)
        assert version_verify(environment, candidate.id) == again
        # The failed one stays out of every user read, and still reads by its id.
        assert not HostedHarnessJob.no_workspace_objects.filter(
            user_runs(), id=first.id
        ).exists()
        assert verify_run(environment, first.id) == first

    def test_a_canceled_verify_runs_again(self, environment):
        candidate = _second_agent(environment)
        first = _verify(environment, candidate)
        HostedHarnessJob.no_workspace_objects.filter(id=first.id).update(
            state=HostedHarnessJob.State.CANCELED
        )

        again = _verify(environment, candidate)

        assert again.id != first.id
        assert again.verify_of_id == environment.id

    def test_a_run_holding_the_verify_key_does_not_block_it(
        self, api_client, environment, keys
    ):
        candidate = _second_agent(environment)
        chosen = ensure_held_back(environment)
        shown = [
            key for key in _suite_keys(environment) if key not in chosen.scenario_keys
        ]
        squatter = _as(
            api_client,
            keys.coder,
            "post",
            f"{BASE}/{environment.id}/run/",
            {"scenario_ids": shown, "trials": 1},
            HTTP_IDEMPOTENCY_KEY=f"verify:{candidate.id}",
        )
        assert squatter.status_code == 202, squatter.content

        posted = _as(
            api_client,
            keys.coder,
            "post",
            f"{BASE}/{environment.id}/verify/",
            {"agent_version_id": str(candidate.id), "trials": 1},
        )

        assert posted.status_code == 202, posted.content
        verify = HostedHarnessJob.no_workspace_objects.get(
            id=posted.json()["verify_id"]
        )
        assert verify.id != uuid.UUID(squatter.json()["job_id"])
        assert verify.verify_of_id == environment.id
        assert verify.payload["metadata"]["selected_scenario_keys"] == list(
            chosen.scenario_keys
        )

    def test_another_version_gets_its_own_verify(self, environment):
        first = _verify(environment, _second_agent(environment))

        other = _verify(environment, environment.agent_version)

        assert other.id != first.id

    def test_an_environment_with_one_scenario_holds_nothing_back(self, environment):
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=environment.suite_version_id
        ).exclude(scenario_key=_suite_keys(environment)[0]).delete()

        with pytest.raises(HostedHarnessError) as refused:
            _verify(environment, _second_agent(environment))

        assert refused.value.code == "held_back_empty"


class TestVerifyApi:
    def test_post_then_get_counts_only(self, api_client, environment, keys):
        candidate = _second_agent(environment)

        posted = _as(
            api_client,
            keys.coder,
            "post",
            f"{BASE}/{environment.id}/verify/",
            {"agent_version_id": str(candidate.id), "trials": 2},
        )

        assert posted.status_code == 202, posted.content
        assert set(posted.json()) == {"verify_id", "state"}
        run = HostedHarnessJob.no_workspace_objects.get(id=posted.json()["verify_id"])
        executions = list(_executions(run).order_by("trial_index"))
        key = executions[0].source_scenario.scenario_key
        _receipt(executions[0].call_execution, key, {"refunded": True})
        _receipt(executions[1].call_execution, key, {"refunded": False})
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(
            state=HostedHarnessJob.State.COMPLETED
        )

        read = _as(
            api_client,
            keys.coder,
            "get",
            f"{BASE}/{environment.id}/verify/{run.id}/",
        )

        assert read.status_code == 200, read.content
        body = read.json()
        assert set(body) == VERIFY_FIELDS
        low, high = wilson_interval(1, 2)
        assert body == {
            "state": "completed",
            "passed": 1,
            "failed": 1,
            "total": 2,
            "trials": 2,
            "held_back_pass_rate": [round(low, 4), round(high, 4)],
            "limited_validation": True,
        }
        text = json.dumps(body)
        names = executions[0].source_scenario.scenario.name
        for hidden in (key, names, "refunded"):
            assert hidden not in text

    def test_repeat_post_returns_the_same_verify(self, api_client, environment, keys):
        candidate = _second_agent(environment)
        path = f"{BASE}/{environment.id}/verify/"

        first = _as(
            api_client,
            keys.coder,
            "post",
            path,
            {"agent_version_id": str(candidate.id)},
        )
        again = _as(
            api_client,
            keys.coder,
            "post",
            path,
            {"agent_version_id": str(candidate.id), "trials": 7},
        )

        assert first.status_code == again.status_code == 202, again.content
        assert again.json()["verify_id"] == first.json()["verify_id"]
        run = HostedHarnessJob.no_workspace_objects.get(id=first.json()["verify_id"])
        assert run.payload["metadata"]["trials"] == 3

    def test_unfinished_verify_has_no_range(self, api_client, environment, keys):
        run = _verify(environment, _second_agent(environment))

        read = _as(
            api_client, keys.coder, "get", f"{BASE}/{environment.id}/verify/{run.id}/"
        )

        assert read.json()["held_back_pass_rate"] is None
        assert read.json()["passed"] == read.json()["failed"] == 0

    def test_get_reads_only_this_environment_s_verifies(
        self, api_client, environment, keys, user, workspace, storage
    ):
        verify = _verify(environment, _second_agent(environment))
        run, _ = create_selected_harness_run(
            environment,
            scenario_keys=_suite_keys(environment),
            trials=1,
            idempotency_key="human",
        )
        other = _environment(user, workspace, "other-env", storage)

        def read(on, run_id, key=keys.coder):
            path = f"{BASE}/{on.id}/verify/{run_id}/"
            return _as(api_client, key, "get", path).status_code

        assert read(environment, verify.id) == 200
        assert read(environment, run.id) == 404
        # The coding agent's key is refused on any other environment before the view;
        # a person's key reaches the view, which finds no verify of that environment.
        assert read(other, verify.id) == 403
        assert read(other, verify.id, key=keys.user) == 404

    @pytest.mark.parametrize("trials", [0, 21])
    def test_trials_out_of_range(self, api_client, environment, keys, trials):
        posted = _as(
            api_client,
            keys.coder,
            "post",
            f"{BASE}/{environment.id}/verify/",
            {"agent_version_id": str(environment.agent_version_id), "trials": trials},
        )

        assert posted.status_code == 400


class TestVerifyRunsStayOutOfTheUsersRuns:
    @pytest.fixture
    def runs(self, environment):
        human, _ = create_selected_harness_run(
            environment,
            scenario_keys=_suite_keys(environment),
            trials=1,
            idempotency_key="human",
        )
        verify = _verify(environment, _second_agent(environment))
        for run in (human, verify):
            _done(run)
        return SimpleNamespace(human=human, verify=verify)

    def test_the_selectors(self, environment, runs):
        executions = set(user_executions().values_list("id", flat=True))
        environment_runs = set(
            user_environment_runs(environment).values_list("id", flat=True)
        )

        assert runs.human.test_execution_id in executions
        assert runs.verify.test_execution_id not in executions
        assert environment_runs == {runs.human.id}

    def test_an_execution_without_a_harness_run_stays(self, environment):
        plain = Execution.objects.create(
            run_test=environment.run_test,
            status=Execution.ExecutionStatus.COMPLETED,
        )

        assert user_executions().filter(id=plain.id).exists()

    def test_the_environments_run_count(self, environment, runs):
        annotated = annotate_for_list(
            HostedHarnessJob.no_workspace_objects.filter(id=environment.id)
        ).get()

        assert annotated.simulation_run_count == 1
        assert environment_row(environment)["runs_count"] == 1

    def test_the_run_history(self, auth_client, environment, runs):
        response = auth_client.get(
            f"/simulate/run-tests/{environment.run_test_id}/executions/"
        )

        assert response.status_code == 200, response.content
        listed = {row["id"] for row in response.json()["results"]}
        assert str(runs.human.test_execution_id) in listed
        assert str(runs.verify.test_execution_id) not in listed

    def test_the_harness_job_list(self, auth_client, runs):
        response = auth_client.get("/simulate/api/harness-jobs/")

        assert response.status_code == 200, response.content
        listed = {row["job"]["job_id"] for row in response.json()}
        assert str(runs.human.id) in listed
        assert str(runs.verify.id) not in listed


class TestRunsACodingAgentAsksFor:
    def _run(self, api_client, key, environment, scenario_keys, idempotency_key):
        return _as(
            api_client,
            key,
            "post",
            f"{BASE}/{environment.id}/run/",
            {"scenario_ids": scenario_keys, "trials": 1},
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )

    def test_a_held_back_case_reads_as_an_unknown_one(
        self, api_client, environment, keys
    ):
        hidden = ensure_held_back(environment).scenario_keys[0]

        held = self._run(api_client, keys.coder, environment, [hidden], "held")
        unknown = self._run(api_client, keys.coder, environment, ["no-such"], "none")

        assert held.status_code == unknown.status_code == 400, held.content
        assert held.json()["error"] == unknown.json()["error"]
        assert held.json()["error"] == "scenario_selection_unknown"
        assert held.json()["message"].replace(hidden, "KEY") == unknown.json()[
            "message"
        ].replace("no-such", "KEY")

    def test_a_mixed_selection_names_only_what_it_sent(
        self, api_client, environment, keys
    ):
        chosen = ensure_held_back(environment)

        response = self._run(
            api_client, keys.coder, environment, _suite_keys(environment), "coder"
        )

        assert response.status_code == 400, response.content
        assert response.json()["error"] == "scenario_selection_unknown"
        shown = set(_suite_keys(environment)) - set(chosen.scenario_keys)
        assert not any(key in response.json()["message"] for key in shown)

    def test_the_first_run_chooses_the_set_before_it_checks(
        self, api_client, environment, keys
    ):
        assert held_back(environment) is None

        response = self._run(
            api_client, keys.coder, environment, _suite_keys(environment), "coder"
        )

        assert response.status_code == 400, response.content
        environment.refresh_from_db()
        assert held_back(environment) is not None

    def test_the_rest_runs_and_carries_the_key(self, api_client, environment, keys):
        chosen = ensure_held_back(environment)
        shown = [
            key for key in _suite_keys(environment) if key not in chosen.scenario_keys
        ]

        response = self._run(api_client, keys.coder, environment, shown, "coder")

        assert response.status_code == 202, response.content
        run = HostedHarnessJob.no_workspace_objects.get(id=response.json()["job_id"])
        assert run.payload["metadata"]["coding_agent_key_id"] == str(keys.coder.id)
        assert run.verify_of_id is None

    def test_its_request_key_never_returns_a_person_s_or_another_key_s_run(
        self, api_client, user, workspace, environment, keys
    ):
        chosen = ensure_held_back(environment)
        shown = [
            key for key in _suite_keys(environment) if key not in chosen.scenario_keys
        ]
        person = self._run(api_client, keys.user, environment, shown, "shared")
        # The person's run could not be scheduled, so a replay of it would re-queue it.
        HostedHarnessJob.no_workspace_objects.filter(id=person.json()["job_id"]).update(
            state=HostedHarnessJob.State.FAILED,
            failure={"code": "scheduler_unavailable"},
        )
        other = _api_key(user, workspace, "coding_agent", environment)

        coder = self._run(api_client, keys.coder, environment, shown, "shared")
        again = self._run(api_client, keys.coder, environment, shown, "shared")
        second = self._run(api_client, other, environment, shown, "shared")

        assert coder.status_code == second.status_code == 202, coder.content
        ids = {person.json()["job_id"], coder.json()["job_id"], second.json()["job_id"]}
        assert len(ids) == 3
        assert again.json()["job_id"] == coder.json()["job_id"]
        untouched = HostedHarnessJob.no_workspace_objects.get(
            id=person.json()["job_id"]
        )
        assert untouched.state == HostedHarnessJob.State.FAILED

    def test_a_person_still_runs_every_case(self, api_client, environment, keys):
        ensure_held_back(environment)

        response = self._run(
            api_client, keys.user, environment, _suite_keys(environment), "person"
        )

        assert response.status_code == 202, response.content
        run = HostedHarnessJob.no_workspace_objects.get(id=response.json()["job_id"])
        assert "coding_agent_key_id" not in run.payload["metadata"]

    def test_the_service_refuses_for_a_coding_agent_only(self, environment):
        chosen = ensure_held_back(environment)

        with pytest.raises(HostedHarnessError) as refused:
            create_selected_harness_run(
                environment,
                scenario_keys=list(chosen.scenario_keys),
                trials=1,
                idempotency_key="coder",
                coding_agent_key_id=str(uuid4()),
            )
        allowed, created = create_selected_harness_run(
            environment,
            scenario_keys=list(chosen.scenario_keys),
            trials=1,
            idempotency_key="fix-or-chat",
        )

        assert refused.value.code == "scenario_selection_unknown"
        assert created and allowed.verify_of_id is None

    def test_the_service_refuses_a_coding_agent_without_a_held_back_set(
        self, environment
    ):
        assert held_back(environment) is None

        with pytest.raises(HostedHarnessError) as refused:
            create_selected_harness_run(
                environment,
                scenario_keys=_suite_keys(environment),
                trials=1,
                idempotency_key="coder",
                coding_agent_key_id=str(uuid4()),
            )

        assert refused.value.code == "coding_agent_needs_suite_version"
        assert refused.value.status_code == 409


class TestWithoutASuiteVersion:
    """Nothing can be held back without a suite version, so a coding agent is refused
    rather than shown every case."""

    def test_require_held_back_refuses(self, user, workspace, storage, settings):
        from simulate.services.harness_verify import require_held_back
        from simulate.tests.test_harness_versioning_backfill import _legacy

        settings.HARNESS_VERSIONING_ENABLED = False
        legacy = _legacy(user, workspace, "no-suite", storage)
        assert legacy.suite_version_id is None

        with pytest.raises(HostedHarnessError) as refused:
            require_held_back(legacy)

        assert refused.value.code == "coding_agent_needs_suite_version"
        assert refused.value.status_code == 409

    def test_the_reads_refuse_rather_than_hide_nothing(self, environment, keys):
        from simulate.views.harness_environment import _held_back_from

        coder = SimpleNamespace(org_api_key=keys.coder)
        person = SimpleNamespace(org_api_key=keys.user)
        with patch(
            "simulate.services.harness_verify.ensure_held_back", return_value=None
        ):
            with pytest.raises(HostedHarnessError) as refused:
                _held_back_from(coder, environment)
            assert _held_back_from(person, environment) == ()

        assert refused.value.code == "coding_agent_needs_suite_version"


class TestWhatACodingAgentReads:
    def test_the_environment_leaves_out_the_held_back_cases(
        self, api_client, environment, keys
    ):
        path = f"{BASE}/{environment.id}/"

        coder = _as(api_client, keys.coder, "get", path)
        person = _as(api_client, keys.user, "get", path)

        assert coder.status_code == person.status_code == 200, coder.content
        environment.refresh_from_db()
        chosen = held_back(environment)
        hidden = chosen.scenario_keys[0]
        every = _suite_keys(environment)
        assert {row["scenario_key"] for row in person.json()["scenarios"]} == set(every)
        assert {row["scenario_key"] for row in coder.json()["scenarios"]} == set(
            every
        ) - {hidden}
        assert hidden not in coder.content.decode()
        assert (
            coder.json()["overview"]["scenario_count"]
            == person.json()["overview"]["scenario_count"] - 1
        )

    def test_a_suite_version_leaves_out_the_held_back_cases(
        self, api_client, environment, keys
    ):
        path = (
            f"{BASE}/{environment.id}/versions/suite/"
            f"{environment.suite_version_id}/members/"
        )

        coder = _as(api_client, keys.coder, "get", path)
        person = _as(api_client, keys.user, "get", path)

        assert coder.status_code == person.status_code == 200, coder.content
        environment.refresh_from_db()
        hidden = held_back(environment).scenario_keys[0]
        every = _suite_keys(environment)
        assert [row["scenario_key"] for row in person.json()] == every
        assert [row["scenario_key"] for row in coder.json()] == [
            key for key in every if key != hidden
        ]
        assert hidden not in coder.content.decode()

    def test_its_own_run_reads_without_the_held_back_cases(
        self, api_client, environment, keys
    ):
        chosen = ensure_held_back(environment)
        hidden = chosen.scenario_keys[0]
        member = HarnessSuiteMember.no_workspace_objects.get(
            suite_version_id=chosen.suite_version_id, scenario_key=hidden
        )
        shown = [
            key for key in _suite_keys(environment) if key not in chosen.scenario_keys
        ]
        created = _as(
            api_client,
            keys.coder,
            "post",
            f"{BASE}/{environment.id}/run/",
            {"scenario_ids": shown, "trials": 1},
            HTTP_IDEMPOTENCY_KEY="coder",
        )
        assert created.status_code == 202, created.content
        path = f"/simulate/api/harness-jobs/{created.json()['job_id']}/"

        coder = _as(api_client, keys.coder, "get", path)
        person = _as(api_client, keys.user, "get", path)

        assert coder.status_code == person.status_code == 200, coder.content
        # The control: the run's stage outputs name every case of its suite.
        assert hidden in json.dumps(person.json()["stage_outputs"])
        body = coder.json()
        assert set(body) == {"job", "status", "receipts", "consumption", "platform"}
        assert set(body["job"]) == {
            "job_id",
            "run_id",
            "run_test_id",
            "test_execution_id",
            "metadata",
        }
        assert set(body["job"]["metadata"]) <= {
            "environment_job_id",
            "selected_scenario_keys",
            "trials",
            "rerun_execution_keys",
            "version_pins",
        }
        assert body["job"]["metadata"]["selected_scenario_keys"] == shown
        assert body["job"]["test_execution_id"] == created.json()["test_execution_id"]
        assert body["status"] == person.json()["status"]
        text = json.dumps(body)
        for secret in (hidden, member.content["instruction"], member.content["name"]):
            assert secret not in text

    def test_a_failed_run_reads_without_the_guest_output(
        self, api_client, environment, keys
    ):
        chosen = ensure_held_back(environment)
        hidden = chosen.scenario_keys[0]
        shown = [
            key for key in _suite_keys(environment) if key not in chosen.scenario_keys
        ]
        created = _as(
            api_client,
            keys.coder,
            "post",
            f"{BASE}/{environment.id}/run/",
            {"scenario_ids": shown, "trials": 1},
            HTTP_IDEMPOTENCY_KEY="coder-failed",
        )
        assert created.status_code == 202, created.content
        # A cache-miss certify runs every case of the suite, and the guest's output
        # names them: in the details and in the cause the message ends with.
        failure = {
            "domain": "environment",
            "stage": "validating_environment",
            "code": "authoring_runtime_validation_failed",
            "message": f"{RUNTIME_VALIDATION_FAILED}: RuntimeError: {hidden} said hi",
            "details": {
                "guest_log_tail": f"chat timed out in {hidden}: caller said hello",
                "process_logs": f"{hidden} transcript",
            },
        }
        run = HostedHarnessJob.no_workspace_objects.get(id=created.json()["job_id"])
        run.state = HostedHarnessJob.State.FAILED
        run.failure = failure
        run.save(update_fields=["state", "failure", "updated_at"])
        project_terminal_job_onto_run(run, timezone.now())
        execution = created.json()["test_execution_id"]
        call = CallExecution.objects.filter(test_execution_id=execution).first()
        job_path = f"/simulate/api/harness-jobs/{run.id}/"
        reads = [
            job_path,
            f"/simulate/test-executions/{execution}/",
            f"/simulate/v3/test-executions/{execution}/calls/",
            f"/simulate/v3/call-executions/{call.id}/",
        ]

        coder = {path: _as(api_client, keys.coder, "get", path) for path in reads}
        person = _as(api_client, keys.user, "get", job_path)

        for path, response in coder.items():
            assert response.status_code == 200, (path, response.content)
            assert hidden not in json.dumps(response.json()), path
        assert coder[job_path].json()["status"]["failure"] == {
            "domain": "environment",
            "stage": "validating_environment",
            "code": "authoring_runtime_validation_failed",
            "message": RUNTIME_VALIDATION_FAILED,
        }
        assert person.json()["status"]["failure"] == failure
        assert (
            coder[job_path].json()["status"]["state"] == HostedHarnessJob.State.FAILED
        )

    def test_its_result_rows_name_each_failure_s_domain_and_code(
        self, api_client, environment, keys
    ):
        chosen = ensure_held_back(environment)
        shown = [
            key for key in _suite_keys(environment) if key not in chosen.scenario_keys
        ]
        created = _as(
            api_client,
            keys.coder,
            "post",
            f"{BASE}/{environment.id}/run/",
            {"scenario_ids": shown, "trials": 2},
            HTTP_IDEMPOTENCY_KEY="coder-errored",
        )
        assert created.status_code == 202, created.content
        execution = created.json()["test_execution_id"]
        calls = list(CallExecution.objects.filter(test_execution_id=execution))
        for call, code in zip(
            calls, ("evidence_missing", "not a code: it said hi"), strict=True
        ):
            call.call_metadata = {
                **(call.call_metadata or {}),
                "hosted_harness_receipt": {
                    "status": "error",
                    "failure": {
                        "domain": "simulator",
                        "stage": "grading",
                        "code": code,
                        "message": "free text",
                    },
                },
            }
            call.save(update_fields=["call_metadata"])

        response = _as(
            api_client,
            keys.coder,
            "get",
            f"/simulate/v3/test-executions/{execution}/calls/",
        )

        assert response.status_code == 200, response.content
        rows = {row["id"]: row for row in response.json()["results"]}
        assert rows[str(calls[0].id)]["failure_domain"] == "simulator"
        assert rows[str(calls[0].id)]["failure_code"] == "evidence_missing"
        # A code that is not a code is free text; it is left out.
        assert rows[str(calls[1].id)]["failure_domain"] == "simulator"
        assert rows[str(calls[1].id)]["failure_code"] is None

    def test_a_person_s_read_chooses_nothing(self, api_client, environment, keys):
        _as(api_client, keys.user, "get", f"{BASE}/{environment.id}/")

        environment.refresh_from_db()
        assert held_back(environment) is None
