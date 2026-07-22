"""Unit tests for JSON-field serialization in trace ingestion.

Covers CORE-BACKEND-YQK: non-finite floats (Infinity/NaN) in span jsonb
fields broke the Postgres COPY ("invalid input syntax for type json /
Token 'Infinity' is invalid"). _serialize_json_field_value must scrub them
to null and never emit bare Infinity/NaN tokens, while leaving clean JSON
byte-identical.
"""

import json

import pytest

from tracer.utils.trace_ingestion import (
    _sanitize_nonfinite_floats,
    _serialize_json_field_value,
)


class TestSanitizeNonFiniteFloats:
    def test_inf_and_nan_become_none(self):
        assert _sanitize_nonfinite_floats(float("inf")) is None
        assert _sanitize_nonfinite_floats(float("-inf")) is None
        assert _sanitize_nonfinite_floats(float("nan")) is None

    def test_finite_floats_unchanged(self):
        assert _sanitize_nonfinite_floats(3.5) == 3.5
        assert _sanitize_nonfinite_floats(0.0) == 0.0

    def test_recurses_dict_list_tuple(self):
        assert _sanitize_nonfinite_floats({"a": [{"b": float("inf")}]}) == {
            "a": [{"b": None}]
        }
        assert _sanitize_nonfinite_floats([1, float("nan"), 3]) == [1, None, 3]

    def test_non_floats_passthrough(self):
        assert _sanitize_nonfinite_floats("x") == "x"
        assert _sanitize_nonfinite_floats(7) == 7
        assert _sanitize_nonfinite_floats(None) is None


class TestSerializeJsonFieldValue:
    def test_none_passthrough(self):
        assert _serialize_json_field_value(None) is None

    def test_clean_json_string_unchanged(self):
        s = '{"a": 1, "b": [2, 3.5]}'
        assert _serialize_json_field_value(s) == s

    def test_dict_with_infinity_scrubbed_and_valid_json(self):
        out = _serialize_json_field_value({"request_id": float("inf"), "ok": 1})
        assert "Infinity" not in out
        assert json.loads(out) == {"request_id": None, "ok": 1}

    def test_dict_with_nan_scrubbed(self):
        out = _serialize_json_field_value({"x": float("nan")})
        assert json.loads(out)["x"] is None

    def test_json_string_containing_infinity_token_is_scrubbed(self):
        # The exact YQK shape: a JSON string whose value is the bare Infinity token.
        s = '{"request_id": Infinity, "name": "cartesia.tts.TTS"}'
        out = _serialize_json_field_value(s)
        assert "Infinity" not in out
        assert json.loads(out) == {"request_id": None, "name": "cartesia.tts.TTS"}

    def test_output_is_valid_postgres_json(self):
        # Whatever comes out must be parseable JSON (no bare NaN/Infinity tokens).
        for val in [{"a": float("inf")}, [float("nan")], {"n": {"m": float("-inf")}}]:
            out = _serialize_json_field_value(val)
            json.loads(out)  # must not raise
