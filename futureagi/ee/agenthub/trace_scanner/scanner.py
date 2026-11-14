"""
Trace Scanner V7.2 — Lightweight AI trace error scanner.

Gemini 3.6 Flash (Vertex) via agentcc gateway, 1 trace per LLM call.
Outputs: issues[] + key_moments[] + meta{}
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import structlog

from ee.agenthub.trace_scanner.compress import (
    SCANNER_TRUNCATION_MARK,
    _OK_STATUS,
    _get_span_kind,
    _tool_names_from_definitions,
    attribute_key_moments,
    build_trace_payload,
    extract_programmatic_metadata,
    flatten_spans,
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
    build_prompt_v8_with_view,
    span_header,
    tools_available_text,
)
from ee.agenthub.untrusted_content import neutralize
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
    return _trigrams_are_quoted(ev, haystack_norm)


def _trigrams_are_quoted(ev: str, haystack_norm: str) -> bool:
    """Enough of a normalised quote's word-trigrams appear in the haystack."""
    words = ev.split()
    if len(words) < 3:
        return False
    grams = [" ".join(words[i : i + 3]) for i in range(len(words) - 2)]
    hits = sum(1 for g in grams if g in haystack_norm)
    return hits / len(grams) >= _EVIDENCE_TRIGRAM_FLOOR


# The recorded fields a FAIL can cite, as the prompt names them. A tool span
# whose only record of failure is its status (no input, no output) is cited by
# that status; an available tool the agent never called is cited by its name in
# the TOOLS AVAILABLE list, which belongs to the trace rather than to a span.
_FIELD_KEYS = {"in": "input.value", "out": "output.value"}
_STATUS_FIELD = "status"
_TOOLS_FIELD = "tools"
_FIELD_ALIASES = {
    "in": "in",
    "input": "in",
    "out": "out",
    "output": "out",
    "status": _STATUS_FIELD,
    "status_code": _STATUS_FIELD,
    "tools": _TOOLS_FIELD,
    "tools_available": _TOOLS_FIELD,
}
# Status values that cannot support a FAIL: the span never set one, or it
# recorded success (the same set key-moment attribution reads as "fine").
_NO_STATUS = _OK_STATUS | {"status_code_unset"}
# A quote is bound to the passages it was copied from. GOAL, TOOLS and
# INSTRUCTION each ask for two passages that usually live in different fields;
# a third is slack. Citing more than that binds the quote to nothing in
# particular, so it is refused rather than truncated.
_MAX_CITES = 3
# Words of a span's header line, for matching a quote of its status.
_STATUS_WORD = re.compile(r"[a-z0-9_]+")
# Where a quote is split into sentences: one passage per cite when a quote has
# no per-cite passages, and one checked sentence at a time within a passage.
_PASSAGE_BREAK = re.compile(r"(?<=[.!?])\s+")
# Words of a quote with punctuation and casing set aside. An apostrophe inside
# a word is punctuation too: "can't", "can’t" and "cant" are one word.
_WORD = re.compile(r"\w+")
_INWORD_APOSTROPHE = re.compile(r"(?<=\w)['\u2019](?=\w)")
# An optional label in front of a quote of the tools list, as the prompt shows it.
_TOOLS_LABEL = re.compile(r"^tools available\s*:\s*")
# Attributes a trace records a called tool's name under: the tool span's own
# name (``tool.name``, ``gen_ai.tool.name``) and the function name of a tool
# call on the span that made it (``...tool_calls.N.tool_call.function.name``).
# The tool definitions sent to the model (``llm.tools.N...``, under any
# prefix) name the tools available, not the ones called.
_TOOL_NAME_KEY = re.compile(
    r"(?:^|[._])(?:tool[._]name|tool_call\.function\.name|function_call\.name)$"
)
_TOOL_DEFINITION_KEY = re.compile(r"(?:^|\.)llm\.tools\.|tool[._]definitions")
# Words a tool span's name can carry besides the tool's own name. A span name
# with "tool" in it ("Tool #1", "execute_tool") or made only of these words
# ("run", "function_call") does not say which tool ran.
_TOOL_SPAN_WORDS = frozenset(
    {"tool", "tools", "call", "calls", "function", "execute", "run", "invoke"}
)
_TOOL_LABEL_WORDS = frozenset({"tool", "tools"})
_TOOL_KINDS = {"Tool", "TOOL"}


def _cites(verdict: dict[str, Any]):
    """The (span id, field, passage) entries a FAIL says its evidence came from.

    ``passage`` is the entry's own ``quote``, or ``None`` when it gives none.
    ``[]`` when it cites nothing; ``None`` when it cites something that cannot
    be read as a span field, or more passages than a quote can be bound to.
    A bare ``span``/``field`` pair on the verdict is read as one citation. The
    tools list belongs to no span, so its cite needs no span id.
    """
    raw = verdict.get("cite")
    if raw is None and verdict.get("span"):
        raw = [{"span": verdict.get("span"), "field": verdict.get("field")}]
    if isinstance(raw, dict):
        raw = [raw]
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > _MAX_CITES:
        return None
    entries = []
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        span_id = str(entry.get("span") or "").strip()
        name = _FIELD_ALIASES.get(str(entry.get("field") or "").strip().lower())
        quote = entry.get("quote")
        if not name or not isinstance(quote, (str, type(None))):
            return None
        if not span_id and name != _TOOLS_FIELD:
            return None
        entries.append((span_id, name, quote.strip() if quote else None))
    return entries


def _cited_passages(verdict: dict[str, Any], entries) -> list[str | None] | None:
    """One passage per cite, in cite order, or None if they cannot be paired.

    Each cite's own ``quote`` when every cite carries one; a status cite may
    leave its quote out next to quoted cites (its passage is then ``None`` and
    stands for the recorded status). Without any quotes, a single cite takes
    the whole evidence, and several cites take the evidence's sentences in
    order, one each; a quote that does not split into exactly one passage per
    cite cannot say which passage came from which field.
    """
    quotes = [quote for _span_id, _name, quote in entries]
    if all(quotes):
        return quotes
    if any(quotes):
        unquoted = {name for _span_id, name, quote in entries if not quote}
        return quotes if unquoted == {_STATUS_FIELD} else None
    evidence = str(verdict.get("evidence") or "").strip()
    if len(entries) == 1:
        return [evidence]
    passages = [p for p in _PASSAGE_BREAK.split(evidence) if p.strip()]
    return passages if len(passages) == len(entries) else None


def _field_values(trace, span_id: str, name: str) -> list[str]:
    """Recorded values of one cited span field ("in"/"out"), one per copy of
    the span.

    Read from the trace itself, never from the prompt: the prompt also carries
    our instructions and framing, and a quote that only matches those is not a
    quote from the trace. Neutralised as the prompt neutralised them, so a
    quote of a field that contained a marker still matches. Every copy of a
    duplicated span id counts, as it does for the completion gate. ``[]`` when
    the span is unknown or recorded nothing in that field.
    """
    values = [
        str((s.get("span_attributes") or {}).get(_FIELD_KEYS[name]) or "")
        for s in _named_spans(trace, span_id)
    ]
    return [neutralize(v) for v in values if v.strip()]


def _status_lines(shown, span_id: str) -> list[tuple[str, str]]:
    """(header line, status) of each shown copy of a span that recorded a
    status other than success.

    The line is rebuilt from the payload the prompt was built from, with the
    prompt's own renderer, so it is the line the model saw word for word,
    including the [FLAGGED] mark the prefilter puts on every Error span.
    """
    lines = []
    for s in (shown or {}).get("spans") or []:
        if str(s.get("id") or "") != span_id:
            continue
        status = str(s.get("status") or "").strip()
        if status.casefold() in _NO_STATUS:
            continue
        lines.append((span_header(s), neutralize(status)))
    return lines


def _shown_tool_names(shown) -> list[str]:
    """The tool names the TOOLS AVAILABLE line shows in full, in its order."""
    names = [neutralize(t) for t in (shown or {}).get("tools_available") or ()]
    text = tools_available_text(names)
    visible, end = [], 0
    for n in names:
        end += len(n)
        if end > len(text):
            break
        visible.append(n)
        end += len(", ")
    return visible


def _recorded_tool_names(attrs) -> set[str]:
    """Tool names recorded in a span's attributes, as the prompt shows names."""
    return {
        _norm_for_match(neutralize(value))
        for key, value in attrs.items()
        if _TOOL_NAME_KEY.search(str(key))
        and not _TOOL_DEFINITION_KEY.search(str(key))
        and str(value or "").strip()
    }


def _names_listed_tool(span_name: str, listed: str) -> bool:
    """Does a tool span's name carry this tool name as a whole name?"""
    if not listed or listed not in span_name:
        return False
    whole = rf"(?<![a-z0-9_]){re.escape(listed)}(?![a-z0-9_])"
    return re.search(whole, span_name) is not None


def _span_name_words(span_name: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", span_name).casefold()
    return set(re.findall(r"[a-z0-9]+", spaced))


def _span_name_is_a_tool_name(span_name: str) -> bool:
    """Can a tool span's own name be read as the name of the tool it ran?"""
    words = _span_name_words(span_name)
    return bool(words - _TOOL_SPAN_WORDS) and not words & _TOOL_LABEL_WORDS


def _called_tool_names(trace) -> set[str] | None:
    """Names of the tools the trace recorded a call to, or None when it
    recorded a tool call whose tool cannot be named.

    A tool span (by the prefilter's rule) is a call of the tool its name
    attribute names, of every declared tool whose name appears in its span
    name as a whole name ("Tool issue_refund" is a call of issue_refund), and
    of its own span name. A tool call recorded on the span that made it counts
    too. A tool span named by none of these ("Tool #1", "run") could be a call
    of any listed tool, so nothing is known to be uncalled; a span whose kind
    says it is not a tool ("Tool Calls" on an LLM span) is not a call.
    """
    spans = [
        span
        for top in (trace or {}).get("spans") or []
        for span, _depth in flatten_spans(top)
    ]
    declared = set()
    for span in spans:
        names = _tool_names_from_definitions(span.get("span_attributes") or {})
        declared |= {_norm_for_match(neutralize(n)) for n in names}
    called = set()
    for span in spans:
        attrs = span.get("span_attributes") or {}
        recorded = _recorded_tool_names(attrs)
        called |= recorded
        raw_name = str(span.get("span_name") or "")
        kind = _get_span_kind(attrs)
        if not (kind in _TOOL_KINDS or "Tool" in raw_name):
            continue
        name = _norm_for_match(neutralize(raw_name))
        named = {n for n in declared if _names_listed_tool(name, n)}
        called |= named | {name}
        if recorded or named or _span_name_is_a_tool_name(raw_name):
            continue
        if not kind or kind in _TOOL_KINDS:
            return None
    return called


def _shown_span_ids(shown) -> set[str]:
    return {str(s.get("id") or "") for s in (shown or {}).get("spans") or []}


def _passage_names_status(passage: str | None, lines) -> bool:
    """Does this passage quote the cited span's status?

    A status is a single recorded word ("Error"), shorter than any quote the
    text gate accepts. A passage citing it must name that status and may
    otherwise only repeat words from the span's header line (id, name, kind,
    the [FLAGGED] mark when the prompt showed it): anything else in it was not
    recorded on that span. A status cite without a passage stands for the
    recorded status.
    """
    if passage is None:
        return bool(lines)
    said = set(_STATUS_WORD.findall(_norm_for_match(passage)))
    for line, status in lines:
        recorded = set(_STATUS_WORD.findall(_norm_for_match(status)))
        if recorded and recorded <= said <= set(
            _STATUS_WORD.findall(_norm_for_match(line))
        ):
            return True
    return False


def _passage_names_tools(passage: str, names: list[str], trace) -> bool:
    """Is this passage copied from TOOLS AVAILABLE, naming a tool never called?

    The passage must be one listed name or a run of them in the order the
    line shows (optionally with its label), and at least one of them must
    have no recorded call: a tools cite supports a FAIL for an available tool
    the agent never called, and a tool it did call cannot show that. When the
    trace recorded a tool call it does not name, no tool is known to be
    uncalled and the cite is declined.
    """
    said = _TOOLS_LABEL.sub("", _norm_for_match(passage))
    items = [i.strip(" \"'`.") for i in said.split(",")]
    if not items or not all(items):
        return False
    listed = [_norm_for_match(n) for n in names]
    width = len(items)
    in_order = any(
        listed[i : i + width] == items for i in range(len(listed) - width + 1)
    )
    if not in_order:
        return False
    called = _called_tool_names(trace)
    return called is not None and any(i not in called for i in items)


def _words(text) -> str:
    """A quote's words, space-separated and padded, with punctuation, casing
    and Unicode composition set aside, so a word sequence is matched on word
    boundaries."""
    folded = unicodedata.normalize("NFC", str(text or "").casefold())
    return f" {' '.join(_WORD.findall(_INWORD_APOSTROPHE.sub('', folded)))} "


def _sentence_is_quoted(sentence: str, first: bool, last: bool, recorded) -> bool:
    """One sentence of a passage of several, found in one recorded sentence.

    ``recorded`` are the cited field's sentences as ``_words``. The sentence's
    words must be that sentence's words, in order, with punctuation, casing
    and whitespace set aside. A sentence break inside the passage must be one
    in the field: the first sentence may start inside a recorded sentence but
    ends where it ends, the last starts where one starts but may stop early,
    and any sentence between them is a whole recorded sentence. A break the
    passage adds cannot join words recorded in different places.
    """
    words = _words(sentence)
    if not words.strip():
        return True
    if first:
        return any(r.endswith(words) for r in recorded)
    if last:
        return any(r.startswith(words) for r in recorded)
    return words in recorded


def _passage_in_field(passage: str, values: list[str]) -> bool:
    """Is this passage a quote of the cited span field?

    Punctuation, casing and whitespace a model rewrites are set aside, but no
    word may be changed, added or left out: the passage's words must appear in
    the field in order, or each of its sentences must be found in the field
    on its own, joined at the field's own sentence breaks.
    """
    said = _norm_for_match(passage)
    fields = [_norm_for_match(v) for v in values]
    if said and said in fields:
        # The whole field, however short, is unambiguous.
        return True
    if len(said) < _MIN_EVIDENCE_CHARS:
        return False
    if any(said in f for f in fields):
        return True
    field_words = [_words(v) for v in values]
    if _words(said).strip() and any(_words(said) in f for f in field_words):
        return True
    sentences = [s for s in _PASSAGE_BREAK.split(said) if s]
    if len(sentences) < 2:
        return False
    recorded = [_words(s) for f in fields for s in _PASSAGE_BREAK.split(f)]
    last = len(sentences) - 1
    return all(
        _sentence_is_quoted(s, i == 0, i == last, recorded)
        for i, s in enumerate(sentences)
    )


def _leading_quote(evidence: str, values: list[str]) -> str | None:
    """The first sentence of a one-cite evidence string when it is copied
    exactly from the cited field, else None.

    A quote followed by the model's own commentary is still a quote, but only
    the copied sentence is evidence: the commentary is not kept.
    """
    first = _PASSAGE_BREAK.split(evidence.strip(), 1)[0].strip()
    said = _norm_for_match(first)
    if len(said) < _MIN_EVIDENCE_CHARS:
        return None
    if any(said in _norm_for_match(v) for v in values):
        return first
    return None


def _bind(verdict: dict[str, Any], trace, shown) -> tuple[str | None, list[str]]:
    """(why this FAIL's evidence is not bound to the trace, its passages).

    The reason is None when every cited passage is found in its own cited
    field, so one real passage cannot carry another that is not in the trace.
    The passages are then the FAIL's evidence of record. ``shown`` is the
    part of the payload the prompt showed: only its spans and tools list can
    be cited, span statuses and the tools list are matched as the prompt
    showed them, span inputs and outputs against the trace's recorded values.
    """
    entries = _cites(verdict)
    if entries is None:
        return "unreadable_cite", []
    if not entries:
        return "uncited", []
    passages = _cited_passages(verdict, entries)
    if passages is None:
        return "unpaired_passages", []
    # One cite without a quote of its own takes the whole evidence, which may
    # be a quote followed by the model's commentary.
    lone = len(entries) == 1 and entries[0][2] is None
    checked = []
    for (span_id, name, _quote), passage in zip(entries, passages, strict=True):
        if name == _STATUS_FIELD:
            lines = _status_lines(shown, span_id)
            if not lines:
                return "cited_field_missing", []
            if not _passage_names_status(passage, lines):
                return "not_in_cited_field", []
            checked.append(passage if passage is not None else lines[0][0])
            continue
        if name == _TOOLS_FIELD:
            names = _shown_tool_names(shown)
            if not names:
                return "cited_field_missing", []
            if not _passage_names_tools(passage, names, trace):
                return "not_in_cited_field", []
            checked.append(passage)
            continue
        if shown is not None and span_id not in _shown_span_ids(shown):
            return "cited_field_missing", []
        values = _field_values(trace, span_id, name)
        if not values:
            return "cited_field_missing", []
        if _passage_in_field(passage, values):
            checked.append(passage)
            continue
        head = _leading_quote(passage, values) if lone else None
        if head is None:
            return "not_in_cited_field", []
        checked.append(head)
    return None, checked


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
        # The view is what the prompt actually shows after its length guard;
        # only that can be cited.
        user_prompt, shown = build_prompt_v8_with_view(payloads)
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
                        # The raw trace, not the prompt: every quote is checked
                        # against the span fields it cites, and the completion
                        # gate verifies the named span's real output. The
                        # prompt also holds our own instructions and framing,
                        # which are not evidence of anything the agent did.
                        trace=trace_map[first_label][0],
                        trace_has_content=_trace_has_captured_content(
                            trace_map[first_label][0]
                        ),
                        # What the model was shown: which spans and whether
                        # the tools list, and the lines a status cite quotes.
                        shown=shown,
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
        shown: dict[str, Any] | None = None,
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

        With the ``trace`` in hand, each passage of a FAIL's evidence must be
        found in the recorded text of the span field cited for it (span id +
        "in"/"out", or "status" for a span that recorded only its status), read
        from the trace itself. Matching against the prompt instead would accept
        a quote of our own instructions or framing, and would not say where in
        the trace the quote came from. A FAIL that cites nothing, cites a field
        that does not hold its quote, or cites a span that does not exist is
        dropped: the model asserting a quote it cannot place is the definition
        of the failure this gate exists to stop. This is deliberately
        fail-closed — a dropped real issue costs recall, a kept invented one
        costs the user's trust in every other row.

        ``shown`` is the part of the payload the prompt showed
        (build_prompt_v8_with_view): only spans and a tools list it showed can
        be cited, a cited status is matched against the span's header line
        exactly as the prompt rendered it, and a cited tool name against the
        TOOLS AVAILABLE list. Rebuilt from the trace when not supplied.

        ``seen_text`` is consulted only when no trace is supplied; the scanner
        always supplies one.
        """
        dims = parsed.get("dimensions") or {}
        if trace is not None and shown is None:
            shown = build_prompt_v8_with_view(
                build_trace_payload(trace, structural_prefilter_with_ids(trace))
            )[1]
        hay = "" if trace is not None else _norm_for_match(seen_text)
        failed = set()
        unverified = []
        reasons = set()
        # Dimensions whose evidence was checked passage by passage carry those
        # passages from here on, so text the gate never checked cannot decide
        # which claims describe the same moment.
        checked = {}
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
                    # nothing to quote. The quote is bound to the span the claim
                    # names: the cut-off ending has to be that span's output.
                    or (
                        _is_truncation_not_absence(named_output)
                        and not _evidence_is_quotable(
                            v.get("evidence"),
                            _norm_for_match(neutralize(named_output)),
                        )
                    )
                ):
                    unverified.append(k)
                    continue
            elif trace is not None:
                reason, passages = _bind(v, trace, shown)
                if reason:
                    unverified.append(k)
                    reasons.add(reason)
                    continue
                checked[k] = {**v, "evidence": " ".join(passages)}
            elif hay and not _evidence_is_quotable(v.get("evidence"), hay):
                unverified.append(k)
                continue
            failed.add(k)
        if unverified:
            # Suppression is a measurement, not a silent behaviour: without this
            # count there is no way to tell a precise scanner from a mute one.
            # The reasons separate a model that stopped citing from one that
            # cites the wrong place.
            logger.info(
                "scanner_evidence_gate_dropped",
                dimensions=sorted(unverified),
                dropped=len(unverified),
                reasons=sorted(reasons),
            )
        # Collapse after gating, not before: a dimension whose evidence was
        # invented must not be the one that survives a merge.
        failed = _collapse_same_event(failed, {**dims, **checked})
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
