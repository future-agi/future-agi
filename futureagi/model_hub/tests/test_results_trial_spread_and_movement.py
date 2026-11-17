"""The Results read orders rows by how far apart a run's tries are, shows the
rows a compared run got worse, better or changed on, and serves each run's
median.

The Sort menu asks for `variance:<metric>:<run>` (most variable across
trials: the population variance of the metric's assessed values on the run's
tries) and `flakiness:<run>` (most flaky: 1 - |2p - 1|, p being the share of
the run's judged tries that pass by the matrix's own rule). Both put the
largest first; a row with fewer than 2 such tries comes last, and equal
values keep their order.

The toolbar asks for `show=regressed|improved|changed` (Worse, Better,
Changed): rows where an evaluation of a compared run moved from the baseline,
trials averaged first, as the Worse-than-baseline-first sort reads them. The
summary counts each over the rows the filters and search keep.

A column's Aggregate menu asks for `agg=median`; every summary carries each
run's median beside its mean.
"""

import json
import uuid
from types import SimpleNamespace

import pytest

from model_hub.serializers.dataset_results import DatasetResultsQuerySerializer
from model_hub.services.results_catalog import ResultsReadError
from model_hub.services.results_root_causes import pinned_score_passes
from model_hub.services.results_selection import filter_rows
from model_hub.tests import test_results_launch_reads as reads
from model_hub.tests.test_results_delta_sort import BASE_VALUES, scored_pair

pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")

NO_METRIC = "The sort names a metric these runs do not score"
NOT_SELECTED = "The sort names a run that is not in this selection"
NO_COMPARISON = "The show needs a baseline and a run to compare with it"
SHOWS = ("regressed", "improved", "changed")


def ids(examples, order):
    return [str(examples[i].example_id) for i in order]


def ordered(client, dataset, **query):
    return reads.result(reads.read(client, dataset, **query, ids_only=True))[
        "example_ids"
    ]


def tried_run(organization, workspace, user, tries, output_type):
    """One run of three tries per row. Per row and try: a value, "error" for a
    try that failed to run, or None for a try with no record."""
    dataset, version, examples = reads.seed(organization, workspace, user, len(tries))
    run, binding = reads.pinned_run(
        version, examples, user, output_type, True, trials=3
    )
    for example, row in zip(examples, tries, strict=True):
        for number, value in enumerate(row, start=1):
            if value == "error":
                reads.record(
                    run,
                    example,
                    trial=number,
                    status="failed",
                    error={"kind": "task_error"},
                )
            elif value is not None:
                reads.score_record(run, example, binding, value, trial=number)
    return dataset, examples, run, str(binding.metric.id)


# Per row, a pass/fail evaluation's three tries.
VERDICTS = [
    (1, 1, 1),  # every try passes: 0
    (1, 0, 1),  # two of three pass: 2/3
    (1, 0, "error"),  # an even split of the two judged tries: 1
    (0, 1, 0),  # one of three passes: 2/3, equal to the second row
    (1, "error", None),  # one judged try: unknown
    (0, 0, 0),  # every try fails: 0
]
FLAKY_FIRST = [2, 1, 3, 0, 5, 4]

# Per row, a score's three tries.
SCORES = [
    (0.5, 0.5, 0.5),  # 0
    (0, 1, 0.5),  # 1/6
    (0, 1, "error"),  # 1/4 over the two assessed tries
    (0.25, 0.75, 0.5),  # 1/24
    (1, None, None),  # one assessed try: unknown
    (1, 0, 0.5),  # 1/6, equal to the second row
]
VARIABLE_FIRST = [2, 1, 5, 3, 0, 4]


@pytest.mark.django_db
def test_most_flaky_puts_the_most_evenly_split_rows_first(
    auth_client, organization, workspace, user
):
    dataset, examples, run, _ = tried_run(
        organization, workspace, user, VERDICTS, "pass_fail"
    )
    query = {"runs": str(run.id), "baseline": "none", "sort": f"flakiness:{run.id}"}
    assert ordered(auth_client, dataset, **query) == ids(examples, FLAKY_FIRST)
    rows = reads.result(reads.read(auth_client, dataset, **query, shape="matrix"))[
        "rows"
    ]
    assert [row["row_id"] for row in rows] == ids(examples, FLAKY_FIRST)


@pytest.mark.django_db
def test_most_variable_puts_the_rows_whose_tries_spread_most_first(
    auth_client, organization, workspace, user
):
    dataset, examples, run, metric = tried_run(
        organization, workspace, user, SCORES, "numeric"
    )
    query = {
        "runs": str(run.id),
        "baseline": "none",
        "sort": f"variance:{metric}:{run.id}",
    }
    assert ordered(auth_client, dataset, **query) == ids(examples, VARIABLE_FIRST)
    rows = reads.result(reads.read(auth_client, dataset, **query))["rows"]
    assert [row["example_id"] for row in rows] == ids(examples, VARIABLE_FIRST)


@pytest.mark.django_db
def test_trial_spread_sorts_refuse_what_the_selection_does_not_hold(
    auth_client, organization, workspace, user
):
    dataset, _, run, metric = tried_run(
        organization, workspace, user, SCORES, "numeric"
    )
    query = {"runs": str(run.id), "baseline": "none"}
    elsewhere = uuid.uuid4()
    refused = {
        f"variance:{uuid.uuid4()}:{run.id}": NO_METRIC,
        f"variance:latency:{run.id}": NO_METRIC,
        f"variance:{metric}:{elsewhere}": NOT_SELECTED,
        f"flakiness:{elsewhere}": NOT_SELECTED,
    }
    for sort, text in refused.items():
        response = reads.read(auth_client, dataset, **query, sort=sort)
        assert response.status_code == 400, (sort, response.content)
        assert text in response.content.decode(), sort


# BASE_VALUES -> RUN_VALUES: +0.25, +0.5, -0.5, -0.25, +0.25, unknown, unknown.
WORSE = [2, 3]
BETTER = [0, 1, 4]
CHANGED = [0, 1, 2, 3, 4]


@pytest.mark.django_db
def test_worse_better_and_changed_show_the_rows_a_compared_run_moved_on(
    auth_client, organization, workspace, user
):
    dataset, examples, base, run, _ = scored_pair(organization, workspace, user)
    query = {"runs": f"{base.id},{run.id}", "baseline": str(base.id)}
    for show, rows in zip(SHOWS, (WORSE, BETTER, CHANGED), strict=True):
        assert ordered(auth_client, dataset, **query, show=show) == ids(
            examples, rows
        ), show
        body = reads.result(reads.read(auth_client, dataset, **query, show=show))
        assert body["total"] == len(rows)
        assert body["summary"]["show_counts"] == {
            "regressed": 2,
            "improved": 3,
            "changed": 5,
        }
    every = reads.result(reads.read(auth_client, dataset, **query))
    assert every["total"] == len(BASE_VALUES)
    assert every["summary"]["show_counts"] == {
        "regressed": 2,
        "improved": 3,
        "changed": 5,
    }


@pytest.mark.django_db
def test_the_show_counts_follow_the_filters_and_the_search(
    auth_client, organization, workspace, user
):
    dataset, examples, base, run, _ = scored_pair(organization, workspace, user)
    query = {"runs": f"{base.id},{run.id}", "baseline": str(base.id)}
    first_three = json.dumps([{"type": "rows", "ids": ids(examples, [0, 1, 2])}])
    body = reads.result(reads.read(auth_client, dataset, **query, filters=first_three))
    assert body["summary"]["show_counts"] == {
        "regressed": 1,
        "improved": 2,
        "changed": 3,
    }
    question = examples[3].input["question"]
    body = reads.result(
        reads.read(auth_client, dataset, **query, search=question, show="regressed")
    )
    assert body["summary"]["show_counts"] == {
        "regressed": 1,
        "improved": 0,
        "changed": 1,
    }
    assert [row["example_id"] for row in body["rows"]] == ids(examples, [3])


@pytest.mark.django_db
def test_worse_reads_the_evaluations_and_a_slower_row_alone_is_not_worse(
    auth_client, organization, workspace, user
):
    dataset, version, examples = reads.seed(organization, workspace, user, 2)
    base, binding = reads.pinned_run(version, examples, user, "numeric", True)
    run, _ = reads.pinned_run(version, examples, user, "numeric", True)
    # Row 0 scores higher; row 1 scores the same and takes four times as long.
    for example, (base_score, run_score, base_ms, run_ms) in zip(
        examples, [(0.25, 0.75, 10, 10), (0.5, 0.5, 10, 40)], strict=True
    ):
        reads.score_record(base, example, binding, base_score, latency_ms=base_ms)
        reads.score_record(run, example, binding, run_score, latency_ms=run_ms)
    query = {"runs": f"{base.id},{run.id}", "baseline": str(base.id)}
    assert ordered(auth_client, dataset, **query, show="regressed") == []
    assert ordered(auth_client, dataset, **query, show="improved") == ids(examples, [0])
    assert ordered(auth_client, dataset, **query, show="changed") == ids(examples, [0])
    # The Worse-than-baseline-first sort also reads latency, as before.
    assert ordered(auth_client, dataset, **query, sort="regressed") == ids(
        examples, [1, 0]
    )


@pytest.mark.django_db
def test_worse_better_and_changed_need_a_baseline_and_a_run_to_compare(
    auth_client, organization, workspace, user
):
    dataset, _, base, run, _ = scored_pair(organization, workspace, user)
    for query in (
        {"runs": f"{base.id},{run.id}", "baseline": "none"},
        {"runs": str(base.id), "baseline": str(base.id)},
    ):
        for show in SHOWS:
            response = reads.read(auth_client, dataset, **query, show=show)
            assert response.status_code == 400, (query, show, response.content)
            assert NO_COMPARISON in response.content.decode()
        body = reads.result(reads.read(auth_client, dataset, **query))
        assert body["summary"]["show_counts"] is None


@pytest.mark.django_db
def test_each_runs_summary_carries_the_median_of_its_row_values(
    auth_client, organization, workspace, user
):
    dataset, _, base, run, metric = scored_pair(
        organization, workspace, user, [0.25, 1, 0.5, 0], [0.75, 0.25, None, 1]
    )
    query = {"runs": f"{base.id},{run.id}", "baseline": str(base.id)}
    for extra in ({}, {"agg": "median"}, {"agg": "mean"}):
        aggregates = reads.result(reads.read(auth_client, dataset, **query, **extra))[
            "summary"
        ]["aggregates"][metric]
        # 0, 0.25, 0.5, 1: the middle two; 0.25, 0.75, 1: the middle one.
        assert aggregates[str(base.id)]["median"] == 0.375, extra
        assert aggregates[str(run.id)]["median"] == 0.75, extra
        assert aggregates[str(base.id)]["value"] == 0.4375, extra


# The ordering and the shows alone, on rows as the read builds them; no database.
PASS_FAIL = {"evaluator_version_id": "version", "output_type_normalized": "pass_fail"}
DECLARED = {
    "evaluator_version_id": "version",
    "output_type_normalized": "numeric",
    "pass_threshold": 0.5,
    "higher_is_better": True,
}
UNDECLARED = {
    "evaluator_version_id": "version",
    "output_type_normalized": "numeric",
    "pass_threshold": 0.5,
}
KEY = "eval:quality"


def tried(value, binding):
    """One try as the grid records it: a value (its verdict by the pin, as
    the grid reads it), "error", "infra", "queued" or "not_assessed" (a
    completed try the evaluation did not score)."""
    unscored = {KEY: {"value": None, "passed": None, "status": "not_assessed"}}
    if value in ("error", "infra", "queued"):
        return {"status": value, "scores": unscored}
    if value == "not_assessed":
        return {"status": "completed", "scores": unscored}
    return {
        "status": "completed",
        "scores": {
            KEY: {
                "value": value,
                "passed": pinned_score_passes(value, binding),
                "status": "assessed",
            }
        },
    }


def tries_selection(tries, binding=PASS_FAIL):
    rows = [
        {
            "example_id": eid,
            "results": {"run": {"trials_detail": [tried(v, binding) for v in row]}},
        }
        for eid, row in tries.items()
    ]
    return SimpleNamespace(
        rows=rows,
        runs=[SimpleNamespace(id="run")],
        baseline_run_id=None,
        plans={"run": [(eid, 1) for eid in tries]},
        bindings={"run": {KEY: binding}},
        items={},
        dataset=None,
    )


def sorted_ids(selection, **query):
    return [row["example_id"] for row in filter_rows(selection, query)]


def test_most_flaky_leaves_out_tries_that_failed_to_run_or_were_not_judged():
    selection = tries_selection(
        {
            "a": (1, 1, 0, 0),  # 2 of 4: 1
            "b": (1, "error", "infra", "queued"),  # one judged try: unknown
            "c": (1, 1, 1, 0),  # 3 of 4: 1/2
            "d": (0, "not_assessed", 1),  # 1 of 2: 1, equal to "a"
            "e": (0, 0, 0),  # 0
            "f": (),  # no try: unknown
            "g": (1, 0, 0, 0),  # 1 of 4: 1/2, equal to "c"
        }
    )
    assert sorted_ids(selection, sort="flakiness:run") == [
        "a",
        "d",
        "c",
        "g",
        "e",
        "b",
        "f",
    ]


def test_most_flaky_reads_a_try_by_the_runs_pass_rule():
    # A score whose pin has a direction and a pass mark passes at or above it.
    tries = {"a": (0.9, 0.6), "b": (0.9, 0.1), "c": (0.1, 0.4)}
    assert sorted_ids(tries_selection(tries, DECLARED), sort="flakiness:run") == [
        "b",
        "a",
        "c",
    ]
    # Without a direction no try passes or fails, so every row keeps its place.
    assert sorted_ids(tries_selection(tries, UNDECLARED), sort="flakiness:run") == [
        "a",
        "b",
        "c",
    ]


def test_most_variable_reads_the_metrics_assessed_tries():
    selection = tries_selection(
        {
            "a": (0.5, 0.5),  # 0
            "b": (0, 1, "error"),  # 1/4 over the two assessed tries
            "c": (0.25, 0.75),  # 1/16
            "d": (1, "not_assessed", "infra"),  # one assessed try: unknown
            "e": (1, 0),  # 1/4, equal to "b"
        },
        DECLARED,
    )
    assert sorted_ids(selection, sort=f"variance:{KEY}:run") == [
        "b",
        "e",
        "c",
        "a",
        "d",
    ]


@pytest.mark.parametrize(
    "sort,text",
    [
        pytest.param(
            "variance:eval:other:run", NO_METRIC, id="a-metric-the-run-does-not-score"
        ),
        pytest.param(
            f"variance:{KEY}:elsewhere",
            NOT_SELECTED,
            id="a-variance-of-a-run-outside-the-selection",
        ),
        pytest.param(
            "flakiness:elsewhere",
            NOT_SELECTED,
            id="a-flakiness-of-a-run-outside-the-selection",
        ),
        pytest.param(
            "variance:run", "Unsupported sort", id="a-variance-with-no-metric"
        ),
        pytest.param(
            "variance:", "Unsupported sort", id="a-variance-with-nothing-named"
        ),
        pytest.param(
            f"variance:{KEY}:", "Unsupported sort", id="a-variance-with-no-run"
        ),
        pytest.param("flakiness:", "Unsupported sort", id="a-flakiness-with-no-run"),
        pytest.param(
            f"flakiness:{KEY}:run", "Unsupported sort", id="a-flakiness-with-a-metric"
        ),
    ],
)
def test_the_selection_refuses_a_trial_spread_sort_it_cannot_answer(sort, text):
    with pytest.raises(ResultsReadError) as error:
        filter_rows(tries_selection({"a": (1, 0)}, DECLARED), {"sort": sort})
    assert error.value.status == 400 and str(error.value) == text


# Per row, the baseline's and the compared run's value. Run minus baseline:
# +0.25, -0.5, +0.04 (within the 0.05 a score must move), unknown, -0.5, +0.5.
PAIRS = {
    "a": (0.5, 0.75),
    "b": (1, 0.5),
    "c": (0.25, 0.29),
    "d": (None, 1),
    "e": (0.75, 0.25),
    "f": (0, 0.5),
}


def compared_selection(binding=DECLARED, pairs=PAIRS, baseline="base"):
    def cell(value):
        return {"scores": {KEY: {"value": value}}, "cost_usd": None, "latency_ms": None}

    rows = [
        {"example_id": eid, "results": {"base": cell(base), "run": cell(run)}}
        for eid, (base, run) in pairs.items()
    ]
    return SimpleNamespace(
        rows=rows,
        runs=[SimpleNamespace(id="base"), SimpleNamespace(id="run")],
        baseline_run_id=baseline,
        plans={"base": [(eid, 1) for eid in pairs]},
        bindings={"base": {KEY: binding}, "run": {KEY: binding}},
        items={},
        dataset=None,
    )


def test_worse_better_and_changed_read_each_evaluations_direction_and_threshold():
    expected = {
        "regressed": ["b", "e"],
        "improved": ["a", "f"],
        "changed": ["a", "b", "e", "f"],
    }
    for show, rows in expected.items():
        selection = compared_selection()
        assert sorted_ids(selection, show=show) == rows, show
        assert selection.show_counts == {"regressed": 2, "improved": 2, "changed": 4}
    # The Worse-than-baseline-first sort groups the same rows, as before.
    assert sorted_ids(compared_selection(), sort="regressed") == [
        "b",
        "e",
        "a",
        "f",
        "c",
        "d",
    ]


def test_a_lower_is_better_evaluation_flips_worse_and_better():
    binding = {
        **UNDECLARED,
        "config_snapshot": {"external": {"higher_is_better": False}},
    }
    assert sorted_ids(compared_selection(binding), show="regressed") == ["a", "f"]
    assert sorted_ids(compared_selection(binding), show="improved") == ["b", "e"]


def test_an_evaluation_with_no_direction_changes_rows_but_makes_none_worse_or_better():
    for show, rows in {
        "regressed": [],
        "improved": [],
        "changed": ["a", "b", "e", "f"],
    }.items():
        assert sorted_ids(compared_selection(UNDECLARED), show=show) == rows, show


def test_any_move_of_a_pass_fail_evaluation_changes_a_row():
    # Run minus baseline: none, -0.5, +0.02 (a move a score would not make), none.
    pairs = {"a": (1, 1), "b": (1, 0.5), "c": (0.5, 0.52), "d": (0, 0)}
    selection = compared_selection(PASS_FAIL, pairs)
    assert sorted_ids(selection, show="changed") == ["b", "c"]
    assert selection.show_counts == {"regressed": 1, "improved": 1, "changed": 2}


def test_without_a_baseline_the_shows_are_refused_and_nothing_is_counted():
    with pytest.raises(ResultsReadError) as error:
        filter_rows(compared_selection(baseline=None), {"show": "regressed"})
    assert error.value.status == 400 and str(error.value) == NO_COMPARISON
    selection = compared_selection(baseline=None)
    assert sorted_ids(selection) == list(PAIRS)
    assert selection.show_counts is None


def test_the_matrix_and_a_baseline_outside_the_selected_runs_carry_no_show_counts():
    selection = compared_selection()
    assert sorted_ids(selection, show="regressed", shape="matrix") == ["b", "e"]
    assert selection.show_counts is None
    # The baseline was not one of the runs asked for: Worse still answers,
    # and nothing is counted.
    selection = compared_selection()
    selection.runs = [SimpleNamespace(id="run")]
    assert sorted_ids(selection, show="regressed") == ["b", "e"]
    assert selection.show_counts is None


# The query's shape.
RUN = "0B5B7B9E-1111-4C8F-9A8E-0E2F8D1D2C3A"


def validated(**data):
    serializer = DatasetResultsQuerySerializer(data={"runs": str(uuid.uuid4()), **data})
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


@pytest.mark.parametrize(
    "metric",
    [
        pytest.param("9d4c3f85-5555-4a1b-9ecd-4c6dcd5b6a7e", id="an-evaluation-id"),
        pytest.param("eval:quality", id="a-key-with-colons"),
    ],
)
def test_the_query_accepts_a_variance_sort_and_reads_the_run_as_an_id(metric):
    assert (
        validated(sort=f"variance:{metric}:{RUN}")["sort"]
        == f"variance:{metric}:{RUN.lower()}"
    )


def test_the_query_accepts_a_flakiness_sort_and_reads_the_run_as_an_id():
    assert validated(sort=f"flakiness:{RUN}")["sort"] == f"flakiness:{RUN.lower()}"


@pytest.mark.parametrize("show", SHOWS)
def test_the_query_accepts_worse_better_and_changed(show):
    assert validated(show=show)["show"] == show


def test_the_query_accepts_the_median():
    assert validated(agg="median")["agg"] == "median"
    assert validated()["agg"] == "mean"
