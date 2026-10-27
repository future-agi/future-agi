"""A second opinion on every claim, before any user sees it.

The scanner is a cheap pass over a compressed trace, and measurement on a
2,107-trace production corpus put roughly one in three of its surfaced claims at
odds with what the trace actually contained. Deterministic gates remove the
claims that are provably unfounded — a quote that was never shown to the model,
an "agent returned nothing" against a complete response. What they cannot settle
is judgement: whether "~80 seconds" is a fabrication when the tool returned
80.27, or whether an intermediate planning step was really the user-facing
answer. Those need a reader, not a rule.

So each surviving claim is put to a stronger model reading the RAW trace, and it
must do two things to keep the claim alive: say CONFIRM, and cite a verbatim
quote that is then checked in code. A confirmation nobody can point at is worth
no more than the assertion it was meant to justify.

Three deliberate choices:

  * The verifier reads the raw spans through its own extraction, never the
    scanner's compressed view. Handing it the same lossy input would let it
    confirm the scanner's mistakes with confidence — the compression is where
    several of the measured false positives were manufactured.
  * It fails closed. An unparseable response, a missing citation, a timeout: the
    claim does not survive. Recall is recoverable; a user's trust in the feed is
    not.
  * It only runs on traces the scanner flagged, which is a minority of traffic,
    so the cost is per-suspicion rather than per-trace.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

import structlog
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
except ImportError:  # OSS build without the gateway
    gateway_call_llm = None
    call_llm_raw = None
    get_gateway_client = None

from ee.agenthub.trace_scanner.prompt import SYSTEM_VERIFIER

logger = structlog.get_logger(__name__)

# Rejections that mean the verifier never answered, as opposed to answering and
# refusing the claim. Nothing is persisted for a trace holding any of these:
# results are only written once the verifier has ruled on every claim.
#
# Fail-closed is right for a refusal and wrong for silence. A gateway that is
# down would otherwise write every flagged trace to the database as clean, and
# the feed would empty itself in a way indistinguishable from a quiet day —
# permanently, since the already-scanned anti-join treats any row as terminal.
# Observed live: the Vertex token went stale within an hour of a passing smoke
# test.
#
# ``no_verdict_returned`` belongs here even though the call itself succeeded. The
# verifier answered about other claims and simply omitted this one, so keeping
# its siblings would persist a verdict that was never given for part of the
# trace.
UNANSWERED_REASONS = frozenset(
    {
        "verifier_error",
        "verifier_no_verdicts",
        "no_raw_trace",
        "no_verdict_returned",
    }
)

# Off. The stage is complete and tested, but it has not been measured against the
# scanner as it now stands — every number for it predates sending the model the
# full span tree, which removed the pre-selection that produced the largest class
# of claims it was catching. Shipping it on that evidence would be trusting a
# result about a pipeline that no longer exists. Turned on deliberately, after a
# run that compares the two.
VERIFY_ENABLED = False

# A stronger reader than the scanner: this stage exists to catch the judgements
# the cheap pass gets wrong, so using the same model would mostly reproduce them.
# Probed against the gateway rather than assumed: gemini-3-pro-preview 404s for
# this project ("does not have access to it") on both the regional and the global
# endpoint, so the registry's VERTEX_GEMINI_3_PRO cannot be used here. 3.1-pro is
# the strongest tier that actually answers. Verify a model responds before
# relying on it — an unreachable verifier fails closed and silently empties the
# feed, which is exactly how the RCA summariser outage presented.
VERIFIER_MODEL: ModelConfig = ModelConfig(
    provider=LiteLlmProvider.VERTEX_AI.value,
    model_name="vertex_ai/gemini-3.1-pro-preview",
    temperature=0.0,
    max_tokens=4096,
)

# Raw traces are sent whole; this only stops one pathological trace from
# becoming an unbounded request.
_MAX_TRACE_CHARS = 400_000
# A reasoning model spends this budget on thinking first. At 4096 the whole
# allowance went to reasoning and the answer came back empty, which fails
# closed and looks exactly like "the verifier rejected everything".
_MAX_OUTPUT_TOKENS = 16_000


def _raw_trace_text(trace_data: Dict[str, Any]) -> str:
    """Flatten the trace's own spans — never the scanner's compressed view."""
    parts: List[str] = []

    def walk(span: Dict[str, Any], depth: int = 0) -> None:
        attrs = span.get("span_attributes") or {}
        # The trace dict names spans `span_name`; a `name` lookup silently
        # returns None and labels every span "span", throwing away the tool and
        # chain names that are often the only thing distinguishing an
        # intermediate step from the user-facing answer.
        name = span.get("span_name") or "span"
        inp = attrs.get("input.value")
        out = attrs.get("output.value")
        parts.append(f"{'  ' * depth}[{name}]")
        if inp:
            parts.append(f"{'  ' * depth}  input: {inp}")
        if out:
            parts.append(f"{'  ' * depth}  output: {out}")
        for child in span.get("child_spans") or []:
            walk(child, depth + 1)

    for root in trace_data.get("spans") or []:
        walk(root)
    text = "\n".join(parts)
    if len(text) > _MAX_TRACE_CHARS:
        from ee.agenthub.trace_scanner.compress import SCANNER_TRUNCATION_MARK

        text = (
            text[:_MAX_TRACE_CHARS]
            + f"{SCANNER_TRUNCATION_MARK}, {len(text) - _MAX_TRACE_CHARS} chars omitted⟩"
        )
    return text


def _call(messages: List[Dict[str, str]]) -> Optional[str]:
    client = get_gateway_client() if get_gateway_client else None
    if client is None:
        if gateway_call_llm is None:
            return None
        return gateway_call_llm(
            model=VERIFIER_MODEL.model_name,
            messages=messages,
            temperature=0.0,
            max_tokens=_MAX_OUTPUT_TOKENS,
        )
    result = call_llm_raw(
        client,
        model=VERIFIER_MODEL.model_name,
        messages=messages,
        temperature=0.0,
        max_tokens=_MAX_OUTPUT_TOKENS,
    )
    # call_llm_raw hands back a GatewayRawResult, not an OpenAI response — the
    # completion is under .response. Reading .choices directly yields nothing,
    # and a bare except would turn that into "the verifier refused everything",
    # emptying the feed for a wiring mistake.
    response = getattr(result, "response", result)
    try:
        return response.choices[0].message.content
    except (AttributeError, IndexError):
        logger.warning(
            "scanner_verify_unreadable_response", shape=type(response).__name__
        )
        return None


def _parse(raw: str) -> List[Dict[str, Any]]:
    if not raw:
        return []
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```")[1] if len(s.split("```")) > 1 else s
        s = s[4:] if s.lower().startswith("json") else s
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        return json.loads(s[start : end + 1]).get("verdicts") or []
    except Exception:
        return []


def _moments_text(key_moments: Optional[List[Any]]) -> str:
    """The scanner's own account of where the failure is.

    Without it the verifier is told "the agent fabricated the totals" and handed
    a trace that runs to ~93,000 characters at p90, with no coordinates — then
    asked to refute anything it cannot establish. That is a needle-in-haystack
    task, and it discarded 61 true claims, 60% of everything it rejected.

    Anchoring stays bounded because the guard is not this prompt: a CONFIRM still
    has to carry the verifier's OWN quote, checked against the raw trace in code.
    These are a starting point to look, not an argument to agree with.
    """
    lines = []
    for km in key_moments or []:
        quote = str(getattr(km, "verbatim", "") or "").strip()
        if not quote:
            continue
        mark = " <- scanner marked this the failure" if getattr(km, "is_failure", False) else ""
        span = str(getattr(km, "span", "") or "")
        where = f" [{span}]" if span else ""
        lines.append(f'  "{quote}"{where}{mark}')
    return "\n".join(lines)


def verify_issues(
    trace_data: Dict[str, Any],
    issues: List[Any],
    key_moments: Optional[List[Any]] = None,
) -> Tuple[List[Any], List[Dict[str, str]]]:
    """Return (confirmed, rejected). Rejections carry a reason for telemetry.

    Any failure of this stage rejects everything it was asked about. That is the
    intended behaviour and not a fallback: if we cannot check a claim, we do not
    show it.
    """
    if not issues:
        return [], []

    trace_text = _raw_trace_text(trace_data)
    if not trace_text.strip():
        return [], [{"brief": getattr(i, "brief", ""), "reason": "no_raw_trace"} for i in issues]

    # Numbered from 1. Models renumber a 0-based list to 1-based as a matter of
    # habit, and under the persist rule a single unmatched index defers the whole
    # trace — so the sweep would re-scan it forever and the feed would thin with
    # nothing in the logs but "no_verdict_returned".
    listing = "\n".join(
        f"{n}. [{getattr(i, 'category', '')}] {getattr(i, 'brief', '')}"
        for n, i in enumerate(issues, start=1)
    )
    moments = _moments_text(key_moments)
    pointer = (
        f"\n\nWHERE THE SCANNER SAYS IT HAPPENED (unverified — check these against "
        f"the trace, and look elsewhere too):\n{moments}"
        if moments
        else ""
    )
    messages = [
        {"role": "system", "content": SYSTEM_VERIFIER},
        {
            "role": "user",
            "content": (
                f"RAW TRACE:\n{trace_text}{pointer}\n\nCLAIMS TO CHECK:\n{listing}"
            ),
        },
    ]

    try:
        verdicts = _parse(_call(messages) or "")
    except Exception as exc:
        logger.warning("scanner_verify_failed", error=str(exc)[:200])
        return [], [
            {"brief": getattr(i, "brief", ""), "reason": "verifier_error"} for i in issues
        ]

    if not verdicts:
        return [], [
            {"brief": getattr(i, "brief", ""), "reason": "verifier_no_verdicts"}
            for i in issues
        ]

    from ee.agenthub.trace_scanner.scanner import _evidence_is_quotable, _norm_for_match

    hay = _norm_for_match(trace_text)
    by_index = {}
    for v in verdicts:
        try:
            by_index[int(v.get("i"))] = v
        except (TypeError, ValueError):
            continue

    # Accept a 0-based answer to the 1-based listing rather than deferring the
    # whole trace over a numbering habit. Only when the set is exactly {0..N-1},
    # so a genuinely partial answer still counts as unanswered.
    if by_index and set(by_index) == set(range(len(issues))):
        by_index = {k + 1: v for k, v in by_index.items()}
        logger.info("scanner_verify_reindexed_zero_based", claims=len(issues))

    confirmed, rejected = [], []
    for n, issue in enumerate(issues, start=1):
        v = by_index.get(n)
        brief = getattr(issue, "brief", "")
        if not v:
            rejected.append({"brief": brief, "reason": "no_verdict_returned"})
            continue
        if str(v.get("verdict", "")).upper() != "CONFIRM":
            rejected.append({"brief": brief, "reason": "refuted"})
            continue
        if not _evidence_is_quotable(v.get("quote"), hay):
            # A confirmation the verifier cannot point at is just a second
            # opinion with no evidence behind it.
            rejected.append({"brief": brief, "reason": "citation_unverifiable"})
            continue
        confirmed.append(issue)

    if rejected:
        logger.info(
            "scanner_verify_rejected",
            trace_id=trace_data.get("trace_id"),
            rejected=len(rejected),
            kept=len(confirmed),
            reasons=sorted({r["reason"] for r in rejected}),
        )
    return confirmed, rejected
