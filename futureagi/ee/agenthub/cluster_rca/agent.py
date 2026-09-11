"""ClusterAnalysisAgent — agentic tool-loop investigation at cluster scope.

The agent is an LLM with a curated harness. Tools mirror codebase exploration
(list / search / read / aggregate / compare / timeline). The LLM composes them
to investigate a TraceErrorGroup and produce a 2-sentence root-cause synthesis
+ 1-sentence fix.

Tool handlers return plain dicts — same pattern as the Judge agent
(ee/agenthub/traceerroragent/judge.py). The LLM→ORM boundary IS typed (see
types.py: ClusterFinding / ClusterSynthesis / ClusterAnalysisResult).

Per-dimension/entity handlers ship piecewise. Unwired handlers return
tool_error(ERROR_CODE_UNAVAILABLE, ...). Real implementations land without
changing the tool surface.
"""

import json
import re
import threading
import uuid as _uuid
from dataclasses import asdict
from typing import Any, Callable

import structlog
from django.db.models import Q

from ee.agenthub.cluster_rca import selectors
from ee.agenthub.cluster_rca.constants import (
    CLUSTER_RCA_COMPACT_KEEP_RECENT,
    CLUSTER_RCA_CONVERGENCE_DIMS,
    CLUSTER_RCA_CONVERGENCE_GRACE,
    CLUSTER_RCA_COST_CEILING_USD,
    CLUSTER_RCA_DOMINANT_MIN_TOTAL,
    CLUSTER_RCA_DOMINANT_PCT,
    CLUSTER_RCA_MAX_TURNS,
    CLUSTER_RCA_WRAPUP_TURNS,
    Confidence,
    FindingType,
)
from ee.agenthub.cluster_rca.context_builder import build_project_schema_context
from ee.agenthub.cluster_rca.filter_adapter import resolve_group_by, to_canonical
from ee.agenthub.cluster_rca.prompts import SYSTEM_PROMPT
from ee.agenthub.cluster_rca.tools import (
    CLUSTER_RCA_TOOLS,
    ERROR_CODE_INVALID_ARGS,
    ERROR_CODE_INVALID_FILTER,
    ERROR_CODE_NOT_FOUND,
    ERROR_CODE_UNAVAILABLE,
    TERMINAL_TOOL_NAME,
    tool_error,
)
from ee.agenthub.cluster_rca.types import (
    ClusterAnalysisResult,
    ClusterFinding,
    ClusterSynthesis,
)
from ee.usage.services.gateway_llm_client import call_llm_raw, get_gateway_client
from tracer.models.trace_error_analysis import ErrorClusterTraces
from tracer.services.clickhouse.cluster_rca_spans import (
    aggregate_span_field,
    aggregate_trace_field,
    distinct_sessions,
    error_messages_in_traces,
    list_spans_in_traces,
    read_span,
    search_spans_in_traces,
    search_trace_ids,
    spans_for_trace,
    timeline_trace_counts,
    trace_roots,
    traces_in_session,
)
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.clickhouse.span_attribute_lookups import (
    aggregate_attribute_over_traces,
    list_attribute_keys_for_project,
    list_attribute_keys_for_traces,
    list_attributes_for_trace,
    scoped_trace_ids,
)


# Type alias for the streaming-events callback the caller can pass in.
# Callable[(event_type: str, payload: dict)] → None
EventCallback = Callable[[str, dict], None]


# Default model — Vertex Gemini 3.6 Flash, routed through agentcc-gateway.
# Thinking is left ENABLED (no thinking_budget=0 override) because cluster
# RCA needs deep reasoning across many tool results.
DEFAULT_MODEL = "vertex_ai/gemini-3.6-flash"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 8100
# Native thinking is essential for this agent — with it off the loop degenerates
# into reflexive tool-spray and never converges. On by default; the gateway only
# surfaces it as reasoning_content when this rides through (opt-in there).
DEFAULT_THINKING_BUDGET = 2048
# Per-turn client-side deadline. The gateway's own request timeout is 300s, so
# without this a single wedged upstream call stalls a run for five minutes —
# and the forced-synthesis fallback that follows can wedge for five more. A
# turn that has not answered in this long is not going to produce a better
# answer than the loop's own error path.
_TURN_TIMEOUT_S = 90.0


def _noop_event(event_type: str, payload: dict) -> None:
    """Default on_event sink — does nothing."""
    return


_SNIPPET_LEN = 200
_DEFAULT_VERBATIM_CAP = 2048  # 2KB default cap for root I/O / span I/O
# Hard ceiling for a single EXPANDED verbatim field (depth='full' / expand=[...]).
# Normal reads are already capped at _DEFAULT_VERBATIM_CAP; only an opted-in
# expand can exceed this. Past the ceiling the field is compressed via the lite
# summarizer before it enters the main model's context, so one deep read can't
# blow the ~1M-token window. Tunable.
_READ_COMPRESS_CHARS = 120_000          # ~30k tokens — compress trigger
_READ_COMPRESS_INPUT_CAP = 2_000_000    # never feed the compressor more than this
# Deterministic safety net for compression: verbatim windows around any failure
# marker are extracted BEFORE the lossy LLM summary, so an error can never be
# silently dropped (an LLM can miss a needle buried in filler — for an RCA tool
# that is unacceptable).
_ERROR_MARKER_RE = re.compile(
    r"(?i)(error|exception|traceback|stack ?trace|fail(?:ed|ure|ing)?|timeout|"
    r"timed out|refused|denied|unauthorized|forbidden|fatal|panic|crash|"
    r"\b[45]\d{2}\b)"
)
_ERROR_WINDOW_CHARS = 240   # verbatim context kept around each marker
_ERROR_BLOCK_BUDGET = 24_000  # cap on the total verbatim-error block

# Inline span I/O budgets for read(trace).
#
# The ceiling that matters is per TRACE, not per field: a per-field cap
# multiplies by span count, and a trace's span count is unbounded. Retained
# context is bounded independently by _compact_old_tool_results, which keeps
# only the most recent CLUSTER_RCA_COMPACT_KEEP_RECENT tool results — so the
# worst case the main model ever holds is that many trace payloads.
_SPAN_IO_FIELD_CAP = 1024       # per span, per field, on a default read
_TRACE_IO_BUDGET = 48_000       # total inline span I/O for one trace
_TRACE_IO_BUDGET_FULL = 160_000  # same, for the opted-in forensic depth
_MAX_SPANS_RENDERED = 400       # span-count ceiling; the tail is elided and counted


def _clamp(value: Any, lo: int, hi: int) -> int:
    """Coerce a paging arg to int and clamp to [lo, hi]. Non-int (e.g. the LLM
    passing a string or null) falls back to lo rather than crashing the ORM."""
    try:
        return max(lo, min(int(value), hi))
    except (TypeError, ValueError):
        return lo


def _snippet(value: Any, limit: int = _SNIPPET_LEN) -> str:
    """Truncate a JSON field for list-item previews.

    Trace.input/output are JSONField — could be dict, list, str, None. We
    str()-stringify, collapse internal whitespace, and cut to `limit` chars.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        s = value
    else:
        try:
            s = json.dumps(value, default=str)
        except (TypeError, ValueError):
            s = str(value)
    s = " ".join(s.split())
    return s[:limit] + ("…" if len(s) > limit else "")


def _verbatim(value: Any, cap: int = _DEFAULT_VERBATIM_CAP, expand: bool = False) -> dict:
    """Default-light verbatim field with truncation markers.

    Returns {value, truncated, full_chars}. When `expand=True`, returns the
    full value with truncated=False regardless of size — the agent opts
    into this via the `expand` parameter on read().
    """
    if value is None:
        return {"value": None, "truncated": False, "full_chars": 0}
    if isinstance(value, str):
        s = value
    else:
        try:
            s = json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            s = str(value)
    full_chars = len(s)
    if expand or full_chars <= cap:
        return {"value": s, "truncated": False, "full_chars": full_chars}
    return {
        "value": s[:cap] + "…",
        "truncated": True,
        "full_chars": full_chars,
    }


def _as_text(value: Any) -> str:
    """Stringify a JSONField value without collapsing whitespace."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _budgeted_field(value: Any, cap: int) -> dict:
    """Verbatim span field, capped, carrying any failure past the cut.

    Head-truncation alone drops the one thing a trace read exists to surface: a
    failure that happens at the END of a long output. When the cut would lose a
    failure marker, a verbatim window around the first marker past it rides
    along with the head.
    """
    s = _as_text(value)
    full = len(s)
    # A negative cap would slice from the END and return almost everything —
    # the opposite of a budget.
    cap = max(0, cap)
    if full <= cap:
        return {"value": s, "truncated": False, "full_chars": full}
    head = s[:cap]
    marker = _ERROR_MARKER_RE.search(s, cap)
    if marker is None:
        return {"value": head + "…", "truncated": True, "full_chars": full}
    start = max(cap, marker.start() - _ERROR_WINDOW_CHARS)
    window = s[start : marker.end() + _ERROR_WINDOW_CHARS]
    return {
        "value": f"{head}…[{full - cap} chars elided]… {window}",
        "truncated": True,
        "full_chars": full,
    }


def _build_root_block(
    input_val: Any,
    output_val: Any,
    error_val: Any,
    expand_input: bool,
    expand_output: bool,
    expand_error: bool,
) -> dict:
    """Assemble trace.root with default-truncation + expand markers.

    Takes raw input/output/error values (CH root-span I/O — strings — or any
    JSON-able value). Output shape per field: `<field>` (value),
    `<field>_truncated` (bool), `<field>_full_chars` (int). The LLM sees the
    truncation flag and can re-call with expand=['root.<field>'] for the full.
    """
    inp = _verbatim(input_val, expand=expand_input)
    out = _verbatim(output_val, expand=expand_output)
    err = _verbatim(error_val, expand=expand_error)
    return {
        "input": inp["value"],
        "input_truncated": inp["truncated"],
        "input_full_chars": inp["full_chars"],
        "output": out["value"],
        "output_truncated": out["truncated"],
        "output_full_chars": out["full_chars"],
        "error": err["value"],
        "error_truncated": err["truncated"],
        "error_full_chars": err["full_chars"],
    }


def _rollup_to_list(agg_result: dict, offset: int, limit: int) -> dict:
    """Translate an aggregate() response into the list() envelope shape.

    Rollup list handlers (tool_names, versions, fix_layers, ...) are just
    aggregate() with a fixed metric+group_by; share one shape adapter so
    paging works consistently and we don't duplicate the wiring.
    """
    if agg_result.get("is_error"):
        return agg_result
    buckets = agg_result.get("buckets", [])
    total = len(buckets)
    page = buckets[offset:offset + limit]
    return {
        "items": [
            {"key": b["key"], "count": b["count"], "pct": b.get("pct", 0.0)}
            for b in page
        ],
        "total_count": total,
        "offset": offset,
        "limit": limit,
        "has_more": total > offset + len(page),
    }


def _match_snippet(query: str, *fields: Any, window: int = 80) -> str:
    """Find the first occurrence of `query` (case-insensitive) across the
    given fields and return a centered window around it, with ellipses.
    Falls back to the first non-empty field's snippet when no match."""
    q = (query or "").lower()
    for f in fields:
        if not f:
            continue
        s = f if isinstance(f, str) else json.dumps(f, default=str, ensure_ascii=False)
        s_low = s.lower()
        idx = s_low.find(q) if q else -1
        if idx >= 0:
            start = max(0, idx - window // 2)
            end = min(len(s), idx + len(q) + window // 2)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(s) else ""
            return prefix + s[start:end] + suffix
    for f in fields:
        if f:
            return _snippet(f)
    return ""


class _AggUnknownGroupBy(Exception):
    """Raised by _agg_* helpers when the group_by key isn't recognized for
    the chosen metric — top-level _tool_aggregate translates this into a
    clean tool_error envelope without burying tracebacks."""

    def __init__(self, group_by: str) -> None:
        super().__init__(group_by)
        self.group_by = group_by


def _parse_json_list(value: Any) -> list:
    """Best-effort parse a JSON-array string (CH stores tags as String).
    Returns [] for null/blank/non-list."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _expand_set(expand: Any) -> set[str]:
    """Normalize the `expand` argument into a dot-path set.

    Accepts None, str (single path), or list[str]. Empty by default —
    every field stays default-light unless explicitly expanded.
    """
    if expand is None:
        return set()
    if isinstance(expand, str):
        return {expand}
    if isinstance(expand, (list, tuple, set)):
        return {str(p) for p in expand if p}
    return set()

logger = structlog.get_logger(__name__)


# ----------------------------------------------------------------------------
# AGENT
# ----------------------------------------------------------------------------


class ClusterAnalysisAgent:
    """Agentic tool-loop agent that investigates a TraceErrorGroup."""

    # Per-entity prefix for the agent-facing alias labels. UUIDs never reach
    # the LLM — every entity is referenced via a short stable label minted
    # per run (T01, Sp01, V01, ...). Reduces token cost and hallucination
    # rate without changing the underlying data model.
    _ALIAS_PREFIXES: dict[str, str] = {
        "trace": "T",
        "span": "Sp",
        "eval_result": "V",
        "session": "Sess",
        "scan_issue": "I",
        "version": "Ver",
        "eval_config": "Cfg",
        "prior_analysis": "An",
    }

    # Filter keys whose values the LLM passes as minted labels (T01/Sess01/…);
    # must be resolved to UUIDs before the WHERE, else it matches nothing. Key
    # -> alias entity.
    _ID_VALUED_FILTER_KEYS: dict[str, str] = {
        "trace_id": "trace",
        "span_id": "span",
        "session_id": "session",
        "session": "session",
        "trace_session_id": "session",
        "version": "version",
    }

    def __init__(
        self,
        cluster_id: str,
        project_id: str,
        question: str | None = None,
        on_event: EventCallback | None = None,
        model: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        thinking_budget: int | None = DEFAULT_THINKING_BUDGET,
        stop_event: threading.Event | None = None,
    ):
        """
        Args:
            cluster_id: TraceErrorGroup reference. Accepts the UUID PK or the
                CharField label (e.g. "E-1B23E5E9"). The agent resolves to
                both and uses the LABEL for all LLM-facing rendering.
            question: Optional user-provided question (chat-mode framing).
                When None, the agent runs self-directed (cached card path).
            on_event: Optional streaming callback. Caller receives
                ('tool_call' | 'tool_result' | 'finding' | 'synthesis' |
                'done' | 'error', payload_dict) as the loop progresses.
                Defaults to a no-op sink — agent runs synchronously.
            project_id: REQUIRED. The authenticated request scope; the cluster
                ref resolves within it (a foreign-project cluster can't resolve
                and have the agent adopt it), and it preloads the project's
                schema context into the system prompt.
            model: Gateway-resolved model name. Defaults to Vertex
                Gemini 3.6 Flash with thinking ENABLED (cluster RCA needs
                deep reasoning across tool results).
            temperature: Sampling temperature for the loop.
            max_tokens: Max output tokens per turn.
            stop_event: Optional cooperative-cancel signal. The run loop
                checks it at the top of each turn; once set it stops cleanly
                and returns the partial result with accumulated cost_usd (so
                spend incurred before the Stop is still billed). The agent runs
                in a worker thread that the event loop cannot interrupt, so this
                Event is the only way to halt an in-flight run.
        """
        self.question = question
        self.findings: list[ClusterFinding] = []
        self.synthesis: ClusterSynthesis | None = None
        self._counter_lock = threading.Lock()
        self._finding_counter = 0
        self._investigation_complete = False
        self.on_event: EventCallback = on_event or _noop_event
        # Cooperative-cancel signal (set by the consumer's explicit-Stop
        # handler). None when the caller doesn't wire cancellation.
        self._stop_event: threading.Event | None = stop_event

        # --- Alias map state ------------------------------------------------
        # label -> uuid (forward), uuid -> label (reverse, dedupes mints).
        # Cluster aliases use the existing CharField (E-XXX / S-XXX) rather
        # than minted T01-style labels — they're already model-friendly.
        self._alias_to_uuid: dict[str, str] = {}
        self._uuid_to_alias: dict[str, str] = {}
        self._alias_counters: dict[str, int] = {}

        # --- Sub-tool state -------------------------------------------------
        # Per-run trace read cache: same trace_uuid asked twice is free, and
        # after compaction drops an old tool result a re-read costs nothing.
        self._trace_summary_cache: dict[tuple, dict] = {}
        # Per-run cluster→trace_uuids cache (every aggregate / list-rollup
        # call wants this; one DB round-trip per cluster is enough).
        self._cluster_trace_uuids_cache: dict[str, list[str]] = {}
        self._cluster_session_uuids_cache: dict[str, list[str]] = {}
        # Per-run span caches — a trace's spans are read at most once from CH;
        # _span_index flattens them by span_id for read(span) hits.
        self._trace_spans_cache: dict[str, list[dict]] = {}
        self._span_index: dict[str, dict] = {}
        # Debounce: (tool, canonical-args) → turn it first succeeded. Exact
        # repeats are returned as a nudge instead of re-executed.
        self._call_history: dict[str, int] = {}
        # Event-triggered convergence: group_by → (dominant value, pct) for each
        # dimension where the cluster collapsed to one value. >=2 ⇒ localized.
        self._dominant_dims: dict[str, tuple[str, float]] = {}
        # Turn the run first converged (>=CONVERGENCE_DIMS dominant dims). The
        # loop hard-stops GRACE turns later — this model ignores soft signals.
        self._converged_at_turn: int | None = None

        # --- Cluster context (uuid + label + project) ----------------------
        cluster_ctx = self._init_cluster_context(cluster_id, project_id)
        if cluster_ctx is None:
            raise ValueError(
                f"cluster '{cluster_id}' not found or has been deleted"
            )
        self.cluster_uuid: str = cluster_ctx["uuid"]
        self.cluster_label: str = cluster_ctx["label"]
        # Public attribute — the LLM-facing identifier. UI / logs see this.
        self.cluster_id: str = self.cluster_label
        self.project_id: str = cluster_ctx["project_id"]

        # Pre-register the cluster alias both ways so any tool that receives
        # the label as filter.cluster_id can resolve it.
        self._alias_to_uuid[self.cluster_label] = self.cluster_uuid
        self._uuid_to_alias[self.cluster_uuid] = self.cluster_label

        # Preload the project's schema (custom span attribute keys) into the
        # system prompt so the agent doesn't burn turns enumerating.
        self.project_schema_context: str = build_project_schema_context(
            self.project_id
        )

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # Gemini thinking budget (tokens). None/0 = thinking off. The gateway
        # maps this to the provider's thinkingConfig and (when supported)
        # surfaces the reasoning trace as message.reasoning_content.
        self.thinking_budget = thinking_budget
        self.max_turns = CLUSTER_RCA_MAX_TURNS

        # Total cost accumulator from agentcc-gateway's x-agentcc-cost header.
        self.total_cost_usd: float = 0.0

        # Gateway-resolved OpenAI-compatible client. The gateway routes to
        # the right provider (Vertex global endpoint for 3.5-flash) and
        # handles cost tracking via response headers. No litellm in the
        # request path.
        self._gateway_client = get_gateway_client()
        if self._gateway_client is None:
            raise RuntimeError(
                "agentcc-gateway client is not configured — cluster RCA "
                "requires the gateway to be reachable."
            )

    # ------------------------------------------------------------------------
    # ALIAS MAP — UUID handicap for the LLM
    # ------------------------------------------------------------------------

    def _mint_alias(self, entity: str, uuid_str: str | None) -> str | None:
        """Mint or return the stable label for this entity's UUID.

        Idempotent: minting the same UUID twice returns the same label. Used
        on every tool-result row that exposes an entity ID to the LLM.
        Unknown entities pass through unchanged (no alias scheme defined).
        """
        if uuid_str is None:
            return None
        uuid_str = str(uuid_str)
        prefix = self._ALIAS_PREFIXES.get(entity)
        if prefix is None:
            return uuid_str
        with self._counter_lock:
            existing = self._uuid_to_alias.get(uuid_str)
            if existing is not None:
                return existing
            self._alias_counters.setdefault(entity, 0)
            self._alias_counters[entity] += 1
            label = f"{prefix}{self._alias_counters[entity]:02d}"
            self._alias_to_uuid[label] = uuid_str
            self._uuid_to_alias[uuid_str] = label
            return label

    def _resolve_scoped_trace_uuids(
        self, filter: dict, cluster_uuid: str | None = None
    ) -> tuple[list[str] | None, dict | None]:
        """Blast-radius reducer + ClickHouse filter narrowing.

        cluster_id is NOT a filter — it's the scope. We resolve it to the
        cluster's stored trace_uuids (the blast radius), then narrow by any
        attr.* / eval.* / ann.* / column filters via the prod-proven
        ClickHouseFilterBuilder (CH-native; same machine the FE filter UI
        drives). cluster_id never enters the filter builder.

        Tenant enforce, not trust: for LLM-driven calls the scope is ALWAYS the
        agent's own init-validated ``self.cluster_uuid``. ``filter.cluster_id``
        is required by the tool schema but never honored for scope —
        ``_resolve_alias`` passes a raw UUID through unchanged, so honoring it
        would let a foreign id (pasted into a filter) widen the blast radius past
        this tenant. Internal callers that legitimately scope to a different,
        already-project-scoped cluster pass ``cluster_uuid`` explicitly.

        Returns (surviving_trace_uuids, None) on success, (None, error) on
        bad input. AND semantics across all filter families.
        """

        if not filter.get("cluster_id"):
            return None, tool_error(
                ERROR_CODE_INVALID_FILTER,
                "filter.cluster_id is required — it scopes the query to one "
                "cluster's traces.",
            )
        cluster_uuid = cluster_uuid or self.cluster_uuid

        blast = self._cluster_trace_uuids(cluster_uuid)
        rest = {k: v for k, v in filter.items() if k != "cluster_id"}
        if not rest or not blast:
            return list(blast), None

        # Canonical filter contract → ClickHouse WHERE (handles attr/eval/
        # ann/column families). scoped_trace_ids returns None when CH is
        # unavailable — surface that rather than silently returning the
        # unfiltered set (which would be a wrong, over-broad answer).
        rest = self._resolve_filter_aliases(rest)
        canonical = to_canonical(rest)
        surviving = scoped_trace_ids(self.project_id, blast, canonical)
        if surviving is None:
            return None, tool_error(
                ERROR_CODE_UNAVAILABLE,
                "filter evaluation requires ClickHouse, which is "
                "unavailable. Retry with cluster_id only, or once CH is back.",
            )
        return surviving, None

    def _resolve_filter_aliases(self, flt: dict) -> dict:
        """Map id-valued filter labels (T01/Sess01/…) to UUIDs before the
        canonical filter. Only _ID_VALUED_FILTER_KEYS keys, and only values that
        resolve (a literal name / already-UUID is left as-is). Handles scalars
        and single-op DSL dicts ({eq/in/not_in: scalar|list}).
        """

        def _resolve_one(v):
            if isinstance(v, str):
                return self._resolve_alias(v) or v
            if isinstance(v, list):
                return [self._resolve_alias(x) or x if isinstance(x, str) else x
                        for x in v]
            return v

        out: dict = {}
        for key, value in flt.items():
            if key not in self._ID_VALUED_FILTER_KEYS:
                out[key] = value
                continue
            if isinstance(value, dict) and len(value) == 1:
                (op_key, op_val), = value.items()
                out[key] = {op_key: _resolve_one(op_val)}
            else:
                out[key] = _resolve_one(value)
        return out

    def _spans_for_trace(self, trace_uuid: str) -> list[dict]:
        """A trace's spans from ClickHouse, cached for the run.

        The blast-radius traces are read at most once each: the summarizer,
        read(trace) skeleton, and read(span) all funnel through here, so we
        never re-query CH for spans of an already-touched trace. Also indexes
        every span by id so read(span) can serve from memory.
        """
        cached = self._trace_spans_cache.get(trace_uuid)
        if cached is not None:
            return cached

        spans = spans_for_trace(self.project_id, trace_uuid)
        self._trace_spans_cache[trace_uuid] = spans
        for sp in spans:
            self._span_index[sp["span_id"]] = sp
        return spans

    def _lookup_span(self, span_uuid: str) -> dict | None:
        """One span by id — from the per-run index if its trace was already
        fetched, else a single CH read (which also indexes it)."""
        hit = self._span_index.get(span_uuid)
        if hit is not None:
            return hit

        sp = read_span(self.project_id, span_uuid)
        if sp is not None:
            self._span_index[sp["span_id"]] = sp
        return sp

    def _cluster_trace_uuids(self, cluster_uuid: str) -> list[str]:
        """All trace UUIDs in the cluster scope (deduped, fast path).

        Cheap and indexed via tracer_ect_cluster_created_idx. Cache on the
        agent so repeated queries within the run don't re-issue.

        Session-level eval clusters have no trace on the junction — the unit is
        a session, which fans out to N traces. For those we expand each member
        session to its traces so the blast radius (and everything that scopes
        off it — list/search/aggregate by trace) sees the real evidence.
        """
        cached = self._cluster_trace_uuids_cache.get(cluster_uuid)
        if cached is not None:
            return cached

        out: set[str] = set(selectors.cluster_member_trace_ids(cluster_uuid))

        # Fan session members out to their traces via ClickHouse: collector
        # (CH-only) sessions have no PG Trace rows, so a PG ``session_id`` join
        # resolves nothing. session_trace_ids reads the spans table and resolves
        # new->old straddler ids — the dead Trace.session walk did neither.
        session_uuids = self._cluster_session_uuids(cluster_uuid)
        if session_uuids:
            with get_reader() as reader:
                for sid in session_uuids:
                    out.update(reader.session_trace_ids(self.project_id, sid))

        result = list(out)
        self._cluster_trace_uuids_cache[cluster_uuid] = result
        return result

    def _cluster_session_uuids(self, cluster_uuid: str) -> list[str]:
        """Session UUIDs a session-level eval cluster groups (empty for every
        other cluster kind). Cached per run."""
        cached = self._cluster_session_uuids_cache.get(cluster_uuid)
        if cached is not None:
            return cached
        result = selectors.cluster_member_session_ids(cluster_uuid)
        self._cluster_session_uuids_cache[cluster_uuid] = result
        return result

    def _eval_scope_q(self, cluster_uuid: str, trace_uuids: list[str]) -> Q:
        """EvalLogger filter for a cluster's eval evidence.

        Span/trace eval rows live on the cluster's traces, but a session
        eval row has NO trace (trace FK NULL) — it lives on the session that
        formed the cluster. Scoping by trace alone would silently drop the
        single most important signal for a session cluster, so OR in the
        cluster's sessions when there are any.
        """
        q = Q(trace_id__in=trace_uuids)
        session_uuids = self._cluster_session_uuids(cluster_uuid)
        if session_uuids:
            q |= Q(trace_session_id__in=session_uuids)
        return q

    def _resolve_alias(self, ref: str | None) -> str | None:
        """Reverse-resolve a label / cluster CharField / raw UUID to its UUID.

        - Known alias (T01, Sp03, E-XXX, ...) → registered UUID
        - UUID-shaped string → pass-through (caller still runs the query)
        - Anything else → None (caller surfaces not_found / invalid_args)
        """
        if ref is None:
            return None
        ref = str(ref)
        hit = self._alias_to_uuid.get(ref)
        if hit is not None:
            return hit
        # UUID-shaped pass-through. Lets the LLM occasionally paste a raw
        # UUID without the resolver blowing up — DB still validates.
        try:
            _uuid.UUID(ref)
            return ref
        except (ValueError, AttributeError):
            return None

    def _evidence_uuids(self, refs) -> list[str]:
        """Evidence lists leave the agent (persisted, shipped to Linear), so
        resolve the LLM-facing aliases (T01/Sp03/...) back to real UUIDs.
        Unresolvable refs are dropped — an alias nobody can look up is noise
        outside the run."""
        out: list[str] = []
        for ref in refs or []:
            if not isinstance(ref, str) or not ref.strip():
                continue
            resolved = self._resolve_alias(ref.strip())
            if resolved:
                out.append(resolved)
        return out

    @staticmethod
    def _init_cluster_context(cluster_ref: str, project_id: str) -> dict | None:
        """Resolve cluster_ref (UUID or CharField label) → full context.

        Returns {uuid, label, project_id} or None on no-match. ``project_id``
        (the authenticated request scope) is REQUIRED and enforced on BOTH
        branches — a cluster id/label from another project can't resolve and
        have the agent adopt that foreign project.
        """
        return selectors.resolve_cluster_context(cluster_ref, project_id)

    # ------------------------------------------------------------------------
    # PUBLIC ENTRY
    # ------------------------------------------------------------------------

    def run(self) -> ClusterAnalysisResult:
        """Run the agentic investigation loop and return the aggregated result."""
        logger.info(
            "cluster_rca_agent_started",
            cluster_id=self.cluster_id,
            has_question=self.question is not None,
        )

        # Progress pings during the (LLM-free) setup so the Analyze loader shows
        # real activity instead of dead-air while the first turn's round-trip is
        # in flight. Only surfaced live by the FE until the first reasoning/step
        # frame replaces the loader; not persisted.
        self.on_event(
            "status",
            {"phase": "reading_context", "detail": "Reading cluster context…"},
        )
        messages = self._initial_messages()
        terminated_reason = "max_turns"
        last_error: str | None = None
        turn = 0
        wrapup_nudged = False

        self.on_event(
            "status",
            {"phase": "investigating", "detail": "Investigating the failures…"},
        )
        for turn in range(self.max_turns):
            try:
                # Cooperative-cancel: the consumer's explicit-Stop handler sets
                # this event. Stop BEFORE the turn's LLM round-trip so no extra
                # spend is incurred; the post-loop return still carries the
                # accumulated cost_usd so spend so far is billed. "stopped" is
                # deliberately NOT in the force-synthesis backstop set below — a
                # user Stop should not burn another LLM call to synthesize.
                if self._stop_event is not None and self._stop_event.is_set():
                    terminated_reason = "stopped"
                    logger.info(
                        "cluster_rca_agent_stopped",
                        cluster_id=self.cluster_id,
                        turn=turn + 1,
                    )
                    break

                # Spend safety bound: the loop is otherwise bounded only by turns
                if self.total_cost_usd >= CLUSTER_RCA_COST_CEILING_USD:
                    terminated_reason = "cost_ceiling"
                    logger.warning(
                        "cluster_rca_cost_ceiling_hit",
                        cluster_id=self.cluster_id,
                        turn=turn + 1,
                        cost_usd=round(self.total_cost_usd, 4),
                    )
                    break

                # Budget-pressure nudge (goal-class termination): as the ceiling
                # approaches with no synthesis yet, tell the model to stop opening
                # new threads and conclude. Injected once; the hard backstop after
                # the loop guarantees a close if it still doesn't.
                turns_left = self.max_turns - turn
                if (
                    turns_left <= CLUSTER_RCA_WRAPUP_TURNS
                    and not wrapup_nudged
                    and self.synthesis is None
                ):
                    wrapup_nudged = True
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"You have {turns_left} turn(s) left and no "
                                "synthesis yet. Stop opening new lines of "
                                "investigation and call submit_synthesis now "
                                "with what the evidence supports. If you cannot "
                                "ground a root cause, describe the observable "
                                "failure and set confidence to L — that is the "
                                "correct close, not a failure."
                            ),
                        }
                    )
                # Gateway-routed OpenAI-compat tool-call. We use
                # `with_raw_response` so we can read the x-agentcc-cost
                # header the gateway sets for billing aggregation. Kept
                # non-streaming on purpose: Vertex omits thought summaries from
                # streamGenerateContent on tool-calling turns, so streaming
                # would gut the per-turn reasoning this agent is built to surface.
                create_kwargs = dict(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    tools=CLUSTER_RCA_TOOLS,
                )
                if self.thinking_budget:
                    # OpenAI-compat client rejects unknown top-level kwargs;
                    # provider-specific thinking config rides in extra_body.
                    create_kwargs["extra_body"] = {
                        "thinking_budget": self.thinking_budget
                    }
                create_kwargs["timeout"] = _TURN_TIMEOUT_S
                _result = call_llm_raw(self._gateway_client, **create_kwargs)
                self.total_cost_usd += _result.cost_usd
                response = _result.response
                choice = response.choices[0]

                # Surface the model's reasoning + any verbalized text so callers
                # (CLI harness, FE) can observe HOW the agent decides, not just
                # which tools it calls. reasoning_content is non-standard; the
                # OpenAI SDK may park it in model_extra, so read the dump.
                _msg_dump = choice.message.model_dump()
                _reasoning = _msg_dump.get("reasoning_content") or getattr(
                    choice.message, "reasoning_content", None
                )
                if _reasoning or choice.message.content:
                    self.on_event(
                        "reasoning",
                        {
                            "turn": turn + 1,
                            "reasoning": _reasoning,
                            "content": choice.message.content,
                        },
                    )

                if choice.finish_reason in ("stop", "end_turn"):
                    terminated_reason = "stop"
                    logger.info(
                        "cluster_rca_agent_stopped_no_tool",
                        cluster_id=self.cluster_id,
                        turn=turn + 1,
                    )
                    break

                if not choice.message.tool_calls:
                    logger.warning(
                        "cluster_rca_agent_no_tool_calls_no_stop",
                        cluster_id=self.cluster_id,
                        turn=turn + 1,
                        finish_reason=choice.finish_reason,
                    )
                    terminated_reason = "stop"
                    break

                messages.append(choice.message.model_dump())

                for tool_call in choice.message.tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        tool_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}

                    self.on_event(
                        "tool_call",
                        {
                            "tool": tool_name,
                            "args": tool_args,
                            "turn": turn + 1,
                        },
                    )
                    result = self._execute_tool(tool_name, tool_args)
                    self.on_event(
                        "tool_result",
                        {
                            "tool": tool_name,
                            "result": result,
                            "turn": turn + 1,
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_name,
                            "content": json.dumps(result, default=str),
                        }
                    )

                self._compact_old_tool_results(messages)

                if self._investigation_complete:
                    terminated_reason = "synthesis_submitted"
                    logger.info(
                        "cluster_rca_agent_synthesis_submitted",
                        cluster_id=self.cluster_id,
                        turn=turn + 1,
                    )
                    break

                # Convergence grace stop. The injected "conclude now" signal is
                # reliably ignored by this model, so once the cause is localized
                # (>=2 dominant dimensions) we give it GRACE turns to quote
                # evidence, then hard-stop and force the synthesis below.
                if self._converged_at_turn is None and (
                    len(self._dominant_dims) >= CLUSTER_RCA_CONVERGENCE_DIMS
                ):
                    self._converged_at_turn = turn
                if (
                    self._converged_at_turn is not None
                    and turn - self._converged_at_turn >= CLUSTER_RCA_CONVERGENCE_GRACE
                ):
                    terminated_reason = "converged"
                    logger.info(
                        "cluster_rca_agent_converged_grace_stop",
                        cluster_id=self.cluster_id,
                        turn=turn + 1,
                        converged_at=self._converged_at_turn + 1,
                    )
                    break

            except Exception as exc:
                last_error = str(exc)
                logger.exception(
                    "cluster_rca_agent_turn_failed",
                    cluster_id=self.cluster_id,
                    turn=turn + 1,
                )
                self.on_event(
                    "error",
                    {"message": str(exc), "turn": turn + 1},
                )
                terminated_reason = "error"
                break

        # Resource-class termination backstop: a run must never end on dead-air.
        # If the loop exhausted its budget (or the model stopped) without a
        # synthesis, force one so the stream always closes on an answer.
        if self.synthesis is None and terminated_reason in (
            "max_turns",
            "stop",
            "converged",
            "error",
            "cost_ceiling",
        ):
            self._force_synthesis(messages, turn + 1)
            # Keep terminated_reason="error" even when synthesis was salvaged, so
            # consumers still see the run failed mid-investigation.
            if self.synthesis is not None and terminated_reason != "error":
                terminated_reason = "synthesis_forced"

        self.on_event(
            "done",
            {
                "turn_count": turn + 1,
                "terminated_reason": terminated_reason,
                "error": last_error,
                "finding_count": len(self.findings),
                "has_synthesis": self.synthesis is not None,
            },
        )

        return ClusterAnalysisResult(
            cluster_id=self.cluster_id,
            synthesis=self.synthesis,
            findings=self.findings,
            turn_count=turn + 1,
            terminated_reason=terminated_reason,
            error=last_error,
            cost_usd=self.total_cost_usd,
        )

    def _force_synthesis(self, messages: list[dict], turn: int) -> None:
        """Force a terminal synthesis when the loop ended without one.

        The Gemini path does not honor tool_choice, so we force the close by
        exposing ONLY submit_synthesis plus an explicit instruction. If the
        model still won't call it (or the call errors), fall back to a
        deterministic low-confidence synthesis built from the findings so the
        run always yields an answer instead of dead-air.
        """
        self.on_event(
            "reasoning",
            {
                "turn": turn,
                "reasoning": "Budget exhausted — forcing a synthesis from the evidence gathered.",
                "content": None,
            },
        )
        forced_messages = messages + [
            {
                "role": "user",
                "content": (
                    "Investigation budget exhausted. Call submit_synthesis NOW "
                    "from the evidence already gathered — do not investigate "
                    "further. If no root cause is grounded in the telemetry, "
                    "describe the observable failure and set confidence to L."
                ),
            }
        ]
        submit_only = [
            t
            for t in CLUSTER_RCA_TOOLS
            if t["function"]["name"] == TERMINAL_TOOL_NAME
        ]
        try:
            create_kwargs = dict(
                model=self.model,
                messages=forced_messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                tools=submit_only,
            )
            if self.thinking_budget:
                create_kwargs["extra_body"] = {
                    "thinking_budget": self.thinking_budget
                }
            _result = call_llm_raw(self._gateway_client, **create_kwargs)
            self.total_cost_usd += _result.cost_usd
            response = _result.response
            choice = response.choices[0]
            for tool_call in choice.message.tool_calls or []:
                if tool_call.function.name != TERMINAL_TOOL_NAME:
                    continue
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}
                self.on_event(
                    "tool_call",
                    {"tool": TERMINAL_TOOL_NAME, "args": tool_args, "turn": turn},
                )
                result = self._execute_tool(TERMINAL_TOOL_NAME, tool_args)
                self.on_event(
                    "tool_result",
                    {"tool": TERMINAL_TOOL_NAME, "result": result, "turn": turn},
                )
                break
        except Exception:
            logger.exception(
                "cluster_rca_force_synthesis_failed", cluster_id=self.cluster_id
            )

        if self.synthesis is None:
            self._fallback_synthesis()

    def _fallback_synthesis(self) -> None:
        """Deterministic last resort: synthesize from findings without an LLM
        call so a run never returns a null synthesis to the stream."""
        if self.findings:
            top = self.findings[0]
            lead = (
                top.title
                or top.description
                or "A recurring failure pattern was observed across the cluster"
            )
            synth = (
                f"{lead.rstrip('.')}. The investigation reached its turn budget "
                "before a single root cause was confirmed; this is the strongest "
                "pattern observed across the cluster."
            )
            evidence = list(top.evidence_trace_ids)
        else:
            synth = (
                "No dominant failure pattern emerged within the investigation "
                "budget; the cluster's traces did not share one clear cause in "
                "the telemetry examined."
            )
            evidence = []
        self.synthesis = ClusterSynthesis(
            synthesis=synth,
            fix="Inspect the cited traces directly to confirm the pattern before acting.",
            confidence=Confidence.LOW,
            evidence_trace_ids=evidence,
        )
        self._investigation_complete = True
        self.on_event("synthesis", asdict(self.synthesis))

    # ------------------------------------------------------------------------
    # PROMPT BUILDERS
    # ------------------------------------------------------------------------

    def _initial_messages(self) -> list[dict]:
        """Build system + initial user prompt as plain OpenAI-compat messages.

        The system prompt is the static harness instructions PLUS the
        project's preloaded schema context (available span attribute keys,
        eval-name hints). No cache_control breakpoints — that's an
        Anthropic-only directive and breaks Vertex (its caching API has a
        4096-token minimum and rejects sub-threshold requests). Vertex's own
        caching kicks in transparently on hot prefixes if it's going to.
        """
        system_text = SYSTEM_PROMPT.replace(
            "The 30-turn ceiling",
            f"The {self.max_turns}-turn ceiling",
        )
        if self.project_schema_context:
            system_text = system_text + "\n\n" + self.project_schema_context

        user_text = self._initial_user_prompt()
        return [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ]

    def _initial_user_prompt(self) -> str:
        if self.question:
            return (
                f"User question: {self.question}\n\n"
                f"cluster_id: {self.cluster_id}\n\n"
                f"Investigate and answer. Use submit_synthesis when done."
            )
        return (
            f"Investigate cluster_id={self.cluster_id} and produce a "
            f"root-cause synthesis.\n\n"
            f"Start with: read(entity='cluster', id='{self.cluster_id}')"
        )

    # ------------------------------------------------------------------------
    # TOP-LEVEL TOOL DISPATCH
    # ------------------------------------------------------------------------

    def _execute_tool(self, tool_name: str, args: dict) -> dict:
        """Dispatch a tool call to its handler. Returns a plain dict."""
        handlers = {
            "list": self._tool_list,
            "search": self._tool_search,
            "read": self._tool_read,
            "aggregate": self._tool_aggregate,
            "compare": self._tool_compare,
            "timeline": self._tool_timeline,
            "submit_finding": self._tool_submit_finding,
            TERMINAL_TOOL_NAME: self._tool_submit_synthesis,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            return tool_error(
                ERROR_CODE_INVALID_ARGS, f"Unknown tool: {tool_name}"
            )

        # Auto-scope: the agent works within ONE cluster, so default the run's
        # cluster_id into the filter. The LLM never has to repeat it and never
        # trips "filter.cluster_id is required".
        if tool_name in ("list", "search", "aggregate", "timeline"):
            flt = args.get("filter")
            if not isinstance(flt, dict):
                flt = {}
            flt.setdefault("cluster_id", self.cluster_id)
            args["filter"] = flt
        elif tool_name == "compare":
            # compare's two arms are themselves filters — scope each to the run.
            for arm in ("set_a", "set_b"):
                s = args.get(arm)
                if isinstance(s, dict):
                    s.setdefault("cluster_id", self.cluster_id)
                    args[arm] = s

        # Debounce: an exact-duplicate call wastes a turn. Return a nudge with
        # the prior result instead of re-executing. Terminal tools are exempt.
        debounced = tool_name not in (TERMINAL_TOOL_NAME, "submit_finding")
        call_key = f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
        if debounced and call_key in self._call_history:
            return {
                "is_error": True,
                "code": "duplicate_call",
                "message": (
                    f"You already called {tool_name} with these exact arguments "
                    "and it succeeded. Don't repeat identical calls — reuse that "
                    "result, or change the filter/query to learn something new. "
                    "If you have enough to answer, submit_finding / submit_synthesis."
                ),
            }

        try:
            result = handler(**args)
        except TypeError as exc:
            # Don't leak the internal method name at the LLM; keep the useful
            # "missing required argument 'x'" part.
            msg = str(exc).replace("ClusterAnalysisAgent._tool_", "").replace("()", "")
            return tool_error(ERROR_CODE_INVALID_ARGS, msg)
        except Exception as exc:
            logger.exception(
                "cluster_rca_tool_failed",
                cluster_id=self.cluster_id,
                tool_name=tool_name,
            )
            return tool_error(
                ERROR_CODE_UNAVAILABLE, f"{tool_name}: {exc}"
            )

        if debounced and not (isinstance(result, dict) and result.get("is_error")):
            self._call_history[call_key] = 1

        # Event-triggered convergence. Track dimensions where the cluster
        # collapses to one dominant value; once two agree the cause is
        # localized, so annotate the result with a conclude-now signal. Fires
        # on the triggering aggregate AND re-fires on any later investigative
        # call before synthesis — those extra calls ARE the over-run we stop.
        if isinstance(result, dict) and not result.get("is_error"):
            if tool_name == "aggregate":
                self._record_dominant_dimension(args.get("group_by"), result)
            if (
                tool_name not in (TERMINAL_TOOL_NAME, "submit_finding")
                and len(self._dominant_dims) >= CLUSTER_RCA_CONVERGENCE_DIMS
            ):
                result["_convergence_signal"] = self._convergence_hint()
        return result

    def _record_dominant_dimension(self, group_by, result: dict) -> None:
        """Remember a dimension on which the cluster collapsed to one value.

        A dimension counts as dominant when its top bucket holds
        >= CLUSTER_RCA_DOMINANT_PCT of a non-trivial total — a 1/1=100% split
        is not a pattern, so require CLUSTER_RCA_DOMINANT_MIN_TOTAL.
        """
        if not group_by:
            return
        buckets = result.get("buckets") or []
        total = result.get("total") or 0
        if not buckets or total < CLUSTER_RCA_DOMINANT_MIN_TOTAL:
            return
        top = max(buckets, key=lambda b: b.get("count", 0))
        pct = top.get("pct", 0) or 0
        if pct >= CLUSTER_RCA_DOMINANT_PCT:
            self._dominant_dims[str(group_by)] = (str(top.get("key")), float(pct))

    def _convergence_hint(self) -> str:
        """Conclude-now nudge naming the dimensions that already localized the
        cause — injected into tool results once the cluster has converged."""
        parts = [
            f"{dim}={key} ({pct:.0f}%)"
            for dim, (key, pct) in self._dominant_dims.items()
        ]
        return (
            f"CONVERGED — the cluster collapses to a dominant value on "
            f"{len(self._dominant_dims)} dimensions: {'; '.join(parts)}. You have "
            "the WHAT and the WHERE. Call submit_synthesis now unless your next "
            "call answers a NEW question that would change the FIX; re-reading "
            "individual traces to confirm what these aggregates already proved "
            "will not, and wastes turns the user is watching."
        )

    # ------------------------------------------------------------------------
    # READ-TOOL DISPATCH (inner, per dimension/entity)
    # ------------------------------------------------------------------------

    def _tool_list(
        self,
        dimension: str,
        filter: dict | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        handlers = {
            "traces": self._list_traces,
            "spans": self._list_spans,
            "sessions": self._list_sessions,
            "tool_names": self._list_tool_names,
            "error_messages": self._list_error_messages,
            "versions": self._list_versions,
            "eval_results": self._list_eval_results,
            "scan_issues": self._list_scan_issues,
            "scan_issue_categories": self._list_scan_issue_categories,
            "fix_layers": self._list_fix_layers,
            "attribute_keys": self._list_attribute_keys,
            "prior_analyses": self._list_prior_analyses,
        }
        handler = handlers.get(dimension)
        if handler is None:
            return tool_error(
                ERROR_CODE_INVALID_ARGS, f"Unknown dimension: {dimension}"
            )
        # Clamp to [1,100]/[0,∞): a negative limit otherwise reaches the ORM as
        # a negative slice ("Negative indexing not supported"), surfacing as a
        # cryptic crash instead of a sane page.
        return handler(filter or {}, _clamp(limit, 1, 100), max(offset, 0))

    def _tool_search(
        self,
        entity: str,
        query: str,
        filter: dict | None = None,
        limit: int = 20,
    ) -> dict:
        handlers = {
            "traces": self._search_traces,
            "spans": self._search_spans,
            "sessions": self._search_sessions,
            "scan_issues": self._search_scan_issues,
        }
        handler = handlers.get(entity)
        if handler is None:
            return tool_error(
                ERROR_CODE_INVALID_ARGS, f"Unknown entity: {entity}"
            )
        return handler(query, filter or {}, _clamp(limit, 1, 100))

    def _tool_read(
        self,
        entity: str,
        id: str,
        depth: str = "summary",
        expand: Any = None,
    ) -> dict:
        """Dispatch read() to per-entity handlers.

        depth is meaningful for trace/session. expand opts the caller into
        full verbatim fields on a per-handler basis (dot-paths like
        "root.input" / "input" / "output"). Unknown expand paths are
        silently ignored.
        """
        exp = _expand_set(expand)
        handlers = {
            "cluster": lambda i: self._read_cluster(i),
            "trace": lambda i: self._read_trace(i, depth, exp),
            "span": lambda i: self._read_span(i, exp),
            "session": lambda i: self._read_session(i, depth),
            "eval_result": lambda i: self._read_eval_result(i, exp),
            "eval_config": lambda i: self._read_eval_config(i),
            "version": lambda i: self._read_version(i),
            "scan_issue": lambda i: self._read_scan_issue(i),
            "prior_analysis": lambda i: self._read_prior_analysis(i),
        }
        handler = handlers.get(entity)
        if handler is None:
            return tool_error(
                ERROR_CODE_INVALID_ARGS, f"Unknown entity: {entity}"
            )
        return handler(id)

    def _tool_aggregate(
        self, metric: str, filter: dict, group_by: str
    ) -> dict:
        """Count-by-group across the cluster's (optionally filtered) traces.

        filter:   cluster_id (required, the blast radius) + optional
                  attr.* / eval.* / column narrowing.
        metric:   "trace_count" | "span_count" | "scan_issue_count"
        group_by: built-in keys (version, session_id, scan_issue_*,
                  span_tool_name|status|type, eval_metric), OR
                  "attr.<key>" → ClickHouse value distribution.
                  eval.* / ann.* / time-bucket group_bys still deferred.

        Returns {buckets: [{key, count, pct}], total, group_by, metric}.
        """

        if metric not in ("trace_count", "span_count", "scan_issue_count"):
            return tool_error(
                ERROR_CODE_INVALID_ARGS,
                f"metric '{metric}' not wired in v1 (use trace_count | "
                "span_count | scan_issue_count)",
            )

        # Blast radius + attr./eval./column filtering → surviving trace set.
        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        cluster_uuid = self.cluster_uuid

        if not trace_uuids:
            return {
                "buckets": [], "total": 0,
                "group_by": group_by, "metric": metric,
            }

        # attr.<key> group_by → ClickHouse value distribution.
        gb_type, gb_col = resolve_group_by(group_by)
        if gb_type == "SPAN_ATTRIBUTE":
            return self._agg_by_attribute(trace_uuids, gb_col, metric, group_by)
        if gb_type in ("EVAL_METRIC", "ANNOTATION"):
            return tool_error(
                ERROR_CODE_INVALID_ARGS,
                f"group_by='{group_by}' not wired yet — eval./ann. value "
                "bucketing is v2. Use 'eval_metric' for eval name grouping.",
            )
        if group_by in ("hour", "day", "minute"):
            return tool_error(
                ERROR_CODE_INVALID_ARGS,
                f"group_by='{group_by}' — use timeline(bucket={group_by}) "
                "for time bucketing.",
            )

        try:
            if metric == "trace_count":
                buckets, total = self._agg_trace_count(
                    trace_uuids, cluster_uuid, group_by
                )
            elif metric == "span_count":
                buckets, total = self._agg_span_count(trace_uuids, group_by)
            elif metric == "scan_issue_count":
                buckets, total = self._agg_scan_issue_count(
                    cluster_uuid, trace_uuids, group_by
                )
            else:  # pragma: no cover — guarded above
                return tool_error(ERROR_CODE_INVALID_ARGS, "unreachable")
        except _AggUnknownGroupBy as exc:
            valid = {
                "trace_count": [
                    "version", "session_id", "scan_issue_category",
                    "scan_issue_group", "scan_issue_fix_layer", "attr.<key>",
                ],
                "span_count": [
                    "span_tool_name", "span_status", "span_type", "attr.<key>",
                ],
                "scan_issue_count": [
                    "scan_issue_category", "scan_issue_group", "scan_issue_fix_layer",
                ],
            }.get(metric, [])
            return tool_error(
                ERROR_CODE_INVALID_ARGS,
                f"group_by='{exc.group_by}' is not valid for metric='{metric}'. "
                f"Valid group_by values: {', '.join(valid)}. "
                "(attr.<key> groups by any custom span attribute; "
                "list(dimension='attribute_keys') shows available keys.)",
            )

        # Sort desc by count, attach pct.
        buckets.sort(key=lambda b: b["count"], reverse=True)
        for b in buckets:
            b["pct"] = round(b["count"] / total * 100, 1) if total else 0.0
        return {
            "buckets": buckets,
            "total": total,
            "group_by": group_by,
            "metric": metric,
        }

    def _agg_trace_count(
        self,
        trace_uuids: list[str],
        cluster_uuid: str,
        group_by: str,
    ) -> tuple[list[dict[str, Any]], int]:
        """trace_count rollups grouped by version / session (CH) or
        scan_issue category|group|fix_layer / eval metric (PG relational)."""

        total = len(trace_uuids)

        # version / session_id → CH (denormalized trace columns on spans).
        if group_by in ("version", "session_id"):

            result = aggregate_trace_field(
                self.project_id, trace_uuids, group_by
            )
            if result is None:
                raise _AggUnknownGroupBy(group_by)
            buckets, tot = result
            if group_by == "session_id":
                for b in buckets:
                    b["key"] = (
                        self._mint_alias("session", b["key"])
                        if b["key"] not in (None, "(none)") else "(no session)"
                    )
            elif group_by == "version":
                for b in buckets:
                    b["key"] = (
                        self._mint_alias("version", b["key"])
                        if b["key"] not in (None, "(none)") else "(no version)"
                    )
            return buckets, tot

        if group_by in ("scan_issue_category", "scan_issue_group", "scan_issue_fix_layer"):
            field_map = {
                "scan_issue_category": "category",
                "scan_issue_group": "group",
                "scan_issue_fix_layer": "fix_layer",
            }
            field_name = field_map[group_by]
            buckets = selectors.count_scan_issue_traces_by(
                cluster_uuid, trace_uuids, field_name
            )
            return (
                [{"key": b["key"] or "(none)", "count": b["count"]} for b in buckets],
                total,
            )

        if group_by == "eval_metric":
            # Count distinct eval rows (not trace_id) so session-level results,
            # which carry no trace, still register against their metric.
            rows = selectors.count_cluster_eval_metrics(
                self._eval_scope_q(cluster_uuid, trace_uuids)
            )
            buckets = [
                {
                    "key": r["key"] or "(unnamed)",
                    "count": r["count"],
                }
                for r in rows
            ]
            # Total is the eval-row count (not trace count) — a trace can
            # carry multiple eval results, so using trace count as the
            # denominator would push pct above 100%.
            eval_total = sum(b["count"] for b in buckets)
            return buckets, eval_total

        # Fallback — unknown bare key
        raise _AggUnknownGroupBy(group_by)

    def _agg_span_count(
        self, trace_uuids: list[str], group_by: str,
    ) -> tuple[list[dict[str, Any]], int]:
        """span_count rollups across the cluster's spans (ClickHouse)."""

        result = aggregate_span_field(self.project_id, trace_uuids, group_by)
        if result is None:
            raise _AggUnknownGroupBy(group_by)
        return result

    def _agg_scan_issue_count(
        self, cluster_uuid: str, trace_uuids: list[str], group_by: str
    ) -> tuple[list[dict[str, Any]], int]:
        """scan_issue_count grouped by issue dimensions.

        Scoped to the cluster AND the surviving trace set, so attr./eval.
        filters narrow scan issues too (via scan_result__trace_id).
        """

        field_map = {
            "scan_issue_category": "category",
            "scan_issue_group": "group",
            "scan_issue_fix_layer": "fix_layer",
            "scan_issue_confidence": "confidence",
        }
        if group_by not in field_map:
            raise _AggUnknownGroupBy(group_by)
        field_name = field_map[group_by]
        buckets, total = selectors.count_scan_issues_by(
            cluster_uuid, trace_uuids, field_name
        )
        return (
            [{"key": b["key"] or "(none)", "count": b["count"]} for b in buckets],
            total,
        )

    def _agg_by_attribute(
        self,
        trace_uuids: list[str],
        attr_key: str,
        metric: str,
        group_by_label: str,
    ) -> dict:
        """attr.<key> value distribution via ClickHouse.

        metric='trace_count' → distinct traces per value;
        metric='span_count'  → spans per value.
        scan_issue_count + attr group_by isn't meaningful → error.
        """
        if metric == "scan_issue_count":
            return tool_error(
                ERROR_CODE_INVALID_ARGS,
                "attr.* group_by is not valid with metric=scan_issue_count "
                "(attributes live on spans, not scan issues).",
            )

        distinct_traces = metric == "trace_count"
        rows = aggregate_attribute_over_traces(
            self.project_id, trace_uuids, attr_key, distinct_traces=distinct_traces
        )
        buckets = [{"key": r.value, "count": r.count} for r in rows]
        total = sum(b["count"] for b in buckets)
        buckets.sort(key=lambda b: b["count"], reverse=True)
        for b in buckets:
            b["pct"] = round(b["count"] / total * 100, 1) if total else 0.0
        return {
            "buckets": buckets,
            "total": total,
            "group_by": group_by_label,
            "metric": metric,
        }

    def _tool_compare(self, set_a: dict, set_b: dict) -> dict:
        """Compare two trace populations defined by filters.

        v1 returns the basic shape: counts in each set, intersection, and
        a coarse "lift" ratio. Feature-level decomposition (which attr.X
        differs between sets) lands in v2 once attr.* aggregation is wired.
        """
        a_list, err_a = self._resolve_scoped_trace_uuids(set_a)
        if err_a:
            return err_a
        b_list, err_b = self._resolve_scoped_trace_uuids(set_b)
        if err_b:
            return err_b
        a_traces = set(a_list or [])
        b_traces = set(b_list or [])
        a_count = len(a_traces)
        b_count = len(b_traces)
        intersect = len(a_traces & b_traces)
        # Coarse lift: how concentrated set_a is, relative to set_b's size.
        lift = (a_count / b_count) if b_count else None
        return {
            "set_a_count": a_count,
            "set_b_count": b_count,
            "intersection_count": intersect,
            "lift_a_over_b": round(lift, 3) if lift is not None else None,
            "note": (
                "v1 compare reports population sizes + intersection. "
                "Feature-level decomposition (attr.* / eval.* deltas) "
                "lands when attr-aggregation arrives."
            ),
            "features": [],
        }

    def _tool_timeline(
        self, filter: dict, bucket: str = "hour"
    ) -> dict:
        """Failure count over time. bucket ∈ {minute, hour, day}.

        v1: per-bucket distinct-trace count for the cluster (ClickHouse, by
        each trace's earliest span time). The agent uses this for 'when did
        this start' / 'is it spiking now'.
        """
        if bucket not in ("minute", "hour", "day"):
            return tool_error(
                ERROR_CODE_INVALID_ARGS,
                f"bucket='{bucket}' (use minute|hour|day)",
            )
        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        if not trace_uuids:
            return {
                "buckets": [], "total": 0, "bucket_size": bucket, "deploys": [],
            }
        rows = timeline_trace_counts(self.project_id, trace_uuids, bucket)
        buckets = [
            {"bucket_start": r["bucket_start"], "count": r["count"], "deploys": []}
            for r in rows
        ]
        return {
            "buckets": buckets,
            "total": sum(b["count"] for b in buckets),
            "bucket_size": bucket,
            "deploys": [],
        }

    # ------------------------------------------------------------------------
    # PER-DIMENSION LIST HANDLERS — all stubs
    # ------------------------------------------------------------------------

    def _list_traces(self, filter: dict, limit: int, offset: int = 0) -> dict:
        """List traces in cluster scope, narrowed by any attr./eval./column filters.

        cluster_id is the blast radius; everything else narrows the surviving
        trace set (see _resolve_scoped_trace_uuids). Membership provenance
        (scanner / eval) comes from ErrorClusterTraces.
        """

        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        cluster_uuid = self.cluster_uuid
        if not trace_uuids:
            return {"items": [], "total_count": 0, "offset": offset, "limit": limit, "has_more": False}

        # Page from the scoped trace_uuids, NOT junction rows: a session cluster's
        # junction rows have trace_id NULL, so trace_id__in there returns nothing.
        # Provenance/created_at still come from junction rows where one exists;
        # session-expanded traces have none and report provenance 'unknown'.
        # Provenance comes from the junction itself (its own deleted=False is the
        # membership truth) — do NOT join the PG ``trace`` FK: collector (CH-only)
        # traces have no PG Trace row, so a ``trace__deleted`` join would drop them
        # and lose their provenance/created_at. CH ``is_deleted`` already governs
        # whether a trace's telemetry surfaces.
        memberships = selectors.cluster_memberships(cluster_uuid, trace_uuids)
        provenance: dict[str, ErrorClusterTraces] = {}
        for m in memberships:
            if m.trace_id:
                provenance.setdefault(str(m.trace_id), m)

        # Stable order: junction-backed traces first (newest-first, preserving
        # the non-session path's existing ordering), then any session-expanded
        # traces (sorted for determinism). Disjoint by construction; setdefault
        # dedups defensively.
        ordered_uuids = list(provenance)
        seen = set(ordered_uuids)
        ordered_uuids.extend(
            sorted(tid for tid in trace_uuids if tid not in seen)
        )

        total_count = len(ordered_uuids)
        page_trace_ids = ordered_uuids[offset:offset + limit]
        roots = trace_roots(self.project_id, page_trace_ids)

        items: list[dict[str, Any]] = []
        for tid in page_trace_ids:
            root = roots.get(tid, {})
            m = provenance.get(tid)
            items.append(
                {
                    "trace_id": self._mint_alias("trace", tid),
                    "_uuid": tid,
                    "name": root.get("trace_name"),
                    "input_snippet": _snippet(root.get("input")),
                    "output_snippet": _snippet(root.get("output")),
                    "has_error": root.get("has_error", False),
                    "session_id": self._mint_alias(
                        "session", root.get("trace_session_id")
                    ),
                    "created_at": (
                        m.created_at.isoformat()
                        if m and m.created_at else None
                    ),
                    "provenance": (
                        "scanner" if m and m.scan_issue_id
                        else "eval" if m and m.eval_logger_id
                        else "unknown"
                    ),
                    "scan_issue_id": self._mint_alias(
                        "scan_issue", m.scan_issue_id if m else None
                    ),
                    "eval_logger_id": self._mint_alias(
                        "eval_result", m.eval_logger_id if m else None
                    ),
                }
            )

        return {
            "items": items,
            "total_count": total_count,
            "offset": offset,
            "limit": limit,
            "has_more": total_count > offset + len(items),
        }

    def _list_spans(self, filter: dict, limit: int, offset: int = 0) -> dict:
        """List spans across the cluster's (filtered) traces (ClickHouse).
        Lean skeleton — no I/O (drill via read(span))."""
        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        if not trace_uuids:
            return {"items": [], "total_count": 0, "offset": offset, "limit": limit, "has_more": False}
        rows, total = list_spans_in_traces(
            self.project_id, trace_uuids, limit=limit, offset=offset
        )
        items = [
            {
                "span": self._mint_alias("span", s["span_id"]),
                "trace_id": self._mint_alias("trace", s.get("trace_id")),
                "parent": (
                    self._mint_alias("span", s["parent_span_id"])
                    if s.get("parent_span_id") else None
                ),
                "type": s.get("observation_type"),
                "name": s.get("name"),
                "status": s.get("status"),
                "latency_ms": s.get("latency_ms"),
            }
            for s in rows
        ]
        return {
            "items": items,
            "total_count": total,
            "offset": offset,
            "limit": limit,
            "has_more": total > offset + len(items),
        }

    def _list_sessions(self, filter: dict, limit: int, offset: int = 0) -> dict:
        """Distinct sessions present in the cluster's (filtered) traces (CH)."""
        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        if not trace_uuids:
            return {"items": [], "total_count": 0, "offset": offset, "limit": limit, "has_more": False}
        session_ids = distinct_sessions(self.project_id, trace_uuids)
        total = len(session_ids)
        page = session_ids[offset:offset + limit]
        items = [
            {"session_id": self._mint_alias("session", sid), "_uuid": sid}
            for sid in page
        ]
        return {
            "items": items,
            "total_count": total,
            "offset": offset,
            "limit": limit,
            "has_more": total > offset + len(items),
        }

    def _list_tool_names(self, filter: dict, limit: int, offset: int = 0) -> dict:
        """Distinct tool span names in cluster scope. Wraps aggregate."""
        agg = self._tool_aggregate(
            metric="span_count", filter=filter, group_by="span_tool_name",
        )
        return _rollup_to_list(agg, offset, limit)

    def _list_error_messages(self, filter: dict, limit: int, offset: int = 0) -> dict:
        """Distinct ERROR-status span status_messages in (filtered) scope (CH)."""
        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        if not trace_uuids:
            return {"items": [], "total_count": 0, "offset": offset, "limit": limit, "has_more": False}
        items, total = error_messages_in_traces(
            self.project_id, trace_uuids, limit=limit, offset=offset
        )
        return {
            "items": items, "total_count": total,
            "offset": offset, "limit": limit,
            "has_more": total > offset + len(items),
        }

    def _list_versions(self, filter: dict, limit: int, offset: int = 0) -> dict:
        """Distinct project versions in cluster scope. Wraps aggregate."""
        agg = self._tool_aggregate(
            metric="trace_count", filter=filter, group_by="version",
        )
        return _rollup_to_list(agg, offset, limit)

    def _list_eval_results(self, filter: dict, limit: int, offset: int = 0) -> dict:
        """EvalLogger rows attached to the cluster's traces (and, for a
        session cluster, the session-level rows on its sessions)."""
        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        cluster_uuid = self.cluster_uuid
        rows, total = selectors.list_cluster_eval_results(
            self._eval_scope_q(cluster_uuid, trace_uuids or []), offset, limit
        )
        items = [
            {
                "id": self._mint_alias("eval_result", er.id),
                "trace_id": self._mint_alias("trace", er.trace_id),
                "metric": (
                    er.custom_eval_config.name
                    if er.custom_eval_config_id else None
                ),
                "score": (
                    er.output_float
                    if er.output_float is not None
                    else er.output_bool
                    if er.output_bool is not None
                    else er.output_str
                    if er.output_str
                    else er.output_str_list
                ),
                "errored": bool(er.error),
                "explanation_snippet": _snippet(er.eval_explanation),
            }
            for er in rows
        ]
        return {
            "items": items,
            "total_count": total,
            "offset": offset,
            "limit": limit,
            "has_more": total > offset + len(items),
        }

    def _list_scan_issues(self, filter: dict, limit: int, offset: int = 0) -> dict:
        """TraceScanIssues attached to this cluster's (filtered) traces."""
        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        cluster_uuid = self.cluster_uuid
        if not trace_uuids:
            return {"items": [], "total_count": 0, "offset": offset, "limit": limit, "has_more": False}
        rows, total = selectors.list_cluster_scan_issues(
            cluster_uuid, trace_uuids, offset, limit
        )
        items = [
            {
                "id": self._mint_alias("scan_issue", i.id),
                "trace_id": (
                    self._mint_alias("trace", i.scan_result.trace_id)
                    if i.scan_result_id else None
                ),
                "category": i.category,
                "group": i.group,
                "fix_layer": i.fix_layer,
                "confidence": i.confidence,
                "brief": i.brief,
            }
            for i in rows
        ]
        return {
            "items": items,
            "total_count": total,
            "offset": offset,
            "limit": limit,
            "has_more": total > offset + len(items),
        }

    def _list_scan_issue_categories(
        self, filter: dict, limit: int, offset: int = 0
    ) -> dict:
        """Distinct scan_issue categories in cluster. Wraps aggregate."""
        agg = self._tool_aggregate(
            metric="scan_issue_count", filter=filter,
            group_by="scan_issue_category",
        )
        return _rollup_to_list(agg, offset, limit)

    def _list_fix_layers(self, filter: dict, limit: int, offset: int = 0) -> dict:
        """Distinct fix_layer values across the cluster's scan issues."""
        agg = self._tool_aggregate(
            metric="scan_issue_count", filter=filter,
            group_by="scan_issue_fix_layer",
        )
        return _rollup_to_list(agg, offset, limit)

    def _list_attribute_keys(self, filter: dict, limit: int, offset: int = 0) -> dict:
        """Distinct span-attribute keys in scope.

        v1 honors filter.cluster_id by walking the cluster's traces' attrs
        through ClickHouse. Falls back to project-scope when no cluster_id
        is passed.
        """
        if filter.get("cluster_id"):
            trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
            if err:
                return err
            batch_keys = list_attribute_keys_for_traces(
                self.project_id, trace_uuids or []
            )
            rows = [
                {"key": k.key, "type": k.type, "count": k.count}
                for k in batch_keys
            ]
        else:
            # Project-scope fallback (uses the existing helper).
            if not self.project_id:
                return tool_error(
                    ERROR_CODE_INVALID_FILTER,
                    "list(attribute_keys) needs filter.cluster_id or a "
                    "known project context.",
                )
            project_keys = list_attribute_keys_for_project(self.project_id)
            rows = [
                {"key": k.key, "type": k.type, "count": k.count}
                for k in project_keys
            ]
        total = len(rows)
        page = rows[offset:offset + limit]
        return {
            "items": page,
            "total_count": total,
            "offset": offset,
            "limit": limit,
            "has_more": total > offset + len(page),
        }

    def _list_prior_analyses(self, filter: dict, limit: int, offset: int = 0) -> dict:
        """Prior cluster RCA runs aren't persisted yet — see _read_prior_analysis."""
        return tool_error(
            ERROR_CODE_UNAVAILABLE,
            "prior_analyses are not persisted yet — empty list",
        )

    # ------------------------------------------------------------------------
    # PER-ENTITY SEARCH HANDLERS — all stubs
    # ------------------------------------------------------------------------

    def _search_traces(
        self, query: str, filter: dict, limit: int
    ) -> dict:
        """Substring search across the cluster's traces' span I/O (ClickHouse).
        Returns distinct matching traces with a representative snippet."""
        if not query:
            return tool_error(
                ERROR_CODE_INVALID_ARGS, "search(traces) needs a non-empty query",
            )
        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        if not trace_uuids:
            return {"items": [], "total_count": 0, "has_more": False}
        matched = search_trace_ids(
            self.project_id, trace_uuids, query, limit=limit + 1
        )
        has_more = len(matched) > limit
        matched = matched[:limit]
        roots = trace_roots(self.project_id, matched) if matched else {}
        items = [
            {
                "trace_id": self._mint_alias("trace", tid),
                "snippet": _match_snippet(
                    query,
                    (roots.get(tid) or {}).get("input"),
                    (roots.get(tid) or {}).get("output"),
                ),
            }
            for tid in matched
        ]
        return {
            "items": items,
            "total_count": len(items),
            "has_more": has_more,
        }

    def _search_spans(
        self, query: str, filter: dict, limit: int
    ) -> dict:
        """Substring search across span input/output/name/status_message (CH)."""
        if not query:
            return tool_error(
                ERROR_CODE_INVALID_ARGS, "search(spans) needs a non-empty query",
            )
        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        if not trace_uuids:
            return {"items": [], "total_count": 0, "has_more": False}
        rows = search_spans_in_traces(
            self.project_id, trace_uuids, query, limit=limit + 1
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            {
                "span": self._mint_alias("span", s["span_id"]),
                "trace_id": self._mint_alias("trace", s.get("trace_id")),
                "name": s.get("name"),
                "type": s.get("observation_type"),
                "snippet": _match_snippet(
                    query, s.get("input"), s.get("output"),
                    s.get("status_message"),
                ),
            }
            for s in rows
        ]
        return {
            "items": items,
            "total_count": len(items),
            "has_more": has_more,
        }

    def _search_sessions(
        self, query: str, filter: dict, limit: int
    ) -> dict:
        """Search session-member traces for substring; returns sessions whose
        any member trace matches (CH). 'find the session where X happened'."""
        if not query:
            return tool_error(
                ERROR_CODE_INVALID_ARGS, "search(sessions) needs a non-empty query",
            )
        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        if not trace_uuids:
            return {"items": [], "total_count": 0, "has_more": False}
        # Traces matching the text, then their distinct sessions.
        matched = search_trace_ids(self.project_id, trace_uuids, query)
        roots = trace_roots(self.project_id, matched) if matched else {}
        seen: list[str] = []
        for tid in matched:
            sid = (roots.get(tid) or {}).get("trace_session_id")
            if sid and sid not in seen:
                seen.append(sid)
        has_more = len(seen) > limit
        seen = seen[:limit]
        return {
            "items": [
                {"session_id": self._mint_alias("session", sid)} for sid in seen
            ],
            "total_count": len(seen),
            "has_more": has_more,
        }

    def _search_scan_issues(
        self, query: str, filter: dict, limit: int
    ) -> dict:
        """Substring search across TraceScanIssue.brief / category."""
        if not query:
            return tool_error(
                ERROR_CODE_INVALID_ARGS, "search(scan_issues) needs a non-empty query",
            )
        trace_uuids, err = self._resolve_scoped_trace_uuids(filter)
        if err:
            return err
        cluster_uuid = self.cluster_uuid
        if not trace_uuids:
            return {"items": [], "total_count": 0, "has_more": False}
        rows = selectors.search_cluster_scan_issues(
            cluster_uuid, trace_uuids, query, limit
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            {
                "id": self._mint_alias("scan_issue", i.id),
                "trace_id": (
                    self._mint_alias("trace", i.scan_result.trace_id)
                    if i.scan_result_id else None
                ),
                "category": i.category,
                "group": i.group,
                "fix_layer": i.fix_layer,
                "brief": i.brief,
            }
            for i in rows
        ]
        return {
            "items": items,
            "total_count": len(items),
            "has_more": has_more,
        }

    # ------------------------------------------------------------------------
    # PER-ENTITY READ HANDLERS — all stubs
    # ------------------------------------------------------------------------

    def _read_cluster(self, id: str) -> dict:
        """Read a TraceErrorGroup as the agent's baseline hypothesis.

        `id` accepts the cluster's CharField label ("E-1B23E5E9") or its
        UUID PK — both resolve via the alias map. The returned payload uses
        labels for every cross-entity reference (eval_config → Cfg01,
        success_trace → T01, ...) so the LLM stays in label-space.
        """

        uuid = self._resolve_alias(id)
        if uuid is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"cluster '{id}' not found"
            )

        group = selectors.get_cluster_for_read(uuid, self.project_id)
        if group is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"cluster '{id}' not found"
            )

        payload: dict[str, Any] = {
            # `id` is the LLM-facing label. _uuid is kept for client deep-links.
            "id": group.cluster_id,
            "_uuid": str(group.id),
            "source": group.source,
            "title": group.title,
            "status": group.status,
            "priority": group.priority,
            "fix_layer": group.fix_layer,
            "size": group.error_count,
            "unique_traces": group.unique_traces,
            "total_events": group.total_events,
            "first_seen": group.first_seen.isoformat() if group.first_seen else None,
            "last_seen": group.last_seen.isoformat() if group.last_seen else None,
            "impact": group.combined_impact,
            "description": group.combined_description,
            "trace_impact": group.trace_impact,
            "external_issue_url": group.external_issue_url,
            "external_issue_id": group.external_issue_id,
        }

        # Scanner-source fields.
        if group.source == "scanner":
            payload["issue_group"] = group.issue_group
            payload["issue_category"] = group.issue_category

        # Eval-source fields.
        if group.source == "eval":
            # The unit of failure (span / trace / session) — drives how the
            # agent reads evidence and how the synthesis should phrase scope.
            payload["eval_target_type"] = group.eval_target_type or "span"
            if group.eval_config_id:
                payload["eval_config"] = {
                    "id": self._mint_alias("eval_config", group.eval_config.id),
                    "_uuid": str(group.eval_config.id),
                    "name": group.eval_config.name,
                }

        # Pointer to the precomputed nearest success trace — the agent can
        # `read(trace, <label>, "summary")` to contrast.
        if group.success_trace_id:
            # Read the FK column, not the related object: a collector success
            # trace has no PG Trace row, so group.success_trace would be None.
            payload["success_trace_id"] = self._mint_alias(
                "trace", group.success_trace_id
            )

        # Data-availability manifest: state up front which telemetry layers
        # actually exist for this cluster, so the agent doesn't spend turns
        # discovering (e.g.) that there are no spans to drill into. The evidence
        # layer is source-dependent — scanner clusters carry scan-issue briefs,
        # eval clusters carry eval results — so report (and, when span-less,
        # steer toward) whichever one THIS cluster actually has. Best-effort —
        # a manifest failure must never break the cluster read.
        try:
            trace_uuids, _terr = self._resolve_scoped_trace_uuids(
                {"cluster_id": group.cluster_id}, cluster_uuid=str(group.id)
            )
            if trace_uuids:
                try:
                    _b, span_total = self._agg_span_count(trace_uuids, "span_status")
                except Exception:
                    span_total = 0

                telemetry: dict[str, Any] = {
                    "traces": len(trace_uuids),
                    "spans": span_total,
                }

                if group.source == "eval":
                    # Count the eval results that actually FORMED the cluster
                    # via the junction — exact and target-agnostic (session
                    # eval rows have no trace, so a trace-scoped count misses
                    # them entirely).
                    telemetry["eval_results"] = (
                        selectors.count_cluster_eval_members(uuid)
                    )
                    target = group.eval_target_type or "span"
                    if target == "session":
                        telemetry["sessions"] = len(
                            self._cluster_session_uuids(uuid)
                        )
                        unit_note = (
                            "This is a SESSION-level eval cluster — the unit of "
                            "failure is the whole session (multi-trace), not a "
                            "single span. Your evidence is the eval results "
                            "(list/read eval_results): their explanations say WHY "
                            "the evaluator failed each session. Use list(sessions) "
                            "/ read(session) to inspect the cross-trace flow; the "
                            "member traces are the session's traces. Read the eval "
                            "explanation (expand 'eval_explanation') before you "
                            "conclude."
                        )
                    elif target == "trace":
                        unit_note = (
                            "This is a TRACE-level eval cluster — the unit of "
                            "failure is the whole trace. Your evidence is the eval "
                            "results (list/read eval_results): their explanations "
                            "say WHY the evaluator failed each trace. Read the eval "
                            "explanation (expand 'eval_explanation') before you "
                            "conclude."
                        )
                    else:
                        unit_note = (
                            "This is a SPAN-level eval cluster. Your evidence is the "
                            "eval results (list/read eval_results): their "
                            "explanations say WHY the evaluator failed. Read the "
                            "eval explanation (expand 'eval_explanation') before you "
                            "conclude."
                        )
                    if span_total == 0:
                        unit_note += (
                            " There is NO span-level telemetry here — do not search "
                            "or aggregate spans; they will come back empty."
                        )
                    # Eval clusters always get the unit note (not just when
                    # span-less): the eval explanation is the primary signal
                    # even when the underlying traces do have spans.
                    payload["telemetry"] = telemetry
                    payload["telemetry_note"] = unit_note
                else:
                    telemetry["scan_issues"] = (
                        selectors.count_cluster_scan_issues(uuid, trace_uuids)
                    )
                    payload["telemetry"] = telemetry
                    if span_total == 0:
                        payload["telemetry_note"] = (
                            "No span-level telemetry for this cluster — the "
                            "queryable evidence is the scan-issue briefs (list/read "
                            "scan_issues), not spans. Do not search or aggregate "
                            "spans; reason from the scan issues and conclude."
                        )
        except Exception:
            logger.exception(
                "cluster_manifest_failed", cluster_id=group.cluster_id
            )

        return payload

    def _read_trace(
        self, id: str, depth: str, expand: set[str] | None = None
    ) -> dict:
        """Read one trace at the chosen depth.

        depth='summary' (default):
          - verbatim root I/O (default-truncated 2KB, expandable)
          - span tree carrying budgeted verbatim I/O, ERROR spans served first
          - notable_attributes from ClickHouse (per-trace attr.* roll-up)
          - deterministic eval_scores join
        depth='spans' — the same tree with no payloads (cheapest lens).
        depth='full' — raised I/O budgets, root.input/output expanded by default.

        Every depth is deterministic. No LLM call happens on this path: the
        model that reads this payload is a stronger reader than anything we
        would pre-digest it with, and a blocking round-trip per trace read was
        the dominant avoidable cost of a run.

        expand: dot-path set ("root.input", "root.output", "root.error").
        """
        if depth not in ("summary", "spans", "full"):
            return tool_error(
                ERROR_CODE_INVALID_ARGS,
                f"unknown depth='{depth}' (use summary|spans|full)",
            )
        expand = expand or set()

        trace_uuid = self._resolve_alias(id)
        if trace_uuid is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"trace '{id}' not found"
            )

        # Cache by (uuid, depth, frozenset(expand)) — different expands ARE
        # different payloads, so don't conflate.
        cache_key = (trace_uuid, depth, frozenset(expand))
        if cache_key in self._trace_summary_cache:
            return self._trace_summary_cache[cache_key]


        # ---- Trace telemetry from ClickHouse spans (cached for the run).
        spans_list = self._spans_for_trace(trace_uuid)
        if not spans_list:
            return tool_error(
                ERROR_CODE_NOT_FOUND,
                f"trace '{id}' has no spans in ClickHouse",
            )

        # Root span = no parent (or parent not in this trace's set). Trace I/O
        # is the root span's I/O; trace context is denormalized onto spans.
        span_ids = {s["span_id"] for s in spans_list}
        roots = [
            s for s in spans_list
            if not s.get("parent_span_id")
            or s["parent_span_id"] not in span_ids
        ]
        roots.sort(key=lambda s: s.get("start_time") or "")
        root = roots[0] if roots else spans_list[0]
        ctx = spans_list[0]  # denormalized trace_* fields are identical per span

        # depth='full' implies expanding root I/O by default.
        full_default_expand = depth == "full"
        expand_input = full_default_expand or "root.input" in expand
        expand_output = full_default_expand or "root.output" in expand
        expand_error = full_default_expand or "root.error" in expand

        root_error = (
            root.get("status_message")
            if root.get("status") == "ERROR"
            else None
        )

        root_block = _build_root_block(
            root.get("input"), root.get("output"), root_error,
            expand_input, expand_output, expand_error,
        )
        # Compress any expanded field that would blow the context window.
        self._cap_oversized_fields(root_block, ["input", "output", "error"], "root")

        # ---- Span tree, with budgeted verbatim I/O at every depth but 'spans'.
        #
        # The I/O is rendered here deterministically rather than handed to a
        # cheaper model to pre-digest first. The investigating model reads a
        # ~1M-token window with thinking enabled and is by far the stronger
        # reader of the two, so paraphrasing the evidence before it arrives
        # costs fidelity and a blocking round-trip and buys nothing.
        #
        # ERROR spans claim budget first: on a long trace the failing span is
        # the one that must survive truncation, and execution order does not
        # put it first.
        # Span-count ceiling. Failing spans are kept unconditionally: cutting
        # on execution order alone drops the span the read exists to find when
        # the failure happens late, which is where failures usually happen.
        if len(spans_list) > _MAX_SPANS_RENDERED:
            keep = {
                i for i, s in enumerate(spans_list) if s.get("status") == "ERROR"
            }
            keep = set(sorted(keep)[:_MAX_SPANS_RENDERED])
            for i in range(len(spans_list)):
                if len(keep) >= _MAX_SPANS_RENDERED:
                    break
                keep.add(i)
            rendered = [spans_list[i] for i in sorted(keep)]
        else:
            rendered = spans_list

        io_by_index: dict[int, dict] = {}
        budget_spent = 0
        if depth in ("summary", "full"):
            field_cap = (
                _DEFAULT_VERBATIM_CAP if depth == "full" else _SPAN_IO_FIELD_CAP
            )
            budget = (
                _TRACE_IO_BUDGET_FULL if depth == "full" else _TRACE_IO_BUDGET
            )
            for i in sorted(
                range(len(rendered)),
                key=lambda n: rendered[n].get("status") != "ERROR",
            ):
                if budget <= 0:
                    break
                s = rendered[i]
                fields: dict[str, Any] = {}
                for key in ("input", "output"):
                    if budget <= 0:
                        break
                    f = _budgeted_field(s.get(key), min(field_cap, budget))
                    if f["full_chars"]:
                        fields[key] = f
                        budget -= len(f["value"])
                if s.get("status") == "ERROR" and s.get("status_message"):
                    f = _budgeted_field(s["status_message"], field_cap)
                    fields["error"] = f
                    budget -= len(f["value"])
                if fields:
                    io_by_index[i] = fields
            budget_spent = len(io_by_index)

        spans_skeleton = []
        for i, s in enumerate(rendered):
            entry: dict[str, Any] = {
                "span": self._mint_alias("span", s["span_id"]),
                "type": s.get("observation_type"),
                "name": s.get("name"),
                "status": s.get("status"),
                "latency_ms": s.get("latency_ms"),
                "parent": (
                    self._mint_alias("span", s["parent_span_id"])
                    if s.get("parent_span_id")
                    else None
                ),
            }
            entry.update(io_by_index.get(i, {}))
            spans_skeleton.append(entry)

        # ---- Notable attributes (ClickHouse per-trace lookup). Graceful no-op.
        notable_attributes: dict[str, Any] = {}
        try:
            for attr in list_attributes_for_trace(trace_uuid):
                if len(attr.values) == 1:
                    notable_attributes[attr.key] = attr.values[0]
                else:
                    notable_attributes[attr.key] = (
                        f"[varies: {', '.join(attr.values[:5])}"
                        + ("…]" if len(attr.values) > 5 else "]")
                    )
        except Exception as exc:
            logger.warning(
                "cluster_rca_attributes_lookup_failed",
                cluster_id=self.cluster_id,
                trace_uuid=trace_uuid,
                error=str(exc),
            )

        # ---- Deterministic eval_scores join.
        eval_scores: list[dict[str, Any]] = []
        eval_rows = selectors.trace_eval_results(trace_uuid)
        for er in eval_rows:
            score: Any = (
                er.output_float
                if er.output_float is not None
                else er.output_bool
                if er.output_bool is not None
                else er.output_str
                if er.output_str
                else er.output_str_list
            )
            eval_scores.append(
                {
                    "id": self._mint_alias("eval_result", er.id),
                    "metric": (
                        er.custom_eval_config.name
                        if er.custom_eval_config_id
                        else None
                    ),
                    "score": score,
                    "errored": bool(er.error),
                    "explanation_snippet": _snippet(er.eval_explanation),
                }
            )

        payload: dict[str, Any] = {
            "trace_id": self._mint_alias("trace", trace_uuid),
            "_uuid": str(trace_uuid),
            "name": ctx.get("trace_name"),
            "session_id": self._mint_alias(
                "session", ctx.get("trace_session_id")
            ),
            "tags": _parse_json_list(ctx.get("tags")),
            "root": root_block,
            "spans": spans_skeleton,
            "notable_attributes": notable_attributes,
            "eval_scores": eval_scores,
        }
        # Say what was withheld. Silent elision reads as "that is the whole
        # trace", which is exactly how a reader concludes nothing went wrong.
        elided = len(spans_list) - len(rendered)
        if elided > 0:
            payload["spans_elided"] = elided
        if depth in ("summary", "full") and budget_spent < len(rendered):
            payload["spans_without_io"] = len(rendered) - budget_spent
            payload["io_budget_note"] = (
                "Inline I/O stopped at the per-trace budget; ERROR spans were "
                "served first. Use read(span, id=...) for a specific span."
            )

        self._trace_summary_cache[cache_key] = payload
        return payload

    def _compress_verbatim(self, text: str, label: str) -> str:
        """Shrink an oversized expanded field, deterministically.

        A single deep read (depth='full' / expand) can return a field large
        enough to blow the main model's context over a run. This used to be
        squeezed by a cheaper model, which cost a blocking round-trip on the
        largest fields — the ones most likely to matter — and whose failure
        mode was dropping the evidence it was called to preserve.

        The verbatim failure windows were already the part doing the real work,
        so they are now the whole answer: every marker's context, plus a head
        of the field for orientation. Nothing leaves here that was not in the
        source, and what is dropped is stated.
        """
        src = text[:_READ_COMPRESS_INPUT_CAP]
        error_block = self._extract_error_windows(src)

        parts = []
        if error_block:
            parts.append(
                "ERROR/FAILURE SIGNAL (verbatim, auto-extracted):\n" + error_block
            )
        head_budget = max(0, _READ_COMPRESS_CHARS - len(error_block))
        parts.append(
            f"FIELD HEAD ({label}, {len(text)} chars total, "
            f"{max(0, len(text) - head_budget)} elided):\n"
            + src[:head_budget]
        )
        return "\n\n".join(parts)

    def _extract_error_windows(self, src: str) -> str:
        """Pull verbatim context windows around every failure marker in `src`,
        merged and budget-capped. Guarantees the diagnostic signal survives
        compression regardless of what the LLM summary keeps."""
        spans: list[tuple[int, int]] = []
        for m in _ERROR_MARKER_RE.finditer(src):
            spans.append(
                (
                    max(0, m.start() - _ERROR_WINDOW_CHARS),
                    min(len(src), m.end() + _ERROR_WINDOW_CHARS),
                )
            )
            if len(spans) > 300:  # bound work on pathological inputs
                break
        if not spans:
            return ""
        spans.sort()
        merged: list[list[int]] = [list(spans[0])]
        for s, e in spans[1:]:
            if s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        out: list[str] = []
        used = 0
        for s, e in merged:
            chunk = src[s:e]
            if used + len(chunk) > _ERROR_BLOCK_BUDGET:
                chunk = chunk[: _ERROR_BLOCK_BUDGET - used]
            out.append(chunk)
            used += len(chunk)
            if used >= _ERROR_BLOCK_BUDGET:
                break
        return " … ".join(out)

    def _cap_oversized_fields(
        self, block: dict, fields: list[str], prefix: str
    ) -> None:
        """In-place: compress any verbatim field over the read ceiling.

        No-op for normal reads (already 2KB-capped); only fires on an opted-in
        expand whose content exceeds _READ_COMPRESS_CHARS. Marks the field with
        `<field>_compressed=True` so the model knows it is a lossy squeeze (the
        `<field>_full_chars` count is already on the block).
        """
        for f in fields:
            v = block.get(f)
            if isinstance(v, str) and len(v) > _READ_COMPRESS_CHARS:
                block[f] = self._compress_verbatim(v, f"{prefix}.{f}")
                block[f"{f}_compressed"] = True

    def _read_span(self, id: str, expand: set[str] | None = None) -> dict:
        """Read one span (ClickHouse) with full attributes. I/O default-
        truncated 2KB; pass expand=['input'] / ['output'] for full values.

        Served from the per-run span index if its trace was already fetched,
        else a single CH read. Span ids are OTEL hex (CharField), not UUIDs —
        resolve via the alias map first.
        """
        expand = expand or set()
        span_id = self._alias_to_uuid.get(id, id)

        span = self._lookup_span(span_id)
        if span is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"span '{id}' not found"
            )

        inp = _verbatim(span.get("input"), expand="input" in expand)
        out = _verbatim(span.get("output"), expand="output" in expand)
        # Compress expanded I/O that would blow the context window.
        in_compressed = out_compressed = False
        if isinstance(inp["value"], str) and len(inp["value"]) > _READ_COMPRESS_CHARS:
            inp["value"] = self._compress_verbatim(inp["value"], "span.input")
            in_compressed = True
        if isinstance(out["value"], str) and len(out["value"]) > _READ_COMPRESS_CHARS:
            out["value"] = self._compress_verbatim(out["value"], "span.output")
            out_compressed = True

        return {
            "span_id": self._mint_alias("span", span["span_id"]),
            "_uuid": span["span_id"],
            "trace_id": self._mint_alias("trace", span.get("trace_id")),
            "parent": (
                self._mint_alias("span", span["parent_span_id"])
                if span.get("parent_span_id")
                else None
            ),
            "name": span.get("name"),
            "type": span.get("observation_type"),
            "operation": span.get("operation_name"),
            "status": span.get("status"),
            "status_message": span.get("status_message"),
            "latency_ms": span.get("latency_ms"),
            "start_time": span.get("start_time"),
            "end_time": span.get("end_time"),
            "input": inp["value"],
            "input_truncated": inp["truncated"],
            "input_full_chars": inp["full_chars"],
            "input_compressed": in_compressed,
            "output": out["value"],
            "output_truncated": out["truncated"],
            "output_full_chars": out["full_chars"],
            "output_compressed": out_compressed,
            "model": span.get("model"),
            "tokens": {
                "prompt": span.get("prompt_tokens"),
                "completion": span.get("completion_tokens"),
                "total": span.get("total_tokens"),
            },
            "cost_usd": span.get("cost"),
            "tags": _parse_json_list(span.get("tags")),
            "provider": span.get("provider"),
        }

    def _read_session(self, id: str, depth: str) -> dict:
        """Read one TraceSession — multi-turn flow.

        depth='summary' (default): factual timeline — list of member traces
        (label, name, created_at, root I/O snippets) ordered chronologically.
        Per-trace LLM summaries are NOT triggered here; the agent calls
        read(trace, T0X, 'summary') on whichever turn looks worth drilling.

        depth='full' / 'spans': not yet wired.
        """
        if depth not in ("summary", "full"):
            return tool_error(
                ERROR_CODE_INVALID_ARGS,
                f"unknown depth='{depth}' (use summary|full)",
            )
        if depth == "full":
            return tool_error(
                ERROR_CODE_UNAVAILABLE,
                "read(session, depth=full) not yet wired — use 'summary' "
                "then drill via read(trace, T0X, 'summary')",
            )

        session_uuid = self._resolve_alias(id)
        if session_uuid is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"session '{id}' not found"
            )

        # Member traces (chronological) from CH — sessions are typically
        # <50 turns; the LLM sees snippets only.
        members = traces_in_session(self.project_id, session_uuid)
        if not members:
            return tool_error(
                ERROR_CODE_NOT_FOUND,
                f"session '{id}' has no traces in ClickHouse",
            )
        ordered = sorted(
            members.items(), key=lambda kv: kv[1].get("first_start") or ""
        )
        return {
            "session_id": self._mint_alias("session", session_uuid),
            "_uuid": str(session_uuid),
            "trace_count": len(ordered),
            "traces": [
                {
                    "trace_id": self._mint_alias("trace", tid),
                    "name": root.get("trace_name"),
                    "created_at": root.get("first_start"),
                    "input_snippet": _snippet(root.get("input")),
                    "output_snippet": _snippet(root.get("output")),
                    "has_error": root.get("has_error", False),
                }
                for tid, root in ordered
            ],
        }

    def _read_eval_result(
        self, id: str, expand: set[str] | None = None
    ) -> dict:
        """Read one EvalLogger row. eval_explanation default-truncated 2KB."""

        expand = expand or set()
        eval_uuid = self._resolve_alias(id)
        if eval_uuid is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"eval_result '{id}' not found"
            )

        # EvalLogger has no direct project FK; scope by membership in THIS
        # cluster's eval evidence (its project-scoped traces + sessions). A
        # foreign-project eval row is not in that set and so cannot resolve —
        # same transitive-scope guarantee the list/agg eval handlers rely on.
        trace_uuids = self._cluster_trace_uuids(self.cluster_uuid)
        er = selectors.get_eval_result_for_read(
            self._eval_scope_q(self.cluster_uuid, trace_uuids), eval_uuid
        )
        if er is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"eval_result '{id}' not found"
            )

        score: Any = (
            er.output_float
            if er.output_float is not None
            else er.output_bool
            if er.output_bool is not None
            else er.output_str
            if er.output_str
            else er.output_str_list
        )
        explanation = _verbatim(
            er.eval_explanation, expand="eval_explanation" in expand
        )
        return {
            "id": self._mint_alias("eval_result", er.id),
            "_uuid": str(er.id),
            "target_type": er.target_type,
            "trace_id": self._mint_alias("trace", er.trace_id),
            "span_id": self._mint_alias("span", er.observation_span_id),
            "metric": (
                er.custom_eval_config.name
                if er.custom_eval_config_id
                else None
            ),
            "eval_config_id": self._mint_alias(
                "eval_config", er.custom_eval_config_id
            ),
            "score": score,
            "errored": bool(er.error),
            "error_message": er.error_message,
            "eval_explanation": explanation["value"],
            "eval_explanation_truncated": explanation["truncated"],
            "eval_explanation_full_chars": explanation["full_chars"],
            "results_tags": er.results_tags or [],
            "eval_tags": er.eval_tags or [],
        }

    def _read_eval_config(self, id: str) -> dict:
        """Read one CustomEvalConfig — the eval definition."""

        cfg_uuid = self._resolve_alias(id)
        if cfg_uuid is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"eval_config '{id}' not found"
            )
        cfg = selectors.get_eval_config_for_read(cfg_uuid, self.project_id)
        if cfg is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"eval_config '{id}' not found"
            )
        return {
            "id": self._mint_alias("eval_config", cfg.id),
            "_uuid": str(cfg.id),
            "name": cfg.name,
            "model": cfg.model,
            "config": cfg.config,
            "mapping": cfg.mapping,
            "filters": cfg.filters,
            "error_localizer": cfg.error_localizer,
            "template_name": (
                getattr(cfg.eval_template, "name", None)
                if cfg.eval_template_id
                else None
            ),
        }

    def _read_version(self, id: str) -> dict:
        """Read one ProjectVersion — deploy metadata for timeline correlation."""

        ver_uuid = self._resolve_alias(id)
        if ver_uuid is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"version '{id}' not found"
            )
        v = selectors.get_version_for_read(ver_uuid, self.project_id)
        if v is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"version '{id}' not found"
            )
        return {
            "id": self._mint_alias("version", v.id),
            "_uuid": str(v.id),
            "name": v.name,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }

    def _read_scan_issue(self, id: str) -> dict:
        """Read one TraceScanIssue — scanner finding metadata."""

        issue_uuid = self._resolve_alias(id)
        if issue_uuid is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"scan_issue '{id}' not found"
            )

        issue = selectors.get_scan_issue_for_read(issue_uuid, self.project_id)
        if issue is None:
            return tool_error(
                ERROR_CODE_NOT_FOUND, f"scan_issue '{id}' not found"
            )

        return {
            "id": self._mint_alias("scan_issue", issue.id),
            "_uuid": str(issue.id),
            "category": issue.category,
            "group": issue.group,
            "fix_layer": issue.fix_layer,
            "confidence": issue.confidence,
            "brief": issue.brief,
            "trace_id": (
                self._mint_alias("trace", issue.scan_result.trace_id)
                if issue.scan_result_id
                else None
            ),
            "cluster_id": (
                issue.cluster.cluster_id if issue.cluster_id else None
            ),
        }

    def _read_prior_analysis(self, id: str) -> dict:
        """Prior cluster RCA runs aren't persisted yet (no ClusterAnalysisRun
        table). Returns unavailable until persistence ships — but the schema
        entry stays so the LLM can discover the surface."""
        return tool_error(
            ERROR_CODE_UNAVAILABLE,
            "prior_analysis runs are not persisted yet — no prior_analyses "
            "available for this cluster",
        )

    # ------------------------------------------------------------------------
    # WRITE TOOLS — submit_finding / submit_synthesis (in-memory for now)
    # ------------------------------------------------------------------------

    def _tool_submit_finding(self, **finding: Any) -> dict:
        try:
            finding_type = FindingType(finding["finding_type"])
            confidence = Confidence(finding["confidence"])
        except (KeyError, ValueError) as exc:
            return tool_error(
                ERROR_CODE_INVALID_ARGS, f"Invalid enum: {exc}"
            )

        with self._counter_lock:
            self._finding_counter += 1
            finding_id = f"F{self._finding_counter:03d}"

        new_finding = ClusterFinding(
            finding_type=finding_type,
            title=finding.get("title", ""),
            description=finding.get("description", ""),
            confidence=confidence,
            evidence_trace_ids=self._evidence_uuids(
                finding.get("evidence_trace_ids")
            ),
            evidence_span_ids=self._evidence_uuids(
                finding.get("evidence_span_ids")
            ),
        )
        self.findings.append(new_finding)
        self.on_event(
            "finding",
            {"finding_id": finding_id, **asdict(new_finding)},
        )
        return {"status": "accepted", "finding_id": finding_id}

    def _tool_submit_synthesis(self, **payload: Any) -> dict:
        try:
            confidence = Confidence(payload["confidence"])
        except (KeyError, ValueError) as exc:
            return tool_error(
                ERROR_CODE_INVALID_ARGS, f"Invalid confidence: {exc}"
            )

        # Reject an empty synthesis before marking complete: a truncated/lazy
        # call can set confidence with blank root-cause text, which would end the
        # run on an empty answer. Error out so the model retries.
        synthesis_text = (payload.get("synthesis") or "").strip()
        if not synthesis_text:
            return tool_error(
                ERROR_CODE_INVALID_ARGS,
                "synthesis is required and must be non-empty: provide the "
                "2-sentence root cause (what is wrong + why).",
            )

        self.synthesis = ClusterSynthesis(
            synthesis=synthesis_text,
            fix=payload.get("fix", ""),
            confidence=confidence,
            evidence_trace_ids=self._evidence_uuids(
                payload.get("evidence_trace_ids")
            ),
            suggested_questions=[
                q for q in (payload.get("suggested_questions") or [])
                if isinstance(q, str) and q.strip()
            ][:3],
        )
        self._investigation_complete = True
        self.on_event("synthesis", asdict(self.synthesis))
        return {"status": "accepted", "investigation_complete": True}

    # ------------------------------------------------------------------------
    # CONTEXT MANAGEMENT — smarter compaction
    # ------------------------------------------------------------------------

    def _compact_old_tool_results(self, messages: list[dict]) -> None:
        """Compact old tool results while preserving submit_* and recent reads.

        Strategy:
          - submit_finding / submit_synthesis tool results: keep verbatim
            (the scratchpad).
          - Last N non-submit results (read / list / aggregate / search / ...):
            keep verbatim.
          - Older non-submit results: replace with a short placeholder. The LLM
            can re-call the tool if it needs the data again.
        """
        # Map tool_call_id -> tool_name from assistant turns
        tool_call_id_to_name: dict[str, str] = {}
        for m in messages:
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    tc_id = tc.get("id")
                    if tc_id and fn.get("name"):
                        tool_call_id_to_name[tc_id] = fn["name"]

        SUBMIT_TOOLS = {"submit_finding", "submit_synthesis"}

        tool_msg_indices: list[tuple[int, str]] = []
        for i, m in enumerate(messages):
            if m.get("role") == "tool":
                tc_id = m.get("tool_call_id")
                name = tool_call_id_to_name.get(tc_id, "")
                tool_msg_indices.append((i, name))

        non_submit = [
            (i, n) for i, n in tool_msg_indices if n not in SUBMIT_TOOLS
        ]
        if len(non_submit) <= CLUSTER_RCA_COMPACT_KEEP_RECENT:
            return

        to_compact = non_submit[:-CLUSTER_RCA_COMPACT_KEEP_RECENT]
        for idx, name in to_compact:
            msg = messages[idx]
            content = msg.get("content", "")
            if isinstance(content, str) and "[compacted]" not in content:
                messages[idx]["content"] = json.dumps(
                    {
                        "compacted": True,
                        "tool": name or "unknown",
                        "note": (
                            "Older tool result compacted. Re-call the tool "
                            "if you need this data."
                        ),
                    }
                )

    @property
    def cost_usd(self) -> float:
        """Total cost accumulated across all gateway calls in this run."""
        return self.total_cost_usd
