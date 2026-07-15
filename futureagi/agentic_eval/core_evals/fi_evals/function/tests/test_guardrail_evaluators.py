"""
Regression tests for guardrail evaluators that could previously fail open.

A guardrail that fails open is worse than one that errors: it returns a clean
verdict for content it never actually inspected, so nothing downstream signals
that the check did not run.

Two such defects are covered here:

1. ``regex_pii_detection`` matched ``detect_types`` against its pattern keys
   case-sensitively and never validated them. ``detect_types=["SSN"]`` — the
   capitalization used by the function's own docstring prose and output labels —
   selected zero patterns, so the scan ran no regexes at all and reported
   ``{"result": True, "reason": "No PII detected (checked: )"}`` over text
   containing a live SSN. Partial recognition (``["SSN", "email"]``) was worse
   still: it scanned only email and silently skipped the SSN.

2. ``_preprocess_strings`` split a keyword string on "," without dropping blank
   entries. A trailing comma ("foo,") produced an empty keyword, and since ""
   is a substring of every string, the verdict of ``contains_any`` /
   ``contains_none`` became independent of the text entirely.
"""

import pytest

from agentic_eval.core_evals.fi_evals.function.functions import (
    contains_all,
    contains_any,
    contains_none,
    regex_pii_detection,
)

PII_TEXT = "My SSN is 123-45-6789 and card 4111-1111-1111-1111"
CLEAN_TEXT = "The weather today is mild and there is nothing sensitive here."


class TestRegexPiiDetectionFailsClosed:
    """``regex_pii_detection`` must never report clean text it did not scan."""

    @pytest.mark.parametrize("detect_types", [["SSN"], ["Ssn"], ["  ssn  "]])
    def test_detect_types_are_case_and_whitespace_insensitive(self, detect_types):
        """Capitalized/padded type names select the pattern instead of nothing."""
        result = regex_pii_detection(PII_TEXT, detect_types=detect_types)
        assert result["result"] is False
        assert "SSN" in result["reason"]

    def test_partially_unsupported_detect_types_do_not_silently_narrow_the_scan(self):
        """An unknown type alongside a known one must not be dropped silently.

        Previously this scanned only ``email`` and reported the SSN as absent.
        """
        result = regex_pii_detection(PII_TEXT, detect_types=["ssn", "emial"])
        assert result["result"] is False
        assert "emial" in result["reason"]

    def test_unsupported_detect_types_fail_closed(self):
        result = regex_pii_detection(PII_TEXT, detect_types=["bogus"])
        assert result["result"] is False
        assert "Unsupported detect_types" in result["reason"]

    def test_empty_detect_types_list_never_reports_clean(self):
        """An empty selection scans nothing, so it must not return a pass."""
        result = regex_pii_detection(PII_TEXT, detect_types=["   "])
        assert result["result"] is False

    def test_json_scalar_detect_types_is_not_iterated_per_character(self):
        """'"ssn"' decodes to a str; it must be treated as one type, not 3 chars."""
        result = regex_pii_detection(PII_TEXT, detect_types='"ssn"')
        assert result["result"] is False
        assert "SSN" in result["reason"]

    def test_never_reports_no_pii_with_an_empty_checked_list(self):
        """The self-refuting 'No PII detected (checked: )' must be unreachable."""
        for detect_types in (["SSN"], ["bogus"], ["   "], [], "SSN"):
            result = regex_pii_detection(PII_TEXT, detect_types=detect_types)
            assert result["reason"] != "No PII detected (checked: )"
            assert not (result["result"] is True and "checked: )" in result["reason"])


class TestRegexPiiDetectionSupportedBehaviour:
    """Behaviour that predates the fix and must be preserved."""

    def test_lowercase_detect_types_still_work(self):
        result = regex_pii_detection(PII_TEXT, detect_types=["ssn"])
        assert result["result"] is False
        assert "SSN" in result["reason"]

    def test_default_scans_every_pattern(self):
        result = regex_pii_detection(PII_TEXT)
        assert result["result"] is False
        assert "SSN" in result["reason"]
        assert "Credit Card" in result["reason"]

    def test_clean_text_passes_and_reports_what_was_checked(self):
        result = regex_pii_detection(CLEAN_TEXT)
        assert result["result"] is True
        assert "ssn" in result["reason"]

    @pytest.mark.parametrize("detect_types", ["ssn,email", '["ssn"]', ["ssn", "email"]])
    def test_csv_json_and_list_inputs_are_all_accepted(self, detect_types):
        result = regex_pii_detection(PII_TEXT, detect_types=detect_types)
        assert result["result"] is False
        assert "SSN" in result["reason"]

    def test_empty_text_is_reported_clean(self):
        assert regex_pii_detection("   ")["result"] is True


class TestBlankKeywordsAreIgnored:
    """A blank keyword is a substring of every string and must be dropped."""

    def test_contains_none_verdict_depends_on_the_text(self):
        """Previously a trailing comma failed every row regardless of content."""
        assert contains_none("foo,", "totally unrelated text")["result"] is True
        assert contains_none("foo,", "this text has foo in it")["result"] is False

    def test_contains_any_verdict_depends_on_the_text(self):
        """Previously a trailing comma passed every row while enforcing nothing."""
        assert contains_any("foo,", "totally unrelated text")["result"] is False
        assert contains_any("foo,", "this text has foo in it")["result"] is True

    @pytest.mark.parametrize(
        "keywords", ["foo,", "foo, ", ",foo", "foo,,bar", " , foo"]
    )
    def test_blank_entries_never_leak_into_the_reason_string(self, keywords):
        result = contains_any(keywords, "unrelated")
        assert not result["reason"].rstrip().endswith(":")

    def test_string_form_agrees_with_list_form(self):
        """ "foo," and ["foo"] describe the same keyword set."""
        assert contains_any("foo,", "unrelated") == contains_any(["foo"], "unrelated")
        assert contains_none("foo,", "unrelated") == contains_none(["foo"], "unrelated")

    def test_all_blank_keywords_agree_with_the_empty_list(self):
        assert contains_none(",", "abc") == contains_none([], "abc")
        assert contains_any(",", "abc") == contains_any([], "abc")


class TestContainmentSupportedBehaviour:
    """Behaviour that predates the fix and must be preserved."""

    def test_contains_any_finds_a_present_keyword(self):
        assert contains_any("alpha,beta", "mentions beta here")["result"] is True

    def test_contains_any_rejects_absent_keywords(self):
        assert contains_any("alpha,beta", "mentions neither")["result"] is False

    def test_contains_none_detects_a_banned_word(self):
        assert contains_none("banned", "this is banned content")["result"] is False

    def test_contains_all_requires_every_keyword(self):
        assert contains_all("alpha,beta", "alpha and beta")["result"] is True
        result = contains_all("alpha,beta", "only alpha")
        assert result["result"] is False
        assert "beta" in result["reason"]

    def test_contains_all_is_unaffected_by_a_trailing_comma(self):
        """contains_all was incidentally immune; keep it that way."""
        assert contains_all("alpha,", "alpha is here")["result"] is True

    def test_matching_is_case_insensitive_by_default(self):
        assert contains_any("FOO", "lowercase foo")["result"] is True

    def test_case_sensitive_matching_is_respected(self):
        assert (
            contains_any("FOO", "lowercase foo", case_sensitive=True)["result"] is False
        )
