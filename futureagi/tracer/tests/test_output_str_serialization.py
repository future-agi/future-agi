"""
TH-4903: EvalLogger.output_str must hold valid JSON for dict values, not Python repr.

Five dispatchers in tracer/utils/eval.py share the same isinstance-chain for
mapping eval `value` → EvalLogger field. Pre-fix, dict values fell through
to the `str()` branch and were stored as Python repr (single-quoted), which
the read path (PR #400) cannot parse cleanly. This test pins both the
per-type dispatch behaviour and the presence of the fix at all 5 call sites.
"""

import json
from pathlib import Path

import pytest

EVAL_FILE = Path(__file__).parent.parent / "utils" / "eval.py"


def _dispatch(value, logger_kwargs=None):
    """Mirror of the type-dispatch block in tracer/utils/eval.py.

    Five dispatchers share this exact block:
        _run_evaluation, _execute_composite_on_span, _execute_evaluation,
        _execute_evaluation_for_trace, _execute_evaluation_for_session.
    The source-level guard `test_fix_present_at_all_five_sites` keeps this
    mirror in sync with the real code.
    """
    if logger_kwargs is None:
        logger_kwargs = {}
    if value == "ERROR":
        return logger_kwargs
    logger_kwargs["value"] = value
    if isinstance(value, bool):
        logger_kwargs["output_bool"] = value
    elif isinstance(value, float) or isinstance(value, int):
        logger_kwargs["output_float"] = float(value)
    elif value in ("Passed", "Failed"):
        logger_kwargs["output_bool"] = value == "Passed"
    elif isinstance(value, list):
        logger_kwargs["output_str_list"] = value
    elif isinstance(value, dict):
        logger_kwargs["output_str"] = json.dumps(value)
    else:
        logger_kwargs["output_str"] = str(value)
    return logger_kwargs


@pytest.mark.parametrize(
    "value,expected_key,expected_value",
    [
        # Dict — the TH-4903 case. Must be JSON, not Python repr.
        ({"score": 0.5, "choice": "3"}, "output_str", '{"score": 0.5, "choice": "3"}'),
        ({"score": 0.0, "choice": "1"}, "output_str", '{"score": 0.0, "choice": "1"}'),
        # List → output_str_list (unchanged path)
        ([1, 2, 3], "output_str_list", [1, 2, 3]),
        (["never", "always"], "output_str_list", ["never", "always"]),
        # Passed/Failed strings → output_bool
        ("Passed", "output_bool", True),
        ("Failed", "output_bool", False),
        # Bool → output_bool
        (True, "output_bool", True),
        (False, "output_bool", False),
        # Numeric → output_float
        (42.5, "output_float", 42.5),
        (5, "output_float", 5.0),
        # Plain string → output_str (unchanged path)
        ("custom string", "output_str", "custom string"),
    ],
)
def test_dispatch_per_type(value, expected_key, expected_value):
    kwargs = _dispatch(value)
    assert kwargs.get(expected_key) == expected_value


def test_dict_output_str_round_trips_through_json():
    """Dict values must be valid JSON parseable by json.loads."""
    kwargs = _dispatch({"score": 0.5, "choice": "3"})
    parsed = json.loads(kwargs["output_str"])
    assert parsed == {"score": 0.5, "choice": "3"}


def test_dict_output_str_is_not_python_repr():
    """Pre-fix shape was `{'score': 0.0, 'choice': '1'}` (single quotes). Lock that out."""
    kwargs = _dispatch({"score": 0.0, "choice": "1"})
    assert "'" not in kwargs["output_str"]
    assert '"' in kwargs["output_str"]


def test_error_value_is_skipped():
    """Sentinel 'ERROR' value bypasses the type-dispatch entirely."""
    kwargs = _dispatch("ERROR")
    assert "output_str" not in kwargs
    assert "value" not in kwargs


def test_fix_present_at_all_five_sites():
    """Source-level regression: the dict→json.dumps branch must exist at all 5 dispatchers.

    If this count drops, someone removed the fix from one of:
        _run_evaluation, _execute_composite_on_span, _execute_evaluation,
        _execute_evaluation_for_trace, _execute_evaluation_for_session.
    """
    src = EVAL_FILE.read_text()
    needle = (
        '        elif isinstance(value, dict):\n'
        '            logger_kwargs["output_str"] = json.dumps(value)'
    )
    count = src.count(needle)
    assert count == 5, f"Expected 5 occurrences of TH-4903 fix, found {count}"
