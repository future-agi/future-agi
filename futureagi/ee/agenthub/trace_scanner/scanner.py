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
    attribute_key_moments,
    compress_v2,
    extract_programmatic_metadata,
    kevinify,
    recover_verbatim,
    structural_prefilter_with_ids,
)
from ee.agenthub.trace_scanner.prompt import (
    SUBCAT_TO_FIX_LAYER,
    SUBCAT_TO_GROUP,
    VALID_SUBCATEGORIES,
    build_prompt,
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
        compressed_traces = []
        trace_map = {}  # short_label → (trace_data, prefilter, programmatic_meta)

        for idx, trace in enumerate(traces):
            label = f"t{idx + 1}"
            trace["_short_label"] = label

            prefilter = structural_prefilter_with_ids(trace)
            compressed = compress_v2(trace, prefilter)
            prog_meta = extract_programmatic_metadata(trace, prefilter)

            compressed_traces.append(compressed)
            trace_map[label] = (trace, prefilter, prog_meta)

        # Build prompt and call LLM
        prompt_text = build_prompt(compressed_traces)
        messages = [{"role": "user", "content": prompt_text}]

        try:
            raw_response = self._invoke_llm(messages)
            if raw_response is None:
                raise RuntimeError("scanner_gateway_returned_none")
            parsed = self._parse_response(raw_response)
        except Exception as e:
            logger.exception("scanner_llm_call_failed", error=str(e))
            return [
                ScanResult(
                    trace_id=trace_map[label][0]["trace_id"],
                    has_issues=False,
                    error=str(e),
                )
                for label in trace_map
            ]

        # Map LLM output back to trace results
        results = []
        for label, (trace_data, prefilter, prog_meta) in trace_map.items():
            trace_id = trace_data["trace_id"]
            trace_output = parsed.get(label, {})

            issues = self._parse_issues(trace_output.get("issues", []))
            key_moments = self._parse_key_moments(
                trace_output.get("key_moments", []),
                prog_meta["raw_spans_text"],
                trace_data,
            )

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
