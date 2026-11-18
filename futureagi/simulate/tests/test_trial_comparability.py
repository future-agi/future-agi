"""A trial whose start differs from the seeded records is not comparable: the call says so with the tables named, and
the run comparison, pass^k and the pass-rate trend leave it out and count it. A trial that started clean is unaffected.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from simulate.models.test_execution import CallExecution
from simulate.selectors.call_detail import comparability
from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
from simulate.services.run_results_v3_queries import (
    build_run_analytics,
    build_run_reliability,
)
from simulate.tests import test_harness_run_compare as compare
from simulate.tests.test_harness_run_compare import (
    KEYS,
    _by_key,
    _compare,
    _done,
    _trials,
    _two_scenarios,
)
from simulate.tests.test_harness_versioning_runs import _run

# The compare suite's fixtures: versioning on, and a fake object store.
storage = compare.storage
versioning_on = compare.versioning_on

DIFFERS = {"state": "differs", "tables": ["orders", "refunds"], "against": "seed"}


def _call(start):
    state = {} if start is None else {"start": start}
    return SimpleNamespace(id="call", call_metadata={TRIAL_STATE_KEY: state})


def _start_differs(run, index):
    """Record that the run's ``index``-th call (in id order) did not start from the seeded records."""
    call = list(
        CallExecution.no_workspace_objects.filter(
            test_execution_id=run.test_execution_id
        ).order_by("id")
    )[index]
    call.call_metadata = {**call.call_metadata, TRIAL_STATE_KEY: {"start": DIFFERS}}
    call.save(update_fields=["call_metadata"])


def test_a_trial_whose_start_differs_is_not_comparable_and_names_the_tables():
    shown = comparability(_call(DIFFERS))

    assert shown["comparable"] is False
    assert "orders, refunds" in shown["not_comparable_reason"]


@pytest.mark.parametrize(
    "start",
    [None, {"state": "seeded", "against": "seed"}, {"state": "unknown"}],
    ids=["no-start", "seeded", "unknown"],
)
def test_a_trial_that_started_clean_is_comparable(start):
    assert comparability(_call(start)) == {
        "comparable": True,
        "not_comparable_reason": None,
    }


@pytest.mark.django_db
class TestComparisonLeavesOutTrialsThatDidNotStartClean:
    def _runs(self, user, workspace, storage, name, *, differs):
        environment = _two_scenarios(user, workspace, storage, name)
        before = _run(environment, KEYS[:1], trials=2, key="before")
        _trials(before, [(KEYS[0], True), (KEYS[0], True)])
        after = _run(environment, KEYS[:1], trials=2, key="after")
        _trials(after, [(KEYS[0], True), (KEYS[0], False)])
        if differs:
            _start_differs(after, 1)
        _done(before, ago=timedelta(minutes=5))
        return _done(after)

    def test_a_differing_trial_is_left_out_and_counted(self, user, workspace, storage):
        comparison = _compare(
            self._runs(user, workspace, storage, "cmp-differs", differs=True)
        )

        current = _by_key(comparison)[KEYS[0]]["current"]
        assert (current["passed"], current["trials"]) == (1, 1)
        assert comparison["summary"]["excluded_not_comparable"] == 1

    def test_clean_trials_are_all_compared(self, user, workspace, storage):
        comparison = _compare(
            self._runs(user, workspace, storage, "cmp-clean", differs=False)
        )

        current = _by_key(comparison)[KEYS[0]]["current"]
        assert (current["passed"], current["trials"]) == (1, 2)
        assert comparison["summary"]["excluded_not_comparable"] == 0


@pytest.mark.django_db
class TestPassHatKLeavesOutTrialsThatDidNotStartClean:
    def _run_of_three(self, user, workspace, storage, name, *, differs):
        environment = _two_scenarios(user, workspace, storage, name)
        run = _run(environment, KEYS[:1], trials=3, key=name)
        _trials(run, [(KEYS[0], True), (KEYS[0], True), (KEYS[0], False)])
        if differs:
            _start_differs(run, 2)
        return _done(run)

    def test_a_differing_trial_is_left_out_and_counted(self, user, workspace, storage):
        execution = self._run_of_three(
            user, workspace, storage, "rel-differs", differs=True
        )

        reliability = build_run_reliability(execution)

        assert reliability["excluded_not_comparable"] == 1
        assert (reliability["k"], reliability["pass_hat_k"]) == (2, 100.0)
        assert reliability["rows"][0]["runs"] == 2

    def test_analytics_leave_it_out_of_reliability_and_the_trend(
        self, user, workspace, storage
    ):
        execution = self._run_of_three(
            user, workspace, storage, "rel-trend", differs=True
        )

        analytics = build_run_analytics(execution)

        assert analytics["reliability"]["excluded_not_comparable"] == 1
        (point,) = analytics["trends"]
        assert point["excluded_not_comparable"] == 1
        assert point["total"] == 2
