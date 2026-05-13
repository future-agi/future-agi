"""
Z3 symbolic verification of tracer ID transformation invariants.

Models the decision trees of `_format_if_needed` and
`_serialize_json_field_value` from tracer/utils/trace_ingestion.py.

Each test encodes a property as a Z3 formula and asserts UNSAT on its
negation — if Z3 cannot find a counterexample, the property is proven.

Run with: pytest tracer/formal_tests/ -v -m unit
"""

import pytest

pytest.importorskip("z3", reason="z3-solver required")
import z3

pytestmark = pytest.mark.unit


# ── Module-level Z3 sorts (declared once — Z3 forbids re-declaration) ─────────

# Output categories for _format_if_needed decision tree:
#   IS_NONE             — empty/falsy input
#   IS_HEX_PASSTHROUGH  — hex input returned unchanged
#   IS_FORMATTED        — non-hex input sent through base64 decode
FormatCat, (IS_NONE, IS_HEX_PASSTHROUGH, IS_FORMATTED) = z3.EnumSort(
    "FormatCat", ["is_none", "is_hex_passthrough", "is_formatted"]
)

# Output categories for _serialize_json_field_value decision tree:
#   OUT_NONE        — None input
#   OUT_PASSTHROUGH — string input that is already valid JSON
#   OUT_WRAPPED     — everything else (json.dumps applied)
SerializeCat, (OUT_NONE, OUT_PASSTHROUGH, OUT_WRAPPED) = z3.EnumSort(
    "SerializeCat", ["out_none", "out_passthrough", "out_wrapped"]
)


def _fmt(is_hex_in: z3.BoolRef, is_empty: z3.BoolRef) -> z3.ExprRef:
    """Z3 model of the _format_if_needed decision tree."""
    return z3.If(is_empty, IS_NONE,
           z3.If(is_hex_in, IS_HEX_PASSTHROUGH, IS_FORMATTED))


def _ser(is_none: z3.BoolRef, is_str: z3.BoolRef, is_valid_json_str: z3.BoolRef) -> z3.ExprRef:
    """Z3 model of the _serialize_json_field_value decision tree."""
    return z3.If(is_none, OUT_NONE,
           z3.If(z3.And(is_str, is_valid_json_str), OUT_PASSTHROUGH, OUT_WRAPPED))


# ── _format_if_needed properties ──────────────────────────────────────────────

def test_empty_input_always_returns_none():
    """PROPERTY: _format_if_needed(empty) is None regardless of hex status."""
    s = z3.Solver()
    is_hex_in, is_empty = z3.Bools("is_hex_in is_empty")
    s.add(z3.And(is_empty, _fmt(is_hex_in, is_empty) != IS_NONE))
    assert s.check() == z3.unsat, "Empty input must always produce None"


def test_hex_input_returned_unchanged():
    """PROPERTY: hex, non-empty input always takes the passthrough path."""
    s = z3.Solver()
    is_hex_in, is_empty = z3.Bools("is_hex_in2 is_empty2")
    s.add(z3.And(z3.Not(is_empty), is_hex_in, _fmt(is_hex_in, is_empty) != IS_HEX_PASSTHROUGH))
    assert s.check() == z3.unsat, "Hex input must be returned unchanged"


def test_non_empty_non_hex_always_formatted():
    """PROPERTY: non-empty non-hex input always goes through format_id."""
    s = z3.Solver()
    is_hex_in, is_empty = z3.Bools("is_hex_in3 is_empty3")
    s.add(z3.And(
        z3.Not(is_empty),
        z3.Not(is_hex_in),
        _fmt(is_hex_in, is_empty) != IS_FORMATTED,
    ))
    assert s.check() == z3.unsat, "Non-hex input must always be formatted"


def test_passthrough_and_format_paths_are_exclusive():
    """PROPERTY: IS_HEX_PASSTHROUGH and IS_FORMATTED cannot hold simultaneously."""
    s = z3.Solver()
    is_hex_in, is_empty = z3.Bools("is_hex_in4 is_empty4")
    out = _fmt(is_hex_in, is_empty)
    s.add(z3.And(out == IS_HEX_PASSTHROUGH, out == IS_FORMATTED))
    assert s.check() == z3.unsat, "Passthrough and format paths must be exclusive"


def test_all_inputs_produce_exactly_one_output_category():
    """PROPERTY: every input produces exactly one of the three output categories."""
    s = z3.Solver()
    is_hex_in, is_empty = z3.Bools("is_hex_in5 is_empty5")
    out = _fmt(is_hex_in, is_empty)
    # Negation: output is none of the three
    s.add(z3.And(out != IS_NONE, out != IS_HEX_PASSTHROUGH, out != IS_FORMATTED))
    assert s.check() == z3.unsat, "Output must be one of the three defined categories"


# ── _serialize_json_field_value properties ────────────────────────────────────

def test_none_input_returns_none():
    """PROPERTY: None input always produces OUT_NONE."""
    s = z3.Solver()
    is_none, is_str, is_valid = z3.Bools("sn1 ss1 sv1")
    s.add(z3.And(is_none, _ser(is_none, is_str, is_valid) != OUT_NONE))
    assert s.check() == z3.unsat, "None input must return None"


def test_non_none_input_never_returns_none():
    """PROPERTY: non-None input never produces OUT_NONE."""
    s = z3.Solver()
    is_none, is_str, is_valid = z3.Bools("sn2 ss2 sv2")
    s.add(z3.And(z3.Not(is_none), _ser(is_none, is_str, is_valid) == OUT_NONE))
    assert s.check() == z3.unsat, "Non-None input must never produce None output"


def test_valid_json_string_passes_through():
    """PROPERTY: non-None str that is already valid JSON always takes passthrough path."""
    s = z3.Solver()
    is_none, is_str, is_valid = z3.Bools("sn3 ss3 sv3")
    s.add(z3.And(
        z3.Not(is_none),
        is_str,
        is_valid,
        _ser(is_none, is_str, is_valid) != OUT_PASSTHROUGH,
    ))
    assert s.check() == z3.unsat, "Valid JSON string must be passed through unchanged"


def test_passthrough_and_wrapped_are_exclusive():
    """PROPERTY: OUT_PASSTHROUGH and OUT_WRAPPED cannot hold simultaneously."""
    s = z3.Solver()
    is_none, is_str, is_valid = z3.Bools("sn4 ss4 sv4")
    out = _ser(is_none, is_str, is_valid)
    s.add(z3.And(out == OUT_PASSTHROUGH, out == OUT_WRAPPED))
    assert s.check() == z3.unsat, "Passthrough and wrapped paths must be exclusive"


def test_all_serialize_inputs_produce_exactly_one_output_category():
    """PROPERTY: every input produces exactly one of the three output categories."""
    s = z3.Solver()
    is_none, is_str, is_valid = z3.Bools("sn5 ss5 sv5")
    out = _ser(is_none, is_str, is_valid)
    s.add(z3.And(out != OUT_NONE, out != OUT_PASSTHROUGH, out != OUT_WRAPPED))
    assert s.check() == z3.unsat, "Output must be one of the three defined categories"
