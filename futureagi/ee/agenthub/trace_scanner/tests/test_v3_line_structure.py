"""The scanner must be able to see line structure in what it judges.

`_v3_plain` normalised whitespace with a single `\\s+` → " " collapse, which also
deleted every newline. Two things depended on the newlines it removed:

  * the taxonomy asks the model to judge formatting, and
  * the V8 prompt asks it to quote VERBATIM from the trace.

Neither is achievable on text whose line breaks were stripped on the way in. On
a 2,107-trace corpus this produced the single largest class of false positive —
issues asserting that fields had been "merged onto one line" against outputs
that were correctly formatted. The model was reporting the input faithfully; the
input was wrong.

These tests pin the property that matters (line breaks survive) without pinning
the exact whitespace policy, so horizontal normalisation stays free to change.
"""

from ee.agenthub.trace_scanner.compress import _v3_plain


class TestLineStructureSurvives:
    def test_newlines_are_preserved(self):
        """The regression itself: a multi-line answer must not arrive as one line."""
        out = _v3_plain("Heading: Q3 Review\nSummary: revenue up\nLanguage: en", 500)
        assert out.count("\n") == 2, f"line breaks were destroyed: {out!r}"
        assert "Heading: Q3 Review" in out

    def test_a_correctly_formatted_answer_is_not_flattened(self):
        """The exact shape the false positives were raised against."""
        raw = "** Category: Poor\n** Reasoning: the response omits the cited figure"
        out = _v3_plain(raw, 500)
        assert "\n" in out, "an answer with real line breaks was flattened to one line"
        assert out.splitlines() == [
            "** Category: Poor",
            "** Reasoning: the response omits the cited figure",
        ]

    def test_horizontal_whitespace_still_collapses(self):
        """Structure is preserved; indentation noise is not."""
        assert _v3_plain("a    b\t\tc", 500) == "a b c"
        assert _v3_plain("line one   \n   line two", 500) == "line one\nline two"

    def test_blank_line_runs_are_capped(self):
        assert _v3_plain("a\n\n\n\n\nb", 500) == "a\n\nb"

    def test_truncation_prefers_a_line_boundary(self):
        """A truncated tail must not itself read as a run-together line."""
        text = "\n".join(f"field {i}: value" for i in range(20))
        out = _v3_plain(text, 120)
        content, marker = out.split("⟨", 1)
        assert "truncated by the scanner" in marker
        assert not content.endswith("field"), "cut mid-line near a boundary"
        assert len(content) <= 120, "content exceeded its budget"

    def test_truncation_says_who_truncated(self):
        """An ellipsis reads as the agent stopping mid-sentence — which is a
        reportable failure. Our own budget running out must never look like that.
        """
        out = _v3_plain("x" * 500, 100)
        assert "truncated by the scanner" in out
        assert not out.endswith("…"), "ambiguous marker: reads as an incomplete answer"
        assert "400 chars omitted" in out

    def test_empty_and_non_string_inputs(self):
        assert _v3_plain("", 100) == ""
        assert _v3_plain(None, 100) == ""
        assert _v3_plain(123, 100) == "123"

    def test_single_line_input_is_unchanged(self):
        assert _v3_plain("just one line", 500) == "just one line"
