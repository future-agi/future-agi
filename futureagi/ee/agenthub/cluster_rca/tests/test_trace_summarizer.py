"""Tests for trace_summarizer field truncation."""

from ee.agenthub.cluster_rca.trace_summarizer import _json_str, _MAX_FIELD_CHARS


class TestJsonStrTruncation:
    def test_none_returns_none_marker(self):
        assert _json_str(None) == "(none)"

    def test_short_string_passes_through(self):
        assert _json_str("hello") == "hello"

    def test_short_dict_serialized(self):
        assert _json_str({"a": 1}) == '{"a": 1}'

    def test_long_string_truncated(self):
        big = "x" * (_MAX_FIELD_CHARS + 1000)
        result = _json_str(big)
        assert len(result) < len(big)
        assert result.startswith("x" * 100)
        assert "chars truncated" in result

    def test_long_json_truncated(self):
        big = {"data": "y" * (_MAX_FIELD_CHARS + 500)}
        result = _json_str(big)
        assert "chars truncated" in result

    def test_exact_boundary_not_truncated(self):
        exact = "z" * _MAX_FIELD_CHARS
        result = _json_str(exact)
        assert result == exact
        assert "truncated" not in result

    def test_custom_max_chars(self):
        result = _json_str("abcdefghij", max_chars=5)
        assert result == "abcde… [5 chars truncated]"
