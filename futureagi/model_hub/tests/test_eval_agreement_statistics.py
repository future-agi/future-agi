"""Agreement denominators, numeric ties and malformed values are explicit."""

import pytest

from model_hub.services.eval_agreement_stats import scalar, statistics, stored_verdict


@pytest.mark.parametrize(
    "label_type,value,expected",
    [
        ("categorical", {"selected": ["Pass"]}, "Pass"),
        ("categorical", {"selected": ["Pass", "Fail"]}, None),
        ("text", {"text": "Exact words"}, "Exact words"),
        ("numeric", {"value": 0}, 0),
        ("star", {"rating": 3}, 3),
        ("thumbs_up_down", {"value": "up"}, "up"),
        ("numeric", {"value": True}, None),
        ("numeric", {"value": float("nan")}, None),
        ("numeric", {"value": "1"}, 1),
    ],
)
def test_human_label_encoding(label_type, value, expected):
    assert scalar(value, label_type) == expected


def test_numeric_statistics_use_tied_ranks_and_absolute_difference():
    report = statistics(
        [
            {"human": 1, "judge": 3},
            {"human": 1, "judge": 3},
            {"human": 3, "judge": 1},
        ],
        True,
    )
    assert report["spearman"] == pytest.approx(-1)
    assert report["mad"] == 2
    assert report["accuracy"] is None
    assert statistics([{"human": 1, "judge": 1}], True)["spearman"] is None


def test_absent_denominators_remain_unknown():
    assert statistics([], False)["accuracy"] is None
    report = statistics([{"human": "Pass", "judge": "Pass"}], False)
    assert report["accuracy"] == 1
    assert report["kappa"] is None
    report = statistics([{"human": "Pass", "judge": "Fail"}], False)
    assert report["per_label"][0]["recall"] is None
    assert report["per_label"][1]["precision"] is None


def test_judge_uses_stored_choice_or_number_without_threshold_inference():
    assert stored_verdict('{"choice":"Old choice","score":0.2}', False) == "Old choice"
    assert stored_verdict('{"choice":"Old choice","score":0.2}', True) == 0.2
    assert stored_verdict('{"score":1}', False) is None
    assert stored_verdict('{"score":NaN}', True) is None
    assert stored_verdict("Passed", False) == "Passed"
    assert (
        stored_verdict("{'score': 0.2, 'choice': 'Old choice'}", False) == "Old choice"
    )
    assert stored_verdict("{'score': 0.2, 'choice': 'Old choice'}", True) == 0.2
    assert stored_verdict("{broken", False) is None


def test_blank_verdicts_are_excluded_and_numeric_overflow_is_unknown():
    assert scalar({"selected": [" "]}, "categorical") is None
    assert scalar({"text": " "}, "text") is None
    assert stored_verdict(" ", False) is None
    assert stored_verdict('{"choice":" "}', False) is None
    assert statistics([{"judge": 1e308, "human": -1e308}], True)["mad"] is None
    assert statistics([{"judge": 1e308, "human": 0}] * 3, True)["mad"] == 1e308


def test_category_matrix_limit_is_explicit():
    with pytest.raises(ValueError, match="Too many distinct"):
        statistics([{"human": str(i), "judge": str(i)} for i in range(101)], False)
