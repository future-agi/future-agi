"""Unit tests for the shared OpenAPI-helper serializer fields.

`StringOrObjectField` and `StringOrArrayField` each carry a runtime guard
(`to_internal_value`) on top of the OpenAPI extension. The OpenAPI side is
covered by the generated-contract regen check; the runtime guard is the
non-trivial part: the parent `JSONField.to_internal_value` accepts any JSON
value (arrays, numbers, booleans, `null`, dicts), but our contracts say
``string | object`` and ``string | array`` respectively. Without the override
an SDK / curl / internal caller would persist ``response_format: []`` or
``messages[].content: 42`` happily, past the FE contract validator. These
tests exist so a future revert of either override fails CI loudly.
"""

import pytest
from rest_framework import serializers as drf_serializers

from tfc.utils.serializer_fields import StringOrArrayField, StringOrObjectField


# ── StringOrObjectField — string | object ────────────────────────────────────


class _ObjectFieldHost(drf_serializers.Serializer):
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
        # The headline allowed shape: ``response_format: "text"`` /
        # ``"json_object"`` from the form.
        s = _ObjectFieldHost(data={"response_format": "json_object"})
        assert s.is_valid(), s.errors
        assert s.validated_data["response_format"] == "json_object"

    def test_accepts_empty_string(self):
        # Empty string is still a string — the guard checks type, not truthiness.
        s = _ObjectFieldHost(data={"response_format": ""})
        assert s.is_valid(), s.errors

    def test_accepts_dict(self):
        # The other allowed shape: a structured JSON-schema response_format
        # object.
        payload = {"type": "json_schema", "json_schema": {"name": "x"}}
        s = _ObjectFieldHost(data={"response_format": payload})
        assert s.is_valid(), s.errors
        assert s.validated_data["response_format"] == payload

    def test_accepts_empty_dict(self):
        # ``{}`` is a valid (if empty) object — type, not truthiness.
        s = _ObjectFieldHost(data={"response_format": {}})
        assert s.is_valid(), s.errors

    def test_rejects_list(self):
        # ``JSONField.to_internal_value`` would accept ``[]``; the guard must not.
        s = _ObjectFieldHost(data={"response_format": []})
        assert not s.is_valid()
        assert "response_format" in s.errors

    def test_rejects_integer(self):
        s = _ObjectFieldHost(data={"response_format": 42})
        assert not s.is_valid()
        assert "response_format" in s.errors

    def test_rejects_boolean(self):
        # Booleans are ``int`` subclasses but not ``str`` / ``dict``.
        s = _ObjectFieldHost(data={"response_format": True})
        assert not s.is_valid()
        assert "response_format" in s.errors

    def test_omitted_field_is_allowed(self):
        # ``required=False`` — payloads without the key validate.
        s = _ObjectFieldHost(data={})
        assert s.is_valid(), s.errors


# ── StringOrArrayField — string | array ──────────────────────────────────────


class _ArrayFieldHost(drf_serializers.Serializer):
    """Host for ``StringOrArrayField`` — mirrors the prod usage on
    ``MessageItemSerializer.content``."""

    content = StringOrArrayField(required=False)


class TestStringOrArrayFieldRuntimeGuard:
    """The `to_internal_value` override accepts only `(str, list)`.

    Mirror of the StringOrObjectField suite — same gap, different union.
    Without this guard ``messages[].content: 42`` / ``content: {}`` /
    ``content: null`` would persist past the contract validator on any
    SDK / curl caller.
    """

    def test_accepts_plain_string(self):
        # Headline shape: plain text message content.
        s = _ArrayFieldHost(data={"content": "Hello, world!"})
        assert s.is_valid(), s.errors
        assert s.validated_data["content"] == "Hello, world!"

    def test_accepts_empty_string(self):
        s = _ArrayFieldHost(data={"content": ""})
        assert s.is_valid(), s.errors

    def test_accepts_list(self):
        # The other allowed shape: array of content-part objects
        # (OpenAI multi-part format).
        payload = [{"type": "text", "text": "Hi"}, {"type": "image_url", "image_url": "..."}]
        s = _ArrayFieldHost(data={"content": payload})
        assert s.is_valid(), s.errors
        assert s.validated_data["content"] == payload

    def test_accepts_empty_list(self):
        s = _ArrayFieldHost(data={"content": []})
        assert s.is_valid(), s.errors

    def test_rejects_dict(self):
        # ``JSONField.to_internal_value`` would accept ``{}``; the guard must not.
        s = _ArrayFieldHost(data={"content": {"type": "text"}})
        assert not s.is_valid()
        assert "content" in s.errors

    def test_rejects_integer(self):
        s = _ArrayFieldHost(data={"content": 42})
        assert not s.is_valid()
        assert "content" in s.errors

    def test_rejects_boolean(self):
        s = _ArrayFieldHost(data={"content": False})
        assert not s.is_valid()
        assert "content" in s.errors

    def test_omitted_field_is_allowed(self):
        s = _ArrayFieldHost(data={})
        assert s.is_valid(), s.errors
