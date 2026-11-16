"""Tests for tracer.utils.pii_scrubber."""

import contextlib
import json
import traceback
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
        from tracer.utils.pii_scrubber import PIIScrubError, scrub_pii_in_span_batch

        spans = [
            {
                "project_name": "proj",
                "attributes": {"fi.input.value": "Email: a@b.com"},
            }
        ]
        with mock.patch(
            "tracer.utils.pii_scrubber._ensure_engines", return_value=False
        ):
            # A PIIScrubError, so trace ingestion retries the batch.
            with pytest.raises(PIIScrubError, match="EXTRAS=pii"):
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


# ---------------------------------------------------------------------------
# bulk_create_observation_span_task — a batch whose scrub failed
# ---------------------------------------------------------------------------
class TestUnscrubbedBatchRetry:
    """The task runs once (max_retries=0) and deletes its payload on read, so it
    stores a rejected batch again and retries it, a bounded number of times."""

    RAW = "Email: a@b.com"

    def _run(self, *, attempt=0, store=None, enqueue=None):
        from tracer.utils import trace_ingestion
        from tracer.utils.pii_scrubber import PIIScrubError

        spans = [{"project_name": "proj", "attributes": {"fi.input.value": self.RAW}}]
        task = trace_ingestion.bulk_create_observation_span_task
        storage = trace_ingestion.payload_storage
        with (
            mock.patch("ee.usage.deployment.DeploymentMode.is_oss", return_value=True),
            # The scrub runs before the first write; no database is needed.
            mock.patch.object(
                trace_ingestion.transaction, "atomic", contextlib.nullcontext
            ),
            mock.patch.object(storage, "retrieve", return_value=self.RAW.encode()),
            mock.patch.object(
                storage, "store", side_effect=store, return_value="payload:retry"
            ) as stored,
            mock.patch.object(trace_ingestion, "deserialize_trace_payload"),
            mock.patch.object(
                trace_ingestion, "_parse_otel_request", return_value=spans
            ),
            mock.patch.object(trace_ingestion, "normalize_span_attributes"),
            mock.patch.object(
                trace_ingestion,
                "get_pii_settings_for_projects",
                return_value={"proj": True},
            ),
            mock.patch.object(
                trace_ingestion,
                "scrub_pii_in_span_batch",
                side_effect=PIIScrubError("PII redaction failed (MemoryError)."),
            ),
            mock.patch.object(
                trace_ingestion, "bulk_convert_otel_spans_to_observation_spans"
            ) as converted,
            mock.patch.object(task, "apply_async", side_effect=enqueue) as enqueued,
            mock.patch.object(trace_ingestion, "logger") as log,
        ):
            # The task body without its Temporal wrapper, whose connection cleanup
            # touches the database when an earlier test left a connection open.
            task._original_func(
                "payload:in", "org", "user", "ws", "protobuf", pii_scrub_attempt=attempt
            )

        converted.assert_not_called()
        assert self.RAW not in str(log.mock_calls)
        log.exception.assert_not_called()
        log.error.assert_not_called()
        return stored, enqueued, log

    def test_a_rejected_batch_is_stored_again_and_retried_later(self):
        from tracer.utils.trace_ingestion import PII_SCRUB_RETRY_DELAYS

        stored, enqueued, log = self._run(attempt=1)

        stored.assert_called_once_with(self.RAW.encode())
        enqueued.assert_called_once_with(
            args=["payload:retry", "org", "user", "ws", "protobuf"],
            kwargs={"pii_scrub_attempt": 2},
            task_id="payload:in-pii-retry",
            start_delay=PII_SCRUB_RETRY_DELAYS[1],
        )
        assert log.warning.call_args.args == ("pii_scrub_batch_retried",)

    def test_the_last_retry_drops_the_batch_and_says_so(self):
        from tracer.utils.trace_ingestion import PII_SCRUB_RETRY_DELAYS

        stored, enqueued, log = self._run(attempt=len(PII_SCRUB_RETRY_DELAYS))

        stored.assert_not_called()
        enqueued.assert_not_called()
        assert log.warning.call_args.args == ("pii_scrub_batch_dropped",)

    @pytest.mark.parametrize(
        "failure",
        [
            {"store": RuntimeError("Redis is not available")},
            {"enqueue": RuntimeError("Temporal is not reachable")},
        ],
        ids=["store", "enqueue"],
    )
    def test_a_retry_that_cannot_be_enqueued_drops_the_batch_and_says_so(self, failure):
        _stored, _enqueued, log = self._run(**failure)

        assert log.warning.call_args.args == ("pii_scrub_batch_dropped",)
        assert "RuntimeError" in log.warning.call_args.kwargs["error"]

    def test_a_retry_store_that_redis_refuses_sends_no_raw_byte_to_sentry(
        self, monkeypatch
    ):
        """Sentry attaches frame variables to error events; the re-store holds the batch."""
        import logging

        import sentry_sdk
        from redis.exceptions import RedisError
        from sentry_sdk.integrations.logging import LoggingIntegration

        from tfc.logging import sentry as project_sentry
        from tfc.utils.payload_storage import get_payload_storage
        from tracer.utils import trace_ingestion
        from tracer.utils.pii_scrubber import PIIScrubError

        sentinel = b"raw-batch-sentinel-5c1e jane@acme.com"
        events = []
        scrub = project_sentry._get_before_send()  # the project's own scrubber

        def capture(event, hint):
            events.append(scrub(event, hint))
            return None  # nothing leaves the test

        redis = mock.Mock()
        redis.setex.side_effect = RedisError("OOM command not allowed")
        storage = get_payload_storage()
        monkeypatch.setattr(storage, "_redis_available", True)
        monkeypatch.setattr(storage, "_redis_client", redis)
        spans = [{"project_name": "proj", "attributes": {"fi.input.value": "x"}}]
        task = trace_ingestion.bulk_create_observation_span_task
        sentry_sdk.init(
            dsn="https://public@sentry.invalid/1",
            before_send=capture,
            include_local_variables=True,  # as tfc/logging/sentry.py sets them
            attach_stacktrace=True,
            default_integrations=False,
            integrations=[
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
            ],
        )
        try:
            with (
                mock.patch(
                    "ee.usage.deployment.DeploymentMode.is_oss", return_value=True
                ),
                mock.patch.object(
                    trace_ingestion.transaction, "atomic", contextlib.nullcontext
                ),
                mock.patch.object(
                    trace_ingestion.payload_storage, "retrieve", return_value=sentinel
                ),
                mock.patch.object(trace_ingestion, "deserialize_trace_payload"),
                mock.patch.object(
                    trace_ingestion, "_parse_otel_request", return_value=spans
                ),
                mock.patch.object(trace_ingestion, "normalize_span_attributes"),
                mock.patch.object(
                    trace_ingestion,
                    "get_pii_settings_for_projects",
                    return_value={"proj": True},
                ),
                mock.patch.object(
                    trace_ingestion,
                    "scrub_pii_in_span_batch",
                    side_effect=PIIScrubError("PII redaction failed (MemoryError)."),
                ),
                mock.patch.object(task, "apply_async") as enqueued,
            ):
                task._original_func("payload:in", "org", "user", "ws", "protobuf")
        finally:
            sentry_sdk.init()  # no DSN: no client

        redis.setex.assert_called_once()  # the re-store reached Redis and failed
        enqueued.assert_not_called()
        assert b"raw-batch-sentinel" not in json.dumps(events, default=str).encode()
