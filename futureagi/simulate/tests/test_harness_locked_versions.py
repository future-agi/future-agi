"""Locking an environment's versions, with train and test splits."""

import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.db.models import RestrictedError
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessLockedVersion,
    HarnessNudge,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HarnessTaskSplit,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.services.harness_tool_reach import blocked_tools
from simulate.services.harness_versioning.backfill import backfill_environment
from simulate.services.harness_versioning.locked_versions import (
    Task,
    lock_version,
    plan_splits,
    target_test_size,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    grades_nothing,
    sub_goal_catalogue,
)
from simulate.tests.test_harness_versioning_backfill import _legacy, _RealisticStorage
from tfc.constants.levels import Level

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


def _environment(user, workspace, storage, key="rel"):
    environment = _legacy(user, workspace, key, storage)
    backfill_environment(environment.id)
    environment.refresh_from_db()
    return environment


# The one sub-goal the environment's authored catalogue grades (``_authoring``).
GOAL = "refunded"


def _scenarios(count, *, prefix="s", use_case="refunds", scorable=True, goal=GOAL):
    sub_goals = [{"name": goal}] if scorable else []
    return [(f"{prefix}{index}", use_case, sub_goals) for index in range(count)]


def _catalogue(environment):
    return {
        goal["name"]
        for goal in HarnessEnvironmentVersion.no_workspace_objects.get(
            id=environment.environment_version_id
        ).documents["sub_goals"]
    }


def _suite(environment, scenarios):
    """A new active suite version of ``scenarios`` ((key, use case, sub-goals)), made
    the environment's default."""
    number = (
        HarnessSuiteVersion.no_workspace_objects.filter(job=environment).count() + 1
    )
    suite = HarnessSuiteVersion.no_workspace_objects.create(
        job=environment,
        organization=environment.organization,
        workspace=environment.workspace,
        version_number=number,
        status="active",
        origin="manual",
        hash=f"sha256:{number:064x}",
        archive_object_key=f"suite-{number}",
        archive_sha256="0" * 64,
        archive_size=1,
        parent=environment.suite_version,
        scenario_count=len(scenarios),
    )
    for index, (key, use_case, sub_goals) in enumerate(scenarios, start=1):
        row, _ = HostedHarnessScenario.all_objects.update_or_create(
            job=environment,
            scenario_key=key,
            defaults={"deleted": False, "number": index, "use_case": use_case},
        )
        HarnessSuiteMember.no_workspace_objects.create(
            suite_version=suite,
            scenario=row,
            scenario_key=key,
            number=index,
            content={
                "name": key,
                "use_case": use_case,
                "sub_goals": sub_goals,
            },
        )
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        suite_version=suite
    )
    environment.refresh_from_db()
    return suite


def _lock_body(environment):
    return {
        "environment_version_id": str(environment.environment_version_id),
        "suite_version_id": str(environment.suite_version_id),
    }


def _lock(auth_client, environment):
    return auth_client.post(
        f"{BASE}/{environment.id}/versions/lock/",
        _lock_body(environment),
        format="json",
    )


def _preview(auth_client, environment):
    """Whether the default pair can be locked now, as the card reads it."""
    response = auth_client.get(f"{BASE}/{environment.id}/versions/lock-preview/")
    assert response.status_code == 200, response.content
    return response.json()


def _splits(environment):
    return dict(
        HarnessTaskSplit.no_workspace_objects.filter(
            scenario__job=environment
        ).values_list("scenario__scenario_key", "split")
    )


def _keyed(environment, members):
    """A roster keyed by scenario key rather than scenario id."""
    keys = dict(
        HostedHarnessScenario.all_objects.filter(job=environment).values_list(
            "id", "scenario_key"
        )
    )
    return {keys[scenario_id]: split for scenario_id, split in members.items()}


def _as_member(user):
    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        level=Level.MEMBER, role="Member"
    )
    WorkspaceMembership.no_workspace_objects.filter(user=user).update(
        level=Level.WORKSPACE_MEMBER, role="Workspace Member"
    )
    user.organization_role = "Member"
    user.save(update_fields=["organization_role"])


def _as_workspace_admin(user):
    """An organization member who administers the environment's workspace."""
    _as_member(user)
    WorkspaceMembership.no_workspace_objects.filter(user=user).update(
        level=Level.WORKSPACE_ADMIN, role="Workspace Admin"
    )


def _tasks(sizes):
    return [
        Task(f"{name}-{index}", f"{name}-{index}", name)
        for name, size in sizes.items()
        for index in range(size)
    ]


def _tests_by_category(assign, names):
    return {
        name: sum(
            1
            for key, split in assign.items()
            if key.startswith(f"{name}-") and split == "test"
        )
        for name in names
    }


class TestTestTarget:
    @pytest.mark.parametrize(
        ("scorable", "expected"),
        [
            (10, 3),
            (11, 4),
            (20, 6),
            (100, 30),
            (200, 60),
            (250, 60),
            (300, 60),
            (500, 100),
        ],
    )
    def test_thirty_percent_small_twenty_percent_large(self, scorable, expected):
        assert target_test_size(scorable) == expected


class TestPlanSplits:
    def test_named_categories_take_seats_before_the_pool(self):
        # Five categories of two and six single-task ones: N = 16, target 5.
        sizes = dict.fromkeys("abcde", 2) | {f"solo{i}": 1 for i in range(6)}

        plan = plan_splits(_tasks(sizes), {}, "env")

        assert plan.test_target == 5
        assert _tests_by_category(plan.assign, "abcde") == dict.fromkeys("abcde", 1)
        assert not any(
            split == "test"
            for key, split in plan.assign.items()
            if key.startswith("solo")
        )

    def test_first_lock_follows_category_sizes(self):
        plan = plan_splits(_tasks({"a": 20, "b": 15, "c": 4, "d": 1}), {}, "env")

        tests = {
            name: sum(
                1
                for key, split in plan.assign.items()
                if key.startswith(f"{name}-") and split == "test"
            )
            for name in "abcd"
        }
        assert plan.test_target == 12
        assert (plan.train, plan.test) == (28, 12)
        assert tests == {"a": 6, "b": 5, "c": 1, "d": 0}

    def test_a_lone_single_task_category_stays_in_train(self):
        # One single-task category alone forms a group of one, which keeps its train task.
        plan = plan_splits(_tasks({"a": 14, "lonely": 1}), {}, "env")

        assert plan.assign["lonely-0"] == "train"
        assert plan.test == target_test_size(15)

    def test_single_task_categories_split_as_one_group(self):
        # Three single-task categories pool into one group of three: it is covered like
        # any category of two or more, and keeps at least one train task.
        sizes = {"a": 14, "s1": 1, "s2": 1, "s3": 1}

        plan = plan_splits(_tasks(sizes), {}, "env")

        pooled = [plan.assign[f"s{i}-0"] for i in (1, 2, 3)]
        assert pooled.count("test") == 1
        assert plan.test == target_test_size(17)

    def test_every_category_keeps_a_train_task(self):
        plan = plan_splits(_tasks({"a": 2, "b": 2, "c": 2, "d": 2, "e": 2}), {}, "env")

        for name in "abcde":
            assert "train" in {
                plan.assign[f"{name}-0"],
                plan.assign[f"{name}-1"],
            }

    def test_the_same_tasks_always_get_the_same_splits(self):
        tasks = _tasks({"a": 30, "b": 10})

        assert (
            plan_splits(tasks, {}, "env").assign
            == plan_splits(list(reversed(tasks)), {}, "env").assign
        )

    def test_growth_keeps_old_splits_and_only_new_tasks_fill_seats(self):
        first = plan_splits(_tasks({"a": 40}), {}, "env")
        grown = _tasks({"a": 60})

        second = plan_splits(grown, dict(first.assign), "env")

        assert set(second.assign) == {f"a-{index}" for index in range(40, 60)}
        assert second.test == target_test_size(60)
        assert sum(1 for split in second.assign.values() if split == "test") == (
            target_test_size(60) - first.test
        )

    def test_a_first_lock_puts_every_category_in_test(self):
        plan = plan_splits(_tasks({"a": 14, "b": 2, "c": 2, "d": 2}), {}, "env")

        assert (plan.train, plan.test) == (14, 6)
        assert _tests_by_category(plan.assign, "abcd") == {
            "a": 3,
            "b": 1,
            "c": 1,
            "d": 1,
        }

    def test_growth_never_takes_the_suite_past_its_target(self):
        first = plan_splits(_tasks({"a": 100, "b": 100}), {}, "env")
        assert first.test == 60

        second = plan_splits(_tasks({"a": 100, "b": 200}), dict(first.assign), "env")

        # Retained test tasks already fill the target of 300: the new ones train.
        assert (second.train, second.test, second.test_target) == (240, 60, 60)
        assert set(second.assign.values()) == {"train"}

    def test_a_new_category_on_a_full_split_takes_one_test_task(self):
        # Option C: a category with no test task takes one seat from its new tasks even
        # when the target is met, while the split stays within 30% of N. Nothing moves.
        first = plan_splits(_tasks({"a": 100, "b": 100}), {}, "env")

        second = plan_splits(
            _tasks({"a": 100, "b": 100, "c": 50}), dict(first.assign), "env"
        )

        assert second.test_target == 60
        assert second.test == 61
        assert _tests_by_category(second.assign, "c") == {"c": 1}
        assert all(
            second.assign.get(key, split) == split
            for key, split in first.assign.items()
        )

    def test_each_new_category_on_a_full_split_takes_one(self):
        first = plan_splits(_tasks({"a": 100, "b": 100}), {}, "env")

        second = plan_splits(
            _tasks({"a": 100, "b": 100, "c": 20, "d": 20, "e": 20}),
            dict(first.assign),
            "env",
        )

        assert second.test == 63
        assert _tests_by_category(second.assign, "cde") == dict.fromkeys("cde", 1)

    def test_a_growing_target_absorbs_the_coverage_seats(self):
        first = plan_splits(_tasks({"a": 100, "b": 100}), {}, "env")
        covered = plan_splits(
            _tasks({"a": 100, "b": 100, "c": 50}), dict(first.assign), "env"
        )
        assigned = {**first.assign, **covered.assign}

        # N = 400: target 80. The 61 retained tests count towards it.
        third = plan_splits(
            _tasks({"a": 100, "b": 100, "c": 50, "d": 150}), assigned, "env"
        )

        assert third.test == third.test_target == 80

    def test_the_30_percent_cap_binds_coverage(self):
        # 40 tasks in 15 categories: the cap is ceil(0.3 × 40) = 12, so 12 are covered.
        sizes = {"big": 12} | {f"c{i}": 2 for i in range(14)}

        plan = plan_splits(_tasks(sizes), {}, "env")

        assert plan.test == 12
        covered = [
            name for name in sizes if _tests_by_category(plan.assign, [name])[name]
        ]
        assert len(covered) == 12

    def test_coverage_never_takes_a_category_s_only_train_task(self):
        # Four pairs: 8 tasks, cap ceil(0.3 × 8) = 3. Each pair keeps a train task.
        plan = plan_splits(_tasks({f"p{i}": 2 for i in range(4)}), {}, "env")

        assert plan.test == 3
        for i in range(4):
            assert "train" in {plan.assign[f"p{i}-0"], plan.assign[f"p{i}-1"]}

    def test_an_all_assigned_train_category_stays_uncovered(self):
        first = plan_splits(_tasks({"a": 100, "b": 100}), {}, "env")
        trained = {f"b-{i}": "train" for i in range(100)}
        assigned = {**first.assign, **trained}

        # b has no new tasks: nothing can move, so b stays without a test task.
        again = plan_splits(_tasks({"a": 100, "b": 100}), assigned, "env")

        assert again.assign == {}

    def test_growth_fills_the_target_from_the_new_tasks(self):
        first = plan_splits(_tasks({"a": 40}), {}, "env")

        second = plan_splits(_tasks({"a": 40, "b": 60}), dict(first.assign), "env")

        assert second.test == target_test_size(100) == 30
        assert _tests_by_category(second.assign, "ab") == {"a": 0, "b": 18}

    def test_a_test_task_never_moves_to_train(self):
        first = plan_splits(_tasks({"a": 40}), {}, "env")
        tested = {key for key, split in first.assign.items() if split == "test"}

        # Every train task is removed; the test tasks stay in test.
        kept = [task for task in _tasks({"a": 40}) if task.key in tested]
        second = plan_splits(kept, dict(first.assign), "env")

        assert second.assign == {}
        assert (second.train, second.test) == (0, len(tested))


class TestLock:
    def test_locks_the_default_pair_with_its_splits(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(
            environment,
            _scenarios(30) + _scenarios(12, prefix="b", use_case="billing"),
        )

        response = _lock(auth_client, environment)

        assert response.status_code == 200, response.json()
        [locked_version] = response.json()["locked_versions"]
        now = timezone.now()
        assert locked_version["label"] == f"{now.year}.{now.month}.1"
        assert (locked_version["train"], locked_version["test"]) == (29, 13)
        assert locked_version["environment_version_id"] == str(
            environment.environment_version_id
        )
        assert locked_version["suite_version_id"] == str(environment.suite_version_id)
        assert locked_version["locked_by"]["id"] == str(user.id)
        assert locked_version["unlocked_at"] is None
        assert locked_version["suite_label"] == "v2"
        assert "lock" not in response.json()
        assert _preview(auth_client, environment)["refusal"]["code"] == (
            "version_already_locked"
        )
        assert len(_splits(environment)) == 42

    def test_the_preview_reads_the_splits_before_a_lock(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))

        body = auth_client.get(f"{BASE}/{environment.id}/versions/").json()
        lock = _preview(auth_client, environment)

        # The versions read carries the history only; the preview has its own read.
        assert body["locked_versions"] == []
        assert "lock" not in body
        assert lock == {
            "environment_version_id": str(environment.environment_version_id),
            "suite_version_id": str(environment.suite_version_id),
            "proved": False,
            "can_lock": True,
            "refusal": None,
            "train": 14,
            "test": 6,
            "test_target": 6,
        }

    def test_unscorable_scenarios_get_no_split(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(
            environment,
            _scenarios(20) + _scenarios(5, prefix="x", scorable=False),
        )

        response = _lock(auth_client, environment)

        assert response.status_code == 200, response.json()
        splits = _splits(environment)
        assert set(splits) == {f"s{index}" for index in range(20)}
        assert sorted(splits.values()).count("test") == 6

    def test_a_goal_the_pinned_environment_does_not_grade_is_unscorable(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        assert _catalogue(environment) == {GOAL}
        # "resolved" is not in the catalogue: a run of this pair refuses these tasks.
        _suite(
            environment,
            _scenarios(20)
            + _scenarios(12, prefix="r", use_case="billing", goal="resolved"),
        )

        response = _lock(auth_client, environment)

        assert response.status_code == 200, response.json()
        [locked_version] = response.json()["locked_versions"]
        assert locked_version["train"] + locked_version["test"] == 20
        assert not any(key.startswith("r") for key in _splits(environment))

    def test_a_suite_of_goals_the_environment_does_not_grade_cannot_lock(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(12, goal="resolved"))

        lock = _preview(auth_client, environment)
        response = _lock(auth_client, environment)

        assert lock["can_lock"] is False
        assert lock["refusal"]["code"] == "lock_too_few_test"
        assert (lock["train"], lock["test"]) == (0, 0)
        assert response.status_code == 422
        assert not HarnessLockedVersion.no_workspace_objects.exists()

    def test_an_environment_without_a_catalogue_runs_every_task(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        # A version recorded before sub-goals were catalogued: its runs refuse nothing.
        version = environment.environment_version
        version.documents = {**version.documents, "sub_goals": []}
        version.save(update_fields=["documents"])
        _suite(
            environment,
            _scenarios(10, goal="resolved")
            + _scenarios(5, prefix="e", scorable=False)
            + [(f"l{index}", "refunds", None) for index in range(5)],
        )

        response = _lock(auth_client, environment)

        assert response.status_code == 200, response.json()
        assert len(_splits(environment)) == 20

    def test_a_legacy_suite_without_sub_goals_locks(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        version = environment.environment_version
        version.documents = {**version.documents, "sub_goals": []}
        version.save(update_fields=["documents"])
        # Members from before sub-goals: the run plays them, so the locked version counts them.
        _suite(environment, [(f"l{index}", "refunds", None) for index in range(20)])

        response = _lock(auth_client, environment)

        assert response.status_code == 200, response.json()
        [locked_version] = response.json()["locked_versions"]
        assert (locked_version["train"], locked_version["test"]) == (14, 6)

    @pytest.mark.parametrize("malformed", ["refunded", 5, {"name": "refunded"}, None])
    def test_sub_goals_that_are_not_a_list_are_left_to_the_run(
        self, auth_client, user, workspace, storage, malformed
    ):
        environment = _environment(user, workspace, storage)
        _suite(
            environment,
            _scenarios(20) + [("bad", "refunds", malformed)],
        )

        lock = _preview(auth_client, environment)

        # The run refuses only a list naming no graded goal, so this member counts:
        # N = 21, target min(7, 60) = 7.
        assert (lock["train"], lock["test"]) == (14, 7)

    def test_the_target_counts_scorable_tasks(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(
            environment,
            _scenarios(80) + _scenarios(20, prefix="x", scorable=False),
        )

        lock = _preview(auth_client, environment)

        # N is the 80 scorable tasks, not the 100 members: min(24, max(60, 16)).
        assert (lock["train"], lock["test"], lock["test_target"]) == (56, 24, 24)

    def test_four_pairs_stay_within_the_cap_and_cannot_lock(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        # 8 tasks in 4 categories: the cap is ceil(0.3 × 8) = 3, below the 4 a lock needs.
        _suite(
            environment,
            [
                (f"p{pair}-{index}", f"pair{pair}", [{"name": GOAL}])
                for pair in range(4)
                for index in range(2)
            ],
        )

        response = _lock(auth_client, environment)

        assert response.status_code == 422
        assert response.json()["error"] == "lock_too_few_test"
        assert not HarnessLockedVersion.no_workspace_objects.exists()

    def test_too_few_test_tasks_is_refused(self, auth_client, user, workspace, storage):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(10))

        response = _lock(auth_client, environment)

        assert response.status_code == 422
        assert response.json()["error"] == "lock_too_few_test"
        assert response.json()["message"] == (
            "Add scenarios to lock: a test split needs at least 4."
        )
        assert not HarnessLockedVersion.no_workspace_objects.exists()
        assert _splits(environment) == {}

    def test_a_shrunken_suite_below_four_test_tasks_is_refused(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        assert _lock(auth_client, environment).status_code == 200
        tested = [key for key, split in _splits(environment).items() if split == "test"]
        # The next version drops all but three of the test tasks.
        kept = [
            scenario for scenario in _scenarios(20) if scenario[0] not in tested[3:]
        ]
        _suite(environment, kept)

        response = _lock(auth_client, environment)

        assert response.status_code == 422
        assert response.json()["error"] == "lock_too_few_test"

    def test_locking_twice_is_refused(self, auth_client, user, workspace, storage):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        assert _lock(auth_client, environment).status_code == 200

        response = _lock(auth_client, environment)

        assert response.status_code == 409
        assert response.json()["error"] == "version_already_locked"
        label = HarnessLockedVersion.no_workspace_objects.get().label
        # The pair as the card names it: environment vN, scenarios vN.
        assert response.json()["message"] == (
            f"Lock {label} already covers environment v1, scenarios v2."
        )
        assert HarnessLockedVersion.no_workspace_objects.count() == 1

    def test_a_default_that_moved_since_the_page_loaded_is_refused(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        seen = _lock_body(environment)
        _suite(environment, _scenarios(20))

        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/lock/", seen, format="json"
        )

        assert response.status_code == 409
        assert response.json()["error"] == "version_conflict"

    def test_a_building_environment_is_refused(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            state=HostedHarnessJob.State.RUNNING
        )

        response = _lock(auth_client, environment)

        assert response.status_code == 409
        assert response.json()["error"] == "lock_not_ready"

    def test_the_preview_refuses_while_a_rebuild_runs(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        HarnessNudge.no_workspace_objects.create(
            job=environment,
            organization_id=environment.organization_id,
            target="environment",
            state="running",
        )

        lock = _preview(auth_client, environment)
        response = _lock(auth_client, environment)

        # The preview offers no lock the write would refuse.
        assert (lock["can_lock"], lock["refusal"]["code"]) == (False, "rebuild_running")
        assert response.status_code == 409
        assert response.json()["error"] == "rebuild_running"

    def test_a_second_lock_after_growth_keeps_old_splits(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        _lock(auth_client, environment)
        before = _splits(environment)
        _suite(environment, _scenarios(40))

        response = _lock(auth_client, environment)

        assert response.status_code == 200
        after = _splits(environment)
        assert {key: after[key] for key in before} == before
        newer, older = response.json()["locked_versions"]
        assert (older["train"], older["test"]) == (14, 6)
        assert newer["test"] == target_test_size(40)
        assert newer["label"].endswith(".2")


def _needs_production(environment, keys):
    """The default environment version's ``issue_refund`` reaches a payments API
    someone else runs, and the default suite's scenarios ``keys`` call it."""
    version = HarnessEnvironmentVersion._base_manager.get(
        id=environment.environment_version_id
    )
    HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
        documents={
            **(version.documents or {}),
            "contract": {
                "dependencies": [
                    {
                        "name": "payments",
                        "kind": "service",
                        "engine": "http",
                        "reached": {"dsn_env": "PAYMENTS_API_URL"},
                        "used_by": ["issue_refund"],
                    }
                ],
            },
        }
    )
    members = HarnessSuiteMember.no_workspace_objects.filter(
        suite_version_id=environment.suite_version_id
    ).values_list("scenario_key", flat=True)
    HarnessSuiteVersion._base_manager.filter(id=environment.suite_version_id).update(
        documents={
            "scenarios": [
                {
                    "name": key,
                    "solution": [
                        {"tool": "issue_refund" if key in keys else "lookup_order"}
                    ],
                }
                for key in members
            ]
        }
    )
    environment.refresh_from_db()


class TestProductionOnlyTasks:
    """A task that needs a production-only tool of the pinned environment version
    can't run, so it gets no split and doesn't count in N. The rule reads only while
    actors are on."""

    def test_a_task_that_needs_a_production_only_tool_gets_no_split(
        self, auth_client, user, workspace, storage, settings
    ):
        settings.HARNESS_ACTORS_ENABLED = True
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        blocked = {f"s{index}" for index in range(5)}
        _needs_production(environment, blocked)
        assert [entry["tool"] for entry in blocked_tools(environment)] == [
            "issue_refund"
        ]

        lock = _preview(auth_client, environment)
        response = _lock(auth_client, environment)

        assert (lock["train"], lock["test"]) == (15 - target_test_size(15), 5)
        assert response.status_code == 200, response.content
        [locked_version] = response.json()["locked_versions"]
        # The preview and the lock read one rule, so they agree.
        assert (locked_version["train"], locked_version["test"]) == (
            lock["train"],
            lock["test"],
        )
        assert set(_splits(environment)) == {f"s{index}" for index in range(20)} - (
            blocked
        )

    def test_a_backfilled_key_still_meets_its_production_only_tool(
        self, auth_client, user, workspace, storage, settings
    ):
        settings.HARNESS_ACTORS_ENABLED = True
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        blocked = {f"s{index}" for index in range(5)}
        _needs_production(environment, blocked)
        # Their rows were backfilled under other keys: the suite documents still key
        # them by name, as the backfill paired them.
        for index in range(5):
            HostedHarnessScenario.all_objects.filter(
                job=environment, scenario_key=f"s{index}"
            ).update(scenario_key=f"legacy-{index}")
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=environment.suite_version_id, scenario_key=f"s{index}"
            ).update(scenario_key=f"legacy-{index}")

        lock = _preview(auth_client, environment)
        response = _lock(auth_client, environment)

        assert (lock["train"], lock["test"]) == (15 - target_test_size(15), 5)
        assert response.status_code == 200, response.content
        assert set(_splits(environment)) == {f"s{index}" for index in range(5, 20)}

    def test_with_actors_off_every_task_splits_and_the_preview_stays_off_documents(
        self, auth_client, user, workspace, storage, settings
    ):
        settings.HARNESS_ACTORS_ENABLED = False
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        _needs_production(environment, {f"s{index}" for index in range(5)})

        with CaptureQueriesContext(connection) as read:
            lock = _preview(auth_client, environment)
        response = _lock(auth_client, environment)

        assert (lock["train"], lock["test"]) == (14, 6)
        assert response.status_code == 200, response.content
        assert set(_splits(environment)) == {f"s{index}" for index in range(20)}
        version_selects = [
            query["sql"]
            for query in read.captured_queries
            if "simulate_harness_environment_version" in query["sql"]
            or "simulate_harness_suite_version" in query["sql"]
        ]
        # The runnable check reads one key of ONE version: the pinned environment
        # version's sub-goal catalogue, looked up by id. No read lists documents
        # across versions.
        catalogue = "\"documents\" -> 'sub_goals'"
        catalogue_reads = [sql for sql in version_selects if catalogue in sql]
        assert len(catalogue_reads) == 1
        assert '"simulate_harness_environment_version"."id" =' in catalogue_reads[0]
        assert all(
            '"documents"' not in sql.replace(catalogue, "") for sql in version_selects
        )

    @pytest.mark.parametrize(
        ("run_refusal", "message"),
        [
            (
                "These versions can't be run together yet",
                "These versions can't be run together yet, so they can't be locked.",
            ),
            (
                "These versions haven't been prepared yet and building them is"
                " turned off",
                "These versions haven't been prepared yet and building them is"
                " turned off, so they can't be locked.",
            ),
        ],
    )
    def test_a_default_pair_the_run_refuses_cannot_lock(
        self,
        auth_client,
        user,
        workspace,
        storage,
        settings,
        monkeypatch,
        run_refusal,
        message,
    ):
        from simulate.services.harness_versioning import locked_versions

        settings.HARNESS_ACTORS_ENABLED = True
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))

        def refuse(*_args, **_kwargs):
            raise HostedHarnessError(
                "version_not_runnable", run_refusal, status_code=422
            )

        # The default run would refuse its pins, so it could play no task.
        monkeypatch.setattr(locked_versions, "resolve_run_pins", refuse)

        lock = _preview(auth_client, environment)
        response = _lock(auth_client, environment)

        assert (lock["can_lock"], lock["refusal"]) == (
            False,
            {"code": "lock_not_ready", "message": message},
        )
        assert response.status_code == 409
        assert (response.json()["error"], response.json()["message"]) == (
            "lock_not_ready",
            message,
        )
        assert not HarnessLockedVersion.no_workspace_objects.exists()
        assert _splits(environment) == {}

    def test_a_suite_whose_every_task_needs_one_is_refused(
        self, auth_client, user, workspace, storage, settings
    ):
        settings.HARNESS_ACTORS_ENABLED = True
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        _needs_production(environment, {f"s{index}" for index in range(20)})

        lock = _preview(auth_client, environment)
        response = _lock(auth_client, environment)

        # The preview offers no lock the write would refuse.
        assert (lock["can_lock"], lock["refusal"]["code"]) == (
            False,
            "lock_too_few_test",
        )
        assert (lock["train"], lock["test"]) == (0, 0)
        assert response.status_code == 422
        assert response.json()["error"] == "lock_too_few_test"
        assert (response.json()["train"], response.json()["test"]) == (0, 0)
        assert not HarnessLockedVersion.no_workspace_objects.exists()
        assert _splits(environment) == {}

    def test_the_preview_reads_a_flat_number_of_queries_as_locked_versions_grow(
        self, auth_client, user, workspace, storage, settings
    ):
        settings.HARNESS_ACTORS_ENABLED = True
        environment = _environment(user, workspace, storage)

        def unlocked_default():
            """A new default suite, not locked yet, whose ``s0`` needs production."""
            _suite(environment, _scenarios(20))
            _needs_production(environment, {"s0"})

        def measure():
            with CaptureQueriesContext(connection) as read:
                lock = _preview(auth_client, environment)
            # A refusal stops before the split plan, so the plan must run both times.
            assert lock["can_lock"] is True, lock
            assert (lock["train"], lock["test"]) == (19 - target_test_size(19), 6)
            runtime_selects = [
                # Whether a row has a blob may be asked; the blob itself never is.
                query["sql"]
                .replace('"credential_blob" IS NULL', "")
                .replace("\"credential_blob\" = ''", "")
                for query in read.captured_queries
                if "simulate_harness_agent_version_runtime" in query["sql"]
            ]
            # The production-only rule reads the default run's real pins.
            assert runtime_selects
            assert all('"credential_blob"' not in sql for sql in runtime_selects)
            return len(read.captured_queries)

        unlocked_default()
        assert _lock(auth_client, environment).status_code == 200
        unlocked_default()
        few = measure()
        for _ in range(4):
            assert _lock(auth_client, environment).status_code == 200
            unlocked_default()

        assert HarnessLockedVersion.no_workspace_objects.count() == 5
        assert measure() == few


class TestUnlockAndDelete:
    def test_delete_is_refused_while_a_version_is_locked(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [locked_version] = _lock(auth_client, environment).json()["locked_versions"]
        label = locked_version["label"]

        response = auth_client.delete(f"{BASE}/{environment.id}/")

        assert response.status_code == 409
        assert response.json()["error"] == "version_locked"
        assert response.json()["message"] == (
            f"Lock {label} covers this version. A workspace admin or owner must "
            "unlock it first."
        )
        # What an admin's Unlock action in the refusal needs, and the pair its
        # confirmation names.
        assert response.json()["locked_version_id"] == locked_version["id"]
        assert response.json()["label"] == label
        assert (
            response.json()["environment_label"],
            response.json()["suite_label"],
        ) == (locked_version["environment_label"], locked_version["suite_label"])
        assert response.json()["suite_label"] == "v2"
        environment.refresh_from_db()
        assert environment.deleted is False

    def test_unlock_works_with_versioning_off(
        self, auth_client, user, workspace, storage, settings
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [locked_version] = _lock(auth_client, environment).json()["locked_versions"]
        settings.HARNESS_VERSIONING_ENABLED = False

        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/unlock/",
            {"locked_version_id": locked_version["id"]},
            format="json",
        )

        assert response.status_code == 200, response.content
        assert "lock" not in response.json()
        assert HarnessLockedVersion.no_workspace_objects.get().unlocked_at is not None
        # The preview is a versions read: off, it answers as the others do.
        preview = auth_client.get(f"{BASE}/{environment.id}/versions/lock-preview/")
        assert preview.status_code == 404
        assert preview.json()["error"] == "versioning_disabled"

    def test_a_member_may_not_unlock(self, auth_client, user, workspace, storage):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [locked_version] = _lock(auth_client, environment).json()["locked_versions"]
        _as_member(user)

        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/unlock/",
            {"locked_version_id": locked_version["id"]},
            format="json",
        )

        assert response.status_code == 403
        assert response.json()["error"] == "unlock_admin_only"
        assert response.json()["message"] == (
            "Only a workspace admin or owner can unlock versions."
        )
        assert HarnessLockedVersion.no_workspace_objects.get().unlocked_at is None

    def test_a_workspace_admin_may_unlock(self, auth_client, user, workspace, storage):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [locked_version] = _lock(auth_client, environment).json()["locked_versions"]
        _as_workspace_admin(user)

        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/unlock/",
            {"locked_version_id": locked_version["id"]},
            format="json",
        )

        assert response.status_code == 200, response.json()
        assert HarnessLockedVersion.no_workspace_objects.get().unlocked_at is not None

    def test_an_earlier_locked_version_can_be_unlocked(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [locked_version] = _lock(auth_client, environment).json()["locked_versions"]
        # An edit moved the default on; the old locked version is still locked.
        _suite(environment, _scenarios(25))
        assert auth_client.delete(f"{BASE}/{environment.id}/").status_code == 409

        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/unlock/",
            {"locked_version_id": locked_version["id"]},
            format="json",
        )

        assert response.status_code == 200
        assert auth_client.delete(f"{BASE}/{environment.id}/").status_code == 204

    def test_an_admin_can_unlock_with_versioning_off(
        self, auth_client, user, workspace, storage, settings
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [locked_version] = _lock(auth_client, environment).json()["locked_versions"]
        settings.HARNESS_VERSIONING_ENABLED = False

        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/unlock/",
            {"locked_version_id": locked_version["id"]},
            format="json",
        )

        # New locks stop with the flag; the delete guard and its way out stay.
        assert _lock(auth_client, environment).status_code == 404
        assert response.status_code == 200, response.json()
        assert auth_client.delete(f"{BASE}/{environment.id}/").status_code == 204

    def test_a_member_may_lock(self, auth_client, user, workspace, storage):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        _as_member(user)

        response = _lock(auth_client, environment)

        assert response.status_code == 200, response.json()
        [listed] = response.json()["locked_versions"]
        locked_version = HarnessLockedVersion.no_workspace_objects.get(id=listed["id"])
        assert (
            locked_version.environment_version_id,
            locked_version.suite_version_id,
        ) == (
            environment.environment_version_id,
            environment.suite_version_id,
        )
        assert locked_version.locked_by == user
        assert locked_version.unlocked_at is None
        assert (locked_version.train_count, locked_version.test_count) == (14, 6)

    def test_unlock_keeps_the_locked_version_readable_then_delete_works(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [locked_version] = _lock(auth_client, environment).json()["locked_versions"]
        splits = _splits(environment)

        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/unlock/",
            {"locked_version_id": locked_version["id"]},
            format="json",
        )

        assert response.status_code == 200
        [unlocked] = response.json()["locked_versions"]
        assert unlocked["id"] == locked_version["id"]
        assert unlocked["unlocked_at"] is not None
        assert unlocked["unlocked_by"]["id"] == str(user.id)
        assert (unlocked["train"], unlocked["test"]) == (14, 6)
        assert _splits(environment) == splits
        assert auth_client.delete(f"{BASE}/{environment.id}/").status_code == 204

    def test_relocking_makes_a_new_locked_version_with_the_same_splits(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [first] = _lock(auth_client, environment).json()["locked_versions"]
        splits = _splits(environment)
        auth_client.post(
            f"{BASE}/{environment.id}/versions/unlock/",
            {"locked_version_id": first["id"]},
            format="json",
        )

        response = _lock(auth_client, environment)

        assert response.status_code == 200
        second, older = response.json()["locked_versions"]
        assert older["id"] == first["id"]
        assert second["label"] != first["label"]
        assert second["label"].endswith(".2")
        assert (second["train"], second["test"]) == (first["train"], first["test"])
        assert _splits(environment) == splits
        assert HarnessTaskSplit.no_workspace_objects.count() == len(splits)


class TestRequestScope:
    def test_another_environment_s_lock_is_not_found_and_stays_locked(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        other = _environment(user, workspace, storage, key="rel-other")
        _suite(other, _scenarios(20))
        assert _lock(auth_client, environment).status_code == 200
        [theirs] = _lock(auth_client, other).json()["locked_versions"]

        # The caller can see both environments, but the lock is the other one's.
        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/unlock/",
            {"locked_version_id": theirs["id"]},
            format="json",
        )

        assert response.status_code == 404
        assert response.json()["error"] == "locked_version_not_found"
        assert not HarnessLockedVersion.no_workspace_objects.filter(
            unlocked_at__isnull=False
        ).exists()

    @pytest.mark.parametrize(
        ("change", "field", "error"),
        [
            (
                {"suite_version_id": None},
                "suite_version_id",
                "This field may not be null.",
            ),
            (
                {"environment_version_id": "not-a-uuid"},
                "environment_version_id",
                "Must be a valid UUID.",
            ),
            ({"release_id": str(uuid.uuid4())}, "release_id", "Unknown field."),
            ({"suite_version_id": ...}, "suite_version_id", "This field is required."),
        ],
    )
    def test_the_lock_takes_only_its_two_version_ids(
        self, auth_client, user, workspace, storage, change, field, error
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        body = {
            key: value
            for key, value in {**_lock_body(environment), **change}.items()
            if value is not ...
        }

        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/lock/", body, format="json"
        )

        assert response.status_code == 400, response.content
        assert response.json()["details"][field] == [error]
        assert not HarnessLockedVersion.no_workspace_objects.exists()
        assert not HarnessTaskSplit.no_workspace_objects.exists()

    @pytest.mark.parametrize(
        ("body", "field", "error"),
        [
            ({}, "locked_version_id", "This field is required."),
            (
                {"locked_version_id": None},
                "locked_version_id",
                "This field may not be null.",
            ),
            (
                {"locked_version_id": "rel-1"},
                "locked_version_id",
                "Must be a valid UUID.",
            ),
            (
                {"locked_version_id": "LOCKED", "force": True},
                "force",
                "Unknown field.",
            ),
        ],
    )
    def test_the_unlock_takes_only_its_lock_id(
        self, auth_client, user, workspace, storage, body, field, error
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [locked_version] = _lock(auth_client, environment).json()["locked_versions"]
        if body.get("locked_version_id") == "LOCKED":
            body = {**body, "locked_version_id": locked_version["id"]}

        response = auth_client.post(
            f"{BASE}/{environment.id}/versions/unlock/", body, format="json"
        )

        assert response.status_code == 400, response.content
        assert response.json()["details"][field] == [error]
        assert HarnessLockedVersion.no_workspace_objects.get().unlocked_at is None


class TestReadCost:
    def test_member_reads_stay_flat_as_locked_versions_grow(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        _lock(auth_client, environment)
        # The preview reads the suite; the versions list no longer does.
        url = f"{BASE}/{environment.id}/versions/lock-preview/"

        def member_reads():
            with CaptureQueriesContext(connection) as queries:
                assert auth_client.get(url).status_code == 200
            return [
                query["sql"]
                for query in queries.captured_queries
                if "simulate_harness_suite_member" in query["sql"]
            ]

        # Both reads with an unlocked default, so the preview reads its suite each time.
        _suite(environment, _scenarios(25))
        one = member_reads()
        _lock(auth_client, environment)
        _suite(environment, _scenarios(30))
        two = member_reads()

        # Only the default suite's preview: a locked version keeps the counts it locked with.
        assert len(one) == len(two) == 1
        # Only the two keys the split reads, never the whole member content.
        assert all(
            '"content" FROM' not in sql and '"content",' not in sql for sql in two
        )

    def test_history_keeps_every_locked_version_and_the_newest_unlocked(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [locked] = _lock(auth_client, environment).json()["locked_versions"]
        first = HarnessLockedVersion.no_workspace_objects.get()
        # 25 newer lock-and-unlock cycles of an earlier pair.
        for number in range(25):
            later = first.locked_at + timedelta(minutes=number + 1)
            first.pk = uuid.uuid4()
            first.label = f"cycle.{number}"
            first.locked_at = later
            first.unlocked_at = later
            first.save()

        body = auth_client.get(f"{BASE}/{environment.id}/versions/").json()

        labels = [locked_version["label"] for locked_version in body["locked_versions"]]
        assert locked["label"] in labels
        assert len(labels) == 21
        assert labels[:20] == [f"cycle.{number}" for number in range(24, 4, -1)]


class TestLockedVersionsStay:
    def test_a_locked_version_cannot_be_hard_deleted(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        suite = _suite(environment, _scenarios(20))
        _lock(auth_client, environment)

        with pytest.raises(RestrictedError):
            HarnessSuiteVersion._base_manager.filter(id=suite.id).delete()

    def test_a_new_default_leaves_the_locked_version_as_it_was(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        locked_suite = _suite(environment, _scenarios(20))
        [locked_version] = _lock(auth_client, environment).json()["locked_versions"]

        # An edit makes a new, unlocked version and moves the default to it.
        _suite(environment, _scenarios(25))
        body = auth_client.get(f"{BASE}/{environment.id}/versions/").json()
        lock = _preview(auth_client, environment)

        [kept] = body["locked_versions"]
        assert kept == locked_version
        locked_suite.refresh_from_db()
        assert locked_suite.status == "active"
        assert lock["suite_version_id"] == str(environment.suite_version_id)
        assert lock["can_lock"] is True

    def test_the_whole_job_can_still_be_removed(
        self, auth_client, user, workspace, storage
    ):
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        _lock(auth_client, environment)

        from simulate.models import HarnessLockedVersionMember

        assert HarnessLockedVersionMember._base_manager.count() == 20

        HostedHarnessJob._base_manager.filter(environment=environment).delete()
        HostedHarnessJob._base_manager.filter(id=environment.id).delete()

        assert not HarnessLockedVersion._base_manager.exists()
        assert not HarnessTaskSplit._base_manager.exists()
        # The roster's scenario key restricts, but the job's cascade takes both.
        assert not HarnessLockedVersionMember._base_manager.exists()


class TestRoster:
    """A lock keeps its own roster: the scenarios it covers and their splits, as
    planned when it locked, so a later rule change never moves its members."""

    def test_the_roster_is_the_split_planned_at_lock(
        self, auth_client, user, workspace, storage
    ):
        from simulate.services.harness_versioning.locked_versions import (
            locked_version_members,
        )

        environment = _environment(user, workspace, storage)
        _suite(
            environment,
            _scenarios(30)
            + _scenarios(12, prefix="b", use_case="billing")
            + _scenarios(5, prefix="x", scorable=False),
        )
        preview = _preview(auth_client, environment)

        [locked] = _lock(auth_client, environment).json()["locked_versions"]

        roster = _keyed(environment, locked_version_members(locked["id"]))
        assert roster == _splits(environment)
        assert not any(key.startswith("x") for key in roster)
        splits = list(roster.values())
        assert (
            (splits.count("train"), splits.count("test"))
            == (
                locked["train"],
                locked["test"],
            )
            == (preview["train"], preview["test"])
        )

    def test_a_later_production_only_rule_leaves_an_old_lock_s_roster(
        self, auth_client, user, workspace, storage, settings
    ):
        from simulate.services.harness_versioning.locked_versions import (
            locked_version_members,
            production_bound,
            scorable_tasks,
        )

        settings.HARNESS_ACTORS_ENABLED = False
        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [locked] = _lock(auth_client, environment).json()["locked_versions"]
        before = locked_version_members(locked["id"])

        # Actors come on, and the default bundle now blocks five of the lock's tasks.
        settings.HARNESS_ACTORS_ENABLED = True
        _needs_production(environment, {f"s{index}" for index in range(5)})
        assert (
            len(
                scorable_tasks(
                    environment.suite_version_id,
                    environment_version_id=environment.environment_version_id,
                    blocked=production_bound(environment),
                )
            )
            == 15
        )

        body = auth_client.get(f"{BASE}/{environment.id}/versions/").json()

        assert locked_version_members(locked["id"]) == before
        assert len(before) == 20
        [kept] = body["locked_versions"]
        assert (kept["train"], kept["test"]) == (locked["train"], locked["test"])

    def test_a_later_suite_change_leaves_an_old_lock_s_roster(
        self, auth_client, user, workspace, storage
    ):
        from simulate.services.harness_versioning.locked_versions import (
            locked_version_members,
        )

        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [locked] = _lock(auth_client, environment).json()["locked_versions"]
        before = locked_version_members(locked["id"])

        # A new default suite drops ten of the lock's scenarios and adds fifteen,
        # and five of the dropped ones are deleted outright.
        _suite(environment, _scenarios(10) + _scenarios(15, prefix="n"))
        HostedHarnessScenario.all_objects.filter(
            job=environment, scenario_key__in=[f"s{index}" for index in range(15, 20)]
        ).update(deleted=True)
        body = auth_client.get(f"{BASE}/{environment.id}/versions/").json()

        assert len(before) == 20
        assert locked_version_members(locked["id"]) == before
        assert set(_keyed(environment, before)) == {f"s{index}" for index in range(20)}
        [kept] = body["locked_versions"]
        assert kept == locked
        # The new default is a new, unlocked pair the card offers to lock.
        assert _preview(auth_client, environment)["can_lock"] is True

    def test_a_relock_writes_a_new_roster_with_the_same_splits(
        self, auth_client, user, workspace, storage
    ):
        from simulate.models import HarnessLockedVersionMember
        from simulate.services.harness_versioning.locked_versions import (
            locked_version_members,
        )

        environment = _environment(user, workspace, storage)
        _suite(environment, _scenarios(20))
        [first] = _lock(auth_client, environment).json()["locked_versions"]
        auth_client.post(
            f"{BASE}/{environment.id}/versions/unlock/",
            {"locked_version_id": first["id"]},
            format="json",
        )

        second, _ = _lock(auth_client, environment).json()["locked_versions"]

        assert second["id"] != first["id"]
        assert locked_version_members(second["id"]) == locked_version_members(
            first["id"]
        )
        assert HarnessLockedVersionMember.no_workspace_objects.count() == 40


def test_lock_version_records_who_locked(user, workspace, storage):
    environment = _environment(user, workspace, storage)
    _suite(environment, _scenarios(20))

    lock_version(
        environment,
        environment_version_id=environment.environment_version_id,
        suite_version_id=environment.suite_version_id,
        user=user,
    )

    locked_version = HarnessLockedVersion.no_workspace_objects.get()
    assert locked_version.locked_by == user
    assert locked_version.environment == environment


class TestSubGoalShapes:
    """The JSON shapes the runnable rule reads."""

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            # A version recorded before the catalogue was.
            (None, frozenset()),
            ([], frozenset()),
            # Shapes no writer makes: none is a catalogue.
            ("refunded", frozenset()),
            ({"name": "refunded"}, frozenset()),
            (5, frozenset()),
            # Only named goal objects count.
            (
                [{"name": "refunded"}, {"name": ""}, "resolved", {"title": "x"}, 7],
                frozenset({"refunded"}),
            ),
            ([{"name": 3}], frozenset({"3"})),
        ],
    )
    def test_the_catalogue_reads_only_a_list_of_named_goals(self, document, expected):
        assert sub_goal_catalogue(document) == expected

    @pytest.mark.parametrize(
        ("sub_goals", "expected"),
        [
            # A member from before sub-goals is left to the run.
            (None, False),
            ("resolved", False),
            ({"name": "resolved"}, False),
            # A legacy member names its goals bare.
            (["refunded"], False),
            (["resolved"], True),
            ([{"name": "refunded"}], False),
            ([{"name": "resolved"}, "other"], True),
            # A goal object without a name names nothing graded.
            ([{"title": "refunded"}], True),
            ([], True),
        ],
    )
    def test_a_member_grades_nothing_only_when_its_list_names_no_graded_goal(
        self, sub_goals, expected
    ):
        assert grades_nothing(sub_goals, frozenset({GOAL})) is expected

    def test_an_empty_catalogue_grades_anything(self):
        assert grades_nothing(["resolved"], frozenset()) is False


def test_unlock_of_an_unknown_locked_version_is_not_found(user, workspace, storage):
    from simulate.services.harness_versioning.locked_versions import unlock_version

    environment = _environment(user, workspace, storage)

    with pytest.raises(HostedHarnessError) as refused:
        unlock_version(environment, "00000000-0000-0000-0000-000000000000", user=user)

    assert refused.value.code == "locked_version_not_found"
    assert refused.value.message == "This lock doesn't exist."
