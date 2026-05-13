"""
Hypothesis property-based tests for tracer ID transformation pure functions.

Tests the actual implementations inline (without importing the Django-coupled
trace_ingestion module) to verify properties across random inputs.

Implementations sourced from tracer/utils/trace_ingestion.py — any changes
there must be mirrored here or the tests become stale.

Run with: pytest tracer/formal_tests/ -v -m unit
"""
import base64
import json
import re

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

pytestmark = pytest.mark.unit


# ── Implementations under test (inlined from trace_ingestion.py) ──────────────

def _is_hex(s: str) -> bool:
    return re.fullmatch(r"^[0-9a-fA-F]+$", s or "") is not None


def _format_id(id_str: str) -> str | None:
    if not id_str:
        return None
    return base64.b64decode(id_str).hex()


def _format_if_needed(raw: str) -> str | None:
    if not raw:
        return None
    return raw if _is_hex(raw) else _format_id(raw)


def _serialize_json_field_value(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        try:
            json.loads(val)
            return val
        except (json.JSONDecodeError, TypeError):
            return json.dumps(val)
    return json.dumps(val)


def _convert_attributes(attributes: list[dict]) -> dict:
    if not attributes:
        return {}
    return {
        item["key"]: item["value"].get(list(item["value"].keys())[0])
        for item in attributes
        if "key" in item and "value" in item and item["value"]
    }


# ── _is_hex ────────────────────────────────────────────────────────────────────

@given(st.text(alphabet="0123456789abcdefABCDEF", min_size=1))
def test_is_hex_true_for_hex_strings(s):
    assert _is_hex(s) is True


@given(st.text(alphabet="ghijklmnopqrstuvwxyz!@#$%^&*()", min_size=1))
def test_is_hex_false_for_non_hex_strings(s):
    # Only test strings with at least one definitely-non-hex character
    assume(any(c not in "0123456789abcdefABCDEF" for c in s))
    assert _is_hex(s) is False


def test_is_hex_empty_string():
    assert _is_hex("") is False


def test_is_hex_none_coerced():
    # _is_hex uses `s or ""` so None is coerced to ""
    assert _is_hex(None) is False


# ── _format_if_needed idempotency ─────────────────────────────────────────────

@given(st.text(alphabet="0123456789abcdefABCDEF", min_size=1))
def test_format_if_needed_is_identity_for_hex(s):
    """Hex strings are returned unchanged — no base64 decode attempted."""
    assert _format_if_needed(s) == s


@given(st.none() | st.just("") | st.just(False))
def test_format_if_needed_falsy_returns_none(raw):
    assert _format_if_needed(raw) is None


@given(
    st.binary(min_size=1, max_size=32).map(
        lambda b: base64.b64encode(b).decode("ascii")
    )
)
def test_format_if_needed_result_of_format_id_is_hex(b64_str):
    """
    The output of _format_id is always hex, so applying _format_if_needed to
    that output should always return it unchanged (idempotency of hex path).
    """
    formatted = _format_id(b64_str)
    assert formatted is not None
    assert _is_hex(formatted), f"_format_id output is not hex: {formatted!r}"
    assert _format_if_needed(formatted) == formatted


# ── _serialize_json_field_value ───────────────────────────────────────────────

def test_serialize_none_returns_none():
    assert _serialize_json_field_value(None) is None


@given(st.none())
def test_serialize_none_is_always_none(v):
    assert _serialize_json_field_value(v) is None


@given(st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.lists(st.text()),
    st.dictionaries(st.text(min_size=1), st.text()),
))
def test_serialize_non_string_returns_valid_json(val):
    result = _serialize_json_field_value(val)
    assert isinstance(result, str)
    # Round-trip must succeed
    json.loads(result)


@given(st.text())
@settings(max_examples=500)
def test_serialize_string_result_is_always_valid_json(s):
    result = _serialize_json_field_value(s)
    assert isinstance(result, str)
    json.loads(result)  # must not raise


@given(
    st.one_of(
        st.just("{}"),
        st.just("[]"),
        st.just('"hello"'),
        st.just("42"),
        st.just("null"),
        st.just("true"),
    )
)
def test_serialize_valid_json_string_is_identity(json_str):
    """Valid JSON strings are returned unchanged, not double-encoded."""
    assert _serialize_json_field_value(json_str) == json_str


# ── _convert_attributes ───────────────────────────────────────────────────────

def test_convert_attributes_empty_list():
    assert _convert_attributes([]) == {}


def test_convert_attributes_none_like():
    assert _convert_attributes(None) == {}


@given(st.lists(
    st.fixed_dictionaries({
        "key": st.text(min_size=1, max_size=20),
        "value": st.fixed_dictionaries({
            "stringValue": st.text(max_size=50),
        }),
    }),
    min_size=1,
    max_size=10,
))
def test_convert_attributes_all_keys_preserved(items):
    """Every key in the input list appears in the output dict."""
    result = _convert_attributes(items)
    for item in items:
        assert item["key"] in result


@given(st.lists(
    st.fixed_dictionaries({
        "key": st.text(min_size=1, max_size=20),
        "value": st.fixed_dictionaries({
            "intValue": st.integers(min_value=0, max_value=1000),
        }),
    }),
    min_size=1,
    max_size=10,
    unique_by=lambda x: x["key"],
))
def test_convert_attributes_values_extracted_from_value_dict(items):
    """Values are taken from the first key of the 'value' sub-dict."""
    result = _convert_attributes(items)
    for item in items:
        expected = item["value"]["intValue"]
        assert result[item["key"]] == expected


def test_convert_attributes_skips_items_without_key():
    items = [
        {"value": {"stringValue": "no key here"}},
        {"key": "valid", "value": {"stringValue": "present"}},
    ]
    result = _convert_attributes(items)
    assert "valid" in result
    assert len(result) == 1


def test_convert_attributes_skips_items_with_empty_value():
    items = [
        {"key": "empty_val", "value": {}},
        {"key": "valid", "value": {"stringValue": "ok"}},
    ]
    result = _convert_attributes(items)
    assert "empty_val" not in result
    assert "valid" in result
