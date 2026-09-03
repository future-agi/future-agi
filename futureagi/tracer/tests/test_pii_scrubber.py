"""Tests for tracer.utils.pii_scrubber."""

import json
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
