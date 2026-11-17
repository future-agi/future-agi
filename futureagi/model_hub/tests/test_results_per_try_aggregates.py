"""The Results read serves the per-try aggregates a column's Aggregate menu
offers when a run plays more than one try per row, and says how many tries a
row holds.

Each summary entry carries `reducers`: Most common (`mode`), Best (`max`),
`pass_at_k`, `pass_hat_k` and `at_least:<n>` for n from 1 to the row's tries.
Each row's tries become one value and the run's number is the mean over the
rows in view, with its 95% interval:

- Most common: the most common verdict of the tries the evaluation scored
  (a tie is a fail).
- Best: the highest assessed value of the row's tries.
- pass@k: at least one try passed. pass^k: every try passed; a row with a try
  that did not count is left out. At least n: n or more tries passed.
- A try that errored or whose infrastructure failed did not pass: pass@k,
  pass^k and at least n count it as a failed try. Most common and Best read
  the scored tries only. A try still queued, or one the evaluation did not
  score, is left out of all of them.
- An evaluation with no pass/fail rule gives no verdict: only Best has a value.
- On a pass/fail evaluation, an assessed value other than 0 or 1 is no
  pass/fail evidence: that try is unknown for every one of them, and the read
  still answers.
- pass^k reads each row against its own planned tries, so a row planned with
  fewer tries than the run's most is read on those.

Each metric in the summary carries `trials_k`, the most tries a row of a run
in view holds, and an external run's `trials` is its plan's tries per example.
The query accepts every aggregate the menu can name.
"""

import sys
import uuid

import pytest
from rest_framework.exceptions import ValidationError

from model_hub.serializers.dataset_results import DatasetResultsQuerySerializer
from model_hub.services import results_grid
from model_hub.tests import test_results_launch_reads as reads
from model_hub.tests.test_results_trial_spread_and_movement import (
    DECLARED,
    KEY,
    PASS_FAIL,
    UNDECLARED,
    tried,
)

pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")


def rows_of(tries, binding):
    """Rows as the read builds them, one run ("run"), per row its tries: a
    value, "error", "infra", "queued" or "not_assessed"."""
    return [
        {
            "example_id": eid,
            "results": {"run": {"trials_detail": [tried(v, binding) for v in row]}},
        }
        for eid, row in tries.items()
    ]


def aggregates(tries, binding=PASS_FAIL, k=3):
    return results_grid.per_try_aggregates(
        rows_of(tries, binding), "run", KEY, binding, k
    )


def figures(entry):
    return entry["value"], entry["n"]


# A pass/fail evaluation, three tries per row.
VERDICTS = {
    "a": (1, 1, 1),  # every try passes
    "b": (1, 0, 1),  # two of three pass
    "c": (1, "error", "infra"),  # one scored try; two that did not pass
    "d": ("error", "error", "infra"),  # no scored try; three that did not pass
    "e": (0, 1, "queued"),  # an even split of two scored tries; one still queued
    "f": (0, "not_assessed", 0),  # two scored fails; one the evaluation did not score
    "g": ("queued", "queued", "queued"),  # nothing counts
}


def test_most_common_reads_the_scored_tries_and_a_tie_is_a_fail():
    # a, b, c pass; e is a tie; f fails; d and g have no scored try.
    assert figures(aggregates(VERDICTS)["mode"]) == (pytest.approx(3 / 5), 5)


def test_best_is_the_highest_scored_try_of_each_row():
    # a, b, c and e have a passing try; f has none; d and g have no scored try.
    assert figures(aggregates(VERDICTS)["max"]) == (pytest.approx(4 / 5), 5)


def test_pass_at_k_counts_a_try_that_errored_or_failed_on_infrastructure_as_a_fail():
    # a, b, c, e pass at least once; d (no scored try, three failed tries) and
    # f do not; g has no try that counts.
    assert figures(aggregates(VERDICTS)["pass_at_k"]) == (pytest.approx(4 / 6), 6)


def test_pass_hat_k_needs_every_try_to_pass_and_leaves_out_rows_with_a_try_that_did_not_count():
    # a passes every try; b, c and d do not; e, f and g have a try that did not count.
    result = aggregates(VERDICTS)["pass_hat_k"]
    assert figures(result) == (pytest.approx(1 / 4), 4)
    assert result["ci95"] == pytest.approx([0.045586, 0.699364], abs=1e-6)


def test_at_least_n_counts_the_passing_tries_of_each_row():
    result = aggregates(VERDICTS)
    assert figures(result["at_least:1"]) == (pytest.approx(4 / 6), 6)
    # a passes three tries and b two; c, d, e and f pass at most one.
    assert figures(result["at_least:2"]) == (pytest.approx(2 / 6), 6)
    assert figures(result["at_least:3"]) == (pytest.approx(1 / 6), 6)
    assert "at_least:4" not in result


def test_a_share_carries_its_wilson_interval():
    result = aggregates(VERDICTS)
    assert result["pass_at_k"]["ci95"] == pytest.approx([0.299988, 0.903231], abs=1e-6)
    assert result["mode"]["ci95"] == pytest.approx([0.230720, 0.882382], abs=1e-6)
    # Best of a pass/fail evaluation is a share too: 4 of 5 rows.
    assert result["max"]["ci95"] == pytest.approx([0.375528, 0.963777], abs=1e-6)


def test_a_score_reads_each_try_by_the_runs_pass_mark():
    # Pass mark 0.5, higher is better.
    result = aggregates(
        {
            "a": (0.9, 0.2, 0.6),  # pass, fail, pass
            "b": (0.4, "error", 0.1),  # fail, a try that did not pass, fail
            "c": (0.7, 0.8, 0.5),  # every try passes; the mark itself passes
        },
        DECLARED,
    )
    assert figures(result["mode"]) == (pytest.approx(2 / 3), 3)
    assert figures(result["pass_at_k"]) == (pytest.approx(2 / 3), 3)
    assert figures(result["pass_hat_k"]) == (pytest.approx(1 / 3), 3)
    assert figures(result["at_least:2"]) == (pytest.approx(2 / 3), 3)
    # Best: 0.9, 0.4 and 0.8, with the interval of a mean.
    assert figures(result["max"]) == (pytest.approx(0.7), 3)
    assert result["max"]["ci95"] == pytest.approx([0.400605, 0.999395], abs=1e-6)


def test_an_evaluation_with_no_pass_rule_has_only_its_best_value():
    result = aggregates(
        {"a": (0.3, "error", 0.25), "b": ("error", "infra", "error")}, UNDECLARED
    )
    assert figures(result["max"]) == (pytest.approx(0.3), 1)
    for name in (
        "mode",
        "pass_at_k",
        "pass_hat_k",
        "at_least:1",
        "at_least:2",
        "at_least:3",
    ):
        assert result[name] == {"value": None, "n": 0, "ci95": None}, name


def test_with_no_row_that_counts_every_aggregate_has_no_value():
    result = aggregates({"a": ("queued", "not_assessed", "queued")})
    for name, entry in result.items():
        assert entry == {"value": None, "n": 0, "ci95": None}, name


def test_at_least_n_is_served_from_1_to_the_rows_tries_up_to_the_query_bound():
    one = aggregates({"a": (1,)}, k=1)
    assert sorted(name for name in one if name.startswith("at_least:")) == [
        "at_least:1"
    ]
    many = aggregates({"a": (1, 0)}, k=results_grid.AT_LEAST_MAX + 50)
    named = {name for name in many if name.startswith("at_least:")}
    assert named == {f"at_least:{n}" for n in range(1, results_grid.AT_LEAST_MAX + 1)}


def test_a_rows_tries_are_counted_from_the_runs_plan():
    assert results_grid.tries_per_row({"a": [1, 2, 3], "b": [1, 2, 3]}) == 3
    assert results_grid.tries_per_row({"a": [1], "b": [1, 2]}) == 2
    assert results_grid.tries_per_row({}) == 1


UNKNOWN = {"value": None, "n": 0, "ci95": None}
# A pass/fail evaluation that also declares a direction and a pass mark: its
# pin calls an assessed 2 a pass.
PASS_FAIL_WITH_MARK = {**PASS_FAIL, "higher_is_better": True, "pass_threshold": 0.5}


def test_a_pass_fail_value_other_than_0_or_1_is_unknown_for_every_per_try_aggregate():
    # "a" holds an assessed 2 beside two passes; "b" passes every try.
    result = aggregates({"a": (2, 1, 1), "b": (1, 1, 1)})
    assert figures(result["mode"]) == (pytest.approx(1), 2)
    # Best reads the 0/1 tries only, so it stays a share with its interval.
    assert figures(result["max"]) == (pytest.approx(1), 2)
    assert result["max"]["ci95"] == pytest.approx([0.342372, 1.0], abs=1e-6)
    assert figures(result["pass_at_k"]) == (pytest.approx(1), 2)
    # The try holding 2 did not count, so pass^k reads "b" alone.
    assert figures(result["pass_hat_k"]) == (pytest.approx(1), 1)
    assert figures(result["at_least:2"]) == (pytest.approx(1), 2)
    assert figures(result["at_least:3"]) == (pytest.approx(1 / 2), 2)


@pytest.mark.parametrize(
    "binding",
    [PASS_FAIL, PASS_FAIL_WITH_MARK],
    ids=["pass_fail", "pass_fail_with_mark"],
)
def test_rows_whose_pass_fail_tries_hold_no_0_or_1_have_no_per_try_aggregate(binding):
    result = aggregates({"a": (2, 2, 2), "b": (0.5, 2, -1)}, binding)
    for name, entry in result.items():
        assert entry == UNKNOWN, name


def test_the_intervals_have_no_value_outside_their_range_and_never_raise():
    assert results_grid.wilson_interval(2, 1) is None
    assert results_grid.wilson_interval(-0.5, 3) is None
    assert results_grid.wilson_interval(1, 2) == pytest.approx(
        [0.342372, 1.0], abs=1e-6
    )
    assert results_grid.mean_interval([1e200, -1e200]) is None
    # Best of a score whose rows are too far apart for an interval keeps its value.
    result = aggregates({"a": (1e200,), "b": (-1e200,)}, UNDECLARED, k=1)
    assert figures(result["max"]) == (pytest.approx(0.0), 2)
    assert result["max"]["ci95"] is None


def test_best_of_rows_whose_tries_span_the_float_range_keeps_a_finite_value():
    # Each row's tries average 0, while each row's best try is the largest
    # value: their mean must not pass the largest float.
    result = aggregates({"a": (-1e308, 1e308), "b": (-1e308, 1e308)}, UNDECLARED, k=2)
    assert figures(result["max"]) == (1e308, 2)
    assert result["max"]["ci95"] == [1e308, 1e308]


LARGEST = sys.float_info.max


@pytest.mark.parametrize(
    "values,expected",
    [
        pytest.param([LARGEST] * 3, LARGEST, id="three-largest"),
        pytest.param([-LARGEST] * 3, -LARGEST, id="three-most-negative"),
        pytest.param([LARGEST] * 7, LARGEST, id="seven-largest"),
        pytest.param([LARGEST, LARGEST, -LARGEST], LARGEST / 3, id="mixed-signs"),
    ],
)
def test_the_mean_of_values_whose_sum_passes_the_largest_float_is_finite(
    values, expected
):
    assert results_grid.mean(values) == pytest.approx(expected, rel=1e-15)


def test_pass_hat_k_reads_each_row_against_its_own_planned_tries():
    # "a" was planned two tries and "b" three, so the run plays three per row.
    result = aggregates({"a": (1, 0), "b": (1, 1, 1)}, k=3)
    assert figures(result["pass_hat_k"]) == (pytest.approx(1 / 2), 2)
    assert figures(result["pass_at_k"]) == (pytest.approx(1), 2)
    assert figures(result["at_least:2"]) == (pytest.approx(1 / 2), 2)
    assert figures(result["at_least:3"]) == (pytest.approx(1 / 2), 2)
    assert figures(result["mode"]) == (pytest.approx(1 / 2), 2)
    # A row whose own two tries both pass passes every try.
    both = aggregates({"a": (1, 1), "b": (1, 1, 1)}, k=3)
    assert figures(both["pass_hat_k"]) == (pytest.approx(1), 2)


# The query's shape.
def validated(**data):
    serializer = DatasetResultsQuerySerializer(data={"runs": str(uuid.uuid4()), **data})
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


@pytest.mark.parametrize(
    "agg",
    [
        "mode",
        "max",
        "pass_at_k",
        "pass_hat_k",
        "at_least:1",
        "at_least:2",
        "at_least:100",
    ],
)
def test_the_query_accepts_each_per_try_aggregate(agg):
    assert validated(agg=agg)["agg"] == agg


def test_the_query_names_at_least_n_from_1_to_100_and_nothing_else():
    assert [validated(agg=f"at_least:{n}")["agg"] for n in (1, 100)] == [
        "at_least:1",
        "at_least:100",
    ]
    for agg in (
        "at_least:0",
        "at_least:101",
        "at_least:-1",
        "at_least:",
        "at_least",
        "at_least:two",
        "at_least:2.5",
        "at_least: 2",
        "pass@k",
        "best",
    ):
        with pytest.raises(ValidationError):
            validated(agg=agg)


# The read itself: an external run of three tries per row, a pass/fail
# evaluation. Per row and try: a verdict, "error" (the try errored) or
# "infra" (its infrastructure failed).
TRIED = [
    (1, 1, 1),
    (1, 1, "error"),
    ("infra", 0, 1),
    (0, 0, 0),
]


def tried_external_run(organization, workspace, user):
    dataset, version, examples = reads.seed(organization, workspace, user, len(TRIED))
    run, binding = reads.pinned_run(
        version, examples, user, "pass_fail", True, trials=3
    )
    for example, row in zip(examples, TRIED, strict=True):
        for number, value in enumerate(row, start=1):
            if value in ("error", "infra"):
                kind = "infra" if value == "infra" else "task_error"
                reads.record(
                    run, example, trial=number, status="failed", error={"kind": kind}
                )
            else:
                reads.score_record(run, example, binding, value, trial=number)
    return dataset, run, str(binding.metric.id)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "agg", [None, "mean", "mode", "max", "pass_at_k", "pass_hat_k", "at_least:2"]
)
def test_the_read_serves_each_runs_per_try_aggregates_and_its_tries_per_row(
    auth_client, organization, workspace, user, agg
):
    dataset, run, metric = tried_external_run(organization, workspace, user)
    query = {"runs": str(run.id), "baseline": "none", **({"agg": agg} if agg else {})}
    summary = reads.result(reads.read(auth_client, dataset, **query))["summary"]
    (definition,) = [m for m in summary["metrics"] if m["key"] == metric]
    assert definition["trials_k"] == 3
    served = summary["aggregates"][metric][str(run.id)]["reducers"]
    # Most common: 1, 1, a tie, 0. Best: 1, 1, 1, 0.
    assert figures(served["mode"]) == (pytest.approx(2 / 4), 4)
    assert figures(served["max"]) == (pytest.approx(3 / 4), 4)
    # The errored and the infrastructure-failed tries did not pass.
    assert figures(served["pass_at_k"]) == (pytest.approx(3 / 4), 4)
    assert figures(served["pass_hat_k"]) == (pytest.approx(1 / 4), 4)
    assert figures(served["at_least:2"]) == (pytest.approx(2 / 4), 4)
    assert figures(served["at_least:3"]) == (pytest.approx(1 / 4), 4)
    assert "at_least:4" not in served
    # The run says it plays three tries per row, so the spread sorts turn on.
    assert summary["runs"][0]["trials"] == 3


@pytest.mark.django_db
def test_the_default_read_answers_when_each_rows_best_try_is_the_largest_value(
    auth_client, organization, workspace, user
):
    from model_hub.tests.test_results_trial_spread_and_movement import tried_run

    rows = [(-1e308, 1e308, None), (-1e308, 1e308, None)]
    dataset, _, run, metric = tried_run(organization, workspace, user, rows, "numeric")
    summary = reads.result(
        reads.read(auth_client, dataset, runs=str(run.id), baseline="none")
    )["summary"]
    entry = summary["aggregates"][metric][str(run.id)]
    assert entry["value"] == 0
    assert entry["reducers"]["max"]["value"] == 1e308


@pytest.mark.django_db
def test_the_default_read_answers_when_three_rows_best_try_is_the_largest_float(
    auth_client, organization, workspace, user
):
    from model_hub.tests.test_results_trial_spread_and_movement import tried_run

    rows = [(-LARGEST, LARGEST, None)] * 3
    dataset, _, run, metric = tried_run(organization, workspace, user, rows, "numeric")
    summary = reads.result(
        reads.read(auth_client, dataset, runs=str(run.id), baseline="none")
    )["summary"]
    entry = summary["aggregates"][metric][str(run.id)]
    assert entry["value"] == 0
    assert entry["reducers"]["max"]["value"] == LARGEST


@pytest.mark.django_db
def test_the_run_list_gives_an_external_run_its_plans_tries_per_example(
    auth_client, organization, workspace, user
):
    dataset, run, _ = tried_external_run(organization, workspace, user)
    # Settings an external run writes into its own metadata are not read.
    run.metadata["effective_settings"] = {"trials": 99, "concurrency": 99}
    run.save()
    listed = reads.result(reads.read(auth_client, dataset, "dataset-results-runs"))[
        "results"
    ]
    assert [(r["run_id"], r["trials"]) for r in listed] == [(str(run.id), 3)]
    # No settings are invented for it.
    assert listed[0]["concurrency"] is None
    assert listed[0]["effective_settings"] is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "shape",
    [{}, {"shape": "matrix"}, {"ids_only": True}],
    ids=["default", "matrix", "ids_only"],
)
def test_a_pass_fail_value_other_than_0_or_1_leaves_every_read_answering(
    auth_client, organization, workspace, user, shape
):
    # An external run of three tries per row on a pass/fail evaluation, every
    # try assessed 2.
    dataset, version, examples = reads.seed(organization, workspace, user, 2)
    run, binding = reads.pinned_run(
        version, examples, user, "pass_fail", True, trials=3
    )
    for example in examples:
        for number in (1, 2, 3):
            reads.score_record(run, example, binding, 2, trial=number)
    metric = str(binding.metric.id)
    query = {"runs": str(run.id), "baseline": "none", **shape}
    summary = reads.result(reads.read(auth_client, dataset, **query))["summary"]
    entry = summary["aggregates"][metric][str(run.id)]
    # The mean of the assessed values reads as it did.
    assert (entry["value"], entry["n"]) == (2, 2)
    assert set(entry["reducers"]) == {
        "mode",
        "max",
        "pass_at_k",
        "pass_hat_k",
        "at_least:1",
        "at_least:2",
        "at_least:3",
    }
    for name, served in entry["reducers"].items():
        assert served == UNKNOWN, name


def uneven_run(organization, workspace, user, tries):
    """An external run on a pass/fail evaluation whose rows were planned
    different numbers of tries: per row, its planned tries' verdicts."""
    from model_hub.models.experiments import ExperimentRun
    from model_hub.services.experiment_run_creation import create_experiment_run
    from model_hub.services.external_metrics import (
        ExternalMetricDefinition,
        create_or_reuse_external_metric,
    )

    dataset, version, examples = reads.seed(organization, workspace, user, len(tries))
    binding = create_or_reuse_external_metric(
        dataset=version.dataset,
        user=user,
        definition=ExternalMetricDefinition(
            name="fixture-uneven-plan",
            output_type="pass_fail",
            source_hash="b" * 64,
            higher_is_better=True,
        ),
    )
    run = create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=version,
        created_by=user,
        task_kind="external",
        evaluator_metrics=[binding.metric],
        expected_items=[
            (example.example_id, number)
            for example, row in zip(examples, tries, strict=True)
            for number in range(1, len(row) + 1)
        ],
    )
    ExperimentRun.no_workspace_objects.filter(pk=run.id).update(status="completed")
    run.refresh_from_db()
    for example, row in zip(examples, tries, strict=True):
        for number, value in enumerate(row, start=1):
            reads.score_record(run, example, binding, value, trial=number)
    return dataset, run, str(binding.metric.id)


@pytest.mark.django_db
def test_the_read_reads_pass_hat_k_of_each_row_against_its_own_planned_tries(
    auth_client, organization, workspace, user
):
    # One row was planned two tries and the other three.
    dataset, run, metric = uneven_run(
        organization, workspace, user, [(1, 0), (1, 1, 1)]
    )
    query = {"runs": str(run.id), "baseline": "none"}
    summary = reads.result(reads.read(auth_client, dataset, **query))["summary"]
    (definition,) = [m for m in summary["metrics"] if m["key"] == metric]
    assert definition["trials_k"] == 3
    served = summary["aggregates"][metric][str(run.id)]["reducers"]
    assert figures(served["pass_hat_k"]) == (pytest.approx(1 / 2), 2)
    assert figures(served["pass_at_k"]) == (pytest.approx(1), 2)
    assert figures(served["at_least:3"]) == (pytest.approx(1 / 2), 2)
    assert summary["runs"][0]["trials"] == 3
