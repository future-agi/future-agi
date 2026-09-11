"""
Scanner trace payload — structural prefilter, full-span-tree payload, and the
verbatim-recovery helpers behind key-moment breadcrumbs.

No content selection happens here. The payload carries every span raw; the
model identifies the user request and the final response itself.
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

    # No match: return nothing rather than the excerpt we were asked to find.
    # Handing back the model's own paraphrase means the caller stores it in a
    # field named `verbatim` and the UI renders it as a quote from the trace —
    # so a breadcrumb points an engineer at words nobody ever said. Measured on
    # a 2,107-trace corpus, 161 of 278 breadcrumbs (58%) were this fallback, and
    # 31% quoted text that appears nowhere in their own trace. An absent quote is
    # honest; an invented one costs more than it gives.
    return ""


# ---------------------------------------------------------------------------
# HELPERS — span tree utilities
# ---------------------------------------------------------------------------


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
            # `s` here was the leftover loop variable from the error-span loop above,
            # so every detected retry recorded the LAST span's id. The appends still
            # happened, which is why the signal looked alive: on one real trace it
            # fired 249 times. But `anomalous_ids` is a set, so 249 copies of one id
            # collapse to a single entry and the other 248 retry spans are never
            # flagged. Measured over 2,107 traces: 566 of the 844 traces carrying a
            # retry (67%) had the signal collapse this way.
            signals["retry_spans"].append(spans_flat[i]["id"])

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
# FLOW OUTLINE — compact structural view of the whole tree
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


# Haystack caps for key-moment verbatim recovery. The model quotes from FULL
# raw spans, so a head-only cap loses every quote of how a long output ENDS —
# and recovery cost grows with the haystack, so the cap cannot simply be
# removed. Head + tail covers where quotes actually land (openings and
# endings) while keeping recovery time bounded.
_RECOVERY_HEAD_CHARS = 3_000
_RECOVERY_TAIL_CHARS = 1_000


def _recovery_slice(text: str) -> str:
    if len(text) <= _RECOVERY_HEAD_CHARS + _RECOVERY_TAIL_CHARS:
        return text
    return f"{text[:_RECOVERY_HEAD_CHARS]}\n{text[-_RECOVERY_TAIL_CHARS:]}"


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

    all_inputs = []
    all_outputs = []
    for span, depth in flat_spans:
        attrs = span.get("span_attributes", {})
        inp = str(attrs.get("input.value", ""))
        out = str(attrs.get("output.value", ""))
        if inp:
            all_inputs.append(_recovery_slice(inp))
        if out:
            all_outputs.append(_recovery_slice(out))

    return {
        "tools_called": tools_called,
        "tools_available": prefilter_result.get("available_tools", []),
        "turn_count": turn_count,
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


# ---------------------------------------------------------------------------
# TRACE PAYLOAD — every span, raw
# ---------------------------------------------------------------------------
# No selection. The payload used to pre-pick a `task` and a `result` span,
# recover delivered answers, and demote session-log roots — every one of those
# was a heuristic guessing which text was "the answer", and 8 of 18 confirmed
# false positives on the audited corpus were the model correctly judging text
# a heuristic had mislabelled as the agent's response. The model now receives
# the whole tree and identifies the request and the final response itself.


# Shared so the scanner can recognise our own truncation and never report it as
# an agent that stopped early. It should now fire only on a pathological trace.
SCANNER_TRUNCATION_MARK = "⟨trace truncated by the scanner"

# Per-field runaway guard, not a budget: applied to each span input/output
# individually, so no field is ever cut because of how many siblings it has.
# The largest trace field observed sits far below this while the ceiling stays
# well inside the model's context.
FIELD_RUNAWAY_BUDGET = 2_000_000

_ENVELOPE_HINTS = ("choices", "candidates", "generations", "usage", "object", "created")


def _v3_plain(text, max_len):
    """Truncation that preserves line structure. Grammar and negations preserved.

    Collapsing every run of whitespace also collapses newlines, and line breaks
    are the only evidence of output structure the model ever receives. That made
    two things impossible: the taxonomy asks it to judge formatting, and V8 asks
    it to quote verbatim — neither survives text whose line breaks were deleted
    on the way in.

    Measured on a 2,107-trace corpus: claims that fields had been "merged onto
    one line", against outputs that were correctly formatted, were the single
    largest class of false positive. The model was right about what it was
    shown; it was shown the wrong thing.

    Horizontal runs still collapse. The goal is faithful structure, not faithful
    indentation.
    """
    if not text:
        return ""
    text = re.sub(r"[^\S\n]+", " ", str(text))  # spaces/tabs, never newlines
    text = re.sub(r" *\n *", "\n", text)  # no trailing space around breaks
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= max_len:
        return text
    # The whole budget is usable: the reserved character existed for a one-char
    # ellipsis that no longer exists.
    cut = text[:max_len]
    # Cut on a line boundary when one is near the limit, so the tail of a
    # truncated block doesn't itself read as a run-together line.
    nl = cut.rfind("\n")
    if nl > max_len * 0.6:
        cut = cut[:nl]
    # Say who did the truncating. An ellipsis is ambiguous: at the end of an
    # agent response it reads as the agent stopping mid-sentence, which is a
    # reportable failure, and the model has no way to tell that apart from our
    # own budget running out. Name it explicitly so a cut of ours can never be
    # mistaken for an incomplete answer of theirs.
    return f"{cut}{SCANNER_TRUNCATION_MARK}, {len(text) - len(cut)} chars omitted⟩"


def _v3_loads(v):
    if isinstance(v, (dict, list)):
        return v
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s or s[0] not in "[{":
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _is_nontextual(d):
    """True when a response legitimately carries no text: embeddings, rerank
    scores, or a bare numeric payload. These must not be reported as empty."""
    if not isinstance(d, dict):
        return False
    for k in ("data", "embedding", "embeddings", "predictions", "results", "scores"):
        v = d.get(k)
        if isinstance(v, list) and v:
            head = v[0]
            if isinstance(head, (int, float)):
                return True
            if isinstance(head, dict) and any(
                isinstance(head.get(kk), list) and head.get(kk)
                and isinstance(head[kk][0], (int, float))
                for kk in ("embedding", "values", "vector", "scores")
            ):
                return True
    if str(d.get("object", "")).lower() in ("list", "embedding"):
        return True
    return False


def unwrap_output(raw):
    """Pull the assistant's actual text out of a provider response envelope.

    Handles OpenAI (choices[].message.content), Gemini
    (candidates[].content.parts[].text), LangChain (generations[][].text),
    Anthropic (content[].text). Returns the raw string unchanged when it is
    already plain text or an unrecognised shape — never worse than the input.
    """
    d = _v3_loads(raw)
    if d is None:
        return raw or ""

    def _txt(x):
        if isinstance(x, str):
            return x
        if isinstance(x, list):
            return " ".join(_txt(i) for i in x if _txt(i))
        if isinstance(x, dict):
            for k in ("text", "content", "output_text"):
                if isinstance(x.get(k), str) and x[k].strip():
                    return x[k]
            # Structured-output agents answer ENTIRELY via function_call.arguments,
            # with content=null. Without this the whole response reads as empty and
            # the completion dimension fires. 11 corpus roots are function-call-only.
            fc = x.get("function_call") or x.get("functionCall")
            if isinstance(fc, dict) and str(fc.get("arguments") or "").strip():
                return f"[function_call {fc.get('name','')}] {fc['arguments']}"
            tcs = x.get("tool_calls")
            if isinstance(tcs, list) and tcs:
                got = []
                for tc in tcs[:4]:
                    # `(tc or {})` guarded None and empty but not a str, and some
                    # producers emit tool_calls as a list of strings. Every other
                    # branch in this function type-checks before descending; this one
                    # did not, and it raised rather than degrading. The scanner fails
                    # open, so the trace came back has_issues=False and was
                    # indistinguishable from a clean one.
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function")
                    if not isinstance(fn, dict):
                        continue
                    if str(fn.get("arguments") or "").strip():
                        got.append(f"[tool_call {fn.get('name','')}] {fn['arguments']}")
                if got:
                    return "\n".join(got)
            if isinstance(x.get("content"), (list, dict)):
                return _txt(x["content"])
            if isinstance(x.get("parts"), list):
                return _txt(x["parts"])
            if isinstance(x.get("message"), dict):
                return _txt(x["message"])
        return ""

    if isinstance(d, dict):
        for key in ("choices", "candidates", "generations", "content", "messages"):
            v = d.get(key)
            if isinstance(v, list) and v:
                t = _txt(v)
                if t.strip():
                    return t
        t = _txt(d)
        if t.strip():
            return t
        # Non-textual responses are NOT empty. An embeddings call returns a vector
        # and a rerank returns scores; both legitimately have no text. Measured:
        # 4 of 10 sampled v8 false positives were `completion` firing on
        # "[empty model output]" for embedding responses and voice session roots.
        if _is_nontextual(d):
            return "[non-textual response: embedding/scores returned as expected]"
        # recognised envelope but no text found (e.g. empty generation) — say so
        if any(h in d for h in _ENVELOPE_HINTS):
            err = d.get("error") or (d.get("response_metadata") or {}).get("error")
            return f"[empty model output]{' error=' + _v3_plain(json.dumps(err), 300) if err else ''}"
    elif isinstance(d, list):
        t = _txt(d)
        if t.strip():
            return t
    return raw or ""


def build_trace_payload(trace_data, prefilter_result, retry_ids=()):
    """The scanner model's input: every span, raw, in execution order.

    Nothing here decides what the model gets to see. Structure (id, name,
    depth, kind, status, prefilter flags) is annotated; content is passed
    through verbatim apart from whitespace normalisation and the per-field
    runaway guard. The model identifies the user request and the final
    response itself — the pre-selection this replaces mislabelled working
    agents' answers and produced the largest audited false-positive class.
    """
    all_flat = []
    for top in trace_data.get("spans", []):
        all_flat.extend(flatten_spans(top))

    # `retry_ids` is an explicit parameter, but the only production caller
    # (scanner.py) passes just (trace, prefilter), so it defaulted to () and the
    # correctly-computed set that `structural_prefilter_with_ids` stores under
    # `_retry_ids` never reached the flagged tier. Falling back to the dict
    # fixes every caller rather than one call site.
    flagged = set(prefilter_result.get("anomalous_span_ids") or []) | set(
        retry_ids or prefilter_result.get("_retry_ids") or ()
    )

    spans = []
    for span, depth in all_flat:
        a = span.get("span_attributes") or {}
        sid = span.get("span_id")
        is_flagged = sid in flagged
        inp = _v3_plain(a.get("input.value", ""), FIELD_RUNAWAY_BUDGET)
        out = _v3_plain(a.get("output.value", ""), FIELD_RUNAWAY_BUDGET)
        # A span with no content, no status and no flag adds nothing the flow
        # outline doesn't already carry.
        if not (inp or out or span.get("status_code") not in (None, "Unset") or is_flagged):
            continue
        entry = {
            "id": sid,
            "name": span.get("span_name"),
            "depth": depth,
            "status": span.get("status_code", "Unset"),
            "in": inp,
            "out": out,
        }
        kind = _get_span_kind(a)
        if kind:
            entry["kind"] = kind
        if is_flagged:
            entry["flagged"] = True
        spans.append(entry)

    res = {
        "tid": trace_data.get("_short_label", trace_data.get("trace_id")),
        "flow": build_flow_outline(trace_data),
        "signals": prefilter_result.get("signal_summary"),
        "spans": spans,
    }
    tools = prefilter_result.get("available_tools") or []
    if tools:
        res["tools_available"] = tools
    return res
