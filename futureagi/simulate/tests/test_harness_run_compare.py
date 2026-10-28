import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Workspace
from simulate.models import HarnessEnvironmentVersion, HostedHarnessJob
from simulate.models import TestExecution as Execution
from simulate.models.test_execution import CallExecution
from simulate.services import run_compare
from simulate.services.harness_versioning.versions import (
    REMOVED_REASONS,
    record_authoring,
)
from simulate.services.run_results_v3_queries import (
    build_run_comparison,
    run_calls_queryset,
)
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_backfill import _RealisticStorage
from simulate.tests.test_harness_versioning_runs import (
    _run,
    _scenario,
    _suite_archive,
    _versioned,
)

BASE = "/simulate/api/harness-environments"
KEYS = [_key(name) for name in NAMES]


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


class TestNewcombeInterval:
    # Newcombe (1998), Statistics in Medicine 17:873-890, Table II, method 10.
    @pytest.mark.parametrize(
        ("counts", "expected"),
        [
            ((56, 70, 48, 80), (0.0524, 0.3339)),
            ((9, 10, 3, 10), (0.1705, 0.8090)),
            ((6, 7, 2, 7), (0.0582, 0.8062)),
            ((5, 56, 0, 29), (-0.0381, 0.1926)),
            ((0, 10, 0, 20), (-0.1611, 0.2775)),
            ((10, 10, 0, 20), (0.6791, 1.0000)),
        ],
    )
    def test_matches_the_published_values(self, counts, expected):
        low, high = run_compare.newcombe_interval(*counts)

        assert (round(low, 4), round(high, 4)) == expected

    def test_wilson_interval_of_none_passing(self):
        low, high = run_compare.wilson_interval(0, 10)

        assert (round(low, 4), round(high, 4)) == (0.0, 0.2775)


class TestVerdict:
    @pytest.mark.parametrize("counts", [(1, 1, 1, 1), (3, 3, 3, 3), (0, 2, 0, 2)])
    def test_an_identical_pair_is_no_meaningful_change(self, counts):
        outcome, interval = run_compare.verdict(*counts)

        assert outcome == "no_meaningful_change"
        # The interval is still reported, however wide.
        assert interval[0] < -0.1 < 0.1 < interval[1]

    def test_an_interval_inside_the_margin_is_no_meaningful_change(self):
        outcome, (low, high) = run_compare.verdict(500, 1000, 480, 1000)

        assert outcome == "no_meaningful_change"
        assert -0.1 < low < 0 < high < 0.1

    def test_improved_and_regressed(self):
        assert run_compare.verdict(10, 10, 0, 10)[0] == "improved"
        assert run_compare.verdict(0, 10, 10, 10)[0] == "regressed"

    def test_a_change_exactly_at_the_margin_is_not_meaningful_enough(self):
        outcome, (low, _) = run_compare.verdict(1000, 1000, 900, 1000)

        assert low > 0
        assert outcome == "inconclusive"

    def test_just_past_the_margin_is_improved(self):
        outcome, (low, _) = run_compare.verdict(1000, 1000, 899, 1000)

        assert low > 0
        assert outcome == "improved"

    def test_just_past_the_margin_the_other_way_is_regressed(self):
        assert run_compare.verdict(899, 1000, 1000, 1000)[0] == "regressed"
        assert run_compare.verdict(900, 1000, 1000, 1000)[0] == "inconclusive"

    def test_too_few_trials_is_inconclusive(self):
        outcome, interval = run_compare.verdict(5, 5, 4, 5)

        assert outcome == "inconclusive"
        assert interval[0] < 0 < interval[1]

    def test_no_decided_trials_is_inconclusive_without_an_interval(self):
        assert run_compare.verdict(0, 0, 3, 3) == ("inconclusive", None)


class TestTrialPassed:
    def test_every_sub_goal_held(self):
        held = [{"name": "a", "held": True}, {"name": "b", "held": True}]

        assert run_compare.trial_passed(held, "failed") is True

    def test_one_sub_goal_failed(self):
        held = [{"name": "a", "held": True}, {"name": "b", "held": False}]

        assert run_compare.trial_passed(held, "passed") is False

    @pytest.mark.parametrize("other", [True, False])
    def test_an_undecided_sub_goal_leaves_the_trial_undecided(self, other):
        held = [{"name": "a", "held": other}, {"name": "b", "held": None}]

        assert run_compare.trial_passed(held, "passed") is None
        assert run_compare.trial_passed(held, "failed") is None

    def test_undecided_sub_goals_never_read_as_a_regression(self):
        # A judge that left one check open on every trial: failing and passing
        # trials drop out alike, so the pass rate is not pushed down.
        before, after = (run_compare.ScenarioTrials("refund") for _ in range(2))
        for held in [True] * 5 + [False] * 5:
            outcome = "passed" if held else "failed"
            before.add([{"name": "a", "held": held}], outcome, outcome, None)
            after.add(
                [{"name": "a", "held": held}, {"name": "b", "held": None}],
                outcome,
                outcome,
                None,
            )

        assert (before.passed, before.decided) == (5, 10)
        assert after.decided == 0
        assert run_compare.verdict(
            after.passed, after.decided, before.passed, before.decided
        ) == ("inconclusive", None)

    def test_without_sub_goals_the_receipt_status_decides(self):
        assert run_compare.trial_passed(None, "passed") is True
        assert run_compare.trial_passed([], "failed") is False
        assert run_compare.trial_passed(None, "error") is None

    def test_evals_never_decide_a_trial(self):
        trials = run_compare.ScenarioTrials("refund")
        trials.add([{"name": "a", "held": True}], "passed", "failed", "failed")

        assert trials.side() == {
            "passed": 1,
            "trials": 1,
            "undecided": 0,
            "pass_rate": 100.0,
            "eval_pass_rate": 0.0,
        }

    def test_a_call_that_never_ran_is_not_a_trial(self):
        trials = run_compare.ScenarioTrials("refund")
        trials.add([{"name": "a", "held": False}], "error", "error", None)
        trials.add([{"name": "a", "held": None}], "queued", "queued", None)

        assert (trials.side()["trials"], trials.side()["undecided"]) == (0, 0)

    def test_a_finished_trial_nothing_decided_is_counted_undecided(self):
        trials = run_compare.ScenarioTrials("refund")
        trials.add(
            [{"name": "a", "held": False}, {"name": "b", "held": None}],
            "failed",
            "failed",
            None,
        )
        trials.add([{"name": "a", "held": True}], "passed", "passed", None)

        side = trials.side()
        assert (side["passed"], side["trials"], side["undecided"]) == (1, 1, 1)


def _done(run, *, ago=None):
    """Mark a run's execution completed, optionally ``ago`` in the past."""
    fields = {"status": Execution.ExecutionStatus.COMPLETED}
    Execution.objects.filter(id=run.test_execution_id).update(**fields)
    if ago is not None:
        execution = Execution.objects.get(id=run.test_execution_id)
        Execution.objects.filter(id=execution.id).update(
            created_at=execution.created_at - ago
        )
    return Execution.objects.get(id=run.test_execution_id)


def _trials(run, results, *, evals_pass=True):
    """Give the run's calls these ``(key, held)`` results, one per call in id order.

    ``held`` is the value of the trial's one sub-goal, ``refunded``, or a tuple of
    values for several; the receipt's status follows the harness: any check that
    failed fails it, else any undecided one leaves it open.
    """
    calls = list(
        CallExecution.no_workspace_objects.filter(
            test_execution_id=run.test_execution_id
        ).order_by("id")
    )
    assert len(calls) == len(results)
    for call, (key, held) in zip(calls, results, strict=True):
        checks = held if isinstance(held, tuple) else (held,)
        status = (
            "failed"
            if False in checks
            else "inconclusive" if None in checks else "passed"
        )
        call.status = "completed"
        call.call_metadata = {
            "harness_scenario_key": key,
            "harness_outcome_status": status,
            "hosted_harness_receipt": {
                "scenario_key": key,
                "status": status,
                "sub_goals": [
                    {"name": f"refunded-{index}" if index else "refunded", "held": one}
                    for index, one in enumerate(checks)
                ],
            },
        }
        call.eval_outputs = {
            "policy": {
                "name": "Policy",
                "output": "Passed" if evals_pass else "Failed",
                "source": "harness",
            }
        }
        call.save(update_fields=["status", "call_metadata", "eval_outputs"])


def _compare(execution, against=None):
    return build_run_comparison(
        execution, run_calls_queryset(execution), against=against, with_pairs=True
    )


def _by_key(comparison):
    return {pair["scenario_key"]: pair for pair in comparison["pairs"]}


def _two_scenarios(user, workspace, storage, name):
    environment = _versioned(user, workspace, name, storage)
    record_authoring(
        environment,
        _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[1])]),
        origin="chat",
    )
    environment.refresh_from_db()
    return environment


@pytest.mark.django_db
class TestBuildRunComparison:
    def test_pairs_on_the_scenario_key(self, user, workspace, storage):
        environment = _two_scenarios(user, workspace, storage, "cmp-pairs")
        before = _run(environment, KEYS[:2], trials=2, key="before")
        _trials(
            before,
            [(KEYS[0], False), (KEYS[0], False), (KEYS[1], True), (KEYS[1], True)],
        )
        after = _run(environment, KEYS[:2], trials=2, key="after")
        _trials(
            after,
            [(KEYS[0], True), (KEYS[0], True), (KEYS[1], True), (KEYS[1], True)],
        )
        _done(before, ago=timedelta(minutes=5))

        comparison = _compare(_done(after))

        pairs = _by_key(comparison)
        assert set(pairs) == set(KEYS[:2])
        first, second = pairs[KEYS[0]], pairs[KEYS[1]]
        assert first["current"]["pass_rate"] == 100.0
        assert first["against"]["pass_rate"] == 0.0
        assert first["difference"] == 100.0
        assert first["band"] == "paired"
        assert second["verdict"] == "no_meaningful_change"
        assert comparison["summary"]["paired"] == 2

    def test_evals_never_change_the_verdict(self, user, workspace, storage):
        environment = _two_scenarios(user, workspace, storage, "cmp-evals")
        before = _run(environment, KEYS[:1], key="before")
        _trials(before, [(KEYS[0], True)], evals_pass=True)
        after = _run(environment, KEYS[:1], key="after")
        _trials(after, [(KEYS[0], True)], evals_pass=False)
        _done(before, ago=timedelta(minutes=5))

        pair = _by_key(_compare(_done(after)))[KEYS[0]]

        assert pair["verdict"] == "no_meaningful_change"
        assert pair["current"]["eval_pass_rate"] == 0.0
        assert pair["against"]["eval_pass_rate"] == 100.0

    def test_against_defaults_to_the_previous_completed_run(
        self, user, workspace, storage
    ):
        environment = _two_scenarios(user, workspace, storage, "cmp-default")
        oldest = _run(environment, KEYS[:1], key="oldest")
        _trials(oldest, [(KEYS[0], True)])
        _done(oldest, ago=timedelta(minutes=10))
        unfinished = _run(environment, KEYS[:1], key="unfinished")
        _trials(unfinished, [(KEYS[0], True)])
        Execution.objects.filter(id=unfinished.test_execution_id).update(
            created_at=Execution.objects.get(id=oldest.test_execution_id).created_at
            + timedelta(minutes=5)
        )
        current = _run(environment, KEYS[:1], key="current")
        _trials(current, [(KEYS[0], True)])

        comparison = _compare(_done(current))

        assert comparison["previous_execution_id"] == str(oldest.test_execution_id)
        picked = _compare(
            _done(current),
            against=Execution.objects.get(id=unfinished.test_execution_id),
        )
        assert picked["previous_execution_id"] == str(unfinished.test_execution_id)

    def test_with_nothing_before_every_scenario_is_new(self, user, workspace, storage):
        environment = _two_scenarios(user, workspace, storage, "cmp-first")
        only = _run(environment, KEYS[:1], key="only")
        _trials(only, [(KEYS[0], True)])

        comparison = _compare(_done(only))

        assert comparison["available"] is False
        assert [pair["band"] for pair in comparison["pairs"]] == ["new"]
        assert comparison["versions"]["against"] is None
        assert comparison["versions"]["current"]["suite"]["label"]

    def test_new_and_not_applicable_bands_with_the_removal_reason(
        self, user, workspace, storage
    ):
        environment = _two_scenarios(user, workspace, storage, "cmp-bands")
        before = _run(environment, KEYS[:2], key="before")
        _trials(before, [(KEYS[0], True), (KEYS[1], True)])
        record_authoring(
            environment,
            _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[2])]),
            origin="authoring",
        )
        environment.refresh_from_db()
        after = _run(environment, [KEYS[0], KEYS[2]], key="after")
        _trials(after, [(KEYS[0], True), (KEYS[2], False)])
        _done(before, ago=timedelta(minutes=5))

        comparison = _compare(_done(after))

        pairs = _by_key(comparison)
        assert pairs[KEYS[2]]["band"] == "new"
        assert pairs[KEYS[2]]["verdict"] is None
        gone = pairs[KEYS[1]]
        assert gone["band"] == "not_applicable"
        assert gone["current"] is None
        assert gone["reason"] == REMOVED_REASONS["authoring"]
        assert gone["removed_in"] == {
            "id": str(environment.suite_version_id),
            "label": f"v{environment.suite_version.version_number}",
        }
        assert comparison["summary"]["new"] == 1
        assert comparison["summary"]["not_applicable"] == 1
        current, against = (
            comparison["versions"]["current"],
            comparison["versions"]["against"],
        )
        assert current["suite"]["id"] == str(environment.suite_version_id)
        assert against["suite"]["id"] != current["suite"]["id"]

    def test_a_key_the_compared_run_left_out_is_not_new(self, user, workspace, storage):
        environment = _two_scenarios(user, workspace, storage, "cmp-not-run")
        before = _run(environment, KEYS[:1], key="before")
        _trials(before, [(KEYS[0], True)])
        after = _run(environment, KEYS[:2], key="after")
        _trials(after, [(KEYS[0], True), (KEYS[1], True)])
        _done(before, ago=timedelta(minutes=5))

        comparison = _compare(_done(after))

        skipped = _by_key(comparison)[KEYS[1]]
        assert (skipped["band"], skipped["reason"], skipped["against"]) == (
            "not_applicable",
            None,
            None,
        )
        assert skipped["current"]["trials"] == 1
        assert comparison["summary"]["new"] == 0

    def test_against_a_newer_run_the_bands_read_from_old_to_new(
        self, user, workspace, storage
    ):
        environment = _two_scenarios(user, workspace, storage, "cmp-newer")
        before = _run(environment, KEYS[:2], key="before")
        _trials(before, [(KEYS[0], True), (KEYS[1], True)])
        record_authoring(
            environment,
            _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[2])]),
            origin="authoring",
        )
        environment.refresh_from_db()
        after = _run(environment, [KEYS[0], KEYS[2]], key="after")
        _trials(after, [(KEYS[0], True), (KEYS[2], False)])
        older = _done(before, ago=timedelta(minutes=5))

        comparison = _compare(older, against=_done(after))

        pairs = _by_key(comparison)
        gone = pairs[KEYS[1]]
        assert (gone["band"], gone["against"]) == ("not_applicable", None)
        assert gone["reason"] == REMOVED_REASONS["authoring"]
        assert gone["removed_in"]["id"] == str(environment.suite_version_id)
        added = pairs[KEYS[2]]
        assert (added["band"], added["current"]) == ("new", None)
        assert added["against"]["trials"] == 1

    def test_a_later_run_on_an_older_suite_reads_from_old_to_new(
        self, user, workspace, storage
    ):
        environment = _two_scenarios(user, workspace, storage, "cmp-rerun-old")
        both = environment.suite_version
        record_authoring(
            environment,
            _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[2])]),
            origin="manual",
        )
        environment.refresh_from_db()
        edited = environment.suite_version
        on_edited = _run(environment, [KEYS[0], KEYS[2]], key="edited")
        _trials(on_edited, [(KEYS[0], True), (KEYS[2], True)])
        # A rerun of an older run pins the older suite, though it is made later.
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            suite_version=both
        )
        environment.refresh_from_db()
        on_both = _run(environment, KEYS[:2], key="both")
        _trials(on_both, [(KEYS[0], True), (KEYS[1], True)])
        _done(on_edited, ago=timedelta(minutes=5))

        comparison = _compare(_done(on_both))

        assert comparison["previous_execution_id"] == str(on_edited.test_execution_id)
        pairs = _by_key(comparison)
        dropped = pairs[KEYS[1]]
        assert (dropped["band"], dropped["reason"]) == (
            "not_applicable",
            REMOVED_REASONS["manual"],
        )
        assert dropped["removed_in"] == {
            "id": str(edited.id),
            "label": f"v{edited.version_number}",
        }
        added = pairs[KEYS[2]]
        assert (added["band"], added["current"]) == ("new", None)
        assert comparison["summary"]["new"] == 1

    def test_undecided_trials_are_counted_beside_the_decided(
        self, user, workspace, storage
    ):
        environment = _two_scenarios(user, workspace, storage, "cmp-undecided")
        before = _run(environment, KEYS[:1], trials=2, key="before")
        _trials(before, [(KEYS[0], True), (KEYS[0], True)])
        after = _run(environment, KEYS[:1], trials=2, key="after")
        # The judge failed beside a check that did not hold: the run's results
        # count this trial failed, the comparison leaves it out and says so.
        _trials(after, [(KEYS[0], (False, None)), (KEYS[0], True)])
        _done(before, ago=timedelta(minutes=5))

        pair = _by_key(_compare(_done(after)))[KEYS[0]]

        assert pair["current"] == {
            "passed": 1,
            "trials": 1,
            "undecided": 1,
            "pass_rate": 100.0,
            "eval_pass_rate": 100.0,
        }
        assert (pair["against"]["trials"], pair["against"]["undecided"]) == (2, 0)

    def test_the_removal_is_read_along_the_suite_lineage(
        self, user, workspace, storage
    ):
        environment = _two_scenarios(user, workspace, storage, "cmp-lineage")
        both = environment.suite_version
        before = _run(environment, KEYS[:2], key="before")
        _trials(before, [(KEYS[0], True), (KEYS[1], True)])
        # A manual edit drops the second scenario, then the default is rolled back.
        record_authoring(
            environment, _suite_archive([_scenario(NAMES[0])]), origin="manual"
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            suite_version=both
        )
        environment.refresh_from_db()
        record_authoring(
            environment,
            _suite_archive([_scenario(name) for name in NAMES]),
            origin="extend",
        )
        environment.refresh_from_db()
        middle = _run(environment, [KEYS[0], KEYS[2]], key="middle")
        _trials(middle, [(KEYS[0], True), (KEYS[2], True)])
        record_authoring(
            environment,
            _suite_archive([_scenario(NAMES[0]), _scenario(NAMES[2])]),
            origin="chat",
        )
        environment.refresh_from_db()
        after = _run(environment, [KEYS[0], KEYS[2]], key="after")
        _trials(after, [(KEYS[0], True), (KEYS[2], True)])
        against = _done(before, ago=timedelta(minutes=10))
        _done(middle, ago=timedelta(minutes=5))

        gone = _by_key(_compare(_done(after), against=against))[KEYS[1]]
        # Still in the middle run's suite, so it was only left out of that run.
        left_out = _by_key(_compare(_done(middle), against=against))[KEYS[1]]

        assert gone["reason"] == REMOVED_REASONS["chat"]
        assert gone["removed_in"] == {
            "id": str(environment.suite_version_id),
            "label": f"v{environment.suite_version.version_number}",
        }
        assert (left_out["reason"], left_out["removed_in"]) == (None, None)

    def test_a_suite_off_the_lineage_names_no_reason(self, user, workspace, storage):
        environment = _two_scenarios(user, workspace, storage, "cmp-branch")
        both = environment.suite_version
        record_authoring(
            environment, _suite_archive([_scenario(NAMES[0])]), origin="manual"
        )
        environment.refresh_from_db()
        before = _run(environment, KEYS[:1], key="before")
        _trials(before, [(KEYS[0], True)])
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            suite_version=both
        )
        environment.refresh_from_db()
        record_authoring(
            environment,
            _suite_archive([_scenario(NAMES[1]), _scenario(NAMES[2])]),
            origin="chat",
        )
        environment.refresh_from_db()
        after = _run(environment, KEYS[1:], key="after")
        _trials(after, [(KEYS[1], True), (KEYS[2], True)])
        _done(before, ago=timedelta(minutes=5))

        gone = _by_key(_compare(_done(after)))[KEYS[0]]

        assert (gone["band"], gone["reason"], gone["removed_in"]) == (
            "not_applicable",
            None,
            None,
        )

    def test_a_key_left_out_of_a_run_has_no_reason(self, user, workspace, storage):
        environment = _two_scenarios(user, workspace, storage, "cmp-subset")
        before = _run(environment, KEYS[:2], key="before")
        _trials(before, [(KEYS[0], True), (KEYS[1], True)])
        after = _run(environment, KEYS[:1], key="after")
        _trials(after, [(KEYS[0], True)])
        _done(before, ago=timedelta(minutes=5))

        gone = _by_key(_compare(_done(after)))[KEYS[1]]

        assert (gone["band"], gone["reason"], gone["removed_in"]) == (
            "not_applicable",
            None,
            None,
        )

    def test_a_sub_goal_redefined_under_the_same_name_is_criteria_changed(
        self, user, workspace, storage
    ):
        environment = _two_scenarios(user, workspace, storage, "cmp-criteria")
        before = _run(environment, KEYS[:2], key="before")
        _trials(before, [(KEYS[0], True), (KEYS[1], True)])
        after = _run(environment, KEYS[:2], key="after")
        _trials(after, [(KEYS[0], False), (KEYS[1], True)])
        redefined = HarnessEnvironmentVersion.no_workspace_objects.get(
            id=after.environment_version_id
        )
        digests = dict(redefined.documents["sub_goal_digests"])
        assert "refunded" in digests
        redefined.pk = uuid.uuid4()
        redefined.version_number = 99
        redefined.hash = "sha256:" + "e" * 64
        redefined.documents = {
            **redefined.documents,
            "sub_goal_digests": {**digests, "refunded": "sha256:" + "f" * 64},
        }
        redefined.save()
        HostedHarnessJob.no_workspace_objects.filter(id=after.id).update(
            environment_version=redefined
        )
        _done(before, ago=timedelta(minutes=5))

        comparison = _compare(_done(after))

        pair = _by_key(comparison)[KEYS[0]]
        assert pair["band"] == "criteria_changed"
        assert pair["changed_sub_goals"] == ["refunded"]
        assert pair["verdict"] is not None
        assert comparison["summary"]["criteria_changed"] == 2
        assert comparison["summary"]["paired"] == 0
        assert comparison["versions"]["current"]["environment"]["label"] == "v99"

    def test_the_same_environment_version_never_changes_criteria(
        self, user, workspace, storage
    ):
        environment = _two_scenarios(user, workspace, storage, "cmp-same-env")
        before = _run(environment, KEYS[:1], key="before")
        _trials(before, [(KEYS[0], True)])
        after = _run(environment, KEYS[:1], key="after")
        _trials(after, [(KEYS[0], True)])
        _done(before, ago=timedelta(minutes=5))

        pair = _by_key(_compare(_done(after)))[KEYS[0]]

        assert (pair["band"], pair["changed_sub_goals"]) == ("paired", [])

    def test_unpinned_runs_still_compare(self, user, workspace, storage):
        environment = _two_scenarios(user, workspace, storage, "cmp-legacy")
        before = _run(environment, KEYS[:2], key="before")
        _trials(before, [(KEYS[0], False), (KEYS[1], True)])
        after = _run(environment, KEYS[:1], key="after")
        _trials(after, [(KEYS[0], True)])
        HostedHarnessJob.no_workspace_objects.filter(
            id__in=[before.id, after.id]
        ).update(agent_version=None, environment_version=None, suite_version=None)
        _done(before, ago=timedelta(minutes=5))

        comparison = _compare(_done(after))

        nothing = {"agent": None, "environment": None, "suite": None}
        assert comparison["versions"] == {"current": nothing, "against": nothing}
        pairs = _by_key(comparison)
        assert pairs[KEYS[0]]["band"] == "paired"
        assert pairs[KEYS[0]]["current"]["pass_rate"] == 100.0
        assert pairs[KEYS[1]]["band"] == "not_applicable"
        assert pairs[KEYS[1]]["reason"] is None

    def test_the_legacy_fields_keep_their_reading(self, user, workspace, storage):
        environment = _two_scenarios(user, workspace, storage, "cmp-legacy-fields")
        before = _run(environment, KEYS[:2], key="before")
        _trials(before, [(KEYS[0], False), (KEYS[1], True)])
        after = _run(environment, KEYS[:2], key="after")
        _trials(after, [(KEYS[0], True), (KEYS[1], False)])
        _done(before, ago=timedelta(minutes=5))
        execution = _done(after)

        comparison = build_run_comparison(execution, run_calls_queryset(execution))

        assert comparison == {
            "available": True,
            "previous_execution_id": str(before.test_execution_id),
            "shared_scenarios": 2,
            "newly_passing": [KEYS[0]],
            "newly_failing": [KEYS[1]],
        }
        assert {
            key: value
            for key, value in _compare(execution).items()
            if key not in ("pairs", "summary", "versions")
        } == comparison

    def test_without_pairs_a_first_run_is_not_scanned(
        self, user, workspace, storage, django_assert_num_queries
    ):
        environment = _two_scenarios(user, workspace, storage, "cmp-legacy-first")
        only = _run(environment, KEYS[:1], key="only")
        _trials(only, [(KEYS[0], True)])
        execution = _done(only)
        queryset = run_calls_queryset(execution)

        # Only the run's run test and the look for an earlier run: no scan.
        with django_assert_num_queries(2):
            comparison = build_run_comparison(execution, queryset)

        assert comparison == {
            "available": False,
            "previous_execution_id": None,
            "shared_scenarios": 0,
            "newly_passing": [],
            "newly_failing": [],
        }


@pytest.mark.django_db
class TestCompareEndpoint:
    def _pair(self, user, workspace, storage, name):
        environment = _two_scenarios(user, workspace, storage, name)
        before = _run(environment, KEYS[:1], key="before")
        _trials(before, [(KEYS[0], False)])
        after = _run(environment, KEYS[:1], key="after")
        _trials(after, [(KEYS[0], True)])
        _done(before, ago=timedelta(minutes=5))
        _done(after)
        return environment, before, after

    def test_shape(self, auth_client, user, workspace, storage):
        environment, before, after = self._pair(user, workspace, storage, "cmp-api")

        response = auth_client.get(
            f"{BASE}/{environment.id}/compare/",
            {"execution_id": str(after.test_execution_id)},
        )

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "execution_id",
            "against_execution_id",
            "pairs",
            "summary",
            "versions",
        }
        assert body["execution_id"] == str(after.test_execution_id)
        assert body["against_execution_id"] == str(before.test_execution_id)
        [pair] = body["pairs"]
        assert pair["scenario_key"] == KEYS[0]
        assert pair["verdict"] == "inconclusive"
        assert pair["interval"]["low"] < 0 < pair["interval"]["high"]
        assert set(body["versions"]) == {"current", "against"}
        assert set(body["versions"]["current"]) == {"agent", "environment", "suite"}
        assert body["versions"]["current"]["suite"]["label"].startswith("v")

    def test_an_explicit_against_run(self, auth_client, user, workspace, storage):
        environment, before, after = self._pair(user, workspace, storage, "cmp-pick")

        response = auth_client.get(
            f"{BASE}/{environment.id}/compare/",
            {
                "execution_id": str(before.test_execution_id),
                "against_execution_id": str(after.test_execution_id),
            },
        )

        assert response.status_code == 200
        assert response.json()["against_execution_id"] == str(after.test_execution_id)

    def test_a_run_against_itself_is_refused(
        self, auth_client, user, workspace, storage
    ):
        environment, _, after = self._pair(user, workspace, storage, "cmp-self")

        response = auth_client.get(
            f"{BASE}/{environment.id}/compare/",
            {
                "execution_id": str(after.test_execution_id),
                "against_execution_id": str(after.test_execution_id),
            },
        )

        assert response.status_code == 400

    @pytest.mark.parametrize("side", ["current", "against"])
    @pytest.mark.parametrize(
        "unfinished,code",
        [
            (Execution.ExecutionStatus.RUNNING, "comparison_run_unfinished"),
            (Execution.ExecutionStatus.EVALUATING, "comparison_run_unfinished"),
            (Execution.ExecutionStatus.FAILED, "comparison_run_failed"),
            (Execution.ExecutionStatus.CANCELLED, "comparison_run_cancelled"),
        ],
    )
    def test_a_run_that_did_not_complete_is_refused(
        self, auth_client, user, workspace, storage, unfinished, code, side
    ):
        environment, before, after = self._pair(
            user, workspace, storage, f"cmp-{unfinished}-{side}"
        )
        refused = before if side == "against" else after
        Execution.objects.filter(id=refused.test_execution_id).update(status=unfinished)

        response = auth_client.get(
            f"{BASE}/{environment.id}/compare/",
            {
                "execution_id": str(after.test_execution_id),
                "against_execution_id": str(before.test_execution_id),
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == code
        assert response.json()["execution_id"] == str(refused.test_execution_id)

    def test_a_run_still_going_is_not_compared_with_the_previous(
        self, auth_client, user, workspace, storage
    ):
        environment, _, after = self._pair(user, workspace, storage, "cmp-live")
        Execution.objects.filter(id=after.test_execution_id).update(
            status=Execution.ExecutionStatus.RUNNING
        )

        response = auth_client.get(
            f"{BASE}/{environment.id}/compare/",
            {"execution_id": str(after.test_execution_id)},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "comparison_run_unfinished"

    def test_a_run_of_another_environment_is_not_found(
        self, auth_client, user, workspace, storage
    ):
        environment, _, after = self._pair(user, workspace, storage, "cmp-mine")
        _, _, foreign = self._pair(user, workspace, storage, "cmp-theirs")

        for params in (
            {"execution_id": str(foreign.test_execution_id)},
            {
                "execution_id": str(after.test_execution_id),
                "against_execution_id": str(foreign.test_execution_id),
            },
        ):
            response = auth_client.get(f"{BASE}/{environment.id}/compare/", params)
            assert response.status_code == 404

    def test_another_workspaces_environment_is_not_found(
        self, auth_client, user, workspace, storage
    ):
        environment, _, after = self._pair(user, workspace, storage, "cmp-ws")
        other = Workspace.objects.create(
            name="Other",
            organization=workspace.organization,
            is_active=True,
            created_by=user,
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            workspace=other
        )

        response = auth_client.get(
            f"{BASE}/{environment.id}/compare/",
            {"execution_id": str(after.test_execution_id)},
        )

        assert response.status_code == 404

    def test_works_with_versioning_off(
        self, auth_client, user, workspace, storage, settings
    ):
        environment, _, after = self._pair(user, workspace, storage, "cmp-off")
        settings.HARNESS_VERSIONING_ENABLED = False

        response = auth_client.get(
            f"{BASE}/{environment.id}/compare/",
            {"execution_id": str(after.test_execution_id)},
        )

        assert response.status_code == 200
        assert len(response.json()["pairs"]) == 1

    def test_requires_the_execution(self, auth_client, user, workspace, storage):
        environment, _, _ = self._pair(user, workspace, storage, "cmp-missing")

        response = auth_client.get(f"{BASE}/{environment.id}/compare/")

        assert response.status_code == 400


@pytest.mark.django_db
class TestRunListSummary:
    def test_a_summary_page_reads_no_calls(self, auth_client, user, workspace, storage):
        environment = _two_scenarios(user, workspace, storage, "cmp-summary")
        first = _run(environment, KEYS[:2], trials=2, key="first")
        _done(first)
        second = _run(environment, KEYS[:1], key="second")
        url = f"/simulate/run-tests/{environment.run_test_id}/executions/"
        calls = CallExecution._meta.db_table

        with CaptureQueriesContext(connection) as full:
            assert auth_client.get(url, {"page": 1, "limit": 50}).status_code == 200
        with CaptureQueriesContext(connection) as summary:
            response = auth_client.get(url, {"page": 1, "limit": 50, "summary": "true"})

        assert response.status_code == 200
        assert any(calls in query["sql"] for query in full.captured_queries)
        assert not any(calls in query["sql"] for query in summary.captured_queries)
        assert len(summary) < len(full)
        body = response.json()
        assert body["count"] == 2
        newest, oldest = body["results"]
        assert [newest["id"], oldest["id"]] == [
            str(second.test_execution_id),
            str(first.test_execution_id),
        ]
        assert set(oldest) == {
            "id",
            "status",
            "environment_version",
            "suite_version",
            "pinned_agent_version",
            "agent_differs",
        }
        assert oldest["status"] == "Completed"
        assert oldest["suite_version"]["id"] == str(first.suite_version_id)
