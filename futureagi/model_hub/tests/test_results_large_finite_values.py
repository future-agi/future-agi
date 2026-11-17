"""The Results read answers on any finite score, however large.

Each run's summary carries the median of its row values beside their mean.
With an even count the median is halfway between the two middle values,
found without adding them when their sum would pass the largest float.

The Most variable across trials order ranks rows by how far apart one run's
tries are on a metric: their population standard deviation, which orders
rows as their population variance does, worked out so that no step passes
the largest float. Tries that all agree have no spread, however their mean
rounds, and the same tries in another order spread alike. Rows with fewer
than 2 assessed tries still come last.

On ordinary values the median answers exactly as before, and the order
ranks tries that vary as before.
"""

import statistics
import sys

import pytest

from model_hub.services.results_grid import median
from model_hub.services.results_selection import filter_rows
from model_hub.tests import test_results_launch_reads as reads
from model_hub.tests.test_results_delta_sort import scored_pair
from model_hub.tests.test_results_trial_spread_and_movement import (
    DECLARED,
    KEY,
    tried_run,
    tries_selection,
)

pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")

LARGEST = sys.float_info.max
# Four rows, one try each, in row order. The two middle values add up past
# the largest float; every running sum of their mean stays below it.
LARGE = [-1e308, 9e307, 9e307, 9e307]


def ids(examples, order):
    return [str(examples[i].example_id) for i in order]


def ordered(client, dataset, **query):
    return reads.result(reads.read(client, dataset, **query, ids_only=True))[
        "example_ids"
    ]


def spread_order(tries):
    selection = tries_selection(tries, DECLARED)
    return [
        row["example_id"]
        for row in filter_rows(selection, {"sort": f"variance:{KEY}:run"})
    ]


@pytest.mark.django_db
def test_the_read_answers_with_the_median_of_large_row_values(
    auth_client, organization, workspace, user
):
    dataset, _, base, run, metric = scored_pair(
        organization, workspace, user, LARGE, LARGE
    )
    query = {"runs": f"{base.id},{run.id}", "baseline": str(base.id)}
    for extra in ({}, {"agg": "median"}):
        aggregates = reads.result(reads.read(auth_client, dataset, **query, **extra))[
            "summary"
        ]["aggregates"][metric]
        for rid in (str(base.id), str(run.id)):
            assert aggregates[rid]["median"] == 9e307, extra
            assert aggregates[rid]["n"] == 4, extra


@pytest.mark.parametrize(
    "values,middle",
    [
        pytest.param(LARGE, 9e307, id="two-large-middle-values"),
        pytest.param([LARGEST, LARGEST], LARGEST, id="the-largest-float-twice"),
        pytest.param(
            [-LARGEST, -LARGEST, 0, 1], -LARGEST / 2, id="large-and-small-middle-values"
        ),
        pytest.param([-LARGEST, -LARGEST], -LARGEST, id="the-lowest-float-twice"),
        pytest.param([-LARGEST, LARGEST], 0, id="opposite-ends"),
    ],
)
def test_the_median_of_large_values_is_their_middle(values, middle):
    assert median(values) == middle


def test_the_median_of_two_middle_values_past_the_largest_float_is_between_them():
    assert median([1e308, 1.5e308]) == pytest.approx(1.25e308, rel=1e-15)


@pytest.mark.parametrize(
    "values,middle",
    [
        pytest.param([0, 0.25, 0.5, 1], 0.375, id="an-even-count"),
        pytest.param([0.25, 0.75, 1], 0.75, id="an-odd-count"),
        pytest.param([0.2, 0.1], (0.1 + 0.2) / 2, id="the-plain-sum-halved"),
        pytest.param([True, False], 0.5, id="verdicts"),
        pytest.param([10**308, 10**308], 1e308, id="large-whole-numbers"),
        pytest.param(
            [float("inf"), float("nan"), 1, "x", None], 1, id="finite-values-only"
        ),
        pytest.param([], None, id="no-value"),
    ],
)
def test_the_median_of_ordinary_values_is_as_before(values, middle):
    assert median(values) == middle


def test_most_variable_orders_tries_whose_squares_pass_the_largest_float():
    tries = {
        "a": (-1e200, 1e200),  # 1e200 from their mean
        "b": (1.5e308, 1.5e308),  # no spread, though their sum passes the largest float
        "c": (0, 1),  # 0.5
        "d": (-LARGEST, LARGEST),  # the largest spread there is
        "e": (1e308, 1e308, -1e308),  # about 9.4e307
        "f": (1,),  # one assessed try: unknown
    }
    assert spread_order(tries) == ["d", "e", "a", "c", "b", "f"]


def test_tries_that_agree_have_no_spread_however_large():
    tries = {"a": (0, 1), "b": (1.5e308, 1.5e308), "c": (0.25, 0.25)}
    # "b" and "c" do not spread at all, so they keep their order after "a".
    assert spread_order(tries) == ["a", "b", "c"]


# The scaled mean of three tries of this value rounds away from it.
ROUNDS_AWAY = float.fromhex("0x1.8000000000002p+1023")


@pytest.mark.parametrize(
    "agreeing",
    [
        pytest.param((ROUNDS_AWAY,) * 3, id="three-large-tries-whose-mean-rounds-away"),
        pytest.param((0.1,) * 3, id="three-ordinary-tries-whose-mean-rounds-away"),
        pytest.param((LARGEST,) * 3, id="the-largest-float-three-times"),
        pytest.param((-ROUNDS_AWAY,) * 2, id="two-large-negative-tries"),
    ],
)
def test_tries_that_agree_have_no_spread_where_their_mean_rounds(agreeing):
    tries = {
        "still": (0.5, 0.5),  # no spread
        "agree": agreeing,  # no spread either
        "wide": (-1e200, 1e200),  # 1e200 from their mean
        "narrow": (0, 1e-300),  # 5e-301 from their mean
    }
    # Any spread at all would put "agree" ahead of "still"; with none, the
    # two keep their order after the tries that vary.
    assert spread_order(tries) == ["wide", "narrow", "still", "agree"]


def test_tries_in_another_order_spread_alike():
    tries = {
        "a": (0.2, 0.4, 0.9),
        "b": (0.9, 0.2, 0.4),
        "c": (0.4, 0.9, 0.2),
        "d": (0.3, 0.7),
    }
    # The same tries in any order spread alike, so they keep their order.
    assert spread_order(tries) == ["a", "b", "c", "d"]


EXTREMES = [
    LARGEST,
    -LARGEST,
    1e308,
    -1e308,
    1e200,
    -1e200,
    sys.float_info.min,
    5e-324,
    -5e-324,
    0.0,
    1.0,
]


def test_most_variable_answers_on_any_finite_tries():
    tries = {
        f"{i}-{j}": (x, y)
        for i, x in enumerate(EXTREMES)
        for j, y in enumerate(EXTREMES)
    }
    tries.update(
        {
            f"{i}-{j}-{k}": (x, y, z)
            for i, x in enumerate(EXTREMES)
            for j, y in enumerate(EXTREMES)
            for k, z in enumerate(EXTREMES)
        }
    )
    order = spread_order(tries)
    assert sorted(order) == sorted(tries)
    # The widest pair there is, the lowest and the largest float, leads.
    assert order[0] == "0-1"
    assert order.index("0-1") < order.index("2-3") < order.index("4-5")


# Values on a quarter grid, two or four tries a row, so every step is exact and
# the plain population variance has no rounding to hide a tie or an order.
ORDINARY = {
    "a": (0.5, 0.5),  # 0
    "b": (0, 1),  # 16/64
    "c": (0.25, 0.75),  # 4/64
    "d": (0, 0, 1, 1),  # 16/64, equal to "b"
    "e": (0, 0.25, 0.5, 0.75),  # 5/64
    "f": (1, 0.75),  # 1/64
    "g": (0.75, 0.25),  # 4/64, equal to "c"
    "h": (0, 0.5, 1, 1),  # 11/64
    "i": (1,),  # one assessed try: unknown
}


def by_population_variance(tries):
    known = [eid for eid, values in tries.items() if len(values) >= 2]
    unknown = [eid for eid, values in tries.items() if len(values) < 2]
    return sorted(known, key=lambda eid: -statistics.pvariance(tries[eid])) + unknown


def test_most_variable_orders_ordinary_tries_as_their_variance_does():
    assert spread_order(ORDINARY) == ["b", "d", "h", "e", "c", "g", "f", "a", "i"]
    assert spread_order(ORDINARY) == by_population_variance(ORDINARY)


# Everyday scores whose spreads are far apart, and two equal spreads.
EVERYDAY = {
    "a": (0.2, 0.4, 0.9),  # about 0.29
    "b": (0.6, 0.65),  # 0.025
    "c": (1, 2),  # 0.5
    "d": (0.3, 0.7),  # about 0.2
    "e": (5, 6),  # 0.5, equal to "c"
    "f": (0.95, 0.05, 0.5),  # about 0.37
    "g": (0.5, 0.5),  # 0
}


def test_most_variable_orders_everyday_scores_as_before():
    assert spread_order(EVERYDAY) == ["c", "e", "f", "a", "d", "b", "g"]
    assert spread_order(EVERYDAY) == by_population_variance(EVERYDAY)


@pytest.mark.django_db
def test_the_read_orders_by_large_tries_and_answers(
    auth_client, organization, workspace, user
):
    rows = [
        (0.5, 0.5, 0.5),  # no spread
        (-1e200, 1e200, None),  # 1e200 from their mean
        (0, 1, None),  # 0.5
    ]
    dataset, examples, run, metric = tried_run(
        organization, workspace, user, rows, "numeric"
    )
    query = {
        "runs": str(run.id),
        "baseline": "none",
        "sort": f"variance:{metric}:{run.id}",
    }
    assert ordered(auth_client, dataset, **query) == ids(examples, [1, 2, 0])
    grid = reads.result(reads.read(auth_client, dataset, **query))["rows"]
    assert [row["example_id"] for row in grid] == ids(examples, [1, 2, 0])
