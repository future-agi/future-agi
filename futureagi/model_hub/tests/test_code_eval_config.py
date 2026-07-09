"""Behavioural tests for the ``evaluate(...)`` signature parser."""

from __future__ import annotations

import pytest

from model_hub.utils.evals import (
    STANDARD_MAPPING_VARS,
    build_function_params_schema,
    parse_evaluate_params,
)


def test_python_signature_splits_standard_vars_from_config_params():
    code = (
        "def evaluate(input, output, expected, max_words_length):\n"
        "    return {'score': 1.0}\n"
    )

    mapping_vars, config_params = parse_evaluate_params(code, "python")

    assert mapping_vars == ["input", "output", "expected"]
    assert config_params == ["max_words_length"]


def test_python_signature_custom_named_param_is_a_config_param():
    """A named param the sandbox does not synthesise from the row must
    land in the config section so the user can supply a constant value
    instead of being asked to map it to a dataset column.
    """
    code = (
        "def evaluate(max_words_length, input):\n"
        "    text = str(input).strip()\n"
        "    words = text.split()\n"
        "    return {'score': 1.0 if len(words) <= int(max_words_length) else 0.0}\n"
    )

    mapping_vars, config_params = parse_evaluate_params(code, "python")

    assert mapping_vars == ["input"]
    assert config_params == ["max_words_length"]


def test_python_signature_reserves_context_from_both_lists():
    code = "def evaluate(context, input): pass\n"

    mapping_vars, config_params = parse_evaluate_params(code, "python")

    assert mapping_vars == ["input"]
    assert config_params == []


def test_python_signature_ignores_kwargs_wildcard():
    """`**kwargs` collects everything the sandbox pushes; it must not
    surface as either a mapping variable or a config param."""
    code = (
        "def evaluate(input, output, threshold=0.5, **kwargs):\n"
        "    return {'score': 1.0}\n"
    )

    mapping_vars, config_params = parse_evaluate_params(code, "python")

    assert mapping_vars == ["input", "output"]
    assert config_params == ["threshold"]


def test_python_signature_preserves_source_order():
    """Order matters: the FE renders both sections in signature order."""
    code = "def evaluate(threshold, input, tolerance, output): pass\n"

    mapping_vars, config_params = parse_evaluate_params(code, "python")

    assert mapping_vars == ["input", "output"]
    assert config_params == ["threshold", "tolerance"]


@pytest.mark.parametrize(
    "code",
    [
        "",
        None,
        "not python at all",
        "def something_else(input): pass",
        "def evaluate(",  # malformed
    ],
)
def test_parse_returns_empty_lists_on_missing_or_broken_input(code):
    assert parse_evaluate_params(code, "python") == ([], [])


def test_javascript_destructured_signature_splits_the_same_way():
    code = (
        "function evaluate({ input, output, threshold, max_length = 5 }) {\n"
        "  return { score: 1.0 };\n"
        "}\n"
    )

    mapping_vars, config_params = parse_evaluate_params(code, "javascript")

    assert mapping_vars == ["input", "output"]
    assert config_params == ["threshold", "max_length"]


def test_unknown_language_returns_empty_lists():
    assert parse_evaluate_params("def evaluate(x): pass", "rust") == ([], [])


def test_standard_mapping_vars_excludes_context():
    """`context` is synthesised by the sandbox from the row; if we listed
    it as a mapping variable the picker would demand a column mapping for
    something the user should never touch.
    """
    assert "context" not in STANDARD_MAPPING_VARS


def test_build_function_params_schema_shape_matches_runtime_expectation():
    schema = build_function_params_schema(["threshold", "tolerance"])

    assert set(schema.keys()) == {"threshold", "tolerance"}
    for entry in schema.values():
        assert entry == {
            "type": "string",
            "default": None,
            "nullable": True,
            "required": False,
        }


def test_build_function_params_schema_empty_list_yields_empty_dict():
    assert build_function_params_schema([]) == {}
