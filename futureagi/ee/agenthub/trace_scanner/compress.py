"""
Trace compression for the scanner — kevinify, structural prefilter, compress_v2.

Ported from experiments/trace_scanner/{scanner_v3.py, compress_trace.py}.
"""

import json
import re
import statistics

import nltk
from nltk.corpus import stopwords as _nltk_stopwords
from nltk.stem import PorterStemmer


def _get_span_kind(attrs: dict) -> str:
    """Span kind can live under any vendor prefix (openinference.span.kind,
    fi.span.kind, etc.). Return the first match so the scanner isn't coupled
    to a single SDK's attribute namespace."""
    if not attrs:
        return ""
    for key, value in attrs.items():
        if key.endswith(".span.kind") or key == "span.kind":
            if value:
                return str(value)
    return ""

# ---------------------------------------------------------------------------
# KEVINIFY — "Why waste time say lot word when few word do trick"
# ---------------------------------------------------------------------------

_STEMMER = PorterStemmer()
_NLTK_STOPS = set(_nltk_stopwords.words("english"))

_EXTRA_STOPS = {
    "please",
    "note",
    "however",
    "therefore",
    "thus",
    "hence",
    "accordingly",
    "additionally",
    "furthermore",
    "moreover",
    "specifically",
    "particularly",
    "essentially",
    "basically",
    "actually",
    "currently",
    "previously",
    "following",
    "regarding",
    "concerning",
    "including",
    "excluding",
    "using",
    "used",
    "also",
    "would",
    "could",
    "should",
    "shall",
    "maybe",
    "perhaps",
    "likely",
    "unlikely",
    "certainly",
    "definitely",
    "obviously",
    "clearly",
    "simply",
    "really",
    "quite",
    "rather",
    "role",
    "assistant",
    "content",
    "type",
    "text",
    "message",
    "messages",
    "null",
    "none",
    "true",
    "false",
    "undefined",
}

_ALL_STOPS = _NLTK_STOPS | _EXTRA_STOPS

_FILLER_RE = re.compile(
    r"(?:based on|in order to|as well as|due to|in terms of|with respect to"
    r"|it should be noted|it is important|it is worth|note that|please note"
    r"|keep in mind|the following|as follows|in this case|at this point"
    r"|as a result|on the other hand|in addition to|for example"
    r"|i will now|let me|i need to|i should|i'll)",
    re.IGNORECASE,
)

_JSON_NOISE_RE = re.compile(r'[{}\[\]"\\]|\\n|\\t|\\r')


def kevinify(text, max_len=2000):
    """Strip grammar fluff, keep semantic content. Few word do trick."""
    if not text:
        return ""
    text = str(text).strip()

    text = _JSON_NOISE_RE.sub(" ", text)
    text = _FILLER_RE.sub(" ", text)

    try:
        words = nltk.word_tokenize(text)
    except Exception:
        words = text.split()

    kept = []
    for w in words:
        clean = w.strip(".,;:!?()-_'\"")
        if not clean or len(clean) <= 1:
            continue
        if clean.lower() in _ALL_STOPS:
            continue
        kept.append(clean)

    result = " ".join(kept)
    result = re.sub(r"\s+", " ", result).strip()

    if len(result) > max_len:
        # Cut at a word boundary and append NOTHING. Any marker like "..."
        # gets interpreted by the scanner LLM as a truncated agent response
        # no matter how strongly the prompt says otherwise — Haiku's training
        # prior on "..." = truncation is too strong to override.
        result = result[:max_len].rsplit(" ", 1)[0]
    return result


# ---------------------------------------------------------------------------
# VERBATIM RECOVERY — Match kevinified LLM excerpts back to raw text
# ---------------------------------------------------------------------------

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _split_sentences(text):
    """Split text into sentence-like chunks."""
    if not text:
        return []
    parts = _SENTENCE_RE.split(text.strip())
    result = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > 200:
            sub = re.split(r"[;]\s*", p)
            result.extend(s.strip() for s in sub if s.strip())
        else:
            result.append(p)
    return result


def recover_verbatim(kevinified_excerpt, raw_text, min_overlap=0.3):
    """Match a kevinified LLM excerpt back to the raw text."""
    if not kevinified_excerpt or not raw_text:
        return kevinified_excerpt or ""

    raw_text = str(raw_text)
    sentences = _split_sentences(raw_text)
    if not sentences:
        return kevinified_excerpt

    excerpt_words = {w for w in kevinified_excerpt.lower().split() if len(w) > 2}
    if not excerpt_words:
        return kevinified_excerpt

    best_score = 0
    best_sentence = ""

    for sent in sentences:
        sent_kev = kevinify(sent, max_len=500)
        sent_words = {w for w in sent_kev.lower().split() if len(w) > 2}
        if not sent_words:
            continue
        overlap = len(excerpt_words & sent_words)
        score = overlap / len(excerpt_words)
        if score > best_score:
            best_score = score
            best_sentence = sent

    if best_score >= min_overlap:
        return best_sentence.strip()

    # Fallback: sliding window matching
    raw_words = raw_text.split()
    window_size = max(len(kevinified_excerpt.split()) * 3, 20)
    for i in range(0, len(raw_words) - window_size + 1, 5):
        window = " ".join(raw_words[i : i + window_size])
        window_kev = kevinify(window, max_len=500)
        window_words = {w for w in window_kev.lower().split() if len(w) > 2}
        if not window_words:
            continue
        overlap = len(excerpt_words & window_words)
        score = overlap / len(excerpt_words)
        if score > best_score:
            best_score = score
            best_sentence = window

    if best_score >= min_overlap:
        return best_sentence.strip()

    return kevinified_excerpt


def recover_key_moments(key_moments, raw_spans_text):
    """Post-process key_moments: recover verbatim text for user_request/agent_response."""
    if not key_moments:
        return key_moments

    result = dict(key_moments)

    if result.get("user_request"):
        result["user_request_raw"] = recover_verbatim(
            result["user_request"],
            raw_spans_text.get("all_inputs", ""),
        )

    if result.get("agent_response"):
        result["agent_response_raw"] = recover_verbatim(
            result["agent_response"],
            raw_spans_text.get("all_outputs", ""),
        )

    return result


# ---------------------------------------------------------------------------
# TASK HINTS — Cheap keyword signals from root input
# ---------------------------------------------------------------------------

_TASK_PATTERNS = {
    "needs_tool": re.compile(
        r"\b(search|find|look\s*up|check|fetch|get|retrieve|download|browse|navigate|open)\b",
        re.IGNORECASE,
    ),
    "has_format_constraint": re.compile(
        r"\b(format\s+as|output\s+in|respond\s+with|return\s+as|json|csv|markdown|xml|table|list\s+of|bullet)\b",
        re.IGNORECASE,
    ),
    "quantitative_answer": re.compile(
        r"\b(how\s+many|count|number\s+of|total|sum|average|percentage|ratio)\b",
        re.IGNORECASE,
    ),
    "specific_value": re.compile(
        r"\b(what\s+is|what\'s|what\s+was|when\s+did|where\s+is|who\s+is|which)\b",
        re.IGNORECASE,
    ),
}


def extract_task_hints(root_input):
    """Extract keyword-based task category hints from root span input."""
    if not root_input:
        return []
    text = str(root_input)
    return [hint for hint, pattern in _TASK_PATTERNS.items() if pattern.search(text)]


# ---------------------------------------------------------------------------
# HELPERS — span tree utilities
# ---------------------------------------------------------------------------


def _truncate(text, max_len=100):
    if not text:
        return ""
    text = str(text).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _parse_duration_seconds(duration_str):
    """Parse ISO 8601 duration like PT1M24.635189S to seconds."""
    if not duration_str:
        return 0
    try:
        s = duration_str.replace("PT", "")
        total = 0
        if "H" in s:
            h, s = s.split("H")
            total += float(h) * 3600
        if "M" in s:
            m, s = s.split("M")
            total += float(m) * 60
        if "S" in s:
            s = s.replace("S", "")
            if s:
                total += float(s)
        return round(total, 2)
    except Exception:
        return 0


def flatten_spans(span, depth=0, result=None):
    """Recursively flatten nested span tree into a list with depth info."""
    if result is None:
        result = []
    result.append((span, depth))
    for child in span.get("child_spans", []):
        flatten_spans(child, depth + 1, result)
    return result


# ---------------------------------------------------------------------------
# STRUCTURAL PRE-FILTER — Rule-based anomaly detection (free, <1ms)
# ---------------------------------------------------------------------------


def _tool_names_from_definitions(attrs: dict) -> set[str]:
    """Extract tool NAMES from the function-calling tool definitions on a span
    (``llm.tools`` / ``gen_ai.tool.definitions``). Tolerates a JSON string or a
    list, and OpenAI-style (``{type, function:{name}}``) or flat (``{name}``)
    schemas. These are the tools the agent had AVAILABLE, regardless of which it
    actually invoked."""
    names: set[str] = set()
    for key in ("llm.tools", "gen_ai.tool.definitions"):
        raw = attrs.get(key)
        if not raw:
            continue
        try:
            defs = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        if not isinstance(defs, list):
            continue
        for d in defs:
            if isinstance(d, str):
                names.add(d)
            elif isinstance(d, dict):
                fn = d.get("function") if isinstance(d.get("function"), dict) else {}
                nm = fn.get("name") or d.get("name")
                if nm:
                    names.add(str(nm))
    return names


def structural_prefilter(trace_data):
    """Extract rule-based signals from the trace."""
    flat_spans = []
    for top_span in trace_data["spans"]:
        flat_spans.extend(flatten_spans(top_span))

    signals = {
        "error_spans": [],
        "retry_spans": [],
        "duration_outliers": [],
        "tool_failures": [],
        "empty_output": [],
        "token_anomalies": [],
    }

    spans_flat = []
    # Tools the agent had AVAILABLE — parsed from the function-calling tool
    # definitions sent to the LLM (standard telemetry: llm.tools /
    # gen_ai.tool.definitions). This is broader than the tools actually invoked
    # as spans; without it "available" collapses to "called" and the missing-
    # tool signal can never fire. No bespoke attribute required.
    declared_tools: set = set()
    for span, depth in flat_spans:
        attrs = span.get("span_attributes", {})
        declared_tools.update(_tool_names_from_definitions(attrs))
        duration = _parse_duration_seconds(span.get("duration"))
        status = span.get("status_code", "Unset")
        kind = _get_span_kind(attrs)
        has_input = bool(attrs.get("input.value", ""))
        has_output = bool(attrs.get("output.value", ""))
        prompt_tok = int(attrs.get("llm.token_count.prompt", 0) or 0)
        completion_tok = int(attrs.get("llm.token_count.completion", 0) or 0)

        spans_flat.append(
            {
                "id": span["span_id"],
                "name": span["span_name"],
                "depth": depth,
                "kind": kind,
                "duration": duration,
                "status": status,
                "has_input": has_input,
                "has_output": has_output,
                "prompt_tok": prompt_tok,
                "completion_tok": completion_tok,
            }
        )

    for s in spans_flat:
        if s["status"] == "Error":
            signals["error_spans"].append(s["id"])

    for i in range(1, len(spans_flat)):
        if (
            spans_flat[i]["name"] == spans_flat[i - 1]["name"]
            and spans_flat[i]["depth"] == spans_flat[i - 1]["depth"]
        ):
            signals["retry_spans"].append(s["id"])

    by_depth = {}
    for s in spans_flat:
        by_depth.setdefault(s["depth"], []).append(s)
    for depth, siblings in by_depth.items():
        durations = [s["duration"] for s in siblings if s["duration"] > 0]
        if len(durations) >= 3:
            mean = statistics.mean(durations)
            stdev = statistics.stdev(durations)
            if stdev > 0:
                for s in siblings:
                    if s["duration"] > 0 and abs(s["duration"] - mean) > 2 * stdev:
                        signals["duration_outliers"].append(s["id"])

    tool_names = {"Tool", "TOOL"}
    for s in spans_flat:
        is_tool = s["kind"] in tool_names or "Tool" in s["name"]
        if is_tool and s["status"] == "Error":
            signals["tool_failures"].append(s["id"])

    for s in spans_flat:
        if s["has_input"] and not s["has_output"] and s["kind"]:
            signals["empty_output"].append(s["id"])

    for s in spans_flat:
        if s["prompt_tok"] > 0 and s["completion_tok"] > 0:
            ratio = s["completion_tok"] / s["prompt_tok"]
            if ratio > 3 or ratio < 0.05:
                signals["token_anomalies"].append(s["id"])

    # Absence detection
    tool_spans = [
        s for s in spans_flat if s["kind"] in {"Tool", "TOOL"} or "Tool" in s["name"]
    ]
    llm_spans = [s for s in spans_flat if s["kind"] in {"LLM", "llm"}]
    unique_tools = {s["name"] for s in tool_spans}

    if not tool_spans and llm_spans:
        signals["no_tool_calls"] = True
    retriever_spans = [s for s in spans_flat if s["kind"] in {"Retriever", "RETRIEVER"}]
    if llm_spans and not tool_spans and not retriever_spans:
        signals["llm_only_trace"] = True

    anomalous_ids = set()
    for key, ids in signals.items():
        if isinstance(ids, list):
            anomalous_ids.update(ids)

    signal_summary = {}
    for k, v in signals.items():
        if isinstance(v, list) and v:
            signal_summary[k] = len(v)
        elif isinstance(v, bool) and v:
            signal_summary[k] = True

    return {
        "is_clean": len(anomalous_ids) == 0
        and not signals.get("no_tool_calls")
        and not signals.get("llm_only_trace"),
        "anomalous_span_ids": anomalous_ids,
        "signal_summary": signal_summary,
        "total_signals": len(anomalous_ids),
        "available_tools": list(unique_tools | declared_tools),
    }


def structural_prefilter_with_ids(trace_data):
    """Extended prefilter that also returns per-signal ID sets for flagging."""
    result = structural_prefilter(trace_data)

    flat_spans = []
    for top_span in trace_data["spans"]:
        flat_spans.extend(flatten_spans(top_span))

    error_ids = set()
    retry_ids = set()
    duration_ids = set()
    tool_fail_ids = set()

    spans_flat = []
    for span, depth in flat_spans:
        attrs = span.get("span_attributes", {})
        spans_flat.append(
            {
                "id": span["span_id"],
                "name": span["span_name"],
                "depth": depth,
                "kind": _get_span_kind(attrs),
                "duration": _parse_duration_seconds(span.get("duration")),
                "status": span.get("status_code", "Unset"),
            }
        )

    for s in spans_flat:
        if s["status"] == "Error":
            error_ids.add(s["id"])
    for i in range(1, len(spans_flat)):
        if (
            spans_flat[i]["name"] == spans_flat[i - 1]["name"]
            and spans_flat[i]["depth"] == spans_flat[i - 1]["depth"]
        ):
            retry_ids.add(spans_flat[i]["id"])
    for s in spans_flat:
        is_tool = s["kind"] in {"Tool", "TOOL"} or "Tool" in s["name"]
        if is_tool and s["status"] == "Error":
            tool_fail_ids.add(s["id"])

    by_depth = {}
    for s in spans_flat:
        by_depth.setdefault(s["depth"], []).append(s)
    for depth, siblings in by_depth.items():
        durations = [s["duration"] for s in siblings if s["duration"] > 0]
        if len(durations) >= 3:
            mean = statistics.mean(durations)
            stdev = statistics.stdev(durations)
            if stdev > 0:
                for s in siblings:
                    if s["duration"] > 0 and abs(s["duration"] - mean) > 2 * stdev:
                        duration_ids.add(s["id"])

    result["_error_ids"] = error_ids
    result["_retry_ids"] = retry_ids
    result["_duration_ids"] = duration_ids
    result["_tool_fail_ids"] = tool_fail_ids
    return result


# ---------------------------------------------------------------------------
# COMPRESS V2 — Adaptive budget, kevinified I/O, flow outline
# ---------------------------------------------------------------------------


def build_flow_outline(trace_data):
    """Compact tree outline showing agent execution flow with path numbering."""
    parts = []

    def _walk(span, path_prefix):
        name = span.get("span_name", "?")
        kind = _get_span_kind(span.get("span_attributes", {}))
        status = span.get("status_code", "Unset")

        label = f"{path_prefix}:{name}"
        if kind:
            label += f"({kind})"
        if status == "Error":
            label += "[ERR]"

        parts.append(label)

        children = span.get("child_spans", [])
        for idx, child in enumerate(children, start=1):
            _walk(child, f"{path_prefix}.{idx}")

    for idx, root_span in enumerate(trace_data.get("spans", []), start=1):
        _walk(root_span, str(idx))

    return " > ".join(parts)


def compress_v2(trace_data, prefilter_result):
    """
    Smart compression — kevinify all spans with adaptive token budget.
    """
    anomalous_ids = prefilter_result["anomalous_span_ids"]

    all_flat = []
    for top_span in trace_data["spans"]:
        all_flat.extend(flatten_spans(top_span))

    # Extract task (root input) and result (final output)
    root_input = ""
    final_output = ""
    if all_flat:
        root_attrs = all_flat[0][0].get("span_attributes", {})
        root_input = root_attrs.get("input.value", "")
        for span, depth in reversed(all_flat):
            out = span.get("span_attributes", {}).get("output.value", "")
            if out:
                final_output = out
                break
        if not final_output:
            final_output = root_attrs.get("output.value", "")

    # Adaptive budget: ~3000 chars total, distributed by importance
    TOTAL_IO_BUDGET = 3000
    weights = []
    span_meta = []
    for span, depth in all_flat:
        attrs = span.get("span_attributes", {})
        sid = span["span_id"]
        kind = _get_span_kind(attrs)
        is_flagged = sid in anomalous_ids
        is_decision = kind in {"LLM", "llm", "Tool", "TOOL", "Retriever", "RETRIEVER"}

        w = 3 if is_flagged else (2 if is_decision else 1)
        weights.append(w)
        span_meta.append((span, depth, is_flagged, is_decision, kind))

    total_weight = sum(weights) or 1
    budgets = [max(50, int(TOTAL_IO_BUDGET * w / total_weight)) for w in weights]

    spans = []
    for i, (span, depth, is_flagged, is_decision, kind) in enumerate(span_meta):
        attrs = span.get("span_attributes", {})
        sid = span["span_id"]
        status = span.get("status_code", "Unset")
        duration = _parse_duration_seconds(span.get("duration"))
        io_budget = budgets[i]

        inp = kevinify(attrs.get("input.value", ""), io_budget)
        out = kevinify(attrs.get("output.value", ""), io_budget)

        has_content = inp or out or status != "Unset" or is_flagged
        if not has_content:
            continue

        entry = {"n": span["span_name"], "d": depth}
        if kind:
            entry["k"] = kind
        if status != "Unset":
            entry["s"] = status
        if duration and duration > 0.1:
            entry["t"] = duration
        if inp:
            entry["in"] = inp
        if out:
            entry["out"] = out

        if is_flagged:
            flags = []
            if sid in prefilter_result.get("_error_ids", set()):
                flags.append("ERR")
            if sid in prefilter_result.get("_retry_ids", set()):
                flags.append("RETRY")
            if sid in prefilter_result.get("_duration_ids", set()):
                flags.append("SLOW")
            if sid in prefilter_result.get("_tool_fail_ids", set()):
                flags.append("TOOL_FAIL")
            if not flags:
                flags.append("FLAGGED")
            entry["f"] = ",".join(flags)

        pt = int(attrs.get("llm.token_count.prompt", 0) or 0)
        ct = int(attrs.get("llm.token_count.completion", 0) or 0)
        if pt:
            entry["pt"] = pt
        if ct:
            entry["ct"] = ct

        spans.append(entry)

    trace_label = trace_data.get("_short_label", trace_data["trace_id"])
    flow_outline = build_flow_outline(trace_data)

    result = {
        "tid": trace_label,
        "task": kevinify(root_input, 300),
        "result": kevinify(final_output, 300),
        "flow": flow_outline,
        "signals": prefilter_result["signal_summary"],
        "spans": spans,
    }

    available_tools = prefilter_result.get("available_tools", [])
    if available_tools:
        result["tools_available"] = available_tools

    task_hints = extract_task_hints(root_input)
    if task_hints:
        result["task_hints"] = task_hints

    return result


def extract_programmatic_metadata(trace_data, prefilter_result):
    """Extract metadata that doesn't need LLM — pure trace parsing."""
    flat_spans = []
    for top_span in trace_data["spans"]:
        flat_spans.extend(flatten_spans(top_span))

    tools_called = []
    for span, depth in flat_spans:
        attrs = span.get("span_attributes", {})
        kind = _get_span_kind(attrs)
        if kind in {"Tool", "TOOL"} or "Tool" in span["span_name"]:
            tools_called.append(
                {"name": span["span_name"], "status": span.get("status_code", "Unset")}
            )

    llm_spans = [
        s
        for s, d in flat_spans
        if _get_span_kind(s.get("span_attributes", {})) in {"LLM", "llm"}
    ]
    turn_count = len(llm_spans)

    raw_user_request = ""
    raw_agent_response = ""
    if flat_spans:
        root_attrs = flat_spans[0][0].get("span_attributes", {})
        raw_user_request = str(root_attrs.get("input.value", ""))[:500]
        for span, depth in reversed(flat_spans):
            out = span.get("span_attributes", {}).get("output.value", "")
            if out:
                raw_agent_response = str(out)[:500]
                break
        if not raw_agent_response:
            raw_agent_response = str(root_attrs.get("output.value", ""))[:500]

    all_inputs = []
    all_outputs = []
    for span, depth in flat_spans:
        attrs = span.get("span_attributes", {})
        inp = str(attrs.get("input.value", ""))
        out = str(attrs.get("output.value", ""))
        if inp:
            all_inputs.append(inp[:1000])
        if out:
            all_outputs.append(out[:1000])

    return {
        "tools_called": tools_called,
        "tools_available": prefilter_result.get("available_tools", []),
        "turn_count": turn_count,
        "raw_user_request": raw_user_request,
        "raw_agent_response": raw_agent_response,
        "raw_spans_text": {
            "all_inputs": "\n".join(all_inputs),
            "all_outputs": "\n".join(all_outputs),
        },
    }


# ---------------------------------------------------------------------------
# KEY-MOMENT SPAN ATTRIBUTION — deterministic, no LLM
# ---------------------------------------------------------------------------
#
# The scanner LLM emits flat verbatim quotes. We attribute each quote back to
# the real span it came from (by word-overlap) and read role/status off THAT
# span — so the breadcrumb's structure is grounded in actual span data, never
# model-guessed. Keeps the scanner model-agnostic (the LLM call is untouched).

# Status strings that mean "fine" — anything else is treated as a failure.
_OK_STATUS = {"", "ok", "unset", "status_code_ok", "ok ", "none"}


def _role_from_kind(kind: str, is_root_input: bool) -> str:
    """Map a span kind to a breadcrumb role label."""
    if is_root_input:
        return "User"
    k = (kind or "").lower()
    if "tool" in k:
        return "Tool"
    if "retriev" in k:
        return "Retrieval"
    if k in {"llm", "agent", "chain"}:
        return "Agent"
    return "Step"


def attribute_key_moments(quotes, trace_data):
    """For each key-moment quote, find its source span by word overlap and
    return ``{role, span, status, is_failure}`` per quote (same order/length).

    Deterministic: role/status come from the matched span, never the LLM.
    Returns empty fields for a quote that doesn't confidently match a span.
    """
    flat = []
    for top_span in (trace_data or {}).get("spans", []) or []:
        for span, _depth in flatten_spans(top_span):
            attrs = span.get("span_attributes", {}) or {}
            flat.append(
                {
                    "name": span.get("span_name", "?"),
                    "kind": _get_span_kind(attrs),
                    "status": span.get("status_code", "Unset"),
                    "input": str(attrs.get("input.value", "")),
                    "output": str(attrs.get("output.value", "")),
                }
            )
    if flat:
        flat[0]["is_root"] = True

    out = []
    for quote in quotes:
        q_words = {w for w in re.findall(r"\w+", (quote or "").lower()) if len(w) > 2}
        if not q_words:
            out.append({"role": "", "span": "", "status": "", "is_failure": False})
            continue
        best, best_score, from_input = None, 0.0, False
        for sp in flat:
            for is_inp, text in ((True, sp["input"]), (False, sp["output"])):
                if not text:
                    continue
                t_words = set(re.findall(r"\w+", text.lower()))
                if not t_words:
                    continue
                score = len(q_words & t_words) / len(q_words)
                if score > best_score:
                    best, best_score, from_input = sp, score, is_inp
        if best and best_score >= 0.5:
            is_failure = str(best["status"]).strip().lower() not in _OK_STATUS
            out.append(
                {
                    "role": _role_from_kind(
                        best["kind"], best.get("is_root", False) and from_input
                    ),
                    "span": best["name"],
                    "status": "fail" if is_failure else "ok",
                    "is_failure": is_failure,
                }
            )
        else:
            out.append({"role": "", "span": "", "status": "", "is_failure": False})
    return out
