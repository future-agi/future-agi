"""Validation of Node.position.

`Node.position` is documented as UI coordinates `{"x": 0, "y": 0}` and carried
an unimplemented "Need to add a Validation for this structure" TODO. Nothing
checked it, so a malformed position was accepted at write time and only surfaced
in the UI as a node that renders in the wrong place or not at all.

The validator is deliberately narrow: it must not reject anything the system
already writes. The "accepted" tests below are the contract for that — each one
is a shape produced by real code paths (defaults, trace-to-graph import, the
serializer round-trip that carries a `z`).

Pure model-level validation; no database required.
"""

import math

import pytest
from django.core.exceptions import ValidationError

from agent_playground.models.node import Node


def _position(value):
    """A Node carrying only the field under test.

    _validate_position() is called directly rather than through clean(), so the
    other eight validators (which need related rows) stay out of the way.
    """
    node = Node()
    node.position = value
    return node


class TestAcceptedPositions:
    """Shapes the system already produces. These must never start failing."""

    def test_empty_dict_is_valid(self):
        """The field default. node_crud.create_node and version_content both
        fall back to {} when no position is supplied."""
        _position({})._validate_position()

    def test_integer_coordinates(self):
        _position({"x": 0, "y": 0})._validate_position()

    def test_float_coordinates(self):
        _position({"x": 12.5, "y": -3.25})._validate_position()

    def test_negative_coordinates(self):
        _position({"x": -100, "y": -250})._validate_position()

    def test_trace_to_graph_layout_output(self):
        """_compute_positions() emits {"x": level * 350, "y": i * 250}."""
        _position({"x": 2 * 350, "y": 3 * 250})._validate_position()

    def test_extra_keys_are_allowed(self):
        """Positions carrying a z are already persisted and round-tripped —
        see tests/serializers/test_node_serializers.py."""
        _position({"x": 500, "y": 300, "z": 10})._validate_position()

    def test_unset_falsy_values_are_treated_as_absent(self):
        """None reaches here from JSON nulls; treated as "not positioned"."""
        _position(None)._validate_position()


class TestRejectedPositions:
    def test_non_object_is_rejected(self):
        with pytest.raises(ValidationError, match="must be an object"):
            _position([1, 2])._validate_position()

    def test_string_is_rejected(self):
        with pytest.raises(ValidationError, match="must be an object"):
            _position("100,200")._validate_position()

    def test_missing_y_is_rejected(self):
        with pytest.raises(ValidationError, match="missing required key: y"):
            _position({"x": 1})._validate_position()

    def test_missing_x_is_rejected(self):
        with pytest.raises(ValidationError, match="missing required key: x"):
            _position({"y": 1})._validate_position()

    def test_missing_both_axes_is_rejected(self):
        """A non-empty object with neither axis is malformed, not 'unset'."""
        with pytest.raises(ValidationError, match="missing required keys: x, y"):
            _position({"z": 5})._validate_position()

    @pytest.mark.parametrize("bad", ["100", None, [], {}])
    def test_non_numeric_axis_is_rejected(self, bad):
        with pytest.raises(ValidationError, match="must be a number"):
            _position({"x": bad, "y": 0})._validate_position()

    def test_boolean_axis_is_rejected(self):
        """bool subclasses int — True as a coordinate is a bug, not a 1."""
        with pytest.raises(ValidationError, match="must be a number"):
            _position({"x": True, "y": 0})._validate_position()

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_axis_is_rejected(self, bad):
        """NaN/Infinity are not valid JSON and break the client on read."""
        with pytest.raises(ValidationError, match="must be finite"):
            _position({"x": bad, "y": 0})._validate_position()

    def test_error_names_the_offending_axis(self):
        """The point of validating here is a field-level message, so the axis
        has to appear in it."""
        with pytest.raises(ValidationError) as exc_info:
            _position({"x": 0, "y": "nope"})._validate_position()
        assert "'y'" in str(exc_info.value)


class TestWiredIntoClean:
    def test_validate_position_runs_as_part_of_clean(self):
        """A validator that exists but is never called is worse than none."""
        import inspect

        source = inspect.getsource(Node.clean)
        assert "_validate_position()" in source

    def test_nan_would_survive_without_the_validator(self):
        """Documents why finiteness is checked: json accepts NaN, JSON does not.

        Python's json module emits a bare `NaN` token by default, which is not
        valid JSON and is rejected by strict parsers — including the browser's.
        """
        import json

        assert json.dumps({"x": float("nan")}) == '{"x": NaN}'
        with pytest.raises(ValueError):
            json.loads('{"x": NaN}', parse_constant=_reject)


def _reject(token):
    raise ValueError(f"invalid JSON constant: {token}")


class TestValidatorIsSelfConsistent:
    @pytest.mark.parametrize(
        "position",
        [
            {"x": 0, "y": 0},
            {"x": -1.5, "y": 2.5},
            {"x": 700, "y": 100},
            {},
        ],
    )
    def test_positions_used_across_the_existing_test_suite_remain_valid(self, position):
        """Sampled from agent_playground/tests/** so this change cannot break
        fixtures already in the repo."""
        _position(position)._validate_position()

    def test_large_coordinates_are_allowed(self):
        """No arbitrary bounds — the canvas is unbounded."""
        _position({"x": 1e9, "y": -1e9})._validate_position()

    def test_finite_check_uses_math_isfinite_semantics(self):
        assert math.isfinite(1e308)
        _position({"x": 1e308, "y": 0})._validate_position()
