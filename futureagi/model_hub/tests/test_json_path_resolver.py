"""Unit tests for `parse_json_safely` and the derived-variable extractor."""

from model_hub.services.derived_variable_service import (
    extract_derived_variables_from_output,
)
from model_hub.utils.json_path_resolver import parse_json_safely


class TestParseJsonSafely:
    """Direct coverage for the multi-layer JSON parser."""

    def test_strict_dict_parses(self):
        data, ok = parse_json_safely('{"a": 1, "b": {"c": 2}}')
        assert ok is True
        assert data == {"a": 1, "b": {"c": 2}}

    def test_strict_list_parses(self):
        data, ok = parse_json_safely('[{"a": 1}, {"a": 2}]')
        assert ok is True
        assert data == [{"a": 1}, {"a": 2}]

    def test_json_string_scalar_is_rejected(self):
        # Valid JSON but a bare string — the extractor only cares about
        # dict/list because that's what produces sub-paths in the UI.
        data, ok = parse_json_safely('"just a string"')
        assert ok is False
        assert data is None

    def test_non_structural_wrapper_is_rejected(self):
        # Inputs where json_repair would still return a dict/list without the
        # structural gate. These must fail so the gate is actually pinned.
        for raw in [
            '```json\n{"a":1}\n```',
            'Here is the result: {"a":1}',
            '{"a":1} extra text',
            "\ufeff{\"a\":1}",
            "[1,2,3] extra text",
        ]:
            data, ok = parse_json_safely(raw)
            assert ok is False, f"should not parse: {raw!r}"
            assert data is None

    def test_prose_starting_with_curly_but_not_closed_is_rejected(self):
        # Gate requires matching first/last structural chars; open-only
        # prose is rejected even though json_repair would invent a list.
        data, ok = parse_json_safely("{ hello world")
        assert ok is False
        assert data is None

    def test_broken_json_with_trailing_comma_still_repairs(self):
        data, ok = parse_json_safely('{"a": 1, "b": 2,}')
        assert ok is True
        assert data == {"a": 1, "b": 2}

    def test_broken_json_single_quotes_still_repairs(self):
        data, ok = parse_json_safely("{'a': 1, 'b': 2}")
        assert ok is True
        assert data == {"a": 1, "b": 2}

    def test_empty_string_returns_none(self):
        data, ok = parse_json_safely("")
        assert ok is False
        assert data is None

    def test_whitespace_only_returns_none(self):
        data, ok = parse_json_safely("   \n\t  ")
        assert ok is False
        assert data is None

    def test_none_returns_none(self):
        data, ok = parse_json_safely(None)
        assert ok is False
        assert data is None

    def test_dict_passthrough(self):
        payload = {"a": 1}
        data, ok = parse_json_safely(payload)
        assert ok is True
        assert data is payload

    def test_list_passthrough(self):
        payload = [1, 2, 3]
        data, ok = parse_json_safely(payload)
        assert ok is True
        assert data is payload

    def test_non_string_non_container_returns_none(self):
        for bad in (42, 3.14, True):
            data, ok = parse_json_safely(bad)
            assert ok is False, f"expected False for {bad!r}"
            assert data is None


class TestExtractDerivedVariablesRegression:
    def test_embedded_json_in_prose_yields_no_derived_paths(self):
        # Without the structural gate, json_repair extracts {"a": 1} from
        # this string and the dropdown would show llm_test_2.a.
        result = extract_derived_variables_from_output(
            output='Here is the result: {"a":1}',
            column_name="llm_test_2",
        )
        assert result["is_json"] is False
        assert result["paths"] == []
        assert result["full_variables"] == []
        assert result["schema"] == {}
        assert result["raw_sample"] is None

    def test_markdown_fenced_json_yields_no_derived_paths(self):
        result = extract_derived_variables_from_output(
            output='```json\n{"a":1}\n```',
            column_name="llm_test_2",
        )
        assert result["is_json"] is False
        assert result["paths"] == []

    def test_structured_json_output_still_yields_paths(self):
        result = extract_derived_variables_from_output(
            output='{"answer": "42", "reason": {"kind": "final"}}',
            column_name="llm_test_2",
        )
        assert result["is_json"] is True
        assert "answer" in result["paths"]
        assert "reason" in result["paths"]
        assert "reason.kind" in result["paths"]
        assert "llm_test_2.answer" in result["full_variables"]
        assert "llm_test_2.reason.kind" in result["full_variables"]

    def test_repairable_json_output_still_yields_paths(self):
        result = extract_derived_variables_from_output(
            output='{"answer": "42", "score": 0.9,}',
            column_name="llm_test_2",
        )
        assert result["is_json"] is True
        assert set(result["paths"]) == {"answer", "score"}
