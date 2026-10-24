"""R-06..R-12: validate typed answers before producing any score."""

import copy

import pytest

from ee.jev.validation import JevResponseError, validate_answer, validate_response

pytestmark = pytest.mark.requires_ee


def response(answer=None):
    return {
        "model": "jev-1.13.0",
        "answers": {"q1": answer or {"type": "noul", "noul": 0.8}},
        "usage": {"input_tokens": 412, "output_tokens": 6},
    }


def validate(answer, **kwargs):
    return validate_answer(
        answer["type"],
        answer,
        requested_model="jev-1.13.0",
        actual_model="jev-1.13.0",
        **kwargs,
    )


def test_r07_noul_without_confidence():
    answer = validate({"type": "noul", "noul": 0.8})
    assert answer.probability == 0.8
    assert answer.confidence is None


def test_r09_reference_score():
    result = validate(
        {
            "type": "score",
            "score": 1.05,
            "legend": {"0": "bad", "1": "ok", "2": "good"},
            "probabilities": {"0": 0.2, "1": 0.55, "2": 0.25},
            "confidence": 0.55,
        },
        level_count=3,
        expected_levels=["bad", "ok", "good"],
    )
    assert result.normalized_score == 0.525


@pytest.mark.parametrize(
    "total,valid", [(1, True), (0.9995, True), (0.998, False), (0.98, False)]
)
def test_r12_probability_tolerance(total, valid):
    answer = {
        "type": "choice",
        "choice": "Yes",
        "probabilities": {"Yes": total},
        "confidence": 0.9,
    }
    if valid:
        assert validate(answer, expected_labels=["Yes"]).choice == "Yes"
    else:
        with pytest.raises(JevResponseError, match="JEV_RESPONSE_INVALID"):
            validate(answer, expected_labels=["Yes"])


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), -float("inf"), -0.1, 1.1, True, "0.5"]
)
def test_r12_invalid_noul(value):
    with pytest.raises(JevResponseError, match="JEV_RESPONSE_INVALID"):
        validate({"type": "noul", "noul": value})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.pop("answers"),
        lambda r: r.update(answers={"q9": {"type": "noul", "noul": 0.8}}),
        lambda r: r["answers"].update(q2={"type": "noul", "noul": 0.1}),
        lambda r: r["answers"]["q1"].update(type="score"),
        lambda r: r.pop("usage"),
        lambda r: r["usage"].update(input_tokens=-1),
        lambda r: r["usage"].update(output_tokens=True),
        lambda r: r.update(model=""),
        lambda r: r.update(extra=float("nan")),
    ],
)
def test_r12_response_envelope_rejections(mutate):
    raw = response()
    mutate(raw)
    with pytest.raises(JevResponseError, match="JEV_RESPONSE_INVALID"):
        validate_response(raw, "noul", requested_model="jev-latest")


def test_r06_pinned_mismatch_and_alias_actual_model():
    raw = response()
    raw["model"] = "jev-new"
    with pytest.raises(JevResponseError, match="JEV_MODEL_MISMATCH"):
        validate_response(raw, "noul", requested_model="jev-1.13.0")
    assert (
        validate_response(raw, "noul", requested_model="jev-latest").probability == 0.8
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("choice", "Other"),
        ("confidence", float("nan")),
        ("confidence", 1.2),
        ("probabilities", {"Other": 1}),
        ("probabilities", {"Yes": float("inf")}),
    ],
)
def test_r12_choice_invalid_fields(field, value):
    answer = {
        "type": "choice",
        "choice": "Yes",
        "probabilities": {"Yes": 1},
        "confidence": 0.9,
    }
    answer[field] = value
    with pytest.raises(JevResponseError, match="JEV_RESPONSE_INVALID"):
        validate(answer, expected_labels=["Yes"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("score", float("inf")),
        ("score", 2.1),
        ("legend", {"0": "wrong", "1": "ok", "2": "good"}),
        ("legend", {"0": "bad"}),
        ("probabilities", {"0": 0.9, "1": 0.1}),
        ("confidence", -0.1),
    ],
)
def test_r12_score_invalid_fields(field, value):
    answer = {
        "type": "score",
        "score": 1.05,
        "legend": {"0": "bad", "1": "ok", "2": "good"},
        "probabilities": {"0": 0.2, "1": 0.55, "2": 0.25},
        "confidence": 0.55,
    }
    answer[field] = copy.deepcopy(value)
    with pytest.raises(JevResponseError, match="JEV_RESPONSE_INVALID"):
        validate(answer, level_count=3, expected_levels=["bad", "ok", "good"])
