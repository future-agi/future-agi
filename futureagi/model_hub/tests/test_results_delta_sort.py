"""The Results read sorts by one run's values.

A column's menu asks for `delta:<metric>:<run>:asc` (biggest regression first)
and `...:desc` (biggest improvement first). The difference is the run's value
minus the baseline's, trials averaged first, signed by the metric's direction.
The Sort menu also asks for `score:<metric>:<run>:asc|desc` (the run's value,
lowest or highest first) and for a run's latency or spend, highest first.
Rows with an unknown value come last either way, and equal values keep their
order. The other sorts order as before.
"""
import uuid
from types import SimpleNamespace

import pytest
from rest_framework.exceptions import ValidationError

from model_hub.serializers.dataset_results import DatasetResultsQuerySerializer
from model_hub.services.results_catalog import ResultsReadError
from model_hub.services.results_selection import filter_rows
from model_hub.tests import test_results_launch_reads as reads

pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")

NO_METRIC = "The sort names a metric these runs do not score"
NO_RUN = "The sort names a run that is not compared with the baseline here"
NO_BASELINE = "The sort needs a baseline run to compare with"
NOT_SELECTED = "The sort names a run that is not in this selection"
# The sort kind, and the record field, of a run's spend.
SPEND = "cost"

# Per row: the baseline's value and the compared run's value (None = no result).
BASE_VALUES = [0.5, 0.25, 0.5, 1, 0.5, None, 0.5]
RUN_VALUES = [0.75, 0.75, 0, 0.75, 0.75, 0.5, None]
# Run minus baseline: +0.25, +0.5, -0.5, -0.25, +0.25, unknown, unknown.
# Rows 0 and 4 tie; rows 5 and 6 have an unknown side.
DROP_FIRST = [2, 3, 0, 4, 1, 5, 6]
GAIN_FIRST = [1, 0, 4, 3, 2, 5, 6]


def scored_pair(organization, workspace, user, base_values=BASE_VALUES,
                run_values=RUN_VALUES, output_type="numeric", higher=True):
    """A baseline and a compared run on one dataset version, scored by one metric."""
    dataset, version, examples = reads.seed(organization, workspace, user, len(base_values))
    base, binding = reads.pinned_run(version, examples, user, output_type, higher)
    run, _ = reads.pinned_run(version, examples, user, output_type, higher)
    for example, base_value, run_value in zip(examples, base_values, run_values, strict=True):
        if base_value is not None:
            reads.score_record(base, example, binding, base_value)
        if run_value is not None:
            reads.score_record(run, example, binding, run_value)
    return dataset, examples, base, run, str(binding.metric.id)


def ids(examples, order):
    return [str(examples[i].example_id) for i in order]


def ordered(client, dataset, **query):
    return reads.result(reads.read(client, dataset, **query, ids_only=True))["example_ids"]


@pytest.mark.django_db
@pytest.mark.parametrize("higher,asc,desc", [
    pytest.param(True, DROP_FIRST, GAIN_FIRST, id="higher-is-better"),
    pytest.param(False, GAIN_FIRST, DROP_FIRST, id="lower-is-better"),
])
def test_delta_sort_orders_rows_by_the_signed_difference(
        auth_client, organization, workspace, user, higher, asc, desc):
    dataset, examples, base, run, metric = scored_pair(
        organization, workspace, user, higher=higher)
    query = {"runs": f"{base.id},{run.id}", "baseline": str(base.id)}
    for direction, order in (("asc", asc), ("desc", desc)):
        sort = f"delta:{metric}:{run.id}:{direction}"
        assert ordered(auth_client, dataset, **query, sort=sort) == ids(examples, order)


@pytest.mark.django_db
def test_delta_sort_orders_every_read_shape_alike(auth_client, organization, workspace, user):
    dataset, examples, base, run, metric = scored_pair(organization, workspace, user)
    query = {"runs": f"{base.id},{run.id}", "baseline": str(base.id),
             "sort": f"delta:{metric}:{run.id}:asc"}
    expected = ids(examples, DROP_FIRST)
    for shape, collection, key in [({}, "rows", "example_id"),
            ({"shape": "matrix"}, "rows", "row_id"), ({"ids_only": True}, "example_ids", None)]:
        pages = [reads.result(reads.read(auth_client, dataset, **query, **shape,
                                         page=page, page_size=4)) for page in (0, 1)]
        rows = pages[0][collection] + pages[1][collection]
        assert ([row[key] for row in rows] if key else rows) == expected
        assert pages[0]["total"] == pages[1]["total"] == len(expected)
    full = reads.result(reads.read(auth_client, dataset, **query, full=True,
                                   example_id=expected[1]))
    assert full["rows"][0]["neighbors"] == {
        "prev": expected[0], "next": expected[2], "position": 2, "total": len(expected)}


@pytest.mark.django_db
def test_delta_sort_reads_a_baseline_that_is_not_a_selected_run(
        auth_client, organization, workspace, user):
    dataset, examples, base, run, metric = scored_pair(organization, workspace, user)
    query = {"runs": str(run.id), "baseline": str(base.id)}
    for direction, order in (("asc", DROP_FIRST), ("desc", GAIN_FIRST)):
        sort = f"delta:{metric}:{run.id}:{direction}"
        assert ordered(auth_client, dataset, **query, sort=sort) == ids(examples, order)


@pytest.mark.django_db
def test_delta_sort_on_a_pass_fail_metric_compares_the_averaged_pass_rates(
        auth_client, organization, workspace, user):
    dataset, version, examples = reads.seed(organization, workspace, user, 3)
    base, binding = reads.pinned_run(version, examples, user, trials=2)
    run, _ = reads.pinned_run(version, examples, user, trials=2)
    # Per row, each run's two tries: pass rates 0.5 -> 1, 1 -> 0.5 and 0 -> 1.
    tries = [((1, 0), (1, 1)), ((1, 1), (1, 0)), ((0, 0), (1, 1))]
    for example, (base_tries, run_tries) in zip(examples, tries, strict=True):
        for number, (base_value, run_value) in enumerate(zip(base_tries, run_tries, strict=True), start=1):
            reads.score_record(base, example, binding, base_value, trial=number)
            reads.score_record(run, example, binding, run_value, trial=number)
    query = {"runs": f"{base.id},{run.id}", "baseline": str(base.id)}
    sort = f"delta:{binding.metric.id}:{run.id}"
    assert ordered(auth_client, dataset, **query, sort=f"{sort}:asc") == ids(examples, [1, 0, 2])
    assert ordered(auth_client, dataset, **query, sort=f"{sort}:desc") == ids(examples, [2, 0, 1])


@pytest.mark.django_db
@pytest.mark.parametrize("case,text", [
    pytest.param("metric", NO_METRIC, id="a-metric-these-runs-do-not-score"),
    pytest.param("metric_key", NO_METRIC, id="a-metric-key-that-is-not-an-evaluation"),
    pytest.param("run", NO_RUN, id="a-run-outside-the-selection"),
    pytest.param("baseline_run", NO_RUN, id="the-baseline-itself"),
    pytest.param("no_baseline", NO_BASELINE, id="no-baseline"),
])
def test_delta_sort_refusals_say_what_is_wrong(
        auth_client, organization, workspace, user, case, text):
    dataset, _, base, run, metric = scored_pair(
        organization, workspace, user, [1, 0], [0, 1])
    query = {"runs": f"{base.id},{run.id}", "baseline": str(base.id)}
    named_metric, named_run = metric, str(run.id)
    if case == "metric":
        named_metric = str(uuid.uuid4())
    elif case == "metric_key":
        named_metric = "latency"
    elif case == "run":
        named_run = str(uuid.uuid4())
    elif case == "baseline_run":
        named_run = str(base.id)
    else:
        query["baseline"] = "none"
    for direction in ("asc", "desc"):
        response = reads.read(auth_client, dataset, **query,
                              sort=f"delta:{named_metric}:{named_run}:{direction}")
        assert response.status_code == 400, response.content
        assert text in response.content.decode()


@pytest.mark.django_db
def test_a_refused_delta_sort_is_refused_when_no_row_matches_the_filters(
        auth_client, organization, workspace, user):
    dataset, _, base, run, _ = scored_pair(organization, workspace, user, [1, 0], [0, 1])
    response = reads.read(
        auth_client, dataset, runs=f"{base.id},{run.id}", baseline=str(base.id),
        search="no row holds this text", sort=f"delta:{uuid.uuid4()}:{run.id}:asc")
    assert response.status_code == 400, response.content
    assert NO_METRIC in response.content.decode()


# Per row, the compared run's score, latency and spend (None = unknown).
MEASURES = [(0.5, 30, 0.25), (None, 10, 0.5), (1, None, 0.25), (0.25, 30, None), (0.5, 20, 0.75)]
BASE_SCORES = [1, 0.5, 0.25, 0.75, 0]


def measured_pair(organization, workspace, user):
    """Two runs scored by one metric; the second also has a latency and a spend."""
    dataset, version, examples = reads.seed(organization, workspace, user, len(MEASURES))
    base, binding = reads.pinned_run(version, examples, user, "numeric", True)
    run, _ = reads.pinned_run(version, examples, user, "numeric", True)
    for example, base_score, (score, latency, spend) in zip(examples, BASE_SCORES, MEASURES, strict=True):
        reads.score_record(base, example, binding, base_score)
        measured = {"latency_ms": latency, SPEND: spend}
        if score is None:
            reads.record(run, example, **measured)
        else:
            reads.score_record(run, example, binding, score, **measured)
    return dataset, examples, base, run, str(binding.metric.id)


@pytest.mark.django_db
def test_score_latency_and_spend_sorts_order_rows_by_the_named_runs_values(
        auth_client, organization, workspace, user):
    dataset, examples, base, run, metric = measured_pair(organization, workspace, user)
    # Lowest or highest score first; slowest first; largest spend first. Equal
    # values keep their order and the row with an unknown value is last.
    expected = {
        f"score:{metric}:{run.id}:asc": [3, 0, 4, 2, 1],
        f"score:{metric}:{run.id}:desc": [2, 0, 4, 3, 1],
        f"latency:{run.id}": [0, 3, 4, 1, 2],
        f"{SPEND}:{run.id}": [4, 1, 0, 2, 3],
        f"score:{metric}:{base.id}:asc": [4, 2, 1, 3, 0],
        f"score:{metric}:{base.id}:desc": [0, 3, 1, 2, 4],
    }
    # With no baseline, and with a baseline that is one of the selected runs.
    for baseline in ("none", str(base.id)):
        query = {"runs": f"{base.id},{run.id}", "baseline": baseline}
        for sort, order in expected.items():
            rows = ordered(auth_client, dataset, **query, sort=sort)
            assert rows == ids(examples, order), (baseline, sort)


@pytest.mark.django_db
def test_score_latency_and_spend_sorts_refuse_what_the_selection_does_not_hold(
        auth_client, organization, workspace, user):
    dataset, _, base, run, metric = measured_pair(organization, workspace, user)
    # The baseline is not one of the selected runs here.
    query = {"runs": str(run.id), "baseline": str(base.id)}
    elsewhere = uuid.uuid4()
    refused = {
        f"score:{uuid.uuid4()}:{run.id}:asc": NO_METRIC,
        f"score:latency:{run.id}:desc": NO_METRIC,
        f"score:{metric}:{elsewhere}:asc": NOT_SELECTED,
        f"latency:{elsewhere}": NOT_SELECTED,
        f"{SPEND}:{elsewhere}": NOT_SELECTED,
        f"score:{metric}:{base.id}:asc": NOT_SELECTED,
        f"latency:{base.id}": NOT_SELECTED,
    }
    for sort, text in refused.items():
        response = reads.read(auth_client, dataset, **query, sort=sort)
        assert response.status_code == 400, (sort, response.content)
        assert text in response.content.decode(), sort


@pytest.mark.django_db
def test_the_other_sorts_order_as_before(auth_client, organization, workspace, user):
    dataset, examples, base, run, _ = scored_pair(organization, workspace, user)
    query = {"runs": f"{base.id},{run.id}", "baseline": str(base.id)}
    in_order = [str(example.example_id) for example in examples]
    by_input = [str(example.example_id) for example in sorted(
        examples, key=lambda example: example.input["question"])]
    assert ordered(auth_client, dataset, **query) == in_order
    assert ordered(auth_client, dataset, **query, sort="order") == in_order
    assert ordered(auth_client, dataset, **query, sort="-order") == in_order[::-1]
    assert ordered(auth_client, dataset, **query, sort="input") == by_input
    assert ordered(auth_client, dataset, **query, sort="-input") == by_input[::-1]
    # Worse on the metric first, then better, then the rows that did not move.
    assert ordered(auth_client, dataset, **query, sort="regressed") == ids(
        examples, [2, 3, 0, 1, 4, 5, 6])


# The ordering alone, on rows as the read builds them; no database.
DECLARED = {"evaluator_version_id": "version", "output_type_normalized": "numeric",
            "pass_threshold": 0.5, "higher_is_better": True}
UNDECLARED = {"evaluator_version_id": "version", "output_type_normalized": "numeric",
              "pass_threshold": 0.5}
KEY = "eval:quality"
# Run minus baseline: +0.25, -0.5, +0.25, unknown, -0.5.
PAIRS = {"a": (0.5, 0.75), "b": (1, 0.5), "c": (0.25, 0.5), "d": (None, 1), "e": (0.75, 0.25)}


def selection_of(binding, pairs=PAIRS, key=KEY):
    def cell(value):
        return {"scores": {key: {"value": value}}}

    rows = [{"example_id": eid, "results": {"base": cell(base), "run": cell(run)}}
            for eid, (base, run) in pairs.items()]
    return SimpleNamespace(
        rows=rows, runs=[SimpleNamespace(id="base"), SimpleNamespace(id="run")],
        baseline_run_id="base", plans={"base": [(eid, 1) for eid in pairs]},
        bindings={"base": {key: binding}, "run": {key: binding}}, items={}, dataset=None)


def sorted_ids(selection, sort):
    return [row["example_id"] for row in filter_rows(selection, {"sort": sort})]


def test_a_metric_key_with_colons_sorts_and_equal_differences_keep_their_order():
    selection = selection_of(DECLARED)
    assert sorted_ids(selection, f"delta:{KEY}:run:asc") == ["b", "e", "a", "c", "d"]
    assert sorted_ids(selection, f"delta:{KEY}:run:desc") == ["a", "c", "b", "e", "d"]


def test_a_declared_lower_is_better_metric_flips_the_order():
    selection = selection_of(
        {**UNDECLARED, "config_snapshot": {"external": {"higher_is_better": False}}})
    assert sorted_ids(selection, f"delta:{KEY}:run:asc") == ["a", "c", "b", "e", "d"]
    assert sorted_ids(selection, f"delta:{KEY}:run:desc") == ["b", "e", "a", "c", "d"]


def test_a_metric_with_no_direction_keeps_the_row_order():
    selection = selection_of(UNDECLARED)
    for direction in ("asc", "desc"):
        assert sorted_ids(selection, f"delta:{KEY}:run:{direction}") == list(PAIRS)


@pytest.mark.parametrize("sort", [
    pytest.param("delta:", id="nothing-named"),
    pytest.param("delta:run:asc", id="no-metric"),
    pytest.param(f"delta:{KEY}:run", id="no-direction"),
    pytest.param(f"delta:{KEY}:run:up", id="an-unknown-direction"),
    pytest.param("delta::run:asc", id="a-blank-metric"),
    pytest.param("score:run:asc", id="a-score-with-no-metric"),
    pytest.param(f"score:{KEY}:run", id="a-score-with-no-direction"),
    pytest.param("latency:", id="a-latency-with-no-run"),
    pytest.param("latency:run:asc", id="a-latency-with-a-direction"),
])
def test_a_malformed_sort_by_a_runs_values_is_refused_where_the_rows_are_sorted(sort):
    with pytest.raises(ResultsReadError) as error:
        filter_rows(selection_of(DECLARED), {"sort": sort})
    assert error.value.status == 400


@pytest.mark.parametrize("case,text", [
    pytest.param("metric", NO_METRIC, id="a-metric-these-runs-do-not-score"),
    pytest.param("run", NO_RUN, id="a-run-outside-the-selection"),
    pytest.param("baseline_run", NO_RUN, id="the-baseline-itself"),
    pytest.param("no_baseline", NO_BASELINE, id="no-baseline"),
])
def test_the_selection_refuses_a_delta_sort_it_cannot_answer(case, text):
    selection = selection_of(DECLARED)
    sort = {"metric": "delta:eval:other:run:asc", "run": f"delta:{KEY}:elsewhere:asc",
            "baseline_run": f"delta:{KEY}:base:asc"}.get(case, f"delta:{KEY}:run:asc")
    if case == "no_baseline":
        selection.baseline_run_id = None
    with pytest.raises(ResultsReadError) as error:
        filter_rows(selection, {"sort": sort})
    assert error.value.status == 400 and str(error.value) == text


# A run's own values. Per row: the run's score and latency (None = unknown);
# the baseline has a latency only, rising down the rows.
VALUES = {"a": (0.5, 30), "b": (None, 10), "c": (1, None), "d": (0.25, 30), "e": (0.5, 20)}


def measured_selection(baseline=None):
    def cell(score, latency):
        return {"scores": {KEY: {"value": score}}, "latency_ms": latency}

    rows = [{"example_id": eid, "results": {"base": cell(None, n), "run": cell(*pair)}}
            for n, (eid, pair) in enumerate(VALUES.items(), start=1)]
    return SimpleNamespace(
        rows=rows, runs=[SimpleNamespace(id="base"), SimpleNamespace(id="run")],
        baseline_run_id=baseline, plans={"base": [(eid, 1) for eid in VALUES]},
        bindings={"base": {KEY: UNDECLARED}, "run": {KEY: UNDECLARED}}, items={}, dataset=None)


def test_a_score_sort_orders_by_the_runs_value_and_needs_no_baseline_or_direction():
    selection = measured_selection()
    assert sorted_ids(selection, f"score:{KEY}:run:asc") == ["d", "a", "e", "c", "b"]
    assert sorted_ids(selection, f"score:{KEY}:run:desc") == ["c", "a", "e", "d", "b"]


def test_a_latency_sort_puts_the_slowest_row_first_and_unknown_rows_last():
    assert sorted_ids(measured_selection(), "latency:run") == ["a", "d", "e", "b", "c"]


def test_a_sort_by_a_runs_own_values_may_name_a_selected_baseline():
    selection = measured_selection(baseline="base")
    assert sorted_ids(selection, "latency:base") == ["e", "d", "c", "b", "a"]
    assert sorted_ids(selection, "latency:run") == ["a", "d", "e", "b", "c"]


@pytest.mark.parametrize("sort,text", [
    pytest.param("score:eval:other:run:asc", NO_METRIC, id="a-score-on-a-metric-the-run-does-not-score"),
    pytest.param(f"score:{KEY}:elsewhere:asc", NOT_SELECTED, id="a-score-of-a-run-outside-the-selection"),
    pytest.param("latency:elsewhere", NOT_SELECTED, id="a-latency-of-a-run-outside-the-selection"),
])
def test_the_selection_refuses_a_sort_by_a_runs_own_values_it_cannot_answer(sort, text):
    with pytest.raises(ResultsReadError) as error:
        filter_rows(measured_selection(), {"sort": sort})
    assert error.value.status == 400 and str(error.value) == text


# The query's shape.
RUN = "0B5B7B9E-1111-4C8F-9A8E-0E2F8D1D2C3A"


def validated_sort(sort):
    serializer = DatasetResultsQuerySerializer(data={"runs": str(uuid.uuid4()), "sort": sort})
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data["sort"]


@pytest.mark.parametrize("metric", [
    pytest.param("9d4c3f85-5555-4a1b-9ecd-4c6dcd5b6a7e", id="an-evaluation-id"),
    pytest.param("eval:quality", id="a-key-with-colons"),
    pytest.param("latency", id="a-plain-key"),
])
def test_the_query_accepts_a_delta_or_score_sort_and_reads_the_run_as_an_id(metric):
    for kind in ("delta", "score"):
        for direction in ("asc", "desc"):
            assert validated_sort(f"{kind}:{metric}:{RUN}:{direction}") == (
                f"{kind}:{metric}:{RUN.lower()}:{direction}")


def test_the_query_accepts_a_latency_or_spend_sort_and_reads_the_run_as_an_id():
    for kind in ("latency", SPEND):
        assert validated_sort(f"{kind}:{RUN}") == f"{kind}:{RUN.lower()}"


@pytest.mark.parametrize(
    "sort",
    ["order", "-order", "input", "-input", "regressed"],
    ids=["order", "reversed-order", "input", "reversed-input", "regressed"],
)
def test_the_query_accepts_the_other_sorts_unchanged(sort):
    assert validated_sort(sort) == sort


@pytest.mark.parametrize(
    "sort",
    [
        pytest.param("", id="blank"),
        pytest.param("newest", id="an-unknown-word"),
        pytest.param("-regressed", id="a-reversed-regressed"),
        pytest.param("delta", id="delta-alone"),
        pytest.param("delta:", id="delta-with-nothing-named"),
        pytest.param(f"delta:{RUN}:asc", id="delta-with-no-metric"),
        pytest.param(f"delta:eval:quality:{RUN}", id="delta-with-no-direction"),
        pytest.param(
            f"delta:eval:quality:{RUN}:up", id="delta-with-an-unknown-direction"
        ),
        pytest.param(
            "delta:eval:quality:not-a-run:asc", id="delta-with-a-run-that-is-not-an-id"
        ),
        pytest.param(f"delta::{RUN}:asc", id="delta-with-a-blank-metric"),
        pytest.param(
            f"delta:{'m' * 201}:{RUN}:asc", id="delta-with-an-overlong-metric"
        ),
        pytest.param(f"score:{RUN}:asc", id="score-with-no-metric"),
        pytest.param(f"score:eval:quality:{RUN}", id="score-with-no-direction"),
        pytest.param("latency:", id="latency-with-no-run"),
        pytest.param("latency:not-a-run", id="latency-with-a-run-that-is-not-an-id"),
        pytest.param(f"latency:{RUN}:desc", id="latency-with-a-direction"),
        pytest.param(f"{SPEND}:", id="spend-with-no-run"),
        pytest.param(f"variance:{RUN}", id="a-variance-sort-that-names-no-metric"),
        pytest.param(
            f"variance:eval:quality:{RUN}:desc", id="a-variance-sort-with-a-direction"
        ),
        pytest.param(
            "variance:eval:quality:not-a-run",
            id="a-variance-sort-with-a-run-that-is-not-an-id",
        ),
        pytest.param(
            f"variance:{'m' * 201}:{RUN}", id="a-variance-sort-with-an-overlong-metric"
        ),
        pytest.param("flakiness:", id="a-flakiness-sort-with-no-run"),
        pytest.param(
            f"flakiness:eval:quality:{RUN}", id="a-flakiness-sort-that-names-a-metric"
        ),
        pytest.param(f"flakiness:{RUN}:desc", id="a-flakiness-sort-with-a-direction"),
    ],
)
def test_the_query_refuses_any_other_sort(sort):
    with pytest.raises(ValidationError):
        validated_sort(sort)
