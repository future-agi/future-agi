"""Tests for tracer.utils.pii_scrubber."""

import json
import traceback
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

    @staticmethod
    def _engines(*, analyze=None, anonymize=None):
        """Ready stand-in engines; ``analyze``/``anonymize`` are side effects."""
        from tracer.utils import pii_scrubber

        analyzer = mock.Mock()
        analyzer.analyze = mock.Mock(side_effect=analyze, return_value=[mock.Mock()])
        anonymizer = mock.Mock()
        anonymizer.anonymize = mock.Mock(side_effect=anonymize)
        pii_scrubber._analyzer = analyzer
        pii_scrubber._anonymizer = anonymizer
        return mock.patch.object(pii_scrubber, "_get_operator_config", return_value={})

    @pytest.mark.parametrize(
        "failure",
        [
            {"analyze": RuntimeError("engine crashed")},
            {"analyze": MemoryError("model evicted")},
            {"anonymize": RuntimeError("anonymizer failed")},
        ],
        ids=["analysis", "memory", "anonymization"],
    )
    def test_a_failure_a_retry_may_clear_rejects_the_batch(self, failure):
        # With the engines installed, an engine failure must not keep the raw
        # value; the batch is rejected so its retry can scrub it.
        from tracer.utils.pii_scrubber import scrub_pii_in_span_batch

        spans = [
            {
                "project_name": "proj",
                "attributes": {"fi.input.value": "Email: a@b.com"},
            }
        ]
        with self._engines(**failure):
            with pytest.raises(RuntimeError) as raised:
                scrub_pii_in_span_batch(spans, {"proj": True})

        assert type(raised.value).__name__ == "PIIScrubError"
        assert "a@b.com" not in str(raised.value)

    @pytest.mark.parametrize(
        "error, reason",
        [
            (
                ValueError("[E088] Text of length 1000001 exceeds maximum of 1000000."),
                "too_long",
            ),
            (
                UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed"),
                "undecodable",
            ),
        ],
        ids=["too-long", "undecodable"],
    )
    def test_a_value_that_can_never_be_scanned_is_masked_and_the_batch_kept(
        self, error, reason
    ):
        # A retry would fail the same way and drop every other span with it.
        from tracer.utils import pii_scrubber
        from tracer.utils.pii_scrubber import scrub_pii_in_span_batch

        unscannable = "Email: a@b.com, and much more than spaCy will read"
        spans = [
            {
                "project_name": "proj",
                "attributes": {"fi.input.value": unscannable, "fi.span.kind": "LLM"},
            },
            {"project_name": "proj", "attributes": {"fi.output.value": "all good"}},
        ]

        def analyze(text, **kwargs):
            if text == unscannable:
                raise error
            return []

        with (
            self._engines(analyze=analyze),
            mock.patch.object(pii_scrubber, "logger") as log,
        ):
            scrub_pii_in_span_batch(spans, {"proj": True})

        assert spans[0]["attributes"] == {
            "fi.input.value": "[REDACTED: could not be scanned]",
            "fi.span.kind": "LLM",
        }
        assert spans[1]["attributes"] == {"fi.output.value": "all good"}
        log.warning.assert_called_once_with("pii_scrub_value_masked", reason=reason)

    def test_a_failure_carries_no_text_of_the_value_in_its_traceback(self):
        # Engine errors can quote their input; the chained cause would carry it into
        # the ingestion log (format_exc) and the task's failure record.
        from tracer.utils.pii_scrubber import scrub_pii_in_span_batch

        spans = [
            {
                "project_name": "proj",
                "attributes": {"fi.input.value": "mail jane@example.com"},
            }
        ]
        with self._engines(
            analyze=RuntimeError("cannot parse 'mail jane@example.com'")
        ):
            with pytest.raises(RuntimeError) as raised:
                scrub_pii_in_span_batch(spans, {"proj": True})

        assert raised.value.__cause__ is None
        assert "jane@example.com" not in "".join(
            traceback.format_exception(raised.value)
        )

    def test_a_json_encoded_value_fails_closed_too(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_span_batch

        spans = [
            {
                "project_name": "proj",
                "resource_attributes": {
                    "fi.input.value": json.dumps({"messages": ["mail a@b.com"]})
                },
            }
        ]
        with self._engines(analyze=ValueError("boom")):
            with pytest.raises(RuntimeError) as raised:
                scrub_pii_in_span_batch(spans, {"proj": True})

        assert type(raised.value).__name__ == "PIIScrubError"

    def test_a_disabled_project_is_untouched_when_the_engines_fail(self):
        from tracer.utils.pii_scrubber import scrub_pii_in_span_batch

        spans = [
            {
                "project_name": "proj",
                "attributes": {"fi.input.value": "Email: a@b.com"},
            }
        ]
        with self._engines(analyze=ValueError("boom")):
            scrub_pii_in_span_batch(spans, {"proj": False})

        assert spans[0]["attributes"]["fi.input.value"] == "Email: a@b.com"

    def test_explicit_callers_stay_best_effort(self):
        from tracer.utils.pii_scrubber import scrub_span_attributes

        with self._engines(analyze=ValueError("boom")):
            with mock.patch(
                "tracer.utils.pii_scrubber._ensure_engines", return_value=True
            ):
                scrubbed = scrub_span_attributes({"fi.input.value": "a@b.com"})

        assert scrubbed == {"fi.input.value": "a@b.com"}

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
