"""A scan that did not finish must never be recorded as a clean trace.

`MAX_TOKENS` is spent on thinking BEFORE the JSON body — measured 945-3648 completion
tokens against 200-400 tokens of visible output, i.e. 70-90% thoughts. At the old cap
of 6144 the richest traces ran out mid-JSON. Reproduced on the 2026-08-18 prod corpus:
one 1358-span trace returned `finish_reason="length"` at 6144 and parsed to nothing;
the same prompt at a higher cap finished in 6657 tokens and parsed fine.

The damage was not the truncation, it was what happened next. `_parse_response`
returned `{}` on a JSON failure, which is not an exception, so the scan fell through
to `has_issues=False, error=None, retryable=False` — and `write_scan_results` persists
that as COMPLETED while `filter_already_scanned` treats any persisted row as terminal.
A truncated scan became a permanent "this trace is clean" verdict, and it landed
preferentially on the traces carrying the most defects: the flash-arm silent failures
were the corpus's #1, #2 and #3 traces by span count.

This is the same contract `66019e53b` established for transport errors — the scan did
not happen, so the trace is unknown rather than clean. Only the parse path was missed.
"""

from unittest.mock import patch

from ee.agenthub.trace_scanner.scanner import TraceScanner


def _trace(tid="11111111-1111-1111-1111-111111111111"):
    return {
        "trace_id": tid,
        "spans": [
            {
                "span_id": "s1",
                "span_name": "agent",
                "duration": "PT1S",
                "status_code": "Ok",
                "span_attributes": {
                    "span.kind": "AGENT",
                    "input.value": "what is my refund status",
                    "output.value": "let me check",
                },
                "child_spans": [],
            }
        ],
    }


def _scan_with_raw(raw):
    """Run one scan with the gateway stubbed to return `raw` as the body."""
    scanner = TraceScanner()
    with patch.object(TraceScanner, "_invoke_llm", return_value=raw):
        return scanner.scan_batch([_trace()])


class TestAnUnparseableResponseIsNotAVerdict:
    def test_truncated_json_is_retryable_not_clean(self):
        """The exact shape of a cut-off response: valid prefix, no closing brace."""
        truncated = '{"dimensions": {"goal": {"evidence": "let me check", "verdict": "FA'
        results = _scan_with_raw(truncated)
        assert len(results) == 1
        r = results[0]
        assert r.retryable is True, (
            "a truncated scan was recorded as a finished one; write_scan_results will "
            "persist it and filter_already_scanned will never revisit the trace"
        )
        assert r.error, "a truncated scan must carry an error so it is not read as clean"

    def test_prose_instead_of_json_is_retryable(self):
        """Observed in production: the model runs out mid-thought and returns narration."""
        results = _scan_with_raw("Wait! Look at span 1.1.10.1 — the tool returned 504")
        assert results[0].retryable is True
        assert results[0].error

    def test_empty_body_is_retryable(self):
        """The shape a response takes when thinking consumed the entire budget."""
        assert _scan_with_raw("")[0].retryable is True

    def test_a_clean_trace_is_still_reported_clean(self):
        """The fix must not turn a genuine no-issues verdict into a retry."""
        clean = (
            '{"dimensions": {"goal": {"evidence": "let me check", "verdict": "PASS"}}, '
            '"issues": [], "key_moments": []}'
        )
        r = _scan_with_raw(clean)[0]
        assert r.retryable is False, "a well-formed clean verdict must not be retried"
        assert r.has_issues is False
        assert not r.error


class TestOutputBudget:
    def test_cap_leaves_room_for_json_after_thinking(self):
        """6144 was below the measured requirement of one real prod trace (6657)."""
        assert TraceScanner.MAX_TOKENS >= 16_000, (
            "the cap must clear the measured thinking overhead; verify.py settled on "
            "16k for the identical failure mode"
        )
