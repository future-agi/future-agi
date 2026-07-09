import json
import time
import traceback
from dataclasses import asdict

import structlog
from django.db import transaction
from django.utils import timezone

from accounts.models.workspace import Workspace

# NOTE: trigger_error_localization_for_span is imported lazily inside the
# function that uses it (see line ~1358). Eager import here re-enters
# model_hub.tasks.__init__ which imports tracer.utils.eval_tasks, which imports
# back from this module — a cycle that surfaces under settings where the
# ClickHouse client warms up at startup. Keep it lazy.
from agentic_eval.core_evals.fi_evals import *  # noqa: F403
from common.utils.data_injection import normalize as _di_normalize
from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import EvalTemplate
from sdk.utils.helpers import _get_api_call_type
from tfc.constants.api_calls import APICallStatusChoices
from tfc.temporal import temporal_activity
from tfc.utils.case import to_camel_case, to_snake_case
from tracer.models.custom_eval_config import CustomEvalConfig, EvalOutputType
from tracer.models.eval_task import EvalTask
from tracer.models.observation_span import (
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
    ObservationType,
)
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.utils.helper import (
    FieldConfig,
    get_default_project_version_config,
    get_default_trace_config,
)

logger = structlog.get_logger(__name__)

from tfc.billing.boundary import get_billing, token_usage_properties

custom_prompt_eval_types = ["CustomPrompt"]
EXPERIMENT = "experiment"
OBSERVE = "observe"

# Re-export for backward compat
from tracer.utils.eval_helpers import resolve_eval_config_id  # noqa: F401, E402


# Friendly eval-mapping shorthands used in saved configs. The user-
# facing variable picker (voice projects in particular) lets people map
# variables to things like ``recording_url`` or ``transcript``; the
# actual span attribute written by the ingestion layer depends on the
# provider — Vapi writes ``conversation.recording.stereo``, the GenAI
# semantic convention path writes ``gen_ai.voice.recording.url``, the
# simulator writes ``stereo_recording_url``, etc. Without a resolver
# here each provider would require a hand-written mapping per span.
# When the exact attribute isn't present we probe these fallbacks in
# order — first match wins.
def _walk_dotted_path(root, path):
    """Walk a dotted path through nested dicts/lists; return None on miss."""
    if not isinstance(path, str) or not path:
        return None
    current = root
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _walk_raw_log(raw_log: dict, path: str):
    """Walk raw_log with snake_case ↔ camelCase coercion per segment.

    Voice-only fallback in ``_process_mapping``. Bridges FE picker
    snake_case paths (``messages.0.end_time``) to vapi/retell camelCase
    keys (``endTime``). Returns ``_MISSING`` on miss — distinguishing
    that from a legitimate ``None`` matters because voice transcripts
    store real ``null`` for fields like ``duration``/``metadata``.
    """
    if not isinstance(path, str) or not path:
        return _MISSING

    current = raw_log
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return _MISSING
            continue
        if not isinstance(current, dict):
            return _MISSING
        if part in current:
            current = current[part]
            continue
        camel = to_camel_case(part)
        if camel != part and camel in current:
            current = current[camel]
            continue
        snake = to_snake_case(part)
        if snake != part and snake in current:
            current = current[snake]
            continue
        return _MISSING
    return current


# Sentinel: ``None`` is a legitimate stored value, so we can't use it for "miss".
_MISSING = object()


def _build_apicall_output(result, partial_input_warning):
    """Build the ``APICallLog.config.output`` payload for an eval success.

    Bundles ``partial_input_warning`` into the same payload so the single
    save below carries both the result and the warning — avoids the
    earlier double-save (which silently dropped the warning if the
    second save raised).
    """
    payload = {"output": result.value, "reason": result.reason}
    if partial_input_warning:
        payload["warnings"] = [partial_input_warning]
    return payload


def _attach_warning_to_metadata(response, output_metadata, partial_input_warning):
    """Mirror a partial-input warning onto the response and EvalLogger metadata."""
    if not partial_input_warning:
        return
    response["warnings"] = [partial_input_warning]
    output_metadata["warnings"] = [partial_input_warning]


def _resolve_attr(span_attrs: dict, candidate: str):
    """Literal lookup → dotted walk → JSON-parsed parent walk on miss.

    Last step matches the dataset-eval resolver so the trace-eval path
    can resolve picker paths inside JSON-stringified ``input.value`` /
    ``output.value`` flat keys.
    """
    if candidate in span_attrs:
        return span_attrs[candidate]
    walked = _walk_dotted_path(span_attrs, candidate)
    if walked is not None:
        return walked

    from model_hub.utils.json_path_resolver import parse_json_safely

    parts = candidate.split(".")
    for split_idx in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:split_idx])
        remainder = ".".join(parts[split_idx:])
        for key in (f"{prefix}.value", prefix):
            if key not in span_attrs:
                continue
            parsed, ok = parse_json_safely(span_attrs[key])
            if not ok:
                continue
            walked = _walk_dotted_path(parsed, remainder)
            if walked is not None:
                return walked
    return _MISSING


_ATTRIBUTE_ALIASES: dict[str, list[str]] = {
    "recording_url": [
        # Vapi ingestion (``tracer.utils.vapi._extract_recording_urls``)
        "conversation.recording.stereo",
        "conversation.recording.mono.combined",
        "conversation.recording.mono.customer",
        "conversation.recording.mono.assistant",
        # GenAI semantic convention (``tracer.utils.semantic_conventions``)
        "gen_ai.voice.recording.stereo_url",
        "gen_ai.voice.recording.url",
        # Simulator (``simulate.temporal.activities.xl``)
        "stereo_recording_url",
        "voice_recording_url",
    ],
    "stereo_recording_url": [
        "conversation.recording.stereo",
        "gen_ai.voice.recording.stereo_url",
    ],
    "customer_recording_url": [
        "conversation.recording.mono.customer",
        "gen_ai.voice.recording.customer_url",
    ],
    "assistant_recording_url": [
        "conversation.recording.mono.assistant",
        "gen_ai.voice.recording.assistant_url",
    ],
    "transcript": [
        "conversation.transcript",
        "gen_ai.voice.transcript",
        "voice_transcript",
        "call.transcript",
        "provider_transcript",
    ],
    "call_summary": [
        "conversation.summary",
        "gen_ai.voice.summary",
    ],
}


def build_span_context(span, *, anchor_span_id: str | None = None) -> dict:
    """Build the ``span_context`` payload that AgentEvaluator receives.

    Identical shape across span / trace / session handlers so the agent
    sees a consistent dict regardless of which surface triggered the eval.
    ``cost`` is float-coerced because the ORM returns a Decimal — JSON
    serialization would otherwise fail.
    """
    return {
        "id": str(getattr(span, "id", "") or ""),
        "name": getattr(span, "name", None),
        "observation_type": getattr(span, "observation_type", None),
        "status": getattr(span, "status", None),
        "status_message": getattr(span, "status_message", None),
        "model": getattr(span, "model", None),
        "latency_ms": getattr(span, "latency_ms", None),
        "total_tokens": getattr(span, "total_tokens", None),
        "cost": float(span.cost) if getattr(span, "cost", None) else None,
    }


def build_trace_context(trace, *, anchor_span_id: str | None = None) -> dict:
    """Build the ``trace_context`` payload that AgentEvaluator receives.

    Includes span aggregates (count, error count, tokens, latency) AND an
    inline list of span identifiers so the agent can drill into spans via
    ``span_detail`` directly — no preliminary ``list_trace_spans`` call
    required. Span list capped at 200 to bound payload size; aggregates
    cover the full trace.

    ``anchor_span_id`` is set by the span handler to pin the originating
    span — null for trace-level evals. Aggregate query failures fall back
    to empty values rather than raising; the eval continues without the
    optional context fields.
    """
    try:
        # Read from CH 25.3 (was ObservationSpan.objects.filter(trace=trace,
        # deleted=False).aggregate(...) + .order_by("start_time").values(...)).
        # CHSpanReader.list_by_trace already filters is_deleted=0 and orders
        # by start_time, id; we aggregate in Python so we can preserve the
        # Count(filter=Q(status='ERROR')) shape without a new reader method.
        # Per-trace span count is bounded (UI/agent already caps display at
        # 200) so a single pass over the rows is cheap.
        from tracer.services.clickhouse.v2 import get_reader

        trace_id = getattr(trace, "id", None)
        if trace_id is None:
            _agg, _spans = {}, []
        else:
            with get_reader() as reader:
                _ch_spans = reader.list_by_trace(str(trace_id))
            _span_count = len(_ch_spans)
            _error_count = sum(1 for s in _ch_spans if s.status == "ERROR")
            _total_tokens = sum((s.total_tokens or 0) for s in _ch_spans)
            _total_latency = sum((s.latency_ms or 0) for s in _ch_spans)
            _agg = {
                "span_count": _span_count,
                "error_count": _error_count,
                "total_tokens": _total_tokens,
                "total_latency_ms": _total_latency,
            }
            _spans = [
                {
                    "id": s.id,
                    "name": s.name,
                    "observation_type": s.observation_type,
                    "status": s.status,
                    "parent_span_id": s.parent_span_id or None,
                }
                for s in _ch_spans[:200]
            ]
    except Exception:
        _agg, _spans = {}, []

    _created_at = getattr(trace, "created_at", None)
    payload = {
        "id": str(getattr(trace, "id", "") or ""),
        "name": getattr(trace, "name", None),
        "created_at": _created_at.isoformat() if _created_at else None,
        "span_count": _agg.get("span_count") or 0,
        "error_count": _agg.get("error_count") or 0,
        "total_tokens": _agg.get("total_tokens") or 0,
        "total_latency_ms": _agg.get("total_latency_ms") or 0,
        "has_error": bool(_agg.get("error_count") or 0),
        "spans": [
            {
                "id": str(s["id"]),
                "name": s.get("name"),
                "observation_type": s.get("observation_type"),
                "status": s.get("status"),
                "parent_span_id": (
                    str(s["parent_span_id"]) if s.get("parent_span_id") else None
                ),
            }
            for s in _spans
        ],
    }
    if anchor_span_id is not None:
        payload["span_id"] = anchor_span_id
    return payload


def build_session_context(session) -> dict | None:
    """Build the ``session_context`` payload that AgentEvaluator receives.

    Same shape the playground produces (model_hub/views/separate_evals.py),
    so the agent gets a consistent payload regardless of which surface
    triggered the eval. Returns None on lookup/aggregation failure rather
    than raising — the eval continues without the optional context.
    """
    if session is None:
        return None
    try:
        from tracer.models.trace import Trace
        from tracer.services.clickhouse.v2 import get_reader

        # Derive the session's trace set from CH spans, NOT the ``Trace.session``
        # reverse FK (Slice D, DESIGN §5 / PG_ORM_READ_MIGRATION): post-flip that
        # FK is ``None`` for EVERY trace (only spans carry ``trace_session_id``),
        # so ``Trace.objects.filter(session=session)`` returns EMPTY for ALL
        # sessions. ``session_trace_ids`` resolves the input id AND each span's id
        # new→old (remap-aware), so a straddler yields its old∪new spans' traces as
        # ONE set and a net-new session (no PG row) yields its real trace set.
        # ``session`` here is either a saved PG ``TraceSession`` (span/trace-level
        # callers) or the unsaved vehicle ``evaluate_trace_session_observe`` builds
        # — both carry ``.id`` and ``.project_id`` (the vehicle's is the eval
        # config's project). Trace itself is still PG, so the ids drive a PG
        # ``id__in`` filter (Trace is never re-keyed; only the session surrogate).
        session_id = getattr(session, "id", None)
        project_id = getattr(session, "project_id", None)
        if session_id is None or project_id is None:
            _session_trace_ids: list[str] = []
        else:
            with get_reader() as reader:
                _session_trace_ids = reader.session_trace_ids(
                    str(project_id), str(session_id)
                )
        trace_qs = Trace.objects.filter(id__in=_session_trace_ids, deleted=False)
        # Cap at 100 traces for the in-prompt summary; the agent uses
        # explore_trace for deeper drill-down.
        traces_page = list(trace_qs.order_by("created_at")[:100])
        trace_ids = [t.id for t in traces_page]
        # Stringified, as a set, so the per-trace bucket lookups below stay
        # O(1) and match the str trace_id CH returns on each span row.
        _page_trace_id_strs = {str(tid) for tid in trace_ids}

        # Read from CH 25.3 (was three separate ObservationSpan.objects
        # queries: session-wide aggregate, per-trace aggregate, per-trace
        # span listing). list_by_session covers the same row set as the
        # original ObservationSpan.objects.filter(trace__in=trace_qs,
        # deleted=False) — soft-deleted traces cascade to spans (see
        # _soft_delete_trace_tree in tracer/views/trace.py) and
        # CHSpanReader filters is_deleted=0. The session aggregate is
        # computed over the FULL row set (matching the original ORM
        # aggregate which was not capped); the per-trace breakdown and
        # the inline span list are only populated for traces in the
        # 100-trace page so trace_summaries below still iterates the
        # capped set. (``session_id`` resolved once at the top of the try.)
        # ``list_by_session`` matches the RAW input ``session_id`` (it resolves
        # each span new→old and compares to this id), whereas ``session_trace_ids``
        # above resolves the INPUT id too. They agree because every caller of this
        # path hands a SURVIVOR (old) or NET-NEW id, never a straddler's raw NEW id
        # (the session list/detail surfaces resolved/old ids — trace_session.py — and
        # the eval session vehicle is built with the survivor/net-new id); for those
        # the input resolves to itself, so both reads see the same session. (A
        # straddler's NEW id would split here — empty span-agg vs full trace set —
        # but no entry point produces one.)
        if session_id is None:
            _ch_spans = []
        else:
            with get_reader() as reader:
                _ch_spans = reader.list_by_session(str(session_id))

        _start_time = None
        _end_time = None
        _total_spans = 0
        _error_count = 0
        _total_tokens = 0
        _total_cost = 0.0
        per_trace: dict = {}
        spans_by_trace: dict = {}
        for s in _ch_spans:
            # Session totals span every trace; this matches the original
            # filter(trace__in=trace_qs).aggregate(...) which had no
            # 100-trace cap.
            _total_spans += 1
            if s.status == "ERROR":
                _error_count += 1
            _total_tokens += s.total_tokens or 0
            _total_cost += float(s.cost or 0.0)
            if s.start_time and (_start_time is None or s.start_time < _start_time):
                _start_time = s.start_time
            if s.end_time and (_end_time is None or s.end_time > _end_time):
                _end_time = s.end_time

            # Per-trace breakdown + inline span listing are scoped to the
            # traces in the page so the payload size stays bounded.
            if s.trace_id not in _page_trace_id_strs:
                continue
            agg = per_trace.setdefault(
                s.trace_id,
                {
                    "trace_id": s.trace_id,
                    "span_count": 0,
                    "error_count": 0,
                    "total_tokens": 0,
                    "total_latency": 0,
                },
            )
            agg["span_count"] += 1
            if s.status == "ERROR":
                agg["error_count"] += 1
            agg["total_tokens"] += s.total_tokens or 0
            agg["total_latency"] += s.latency_ms or 0

            bucket = spans_by_trace.setdefault(s.trace_id, [])
            if len(bucket) >= 50:
                continue
            bucket.append(
                {
                    "id": str(s.id),
                    "name": s.name,
                    "observation_type": s.observation_type,
                    "status": s.status,
                    "parent_span_id": (
                        str(s.parent_span_id) if s.parent_span_id else None
                    ),
                }
            )

        sess_agg = {
            "total_spans": _total_spans,
            "error_count": _error_count,
            "total_tokens": _total_tokens,
            "total_cost": _total_cost,
            "start_time": _start_time,
            "end_time": _end_time,
        }

        trace_summaries = []
        for t in traces_page:
            # getattr guards against incomplete Trace rows (None on nullable
            # columns from in-flight ingests or older surfaces).
            t_id = getattr(t, "id", None)
            if t_id is None:
                continue
            # CH returns trace_id as a string, but Trace.id is a UUID — look
            # up per_trace / spans_by_trace by the stringified id so the join
            # works regardless of source type.
            t_id_key = str(t_id)
            t_created = getattr(t, "created_at", None)
            t_error = getattr(t, "error", None)
            agg = per_trace.get(t_id_key, {})
            err_count = agg.get("error_count") or 0
            trace_summaries.append(
                {
                    "id": str(t_id),
                    "name": getattr(t, "name", None),
                    "created_at": t_created.isoformat() if t_created else None,
                    "span_count": agg.get("span_count") or 0,
                    "error_count": err_count,
                    "total_tokens": agg.get("total_tokens") or 0,
                    "total_latency_ms": agg.get("total_latency") or 0,
                    "has_error": bool(t_error or err_count > 0),
                    "spans": spans_by_trace.get(t_id_key, []),
                }
            )

        start = sess_agg["start_time"]
        end = sess_agg["end_time"]
        duration = (end - start).total_seconds() if start and end else None

        return {
            "id": str(session.id),
            "name": session.name,
            "project_id": (str(session.project_id) if session.project_id else None),
            "bookmarked": session.bookmarked,
            "created_at": (
                session.created_at.isoformat() if session.created_at else None
            ),
            "trace_count": trace_qs.count(),
            "total_spans": sess_agg["total_spans"] or 0,
            "error_count": sess_agg["error_count"] or 0,
            "total_tokens": sess_agg["total_tokens"] or 0,
            "total_cost": (
                float(round(sess_agg["total_cost"], 6)) if sess_agg["total_cost"] else 0
            ),
            "start_time": str(start) if start else None,
            "end_time": str(end) if end else None,
            "duration_seconds": duration,
            "traces": trace_summaries,
        }
    except Exception as e:
        logger.warning(
            "build_session_context_failed",
            session_id=str(getattr(session, "id", None)),
            error=str(e),
        )
        return None


class EvalSkippedMissingAttribute(ValueError):
    """A mapped span attribute the eval needs is absent, so the eval is skipped.

    There was no input to evaluate — this is a skip, not a failure. Subclasses
    ValueError so existing ``except ValueError`` handlers still catch it, while
    carrying the structured reason the eval logger persists.
    """

    def __init__(self, attribute: str, key: str, span_id):
        self.skipped_reason = f"missing_required_attribute: {attribute}"
        super().__init__(
            f"Required attribute '{attribute}' for key '{key}' not found for span {span_id}"
        )


def _process_mapping(
    mapping: dict | None, span: ObservationSpan, eval_template_id: int
) -> dict:
    """
    Process the mapping from custom eval config to span attributes.

    Uses SpanAttributeAccessor for backward-compatible attribute access,
    supporting both span_attributes (new) and eval_attributes (deprecated).

    Args:
        mapping: Dict mapping eval input keys to span attribute keys
        span: The ObservationSpan to get attributes from
        eval_template_id: The eval template ID for optional key handling

    Returns:
        dict: Parsed mapping with values from span attributes
    """
    from tracer.utils.attribute_accessor import get_span_attributes

    if not mapping:
        return {}

    parsed_mapping = {}
    # Use accessor for backward compatibility (span_attributes || eval_attributes)
    span_attrs = get_span_attributes(span)

    # Handle optional keys from eval template + record whether this is a
    # user-built custom eval. For custom evals, a missing span attribute
    # is treated as an empty value (not a hard error) — the shared
    # validator later decides whether to fail (all empty) or warn
    # (partial). This is what makes the tracer path consistent with
    # dataset / playground / simulation.
    is_user_custom_eval = False
    try:
        given_eval_template = EvalTemplate.no_workspace_objects.get(id=eval_template_id)
        optional_keys = given_eval_template.config.get("optional_keys", [])
        is_user_custom_eval = bool(given_eval_template.config.get("custom_eval", False))
        if len(optional_keys) > 0:
            for key in optional_keys:
                if key in mapping and (mapping[key] is None or mapping[key] == ""):
                    mapping.pop(key)

    except EvalTemplate.DoesNotExist:
        pass

    for key, attribute in mapping.items():
        # Try exact match first, then common fallback patterns.
        # The frontend column picker shows simplified names like "input"
        # but span_attributes often stores them as "input.value". Voice
        # shorthands (``recording_url``, ``transcript``, …) resolve to
        # one of several provider-specific attribute names via the
        # ``_ATTRIBUTE_ALIASES`` table above — first hit wins.
        candidates = [attribute, f"{attribute}.value"]
        for alias in _ATTRIBUTE_ALIASES.get(attribute, []):
            candidates.append(alias)
            candidates.append(f"{alias}.value")

        resolved_value = _MISSING
        for candidate in candidates:
            value = _resolve_attr(span_attrs, candidate)
            if value is not _MISSING:
                resolved_value = value
                break

        if resolved_value is _MISSING and attribute in _SPAN_PUBLIC_FIELDS:
            model_val = getattr(span, attribute, _MISSING)
            if model_val is not _MISSING:
                resolved_value = model_val

        # Voice raw_log fallback: paths the BE response builder normalizes
        # from raw_log at API time (messages.<n>.*, started_at, …) but
        # never persists as flat span_attributes. Gated on observation_type
        # so non-voice spans are unaffected. See _walk_raw_log.
        if (
            resolved_value is _MISSING
            and span.observation_type == ObservationType.CONVERSATION
        ):
            raw_log = span_attrs.get("raw_log")
            if isinstance(raw_log, dict):
                walked = _walk_raw_log(raw_log, attribute)
                if walked is not _MISSING:
                    resolved_value = walked

        if resolved_value is not _MISSING:
            if isinstance(resolved_value, str):
                parsed_mapping[key] = resolved_value
            else:
                parsed_mapping[key] = json.dumps(resolved_value)
        elif is_user_custom_eval:
            # Custom eval: missing span attribute is treated as empty so
            # the shared validator can decide whether to fail (all empty)
            # or run with a partial_input warning. Span path mirrors
            # what dataset/playground do when a column cell is empty.
            parsed_mapping[key] = ""
        else:
            # Expected: the user's eval references an attribute absent on this
            # span. Raw emitter before the ValueError that the outer
            # evaluate_*_observe handler catches and persists as failed. Warning.
            logger.warning(
                f"Required attribute '{attribute}' for key '{key}' not found for span {span.id}"
            )
            raise EvalSkippedMissingAttribute(attribute, key, span.id)

    return parsed_mapping


def _dedupe_preserve_order(items):
    """Return ``items`` with duplicates removed, keeping first-seen order.

    Used to guarantee ``EvalLogger.output_str_list`` never repeats a choice
    when the upstream eval emits duplicates (per-item dicts, choices arrays,
    plain string lists — all funnel through here).
    """
    seen = set()
    out = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _dual_write_eval_value(value, config_output, logger_kwargs):
    """Populate ``logger_kwargs`` with one eval result, dual-writing both the
    new (``output_str``) and legacy (``output_float`` / ``output_str_list``)
    shapes so FE readers that still consume the typed columns keep working.

    Gating (see the dual-write plan):
      * ``output_float`` is (re-)populated only when ``config_output == "score"``.
      * ``output_str_list`` is (re-)populated only when ``config_output == "choices"``.
      * ``output_bool`` is never touched here; the bool / "Passed"/"Failed"
        branches behave exactly as today's dispatch.
      * Any other ``config_output`` (``Pass/Fail``, ``reason``, ``numeric``, …)
        keeps today's isinstance-chain behaviour unchanged.

    The dict shape ``{"score": …, "choice": …}`` / ``{"score": …, "choices": […]}``
    comes from ``evaluations/engine/formatting.py``'s choices branch; we serialize
    it as JSON into ``output_str`` for the new format.
    """
    if isinstance(value, bool):
        logger_kwargs["output_bool"] = value
        return
    if value in ("Passed", "Failed"):
        logger_kwargs["output_bool"] = value == "Passed"
        return

    if config_output == "score":
        if isinstance(value, dict):
            logger_kwargs["output_str"] = json.dumps(value)
            score = value.get("score")
            if isinstance(score, int | float) and not isinstance(score, bool):
                logger_kwargs["output_float"] = float(score)
        elif isinstance(value, int | float):
            logger_kwargs["output_float"] = float(value)
        elif isinstance(value, list):
            # Score evals never store a list — collapse to the mean so the FE
            # always reads a single scalar from output_float. Elements may be
            # raw numbers or per-item dicts shaped like ``{"score": …, "choice": …}``
            # from the choices-promoted code path; extract the score from each.
            # Keep the original list in output_str so per-element values stay
            # inspectable.
            logger_kwargs["output_str"] = json.dumps(value)
            numerics = []
            for v in value:
                if isinstance(v, bool):
                    continue
                if isinstance(v, int | float):
                    numerics.append(v)
                elif isinstance(v, dict):
                    s = v.get("score")
                    if isinstance(s, int | float) and not isinstance(s, bool):
                        numerics.append(s)
            if numerics:
                logger_kwargs["output_float"] = sum(numerics) / len(numerics)
        else:
            logger_kwargs["output_str"] = str(value)
        return

    if config_output == "choices":
        if isinstance(value, dict):
            logger_kwargs["output_str"] = json.dumps(value)
            choice = value.get("choice")
            choices = value.get("choices")
            if isinstance(choice, str):
                logger_kwargs["output_str_list"] = [choice]
            elif isinstance(choices, list):
                logger_kwargs["output_str_list"] = _dedupe_preserve_order(choices)
        elif isinstance(value, str):
            logger_kwargs["output_str"] = value
            logger_kwargs["output_str_list"] = [value]
        elif isinstance(value, list):
            # Two shapes can arrive here:
            #   * Plain list of choice strings.
            #   * List of per-item dicts shaped like ``{"choice": …}`` /
            #     ``{"choices": [...]}`` (mirrors the dict branch above).
            # Flatten + dedupe to a single ordered list either way. If any
            # element is a dict, also dump the raw list to ``output_str`` so the
            # per-item payloads stay inspectable.
            if any(isinstance(v, dict) for v in value):
                logger_kwargs["output_str"] = json.dumps(value)
            collected = []
            for v in value:
                if isinstance(v, str):
                    collected.append(v)
                elif isinstance(v, dict):
                    inner_choice = v.get("choice")
                    inner_choices = v.get("choices")
                    if isinstance(inner_choice, str):
                        collected.append(inner_choice)
                    elif isinstance(inner_choices, list):
                        collected.extend(c for c in inner_choices if isinstance(c, str))
            logger_kwargs["output_str_list"] = _dedupe_preserve_order(collected)
        elif isinstance(value, int | float):
            logger_kwargs["output_float"] = float(value)
        else:
            logger_kwargs["output_str"] = str(value)
        return

    # Other output types — preserve today's dispatch verbatim.
    if isinstance(value, int | float):
        logger_kwargs["output_float"] = float(value)
    elif isinstance(value, list):
        logger_kwargs["output_str_list"] = value
    else:
        logger_kwargs["output_str"] = str(value)


def _eval_config_output(custom_eval_config):
    """Read the stored ``output`` type from an eval template config.

    Never use the runtime-promoted value (``format_eval_value`` internally
    promotes ``score`` → ``choices`` when ``choice_scores`` exist); the gating
    rules in :func:`_dual_write_eval_value` are keyed on the **stored** type.
    """
    try:
        return custom_eval_config.eval_template.config.get("output", "score")
    except (AttributeError, TypeError):
        return "score"


def _emit_eval_billing(
    org_id: str,
    api_call_type,
    source_id: str,
    target_type: str,
    result,
    custom_eval_config,
    ws_id: str | None,
    api_call_log_row,
    feedback_id=None,
):
    """Emit a UsageEvent for the new billing pipeline after a successful eval.

    Centralizes the dual-write block used by span/trace/session eval paths.
    Fails-open (metering must not break the eval) but logs the exception
    so silent drops are diagnoseable — swallowing without a log line
    hides real breakage in prod.
    """
    try:
        billing = get_billing()
        _llm_cost = (result.cost or {}).get("total_cost", 0)
        _per_run_fee = billing.eval_per_run_fee()
        _actual_cost = _llm_cost + _per_run_fee
        _token_usage = result.token_usage or {}
        credits = billing.ai_credits(_actual_cost)

        billing.record_usage(
            org_id,
            api_call_type,
            amount=credits,
            source="tracer" if not feedback_id else "feedback",
            source_id=source_id,
            model=custom_eval_config.model or "",
            workspace_id=ws_id or "",
            log_id=str(api_call_log_row.log_id) if api_call_log_row else "",
            raw_cost_usd=str(_actual_cost),
            target_type=target_type,
            **token_usage_properties(_token_usage),
        )
    except Exception:
        # Fail-open: metering issues never break an eval. Log at debug so
        # the failure is diagnoseable without spamming Sentry — legitimate
        # OSS deploys hit this path (Noop is a no-op, no exception) and
        # only actual EE-side failures produce a log line.
        logger.debug("eval_billing_emit_failed", exc_info=True)


def _run_evaluation(
    run_params,
    eval_model,
    eval_instance,
    observation_span,
    custom_eval_config,
    eval_task_id,
    eval_type_id,
    futureagi_eval,
    runner,
    raw_mapping,
    feedback_id=None,
):
    try:
        source_config = {
            "reference_id": observation_span.id,
            "is_futureagi_eval": futureagi_eval,
            "custom_eval_config_id": str(custom_eval_config.id),
        }
        source_config.update(
            {
                "mappings": run_params,
                "required_keys": list(run_params.keys()),
                "span_id": str(observation_span.id),
                "trace_id": str(observation_span.trace.id),
                "source": "tracer",
            }
        )
        if feedback_id:
            source_config.update({"feedback_id": str(feedback_id)})

        api_call_type = _get_api_call_type(custom_eval_config.model)

        workspace = observation_span.project.workspace
        if workspace is None:
            workspace = Workspace.objects.get(
                organization=observation_span.project.organization,
                is_default=True,
                is_active=True,
            )

        org = observation_span.project.organization
        billing = get_billing()
        usage_check = billing.check_usage(str(org.id), api_call_type)
        if not usage_check.allowed:
            raise ValueError(usage_check.reason or "Usage limit exceeded")

        api_call_log_row = billing.log_and_deduct(
            organization=org,
            api_call_type=api_call_type,
            source="tracer" if not feedback_id else "feedback",
            source_id=eval_model.id,
            config=source_config,
            workspace=workspace,
        )
        if billing.deduct_denied(api_call_log_row):
            if api_call_log_row is None:
                raise ValueError("API call not allowed : Error validating the api call.")
            raise ValueError("API call not allowed : ", api_call_log_row.status)

        # Apply the same empty-input rules the dataset and playground
        # paths use, so eval tasks behave consistently with everywhere
        # else evals can run. The validator also normalizes kwargs to
        # fill any missing required_keys with "" so the underlying eval
        # engine doesn't raise "Missing required key" for unmapped vars.
        from model_hub.utils.eval_input_validation import validate_eval_inputs

        partial_input_warning, run_params = validate_eval_inputs(
            eval_model, run_params, mapped_keys=(run_params or {}).keys()
        )

        start_time = time.time()
        result = eval_instance.run(**run_params)
        end_time = time.time()
        output_type = eval_model.config.get("output", "score")
        response = {
            "data": result.eval_results[0].get("data"),
            "failure": result.eval_results[0].get("failure"),
            "reason": result.eval_results[0].get("reason"),
            "runtime": result.eval_results[0].get("runtime"),
            "model": result.eval_results[0].get("model"),
            "metrics": result.eval_results[0].get("metrics"),
            "metadata": result.eval_results[0].get("metadata"),
            "output": output_type,
            "start_time": start_time,
            "end_time": end_time,
            "duration": end_time - start_time,
        }
        if partial_input_warning:
            response["warnings"] = [partial_input_warning]
        value = runner.format_output(result_data=response, eval_template=eval_model)

        if api_call_log_row is not None:
            config_dict = json.loads(api_call_log_row.config)
            output_payload = {"output": value, "reason": response["reason"]}
            if response.get("warnings"):
                output_payload["warnings"] = response["warnings"]
            config_dict.update(
                {
                    "input": response["data"],
                    "output": output_payload,
                }
            )
            api_call_log_row.config = json.dumps(config_dict)
            api_call_log_row.status = APICallStatusChoices.SUCCESS.value
            api_call_log_row.save()

        # Emit usage event for billing

        try:
            _token_usage = getattr(eval_instance, "token_usage", {})
            actual_cost = getattr(eval_instance, "cost", {}).get("total_cost", 0)
            billing = get_billing()
            credits = billing.ai_credits(actual_cost)
            billing.record_usage(
                str(observation_span.project.organization_id),
                api_call_type,
                amount=credits,
                source="tracer" if not feedback_id else "feedback",
                source_id=str(eval_model.id),
                model=custom_eval_config.model if custom_eval_config else "",
                workspace_id=str(workspace.id) if workspace else "",
                log_id=str(api_call_log_row.log_id) if api_call_log_row else None,
                raw_cost_usd=str(actual_cost),
                **token_usage_properties(_token_usage),
            )
        except Exception:
            pass  # Metering failure must not break eval

        # Ensure metadata is a dictionary before unpacking
        metadata = result.eval_results[0].get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        # Create kwargs dict for EvalLogger based on value type.
        # Persist partial-input warnings into output_metadata.warnings so
        # the eval task logs view (which reads EvalLogger) can render
        # them alongside the eval result.
        _output_metadata = {**metadata}
        if response.get("warnings"):
            _output_metadata["warnings"] = response["warnings"]
        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": _output_metadata,
            "eval_explanation": result.eval_results[0].get("reason"),
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "log_id": api_call_log_row.log_id if api_call_log_row else None,
        }

    except Exception as e:
        traceback.print_exc()
        error_message = str(e)
        try:
            if api_call_log_row is not None:
                api_call_log_row.status = APICallStatusChoices.ERROR.value
                current_config = json.loads(api_call_log_row.config)
                current_config.update({"output": {"output": None, "reason": str(e)}})
                api_call_log_row.config = json.dumps(current_config)
                api_call_log_row.save()
        except Exception:
            pass
        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": {
                "error": error_message,
                "custom_eval_config_name": custom_eval_config.name,
                "eval_template_name": custom_eval_config.eval_template.name,
            },
            "eval_explanation": f"Error during evaluation: {error_message}",
            "results_explanation": {"reason": error_message},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Error during evaluation: {error_message}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    # Determine the appropriate field based on value type
    if value != "ERROR":  # Only try to process value type if no error occurred
        logger_kwargs["value"] = value
        _dual_write_eval_value(
            value, _eval_config_output(custom_eval_config), logger_kwargs
        )

    return logger_kwargs


def _execute_composite_on_span(
    observation_span_id,
    custom_eval_config_id,
    eval_task_id,
    run_params=None,
    feedback_id=None,
):
    """Execute a composite `EvalTemplate` against a tracer span.

    Loads the span + custom eval config, resolves the composite's child
    links, and delegates to `execute_composite_children_sync`. Returns a
    `logger_kwargs` dict matching the shape `_execute_evaluation` emits
    for single evals, so the downstream `EvalLogger` writes behave
    identically regardless of composite vs single.
    """
    from model_hub.models.evals_metric import CompositeEvalChild
    from model_hub.utils.composite_execution import execute_composite_children_sync

    try:
        # CH 25.3 read when EVAL_SPAN_READ_SOURCE=clickhouse. Hybrid loader
        # constructs a partial Django ObservationSpan from the CH row; the
        # `select_related` FK preload is honored on the PG fallback path,
        # and project/organization/workspace lazy-load from PG on attribute
        # access in the CH path.
        from tracer.services.clickhouse.v2.eval_loader import get_observation_span

        observation_span = get_observation_span(
            observation_span_id,
            select_related=("project", "project__organization", "project__workspace"),
        )
        custom_eval_config = CustomEvalConfig.objects.get(
            id=custom_eval_config_id, deleted=False
        )
    except (ObservationSpan.DoesNotExist, CustomEvalConfig.DoesNotExist) as e:
        raise ValueError(f"Span composite eval load failed: {e}") from e

    parent = custom_eval_config.eval_template
    org = observation_span.project.organization
    workspace = observation_span.project.workspace

    child_links = list(
        CompositeEvalChild.objects.filter(parent=parent, deleted=False)
        .select_related("child", "pinned_version")
        .order_by("order")
    )
    if not child_links:
        raise ValueError(f"Composite {parent.id} has no children — cannot run on span.")

    try:
        outcome = execute_composite_children_sync(
            parent=parent,
            child_links=child_links,
            mapping=run_params or {},
            config=custom_eval_config.config or {},
            org=org,
            workspace=workspace,
            model=custom_eval_config.model,
            source="tracer_composite",
        )

        value = (
            outcome.aggregate_score
            if parent.aggregation_enabled
            else (outcome.summary or "")
        )
        response = {
            "data": run_params,
            "failure": False,
            "reason": outcome.summary or "",
            "runtime": 0,
            "model": custom_eval_config.model,
            "metrics": None,
            "metadata": {
                "composite_id": str(parent.id),
                "aggregation_enabled": parent.aggregation_enabled,
                "aggregation_function": parent.aggregation_function,
                "aggregate_pass": outcome.aggregate_pass,
                "children": [cr.model_dump() for cr in outcome.child_results],
            },
            "output": "score" if parent.aggregation_enabled else "text",
        }
        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": response["metadata"],
            "eval_explanation": outcome.summary or "",
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": None,
            "log_id": None,
        }
    except Exception as e:
        traceback.print_exc()
        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": {
                "error": str(e),
                "composite_id": str(parent.id),
            },
            "eval_explanation": f"Composite eval failed: {e}",
            "results_explanation": {"reason": str(e)},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Composite eval failed: {e}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": None,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    if value != "ERROR":
        logger_kwargs["value"] = value
        _dual_write_eval_value(
            value, _eval_config_output(custom_eval_config), logger_kwargs
        )

    return logger_kwargs


def _execute_composite_on_trace(
    *,
    trace: Trace,
    anchor_span: ObservationSpan,
    custom_eval_config: CustomEvalConfig,
    eval_task_id,
    run_params=None,
    feedback_id=None,
):
    """Execute a composite `EvalTemplate` against a Trace.

    Twin of `_execute_composite_on_span` but anchored to a trace. Resolves
    the composite's child links, delegates to `execute_composite_children_sync`,
    and returns a `logger_kwargs` dict shaped like the trace single-eval
    path at the bottom of `_execute_evaluation_for_trace` (target_type=trace,
    trace + anchor_span set, trace_session NULL). The caller writes the
    EvalLogger row.
    """
    from model_hub.models.evals_metric import CompositeEvalChild
    from model_hub.utils.composite_execution import execute_composite_children_sync

    parent = custom_eval_config.eval_template
    org = trace.project.organization
    workspace = trace.project.workspace
    if workspace is None:
        workspace = Workspace.objects.get(
            organization=org,
            is_default=True,
            is_active=True,
        )

    child_links = list(
        CompositeEvalChild.objects.filter(parent=parent, deleted=False)
        .select_related("child", "pinned_version")
        .order_by("order")
    )
    if not child_links:
        raise ValueError(
            f"Composite {parent.id} has no children — cannot run on trace."
        )

    # Mirror the single-eval trace path: set the workspace ContextVar so child
    # evals' tools (explore_trace etc.) see the right org scope.
    try:
        from tfc.middleware.workspace_context import set_workspace_context

        set_workspace_context(workspace=workspace, organization=org)
    except Exception as _ctx_err:
        logger.debug(
            "Failed to set workspace context for composite trace eval",
            error=str(_ctx_err),
        )

    try:
        outcome = execute_composite_children_sync(
            parent=parent,
            child_links=child_links,
            mapping=run_params or {},
            config=custom_eval_config.config or {},
            org=org,
            workspace=workspace,
            model=custom_eval_config.model,
            trace_context={
                "trace_id": str(trace.id),
                "anchor_span_id": str(anchor_span.id),
            },
            source="tracer_composite",
        )

        value = (
            outcome.aggregate_score
            if parent.aggregation_enabled
            else (outcome.summary or "")
        )
        response = {
            "data": run_params,
            "failure": False,
            "reason": outcome.summary or "",
            "runtime": 0,
            "model": custom_eval_config.model,
            "metrics": None,
            "metadata": {
                "composite_id": str(parent.id),
                "aggregation_enabled": parent.aggregation_enabled,
                "aggregation_function": parent.aggregation_function,
                "aggregate_pass": outcome.aggregate_pass,
                "children": [cr.model_dump() for cr in outcome.child_results],
            },
            "output": "score" if parent.aggregation_enabled else "text",
        }
        logger_kwargs = {
            "target_type": EvalTargetType.TRACE.value,
            "trace": trace,
            "observation_span": anchor_span,
            "trace_session": None,
            "output_metadata": response["metadata"],
            "eval_explanation": outcome.summary or "",
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": None,
        }
    except Exception as e:
        traceback.print_exc()
        logger_kwargs = {
            "target_type": EvalTargetType.TRACE.value,
            "trace": trace,
            "observation_span": anchor_span,
            "trace_session": None,
            "output_metadata": {
                "error": str(e),
                "composite_id": str(parent.id),
            },
            "eval_explanation": f"Composite eval failed: {e}",
            "results_explanation": {"reason": str(e)},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Composite eval failed: {e}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": None,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    if value != "ERROR":
        _dual_write_eval_value(
            value, _eval_config_output(custom_eval_config), logger_kwargs
        )

    return logger_kwargs


def _execute_composite_on_session(
    *,
    trace_session: TraceSession,
    custom_eval_config: CustomEvalConfig,
    eval_task_id,
    run_params=None,
    feedback_id=None,
):
    """Execute a composite `EvalTemplate` against a TraceSession.

    Twin of `_execute_composite_on_trace` but session-scoped. Writes a
    target_type='session' EvalLogger shape (trace_session set, observation_span
    + trace NULL). Sets the workspace ContextVar before delegation so child
    evals' tools (e.g. explore_trace) see the right org scope.
    """
    from model_hub.models.evals_metric import CompositeEvalChild
    from model_hub.utils.composite_execution import execute_composite_children_sync

    parent = custom_eval_config.eval_template
    org = trace_session.project.organization
    workspace = trace_session.project.workspace
    if workspace is None:
        workspace = Workspace.objects.get(
            organization=org,
            is_default=True,
            is_active=True,
        )

    child_links = list(
        CompositeEvalChild.objects.filter(parent=parent, deleted=False)
        .select_related("child", "pinned_version")
        .order_by("order")
    )
    if not child_links:
        raise ValueError(
            f"Composite {parent.id} has no children — cannot run on session."
        )

    # The explore_trace tool's live DB actions (list_trace_spans, span_detail)
    # call get_current_organization() to enforce tenant isolation. The
    # ContextVar is request-bound and not set in Temporal worker contexts.
    # Mirror the single-eval session path so children can drill into spans.
    try:
        from tfc.middleware.workspace_context import set_workspace_context

        set_workspace_context(
            workspace=workspace,
            organization=org,
        )
    except Exception as _ctx_err:
        logger.debug(
            "Failed to set workspace context for composite session eval",
            error=str(_ctx_err),
        )

    try:
        outcome = execute_composite_children_sync(
            parent=parent,
            child_links=child_links,
            mapping=run_params or {},
            config=custom_eval_config.config or {},
            org=org,
            workspace=workspace,
            model=custom_eval_config.model,
            session_context={"session_id": str(trace_session.id)},
            source="tracer_composite",
        )

        value = (
            outcome.aggregate_score
            if parent.aggregation_enabled
            else (outcome.summary or "")
        )
        response = {
            "data": run_params,
            "failure": False,
            "reason": outcome.summary or "",
            "runtime": 0,
            "model": custom_eval_config.model,
            "metrics": None,
            "metadata": {
                "composite_id": str(parent.id),
                "aggregation_enabled": parent.aggregation_enabled,
                "aggregation_function": parent.aggregation_function,
                "aggregate_pass": outcome.aggregate_pass,
                "children": [cr.model_dump() for cr in outcome.child_results],
            },
            "output": "score" if parent.aggregation_enabled else "text",
        }
        logger_kwargs = {
            "target_type": EvalTargetType.SESSION.value,
            "trace": None,
            "observation_span": None,
            # Unsaved CH vehicle → write the FK by id column (db_constraint=False);
            # see ``_execute_evaluation_for_session`` for the full rationale.
            "trace_session_id": str(trace_session.id),
            "output_metadata": response["metadata"],
            "eval_explanation": outcome.summary or "",
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": None,
        }
    except Exception as e:
        traceback.print_exc()
        logger_kwargs = {
            "target_type": EvalTargetType.SESSION.value,
            "trace": None,
            "observation_span": None,
            # Unsaved CH vehicle → id-column FK write (see success branch).
            "trace_session_id": str(trace_session.id),
            "output_metadata": {
                "error": str(e),
                "composite_id": str(parent.id),
            },
            "eval_explanation": f"Composite eval failed: {e}",
            "results_explanation": {"reason": str(e)},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Composite eval failed: {e}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": None,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    if value != "ERROR":
        _dual_write_eval_value(
            value, _eval_config_output(custom_eval_config), logger_kwargs
        )

    return logger_kwargs


def _execute_evaluation(
    observation_span_id,
    custom_eval_config_id,
    eval_task_id,
    type,
    run_params=None,
    feedback_id=None,
):
    from evaluations.constants import FUTUREAGI_EVAL_TYPES
    from evaluations.engine import EvalRequest, run_eval

    raw_mapping = run_params.copy()
    try:
        from tracer.services.clickhouse.v2.eval_loader import get_observation_span

        observation_span = get_observation_span(
            observation_span_id,
            select_related=("project", "project__organization", "project__workspace"),
        )

        custom_eval_config = CustomEvalConfig.objects.get(
            id=custom_eval_config_id, deleted=False
        )
    except ObservationSpan.DoesNotExist:
        raise ValueError("Observation span not found")  # noqa: B904
    except CustomEvalConfig.DoesNotExist:
        raise ValueError("Custom eval config not found")  # noqa: B904
    except Exception:
        raise Exception("Error in _execute_evaluation")  # noqa: B904

    eval_type_id = custom_eval_config.eval_template.config.get("eval_type_id")
    futureagi_eval = eval_type_id in FUTUREAGI_EVAL_TYPES
    eval_model = custom_eval_config.eval_template

    # Composite evals: fan out across children via the shared helper and
    # return a synthesised result that matches the shape downstream
    # logging expects. Single-template execution skips this branch.
    # Validator runs per-child inside the recursive call, not at the
    # parent — composite parents don't have their own required_keys.
    if eval_model.template_type == "composite":
        return _execute_composite_on_span(
            observation_span_id=observation_span_id,
            custom_eval_config_id=custom_eval_config_id,
            eval_task_id=eval_task_id,
            run_params=run_params,
            feedback_id=feedback_id,
        )

    # Apply the shared empty-input rules so eval tasks behave the same as
    # the dataset / playground / SDK paths. The validator raises when all
    # mapped inputs are empty (for custom evals) and otherwise returns a
    # partial_input warning we attach to the EvalLogger output_metadata.
    from model_hub.utils.eval_input_validation import validate_eval_inputs

    partial_input_warning, run_params = validate_eval_inputs(
        eval_model, run_params, mapped_keys=(run_params or {}).keys()
    )

    org_id = str(observation_span.project.organization.id)
    ws_id = (
        str(observation_span.project.workspace.id)
        if observation_span.project.workspace
        else None
    )

    # --- Cost tracking (caller-side) ---
    source_config = {
        "reference_id": observation_span.id,
        "is_futureagi_eval": futureagi_eval,
        "custom_eval_config_id": str(custom_eval_config.id),
        "mappings": run_params,
        "required_keys": list(run_params.keys()) if run_params else [],
        "span_id": str(observation_span.id),
        "trace_id": str(observation_span.trace.id),
        "source": "tracer",
    }
    if feedback_id:
        source_config["feedback_id"] = str(feedback_id)

    api_call_type = _get_api_call_type(custom_eval_config.model)
    workspace = observation_span.project.workspace
    if workspace is None:
        workspace = Workspace.objects.get(
            organization=observation_span.project.organization,
            is_default=True,
            is_active=True,
        )

    billing = get_billing()
    api_call_log_row = billing.log_and_deduct(
        organization=observation_span.project.organization,
        api_call_type=api_call_type,
        source="tracer" if not feedback_id else "feedback",
        source_id=eval_model.id,
        config=source_config,
        workspace=workspace,
    )
    if billing.deduct_denied(api_call_log_row):
        if api_call_log_row is None:
            raise ValueError("API call not allowed : Error validating the api call.")
        raise ValueError("API call not allowed : ", api_call_log_row.status)

    # --- Build context for data_injection support ---
    _eval_inputs = dict(run_params or {})
    _di = _di_normalize(
        (custom_eval_config.config or {})
        .get("run_config", {})
        .get("data_injection", {})
    )
    if _di["span_context"]:
        _eval_inputs["span_context"] = build_span_context(observation_span)
    if _di["trace_context"]:
        # Span-handler trace_context stays minimal — the agent already has
        # span_context for the originating span; trace-level aggregates are
        # only built when the eval is at trace/session level.
        _eval_inputs["trace_context"] = {
            "id": str(observation_span.trace_id),
            "span_id": str(observation_span.id),
        }
    if _di["session_context"]:
        # Trace.session is nullable (orphan traces aren't bound to a
        # session) — when missing, skip the kwarg entirely so the agent
        # sees no session_context at all rather than partial / null data.
        _session = getattr(getattr(observation_span, "trace", None), "session", None)
        _session_ctx = build_session_context(_session) if _session else None
        if _session_ctx is not None:
            _eval_inputs["session_context"] = _session_ctx

    # --- Run eval via unified engine ---
    try:
        result = run_eval(
            EvalRequest(
                eval_template=eval_model,
                inputs=_eval_inputs,
                model=custom_eval_config.model,
                kb_id=(
                    getattr(custom_eval_config.kb_id, "id", custom_eval_config.kb_id)
                    if custom_eval_config.kb_id
                    else None
                ),
                runtime_config=custom_eval_config.config,
                organization_id=org_id,
                workspace_id=ws_id,
            )
        )

        # Build the output payload up front so the partial-input warning
        # rides on the single save below — avoids losing the warning if a
        # follow-up save were to fail (see _build_apicall_output).
        if api_call_log_row is not None:
            config_dict = json.loads(api_call_log_row.config)
            config_dict.update(
                {
                    "input": result.data,
                    "output": _build_apicall_output(result, partial_input_warning),
                }
            )
            api_call_log_row.config = json.dumps(config_dict)
            api_call_log_row.status = APICallStatusChoices.SUCCESS.value
            api_call_log_row.save()

        # Dual-write: emit usage event for new billing system (cost-based)
        _emit_eval_billing(
            org_id=org_id,
            api_call_type=api_call_type,
            source_id=str(eval_model.id),
            target_type=EvalTargetType.SPAN.value,
            result=result,
            custom_eval_config=custom_eval_config,
            ws_id=ws_id,
            api_call_log_row=api_call_log_row,
            feedback_id=feedback_id,
        )

        # Parse metadata
        metadata = result.metadata
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        value = result.value
        response = {
            "data": result.data,
            "failure": result.failure,
            "reason": result.reason,
            "runtime": result.runtime,
            "model": result.model_used,
            "metrics": result.metrics,
            "metadata": result.metadata,
            "output": result.output_type,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "duration": result.duration,
        }

        _output_metadata = {**metadata}
        _attach_warning_to_metadata(response, _output_metadata, partial_input_warning)

        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": _output_metadata,
            "eval_explanation": result.reason,
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "log_id": api_call_log_row.log_id if api_call_log_row else None,
        }

    except Exception as e:
        traceback.print_exc()
        error_message = str(e)
        try:
            if api_call_log_row is not None:
                api_call_log_row.status = APICallStatusChoices.ERROR.value
                current_config = json.loads(api_call_log_row.config)
                current_config.update({"output": {"output": None, "reason": str(e)}})
                api_call_log_row.config = json.dumps(current_config)
                api_call_log_row.save()
        except Exception:
            pass
        logger_kwargs = {
            "trace": observation_span.trace,
            "observation_span": observation_span,
            "output_metadata": {
                "error": error_message,
                "custom_eval_config_name": custom_eval_config.name,
                "eval_template_name": custom_eval_config.eval_template.name,
            },
            "eval_explanation": f"Error during evaluation: {error_message}",
            "results_explanation": {"reason": error_message},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Error during evaluation: {error_message}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    # Determine the appropriate field based on value type
    if value != "ERROR":
        logger_kwargs["value"] = value
        _dual_write_eval_value(
            value, _eval_config_output(custom_eval_config), logger_kwargs
        )

    # Persist EvalLogger result
    if logger_kwargs:
        value = logger_kwargs.pop("value") if "value" in logger_kwargs else ""
        log_id = logger_kwargs.pop("log_id") if "log_id" in logger_kwargs else None
        try:
            eval_log = EvalLogger.objects.select_related(
                "observation_span",
                "observation_span__project",
                "observation_span__project__organization",
                "observation_span__project__workspace",
            ).get(
                eval_task_id=eval_task_id,
                observation_span=observation_span,
                custom_eval_config=custom_eval_config,
            )
            # Set each attribute from logger_kwargs
            for key, value in logger_kwargs.items():
                setattr(eval_log, key, value)
            # Save the changes
            eval_log.save()

        except EvalLogger.DoesNotExist:
            eval_log = EvalLogger.objects.create(**logger_kwargs)
            eval_log = EvalLogger.objects.select_related(
                "observation_span",
                "observation_span__project",
                "observation_span__project__organization",
                "observation_span__project__workspace",
            ).get(pk=eval_log.pk)

        from model_hub.services.error_localizer_service import (
            error_localizer_enabled,
        )

        if error_localizer_enabled(custom_eval_config):
            from model_hub.tasks.user_evaluation import (
                trigger_error_localization_for_span,
            )

            trigger_error_localization_for_span(
                eval_template=eval_model,
                eval_logger=eval_log,
                mapping=raw_mapping,
                eval_explanation=logger_kwargs.get("eval_explanation", ""),
                value=value,
                log_id=str(log_id),
            )

        if type == EXPERIMENT:
            # updating project version config
            project = observation_span.project
            project_version = observation_span.project_version
            project_version_config = project_version.config
            project_config = project.config

            if not project_config:
                project_config = get_default_project_version_config()

            if not project_version_config:
                project_version_config = get_default_trace_config()

            choices = (
                custom_eval_config.eval_template.choices
                if custom_eval_config.eval_template.choices
                else None
            )
            eval_template_config = custom_eval_config.eval_template.config or {}
            output_type = (
                eval_template_config.get("output", "score")
                if eval_template_config
                else "score"
            )

            eval_template_id = str(custom_eval_config.eval_template.id)

            if choices and output_type == EvalOutputType.CHOICES.value:
                for choice in choices:
                    present_config = FieldConfig(
                        id=str(custom_eval_config.id) + "**" + choice,
                        name=f"Avg. {choice} ({custom_eval_config.name})",
                        group_by="Evaluation Metrics",
                        output_type=output_type,
                        is_visible=True,
                        reverse_output=eval_template_config.get(
                            "reverse_output", False
                        ),
                        eval_template_id=eval_template_id,
                    )

                    present_config = asdict(present_config)

                    if present_config not in project_config:
                        project_config.append(present_config)
                    if present_config not in project_version_config:
                        project_version_config.append(present_config)
            else:
                present_config = FieldConfig(
                    id=str(custom_eval_config.id),
                    name=f"Avg. {custom_eval_config.name}",
                    group_by="Evaluation Metrics",
                    output_type=output_type,
                    is_visible=True,
                    reverse_output=eval_template_config.get("reverse_output", False),
                    eval_template_id=eval_template_id,
                )
                present_config = asdict(present_config)
                if present_config not in project_config:
                    project_config.append(present_config)
                if present_config not in project_version_config:
                    project_version_config.append(present_config)

            project.config = project_config
            project_version.config = project_version_config
            project.save()
            project_version.save()


def _create_error_eval_logger(
    observation_span: ObservationSpan,
    custom_eval_config: CustomEvalConfig,
    eval_task_id: str,
    error: Exception,
):
    """
    Persist the outcome when an eval could not run for an observation span.

    A missing required span attribute is a skip — the eval never ran because
    there was no input — so the row is written with ``skipped_reason`` set and
    ``error=False``. Read paths key off ``skipped_reason`` to render "Skipped"
    and exclude these rows from failure-rate metrics. Genuine failures keep the
    ``error=True`` / ``output_str="ERROR"`` shape.
    """
    skipped_reason = getattr(error, "skipped_reason", None)
    message = str(error)
    EvalLogger.objects.create(
        trace=observation_span.trace,
        observation_span=observation_span,
        output_metadata=None if skipped_reason else {"error": message},
        eval_explanation=(
            None if skipped_reason else f"Error during evaluation: {message}"
        ),
        results_explanation={} if skipped_reason else {"reason": message},
        eval_task_id=eval_task_id,
        custom_eval_config=custom_eval_config,
        error=skipped_reason is None,
        error_message=None if skipped_reason else f"Error during evaluation: {message}",
        output_str=None if skipped_reason else "ERROR",
        skipped_reason=skipped_reason,
    )


@temporal_activity(
    # Retry transient worker / LLM / network failures. The activity is
    # idempotent — an ``EvalLogger.filter(…).exists()`` check early in
    # the body short-circuits re-runs that already succeeded — so a
    # retry never double-writes. ``max_retries=0`` (the prior default)
    # meant any activity in flight during a worker restart or upstream
    # blip was silently dropped with no DLQ; on a 769-span fan-out we
    # observed ~86% of activities vanish across a few worker recycles.
    max_retries=3,
    retry_delay=60,
    time_limit=3600,
    queue="tasks_s",
)
def evaluate_observation_span(
    observation_span_id=None,
    custom_eval_config_id=None,
    feedback_id=None,
):
    if not observation_span_id or not custom_eval_config_id:
        raise ValueError(
            "observation_span_id and custom_eval_config_id are required parameters"
        )

    try:
        custom_eval_config = CustomEvalConfig.objects.get(id=custom_eval_config_id)
        from tracer.services.clickhouse.v2.eval_loader import get_observation_span

        observation_span = get_observation_span(observation_span_id)
    except CustomEvalConfig.DoesNotExist:
        raise ValueError(
            f"CustomEvalConfig with id {custom_eval_config_id} does not exist."
        ) from None
    except ObservationSpan.DoesNotExist:
        raise ValueError(
            f"ObservationSpan with id {observation_span_id} does not exist."
        ) from None

    # mark all previous eval_logger as deleted
    EvalLogger.objects.filter(
        observation_span=observation_span, custom_eval_config=custom_eval_config
    ).update(deleted=True, deleted_at=timezone.now())

    try:
        run_params = _process_mapping(
            custom_eval_config.mapping,
            observation_span,
            custom_eval_config.eval_template.id,
        )

        _execute_evaluation(
            observation_span_id=observation_span_id,
            custom_eval_config_id=custom_eval_config_id,
            eval_task_id=None,
            run_params=run_params,
            type=EXPERIMENT,
            feedback_id=feedback_id,
        )
        return True
    except ValueError as e:
        # Expected validation failure (e.g. missing required input for the eval).
        # Recorded as a failed-eval result below; logged at WARNING so it does
        # not page Sentry as a code bug.
        logger.warning(f"Error during evaluation in evaluate_observation_span: {e}")
        _create_error_eval_logger(observation_span, custom_eval_config, None, str(e))
        return False

    except Exception as e:
        logger.exception(
            f"Exception during evaluation in evaluate_observation_span: {e}"
        )
        return False


def _write_eval_logger(
    logger_kwargs, observation_span, custom_eval_config, eval_task_id
):
    """Write composite eval results to EvalLogger.

    Composite evals return a logger_kwargs dict from _execute_composite_on_span
    but don't persist it internally (single evals do this in _run_evaluation).
    """
    logger_kwargs.pop("value", None)
    logger_kwargs.pop("log_id", None)
    logger_kwargs.setdefault("trace", observation_span.trace)
    logger_kwargs.setdefault("observation_span", observation_span)
    logger_kwargs.setdefault("custom_eval_config", custom_eval_config)
    logger_kwargs.setdefault("eval_task_id", eval_task_id)
    try:
        EvalLogger.objects.create(**logger_kwargs)
    except Exception as e:
        logger.error(f"Failed to write composite eval logger: {e}")


@temporal_activity(
    # See the retry rationale on ``evaluate_observation_span`` above;
    # this is the per-span activity dispatched by the eval-task cron
    # for observe-mode projects and is the one most exposed to worker
    # recycles during large fan-outs.
    max_retries=3,
    retry_delay=60,
    time_limit=3600,
    queue="tasks_s",
)
def evaluate_observation_span_observe(
    observation_span_id=None,
    custom_eval_config_id=None,
    eval_task_id=None,
    feedback_id=None,
):
    if not observation_span_id or not custom_eval_config_id:
        raise ValueError(
            "observation_span_id and custom_eval_config_id are required parameters"
        )
    try:
        custom_eval_config = CustomEvalConfig.objects.get(id=custom_eval_config_id)
        from tracer.services.clickhouse.v2.eval_loader import get_observation_span

        observation_span = get_observation_span(observation_span_id)
    except CustomEvalConfig.DoesNotExist:
        raise ValueError(
            f"CustomEvalConfig with id {custom_eval_config_id} does not exist."
        ) from None
    except ObservationSpan.DoesNotExist:
        raise ValueError(
            f"ObservationSpan with id {observation_span_id} does not exist."
        ) from None

    if EvalLogger.objects.filter(
        observation_span_id=observation_span_id,
        custom_eval_config_id=custom_eval_config_id,
        eval_task_id=eval_task_id,
    ).exists():
        # ``EvalLogger.objects`` is BaseModelManager — soft-deleted rows are
        # already excluded, so an explicit ``deleted=False`` would be a
        # tautology.
        logger.info(
            f"EvalLogger with observation_span_id {observation_span_id} and custom_eval_config_id {custom_eval_config_id} already exists for eval task {eval_task_id}."
        )
        return

    # mark all previous eval_logger as deleted
    EvalLogger.objects.filter(
        observation_span=observation_span,
        custom_eval_config=custom_eval_config,
        eval_task_id=eval_task_id,
    ).update(deleted=True, deleted_at=timezone.now())

    try:
        run_params = _process_mapping(
            custom_eval_config.mapping,
            observation_span,
            custom_eval_config.eval_template.id,
        )

        result = _execute_evaluation(
            observation_span_id=observation_span_id,
            custom_eval_config_id=custom_eval_config_id,
            eval_task_id=eval_task_id,
            run_params=run_params,
            type=OBSERVE,
            feedback_id=feedback_id,
        )

        # Composite evals return a logger_kwargs dict instead of writing
        # to EvalLogger internally (single evals do it in _run_evaluation).
        # Persist the composite result here.
        if isinstance(result, dict) and "trace" in result:
            _write_eval_logger(
                result,
                observation_span,
                custom_eval_config,
                eval_task_id,
            )

        # Re-enabled with per-project Temporal dedup. The original per-row
        # enqueue caused embedding-service overload under backfill (N×M
        # fan-out → many concurrent same-project clustering runs each
        # re-embedding the whole unclustered backlog). A deterministic
        # per-project workflow id + USE_EXISTING conflict policy collapses
        # concurrent triggers for a project onto the single in-flight run;
        # once it completes the next trigger starts a fresh run that
        # re-sweeps whatever is still unclustered (cluster_eval_results is
        # idempotent), so coalescing is safe and loses nothing.
        try:
            from temporalio.common import WorkflowIDConflictPolicy

            from tracer.tasks.eval_clustering import cluster_eval_results_task

            project_id = str(observation_span.project_id)
            cluster_eval_results_task.apply_async(
                args=(project_id,),
                task_id=f"eval-cluster-{project_id}",
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        except Exception:
            logger.debug("eval_clustering_dispatch_skipped", exc_info=True)

        return True
    except ValueError as e:
        # Expected validation failure (missing/invalid eval input). Persisted as
        # a failed span below; WARNING keeps it out of the Sentry issue stream.
        logger.warning(
            f"Error during evaluation in evaluate_observation_span_observe: {e}"
        )
        if eval_task_id:
            try:
                with transaction.atomic():
                    eval_task = EvalTask.objects.select_for_update().get(
                        id=eval_task_id
                    )
                    failed_spans = (
                        eval_task.failed_spans if eval_task.failed_spans else []
                    )

                    failed_spans.append(
                        {
                            "observation_span_id": observation_span_id,
                            "custom_eval_config_id": custom_eval_config_id,
                            "error": str(e),
                        }
                    )

                    eval_task.failed_spans = failed_spans
                    eval_task.save(update_fields=["failed_spans", "updated_at"])
            except EvalTask.DoesNotExist:
                # Expected race: the EvalTask was deleted before this async task
                # ran. Nothing to update; downgrade to warning.
                logger.warning(f"EvalTask with id {eval_task_id} does not exist.")
            except Exception as e:
                logger.error(
                    f"Error during updating failed spans in exception handling evaluate_observation_span_observe: {e}"
                )
        _create_error_eval_logger(observation_span, custom_eval_config, eval_task_id, e)

        return False
    except Exception as e:
        logger.exception(
            f"Exception during evaluation in evaluate_observation_span_observe: {e}"
        )
        return False


@temporal_activity(
    # Same rationale as the two activities above — tag-triggered rerun
    # also benefits from idempotent retries.
    max_retries=3,
    retry_delay=60,
    time_limit=3600,
    queue="tasks_s",
)
def eval_observation_span_runner(observation_span_id, eval_tags):
    try:
        # Goes via CH 25.3 when EVAL_SPAN_READ_SOURCE=clickhouse (CH 25.3
        # span point-read replaces the heavy JSONB select on tracer_observation_span);
        # falls back to PG ORM otherwise. Raises ObservationSpan.DoesNotExist
        # in both modes — downstream `except` blocks unchanged.
        from tracer.services.clickhouse.v2.eval_loader import get_observation_span

        observation_span = get_observation_span(observation_span_id)
        if not observation_span or not eval_tags:
            return

        if isinstance(eval_tags, str):
            try:
                eval_tags = json.loads(eval_tags)
            except json.JSONDecodeError:
                eval_tags = {}
                logger.warning(
                    "eval_tags JSON decode failed, defaulting to empty dict."
                )

        for eval_tag in eval_tags:
            type = eval_tag.get("type")

            custom_eval_config_id = eval_tag.get("custom_eval_config_id")

            if (
                type == "OBSERVATION_SPAN_TYPE"
                and eval_tag.get("value").lower() == observation_span.observation_type
            ):
                try:
                    evaluate_observation_span(
                        observation_span.id, custom_eval_config_id
                    )
                except Exception as e:
                    custom_eval_config = CustomEvalConfig.objects.get(
                        id=custom_eval_config_id
                    )
                    EvalLogger.objects.create(
                        trace=observation_span.trace,
                        observation_span=observation_span,
                        output_metadata={
                            "error": str(e),
                            "observation_type": observation_span.observation_type,
                        },
                        eval_explanation=f"Error during evaluation: {str(e)}",
                        results_explanation={"reason": str(e)},
                        output_str="ERROR",
                        error=True,
                        error_message=f"Error during evaluation: {str(e)}",
                        custom_eval_config=custom_eval_config,
                    )

        # TODO(tech-debt): Setting eval_status on the span is lossy — it collapses
        # N eval results into one flag. Should be derived from EvalLogger rows instead.
        observation_span.eval_status = StatusType.COMPLETED.value
        observation_span.save()
    except Exception as e:
        traceback.print_exc()
        logger.error(f"Error during evaluation in eval_observation_span_runner: {e}")
        observation_span.eval_status = StatusType.FAILED.value
        observation_span.save()


def score_evals(evals: list):
    """
    Calculate average score for a list of EvalLogger entries.

    Args:
        evals: List of EvalLogger objects
    Returns:
        float: Average score (0-100) or 0 if no valid evaluations
    """
    if not evals:
        return {
            "avg_score": 0,
            "eval_response_data": {},
        }

    total_count = len(evals)
    valid_scores = []
    valid_scores_list = []
    eval_response_data = {}

    for eval_log in evals:
        # if eval_log.eval_id is None:
        #     continue

        custom_eval_config = eval_log.custom_eval_config

        if custom_eval_config and custom_eval_config.id not in eval_response_data:
            eval_response_data[str(custom_eval_config.id)] = {
                "passed_count": 0,
                "failed_count": 0,
                "count": 0,
                "failed_traces_count": 0,
                "failed_traces_ids": [],
                "name": "Low " + custom_eval_config.name,
            }

        eval_response_data[str(custom_eval_config.id)]["count"] += 1
        eval_response_data[str(custom_eval_config.id)]["name"] = (
            "Low " + custom_eval_config.name
        )

        # Handle boolean outputs (Pass/Fail)
        if eval_log.output_bool is not None:
            if eval_log.output_bool:
                valid_scores.append(100)
                eval_response_data[str(custom_eval_config.id)]["passed_count"] += 1
            else:
                valid_scores.append(0)
                eval_response_data[str(custom_eval_config.id)]["failed_count"] += 1
                if (
                    eval_log.trace.id
                    not in eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ]
                ):
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_count"
                    ] += 1
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ].append(eval_log.trace.id)
            continue

        # Handle float outputs (direct scores)
        if eval_log.output_float is not None:
            # Ensure score is between 0-100
            score = min(max(eval_log.output_float * 100, 0), 100)
            valid_scores.append(score)
            if score >= 30:
                eval_response_data[str(custom_eval_config.id)]["passed_count"] += 1
            else:
                eval_response_data[str(custom_eval_config.id)]["failed_count"] += 1
                if (
                    eval_log.trace.id
                    not in eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ]
                ):
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_count"
                    ] += 1
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ].append(eval_log.trace.id)
            continue

        # Handle string outputs ("Passed"/"Failed")
        if eval_log.output_str:
            if eval_log.output_str.lower() == "passed":
                valid_scores.append(100)
                eval_response_data[str(custom_eval_config.id)]["passed_count"] += 1
            elif (
                eval_log.output_str.lower() == "failed"
                or eval_log.output_str.lower() == "error"
            ):
                valid_scores.append(0)
                eval_response_data[str(custom_eval_config.id)]["failed_count"] += 1
                if (
                    eval_log.trace.id
                    not in eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ]
                ):
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_count"
                    ] += 1
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ].append(eval_log.trace.id)
            else:
                valid_scores.append(100)
                eval_response_data[str(custom_eval_config.id)]["passed_count"] += 1

            continue

        if eval_log.output_str_list:
            unique_values = set()
            if isinstance(eval_log.output_str_list, list):
                unique_values.update(eval_log.output_str_list)
                valid_scores_list.extend(list(unique_values))
                valid_scores_list = list(set(valid_scores_list))
                eval_response_data[str(custom_eval_config.id)]["passed_count"] += 1
            else:
                eval_response_data[str(custom_eval_config.id)]["failed_count"] += 1
                if (
                    eval_log.trace.id
                    not in eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ]
                ):
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_count"
                    ] += 1
                    eval_response_data[str(custom_eval_config.id)][
                        "failed_traces_ids"
                    ].append(eval_log.trace.id)

    # Calculate average score
    if len(valid_scores) > 0:
        return {
            "avg_score": round(sum(valid_scores) / total_count, 2),
            "eval_response_data": eval_response_data,
        }

    if len(valid_scores_list) > 0:
        return {
            "avg_score": valid_scores_list,
            "eval_response_data": eval_response_data,
        }

    return {"avg_score": 0, "eval_response_data": eval_response_data}


def avg_latency(evals: list):
    total_count = 0
    total_latency = 0

    for eval_log in evals:
        try:
            latency = eval_log.latency_ms
            total_latency += latency
            total_count += 1
        except Exception:
            logger.error("ERROR FETCHIHNG LATENCY")
            pass
    if total_count == 0:
        return 0
    return round(total_latency / total_count, 2)


def avg_cost(evals: list):
    total_count = 0
    total_cost = 0

    for eval_log in evals:
        try:
            # cost = eval_log.observation_span.prompt_tokens
            if eval_log.prompt_tokens is not None:
                total_cost += eval_log.prompt_tokens * 0.00000015
            if eval_log.completion_tokens is not None:
                total_cost += eval_log.completion_tokens * 0.0000006
            # total_cost += cost
            total_count += 1
        except Exception:
            logger.error("ERROR FETCHIHNG COST")
            pass
    if total_count == 0:
        return 0
    return round(total_cost / total_count, 2)


def avg_tokens(evals: list):
    total_count = 0
    total_tokens = 0

    for eval_log in evals:
        try:
            tokens = eval_log.total_tokens
            total_tokens += tokens
            total_count += 1
        except Exception:
            logger.error("ERROR FETCHIHNG COST")
            pass
    if total_count == 0:
        return 0
    return round(total_tokens / total_count, 2)


def score_categorical(evals: list, value):
    if not evals:
        return {
            "avg_score": 0,
        }
    passed_count = 0

    total_count = len(evals)

    for eval_log in evals:
        if eval_log.output_str_list:
            if value in eval_log.output_str_list:
                passed_count += 1

    return round(passed_count / total_count, 2) * 100 if total_count > 0 else 0


# ============================================================================
# Trace + session evaluator helpers
# ============================================================================
#
# The trace and session evaluators mirror evaluate_observation_span_observe
# but resolve their mapping variables from a different subject (a Trace or a
# TraceSession instead of an ObservationSpan), and write to EvalLogger with
# different target_type / FK shape:
#
#   target_type='trace'   -> observation_span = trace's root span,
#                            trace = the trace, trace_session = NULL
#   target_type='session' -> observation_span = NULL, trace = NULL,
#                            trace_session = the session
#
# Mapping resolvers walk dotted paths against the subject:
#
#   Trace fields:  ``input``, ``output``, ``name``, ``error``, ``tags``,
#                  ``metadata``, ``external_id``
#   Session fields: ``name``, ``bookmarked``
#   Hierarchy:      ``spans.<n>.<field>`` (n = 0-indexed integer or
#                   ``first``/``last``); for sessions also
#                   ``traces.<n>.spans.<m>.<field>``.
#
# Composite eval support spans all three row types: span, trace, and
# session evaluators each have a `_execute_composite_on_*` helper that
# fans out to `execute_composite_children_sync` and returns a
# `logger_kwargs` dict matching the target_type-specific FK shape.


# ── Anchor span resolution ──
#
# Trace-level eval rows MUST land with a non-NULL observation_span (per the
# EvalLogger check constraint). The "anchor" is the trace's root span — the one
# whose parent_span_id is NULL. If a trace has no explicit root (anomalous
# data), fall back to the earliest span by start_time. If a trace has zero
# spans, return None — the caller records failure on EvalTask.failed_spans
# and skips the EvalLogger write.


def _find_anchor_span(trace: Trace):
    # Single query: root spans (parent_span_id IS NULL) get rank 0 so they
    # sort first; non-root spans fall back to rank 1. Ties within a rank
    # break on start_time then id for determinism. Empty traces → first()
    # returns None, matching the original contract. Saves one DB round-trip
    # per trace — meaningful when process_eval_task dispatches across
    # hundreds of traces per tick.
    from django.db.models import Case, IntegerField, When

    return (
        trace.observation_spans.annotate(
            _root_rank=Case(
                When(parent_span_id__isnull=True, then=0),
                default=1,
                output_field=IntegerField(),
            )
        )
        .order_by("_root_rank", "start_time", "id")
        .first()
    )


# ── Path resolution ──
#
# Recursive walker that handles the dot-notation grammar specified in the
# row_type plan: scalar fields, JSONField traversal, indexed/positional
# child collections (``spans.0`` / ``spans.first`` / ``traces.last``), and
# composed paths through children (``traces.0.spans.0.input``).


def _resolve_collection_path(items: list, path: str, item_resolver):
    """Walk into an ordered collection — supports indices and ``first``/``last``."""
    if not path:
        return items
    parts = path.split(".", 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if head == "first":
        return item_resolver(items[0], rest) if items else _MISSING
    if head == "last":
        return item_resolver(items[-1], rest) if items else _MISSING

    try:
        idx = int(head)
    except ValueError:
        return _MISSING
    if idx < 0 or idx >= len(items):
        return _MISSING
    return item_resolver(items[idx], rest)


# Allow-list of model attributes the trace + session mapping resolvers
# expose. Prevents users from mapping eval inputs to internal Django state
# (``_state``, ``pk``, manager refs, methods, FK-Model objects) and keeps
# the mappable surface a deliberate API contract — not "whatever happens
# to be on the model". When a new field is added to one of these models,
# decide whether it belongs in the eval-mapping surface and update the
# set if so. Span resolution intentionally has no allow-list — it routes
# through the OTel ``span_attributes`` JSONField bag, which is the
# canonical surface the span mapping picker exposes today.
_TRACE_PUBLIC_FIELDS = frozenset(
    {"input", "output", "name", "error", "tags", "metadata", "external_id"}
)
_SESSION_PUBLIC_FIELDS = frozenset({"name", "bookmarked"})

# Span model fields that are stored as dedicated DB columns (not inside
# ``span_attributes``).  The eval mapping picker can expose these via
# ``spans.<n>.<field>`` paths, but they won't be found by
# ``_resolve_attr(span_attrs, …)`` because they live on the Django model,
# not in the JSON bag.  This allow-list mirrors the pattern used by
# ``_TRACE_PUBLIC_FIELDS`` above.
_SPAN_PUBLIC_FIELDS = frozenset(
    {
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost",
        "response_time",
        "model",
        "name",
        "observation_type",
        "status",
        "status_message",
        "provider",
        "input",
        "output",
    }
)


def _resolve_span_path(span: ObservationSpan, path: str):
    """Walk a path against a span via the ``span_attributes`` bag.

    Routes through ``_resolve_attr(span_attrs, path)`` — same surface as the
    pre-existing ``_process_mapping`` resolver, so a saved span mapping that
    works at the span level also works when the path bottoms out at a span
    via ``spans.<n>.<field>`` from a trace or session resolver. The SDK
    mirrors model fields (``input``, ``output``, ``model``, etc.) into
    ``span_attributes`` during ingestion, so users don't lose access to
    them.

    The explicit ``span_attributes`` head case lets a path return the
    whole bag (``spans.0.span_attributes``) or walk into a nested key
    (``spans.0.span_attributes.foo.bar``) without going through the
    aliasing fallback in ``_resolve_attr``.
    """
    from tracer.utils.attribute_accessor import get_span_attributes

    if not path:
        return span

    parts = path.split(".", 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if head == "span_attributes":
        span_attrs = get_span_attributes(span)
        if not rest:
            return span_attrs
        return _resolve_attr(span_attrs, rest)

    span_attrs = get_span_attributes(span)
    result = _resolve_attr(span_attrs, path)
    if result is not _MISSING:
        return result

    if head in _SPAN_PUBLIC_FIELDS and not rest:
        value = getattr(span, head, _MISSING)
        if value is not _MISSING:
            return value

    return _MISSING


def _resolve_trace_path(trace: Trace, path: str):
    """Walk a path against a trace; supports ``spans.<n>.<field>`` recursion."""
    if not path:
        return trace

    parts = path.split(".", 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if head in _TRACE_PUBLIC_FIELDS:
        value = getattr(trace, head)
        if not rest:
            return value
        walked = _walk_dotted_path(value, rest)
        return walked if walked is not None else _MISSING

    if head == "spans":
        spans = list(trace.observation_spans.order_by("start_time", "id"))
        return _resolve_collection_path(spans, rest, _resolve_span_path)

    return _MISSING


def _resolve_session_path(trace_session: TraceSession, path: str):
    """Walk a path against a session; supports ``traces.<n>.spans.<m>.<field>``."""
    if not path:
        return trace_session

    parts = path.split(".", 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if head in _SESSION_PUBLIC_FIELDS:
        value = getattr(trace_session, head)
        if not rest:
            return value
        walked = _walk_dotted_path(value, rest)
        return walked if walked is not None else _MISSING

    if head == "traces":
        # Match the trace-listing UI's ordering (``list_traces_of_session``
        # in ``tracer/views/trace.py``): earliest root span's ``start_time``,
        # falling back to ``created_at`` when no root span has landed yet.
        # Without this, sessions whose traces share a ``created_at`` (the
        # SDK stamps every trace in a run with the same instant) tie-break
        # by id alphabetically -- picking a "trace 0" the user never sees
        # at the top of the trace list.
        from django.db.models import OuterRef, Subquery
        from django.db.models.functions import Coalesce

        from tracer.services.clickhouse.v2 import get_reader

        # Derive the session's trace ids from CH spans, NOT the
        # ``trace_session.traces`` reverse FK (Slice D, DESIGN §5 /
        # PG_ORM_READ_MIGRATION): post-flip the ``Trace.session`` FK is ``None``
        # for EVERY trace, so the reverse accessor returns EMPTY for ALL sessions
        # (and ``trace_session`` here is the UNSAVED vehicle
        # ``evaluate_trace_session_observe`` builds — it has no DB rows pointing at
        # it at all). ``session_trace_ids`` is remap-aware on the input id AND each
        # span's id, so a straddler yields its old∪new traces as one set and a
        # net-new session yields its real set. The vehicle carries ``.id`` and
        # ``.project_id`` (the eval config's project). Trace stays PG → ``id__in``.
        _project_id = getattr(trace_session, "project_id", None)
        _session_id = getattr(trace_session, "id", None)
        if _project_id is None or _session_id is None:
            _trace_ids: list[str] = []
        else:
            with get_reader() as reader:
                _trace_ids = reader.session_trace_ids(
                    str(_project_id), str(_session_id)
                )

        root_start = (
            # The earliest-root-span ordering stays a PG ``ObservationSpan``
            # Subquery correlated against the outer Trace.id (CH25-TODO: no
            # CHSpanReader equivalent for a correlated per-trace earliest-root
            # fetch as of 2026-05-26; the trace SET is now CH-derived above, the
            # ORDERING remains PG — deferred per ch25 chunk Rule 1).
            ObservationSpan.objects.filter(
                trace_id=OuterRef("id"), parent_span_id__isnull=True
            )
            .order_by("start_time")
            .values("start_time")[:1]
        )
        traces = list(
            Trace.objects.filter(id__in=_trace_ids, deleted=False)
            .annotate(_root_start=Coalesce(Subquery(root_start), "created_at"))
            .order_by("_root_start", "id")
        )
        return _resolve_collection_path(traces, rest, _resolve_trace_path)

    return _MISSING


def _process_trace_mapping(
    mapping: dict | None, trace: Trace, eval_template_id
) -> dict:
    """Resolve a saved mapping against a Trace.

    Mirrors ``_process_mapping`` (the span resolver) but walks the trace
    path grammar: trace fields, child-span aggregators, and dotted paths
    into ``spans.<n>.<field>``. Raises ``ValueError`` on a required-key
    miss so the caller writes an error EvalLogger row and continues.
    """
    if not mapping:
        return {}

    parsed: dict = {}
    is_user_custom_eval = False

    try:
        given_eval_template = EvalTemplate.no_workspace_objects.get(id=eval_template_id)
        optional_keys = given_eval_template.config.get("optional_keys", []) or []
        is_user_custom_eval = bool(given_eval_template.config.get("custom_eval", False))
        for key in optional_keys:
            if key in mapping and (mapping[key] is None or mapping[key] == ""):
                mapping.pop(key)
    except EvalTemplate.DoesNotExist:
        # A missing EvalTemplate means we cannot determine which mapping
        # keys are optional, so treating every key as required would
        # produce misleading "Required attribute X not found" errors for
        # legitimately-optional keys. Fail fast — the caller writes a
        # failed EvalLogger row and continues, same as on a required-key
        # miss below.
        logger.error(
            f"EvalTemplate {eval_template_id} not found while processing "
            f"trace mapping for trace {trace.id}"
        )
        raise ValueError(f"EvalTemplate {eval_template_id} not found") from None

    for key, attribute in mapping.items():
        value = _resolve_trace_path(trace, attribute) if attribute else _MISSING
        if value is _MISSING:
            if is_user_custom_eval:
                # Custom eval: treat missing trace attribute as empty so
                # the shared validator can fail (all empty) or warn
                # (partial). Mirrors the span / dataset behaviour.
                parsed[key] = ""
                continue
            # Expected: the user's eval references an attribute absent on this
            # trace. Raw emitter before the ValueError that the outer
            # evaluate_trace_observe handler catches and persists as failed. Warning.
            logger.warning(
                f"Required attribute '{attribute}' for key '{key}' not found "
                f"on trace {trace.id}"
            )
            raise ValueError(
                f"Required attribute '{attribute}' for key '{key}' not found "
                f"on trace {trace.id}"
            )
        parsed[key] = value if isinstance(value, str) else json.dumps(value)

    return parsed


def _process_session_mapping(
    mapping: dict | None, trace_session: TraceSession, eval_template_id
) -> dict:
    """Resolve a saved mapping against a TraceSession."""
    if not mapping:
        return {}

    parsed: dict = {}
    is_user_custom_eval = False

    try:
        given_eval_template = EvalTemplate.no_workspace_objects.get(id=eval_template_id)
        optional_keys = given_eval_template.config.get("optional_keys", []) or []
        is_user_custom_eval = bool(given_eval_template.config.get("custom_eval", False))
        for key in optional_keys:
            if key in mapping and (mapping[key] is None or mapping[key] == ""):
                mapping.pop(key)
    except EvalTemplate.DoesNotExist:
        # See ``_process_trace_mapping`` above for the rationale: silently
        # skipping optional-keys handling on a missing template produces
        # misleading "required attribute not found" errors. Fail fast.
        logger.error(
            f"EvalTemplate {eval_template_id} not found while processing "
            f"session mapping for session {trace_session.id}"
        )
        raise ValueError(f"EvalTemplate {eval_template_id} not found") from None

    for key, attribute in mapping.items():
        value = (
            _resolve_session_path(trace_session, attribute) if attribute else _MISSING
        )
        if value is _MISSING:
            if is_user_custom_eval:
                # Custom eval: treat missing session attribute as empty
                # so the shared validator can fail (all empty) or warn
                # (partial), matching dataset/span behaviour.
                parsed[key] = ""
                continue
            # Expected: the user's eval references an attribute absent on this
            # session. Raw emitter before the ValueError that the outer
            # evaluate_trace_session_observe handler catches and persists as failed. Warning.
            logger.warning(
                f"Required attribute '{attribute}' for key '{key}' not found "
                f"on session {trace_session.id}"
            )
            raise ValueError(
                f"Required attribute '{attribute}' for key '{key}' not found "
                f"on session {trace_session.id}"
            )
        parsed[key] = value if isinstance(value, str) else json.dumps(value)

    return parsed


# ── Eval execution: trace ──


def _execute_evaluation_for_trace(
    *,
    trace: Trace,
    anchor_span: ObservationSpan,
    custom_eval_config: CustomEvalConfig,
    eval_task_id,
    run_params: dict,
    feedback_id=None,
):
    """Run the eval engine against a trace + persist the EvalLogger row.

    Twin of ``_execute_evaluation`` — same flow (cost log → run_eval → write
    logger), but resolves project/org/workspace off the trace and writes
    a target_type='trace' row anchored to ``anchor_span``. Composite
    templates fan out via ``_execute_composite_on_trace``; children log
    their own cost rows so the parent cost-log path is skipped.
    """
    from evaluations.constants import FUTUREAGI_EVAL_TYPES
    from evaluations.engine import EvalRequest, run_eval

    eval_template = custom_eval_config.eval_template
    if eval_template.template_type == "composite":
        logger_kwargs = _execute_composite_on_trace(
            trace=trace,
            anchor_span=anchor_span,
            custom_eval_config=custom_eval_config,
            eval_task_id=eval_task_id,
            run_params=run_params,
            feedback_id=feedback_id,
        )
        EvalLogger.objects.create(**logger_kwargs)
        return
    eval_type_id = eval_template.config.get("eval_type_id")
    futureagi_eval = eval_type_id in FUTUREAGI_EVAL_TYPES

    # Apply the shared empty-input rules — see _execute_evaluation for
    # the rationale. Same partial_input warning gets attached to the
    # EvalLogger output_metadata so the trace-target row gets the badge.
    from model_hub.utils.eval_input_validation import validate_eval_inputs

    partial_input_warning, run_params = validate_eval_inputs(
        eval_template, run_params, mapped_keys=(run_params or {}).keys()
    )

    org_id = str(trace.project.organization.id)
    workspace = trace.project.workspace
    if workspace is None:
        workspace = Workspace.objects.get(
            organization=trace.project.organization,
            is_default=True,
            is_active=True,
        )
    ws_id = str(workspace.id) if workspace else None

    source_config = {
        "reference_id": str(trace.id),
        "is_futureagi_eval": futureagi_eval,
        "custom_eval_config_id": str(custom_eval_config.id),
        "mappings": run_params,
        "required_keys": list(run_params.keys()) if run_params else [],
        "trace_id": str(trace.id),
        "span_id": str(anchor_span.id),
        "target_type": EvalTargetType.TRACE.value,
        "source": "tracer",
    }
    if feedback_id:
        source_config["feedback_id"] = str(feedback_id)

    api_call_type = _get_api_call_type(custom_eval_config.model)
    billing = get_billing()
    api_call_log_row = billing.log_and_deduct(
        organization=trace.project.organization,
        api_call_type=api_call_type,
        source="tracer" if not feedback_id else "feedback",
        source_id=eval_template.id,
        config=source_config,
        workspace=workspace,
    )
    if billing.deduct_denied(api_call_log_row):
        if api_call_log_row is None:
            raise ValueError("API call not allowed : Error validating the api call.")
        raise ValueError("API call not allowed : ", api_call_log_row.status)

    # --- Set workspace context for tools that need org-scoping ---
    # See _execute_evaluation_for_session for rationale; same applies here.
    try:
        from tfc.middleware.workspace_context import set_workspace_context

        set_workspace_context(
            workspace=workspace,
            organization=trace.project.organization,
        )
    except Exception as _ctx_err:
        logger.warning("Failed to set workspace context for trace eval: %s", _ctx_err)

    # --- Build context for data_injection support (trace-scoped) ---
    # Mirrors the span-level _execute_evaluation block. At trace level, the
    # entity being evaluated is the Trace itself (anchored on a span):
    #   trace_context   → trace identity + name. Agents drill into spans
    #                     via the explore_trace tool using these IDs.
    #   session_context → walk trace.session (nullable for orphan traces);
    #                     build full session aggregate when present.
    #   span_context    → the anchor_span data, same shape as the span-level
    #                     handler. Useful when the eval is conceptually
    #                     trace-scoped but the anchor span has rich detail.
    _eval_inputs = dict(run_params or {})
    _di = _di_normalize(
        (custom_eval_config.config or {})
        .get("run_config", {})
        .get("data_injection", {})
    )
    if _di["trace_context"]:
        _eval_inputs["trace_context"] = build_trace_context(trace)
    if _di["session_context"]:
        _session = getattr(trace, "session", None)
        _session_ctx = build_session_context(_session) if _session else None
        if _session_ctx is not None:
            _eval_inputs["session_context"] = _session_ctx
    if _di["span_context"]:
        _eval_inputs["span_context"] = build_span_context(anchor_span)

    try:
        result = run_eval(
            EvalRequest(
                eval_template=eval_template,
                inputs=_eval_inputs,
                model=custom_eval_config.model,
                kb_id=(
                    getattr(custom_eval_config.kb_id, "id", custom_eval_config.kb_id)
                    if custom_eval_config.kb_id
                    else None
                ),
                runtime_config=custom_eval_config.config,
                organization_id=org_id,
                workspace_id=ws_id,
            )
        )

        if api_call_log_row is not None:
            config_dict = json.loads(api_call_log_row.config)
            config_dict.update(
                {
                    "input": result.data,
                    "output": _build_apicall_output(result, partial_input_warning),
                }
            )
            api_call_log_row.config = json.dumps(config_dict)
            api_call_log_row.status = APICallStatusChoices.SUCCESS.value
            api_call_log_row.save()

        # Dual-write: emit usage event for new billing system (cost-based)
        _emit_eval_billing(
            org_id=org_id,
            api_call_type=api_call_type,
            source_id=str(eval_template.id),
            target_type=EvalTargetType.TRACE.value,
            result=result,
            custom_eval_config=custom_eval_config,
            ws_id=ws_id,
            api_call_log_row=api_call_log_row,
            feedback_id=feedback_id,
        )

        metadata = result.metadata
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        value = result.value
        response = {
            "data": result.data,
            "failure": result.failure,
            "reason": result.reason,
            "runtime": result.runtime,
            "model": result.model_used,
            "metrics": result.metrics,
            "metadata": result.metadata,
            "output": result.output_type,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "duration": result.duration,
        }
        _output_metadata = {**metadata}
        _attach_warning_to_metadata(response, _output_metadata, partial_input_warning)
        logger_kwargs = {
            "target_type": EvalTargetType.TRACE.value,
            "trace": trace,
            "observation_span": anchor_span,
            "trace_session": None,
            "output_metadata": _output_metadata,
            "eval_explanation": result.reason,
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
        }
    except Exception as e:
        traceback.print_exc()
        error_message = str(e)
        try:
            if api_call_log_row is not None:
                api_call_log_row.status = APICallStatusChoices.ERROR.value
                current_config = json.loads(api_call_log_row.config)
                current_config.update({"output": {"output": None, "reason": str(e)}})
                api_call_log_row.config = json.dumps(current_config)
                api_call_log_row.save()
        except Exception:
            pass
        logger_kwargs = {
            "target_type": EvalTargetType.TRACE.value,
            "trace": trace,
            "observation_span": anchor_span,
            "trace_session": None,
            "output_metadata": {
                "error": error_message,
                "custom_eval_config_name": custom_eval_config.name,
                "eval_template_name": eval_template.name,
            },
            "eval_explanation": f"Error during evaluation: {error_message}",
            "results_explanation": {"reason": error_message},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Error during evaluation: {error_message}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    if value != "ERROR":
        _dual_write_eval_value(
            value, _eval_config_output(custom_eval_config), logger_kwargs
        )

    EvalLogger.objects.create(**logger_kwargs)


def _execute_evaluation_for_session(
    *,
    trace_session: TraceSession,
    custom_eval_config: CustomEvalConfig,
    eval_task_id,
    run_params: dict,
    feedback_id=None,
):
    """Twin of ``_execute_evaluation_for_trace`` but for sessions.

    Composite templates fan out via ``_execute_composite_on_session``;
    children log their own cost rows so the parent cost-log path is skipped.
    """
    from evaluations.constants import FUTUREAGI_EVAL_TYPES
    from evaluations.engine import EvalRequest, run_eval

    eval_template = custom_eval_config.eval_template
    if eval_template.template_type == "composite":
        logger_kwargs = _execute_composite_on_session(
            trace_session=trace_session,
            custom_eval_config=custom_eval_config,
            eval_task_id=eval_task_id,
            run_params=run_params,
            feedback_id=feedback_id,
        )
        EvalLogger.objects.create(**logger_kwargs)
        return
    eval_type_id = eval_template.config.get("eval_type_id")
    futureagi_eval = eval_type_id in FUTUREAGI_EVAL_TYPES

    # Shared empty-input rules — see _execute_evaluation for rationale.
    from model_hub.utils.eval_input_validation import validate_eval_inputs

    partial_input_warning, run_params = validate_eval_inputs(
        eval_template, run_params, mapped_keys=(run_params or {}).keys()
    )

    org_id = str(trace_session.project.organization.id)
    workspace = trace_session.project.workspace
    if workspace is None:
        workspace = Workspace.objects.get(
            organization=trace_session.project.organization,
            is_default=True,
            is_active=True,
        )
    ws_id = str(workspace.id) if workspace else None

    source_config = {
        "reference_id": str(trace_session.id),
        "is_futureagi_eval": futureagi_eval,
        "custom_eval_config_id": str(custom_eval_config.id),
        "mappings": run_params,
        "required_keys": list(run_params.keys()) if run_params else [],
        "session_id": str(trace_session.id),
        "target_type": EvalTargetType.SESSION.value,
        "source": "tracer",
    }
    if feedback_id:
        source_config["feedback_id"] = str(feedback_id)

    api_call_type = _get_api_call_type(custom_eval_config.model)
    billing = get_billing()
    api_call_log_row = billing.log_and_deduct(
        organization=trace_session.project.organization,
        api_call_type=api_call_type,
        source="tracer" if not feedback_id else "feedback",
        source_id=eval_template.id,
        config=source_config,
        workspace=workspace,
    )
    if billing.deduct_denied(api_call_log_row):
        if api_call_log_row is None:
            raise ValueError("API call not allowed : Error validating the api call.")
        raise ValueError("API call not allowed : ", api_call_log_row.status)

    # --- Set workspace context for tools that need org-scoping ---
    # The explore_trace tool's live DB actions (list_trace_spans, span_detail)
    # call get_current_organization() to enforce tenant isolation. The
    # ContextVar is request-bound and not set in Temporal worker contexts.
    # Set it here from the session's project so the agent can drill into
    # individual trace spans during exploration.
    try:
        from tfc.middleware.workspace_context import set_workspace_context

        set_workspace_context(
            workspace=workspace,
            organization=trace_session.project.organization,
        )
    except Exception as _ctx_err:
        logger.warning("Failed to set workspace context for session eval: %s", _ctx_err)

    # --- Build context for data_injection support (session-scoped) ---
    # Mirrors the span-level _execute_evaluation block. At session level, the
    # entity being evaluated is the TraceSession, so:
    #   session_context → full session aggregate (traces, span/error counts,
    #                     tokens, cost, time range — via build_session_context)
    #   trace_context   → not applicable at session-level (no single focal
    #                     trace; the session has many). We omit to avoid
    #                     committing to an ambiguous "first trace" semantic.
    #                     Agents can drill into individual traces via the
    #                     session_context.traces[] summaries + explore_trace.
    #   span_context    → not applicable at session-level.
    _eval_inputs = dict(run_params or {})
    _di = _di_normalize(
        (custom_eval_config.config or {})
        .get("run_config", {})
        .get("data_injection", {})
    )
    if _di["session_context"]:
        _session_ctx = build_session_context(trace_session)
        if _session_ctx is not None:
            _eval_inputs["session_context"] = _session_ctx

    try:
        result = run_eval(
            EvalRequest(
                eval_template=eval_template,
                inputs=_eval_inputs,
                model=custom_eval_config.model,
                kb_id=(
                    getattr(custom_eval_config.kb_id, "id", custom_eval_config.kb_id)
                    if custom_eval_config.kb_id
                    else None
                ),
                runtime_config=custom_eval_config.config,
                organization_id=org_id,
                workspace_id=ws_id,
            )
        )

        if api_call_log_row is not None:
            config_dict = json.loads(api_call_log_row.config)
            config_dict.update(
                {
                    "input": result.data,
                    "output": _build_apicall_output(result, partial_input_warning),
                }
            )
            api_call_log_row.config = json.dumps(config_dict)
            api_call_log_row.status = APICallStatusChoices.SUCCESS.value
            api_call_log_row.save()

        # Dual-write: emit usage event for new billing system (cost-based)
        _emit_eval_billing(
            org_id=org_id,
            api_call_type=api_call_type,
            source_id=str(eval_template.id),
            target_type=EvalTargetType.SESSION.value,
            result=result,
            custom_eval_config=custom_eval_config,
            ws_id=ws_id,
            api_call_log_row=api_call_log_row,
            feedback_id=feedback_id,
        )

        metadata = result.metadata
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        value = result.value
        response = {
            "data": result.data,
            "failure": result.failure,
            "reason": result.reason,
            "runtime": result.runtime,
            "model": result.model_used,
            "metrics": result.metrics,
            "metadata": result.metadata,
            "output": result.output_type,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "duration": result.duration,
        }
        _output_metadata = {**metadata}
        _attach_warning_to_metadata(response, _output_metadata, partial_input_warning)
        logger_kwargs = {
            "target_type": EvalTargetType.SESSION.value,
            "trace": None,
            "observation_span": None,
            # Write the session FK by its id column (db_constraint=False) — the
            # ``trace_session`` here is an UNSAVED CH-sourced vehicle with no PG
            # row, so the relation can't be assigned, but the soft id is exactly
            # what the column needs (same pattern as the already-CH-only
            # trace/observation_span FKs on this row).
            "trace_session_id": str(trace_session.id),
            "output_metadata": _output_metadata,
            "eval_explanation": result.reason,
            "results_explanation": response,
            "eval_task_id": eval_task_id,
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
        }
    except Exception as e:
        traceback.print_exc()
        error_message = str(e)
        try:
            if api_call_log_row is not None:
                api_call_log_row.status = APICallStatusChoices.ERROR.value
                current_config = json.loads(api_call_log_row.config)
                current_config.update({"output": {"output": None, "reason": str(e)}})
                api_call_log_row.config = json.dumps(current_config)
                api_call_log_row.save()
        except Exception:
            pass
        logger_kwargs = {
            "target_type": EvalTargetType.SESSION.value,
            "trace": None,
            "observation_span": None,
            # See success branch: id-column FK write for the unsaved CH vehicle.
            "trace_session_id": str(trace_session.id),
            "output_metadata": {
                "error": error_message,
                "custom_eval_config_name": custom_eval_config.name,
                "eval_template_name": eval_template.name,
            },
            "eval_explanation": f"Error during evaluation: {error_message}",
            "results_explanation": {"reason": error_message},
            "output_str": "ERROR",
            "error": True,
            "error_message": f"Error during evaluation: {error_message}",
            "custom_eval_config": custom_eval_config,
            "eval_type_id": eval_type_id,
            "eval_task_id": eval_task_id,
        }
        value = "ERROR"

    if value != "ERROR":
        _dual_write_eval_value(
            value, _eval_config_output(custom_eval_config), logger_kwargs
        )

    EvalLogger.objects.create(**logger_kwargs)


# ── Error helpers ──


def _create_error_eval_logger_for_trace(
    trace: Trace,
    anchor_span: ObservationSpan,
    custom_eval_config: CustomEvalConfig,
    eval_task_id,
    error_message: str,
):
    """Persist a target_type='trace' EvalLogger row with error=True."""
    EvalLogger.objects.create(
        target_type=EvalTargetType.TRACE.value,
        trace=trace,
        observation_span=anchor_span,
        trace_session=None,
        output_metadata={"error": error_message},
        eval_explanation=f"Error during evaluation: {error_message}",
        results_explanation={"reason": error_message},
        eval_task_id=eval_task_id,
        custom_eval_config=custom_eval_config,
        error=True,
        error_message=f"Error during evaluation: {error_message}",
        output_str="ERROR",
    )


def _create_error_eval_logger_for_session(
    trace_session: TraceSession,
    custom_eval_config: CustomEvalConfig,
    eval_task_id,
    error_message: str,
):
    """Persist a target_type='session' EvalLogger row with error=True."""
    EvalLogger.objects.create(
        target_type=EvalTargetType.SESSION.value,
        trace=None,
        observation_span=None,
        # ``trace_session`` is the unsaved CH-sourced vehicle (no PG row) —
        # write the FK by its id column (db_constraint=False), never the
        # relation. See ``_execute_evaluation_for_session``.
        trace_session_id=str(trace_session.id),
        output_metadata={"error": error_message},
        eval_explanation=f"Error during evaluation: {error_message}",
        results_explanation={"reason": error_message},
        eval_task_id=eval_task_id,
        custom_eval_config=custom_eval_config,
        error=True,
        error_message=f"Error during evaluation: {error_message}",
        output_str="ERROR",
    )


# ── Temporal activities ──


@temporal_activity(
    max_retries=3,
    retry_delay=60,
    time_limit=3600,
    queue="tasks_s",
)
def evaluate_trace_observe(
    trace_id=None,
    custom_eval_config_id=None,
    eval_task_id=None,
    feedback_id=None,
):
    """Per-trace evaluator dispatched by ``process_eval_task`` for row_type=traces.

    Mirrors ``evaluate_observation_span_observe`` but scoped to a Trace:
    look up the trace + eval config, idempotency-check on
    ``(trace_id, target_type='trace', eval_config, eval_task)``, soft-delete
    any prior attempts for the same triple, resolve mapping variables off
    the trace via ``_process_trace_mapping``, run the engine, write a
    target_type='trace' EvalLogger row anchored to the trace's root span.
    """
    if not trace_id or not custom_eval_config_id:
        raise ValueError("trace_id and custom_eval_config_id are required parameters")

    try:
        custom_eval_config = CustomEvalConfig.objects.get(id=custom_eval_config_id)
        trace = Trace.objects.select_related(
            "project", "project__organization", "project__workspace"
        ).get(id=trace_id)
    except CustomEvalConfig.DoesNotExist:
        raise ValueError(
            f"CustomEvalConfig with id {custom_eval_config_id} does not exist."
        ) from None
    except Trace.DoesNotExist:
        raise ValueError(f"Trace with id {trace_id} does not exist.") from None

    # Idempotency: the dispatcher writes one row per (trace, eval_config, task).
    # ``eval_task_id`` already scopes the check to this task's row_type — every
    # row sharing this eval_task_id is target_type='trace' by construction
    # (the dispatcher dispatched this activity because EvalTask.row_type='traces').
    if EvalLogger.objects.filter(
        trace_id=trace_id,
        custom_eval_config_id=custom_eval_config_id,
        eval_task_id=eval_task_id,
    ).exists():
        logger.info(
            f"EvalLogger (target_type=trace) for trace_id {trace_id} and "
            f"custom_eval_config_id {custom_eval_config_id} already exists "
            f"for eval task {eval_task_id}."
        )
        return

    anchor_span = _find_anchor_span(trace)
    if anchor_span is None:
        # Trace has zero spans — can't write a trace EvalLogger row (the
        # check constraint forbids target_type='trace' with NULL span).
        # Record the failure on EvalTask.failed_spans and bail.
        if eval_task_id:
            try:
                with transaction.atomic():
                    eval_task = EvalTask.objects.select_for_update().get(
                        id=eval_task_id
                    )
                    failed = list(eval_task.failed_spans or [])
                    failed.append(
                        {
                            "trace_id": str(trace_id),
                            "custom_eval_config_id": str(custom_eval_config_id),
                            "error": (
                                f"Trace {trace_id} has zero spans — "
                                "cannot anchor a trace-level eval result."
                            ),
                        }
                    )
                    eval_task.failed_spans = failed
                    eval_task.save(update_fields=["failed_spans", "updated_at"])
            except Exception as save_err:
                logger.error(
                    f"Failed to record zero-span trace failure on eval task: {save_err}"
                )
        return False

    try:
        run_params = _process_trace_mapping(
            custom_eval_config.mapping,
            trace,
            custom_eval_config.eval_template.id,
        )
        _execute_evaluation_for_trace(
            trace=trace,
            anchor_span=anchor_span,
            custom_eval_config=custom_eval_config,
            eval_task_id=eval_task_id,
            run_params=run_params,
            feedback_id=feedback_id,
        )
        return True
    except ValueError as e:
        # Expected validation failure; persisted as a failed eval below.
        logger.warning(f"Error during evaluation in evaluate_trace_observe: {e}")
        if eval_task_id:
            try:
                with transaction.atomic():
                    eval_task = EvalTask.objects.select_for_update().get(
                        id=eval_task_id
                    )
                    failed = list(eval_task.failed_spans or [])
                    failed.append(
                        {
                            "trace_id": str(trace_id),
                            "custom_eval_config_id": str(custom_eval_config_id),
                            "error": str(e),
                        }
                    )
                    eval_task.failed_spans = failed
                    eval_task.save(update_fields=["failed_spans", "updated_at"])
            except EvalTask.DoesNotExist:
                # Expected race: the EvalTask was deleted before this async task
                # ran. Nothing to update; downgrade to warning.
                logger.warning(f"EvalTask with id {eval_task_id} does not exist.")
            except Exception as save_err:
                logger.error(
                    f"Error updating failed_spans during trace eval error: {save_err}"
                )
        _create_error_eval_logger_for_trace(
            trace, anchor_span, custom_eval_config, eval_task_id, str(e)
        )
        return False
    except Exception as e:
        logger.exception(f"Exception during evaluation in evaluate_trace_observe: {e}")
        return False


@temporal_activity(
    max_retries=3,
    retry_delay=60,
    time_limit=3600,
    queue="tasks_s",
)
def evaluate_trace_session_observe(
    session_id=None,
    custom_eval_config_id=None,
    eval_task_id=None,
    feedback_id=None,
):
    """Per-session evaluator dispatched by ``process_eval_task`` for row_type=sessions.

    Mirrors ``evaluate_trace_observe`` but scoped to a TraceSession.
    Writes a target_type='session' EvalLogger row with NULL span/trace
    and the session FK populated.
    """
    if not session_id or not custom_eval_config_id:
        raise ValueError("session_id and custom_eval_config_id are required parameters")

    try:
        # select_related the project chain: the session vehicle below borrows
        # the eval CONFIG's project/org/workspace (the eval's own org-scope,
        # used for cost-deduction + workspace-context). The session's PG
        # project FK no longer exists post-flip, so the config's project — not
        # the session's — is the anchor for org-scope, and it's always present.
        custom_eval_config = CustomEvalConfig.objects.select_related(
            "project", "project__organization", "project__workspace"
        ).get(id=custom_eval_config_id)
    except CustomEvalConfig.DoesNotExist:
        raise ValueError(
            f"CustomEvalConfig with id {custom_eval_config_id} does not exist."
        ) from None

    # Resolve the eval TARGET's identity from CH (DESIGN §5 / Slice C), NOT the
    # PG ``TraceSession`` table: a net-new session (first seen post-flip) has no
    # PG row, and a straddler queried by its NEW deterministic id would 404 the
    # old ``.get``. ``resolve_session_fields`` is remap-aware (straddler old|new
    # id → ONE survivor) and returns {external_session_id, first_seen,
    # bookmarked, display_name}. Lazy import keeps the eval.py↔eval_tasks.py
    # cycle (see module-top NOTE) and the CH client warm-up off this module's
    # import path, mirroring the existing ``get_reader`` lazy imports.
    from tracer.services.clickhouse.v2.trace_session_dict_reader import (
        resolve_session_fields,
    )

    _fields = resolve_session_fields([session_id]).get(str(session_id))
    if not _fields:
        # Parity with the old ``.get`` raising DoesNotExist: no live curated
        # session names this id (unknown / tombstoned / wrong project's id that
        # never landed in this island).
        raise ValueError(f"TraceSession with id {session_id} does not exist.")

    # Build an UNSAVED TraceSession vehicle so the unchanged downstream
    # (``_process_session_mapping`` / ``_execute_evaluation_for_session`` /
    # composite / ``build_session_context``) keeps reading session fields by
    # attribute — but every field is now sourced from CH + the PG overlay, not a
    # PG ``trace_session`` row that may not exist:
    #   • name      ← display_name override else external_session_id (DESIGN
    #                 §5.2 COALESCE); feeds ``_SESSION_PUBLIC_FIELDS`` mapping.
    #   • bookmarked← PG overlay (``_SESSION_PUBLIC_FIELDS`` + build_session_ctx).
    #   • created_at← first_seen (DESIGN §5.2: the session's creation IS its
    #                 first observed activity; build_session_context reads it).
    #   • project   ← the eval CONFIG's project (org/workspace org-scope anchor).
    # The instance is NEVER saved; the EvalLogger FK is written by id column
    # (db_constraint=False) so no PG ``trace_session`` row is required.
    trace_session = TraceSession(
        id=session_id,
        name=_fields["display_name"] or _fields["external_session_id"],
        bookmarked=bool(_fields["bookmarked"]),
        created_at=_fields["first_seen"],
        project=custom_eval_config.project,
    )

    # Same idempotency rationale as the trace evaluator: eval_task_id alone
    # scopes the check to this task's row_type (target_type='session'), so a
    # redundant target_type filter would be a tautology.
    if EvalLogger.objects.filter(
        trace_session_id=session_id,
        custom_eval_config_id=custom_eval_config_id,
        eval_task_id=eval_task_id,
    ).exists():
        logger.info(
            f"EvalLogger (target_type=session) for session_id {session_id} "
            f"and custom_eval_config_id {custom_eval_config_id} already "
            f"exists for eval task {eval_task_id}."
        )
        return

    try:
        run_params = _process_session_mapping(
            custom_eval_config.mapping,
            trace_session,
            custom_eval_config.eval_template.id,
        )
        _execute_evaluation_for_session(
            trace_session=trace_session,
            custom_eval_config=custom_eval_config,
            eval_task_id=eval_task_id,
            run_params=run_params,
            feedback_id=feedback_id,
        )
        return True
    except ValueError as e:
        # Expected validation failure; persisted as a failed eval below.
        logger.warning(
            f"Error during evaluation in evaluate_trace_session_observe: {e}"
        )
        if eval_task_id:
            try:
                with transaction.atomic():
                    eval_task = EvalTask.objects.select_for_update().get(
                        id=eval_task_id
                    )
                    failed = list(eval_task.failed_spans or [])
                    failed.append(
                        {
                            "session_id": str(session_id),
                            "custom_eval_config_id": str(custom_eval_config_id),
                            "error": str(e),
                        }
                    )
                    eval_task.failed_spans = failed
                    eval_task.save(update_fields=["failed_spans", "updated_at"])
            except EvalTask.DoesNotExist:
                # Expected race: the EvalTask was deleted before this async task
                # ran. Nothing to update; downgrade to warning.
                logger.warning(f"EvalTask with id {eval_task_id} does not exist.")
            except Exception as save_err:
                logger.error(
                    f"Error updating failed_spans during session eval error: {save_err}"
                )
        _create_error_eval_logger_for_session(
            trace_session, custom_eval_config, eval_task_id, str(e)
        )
        return False
    except Exception as e:
        logger.exception(
            f"Exception during evaluation in evaluate_trace_session_observe: {e}"
        )
        return False
