"""
Hypothesis property-based tests for format_eval_value.

Where Z3 proves properties hold for all logical inputs, Hypothesis generates
random concrete inputs and checks runtime invariants — catching edge cases
that formal models miss (None handling, empty strings, unexpected types).

Run with: pytest evaluations/tests/test_formatter_hypothesis.py -v -m unit
"""

from types import SimpleNamespace

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

pytestmark = pytest.mark.unit

KNOWN_OUTPUT_TYPES = ["Pass/Fail", "score", "numeric", "reason", "choices"]
ALL_OUTPUT_TYPES = KNOWN_OUTPUT_TYPES + ["unknown_type", "", None]


# ── Strategies ───────────────────────────────────────────────────────────────

metric = st.fixed_dictionaries({"id": st.text(), "value": st.floats(allow_nan=False, allow_infinity=False, min_value=-1e9, max_value=1e9)})

result_data = st.fixed_dictionaries({
    "output":   st.sampled_from(ALL_OUTPUT_TYPES),
    "failure":  st.one_of(st.none(), st.text(min_size=1)),
    "reason":   st.one_of(st.none(), st.text()),
    "data":     st.one_of(
                    st.none(),
                    st.dictionaries(st.text(), st.text()),
                    st.lists(st.text(), min_size=0, max_size=3),
                    st.text(),
                ),
    "metrics":  st.one_of(st.just([]), st.lists(metric, min_size=1, max_size=3)),
    "model":    st.just("test"),
    "runtime":  st.just(0.1),
    "metadata": st.just({}),
})

choice_scores_st = st.one_of(
    st.just({}),
    st.dictionaries(
        st.text(min_size=1, max_size=20),
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        min_size=1,
        max_size=5,
    ),
)

eval_template_st = st.builds(
    lambda output_type, eval_type_id, choice_scores, multi_choice: SimpleNamespace(
        name="test-eval",
        config={"eval_type_id": eval_type_id, "output": output_type},
        choice_scores=choice_scores,
        multi_choice=multi_choice,
        choices=[],
        criteria=None,
        organization=None,
    ),
    output_type=st.sampled_from(ALL_OUTPUT_TYPES),
    eval_type_id=st.sampled_from([
        "CustomPromptEvaluator", "DeterministicEvaluator",
        "AgentEvaluator", "CustomCodeEval", "RankingEvaluator",
    ]),
    choice_scores=choice_scores_st,
    multi_choice=st.booleans(),
)


# ── Properties ───────────────────────────────────────────────────────────────

@given(rd=result_data, tmpl=eval_template_st)
@settings(max_examples=500)
def test_format_eval_value_never_raises(rd, tmpl):
    """format_eval_value is total: it never raises for any input combination."""
    from evaluations.engine.formatting import format_eval_value

    try:
        format_eval_value(rd, tmpl)
    except Exception as e:
        pytest.fail(f"format_eval_value raised {type(e).__name__}: {e}\nInput: {rd}\nTemplate: {tmpl}")


@given(
    failure=st.one_of(st.none(), st.text(min_size=1)),
    data=st.one_of(st.text(), st.none()),
)
@settings(max_examples=200)
def test_pass_fail_non_deterministic_uses_failure_flag(failure, data):
    """
    For non-DeterministicEvaluator Pass/Fail evals, result is 'Passed' iff
    failure is falsy. The data value is irrelevant when data is not a dict.
    """
    from evaluations.engine.formatting import format_eval_value

    assume(not isinstance(data, dict))

    tmpl = SimpleNamespace(
        name="test",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        choice_scores={},
        multi_choice=False,
        choices=[],
        criteria=None,
        organization=None,
    )
    rd = {"output": "Pass/Fail", "failure": failure, "data": data, "metrics": [], "reason": None}

    result = format_eval_value(rd, tmpl)

    if failure:
        assert result == "Failed", f"Expected 'Failed' when failure={failure!r}, got {result!r}"
    else:
        assert result == "Passed", f"Expected 'Passed' when failure=None, got {result!r}"


@given(values=st.lists(
    st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
    min_size=1, max_size=5,
))
@settings(max_examples=200)
def test_score_output_returns_first_metric_value(values):
    """For 'score' output type, result is always metrics[0]['value']."""
    from evaluations.engine.formatting import format_eval_value

    tmpl = SimpleNamespace(
        name="test",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "score"},
        choice_scores={},
        multi_choice=False,
        choices=[],
        criteria=None,
        organization=None,
    )
    metrics = [{"id": f"m{i}", "value": v} for i, v in enumerate(values)]
    rd = {"output": "score", "failure": None, "data": None, "metrics": metrics, "reason": None}

    result = format_eval_value(rd, tmpl)

    assert result == values[0], f"Expected {values[0]}, got {result}"


@given(reason=st.one_of(st.none(), st.text()))
@settings(max_examples=100)
def test_reason_output_returns_reason_string(reason):
    """For 'reason' output type, result is always result_data['reason']."""
    from evaluations.engine.formatting import format_eval_value

    tmpl = SimpleNamespace(
        name="test",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "reason"},
        choice_scores={},
        multi_choice=False,
        choices=[],
        criteria=None,
        organization=None,
    )
    rd = {"output": "reason", "failure": None, "data": None, "metrics": [], "reason": reason}

    result = format_eval_value(rd, tmpl)

    assert result == reason


@given(
    output_type=st.text().filter(lambda s: s not in KNOWN_OUTPUT_TYPES),
    rd=result_data,
)
@settings(max_examples=200)
def test_unknown_output_type_returns_none(output_type, rd):
    """Unknown output types always return None — no silent wrong classification."""
    from evaluations.engine.formatting import format_eval_value

    rd = {**rd, "output": output_type}
    tmpl = SimpleNamespace(
        name="test",
        config={"eval_type_id": "CustomPromptEvaluator", "output": output_type},
        choice_scores={},
        multi_choice=False,
        choices=[],
        criteria=None,
        organization=None,
    )

    result = format_eval_value(rd, tmpl)

    assert result is None, f"Expected None for unknown output_type={output_type!r}, got {result!r}"


@given(
    choice_scores=st.dictionaries(
        st.text(min_size=1, max_size=10),
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        min_size=1, max_size=5,
    ),
    output_type=st.sampled_from(["score", "numeric", "reason", "choices"]),
)
@settings(max_examples=200)
def test_nonempty_choice_scores_forces_choices_branch(choice_scores, output_type):
    """
    When choice_scores is non-empty, output_type is forced to 'choices'
    regardless of the configured output_type (unless it's Pass/Fail).
    Result must be a dict with 'score' key, or None if data is absent.
    """
    from evaluations.engine.formatting import format_eval_value

    tmpl = SimpleNamespace(
        name="test",
        config={"eval_type_id": "CustomPromptEvaluator", "output": output_type},
        choice_scores=choice_scores,
        multi_choice=False,
        choices=list(choice_scores.keys()),
        criteria=None,
        organization=None,
    )
    # Provide a choice that exists in choice_scores
    choice_label = next(iter(choice_scores))
    rd = {
        "output": output_type,
        "failure": None,
        "data": choice_label,
        "metrics": [{"id": "m0", "value": 0.5}],
        "reason": "test",
    }

    result = format_eval_value(rd, tmpl)

    assert isinstance(result, dict), f"Expected dict for choices output, got {type(result).__name__}: {result!r}"
    assert "score" in result, f"choices result missing 'score' key: {result!r}"
