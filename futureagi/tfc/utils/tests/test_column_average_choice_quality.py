"""Column-summary regressions: stored quality is not execution completion.

Uses the production aggregator/formatter and an in-memory queryset boundary;
no database, provider, or live-service calls are needed by these tests.
"""

import json
from types import SimpleNamespace

import pytest

from evaluations.engine.formatting import format_eval_value
from model_hub.models.choices import OwnerChoices, SourceChoices
from tfc.utils import functions as summaries


class _Cells(list):
    def filter(self, **filters):
        def matches(cell):
            for key, expected in filters.items():
                if key == "row_id__in":
                    if cell.row_id not in expected:
                        return False
                    continue
                actual = cell
                for attr in key.split("__"):
                    actual = getattr(actual, attr)
                if actual != expected:
                    return False
            return True

        return _Cells(cell for cell in self if matches(cell))

    def count(self):
        return len(self)

    def exists(self):
        return bool(self)

    def order_by(self, *_fields):
        return self

    def values_list(self, field, *, flat):
        assert flat
        return [getattr(cell, field) for cell in self]


def _template(output="choices", *, choices=None, multi_choice=False):
    return SimpleNamespace(
        config={"output": output},
        owner=OwnerChoices.USER.value,
        choices=choices if choices is not None else ["Bad", "Good"],
        choice_scores={"Bad": 0, "Good": 1},
        multi_choice=multi_choice,
    )


@pytest.fixture
def summarize(monkeypatch):
    def run(
        values,
        *,
        template=None,
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        row_ids=None,
        overrides=None,
    ):
        cells = _Cells(
            SimpleNamespace(
                id=f"cell-{index}",
                row_id=index,
                row=SimpleNamespace(deleted=False),
                value=value,
                value_infos="{}",
                status="pass",
                deleted=False,
            )
            for index, value in enumerate(values)
        )
        for index, attrs in (overrides or {}).items():
            for key, value in attrs.items():
                setattr(cells[index], key, value)
        column = SimpleNamespace(
            id="column-id",
            name="quality",
            source=source,
            source_id=(
                "metric-id"
                if source == SourceChoices.EVALUATION.value
                else "variant-sourceid-metric-id"
            ),
            cell_set=cells,
        )
        monkeypatch.setattr(
            summaries.Column.objects,
            "prefetch_related",
            lambda *_args: SimpleNamespace(get=lambda **_kwargs: column),
        )
        metric = SimpleNamespace(template=template or _template())
        monkeypatch.setattr(
            summaries.UserEvalMetric.objects, "get", lambda **_kwargs: metric
        )
        return summaries.calculate_column_average("column-id", row_ids=row_ids)

    return run


@pytest.mark.parametrize(
    "encode", [lambda value: value, json.dumps, repr], ids=["dict", "json", "text-field-repr"]
)
@pytest.mark.parametrize(
    "scores, expected",
    [([0, 0], 0), ([1, 1], 100), ([0, 1], 50), ([0.005, 0.005], 0.5)],
    ids=["all-bad", "all-good", "mixed", "half-percent"],
)
def test_scored_choices_average_saved_scores(summarize, encode, scores, expected):
    values = [
        encode({"score": score, "choice": "Good" if score == 1 else "Bad"})
        for score in scores
    ]
    stats = summarize(values)
    assert stats["average"] == expected
    assert stats["valid_rows"] == stats["total_rows"] == 2
    assert stats["success_rate"] == 100
    assert stats["percentiles"] == summaries.calculate_percentiles(scores)


@pytest.mark.parametrize(
    "source",
    [
        SourceChoices.EVALUATION.value,
        SourceChoices.EXPERIMENT_EVALUATION.value,
        SourceChoices.OPTIMISATION_EVALUATION.value,
    ],
)
def test_dataset_and_experiment_columns_share_choice_quality(summarize, source):
    stats = summarize([{"score": 0, "choice": "Bad"}], source=source)
    assert stats["average"] == 0
    assert stats["valid_rows"] == 1
    assert stats["success_rate"] == 100


@pytest.mark.parametrize("live_scores", [{"Bad": 0, "Good": 1}, {"Bad": 1, "Good": 0}, {}])
def test_multichoice_keeps_saved_runtime_overlay_not_live_mapping(summarize, live_scores):
    template = _template(multi_choice=True)
    # The persisted envelope already contains the effective version + runtime map.
    value = format_eval_value(
        {"output": "choices", "data": ["Bad", "Good"]},
        template,
        choice_scores={"Bad": 0.2, "Good": 0.6},
        multi_choice=True,
    )
    assert value["score"] == pytest.approx(0.4)
    template.choice_scores = live_scores
    stats = summarize([repr(value)], template=template)
    assert stats["average"] == 40
    assert stats["success_rate"] == 100


def test_scored_numeric_labels_use_scores_not_label_numbers(summarize):
    template = _template(choices=["1", "5"])
    template.choice_scores = {"1": 0, "5": 1}
    stats = summarize(
        [repr({"score": 0, "choice": "1"}), repr({"score": 1, "choice": "5"})],
        template=template,
    )
    assert stats["average"] == 50


def test_text_field_choice_with_apostrophe_preserves_score(summarize):
    stats = summarize([repr({"score": 0.25, "choice": "It isn't good"})])
    assert stats["average"] == 25


def test_zero_policy_participates_and_exclude_policy_does_not(summarize):
    stats = summarize(
        [repr({"score": 1, "choice": "Good"}), "0", ""],
        overrides={
            1: {"value_infos": json.dumps({"upstream_error": True, "error_policy": "zero"})},
            2: {
                "status": "error",
                "value_infos": json.dumps({"upstream_error": True, "error_policy": "exclude"}),
            },
        },
    )
    assert stats["average"] == 50
    assert stats["total_rows"] == stats["valid_rows"] == 2


def test_all_policy_zero_choices_are_zero_not_completion_rate(summarize):
    stats = summarize(
        ["0"],
        overrides={0: {"value_infos": {"upstream_error": True, "error_policy": "zero"}}},
    )
    assert stats["average"] == 0


def test_choices_keep_completed_live_row_and_row_id_filters(summarize):
    values = [{"score": 0.2, "choice": "Bad"}] + [{"score": 1, "choice": "Good"}] * 5
    stats = summarize(
        values,
        row_ids=[0, 1, 2, 3, 4],
        overrides={
            1: {"status": "error"},
            2: {"status": "running"},
            3: {"deleted": True},
            4: {"row": SimpleNamespace(deleted=True)},
        },
    )
    assert stats["average"] == 20
    assert stats["total_rows"] == stats["valid_rows"] == 1


@pytest.mark.parametrize("score", [None, "invalid", float("nan"), float("inf"), -1, 2])
def test_invalid_scored_envelope_is_not_a_successful_quality_score(summarize, score):
    stats = summarize([{"score": score, "choice": "Bad"}])
    assert stats["average"] is None
    assert stats["valid_rows"] == 0
    assert stats["success_rate"] == 0


def test_quality_mean_and_valid_response_rate_are_separate(summarize):
    stats = summarize([{"score": 0.25, "choice": "Bad"}, {"score": None, "choice": "Bad"}])
    assert stats["average"] == 25
    assert stats["valid_rows"] == 1
    assert stats["total_rows"] == 2
    assert stats["success_rate"] == 50


@pytest.mark.parametrize("output", ["score", "numeric"])
@pytest.mark.parametrize("value, expected", [("0", 0), ("0.005", 0.5), ("0.01", 1), ("0.02", 2), ("0.5", 50), ("1", 100)])
def test_score_and_numeric_percentages_are_scaled_once(summarize, output, value, expected):
    stats = summarize([value], template=_template(output))
    assert stats["average"] == expected
    assert stats["valid_rows"] == 1
    assert stats["success_rate"] == 100


def test_pass_fail_quality_is_unchanged(summarize):
    stats = summarize(["Failed", "Passed"], template=_template("Pass/Fail"))
    assert stats["average"] == stats["success_rate"] == 50
    assert stats["valid_rows"] == 2


@pytest.mark.parametrize(
    "choices, expected, percentage", [(["1", "5"], 3, False), (["0", "1"], 50, True)]
)
def test_legacy_unscored_numeric_choices_are_unchanged(summarize, choices, expected, percentage):
    template = _template(choices=choices)
    template.choice_scores = {}
    stats = summarize([json.dumps([choice]) for choice in choices], template=template)
    assert stats["average"] == expected
    assert stats["is_numeric_eval"] is True
    assert stats["is_numeric_eval_percentage"] is percentage


def test_unscored_categorical_completion_is_unchanged(summarize):
    template = _template()
    template.choice_scores = {}
    stats = summarize(["Bad", "Good"], template=template)
    assert stats["average"] == stats["success_rate"] == 100


def test_empty_column_is_unscored(summarize):
    stats = summarize([])
    assert stats["average"] is None
    assert stats["total_rows"] == stats["valid_rows"] == 0


@pytest.mark.parametrize("encode_info", [lambda value: value, json.dumps], ids=["dict", "json"])
@pytest.mark.parametrize(
    "choices, expected, percentage",
    [(["1", "5"], 2.5, False), (["0", "1"], 50, True)],
    ids=["numeric-scale", "percentage-scale"],
)
@pytest.mark.parametrize("all_zero", [False, True], ids=["completed-and-zero", "all-zero"])
def test_legacy_unscored_numeric_choices_with_policy_zero(
    summarize, encode_info, choices, expected, percentage, all_zero
):
    template = _template(choices=choices)
    template.choice_scores = {}
    # A policy-zero marker is not evidence of a scored choice envelope.
    values = ["0" if all_zero else json.dumps([choices[-1]]), "0"]
    zero_rows = [0, 1] if all_zero else [1]
    stats = summarize(
        values,
        template=template,
        overrides={
            index: {
                "value_infos": encode_info({"upstream_error": True, "error_policy": "zero"})
            }
            for index in zero_rows
        },
    )
    assert stats["average"] == (0 if all_zero else expected)
    assert stats["total_rows"] == stats["valid_rows"] == 2
    assert stats["success_rate"] == 100
    assert stats["is_numeric_eval"] is True
    assert stats["is_numeric_eval_percentage"] is percentage
    numeric_values = [0.0 if all_zero else float(choices[-1]), 0.0]
    assert stats["percentiles"] == summaries.calculate_percentiles(numeric_values)


@pytest.mark.parametrize("live_scores", [{"1": 0, "5": 1}, {}])
def test_scored_numeric_choices_keep_policy_zero_participation(summarize, live_scores):
    template = _template(choices=["1", "5"])
    template.choice_scores = live_scores
    stats = summarize(
        ["0", repr({"score": 1, "choice": "5"})],
        template=template,
        overrides={0: {"value_infos": {"upstream_error": True, "error_policy": "zero"}}},
    )
    assert stats["average"] == 50
    assert stats["total_rows"] == stats["valid_rows"] == 2
    assert stats["success_rate"] == 100
    assert stats["percentiles"] == summaries.calculate_percentiles([0.0, 1.0])
