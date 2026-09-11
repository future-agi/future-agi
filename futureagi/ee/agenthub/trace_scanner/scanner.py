"""
Trace Scanner V7.2 — Lightweight AI trace error scanner.

Gemini 3.6 Flash (Vertex) via agentcc gateway, 1 trace per LLM call.
Outputs: issues[] + key_moments[] + meta{}
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import structlog

from ee.agenthub.trace_scanner.compress import (
    SCANNER_TRUNCATION_MARK,
    attribute_key_moments,
    build_trace_payload,
    extract_programmatic_metadata,
    recover_verbatim,
    structural_prefilter_with_ids,
    unwrap_output,
)
from ee.agenthub.trace_scanner.prompt import (
    DIMENSION_TO_SUBCATEGORY,
    SUBCAT_TO_FIX_LAYER,
    SUBCAT_TO_GROUP,
    SYSTEM_V8,
    VALID_SUBCATEGORIES,
    build_prompt_v8,
)
from agentic_eval.core.utils.model_config import (
    LiteLlmProvider,
    ModelConfig,
    ModelConfigs,
)
try:
    from ee.usage.services.gateway_llm_client import (
        call_llm as gateway_call_llm,
        call_llm_raw,
        get_gateway_client,
    )
except ImportError:
    gateway_call_llm = None
    call_llm_raw = None
    get_gateway_client = None

logger = structlog.get_logger(__name__)

BATCH_SIZE = 1
# Evidence shorter than this matches by accident ("error", "the user asked") and
# proves nothing about whether the model actually read the trace.
_MIN_EVIDENCE_CHARS = 16
# Fraction of an evidence quote's word-trigrams that must be present before we
# accept it as a real quote rather than an invention. Models trim clauses and
# normalise punctuation when quoting; they do not reproduce 80% of the trigrams
# of a sentence that was never shown to them.
_EVIDENCE_TRIGRAM_FLOOR = 0.8


# The one dimension whose failure mode is an absence rather than a passage of
# text, so it is checked against the response instead of by quotation.
_ABSENCE_DIMENSION = "completion"
# A response shorter than this is not a real answer to anything.
_EMPTY_RESPONSE_CHARS = 24
# Characters a finished answer plausibly ends on.
_TERMINATORS = ".!?\"')]}>…`"


def _trace_has_captured_content(trace) -> bool:
    """Did the trace record any agent input or output at all?

    Some traces arrive with spans but no captured values — an instrumentation
    gap rather than an agent that stayed silent. Downstream the two are
    indistinguishable: both present as an empty response. Reporting the first as
    the second tells a user their agent failed when what actually failed was our
    recording of it, and on the production corpus that was 26 of 193 surfaced
    claims, every one of them "Incomplete Response".
    """
    stack = list((trace or {}).get("spans") or [])
    while stack:
        span = stack.pop()
        attrs = span.get("span_attributes") or {}
        for key in ("input.value", "output.value"):
            if str(attrs.get(key) or "").strip():
                return True
        stack.extend(span.get("child_spans") or [])
    return False


def _named_spans(trace, span_id) -> List[Dict[str, Any]]:
    """Every span carrying this id. Plural because ids are not always unique —
    some producers emit a subtree twice, and picking whichever copy a walk
    reached first would decide an absence claim by traversal order."""
    sid = str(span_id or "").strip()
    if not sid:
        return []
    found, stack = [], list((trace or {}).get("spans") or [])
    while stack:
        span = stack.pop()
        if str(span.get("span_id") or "") == sid:
            found.append(span)
        stack.extend(span.get("child_spans") or [])
    return found


def _final_response_output(trace, span_id):
    """Raw output of the span the model named as the final response.

    ``""`` when the span exists but recorded no output (a verifiable absence);
    ``None`` when the id resolves to no span at all — the claim then cannot be
    checked against anything and is dropped, same as an unquotable quote.
    Among duplicate copies the longest output wins, so a claim of silence has to
    hold against every copy rather than the emptiest one.
    """
    spans = _named_spans(trace, span_id)
    if not spans:
        return None
    return max(
        (str((s.get("span_attributes") or {}).get("output.value") or "") for s in spans),
        key=len,
    )


# Descending into the named span's children to refute an absence was tried and
# reverted. It cannot be done by output size: instrumentation middleware emits
# spans whose output is the accumulated message state, larger than any real
# answer and structurally indistinguishable from one, so every genuine silence
# in a framework-instrumented trace was suppressed by its own scaffolding.
# Recognising the answer among intermediate spans is the model's job, which is
# why it names the span; the gate only checks what it named.


def _is_truncation_not_absence(raw_output) -> bool:
    """Is the claim "it stopped mid-answer" rather than "it said nothing"?

    Only the first has anything to quote, so only the first carries the quoting
    burden. The line between them is the same floor an absence uses, because an
    empty provider envelope unwraps to a short placeholder rather than to "".
    """
    return len(str(unwrap_output(raw_output) or "").strip()) >= _EMPTY_RESPONSE_CHARS


def _completion_claim_holds(result_text) -> bool:
    """Is the agent's response actually missing or cut short?

    Verifying the claim beats quoting it: an empty response has nothing to quote
    and a complete one can always have some sentence lifted out of it, so the
    quote proves nothing either way. Our own truncation marker is excluded — a
    trace we shortened must never be reported as an agent that stopped early.
    """
    text = str(result_text or "").strip()
    if SCANNER_TRUNCATION_MARK in text:
        text = text.split(SCANNER_TRUNCATION_MARK)[0].strip()
        return len(text) < _EMPTY_RESPONSE_CHARS
    if len(text) < _EMPTY_RESPONSE_CHARS:
        return True
    # Ends without terminal punctuation or a closing bracket: cut mid-thought.
    # The terminator set is deliberately wide, because this test fails OPEN — an
    # ending it does not recognise keeps the claim alive and reaches the user. A
    # response ending in a bare figure ("...bringing the total to 412.90"), a
    # closed code fence, or an emoji is a complete answer, and every one of those
    # read as truncated before they were listed here.
    last = text[-1]
    if last in _TERMINATORS or last.isdigit() or not last.isascii():
        return False
    return not text.endswith("```")


def _norm_for_match(s) -> str:
    """Whitespace- and case-insensitive form for substring comparison."""
    return re.sub(r"\s+", " ", str(s or "")).casefold().strip()


def _evidence_is_quotable(evidence, haystack_norm: str) -> bool:
    """Was this quote actually present in what the model was shown?

    Three passes, loosening in the ways a model legitimately drifts and tightening
    against the ways it invents:
      1. the whole quote, normalised — the common case
      2. a long leading run — the model trimmed a trailing clause, or joined two
         fragments with an ellipsis
      3. word-trigram coverage — punctuation and casing were rewritten but the
         words are demonstrably from the source
    """
    ev = _norm_for_match(evidence)
    if len(ev) < _MIN_EVIDENCE_CHARS:
        return False
    if ev in haystack_norm:
        return True
    head = ev[:120]
    if len(head) >= _MIN_EVIDENCE_CHARS and head in haystack_norm:
        return True
    # A quote followed by the model's own commentary is still a quote. Requiring
    # the whole string to match would reject a model that read the trace and then
    # explained itself, which costs recall for a stylistic habit rather than for
    # inventing anything.
    for sentence in re.split(r"(?<=[.!?])\s+", ev):
        if len(sentence) >= _MIN_EVIDENCE_CHARS and sentence in haystack_norm:
            return True
    words = ev.split()
    if len(words) < 3:
        return False
    grams = [" ".join(words[i : i + 3]) for i in range(len(words) - 2)]
    hits = sum(1 for g in grams if g in haystack_norm)
    return hits / len(grams) >= _EVIDENCE_TRIGRAM_FLOOR


# Share of the shorter quote's word-trigrams that must appear in the longer one
# before two dimensions are treated as describing one moment. Deliberately well
# below the invention floor above: that test asks "did the model make this up",
# this one asks "are these the same sentence", and quoting the same moment for
# two different reasons naturally clips it differently at each end.
_SAME_EVENT_OVERLAP = 0.6

# When one event trips several dimensions, the surviving claim is the one that
# names the mechanism rather than the symptom: a tool that failed EXPLAINS the
# fabrication that followed, and "did not answer" is the least specific reading
# of anything. Order is most-mechanical to most-general.
_DIMENSION_SPECIFICITY = ("tools", "grounding", "instruction", "goal", "completion")


def _word_trigrams(norm_text: str) -> set:
    words = norm_text.split()
    return {" ".join(words[i : i + 3]) for i in range(len(words) - 2)}


def _same_event(evidence_a, evidence_b) -> bool:
    """Do two dimensions' quotes point at the same moment in the trace?

    Conservative on purpose. A false merge hides a genuinely distinct bug, which
    is worse than leaving a duplicate in the feed, so this requires the quotes to
    substantially BE each other rather than merely to be related.
    """
    a, b = _norm_for_match(evidence_a), _norm_for_match(evidence_b)
    if len(a) < _MIN_EVIDENCE_CHARS or len(b) < _MIN_EVIDENCE_CHARS:
        return False
    if a in b or b in a:
        return True
    ta, tb = _word_trigrams(a), _word_trigrams(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= _SAME_EVENT_OVERLAP


def _collapse_same_event(failed: set, dims: dict) -> set:
    """One mistake, one claim.

    The prompt asks for an issue per FAILED dimension, so a single mistake that
    trips two dimensions arrives as two claims — measured at 10 of 60 traces on a
    production corpus, 9 of them describing literally the same event twice. The
    category then differs between the two, and category-partitioned assignment
    makes them unmergeable both when clustering and when merging clusters, so the
    duplicate survives all the way to the feed: one cash-arithmetic bug reached
    the user as four claims spread over three separate entries.

    Nothing downstream can repair this, and it needs no model — the evidence
    quotes are already in hand and simply compared.
    """
    ordered = sorted(
        failed,
        key=lambda d: _DIMENSION_SPECIFICITY.index(d)
        if d in _DIMENSION_SPECIFICITY
        else len(_DIMENSION_SPECIFICITY),
    )
    kept: list = []
    dropped: list = []
    for dim in ordered:
        evidence = (dims.get(dim) or {}).get("evidence")
        if any(_same_event(evidence, (dims.get(k) or {}).get("evidence")) for k in kept):
            dropped.append(dim)
            continue
        kept.append(dim)
    if dropped:
        logger.info(
            "scanner_duplicate_dimension_collapsed",
            kept=sorted(kept),
            dropped=sorted(dropped),
        )
    return set(kept)


# Briefs become cluster titles and clustering keys; anything longer is an evidence quote
# that has leaked into the wrong field.
_MAX_BRIEF_CHARS = 110
SCAN_VERSION = "v7.2"

# Prefer the registry entry; fall back to an inline config so this module
# keeps working in deployments whose `ModelConfigs` hasn't picked up the
# new entry yet (e.g. ee landing before the agentic_eval bump).
_DEFAULT_SCANNER_MODEL: ModelConfig = getattr(
    ModelConfigs, "VERTEX_GEMINI_3_6_FLASH", None
) or ModelConfig(
    provider=LiteLlmProvider.VERTEX_AI.value,
    model_name="vertex_ai/gemini-3.6-flash",
    temperature=0.2,
    max_tokens=8100,
)


@dataclass
class ScanIssue:
    """Single issue found by the scanner."""

    category: str  # Subcategory e.g. "Language-only"
    group: str  # Scanner group e.g. "Tool Failures"
    fix_layer: str  # "Tools" / "Prompt" / "Orchestration" / "Guardrails"
    confidence: str  # "H" / "M" / "L"
    brief: str  # Short description


@dataclass
class KeyMoment:
    """A key moment extracted from the trace.

    ``kevinified`` + ``verbatim`` are the LLM excerpt and its recovered raw
    text. ``role``/``span``/``status``/``is_failure`` are attributed
    deterministically from the source span (no LLM) so the FE can render a
    grounded breadcrumb; they're empty when the quote doesn't match a span.
    """

    kevinified: str
    verbatim: str
    role: str = ""
    span: str = ""
    status: str = ""
    is_failure: bool = False


@dataclass
class ScanMeta:
    """Programmatic metadata extracted from trace (no LLM needed)."""

    tools_called: List[Dict[str, str]] = field(default_factory=list)
    tools_available: List[str] = field(default_factory=list)
    turn_count: int = 0


@dataclass
class ScanResult:
    """Scanner output for a single trace."""

    trace_id: str
    has_issues: bool
    issues: List[ScanIssue] = field(default_factory=list)
    key_moments: List[KeyMoment] = field(default_factory=list)
    meta: ScanMeta = field(default_factory=ScanMeta)
    error: Optional[str] = None
    # Set when the scan could not be completed for a reason that may not recur,
    # so no row is persisted at all. A FAILED row would not help: the
    # already-scanned anti-join treats any row as terminal, so writing one
    # converts a passing outage into a trace that is never looked at again.
    retryable: bool = False


class TraceScanner:
    """
    Lightweight trace error scanner.

    LLM traffic is routed through the agentcc gateway so internal scanner
    usage is metered / observable like customer traffic.

    Usage:
        scanner = TraceScanner()
        results = scanner.scan_batch(traces)
    """

    TEMPERATURE = 0.2
    MAX_TOKENS = 6144

    def __init__(self, model_config: Optional[ModelConfig] = None):
        self.model_config = model_config or _DEFAULT_SCANNER_MODEL
        self.total_cost_usd: float = 0.0
        self.token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def scan_batch(self, traces: List[Dict[str, Any]]) -> List[ScanResult]:
        """
        Scan a batch of traces (up to BATCH_SIZE per LLM call).

        Args:
            traces: List of trace dicts with "trace_id" and "spans" keys.
                    Span format: nested tree with span_attributes, child_spans, etc.

        Returns:
            List of ScanResult, one per trace.
        """
        results = []
        for i in range(0, len(traces), BATCH_SIZE):
            batch = traces[i : i + BATCH_SIZE]
            batch_results = self._scan_single_batch(batch)
            results.extend(batch_results)
        return results

    def _scan_single_batch(self, traces: List[Dict[str, Any]]) -> List[ScanResult]:
        """Scan a single batch (1-3 traces) with one LLM call."""
        # Assign short labels for token efficiency
        payloads = []
        trace_map = {}  # short_label → (trace_data, prefilter, programmatic_meta)

        for idx, trace in enumerate(traces):
            label = f"t{idx + 1}"
            trace["_short_label"] = label

            prefilter = structural_prefilter_with_ids(trace)
            payload = build_trace_payload(trace, prefilter)
            prog_meta = extract_programmatic_metadata(trace, prefilter)

            payloads.append(payload)
            trace_map[label] = (trace, prefilter, prog_meta)

        # Build prompt and call LLM. BATCH_SIZE is 1, so this is one trace per call
        # and the decomposed prompt gets the model's full attention on it.
        user_prompt = build_prompt_v8(payloads)
        messages = [
            {"role": "system", "content": SYSTEM_V8},
            {"role": "user", "content": user_prompt},
        ]

        try:
            raw_response = self._invoke_llm(messages)
            if raw_response is None:
                raise RuntimeError("scanner_gateway_returned_none")
            parsed = self._parse_response(raw_response)
            # V8 returns ONE flat object per trace; the mapping below expects the
            # batched {label: {...}} shape the old multi-trace prompt produced.
            if "dimensions" in parsed or "key_moments" in parsed:
                first_label = next(iter(trace_map))
                parsed = {
                    first_label: self._v8_to_trace_output(
                        parsed,
                        # The gate compares evidence against the exact text the
                        # model was shown, so a legitimate quote always matches.
                        user_prompt,
                        # The raw trace: the completion gate verifies the named
                        # span's real output, not a pre-selected result.
                        trace_map[first_label][0],
                        _trace_has_captured_content(trace_map[first_label][0]),
                    )
                }
        except Exception as e:
            logger.exception("scanner_llm_call_failed", error=str(e))
            # Retryable: the scan did not happen, so the trace is unknown rather
            # than clean. Persisting a row here — of any status — would retire it
            # permanently, because the already-scanned anti-join treats FAILED
            # exactly like COMPLETED. A gateway outage would then silently take
            # every trace it touched out of the feed for good. Traces that fail
            # forever are still bounded: the sweep abandons anything left
            # unscanned past the watermark lag with a durable FAILED marker.
            return [
                ScanResult(
                    trace_id=trace_map[label][0]["trace_id"],
                    has_issues=False,
                    error=str(e),
                    retryable=True,
                )
                for label in trace_map
            ]

        # Map LLM output back to trace results
        results = []
        for label, (trace_data, prefilter, prog_meta) in trace_map.items():
            trace_id = trace_data["trace_id"]
            trace_output = parsed.get(label, {})

            issues = self._parse_issues(trace_output.get("issues", []))
            # Parsed before verification, not after, because the verifier is
            # given these as the scanner's own account of where the failure is.
            # Nothing here depends on the issues, so the move is free.
            key_moments = self._parse_key_moments(
                trace_output.get("key_moments", []),
                prog_meta["raw_spans_text"],
                trace_data,
            )
            # Second opinion on whatever survived the deterministic gates. Only
            # runs when something was flagged, so the cost lands on suspicion
            # rather than on traffic. Imported lazily: verify.py reads helpers
            # from this module, and a top-level import would close the cycle.
            if issues:
                from ee.agenthub.trace_scanner.verify import (
                    UNANSWERED_REASONS,
                    VERIFY_ENABLED,
                    verify_issues,
                )

                if VERIFY_ENABLED:
                    issues, rejected = verify_issues(trace_data, issues, key_moments)
                    # Nothing is persisted until the verifier has ruled on every
                    # claim. One unanswered claim makes the whole trace unknown
                    # rather than clean, and both a "clean" row and a FAILED row
                    # are terminal — either would stop the trace ever being
                    # scanned again. Writing nothing leaves it for the sweep.
                    unanswered = [
                        r for r in rejected if r["reason"] in UNANSWERED_REASONS
                    ]
                    if unanswered:
                        logger.warning(
                            "scanner_verify_incomplete_deferring_trace",
                            trace_id=trace_id,
                            unanswered=len(unanswered),
                            confirmed_but_withheld=len(issues),
                            reasons=sorted({r["reason"] for r in unanswered}),
                        )
                        results.append(
                            ScanResult(
                                trace_id=trace_id,
                                has_issues=False,
                                error="scanner_verify_incomplete",
                                retryable=True,
                            )
                        )
                        continue

            meta = ScanMeta(
                tools_called=prog_meta["tools_called"],
                tools_available=prog_meta["tools_available"],
                turn_count=prog_meta["turn_count"],
            )

            results.append(
                ScanResult(
                    trace_id=trace_id,
                    has_issues=len(issues) > 0,
                    issues=issues,
                    key_moments=key_moments,
                    meta=meta,
                )
            )

        return results

    def _invoke_llm(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Run one gateway LLM call and accumulate the per-request cost.

        Prefers the raw OpenAI-compatible client so the agentcc gateway's
        per-request cost (shipped as the `x-agentcc-cost` response header)
        can be read and aggregated for billing. Falls back to the
        content-only helper when no gateway is configured (OSS).
        """
        client = get_gateway_client()
        if client is None:
            # OSS / no gateway — use the litellm-backed helper, no cost data
            return gateway_call_llm(
                model=self.model_config.model_name,
                messages=messages,
                temperature=self.TEMPERATURE,
                max_tokens=self.MAX_TOKENS,
            )

        _result = call_llm_raw(
            client,
            model=self.model_config.model_name,
            messages=messages,
            temperature=self.TEMPERATURE,
            max_tokens=self.MAX_TOKENS,
            extra_body={"thinking_config": {"thinking_budget": 0}},
        )
        self.total_cost_usd += _result.cost_usd
        response = _result.response
        usage = getattr(response, "usage", None)
        if usage:
            self.token_usage["prompt_tokens"] += getattr(
                usage, "prompt_tokens", 0
            ) or 0
            self.token_usage["completion_tokens"] += getattr(
                usage, "completion_tokens", 0
            ) or 0
            self.token_usage["total_tokens"] += getattr(usage, "total_tokens", 0) or 0
        try:
            return response.choices[0].message.content
        except (AttributeError, IndexError):
            return None

    def _parse_response(self, raw: str) -> Dict[str, Any]:
        """Extract JSON from LLM response, handling markdown fences."""
        # Strip markdown code fences if present
        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to find JSON object in the response
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning("scanner_json_parse_failed", raw_length=len(raw))
            return {}

    @staticmethod
    def _v8_to_trace_output(
        parsed: Dict[str, Any],
        seen_text: str = "",
        trace: Optional[Dict[str, Any]] = None,
        trace_has_content: bool = True,
    ) -> Dict[str, Any]:
        """Translate V8's per-dimension verdicts into the shipped issue shape.

        A dimension is only turned into an issue when it FAILED, so a hallucinated
        entry in "issues" without a matching FAIL verdict is dropped rather than
        surfaced — the evidence gate is the whole point of the prompt.

        The prompt states the rule the model is meant to follow: "If you cannot
        quote the required evidence, the verdict for that dimension is PASS. An
        unquotable failure is a hallucinated failure." Until now that rule was
        enforced by the model on itself — nothing checked that the quote it
        returned was ever in front of it, and the quote was then discarded.

        ``seen_text`` is what the model was actually shown. A FAIL whose evidence
        cannot be found there is dropped: the model asserting a quote it did not
        receive is the definition of the failure this gate exists to stop. This
        is deliberately fail-closed — a dropped real issue costs recall, a kept
        invented one costs the user's trust in every other row.
        """
        dims = parsed.get("dimensions") or {}
        hay = _norm_for_match(seen_text)
        failed = set()
        unverified = []
        for k, v in dims.items():
            if not isinstance(v, dict) or str(v.get("verdict", "")).upper() != "FAIL":
                continue
            if k == _ABSENCE_DIMENSION:
                # You cannot quote an absence. "The agent returned nothing" is
                # unquotable precisely when it is TRUE, so demanding a quote here
                # blinds the dimension instead of sharpening it — measured at 94
                # of 124 gate drops landing on this one dimension alone. Instead
                # the model names the span whose output is (or should have been)
                # the final response, and the claim is checked against that
                # span's REAL output: it survives only if that output is missing
                # or cut short. An unresolvable span id is an unverifiable claim
                # and drops, exactly like an unquotable quote.
                # ...and only when the trace recorded anything at all. With no
                # captured content there is no way to tell a silent agent from a
                # silent recorder, and blaming the agent for our own gap is the
                # worse of the two mistakes.
                named_output = _final_response_output(trace, v.get("span"))
                if (
                    not trace_has_content
                    or named_output is None
                    # The gate judges the response text, not its provider
                    # envelope: a wrapper with empty content must still read as
                    # empty, and raw JSON ending in "}" must not read as a
                    # cleanly terminated answer.
                    or not _completion_claim_holds(unwrap_output(named_output))
                    # Two different claims share this dimension. "Produced
                    # nothing" is an absence and is exempt from quoting. "Stopped
                    # mid-answer" is not — there is a response, the prompt asks
                    # for the cut-off ending to be quoted, and a quote that
                    # cannot be found in what the model was shown is invented
                    # here for the same reason it is anywhere else. The two are
                    # told apart by the same floor that defines an absence, not
                    # by mere non-emptiness: an empty provider envelope unwraps
                    # to a short placeholder, which is still an absence and has
                    # nothing to quote.
                    or (
                        hay
                        and _is_truncation_not_absence(named_output)
                        and not _evidence_is_quotable(v.get("evidence"), hay)
                    )
                ):
                    unverified.append(k)
                    continue
            elif hay and not _evidence_is_quotable(v.get("evidence"), hay):
                unverified.append(k)
                continue
            failed.add(k)
        if unverified:
            # Suppression is a measurement, not a silent behaviour: without this
            # count there is no way to tell a precise scanner from a mute one.
            logger.info(
                "scanner_evidence_gate_dropped",
                dimensions=sorted(unverified),
                dropped=len(unverified),
            )
        # Collapse after gating, not before: a dimension whose evidence was
        # invented must not be the one that survives a merge.
        failed = _collapse_same_event(failed, dims)
        briefs = {
            i.get("dim"): i for i in (parsed.get("issues") or []) if isinstance(i, dict)
        }
        issues = []
        low_confidence = []
        for dim in failed:
            item = briefs.get(dim) or {}
            # Prefer the model's own subcategory: DIMENSION_TO_SUBCATEGORY alone can only ever
            # emit 5 of 20, which halves category accuracy on TRAIL (36.8% -> 18.8%).
            cat = str(item.get("cat") or "").strip()
            subcat = cat if cat in VALID_SUBCATEGORIES else DIMENSION_TO_SUBCATEGORY.get(dim)
            if not subcat:
                continue
            # NEVER fall back to the evidence quote. `brief` is what _retitle_from_members
            # turns into the cluster title and what scan_types embeds for clustering, and
            # evidence is a VERBATIM span of this one trace. Using it made 76% of production
            # briefs quotes rather than descriptions (median 19 words vs the <=15 spec, cut
            # mid-sentence at the cap) — which is why cluster titles do not match their
            # members and why near-identical defects fail to cluster at all. A short generic
            # label groups correctly and reads honestly; a stray quote does neither.
            conf = str(item.get("conf") or "M").upper()[:1]
            if conf == "L":
                # The model was given L back as a real option so it has somewhere
                # to put uncertainty other than an assertion. Keeping those out of
                # the feed is the whole point: a row the model itself could not
                # establish is exactly the row that costs a user their trust.
                # Recorded, not discarded — recall work needs them.
                low_confidence.append(subcat)
                continue
            brief = " ".join(str(item.get("brief") or "").split())
            if not brief or len(brief) > _MAX_BRIEF_CHARS:
                brief = subcat
            issues.append({
                "cat": subcat,
                "conf": conf if conf in ("H", "M") else "M",
                "brief": brief[:_MAX_BRIEF_CHARS],
            })
        if low_confidence:
            # Suppression has to be visible. Going quiet and being precise look
            # identical from the outside, and only these counters tell them apart.
            logger.info(
                "scanner_low_confidence_withheld",
                categories=sorted(low_confidence),
                withheld=len(low_confidence),
            )
        return {"issues": issues, "key_moments": parsed.get("key_moments") or []}

    def _parse_issues(self, raw_issues: List[Dict]) -> List[ScanIssue]:
        """Validate and normalize issues from LLM output."""
        issues = []
        for item in raw_issues:
            cat = item.get("cat", "")
            conf = item.get("conf", "M")
            brief = item.get("brief", "")

            # Validate subcategory
            if cat not in VALID_SUBCATEGORIES:
                # Try fuzzy match
                cat_lower = cat.lower()
                matched = None
                for valid in VALID_SUBCATEGORIES:
                    if valid.lower() == cat_lower:
                        matched = valid
                        break
                if not matched:
                    logger.debug("scanner_unknown_category", category=cat)
                    continue
                cat = matched

            # Validate confidence
            if conf not in ("H", "M", "L"):
                conf = "M"

            group = SUBCAT_TO_GROUP.get(cat, "")
            fix_layer = SUBCAT_TO_FIX_LAYER.get(cat, "")

            issues.append(
                ScanIssue(
                    category=cat,
                    group=group,
                    fix_layer=fix_layer,
                    confidence=conf,
                    brief=brief[:200],  # cap brief length
                )
            )
        return issues

    def _parse_key_moments(
        self,
        raw_moments: List[str],
        raw_spans_text: Dict[str, str],
        trace_data: Optional[Dict] = None,
    ) -> List[KeyMoment]:
        """Parse key_moments — recover verbatim text from kevinified excerpts
        and attribute each to its source span (role/span/status/is_failure)
        deterministically."""
        clean = [m for m in raw_moments if m and isinstance(m, str)]
        all_raw = (
            raw_spans_text.get("all_inputs", "")
            + "\n"
            + raw_spans_text.get("all_outputs", "")
        )
        attribution = attribute_key_moments(clean, trace_data) if trace_data else []

        moments = []
        for i, moment in enumerate(clean):
            verbatim = recover_verbatim(moment, all_raw)
            attr = attribution[i] if i < len(attribution) else {}
            moments.append(
                KeyMoment(
                    kevinified=moment,
                    verbatim=verbatim,
                    role=attr.get("role", ""),
                    span=attr.get("span", ""),
                    status=attr.get("status", ""),
                    is_failure=attr.get("is_failure", False),
                )
            )
        return moments
