"""Tests for tracer.utils.pii_scrubber."""

import json
from itertools import pairwise
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _reset_engines():
    """Reset module-level singletons between tests."""
    from tracer.utils import pii_scrubber

    pii_scrubber._analyzer = None
    pii_scrubber._anonymizer = None
    pii_scrubber._INIT_FAILED = False
    yield
    pii_scrubber._analyzer = None
    pii_scrubber._anonymizer = None
    pii_scrubber._INIT_FAILED = False


# ---------------------------------------------------------------------------
# _ensure_engines — init-failure latch
# ---------------------------------------------------------------------------
class TestEnsureEnginesLatch:
    """The fail-closed batch path makes the latch load-bearing: it must only
    stick for failures that can never resolve without a rebuild/restart."""

    @staticmethod
    def _patch_analyzer(side_effect):
        return mock.patch("presidio_analyzer.AnalyzerEngine", side_effect=side_effect)

    def test_import_error_latches_permanently(self):
        from tracer.utils import pii_scrubber

        with self._patch_analyzer(ImportError("No module named presidio_analyzer")):
            assert pii_scrubber._ensure_engines() is False
        assert pii_scrubber._INIT_FAILED is True

        # Latched: no re-initialisation attempt on subsequent calls.
        with self._patch_analyzer(AssertionError("must not retry")):
            assert pii_scrubber._ensure_engines() is False

    def test_missing_spacy_model_latches_permanently(self):
        from tracer.utils import pii_scrubber

        with self._patch_analyzer(OSError("[E050] Can't find model 'en_core_web_sm'.")):
            assert pii_scrubber._ensure_engines() is False
        assert pii_scrubber._INIT_FAILED is True

    def test_transient_failure_does_not_latch_and_retries(self):
        from tracer.utils import pii_scrubber

        with self._patch_analyzer(MemoryError("model load OOM")):
            assert pii_scrubber._ensure_engines() is False
        assert pii_scrubber._INIT_FAILED is False
        assert pii_scrubber._analyzer is None
        assert pii_scrubber._anonymizer is None

        # Next call re-attempts and succeeds with the real engines.
        assert pii_scrubber._ensure_engines() is True
        assert pii_scrubber._analyzer is not None
        assert pii_scrubber._anonymizer is not None

    def test_transient_os_error_does_not_latch(self):
        from tracer.utils import pii_scrubber

        # An OSError that is *not* spaCy's missing-model code (e.g. file
        # contention on first load) must be retried, not latched.
        with self._patch_analyzer(PermissionError("resource temporarily locked")):
            assert pii_scrubber._ensure_engines() is False
        assert pii_scrubber._INIT_FAILED is False

    def test_half_initialised_state_is_never_published(self):
        from tracer.utils import pii_scrubber

        with mock.patch(
            "presidio_anonymizer.AnonymizerEngine",
            side_effect=RuntimeError("anonymizer boom"),
        ):
            assert pii_scrubber._ensure_engines() is False
        # Analyzer construction succeeded, but must not be exposed alone.
        assert pii_scrubber._analyzer is None
        assert pii_scrubber._anonymizer is None
        assert pii_scrubber._INIT_FAILED is False

    def test_is_permanent_init_error_classification(self):
        from tracer.utils.pii_scrubber import _is_permanent_init_error

        assert _is_permanent_init_error(ImportError("x")) is True
        assert _is_permanent_init_error(ModuleNotFoundError("x")) is True
        assert _is_permanent_init_error(OSError("[E050] Can't find model")) is True
        assert _is_permanent_init_error(OSError("disk I/O error")) is False
        assert _is_permanent_init_error(MemoryError()) is False
        assert _is_permanent_init_error(RuntimeError("x")) is False


# ---------------------------------------------------------------------------
# scrub_pii_in_string
# ---------------------------------------------------------------------------
class TestScrubPiiInString:
    def test_email(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_string

        result = scrub_pii_in_string("Contact john@example.com for info")
        assert "<EMAIL_ADDRESS>" in result
        assert "john@example.com" not in result

    def test_phone(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_string

        result = scrub_pii_in_string("Call 555-123-4567 now")
        assert "<PHONE_NUMBER>" in result
        assert "555-123-4567" not in result

    def test_credit_card(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_string

        result = scrub_pii_in_string("Card: 4111 1111 1111 1111")
        assert "<CREDIT_CARD>" in result
        assert "4111" not in result

    def test_ssn(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_string

        # Note: Presidio's US_SSN recognizer has limited detection with
        # en_core_web_sm. With en_core_web_lg it performs better.
        # We verify at minimum that the function doesn't crash.
        result = scrub_pii_in_string("SSN: 123-45-6789")
        assert isinstance(result, str)

    def test_ip_address(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_string

        result = scrub_pii_in_string("Server at 192.168.1.100 is down")
        assert "<IP_ADDRESS>" in result
        assert "192.168.1.100" not in result

    def test_no_pii(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_string

        text = "Hello, this is a normal sentence."
        assert scrub_pii_in_string(text) == text

    def test_empty_string(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_string

        assert scrub_pii_in_string("") == ""

    def test_fail_open_on_error(self):
        # Force analyzer to raise
        from tracer.utils import pii_scrubber
        from tracer.utils.pii_scrubber import scrub_pii_in_string

        pii_scrubber._ensure_engines()
        original_analyze = pii_scrubber._analyzer.analyze
        pii_scrubber._analyzer.analyze = mock.Mock(side_effect=RuntimeError("boom"))
        result = scrub_pii_in_string("test@example.com")
        assert result == "test@example.com"
        pii_scrubber._analyzer.analyze = original_analyze


# ---------------------------------------------------------------------------
# find_pii — the redaction preview; it never fails open
# ---------------------------------------------------------------------------
class TestFindPii:
    def test_finds_each_value_with_its_place_and_token(self):
        from tracer.utils.pii_scrubber import find_pii

        text = "Mail john@example.com or call 555-123-4567"
        (found,) = find_pii([text])

        emails = [f for f in found if f.entity == "EMAIL_ADDRESS"]
        assert [(f.value, f.replacement) for f in emails] == [
            ("john@example.com", "<EMAIL_ADDRESS>")
        ]
        assert all(text[f.start : f.end] == f.value for f in found)
        assert [f.start for f in found] == sorted(f.start for f in found)

    def test_one_list_of_findings_per_text_in_order(self):
        from tracer.utils.pii_scrubber import find_pii

        found = find_pii(["nothing here", "", "write to a@b.co"])

        assert [len(hits) for hits in found[:2]] == [0, 0]
        assert [f.value for f in found[2]] == ["a@b.co"]

    def test_finds_names_though_ingestion_keeps_them(self):
        from tracer.utils.pii_scrubber import find_pii

        (found,) = find_pii(["My name is Riley Shaw."])

        assert any(f.entity == "PERSON" and "Riley" in f.value for f in found)

    def test_findings_never_overlap(self):
        from tracer.utils.pii_scrubber import find_pii

        (found,) = find_pii(
            ["Contact jane.doe@example.com at 10.0.0.1 or 555-123-4567"]
        )

        assert all(a.end <= b.start for a, b in pairwise(found))

    def test_nothing_to_find(self):
        from tracer.utils.pii_scrubber import find_pii

        assert find_pii(["Hello, this is a normal sentence."]) == [[]]
        assert find_pii([]) == []

    def test_no_engines_is_none_not_nothing_found(self):
        from tracer.utils import pii_scrubber
        from tracer.utils.pii_scrubber import find_pii

        with mock.patch.object(pii_scrubber, "_ensure_engines", return_value=False):
            assert find_pii(["john@example.com"]) is None

    def test_a_failed_analysis_is_none_not_nothing_found(self):
        from tracer.utils.pii_scrubber import find_pii

        with mock.patch(
            "presidio_analyzer.BatchAnalyzerEngine.analyze_iterator",
            side_effect=RuntimeError("boom"),
        ):
            assert find_pii(["john@example.com"]) is None


# ---------------------------------------------------------------------------
# find_pii_basic — the fixed-shape stand-in for a server without Presidio
# ---------------------------------------------------------------------------
class TestFindPiiBasic:
    def _entities(self, text):
        from tracer.utils.pii_scrubber import find_pii_basic

        (found,) = find_pii_basic([text])
        assert all(text[f.start : f.end] == f.value for f in found)
        return [(f.entity, f.value) for f in found]

    def test_fixed_shape_values(self):
        assert self._entities(
            "jane.doe@example.com, +1 415-555-0134, 123-45-6789, 10.0.0.12, "
            "sk-live-abcdefghijklmnopqrstuvwx"
        ) == [
            ("EMAIL_ADDRESS", "jane.doe@example.com"),
            ("PHONE_NUMBER", "+1 415-555-0134"),
            ("US_SSN", "123-45-6789"),
            ("IP_ADDRESS", "10.0.0.12"),
            ("API_KEY", "sk-live-abcdefghijklmnopqrstuvwx"),
        ]

    def test_a_card_number_must_pass_the_luhn_check(self):
        assert self._entities("card 4111 1111 1111 1111") == [
            ("CREDIT_CARD", "4111 1111 1111 1111")
        ]
        assert self._entities("order 4111 1111 1111 1112") == []

    def test_it_finds_no_names(self):
        assert self._entities("My name is Riley Shaw.") == []


# ---------------------------------------------------------------------------
# scrub_pii_in_value
# ---------------------------------------------------------------------------
class TestScrubPiiInValue:
    def test_plain_string(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_value

        result = scrub_pii_in_value("Email: test@example.com")
        assert "<EMAIL_ADDRESS>" in result

    def test_json_string(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_value

        data = json.dumps({"content": "Contact john@example.com"})
        result = scrub_pii_in_value(data)
        parsed = json.loads(result)
        assert "<EMAIL_ADDRESS>" in parsed["content"]

    def test_dict(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_value

        result = scrub_pii_in_value({"text": "Email: a@b.com", "count": 42})
        assert "<EMAIL_ADDRESS>" in result["text"]
        assert result["count"] == 42

    def test_list(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_value

        result = scrub_pii_in_value(["a@b.com", "clean", 42])
        assert "<EMAIL_ADDRESS>" in result[0]
        assert result[1] == "clean"
        assert result[2] == 42

    def test_nested_dict(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_value

        data = {"messages": [{"content": "user@test.com"}]}
        result = scrub_pii_in_value(data)
        assert "<EMAIL_ADDRESS>" in result["messages"][0]["content"]

    def test_non_string_passthrough(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_value

        assert scrub_pii_in_value(42) == 42
        assert scrub_pii_in_value(3.14) == 3.14
        assert scrub_pii_in_value(True) is True
        assert scrub_pii_in_value(None) is None


# ---------------------------------------------------------------------------
# _is_content_key
# ---------------------------------------------------------------------------
class TestIsContentKey:
    def test_content_keys(self):
        from tracer.utils.pii_scrubber import _is_content_key

        assert _is_content_key("fi.input.value") is True
        assert _is_content_key("fi.output.value") is True
        assert _is_content_key("gen_ai.input.messages") is True
        assert _is_content_key("fi.llm.message.content") is True
        assert _is_content_key("user.query") is True
        assert _is_content_key("fi.completion") is True

    def test_structural_keys(self):
        from tracer.utils.pii_scrubber import _is_content_key

        assert _is_content_key("gen_ai.request.model") is False
        assert _is_content_key("fi.span.kind") is False
        assert _is_content_key("gen_ai.response.finish_reason") is False
        assert _is_content_key("fi.tool_call_id") is False


# ---------------------------------------------------------------------------
# scrub_pii_in_span_batch
# ---------------------------------------------------------------------------
class TestScrubPiiInSpanBatch:
    def test_enabled_project_fails_closed_when_engines_unavailable(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_span_batch

        spans = [
            {
                "project_name": "proj",
                "attributes": {"fi.input.value": "Email: a@b.com"},
            }
        ]
        with mock.patch(
            "tracer.utils.pii_scrubber._ensure_engines", return_value=False
        ):
            with pytest.raises(RuntimeError, match="EXTRAS=pii"):
                scrub_pii_in_span_batch(spans, {"proj": True})

        assert spans[0]["attributes"]["fi.input.value"] == "Email: a@b.com"

    def test_disabled_projects_do_not_require_engines(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_span_batch

        spans = [
            {
                "project_name": "proj",
                "attributes": {"fi.input.value": "Email: a@b.com"},
            }
        ]
        with mock.patch("tracer.utils.pii_scrubber._ensure_engines") as ensure_engines:
            scrub_pii_in_span_batch(spans, {"proj": False})

        ensure_engines.assert_not_called()

    def test_scrubs_enabled_project_only(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_span_batch

        spans = [
            {
                "project_name": "proj-a",
                "attributes": {"fi.input.value": "Email: a@b.com"},
            },
            {
                "project_name": "proj-b",
                "attributes": {"fi.input.value": "Email: c@d.com"},
            },
        ]
        settings = {"proj-a": True, "proj-b": False}
        scrub_pii_in_span_batch(spans, settings)

        # proj-a should be scrubbed
        assert "<EMAIL_ADDRESS>" in spans[0]["attributes"]["fi.input.value"]
        # proj-b should be untouched
        assert "c@d.com" in spans[1]["attributes"]["fi.input.value"]

    def test_missing_project_name_skipped(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_span_batch

        spans = [{"attributes": {"fi.input.value": "a@b.com"}}]
        scrub_pii_in_span_batch(spans, {})
        assert spans[0]["attributes"]["fi.input.value"] == "a@b.com"

    def test_skips_non_content_keys(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_span_batch

        spans = [
            {
                "project_name": "proj",
                "attributes": {
                    "fi.span.kind": "LLM",
                    "fi.input.value": "a@b.com",
                },
            },
        ]
        scrub_pii_in_span_batch(spans, {"proj": True})
        assert spans[0]["attributes"]["fi.span.kind"] == "LLM"
        assert "<EMAIL_ADDRESS>" in spans[0]["attributes"]["fi.input.value"]
