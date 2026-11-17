"""
A star or number value its label refuses is answered with a sentence that says
what to enter: the label's limits, written as a person reads them (3, not 3.0;
2.5 as it is), and never the name of the field the value was sent in.

A number off its label's steps is told the steps as the check counts them:
from the label's minimum, or as multiples of the step when the minimum is 0 or
not set. A single-choice label given more or fewer than one option is told to
choose one.
"""

import pytest

from model_hub.models.choices import AnnotationTypeChoices
from tracer.utils.annotation_validation import validate_annotation_value

STAR = AnnotationTypeChoices.STAR.value
NUMERIC = AnnotationTypeChoices.NUMERIC.value
CATEGORICAL = AnnotationTypeChoices.CATEGORICAL.value

CHOOSE_ONE = "Choose one option for this label."

SINGLE_CHOICE = {
    "options": [{"label": "Good"}, {"label": "Bad"}],
    "multi_choice": False,
}


@pytest.mark.unit
@pytest.mark.parametrize(
    "label_type, settings, value, sentence",
    [
        pytest.param(
            STAR,
            {"no_of_stars": 3},
            4,
            "Choose from 1 to 3 stars.",
            id="star above its stars",
        ),
        pytest.param(
            STAR,
            {"no_of_stars": 3},
            0,
            "Choose from 1 to 3 stars.",
            id="star below one",
        ),
        pytest.param(
            STAR,
            {"no_of_stars": 3.0},
            4,
            "Choose from 1 to 3 stars.",
            id="star count stored as a whole float",
        ),
        pytest.param(
            NUMERIC,
            {"min": 0, "max": 10},
            12,
            "Enter a number from 0 to 10.",
            id="number with both limits, above",
        ),
        pytest.param(
            NUMERIC,
            {"min": 0, "max": 10},
            -1,
            "Enter a number from 0 to 10.",
            id="number with both limits, below",
        ),
        pytest.param(
            NUMERIC,
            {"max": 10},
            12,
            "Enter a number of 10 or less.",
            id="number with a maximum only",
        ),
        pytest.param(
            NUMERIC,
            {"min": 1},
            0,
            "Enter a number of 1 or more.",
            id="number with a minimum only",
        ),
        pytest.param(
            NUMERIC,
            {"min": 0.0, "max": 2.5},
            3,
            "Enter a number from 0 to 2.5.",
            id="a whole and a fractional limit",
        ),
        pytest.param(
            NUMERIC,
            {"min": 0.5, "max": 10.0},
            0.25,
            "Enter a number from 0.5 to 10.",
            id="a fractional and a whole limit",
        ),
        pytest.param(
            NUMERIC,
            {"min": 1, "max": 9, "step_size": 2},
            4,
            "Enter a number in steps of 2 from 1.",
            id="whole step counted from the minimum",
        ),
        pytest.param(
            NUMERIC,
            {"min": 1.0, "max": 9.0, "step_size": 2.0},
            4,
            "Enter a number in steps of 2 from 1.",
            id="whole step and minimum stored as floats",
        ),
        pytest.param(
            NUMERIC,
            {"min": 0.25, "max": 5, "step_size": 0.5},
            1,
            "Enter a number in steps of 0.5 from 0.25.",
            id="fractional step counted from a fractional minimum",
        ),
        pytest.param(
            NUMERIC,
            {"max": 10, "step_size": 2},
            3,
            "Enter a multiple of 2.",
            id="whole step with no minimum",
        ),
        pytest.param(
            NUMERIC,
            {"min": 0, "max": 10, "step_size": 2},
            3,
            "Enter a multiple of 2.",
            id="whole step with a minimum of 0",
        ),
        pytest.param(
            NUMERIC,
            {"min": 0, "max": 5, "step_size": 0.5},
            1.2,
            "Enter a multiple of 0.5.",
            id="fractional step with a minimum of 0",
        ),
    ],
)
def test_a_refused_star_or_number_says_what_to_enter(
    label_type, settings, value, sentence
):
    reason = validate_annotation_value(label_type, settings, value_float=value)

    assert reason == sentence
    assert "value_float" not in reason


@pytest.mark.unit
@pytest.mark.parametrize(
    "label_type, settings, value",
    [
        pytest.param(STAR, {"no_of_stars": 3}, 3, id="star at its stars"),
        pytest.param(STAR, {"no_of_stars": 3}, 1, id="star at one"),
        pytest.param(NUMERIC, {"min": 0, "max": 10}, 10, id="number at its maximum"),
        pytest.param(NUMERIC, {"min": 0, "max": 10}, 0, id="number at its minimum"),
        pytest.param(NUMERIC, {"max": 10}, -5, id="number with a maximum only"),
        pytest.param(NUMERIC, {"min": 1}, 50, id="number with a minimum only"),
        pytest.param(
            NUMERIC,
            {"min": 1, "max": 9, "step_size": 2},
            5,
            id="number on a step counted from the minimum",
        ),
        pytest.param(
            NUMERIC, {"max": 10, "step_size": 2}, 4, id="number on a multiple"
        ),
        pytest.param(
            NUMERIC,
            {"min": 0, "max": 5, "step_size": 0.5},
            1.5,
            id="number on a fractional step",
        ),
    ],
)
def test_a_star_or_number_within_its_limits_is_accepted(label_type, settings, value):
    assert validate_annotation_value(label_type, settings, value_float=value) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "chosen",
    [
        pytest.param(["Good", "Bad"], id="two options"),
        pytest.param([], id="no option"),
    ],
)
def test_a_single_choice_label_given_other_than_one_option_says_choose_one(chosen):
    reason = validate_annotation_value(
        CATEGORICAL, SINGLE_CHOICE, value_str_list=chosen
    )

    assert reason == CHOOSE_ONE
    assert "multi_selection" not in reason


@pytest.mark.unit
@pytest.mark.parametrize(
    "settings, chosen",
    [
        pytest.param(SINGLE_CHOICE, ["Good"], id="single choice, one option"),
        pytest.param(
            {**SINGLE_CHOICE, "multi_choice": True},
            ["Good", "Bad"],
            id="multiple choice, two options",
        ),
    ],
)
def test_a_choice_label_given_options_it_allows_is_accepted(settings, chosen):
    assert (
        validate_annotation_value(CATEGORICAL, settings, value_str_list=chosen) is None
    )
