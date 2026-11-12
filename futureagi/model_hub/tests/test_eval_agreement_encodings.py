"""Both native controls and legacy queue submissions retain their verdicts."""

import pytest

from model_hub.services.eval_agreement_stats import scalar, stored_verdict


@pytest.mark.parametrize(
    "value,label_type,expected",
    [
        ("Pass", "categorical", "Pass"),
        (["Pass"], "categorical", "Pass"),
        ([1], "categorical", "1"),
        ({"selected": [1]}, "categorical", "1"),
        ({"selected": "Pass"}, "categorical", "Pass"),
        (["Pass", "Fail"], "categorical", None),
        (3, "numeric", 3),
        ("3.5", "numeric", 3.5),
        ({"value": "3.5"}, "numeric", 3.5),
        ("NaN", "numeric", None),
        ("Infinity", "numeric", None),
        (True, "numeric", None),
        ("4", "star", 4),
        ("clear", "text", "clear"),
        ("up", "thumbs_up_down", "up"),
    ],
)
def test_writer_accepted_human_encodings(value, label_type, expected):
    assert scalar(value, label_type) == expected


@pytest.mark.parametrize("choice", ["1", "true", "false", "null"])
def test_plain_json_like_choices_remain_categorical_text(choice):
    assert stored_verdict(choice, False) == choice


@pytest.mark.parametrize("choice", ["[Pass]", '"Pass"', "{unencoded}"])
def test_configured_plain_choices_are_not_mistaken_for_envelopes(choice):
    assert stored_verdict(choice, False, {choice}) == choice


def test_ambiguous_legacy_encoding_is_excluded_instead_of_guessed():
    assert stored_verdict('"Pass"', False, {'"Pass"', "Pass"}) is None
    assert (
        stored_verdict('{"choice":"Pass"}', False, {'{"choice":"Pass"}', "Pass"})
        is None
    )
    assert stored_verdict('"Pass"', False, {"Pass"}) == "Pass"


def test_deeply_nested_json_is_excluded_not_a_report_error():
    assert stored_verdict("[" * 20000 + "0" + "]" * 20000, False) is None
