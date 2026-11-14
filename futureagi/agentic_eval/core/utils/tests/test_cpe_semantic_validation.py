"""CPE result-contract tests using the existing provider-free evaluator fixture.

Exercise the actual JSON parser/result boundary, not a stubbed validator.
Recover unambiguous label/number drift while preserving exact declared labels,
distinct multi-choice order and rejection of ambiguous or invalid results.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from agentic_eval.core.utils.tests.test_cpe_clamp import (
    _make_cpe,
    _patched_evaluate,
)


def _evaluate(output_type, value, choices=None, multi=False, scores=None):
    evaluator = _make_cpe(output_type, choices=choices, choice_scores=scores)
    evaluator._multi_choice = multi
    return _patched_evaluate(
        evaluator, json.dumps({"result": value, "explanation": "contract witness"})
    )


@pytest.mark.parametrize(
    "output_type,choices,multi,scores,value",
    [
        pytest.param("Pass/Fail", [], False, None, "Maybe", id="unknown-pass-fail"),
        pytest.param("score", [], False, None, "not-a-number", id="nonnumeric-score"),
        pytest.param(
            "choices",
            ["Low", "High"],
            False,
            {"Low": 0.2, "High": 0.8},
            "Unknown",
            id="unknown-scored-choice",
        ),
        pytest.param(
            "choices",
            ["Alpha", "Beta"],
            False,
            None,
            "Unknown",
            id="unknown-single-choice",
        ),
        pytest.param(
            "choices",
            ["Alpha", "Beta"],
            True,
            None,
            ["Alpha", "Unknown"],
            id="partly-unknown-multi-choice",
        ),
        pytest.param(
            "choices", ["Alpha", "Beta"], True, None, [], id="empty-multi-choice"
        ),
    ],
)
def test_six_proven_semantic_invalid_results_raise(
    output_type, choices, multi, scores, value
):
    with pytest.raises(ValueError, match="^Invalid evaluation result:"):
        _evaluate(output_type, value, choices, multi, scores)


@pytest.mark.parametrize("output_type", ["score", "numeric"])
@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        "NaN",
        "Infinity",
        "-Infinity",
        "1e999",
        "",
        "not-a-number",
        [],
        [0.5],
        {"score": 0.5},
    ],
)
def test_numeric_contract_rejects_non_numbers_and_nonfinite_values(output_type, value):
    with pytest.raises(ValueError, match="finite JSON number"):
        _evaluate(output_type, value)


@pytest.mark.parametrize("output_type", ["score", "numeric"])
@pytest.mark.parametrize("wire_number", ["1e999", "-1e999"])
def test_numeric_overflow_in_json_is_rejected(output_type, wire_number):
    evaluator = _make_cpe(output_type)
    with pytest.raises(ValueError, match="finite JSON number"):
        _patched_evaluate(evaluator, f'{{"result": {wire_number}, "explanation": "x"}}')


@pytest.mark.parametrize("output_type", ["score", "numeric"])
@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 0.0),
        (1, 1.0),
        (0.25, 0.25),
        (-0.0, -0.0),
        (-7, 0.0),
        (7, 1.0),
        (-1e308, 0.0),
        (1e308, 1.0),
    ],
)
def test_finite_numeric_results_keep_clamp_metrics_and_verdict(
    output_type, value, expected
):
    result = _evaluate(output_type, value)
    assert result["data"] == {"result": expected}
    assert result["metrics"] == [{"id": "custom_eval_score", "value": expected}]
    assert result["failure"] is False  # No new numeric failure threshold.
    assert result["reason"] == "contract witness"
    assert json.loads(result["metadata"])["explanation"] == "contract witness"


@pytest.mark.parametrize(
    "value",
    [
        "Passed",
        True,
        None,
        1,
        ["Pass"],
        {"result": "Pass"},
    ],
)
def test_pass_fail_rejects_unknown_labels_and_wrong_types(value):
    with pytest.raises(ValueError, match="^Invalid evaluation result:"):
        _evaluate("Pass/Fail", value)


@pytest.mark.parametrize(
    "value,expected,failed",
    [
        ("Pass", "Pass", False),
        (" pass ", "Pass", False),
        ("PASS", "Pass", False),
        ("Fail", "Fail", True),
        ("fail", "Fail", True),
        (" FAIL ", "Fail", True),
    ],
)
def test_pass_fail_normalizes_before_computing_verdict(value, expected, failed):
    result = _evaluate("Pass/Fail", value)
    assert result["data"] == {"result": expected}
    assert result["failure"] is failed
    assert result["metrics"] == [{"id": "custom_eval_score", "value": expected}]


@pytest.mark.parametrize(
    "value",
    [None, True, 1, [], ["Alpha"], {"result": "Alpha"}],
)
def test_single_choice_rejects_unknown_labels_and_wrong_shapes(value):
    with pytest.raises(ValueError, match="^Invalid evaluation result:"):
        _evaluate("choices", value, ["Alpha", "Beta"])


@pytest.mark.parametrize(
    "value",
    [
        "Alpha",
        None,
        True,
        1,
        {},
        [["Alpha"]],
        [1],
        [None],
        [True],
    ],
)
def test_multi_choice_requires_nonempty_list_of_declared_labels(value):
    with pytest.raises(ValueError, match="^Invalid evaluation result:"):
        _evaluate("choices", value, ["Alpha", "Beta"], multi=True)


@pytest.mark.parametrize(
    "label", ["1", "10", "NaN", "Infinity", "Joy", "joy", " joy ", "Pass", "Fail"]
)
@pytest.mark.parametrize("multi", [False, True])
def test_declared_labels_are_preserved_without_coercion_or_normalization(label, multi):
    choices = ["1", "10", "NaN", "Infinity", "Joy", "joy", " joy ", "Pass", "Fail"]
    value = [label] if multi else label
    result = _evaluate("choices", value, choices, multi)
    assert result["data"] == {"result": value}
    assert result["metrics"] == [{"id": "custom_eval_score", "value": value}]
    assert result["failure"] is (value == "Fail")  # Existing CPE behavior, unchanged.


@pytest.mark.parametrize(
    "value,multi",
    [("Low", False), ("High", False), (["High", "Low"], True), (["Low", "High"], True)],
)
def test_mapped_choices_keep_labels_order_and_existing_verdict(value, multi):
    # engine.instance promotes scored-choice output to runtime "choices" before CPE.
    scores = {"Low": 0.2, "High": 0.8}
    result = _evaluate("choices", value, list(scores), multi, scores)
    assert result["data"] == {"result": value}
    assert result["metrics"] == [{"id": "custom_eval_score", "value": value}]
    assert result["failure"] is False  # Mapping/failure thresholds remain downstream.


@pytest.mark.parametrize("value", [["High", "High"], ["High", "Low", "High"]])
@pytest.mark.parametrize("scores", [None, {"Low": 0.2, "High": 0.8}])
def test_multi_choice_rejects_repeated_picks(value, scores):
    with pytest.raises(ValueError, match="duplicate choices are not allowed"):
        _evaluate("choices", value, ["Low", "High"], multi=True, scores=scores)


@pytest.mark.parametrize("value", [["Alpha", {}], ["Alpha", ["Alpha"]]])
def test_multi_choice_validates_members_before_hashing(value):
    with pytest.raises(ValueError, match="nonempty list of declared choice strings"):
        _evaluate("choices", value, ["Alpha", "Beta"], multi=True)


@pytest.mark.parametrize("value", [["joy", "Joy", " joy "], [" joy ", "Joy", "joy"]])
def test_distinct_declared_case_and_space_variants_keep_order(value):
    scores = {"Joy": 1.0, "joy": 0.5, " joy ": 0.0}
    result = _evaluate("choices", value, list(scores), multi=True, scores=scores)
    assert result["data"] == {"result": value}
    assert result["metrics"] == [{"id": "custom_eval_score", "value": value}]
    assert result["failure"] is False


def test_membership_uses_declared_schema_not_score_mapping_fallback():
    # Scoring configuration cannot introduce a label absent from the vocabulary.
    with pytest.raises(ValueError, match="^Invalid evaluation result:"):
        _evaluate("choices", "Other", ["Low", "High"], scores={"Other": 0.2})
    result = _evaluate(
        "choices", "LOW", ["Low", "High"], scores={"LOW": 0.2, "High": 0.8}
    )
    assert result["data"] == {"result": "Low"}


@pytest.mark.parametrize("output_type", ["reason", "choices"])
@pytest.mark.parametrize("multi", [False, True])
def test_unconstrained_string_schema_fallback_remains_supported(output_type, multi):
    # Empty choices currently declares a plain string, not a closed enum. This
    # result-only patch does not introduce configuration validation.
    result = _evaluate(output_type, "free text", multi=multi)
    assert result["data"] == {"result": "free text"}


def test_invalid_numeric_value_is_rejected_before_clamping():
    with patch(
        "agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator.clamp_unit_score"
    ) as clamp:
        with pytest.raises(ValueError, match="finite JSON number"):
            _evaluate("score", True)
    clamp.assert_not_called()


@pytest.mark.parametrize("output_type", ["score", "numeric"])
@pytest.mark.parametrize(
    "value,expected",
    [
        (" 0.85 ", 0.85),
        ("1e-1", 0.1),
        ("0", 0.0),
        ("1", 1.0),
        ("3.5", 1.0),
        ("-0.1", 0.0),
    ],
)
def test_numeric_strings_normalize_then_use_existing_clamp(
    output_type, value, expected
):
    with capture_logs() as captured:
        result = _evaluate(output_type, value)
    assert result["data"] == {"result": expected}
    assert result["metrics"] == [{"id": "custom_eval_score", "value": expected}]
    assert result["failure"] is False
    warnings = [
        entry
        for entry in captured
        if entry["event"] == "eval_score_out_of_range_clamped"
    ]
    assert len(warnings) == int(float(value) < 0 or float(value) > 1)


def test_finite_out_of_range_value_still_emits_existing_warning():
    with capture_logs() as captured:
        result = _evaluate("numeric", 3.5)
    assert result["data"] == {"result": 1.0}
    assert [entry["event"] for entry in captured].count(
        "eval_score_out_of_range_clamped"
    ) == 1


@pytest.mark.parametrize(
    "value,choices,multi,expected",
    [
        (" Always ", ["never", "always"], False, "always"),
        ("STRASSE", ["Straße", "other"], False, "Straße"),
        (3, ["1", "2", "3", "4", "5"], False, "3"),
        (0.5, ["0.5", "1.0"], False, "0.5"),
        ([" JOY ", "Sad"], ["joy", "sad"], True, ["joy", "sad"]),
        ([3, " 1 "], ["1", "3"], True, ["3", "1"]),
    ],
)
def test_unambiguous_choice_drift_returns_declared_labels(
    value, choices, multi, expected
):
    result = _evaluate("choices", value, choices, multi)
    assert result["data"] == {"result": expected}
    assert result["metrics"] == [{"id": "custom_eval_score", "value": expected}]


@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize(
    "value,choices",
    [
        ("JOY", ["Joy", "joy"]),
        (" joy ", ["joy", "joy "]),
        ("STRASSE", ["Straße", "strasse"]),
        (3, ["3", " 3 "]),
    ],
)
def test_ambiguous_label_normalization_is_rejected(value, choices, multi):
    with pytest.raises(ValueError, match="ambiguous"):
        _evaluate("choices", [value] if multi else value, choices, multi)


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf")])
@pytest.mark.parametrize("multi", [False, True])
def test_nonfinite_and_boolean_values_never_become_choice_labels(value, multi):
    with pytest.raises(ValueError, match="^Invalid evaluation result:"):
        _evaluate(
            "choices",
            [value] if multi else value,
            ["True", "False", "nan", "inf"],
            multi,
        )


def test_normalized_duplicate_choices_remain_invalid():
    with pytest.raises(ValueError, match="duplicate choices"):
        _evaluate("choices", ["Joy", " JOY "], ["Joy", "Sad"], multi=True)


def test_normalized_failure_survives_formatting_and_error_feed_scoring():
    from evaluations.engine.formatting import format_eval_value

    # Importing the clustering module initializes its embedding manager. This
    # regression exercises only its pure score predicate, never model serving.
    with patch(
        "agentic_eval.core.embeddings.serving_client.ModelServingClient.health_check",
        return_value=True,
    ):
        from tracer.queries.eval_clustering import is_clusterable_eval_failure

    template = SimpleNamespace(
        choice_scores={"never": 0.0, "always": 1.0},
        pass_threshold=0.5,
        config={},
        multi_choice=False,
    )
    result = _evaluate(
        "choices", " Never ", ["never", "always"], scores=template.choice_scores
    )
    formatted = format_eval_value({**result, "output": "choices"}, template)
    assert formatted == {"choice": "never", "score": 0.0}
    entry = SimpleNamespace(
        output_str=json.dumps(formatted),
        output_float=None,
        output_bool=None,
        output_str_list=[],
        eval_explanation=result["reason"],
        custom_eval_config=SimpleNamespace(eval_template=template),
    )
    assert is_clusterable_eval_failure(entry) is True


@pytest.mark.parametrize("valid", [False, True])
def test_result_logs_never_include_judge_values_or_explanation(valid):
    evaluator = _make_cpe(
        "choices", choices=["private-label"] if valid else ["allowed"]
    )
    evaluator.api_key = "private-api-key"
    response = json.dumps(
        {"result": "private-label", "explanation": "private-explanation"}
    )
    with capture_logs() as captured:
        if valid:
            result = _patched_evaluate(evaluator, response)
            assert result["reason"] == "private-explanation"
        else:
            with pytest.raises(ValueError, match="^Invalid evaluation result:"):
                _patched_evaluate(evaluator, response)
    serialized = json.dumps(captured)
    for secret in ["private-label", "private-explanation", "private-api-key"]:
        assert secret not in serialized
    errors = [
        entry for entry in captured if entry["event"] == "custom_prompt_eval_error"
    ]
    assert len(errors) == (0 if valid else 1)
    if not valid:
        assert errors[0]["phase"] == "result_validation"
        assert errors[0]["model"] == "stub-model"
        assert errors[0]["provider"] == "openai"
        assert errors[0]["output_type"] == "choices"
        assert errors[0]["result_type"] == "str"
        assert "exc_info" not in errors[0]
