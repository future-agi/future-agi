"""Unit tests for `parse_json_safely` and the derived-variable extractor.

Introduced with TH-6975: the tolerant `json_repair.loads` / `ast.literal_eval`
fallbacks were treating narrative LLM output as candidate JSON and
hallucinating dicts / lists out of prose, which surfaced as nonsense
"sub-variables" in the Variable Mapping dropdown of the Add-Evaluation flow.

Run with: pytest model_hub/tests/test_json_path_resolver.py -v
"""

from model_hub.services.derived_variable_service import (
    extract_derived_variables_from_output,
)
from model_hub.utils.json_path_resolver import parse_json_safely


class TestParseJsonSafely:
    """Direct coverage for the multi-layer JSON parser."""

    # --- Happy paths for the strict parser (step 1) ---

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

    # --- TH-6975 regression: prose must NOT be hallucinated into JSON ---

    def test_narrative_with_quotes_and_commas_is_rejected(self):
        """Exact fragments from the ticket that used to hallucinate a list.

        `json_repair.loads` will happily interpret this as
        `["Save Template", "and", "Create new template."]`, producing the
        `col.Save Template,` sub-menu items shown in the ticket video.
        The structural-char gate blocks it entirely.
        """
        for prose in [
            'Save Template," and "Create new template."',
            'Test" and "Run',
            "Test,",
            "return response in the tool schema",
            "return_reason}}",
        ]:
            data, ok = parse_json_safely(prose)
            assert ok is False, f"prose should not parse: {prose!r}"
            assert data is None

    def test_prose_starting_with_curly_but_not_closed_is_rejected(self):
        # The gate requires matching first/last structural chars — a
        # sentence that happens to open with `{` won't slip through.
        data, ok = parse_json_safely("{ hello world")
        assert ok is False
        assert data is None

    def test_prose_ending_with_close_brace_only_is_rejected(self):
        data, ok = parse_json_safely("goodbye }")
        assert ok is False
        assert data is None

    def test_broken_json_with_trailing_comma_still_repairs(self):
        # Broken-JSON survivor path #2. Starts + ends structural.
        data, ok = parse_json_safely('{"a": 1, "b": 2,}')
        assert ok is True
        assert data == {"a": 1, "b": 2}

    def test_broken_json_single_quotes_still_repairs(self):
        data, ok = parse_json_safely("{'a': 1, 'b': 2}")
        assert ok is True
        assert data == {"a": 1, "b": 2}

    # --- Boundaries the pre-fix code already handled — keep them green ---

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
    """Full extractor regression — this is the surface the FE dropdown
    reads. Pre-fix, a prose cell yielded fake `column.<fragment>` paths."""

    def test_prose_output_yields_no_derived_paths(self):
        # The exact category of value that used to produce the ticket's
        # `llm_test_2.return_reason}}` mapping — a plain-text LLM answer
        # that happens to contain quotes and commas.
        result = extract_derived_variables_from_output(
            output=(
                'To create a new template, click "Save Template," and then '
                '"Create new template." Set the model to gpt-4 and return '
                "the response in the tool schema."
            ),
            column_name="llm_test_2",
        )
        assert result["is_json"] is False
        assert result["paths"] == []
        assert result["full_variables"] == []
        assert result["schema"] == {}
        assert result["raw_sample"] is None

    def test_structured_json_output_still_yields_paths(self):
        # Guard against over-correction: real structured output must still
        # produce the expected `column.path` variables.
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
        # Broken-but-recognizable JSON (trailing comma) must still repair
        # into paths — the tolerant parser is retained for this case.
        result = extract_derived_variables_from_output(
            output='{"answer": "42", "score": 0.9,}',
            column_name="llm_test_2",
        )
        assert result["is_json"] is True
        assert set(result["paths"]) == {"answer", "score"}
