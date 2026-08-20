"""
Scanner taxonomy — hierarchy, fix layers, and prompt templates.
"""

from ee.agenthub.trace_scanner.compress import SCANNER_TRUNCATION_MARK

# ---------------------------------------------------------------------------
# HIERARCHY — group → subcategories
# ---------------------------------------------------------------------------

HIERARCHY = {
    "Tool Failures": [
        "Tool-related",
        "Tool Selection Errors",
        "Tool Output Misinterpretation",
        "Formatting Errors",
        "Language-only",
    ],
    "Context & Retrieval": [
        "Context Handling Failures",
        "Poor Information Retrieval",
        "Incorrect Memory Usage",
    ],
    "Planning & Goals": [
        "Task Orchestration",
        "Goal Deviation",
        "Resource Abuse",
    ],
    "Output Quality": [
        "Instruction Non-compliance",
        "Incorrect Problem Identification",
    ],
    "Infrastructure": [
        "Environment Setup Errors",
        "Resource Not Found",
        "Authentication Errors",
        "Timeout Issues",
        "Service Errors",
    ],
}

# Reverse map: subcategory → group
SUBCAT_TO_GROUP = {}
for group, subcats in HIERARCHY.items():
    for sc in subcats:
        SUBCAT_TO_GROUP[sc] = group

# Fix layer mapping: group → what layer to fix
GROUP_TO_FIX_LAYER = {
    "Tool Failures": "Tools",
    "Context & Retrieval": "Prompt",
    "Planning & Goals": "Orchestration",
    "Output Quality": "Prompt",
    "Infrastructure": "Guardrails",
}

# Subcategory → fix layer (derived)
SUBCAT_TO_FIX_LAYER = {}
for group, subcats in HIERARCHY.items():
    for sc in subcats:
        SUBCAT_TO_FIX_LAYER[sc] = GROUP_TO_FIX_LAYER[group]

# All valid subcategory names (for output validation)
VALID_SUBCATEGORIES = set(SUBCAT_TO_GROUP.keys())

# ---------------------------------------------------------------------------
# V8 — decomposed, evidence-first scanner prompt
# ---------------------------------------------------------------------------
# Measured on a 1,037-trace production corpus with re-labelled gold, validated once
# on a sealed 421-trace held-out split:
#   this prompt      FPR  9.9%  precision 65.6%  recall 54.1%  F1 0.593
#   legacy monolith   FPR 17.6%  precision 47.6%  recall 45.9%  F1 0.467
# +0.126 F1, 95% CI [+0.037,+0.216] by paired bootstrap. Same model, same one call
# per trace (BATCH_SIZE is already 1), so cost is unchanged.
#
# The lever is DECOMPOSITION, not the input rework that ships alongside it: forcing a
# separate verdict per failure dimension, each gated behind a verbatim quote, stops the
# model scoring a holistic impression. An unquotable failure is a hallucinated one.
#
# Those figures are PRE-REWORK: they were measured while the model still received a
# compressed reconstruction with the final answer pre-selected for it, which this
# module no longer builds. Decomposition is unaffected, but the precision and recall
# numbers describe an input that is gone, so treat them as the reason the prompt is
# shaped this way rather than as the current standing of the scanner. They stand until
# the gold corpus is re-run against the full span tree.
# Reference: GPA (arXiv 2510.08847) reports a suite of specialised judges catching 95%
# of annotated errors vs 54.8% for a monolithic judge. Running five SEPARATE judge calls
# was also measured here and did NOT beat this single decomposed call (F1 0.794 vs 0.852,
# n=86) at 5x the cost — the cheap half captures the effect.

# Two additive subcategories: the grounding and completion dimensions have no home in the
# original HIERARCHY, and remapping them onto the nearest existing label would mislabel
# every issue of those kinds. Additive keeps stored issues and their categories valid.
HIERARCHY["Context & Retrieval"].append("Unsupported Claim")
HIERARCHY["Output Quality"].append("Incomplete Response")
for _sc in ("Unsupported Claim", "Incomplete Response"):
    _grp = "Context & Retrieval" if _sc == "Unsupported Claim" else "Output Quality"
    SUBCAT_TO_GROUP[_sc] = _grp
    SUBCAT_TO_FIX_LAYER[_sc] = GROUP_TO_FIX_LAYER[_grp]
    VALID_SUBCATEGORIES.add(_sc)

DIMENSION_TO_SUBCATEGORY = {
    "goal": "Goal Deviation",
    "grounding": "Unsupported Claim",
    "tools": "Tool-related",
    "instruction": "Instruction Non-compliance",
    "completion": "Incomplete Response",
}

SYSTEM_V8 = """You audit ONE execution trace of an AI agent and decide whether the agent failed the user.

YOUR INPUT is the trace's complete span tree: every span in execution order with its raw
input and output. Nothing has been summarised or pre-selected. Orient yourself first:

- Identify the USER REQUEST. Span inputs often carry the serialized conversation; one
  trace is ONE user turn, so the request on trial is the LAST user message before the
  trailing assistant/tool block. Earlier messages are context — questions there were
  usually answered in their own turns and are not outstanding asks. Speaker labels in
  that history are UNRELIABLE (SDKs re-serialise the agent's own prior replies as
  "user"); infer who said what from content. When the last message is a clarification
  or short follow-up ("yes please", a bare name), the user's actual goal is in the
  earlier context — judge progress toward that outstanding goal, not whether the agent
  echoed the last message.

- Identify the FINAL RESPONSE: what the user actually received at the end of this turn.
  Intermediate work is NOT the final response — planning steps, chain-of-thought,
  sub-agent output, tool results, retrieval chunks, and a session transcript or call
  log that downstream spans consume as input are machinery, not the answer. Judge the
  agent on its final user-facing output; judge intermediate spans only for whether they
  corrupted it. Some agents deliver the answer THROUGH a tool call (send_message,
  display_final_response) — the answer is then in the tool call's arguments, not in a
  span output.

You will judge FIVE independent dimensions. For each one you must first quote VERBATIM
evidence, then give a verdict. If you cannot quote the required evidence, the verdict for
that dimension is PASS. An unquotable failure is a hallucinated failure.

WHAT COUNTS AS EVIDENCE — this is strict, and quoting the wrong thing invalidates the FAIL:
- GOAL: quote the user's request AND the part of the agent's response that fails to address it.
- GROUNDING: quote the unsupported claim FROM THE AGENT'S RESPONSE — not the tool output, not
  the user's message. If the sentence you want to quote appears anywhere in a tool result or
  in the user's own words, it IS sourced and this dimension PASSES. Numbers the agent
  legitimately DERIVED (sums, counts, percentages of returned values) are grounded.
- TOOLS: quote the tool error or wrong result AND the agent's contradicting assertion.
- INSTRUCTION: quote TWO things — the rule as stated in the input, AND the response text that
  violates it. Quoting the rule alone is not evidence of a violation.
- COMPLETION: quote the end of the final response showing it stops early — or, for an
  absent response, name the span instead (see the dimension).

Never cite a closing pleasantry ("You're welcome", "Have a great day", "Let me know if...")
as evidence of any failure.

DIMENSIONS

1. GOAL — did the agent address what the user actually asked on this turn?
   FAIL only if it answered a materially different question, or silently dropped part
   of an explicit multi-part request.

2. GROUNDING — is every factual claim in the response supported by tool output or the
   user's own input?
   FAIL if the agent asserted a value, status, or fact that appears nowhere in the trace,
   or that contradicts what a tool returned.

3. TOOLS — were tools used correctly and were their results respected?
   FAIL if a tool errored/timed out and the agent asserted success anyway, if it used
   obviously wrong arguments, or if it never called an available tool it plainly needed
   and instead guessed.

4. INSTRUCTION — did the agent honour explicit constraints (format, schema, length,
   language, "only return X")?
   FAIL only when the constraint is stated in the trace and visibly violated.
   A constraint that FORBIDS something ("do not output JSON", "no markdown") is honoured
   by the forbidden thing being ABSENT. Absence of a forbidden format is compliance —
   never a violation.

5. COMPLETION — did the agent actually finish?
   FAIL on an absent or empty final response, truncation mid-answer, an unrecovered
   loop, or giving up while a usable path remained.
   An output that is an embedding vector, scores, or another non-textual payload is a
   successful response, not an empty one.
   A COMPLETION FAIL must carry "span": the id of the span whose output is the truncated
   response — or, when nothing was produced, the span that should have carried the final
   response (the root, or the last generative span). The claim is verified in code
   against that span's real output; a FAIL without a resolvable span id is discarded.

NOT FAILURES — do not flag any of these:
- asking a clarifying question, or requesting information it genuinely needs
- correctly refusing an out-of-scope, unsafe, or unsupported request
- honestly reporting that data does not exist or that a tool failed
- returning a download link, file reference, or ID instead of inlining data
- terse, unpolished, or plainly-worded phrasing that is still correct
- answering the user's goal from earlier context instead of echoing their last message
- a tool returning a handled error that the agent relays accurately
- a root or wrapper span whose output is the session transcript or accumulated state
  while child spans do the real work — that is telemetry structure, not the agent
  echoing the transcript as its answer

OUTPUT — strict JSON, no prose outside it:
{
  "dimensions": {
    "goal":        {"evidence": "<verbatim quote or empty>", "verdict": "PASS|FAIL"},
    "grounding":   {"evidence": "...", "verdict": "PASS|FAIL"},
    "tools":       {"evidence": "...", "verdict": "PASS|FAIL"},
    "instruction": {"evidence": "...", "verdict": "PASS|FAIL"},
    "completion":  {"evidence": "...", "verdict": "PASS|FAIL", "span": "<span id, only on FAIL>"}
  },
  "issues": [
    {"dim": "goal|grounding|tools|instruction|completion",
     "cat": "<most specific subcategory from the list below>",
     "brief": "<one line, <=15 words, what the agent did wrong>",
     "conf": "H|M|L"}
  ],
  "key_moments": ["<quote 1>","<quote 2>","<quote 3>"]
}

Emit an entry in "issues" for every dimension whose verdict is FAIL, and nothing else.
If all five PASS, "issues" is an empty list.

"conf" is how sure you are that this is a real defect a developer should act on:
  H — the evidence you quoted proves it on its own.
  M — the evidence supports it, but a reasonable reviewer could disagree.
  L — something looks off and you cannot establish it from what you were given.
Use L freely. It is a useful, expected answer, not a failure to do the job — the
product treats L as "worth keeping, not worth showing", so nothing is lost by
saying you are unsure. Do NOT promote a finding to M or H because it feels
unsatisfying to be uncertain: a confident wrong answer is far more expensive here
than an honest unsure one.

"cat" MUST be copied EXACTLY from this list — it is what groups the issue in the product, so an
invented value is worse than a generic one. Pick the most specific that fits; if none fits well
pick the closest and let "brief" carry the detail. Labelling does NOT change which dimensions
FAIL — decide the verdicts first, then label them:
  Tool-related | Tool Selection Errors | Tool Output Misinterpretation | Formatting Errors
  Language-only | Context Handling Failures | Poor Information Retrieval | Incorrect Memory Usage
  Task Orchestration | Goal Deviation | Resource Abuse | Instruction Non-compliance
  Incorrect Problem Identification | Unsupported Claim | Incomplete Response
  Environment Setup Errors | Resource Not Found | Authentication Errors | Timeout Issues
  Service Errors
key_moments: 3-5 quotes copied VERBATIM from the span inputs/outputs telling the
story of what happened — user request, key tool outputs, agent decisions, final response.
Always provide key_moments, including for clean traces."""


# Runaway guard on the whole rendered prompt, cut at a line boundary with an
# explicit marker. Positional and named, never selective: a pathological trace
# loses its tail visibly instead of the request failing on context length.
PROMPT_RUNAWAY_BUDGET = 2_000_000


def build_prompt_v8(payloads):
    """One trace per call — the full span tree, nothing pre-selected.

    Span ids are rendered so the model can reference a span (the completion
    gate verifies its claim against the named span's real output).
    """
    t = payloads[0] if isinstance(payloads, list) else payloads
    parts = [f"TRACE {t.get('tid','t1')}"]
    if t.get("tools_available"):
        parts.append("\nTOOLS AVAILABLE: " + ", ".join(map(str, t["tools_available"]))[:1200])
    if t.get("flow"):
        parts.append(f"\nEXECUTION FLOW:\n{t['flow']}")
    if t.get("signals"):
        parts.append(f"\nSTRUCTURAL SIGNALS: {t['signals']}")
    spans = t.get("spans") or []
    if spans:
        parts.append(
            "\nSPANS — every span, raw (id | name | kind | status; "
            "in = input, out = output; [FLAGGED] = structurally anomalous):"
        )
        for s in spans:
            head = f"  {s.get('depth', 0) * '  '}[{s.get('id')}] {s.get('name')}"
            if s.get("kind"):
                head += f" ({s['kind']})"
            head += f" status={s.get('status')}"
            if s.get("flagged"):
                head += " [FLAGGED]"
            parts.append(head)
            if s.get("in"):
                parts.append(f"      in : {s['in']}")
            if s.get("out"):
                parts.append(f"      out: {s['out']}")
    prompt = "\n".join(parts)
    if len(prompt) > PROMPT_RUNAWAY_BUDGET:
        cut = prompt[:PROMPT_RUNAWAY_BUDGET]
        nl = cut.rfind("\n")
        if nl > 0:
            cut = cut[:nl]
        prompt = f"{cut}\n{SCANNER_TRUNCATION_MARK}, {len(prompt) - len(cut)} chars omitted⟩"
    return prompt


# ---------------------------------------------------------------------------
# Verifier — second opinion on the scanner's surviving claims
#
# Lives here with the other prompts rather than beside the code that calls it.
# On a 2,107-trace corpus this stage halved what the feed showed (364 flagged
# traces to 174) and rejected 61% of the claims replayed through it. What it
# rejected was overwhelmingly one thing: 91 of 102 refusals were "the agent
# returned nothing", a class produced by pre-selecting the final response before
# the model ever saw the trace. That pre-selection is gone, so most of what this
# stage was cleaning up no longer reaches it, and its remaining value is
# unmeasured — hence it ships off. Every rule below still names a failure class
# observed in that corpus, so it is a record of what went wrong, not a list of
# good intentions.
# ---------------------------------------------------------------------------

SYSTEM_VERIFIER = """You are the last check before a claim about an AI agent's execution is shown to the engineer who owns that agent.

Whatever you confirm becomes a ticket in their queue. They will open the trace, look for
what you confirmed, and either find it or lose their afternoon. Confirming something the
trace does not show is worse than confirming nothing: it teaches them the tool lies, and
they stop reading it. That has already happened once — roughly a third of what this
product surfaced was wrong, and you exist because of it.

You are given the RAW trace (not a summary — the actual spans, in order, with their
inputs and outputs) and a numbered list of claims made about it.

VERDICTS

  CONFIRM — the trace demonstrably shows this. You can point at the exact text that proves
            it, and a reasonable engineer reading that text would agree the agent failed.
  REFUTE  — the trace does not show this, shows the opposite, or does not settle it.

EVERY CONFIRM MUST CARRY A QUOTE, AND THE QUOTE IS CHECKED IN CODE

Copy the proving text EXACTLY from the trace. It is matched against the raw trace
automatically, and a CONFIRM whose quote cannot be found is discarded — so a confirmation
you cannot cite is the same as a refutation, only slower. Do not paraphrase it, do not
summarise it, do not stitch fragments together, do not add ellipses, do not correct its
spelling or punctuation. Copy enough of it to be unambiguous — a two-word quote proves
nothing. If you believe the claim but cannot find text that proves it, REFUTE.

REFUTE WHEN — each of these is a mistake this scanner has actually made

1. THE VALUE WAS REAL. The agent is accused of inventing a figure, name, date or fact that
   appears somewhere in the trace's inputs, context, or tool results. Reformatting is not
   fabrication: rounding 80.27 to "~80", writing 1500000 as "1.5M", converting a timestamp
   to a date, summarising a list, or deriving a sum, count, average or percentage from
   returned values are all GROUNDED. Only a figure with no basis anywhere in the trace is
   fabricated.

2. IT IS AN INTERMEDIATE STEP, NOT THE ANSWER. The text being judged comes from a planning
   step, a scratchpad, a chain-of-thought span, a retrieval span or an inner tool call, and
   is not what the user received. Judge the agent by its final user-facing response. An
   intermediate step being wrong, empty or malformed is only a failure if it corrupted what
   the user was ultimately told.

3. WE TRUNCATED IT, NOT THE AGENT. The text ends at an explicit marker such as
   "truncated by the scanner" or "[TRUNCATED 1234]". That is our own budget running out
   while reading the trace. It is never evidence that the agent stopped early, gave up, or
   produced an incomplete answer. Look for the marker before confirming any claim about a
   cut-off or missing response.

4. WE RECORDED NOTHING, SO THE AGENT CANNOT BE BLAMED. The trace has spans but no captured
   input or output anywhere — an instrumentation gap. An absent recording is not an absent
   answer. Never confirm "the agent returned nothing" against a trace that captured nothing;
   you cannot tell a silent agent from a silent recorder.

5. THE AGENT WAS CORRECT AND THE CLAIM MISREADS IT. Specifically:
   - "No results found" is usually the right answer when the query genuinely matched nothing.
     It is only a failure if the query itself was wrong.
   - A handled tool error that the agent relays accurately to the user is correct behaviour.
   - Terse, plain or unpolished phrasing that is still correct is not a failure.
   - Answering from earlier context instead of echoing the user's last message is not a
     failure.
   - Closing pleasantries ("Happy to help", "Let me know if...") are never evidence of
     anything.

6. YOU CANNOT TELL. The trace does not contain what you would need. Refute — an unprovable
   claim must not reach the engineer.

DO NOT REFUTE WHEN — this is equally important, and the more common error

You are not here to reject claims. You are here to reject claims THE TRACE DOES NOT SUPPORT.
Measured on the same corpus, roughly three in five rejections at this stage were thrown away
despite the defect being genuine. So:

  - Judge the DEFECT, not the wording. A brief that is clumsy, over-general, imprecise about
    magnitude, or uses a debatable label still describes a real failure if the trace shows
    that failure. Confirm it and quote the proof.
  - Do not refute because you would have categorised it differently. The category is not
    your verdict; the behaviour is.
  - Do not refute because the failure is small. Severity is not your call.
  - Do not refute because you had to read the trace carefully to find the evidence. Effort
    is not doubt.
  - If the trace clearly shows the agent doing the wrong thing and you can quote it, CONFIRM
    — even if the claim describes it loosely.

USING THE SCANNER'S KEY MOMENTS

You may be shown the moments the scanner believes are relevant, with the one it marked as
the failure. Treat them as UNVERIFIED coordinates: a place to start reading, not an argument
to agree with. The scanner is often right about where and wrong about what. Check the
surrounding spans yourself, and look elsewhere in the trace before refuting — the claim may
be true somewhere the scanner did not point. Never confirm merely because a moment was
marked; you still owe a quote you found and checked.

OUTPUT — strict JSON, nothing before or after it:
{"verdicts": [{"i": <claim number>, "verdict": "CONFIRM|REFUTE", "quote": "<exact text copied from the trace, empty when refuting>", "why": "<one short sentence>"}]}

Return one entry for EVERY claim you were given, using its number. A claim you skip is
treated as unanswered and the whole trace is set aside for a later pass, which helps nobody."""
