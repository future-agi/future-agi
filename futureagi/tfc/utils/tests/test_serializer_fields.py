"""Unit tests for the shared OpenAPI-helper serializer fields.

`StringOrObjectField` carries a runtime guard (`to_internal_value`) on top of
the OpenAPI extension. The OpenAPI side is fine — the runtime guard is the
non-trivial part: the parent `JSONField.to_internal_value` accepts arrays,
numbers, booleans and `null`, but our contract says `string | object` only.
Without the override an SDK / curl / internal caller would persist
`response_format: []` happily, past the FE contract validator. These tests
exist so a future revert of the override fails CI loudly.
"""

import pytest
from rest_framework import serializers as drf_serializers

from tfc.utils.serializer_fields import StringOrObjectField


class _FormSerializer(drf_serializers.Serializer):
    """Minimal host serializer — `to_internal_value` runs as part of
    full-payload validation, not on a bare field instance, so we exercise
    the same path the prod API does (`serializer.is_valid()`)."""

    response_format = StringOrObjectField(required=False)


class TestStringOrObjectFieldRuntimeGuard:
    """The `to_internal_value` override accepts only `(str, dict)`.

    Each test calls `is_valid()` so the override runs in the same path
    `validated_request` / DRF takes for an inbound request body.
    """

    def test_accepts_plain_string(self):
        # The headline allowed shape: `response_format: "text"` /
        # `"json_object"` from the form.
        s = _FormSerializer(data={"response_format": "json_object"})
        assert s.is_valid(), s.errors
        assert s.validated_data["response_format"] == "json_object"

    def test_accepts_empty_string(self):
        # Empty string is still a string — the guard checks type, not truthiness.
        s = _FormSerializer(data={"response_format": ""})
        assert s.is_valid(), s.errors
        assert s.validated_data["response_format"] == ""

    def test_accepts_dict(self):
        # The other allowed shape: a structured JSON-schema response_format
        # object.
        payload = {"type": "json_schema", "json_schema": {"name": "x"}}
        s = _FormSerializer(data={"response_format": payload})
        assert s.is_valid(), s.errors
        assert s.validated_data["response_format"] == payload

    def test_accepts_empty_dict(self):
        # `{}` is a valid (if empty) object — type, not truthiness.
        s = _FormSerializer(data={"response_format": {}})
        assert s.is_valid(), s.errors
        assert s.validated_data["response_format"] == {}

    def test_rejects_list(self):
        # `JSONField.to_internal_value` would accept `[]`; the guard must not.
        s = _FormSerializer(data={"response_format": []})
        assert not s.is_valid()
        assert "response_format" in s.errors

    def test_rejects_non_empty_list(self):
        s = _FormSerializer(data={"response_format": [1, 2, 3]})
        assert not s.is_valid()
        assert "response_format" in s.errors

    def test_rejects_integer(self):
        s = _FormSerializer(data={"response_format": 42})
        assert not s.is_valid()
        assert "response_format" in s.errors

    def test_rejects_float(self):
        s = _FormSerializer(data={"response_format": 3.14})
        assert not s.is_valid()
        assert "response_format" in s.errors

    def test_rejects_boolean(self):
        # Booleans are `int` subclasses in Python but not `str` / `dict`,
        # so the guard correctly rejects them.
        s = _FormSerializer(data={"response_format": True})
        assert not s.is_valid()
        assert "response_format" in s.errors

    def test_omitted_field_is_allowed(self):
        # `required=False` — payloads without the key must still validate.
        # The guard never runs on a missing key (DRF short-circuits earlier).
        s = _FormSerializer(data={})
        assert s.is_valid(), s.errors
        assert "response_format" not in s.validated_data
