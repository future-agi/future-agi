"""TraceSummarizer — top-down audit of one execution trace.

Purpose-built for ClusterAnalysisAgent's `read(trace, "summary")` tool.
Distinct from TraceErrorAnalysisAgent (Judge + Chauffeur) — that's a peer
skill cluster_rca can dispatch when audit isn't enough; this is the cheap
fast lens read at the top of every trace investigation.

Not a reuse of Chauffeur — they diverge on LLM plane (agentcc-gateway vs
agentic_eval/Bedrock), input (CH spans + alias map vs PG summary string), and
output (typed TraceSummary vs sub_flows), so a shared impl would be lossy.

Stance:
- Work TOP-DOWN: root I/O first, then span tree by depth. Don't aggregate
  span observations into a trace narrative — start at the goal.
- AUDIT, don't speculate. The cluster RCA agent forms hypotheses;
  the summarizer just notices.
- Root input/output and span I/O pass through verbatim — the LLM never
  rewrites them. Only the audit observations + key_steps are LLM-authored.
- Per-field I/O cap (8k chars) prevents pathologically large spans from
  blowing the lite model's context. Generous enough to preserve structure
  for normal spans.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import structlog

from ee.usage.services.gateway_llm_client import call_llm_raw, get_gateway_client


logger = structlog.get_logger(__name__)


DEFAULT_SUMMARIZER_MODEL = "vertex_ai/gemini-3.1-flash-lite"
DEFAULT_SUMMARIZER_TEMPERATURE = 0.1
DEFAULT_SUMMARIZER_MAX_TOKENS = 4000


SYSTEM_PROMPT = """You audit ONE execution trace from an AI agent.

The cluster RCA agent has flagged this trace as part of a failure pattern
and reads your audit to form hypotheses. You do NOT form hypotheses. You
audit.

# Stance

- Work TOP-DOWN. Read the ROOT I/O first, understand the apparent goal,
  then walk forward through what the agent did. Don't reason bottom-up
  from span chatter.
- AUDIT, don't speculate. Notice patterns; do NOT assign root cause or
  blame.

# What to notice (audit lens)

Things a senior engineer would notice on first glance:
- Did the apparent goal get achieved? (success / partial / failed)
- Repeated patterns (same tool called 3x consecutively, similar errors)
- Missing expected steps (no retrieval before a knowledge question)
- Inconsistencies (tool returned X, agent acted as if Y)
- Anomalies (latency spike, empty output, malformed structure)

# Hard rules

1. DO NOT rewrite root input or root output. Those pass through verbatim
   from the database elsewhere — they are not your job. Don't include
   them in your output.
2. Audit observations are signals, NOT root cause analysis.
3. Reference spans by the EXACT label shown in the trace bundle
   (e.g. Sp03). Never make up an ID.
4. Cap audit observations at 5 — force prioritization.
5. `confidence` is YOUR certainty about the observation itself:
   - high: directly visible from the data
   - med:  pattern-level inference from the data
   - low:  weak signal — include only if salient, flag as low
6. Output STRICT JSON only. No markdown code fences. No commentary
   before or after the JSON.

# Output schema

{
  "apparent_goal": "<1 sentence: what the agent was asked to do, paraphrased factually>",
  "outcome": "completed" | "errored" | "abandoned" | "partial",
  "key_steps": [
    {
      "step": 1,
      "span": "Sp02",
      "type": "tool" | "llm" | "retriever" | "agent" | "chain" | "guardrail" | "evaluator" | "embedding" | "reranker" | "conversation" | "unknown",
      "what": "<short factual description of what the span did>",
      "outcome": "<short factual description of how it ended>"
    }
  ],
  "audit_observations": [
    {
      "kind": "repetition" | "missing_step" | "inconsistency" | "anomaly" | "outcome_gap",
      "observation": "<1 sentence: what stands out>",
      "confidence": "high" | "med" | "low",
      "evidence_spans": ["Sp02", "Sp04"]
    }
  ]
}
"""


@dataclass
class TraceSummary:
    """Structured audit output. Cluster RCA wraps this inside a larger
    dict with verbatim root I/O + eval_scores + metadata before returning
    to the LLM."""

    apparent_goal: str = ""
    outcome: str = ""
    key_steps: list[dict[str, Any]] = field(default_factory=list)
    audit_observations: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float = 0.0


class TraceSummarizer:
    """Top-down audit summarizer for one trace.

    Args:
        alias_mint: Callable returned by the parent agent's `_mint_alias`.
            Used to convert span CharField IDs to Sp01-style labels before
            the LLM sees them — keeps alias map coherent across the run.
        model: Gateway-resolved model. Defaults to gemini-3.1-flash-lite.
    """

    def __init__(
        self,
        alias_mint: Callable[[str, Optional[str]], Optional[str]],
        spans_provider: Callable[[str], list[dict]],
        model: str = DEFAULT_SUMMARIZER_MODEL,
        temperature: float = DEFAULT_SUMMARIZER_TEMPERATURE,
        max_tokens: int = DEFAULT_SUMMARIZER_MAX_TOKENS,
    ):
        self._mint = alias_mint
        # Agent-owned, run-cached CH span provider: trace_uuid -> [span dict].
        # No PG — CH spans are the source of truth, and the run cache means a
        # trace's spans are fetched at most once.
        self._spans_provider = spans_provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = get_gateway_client()
        if self._client is None:
            raise RuntimeError(
                "agentcc-gateway client is not configured — "
                "TraceSummarizer requires the gateway to be reachable."
            )

    def summarize(self, trace_uuid: str) -> Optional[TraceSummary]:
        """Render the top-down bundle from CH spans, run the audit.

        Returns None when the trace has no spans in CH; raises on gateway /
        parse failure (caller wraps the exception in tool_error).
        """
        spans = self._spans_provider(trace_uuid)
        if not spans:
            return None
        bundle_text = self._build_bundle(spans)
        return self._call(bundle_text)

    def _build_bundle(self, spans: list[dict]) -> str:
        """Render the trace top-down: root I/O (the root span's I/O), then the
        span tree with depth indentation. Spans are CH column-keyed dicts;
        span ids are re-aliased via self._mint so the LLM sees Sp01 labels.
        """
        # parent_span_id -> [children]
        children_map: dict[Optional[str], list[dict]] = {}
        for s in spans:
            children_map.setdefault(s.get("parent_span_id"), []).append(s)
        for k in children_map:
            children_map[k].sort(key=lambda s: s.get("start_time") or "")

        # Roots: parent missing OR parent not in this trace's span set.
        span_id_set = {s["span_id"] for s in spans}
        roots = [
            s
            for s in spans
            if not s.get("parent_span_id")
            or s["parent_span_id"] not in span_id_set
        ]
        roots.sort(key=lambda s: s.get("start_time") or "")

        # Trace context from the denormalized CH columns + the root span.
        trace_name = (spans[0].get("trace_name") or "").strip() or "(unnamed)"
        root = roots[0] if roots else None

        lines: list[str] = []
        lines.append("=== TRACE ===")
        lines.append(f"name: {trace_name}")
        lines.append("")
        lines.append("=== ROOT I/O (verbatim — do not rewrite) ===")
        if root is not None:
            lines.append(f"input:  {_json_str(root.get('input'))}")
            lines.append(f"output: {_json_str(root.get('output'))}")
            if root.get("status_message") and root.get("status") == "ERROR":
                lines.append(f"error:  {root['status_message']}")
        lines.append("")
        lines.append("=== SPAN TREE (top-down) ===")

        def walk(span: dict, depth: int) -> None:
            indent = "  " * depth
            label = self._mint("span", span["span_id"]) or span["span_id"]
            status_marker = " [ERROR]" if span.get("status") == "ERROR" else ""
            lat = f" {span['latency_ms']}ms" if span.get("latency_ms") else ""
            lines.append(
                f"{indent}{label} [{span.get('observation_type')}] "
                f"\"{span.get('name')}\"{lat}{status_marker}"
            )
            if span.get("input"):
                lines.append(f"{indent}  input:  {_json_str(span['input'])}")
            if span.get("output"):
                lines.append(f"{indent}  output: {_json_str(span['output'])}")
            if span.get("status_message"):
                lines.append(f"{indent}  message: {span['status_message']}")
            for child in children_map.get(span["span_id"], []):
                walk(child, depth + 1)

        if not roots:
            lines.append("(no spans)")
        for root_span in roots:
            walk(root_span, 0)

        return "\n".join(lines)

    def _call(self, bundle_text: str) -> TraceSummary:
        """Run the LLM. Parses strict JSON; falls back to markdown extract."""
        _result = call_llm_raw(
            self._client,
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": bundle_text},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        cost_usd = _result.cost_usd
        response = _result.response
        content = response.choices[0].message.content or "{}"
        data = _safe_json_parse(content)

        return TraceSummary(
            apparent_goal=data.get("apparent_goal", "") or "",
            outcome=data.get("outcome", "") or "",
            key_steps=data.get("key_steps") or [],
            audit_observations=data.get("audit_observations") or [],
            cost_usd=cost_usd,
        )


_MAX_FIELD_CHARS = 8_000


def _json_str(value: Any, *, max_chars: int = _MAX_FIELD_CHARS) -> str:
    """Stringify a JSON value for the trace bundle.

    Per-field cap prevents a single multi-MB I/O payload from blowing
    the lite model's context.  The cap is generous (8k chars ≈ 2k tokens)
    so structure is preserved for normal spans.
    """
    if value is None:
        return "(none)"
    if isinstance(value, str):
        s = value
    else:
        try:
            s = json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            s = str(value)
    if len(s) > max_chars:
        return s[:max_chars] + f"… [{len(s) - max_chars} chars truncated]"
    return s


def _safe_json_parse(content: str) -> dict[str, Any]:
    """Best-effort JSON parse. Tries direct, then markdown code-fence extract."""
    content = (content or "").strip()
    if not content:
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    logger.warning(
        "trace_summarizer_json_parse_failed",
        content_preview=content[:200],
    )
    return {}
