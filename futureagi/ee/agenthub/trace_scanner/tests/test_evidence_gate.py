"""A failure the model cannot quote is a failure that did not happen.

The V8 prompt already states this rule — "If you cannot quote the required
evidence, the verdict for that dimension is PASS. An unquotable failure is a
hallucinated failure" — but enforced it on the honour system. The evidence the
model returned was never checked against what it was shown, and was then thrown
away.

Auditing a 2,107-trace corpus found roughly one flagged trace in three did not
exhibit the issue its row claimed, including rows asserting an agent had
fabricated figures that were present verbatim in the tool output. Those claims
fail this gate deterministically, with no model change.

The gate is fail-closed on purpose: a dropped real issue costs recall, which is
recoverable, while a surfaced invented one costs the user's trust in every other
row, which is not.
"""

import pytest

from ee.agenthub.trace_scanner.scanner import (
    TraceScanner,
    _completion_claim_holds,
    _trace_has_captured_content,
    _evidence_is_quotable,
    _norm_for_match,
)

TRACE = _norm_for_match(
    "User: how long did the export take?\n"
    "Tool generate_insight returned: total 80.27 seconds, 9.21 seconds average\n"
    "Agent: The export took ~80 seconds, averaging ~9 seconds per file."
)


class TestEvidenceIsQuotable:
    def test_exact_quote_passes(self):
        assert _evidence_is_quotable("total 80.27 seconds, 9.21 seconds average", TRACE)

    def test_quote_with_different_whitespace_and_case_passes(self):
        assert _evidence_is_quotable("Total 80.27   Seconds, 9.21 Seconds Average", TRACE)

    def test_trimmed_trailing_clause_passes(self):
        """Models routinely quote the head of a sentence and drop the tail."""
        assert _evidence_is_quotable(
            "The export took ~80 seconds, averaging ~9 seconds per file. "
            "This was reported to the user in the final response.",
            TRACE,
        )

    def test_invented_quote_is_rejected(self):
        """The exact shape of the audited false positive."""
        assert not _evidence_is_quotable(
            "Agent stated the export took 45 minutes with no supporting tool output",
            TRACE,
        )

    def test_short_evidence_is_rejected(self):
        """Fragments this small match by accident and prove nothing."""
        assert not _evidence_is_quotable("error", TRACE)
        assert not _evidence_is_quotable("the user", TRACE)

    def test_empty_evidence_is_rejected(self):
        for empty in ("", None, "   "):
            assert not _evidence_is_quotable(empty, TRACE)


class TestFailedDimensionsNeedEvidence:
    @staticmethod
    def _parsed(evidence):
        return {
            "dimensions": {
                "grounding": {"evidence": evidence, "verdict": "FAIL"},
                "goal": {"evidence": "", "verdict": "PASS"},
            },
            "issues": [
                {"dim": "grounding", "cat": "Tool Output Misinterpretation",
                 "conf": "H", "brief": "agent fabricated the timing figures"}
            ],
        }

    def test_fail_with_real_evidence_becomes_an_issue(self):
        out = TraceScanner._v8_to_trace_output(
            self._parsed("total 80.27 seconds, 9.21 seconds average"),
            "user asked. tool returned total 80.27 seconds, 9.21 seconds average.",
        )
        assert len(out["issues"]) == 1
        assert out["issues"][0]["cat"] == "Tool Output Misinterpretation"

    def test_fail_with_invented_evidence_is_dropped(self):
        out = TraceScanner._v8_to_trace_output(
            self._parsed("agent claimed the export took 45 minutes"),
            "user asked. tool returned total 80.27 seconds, 9.21 seconds average.",
        )
        assert out["issues"] == [], "an unquotable failure was surfaced to the user"

    def test_fail_with_no_evidence_is_dropped(self):
        out = TraceScanner._v8_to_trace_output(
            self._parsed(""), "user asked. tool returned 80.27 seconds."
        )
        assert out["issues"] == []

    def test_gate_is_skipped_when_the_input_is_unknown(self):
        """No seen_text means we cannot judge; do not silently drop everything."""
        out = TraceScanner._v8_to_trace_output(self._parsed("anything at all here"), "")
        assert len(out["issues"]) == 1

    def test_passing_dimensions_never_produce_issues(self):
        parsed = {
            "dimensions": {"goal": {"evidence": "", "verdict": "PASS"}},
            "issues": [{"dim": "goal", "cat": "Goal Deviation", "brief": "x"}],
        }
        assert TraceScanner._v8_to_trace_output(parsed, "some trace text here")["issues"] == []


class TestBreadcrumbsDoNotInventQuotes:
    """A breadcrumb's `verbatim` field is rendered to the user as a quote from
    the trace. When recovery failed it returned the model's own paraphrase, so
    the UI pointed engineers at words nobody said — 161 of 278 breadcrumbs on the
    audited corpus. No quote is honest; an invented one is not.
    """

    def test_unmatchable_excerpt_yields_no_quote(self):
        from ee.agenthub.trace_scanner.compress import recover_verbatim

        assert recover_verbatim(
            "agent refused escalation request entirely",
            "The user asked about billing. The agent transferred them to support.",
        ) == ""

    def test_matchable_excerpt_returns_the_real_sentence(self):
        from ee.agenthub.trace_scanner.compress import recover_verbatim

        raw = "The user asked about billing. The agent transferred them to support."
        got = recover_verbatim("user asked billing", raw)
        assert got, "a recoverable quote was dropped"
        assert got in raw, f"returned text is not from the trace: {got!r}"


class TestLowConfidenceIsWithheldNotForced:
    """The prompt used to forbid L ("drop anything you'd rate L"), so a model
    that was unsure had nowhere to put that except an H or M assertion. On the
    audited corpus every single one of 405 issues came back H. Giving L back and
    withholding it from the feed converts forced false certainty into a signal we
    keep for recall work.
    """

    @staticmethod
    def _parsed(conf):
        return {
            "dimensions": {"goal": {"evidence": "the agent never answered", "verdict": "FAIL"}},
            "issues": [{"dim": "goal", "cat": "Goal Deviation",
                        "brief": "did not answer the question", "conf": conf}],
        }

    SEEN = "user asked a question. the agent never answered it."

    def test_low_confidence_is_not_surfaced(self):
        out = TraceScanner._v8_to_trace_output(self._parsed("L"), self.SEEN)
        assert out["issues"] == [], "an issue the model itself could not establish was shown"

    @pytest.mark.parametrize("conf", ["H", "M"])
    def test_established_findings_still_surface(self, conf):
        out = TraceScanner._v8_to_trace_output(self._parsed(conf), self.SEEN)
        assert len(out["issues"]) == 1
        assert out["issues"][0]["conf"] == conf

    def test_unknown_confidence_defaults_to_medium_not_dropped(self):
        out = TraceScanner._v8_to_trace_output(self._parsed("banana"), self.SEEN)
        assert len(out["issues"]) == 1
        assert out["issues"][0]["conf"] == "M"

    def test_lowercase_l_is_still_withheld(self):
        assert TraceScanner._v8_to_trace_output(self._parsed("l"), self.SEEN)["issues"] == []


class TestAbsenceClaimsAreVerifiedNotQuoted:
    """"The agent returned nothing" is unquotable exactly when it is TRUE.

    Demanding a quote for the completion dimension blinded it: 94 of 124 gate
    drops on a 350-trace run landed on this one dimension. So the model names
    the span that carries (or should have carried) the final response, and the
    claim is checked in code against that span's real output.
    """

    def test_empty_response_upholds_the_claim(self):
        assert _completion_claim_holds("")
        assert _completion_claim_holds("   ")
        assert _completion_claim_holds(None)

    def test_response_cut_mid_sentence_upholds_the_claim(self):
        assert _completion_claim_holds("The total for your three invoices comes to")

    def test_complete_response_refutes_the_claim(self):
        assert not _completion_claim_holds(
            "Your three invoices total $412.90, due on the 14th."
        )

    def test_our_own_truncation_never_reads_as_the_agent_stopping(self):
        """The scanner's budget running out is not the agent giving up."""
        from ee.agenthub.trace_scanner.compress import SCANNER_TRUNCATION_MARK

        cut = (
            "Here is the full breakdown of every invoice you asked about, "
            "starting with the oldest" + SCANNER_TRUNCATION_MARK + ", 900 chars omitted⟩"
        )
        assert not _completion_claim_holds(cut), "our truncation was reported as an incomplete answer"

    @staticmethod
    def _parsed_completion(span):
        dim = {"evidence": "", "verdict": "FAIL"}
        if span is not None:
            dim["span"] = span
        return {
            "dimensions": {"completion": dim},
            "issues": [{"dim": "completion", "cat": "Incomplete Response",
                        "brief": "agent returned nothing", "conf": "H"}],
        }

    @staticmethod
    def _trace(root_out):
        """Root span plus one child with real output — the tool result that
        must never be allowed to stand in for the missing final answer."""
        return {"trace_id": "t", "spans": [{
            "span_id": "root", "span_name": "agent",
            "span_attributes": {"input.value": "list my invoices",
                                **({"output.value": root_out} if root_out else {})},
            "child_spans": [{
                "span_id": "tool-1", "span_name": "fetch_invoices",
                "span_attributes": {"output.value": '{"invoices": [412.90]}'},
                "child_spans": [],
            }],
        }]}

    def test_fail_naming_a_span_with_no_output_survives(self):
        """The genuine failure: the agent produced nothing, and the model names
        the span that should have carried the answer."""
        out = TraceScanner._v8_to_trace_output(
            self._parsed_completion("root"), "seen", self._trace(""),
        )
        assert len(out["issues"]) == 1, "a real empty-output failure was gated away"

    def test_fail_naming_a_span_with_a_complete_answer_is_dropped(self):
        out = TraceScanner._v8_to_trace_output(
            self._parsed_completion("root"), "seen",
            self._trace("Your three invoices total $412.90, due on the 14th."),
        )
        assert out["issues"] == []

    def test_fail_without_a_span_id_is_dropped(self):
        """An absence claim that names nothing cannot be verified against
        anything, and an unverifiable claim does not reach the user."""
        out = TraceScanner._v8_to_trace_output(
            self._parsed_completion(None), "seen", self._trace(""),
        )
        assert out["issues"] == []

    def test_fail_with_an_invented_span_id_is_dropped(self):
        out = TraceScanner._v8_to_trace_output(
            self._parsed_completion("no-such-span"), "seen", self._trace(""),
        )
        assert out["issues"] == []

    def test_empty_content_inside_a_provider_envelope_still_reads_as_empty(self):
        """The raw output is envelope JSON ending in "}" — textually substantial
        and cleanly terminated, yet the agent said nothing. The gate must judge
        the unwrapped response, not the wrapper."""
        envelope = '{"choices": [{"message": {"content": null}}], "usage": {}}'
        out = TraceScanner._v8_to_trace_output(
            self._parsed_completion("root"), "seen", self._trace(envelope),
        )
        assert len(out["issues"]) == 1, "an empty answer hid inside its envelope"


class TestSilentAgentIsNotConfusedWithSilentRecorder:
    """A trace that captured nothing cannot prove the agent produced nothing.

    26 of 193 claims surfaced on the production corpus were "Incomplete
    Response" against traces holding zero input or output — spans present,
    values never recorded. That is an instrumentation gap being reported to the
    user as their agent failing.
    """

    PARSED = {
        "dimensions": {"completion": {"evidence": "", "verdict": "FAIL", "span": "root"}},
        "issues": [{"dim": "completion", "cat": "Incomplete Response",
                    "brief": "agent returned nothing", "conf": "H"}],
    }
    TRACE = {"trace_id": "t", "spans": [{
        "span_id": "root", "span_name": "agent",
        "span_attributes": {"input.value": "list my invoices"},
        "child_spans": [],
    }]}

    def test_claim_is_dropped_when_the_trace_captured_nothing(self):
        out = TraceScanner._v8_to_trace_output(
            self.PARSED, "some trace text", self.TRACE, trace_has_content=False
        )
        assert out["issues"] == [], "our own recording gap was blamed on the agent"

    def test_claim_survives_when_the_trace_captured_something(self):
        out = TraceScanner._v8_to_trace_output(
            self.PARSED, "some trace text", self.TRACE, trace_has_content=True
        )
        assert len(out["issues"]) == 1, "a real empty-output failure was gated away"

    def test_content_is_detected_through_nested_spans(self):
        """Content usually lives on a child span, so a root-only check reads a
        fully-instrumented trace as empty."""
        trace = {"spans": [{
            "span_name": "root",
            "span_attributes": {"span.kind": "CHAIN"},
            "child_spans": [{
                "span_name": "llm",
                "span_attributes": {"output.value": "the real answer"},
                "child_spans": [],
            }],
        }]}
        assert _trace_has_captured_content(trace)

    def test_spans_without_values_read_as_empty(self):
        trace = {"spans": [{
            "span_name": "kylin.http.request",
            "span_attributes": {"span.kind": "CHAIN"},
            "child_spans": [],
        }]}
        assert not _trace_has_captured_content(trace)

    def test_whitespace_only_values_do_not_count_as_content(self):
        trace = {"spans": [{
            "span_name": "root",
            "span_attributes": {"output.value": "   \n  "},
            "child_spans": [],
        }]}
        assert not _trace_has_captured_content(trace)


class TestAbsenceSurvivesFrameworkScaffolding:
    """Instrumentation spans must not be mistaken for the agent answering.

    Framework middleware emits spans whose output is the accumulated message
    state — on the production corpus these ran to 2,990 and 39,999 characters,
    larger than any real answer and structurally identical to one. A gate that
    refutes silence by looking for substantial output below the named span
    therefore suppresses exactly the traces it should report: all four cases
    examined were genuine silences whose model generations carried "text": "".
    Recognising the answer among intermediate spans is the model's job, which is
    why it names a span; the gate only checks what it named.
    """

    @staticmethod
    def _scaffolded(final_out):
        """A silent agent under a middleware span that echoes the prompt."""
        return {"trace_id": "t", "spans": [{
            "span_id": "root", "span_name": "deepthink_agent",
            "span_attributes": {"input.value": "summarise the call"},
            "child_spans": [{
                "span_id": "wrapper", "span_name": "LangGraph",
                "span_attributes": {"span.kind": "CHAIN"},
                "child_spans": [
                    {"span_id": "mw", "span_name": "PatchToolCallsMiddleware.before_agent",
                     "span_attributes": {"span.kind": "CHAIN", "output.value":
                                         '{"messages": "Overwrite(value=[SystemMessage'
                                         '(content=You are an analytics assistant)])"}'},
                     "child_spans": []},
                    {"span_id": "gen", "span_name": "ChatAnthropic",
                     "span_attributes": {"span.kind": "CHAIN", "output.value":
                                         '{"generations": [[{"text": "%s"}]]}' % final_out},
                     "child_spans": []},
                ],
            }],
        }]}

    def test_middleware_echo_does_not_refute_a_genuine_silence(self):
        out = TraceScanner._v8_to_trace_output(
            TestAbsenceClaimsAreVerifiedNotQuoted._parsed_completion("gen"),
            "seen", self._scaffolded(""),
        )
        assert len(out["issues"]) == 1, "scaffolding suppressed a real empty response"

    def test_a_real_answer_in_the_named_span_is_still_dropped(self):
        out = TraceScanner._v8_to_trace_output(
            TestAbsenceClaimsAreVerifiedNotQuoted._parsed_completion("gen"),
            "seen",
            self._scaffolded("The customer agreed to pay on the 14th of next month."),
        )
        assert out["issues"] == []

    def test_duplicate_span_ids_are_judged_by_the_copy_that_answered(self):
        """Some producers emit a subtree twice. Resolving to whichever copy a
        walk reaches first would decide the claim by traversal order."""
        trace = {"trace_id": "t", "spans": [{
            "span_id": "root", "span_name": "agent",
            "span_attributes": {"input.value": "hi"},
            "child_spans": [
                {"span_id": "dup", "span_name": "turn",
                 "span_attributes": {"span.kind": "LLM"}, "child_spans": []},
                {"span_id": "dup", "span_name": "turn",
                 "span_attributes": {"span.kind": "LLM",
                                     "output.value": "Yes, that is booked for Friday."},
                 "child_spans": []},
            ],
        }]}
        out = TraceScanner._v8_to_trace_output(
            TestAbsenceClaimsAreVerifiedNotQuoted._parsed_completion("dup"), "seen", trace,
        )
        assert out["issues"] == [], "an absence held against only one copy of the span"


class TestTruncationIsQuotedButAbsenceIsNot:
    """One dimension, two claims, two different burdens of proof.

    "Produced nothing" is an absence: unquotable exactly when true, so it is
    verified against the named span instead. "Stopped mid-answer" is not an
    absence — there is a response, the prompt asks for its cut-off ending to be
    quoted, and a quote nobody can find is invented for the same reason it is in
    every other dimension. Exempting the whole dimension from quoting let the
    second kind through unchecked.
    """

    CUT = "Your three invoices total $412.90 and the next one is due on the"

    @staticmethod
    def _trace(out):
        return {"trace_id": "t", "spans": [{
            "span_id": "root", "span_name": "agent",
            "span_attributes": {"input.value": "list my invoices",
                                "output.value": out},
            "child_spans": [],
        }]}

    @staticmethod
    def _parsed(evidence):
        return {
            "dimensions": {"completion": {"evidence": evidence, "verdict": "FAIL",
                                          "span": "root"}},
            "issues": [{"dim": "completion", "cat": "Incomplete Response",
                        "brief": "response stops mid-sentence", "conf": "H"}],
        }

    def test_a_real_quote_of_the_cut_ending_survives(self):
        out = TraceScanner._v8_to_trace_output(
            self._parsed(self.CUT), f"out: {self.CUT}", self._trace(self.CUT),
        )
        assert len(out["issues"]) == 1, "a genuine truncation was gated away"

    def test_an_invented_quote_of_a_truncation_is_dropped(self):
        out = TraceScanner._v8_to_trace_output(
            self._parsed("the agent stopped after acknowledging the request"),
            f"out: {self.CUT}",
            self._trace(self.CUT),
        )
        assert out["issues"] == [], "an unquotable truncation claim reached the user"

    def test_an_absence_still_needs_no_quote(self):
        """The exemption must survive: there is nothing to quote when the span
        recorded no output at all."""
        out = TraceScanner._v8_to_trace_output(
            self._parsed(""), "out:", self._trace(""),
        )
        assert len(out["issues"]) == 1, "the absence exemption was lost"


class TestAFinishedAnswerIsNotReadAsCutOff:
    """The tail test fails open — an ending it does not recognise keeps the
    claim alive and reaches the user, so every plausible finish belongs here."""

    @pytest.mark.parametrize("ending", [
        "Your three invoices come to a total of 412.90",
        "Here is the query you asked for:\n```sql\nSELECT 1;\n```",
        "All three tickets are now closed and resolved 🎉",
        "The report is attached and the totals reconcile)",
    ])
    def test_complete_answers_are_not_truncations(self, ending):
        assert not _completion_claim_holds(ending), f"read as cut off: {ending!r}"

    def test_a_genuine_mid_word_cut_is_still_caught(self):
        assert _completion_claim_holds(
            "Your three invoices total $412.90 and the next one is due on the four"
        )
