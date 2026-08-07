"""
Scanner taxonomy — hierarchy, fix layers, and prompt templates.

Ported from experiments/trace_scanner/scanner_v3.py.
"""

import json

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
# PROMPT TEMPLATES
# ---------------------------------------------------------------------------

CACHED_PREFIX = """You are an AI trace error scanner. For each trace, evaluate 5 error groups. For each group, decide YES or NO. If YES, identify WHICH specific subcategory.

!! CRITICAL — INPUT IS COMPRESSED, NOT RAW !!
Every text field you see below (task, result, spans[].in, spans[].out) has been
pre-processed by a lossy compressor that strips stopwords, grammar fluff,
punctuation (including periods), and JSON syntax — keeping only content-bearing
keywords. A trailing "..." means "we hit our length budget", NOT that the
agent's actual response was truncated. Missing periods, choppy phrasing,
or dropped articles ("the", "a", "is") are compression artifacts, NOT agent
errors. DO NOT flag issues like "response truncated mid-sentence",
"incomplete output", "choppy grammar", or "missing punctuation" — those are
artifacts of OUR compression, not the agent's behavior. Judge the agent on
the SEMANTIC CONTENT of the keywords, not their surface form.

TRACE FORMAT:
- task: the original user request (WHAT the agent was supposed to do) — compressed
- result: the agent's final output (WHAT it actually produced) — compressed
- flow: execution tree outline showing agent path with numbering (e.g. "1:main(AGENT) > 1.1:classify(LLM) > 1.2:search(Tool)[ERR]"). Read this FIRST to understand the agent's step sequence and where errors occurred.
- tools_available: tools the agent COULD have used
- task_hints: keyword signals (needs_tool, has_format_constraint, quantitative_answer, specific_value)
- signals: structural anomaly flags from rule engine
- spans: ALL spans with COMPRESSED I/O (n=name, d=depth, k=kind, s=status, t=duration, in=input, out=output, f=flags if anomalous)

## ERROR GROUPS

### 1. TOOL FAILURES — Any issue with how tools were used?
Look at: flagged spans with ERR/TOOL_FAIL flags, tools_available vs tools actually called, tool parameters
Subcategories:
- Tool-related: Tool crashed, returned error, or was unavailable. Example: search_tool error, agent fabricated result.
- Tool Selection Errors: Wrong tool chosen. Check tools_available — was a better tool available? Example: used web_search when document_search existed.
- Tool Output Misinterpretation: Misread what a tool returned. Example: API returned error JSON, agent treated error as data.
- Formatting Errors: Tool called with malformed parameters. Example: page_down called with empty {} 6 times.
- Language-only: Agent answered with text when it SHOULD have called a tool to fetch data. ONLY flag this when the task genuinely required a data lookup or computation. Do NOT flag for how-to questions, product guidance, or process explanations — those are correctly answered with text. Check: if task_hints has "needs_tool" AND the question requires live data AND no tool spans in tree. Example: asked "how many invoices this month?" and agent guessed instead of querying.

### 2. CONTEXT & RETRIEVAL — Did the agent miss, forget, or misuse information?
Look at: sequential span patterns (same error repeated), task vs result mismatch on retrieved data
Subcategories:
- Context Handling Failures: Forgot or ignored prior context, OR repeated the same failed approach without adapting. Do NOT flag if the agent correctly handled a follow-up turn in a multi-turn conversation. Example of real failure: tool returned error explaining correct format, agent repeated same mistake.
- Poor Information Retrieval: Got WRONG info from tools/search — the data returned does not match what was asked for. Do NOT flag when: (a) the query returned no results and agent correctly said so, (b) the agent honestly said a feature/doc doesn't exist, (c) the agent provided data with a download link. Only flag when the agent retrieved INCORRECT data or answered with data about the WRONG entity. Example of real failure: searched for vendor A, returned data for vendor B.
- Incorrect Memory Usage: Referenced wrong conversation or hallucinated prior context.

### 3. PLANNING & GOALS — Did the agent stay on track and work efficiently?
Look at: task vs result (same goal?), span count, tree structure for wasted steps
Subcategories:
- Task Orchestration: Steps out of order, missed dependencies. Example: tried to summarize before downloading.
- Goal Deviation: Strayed from intended objective. Compare task vs result — agent answered a COMPLETELY DIFFERENT question. NOTE: asking a clarifying question is NOT deviation. Offering a workaround when a feature doesn't exist is NOT deviation. Refusing an off-topic query is NOT deviation. Only flag when the agent clearly answered the WRONG question despite understanding the right one. Example of real deviation: asked for vendor A's invoices, agent returned vendor B's data.
- Resource Abuse: Excessive tool calls, redundant steps. Example: same query 8 times, 20 calls when 3 suffice.

### 4. OUTPUT QUALITY — Does the final output match what was asked?
Look at: task field (requirements) vs result field (actual output), task_hints
Subcategories:
- Instruction Non-compliance: Output ignores EXPLICIT format/length/style constraints stated by the user. The agent must have clearly violated a STATED requirement. Do NOT flag when: (a) the agent gave a correct answer in a reasonable format, (b) the agent said a feature isn't available/documented (that's honesty, not non-compliance), (c) the agent provided data in a table or download link. Example of real non-compliance: user said "return JSON only", agent returned prose. Example that is NOT non-compliance: user asked "can I bulk edit?" and agent said "no documentation found for this" — that's honest.
- Incorrect Problem Identification: Agent fundamentally misunderstood WHAT the user was asking — answered a completely different question. Do NOT flag when: (a) the agent understood the question but gave an incomplete answer, (b) the agent correctly refused an off-topic/adversarial query, (c) the agent asked for clarification on an ambiguous input like a filename or abbreviation. Example of real misidentification: user asked about deleting policies, agent explained how to create new ones.

### 5. INFRASTRUCTURE — Any system-level failures?
Look at: flagged spans with ERR flags, HTTP status codes in span output
Subcategories:
- Environment Setup Errors: Missing config, API keys, dependencies.
- Resource Not Found: 404, file not found, invalid URL.
- Authentication Errors: 401/403, expired token.
- Timeout Issues: Operations exceeded time limits.
- Service Errors: 5xx errors, service unavailable.

## CRITICAL — WHAT IS NOT AN ERROR
Before flagging anything, check these. Violations of this list are FALSE POSITIVES:

1. **Disambiguation / clarification is CORRECT behavior.** If the user's query is ambiguous (matches multiple entities, vendors, locations, GL accounts, etc.) and the agent asks "did you mean X or Y?", that is GOOD — do NOT flag as Goal Deviation, Incorrect Problem Identification, or Language-only. The agent is being responsible.

2. **Text-only answers are fine when no tool is needed.** Questions like "how do I do X?", "what does Y mean?", "who should I contact?", "how do I log out?" are product/process questions. They don't need a tool call — a knowledgeable text answer is correct. Only flag Language-only when the task GENUINELY required data lookup or computation that ONLY a tool could provide.

3. **Judge the FINAL outcome, not intermediate errors.** If the agent hit a SQL error or tool failure but RECOVERED and delivered a correct final answer, that is NOT a medium/high issue. At most it's a LOW confidence "fragility" signal. The user got what they asked for.

4. **Multi-turn context: short follow-ups are normal.** Queries like "try last year", "partial match is okay", "yes please", "yes", "thank you" are follow-ups or conversation closers. If the agent handled them correctly in context, don't flag them. Polite sign-offs ("thank you" → "you're welcome") are NEVER errors.

5. **"No results found" is often correct.** If the agent queried the database and genuinely found no matching data, reporting "no results" IS the correct answer. Don't flag this as Poor Information Retrieval unless there's evidence the query itself was wrong.

6. **"Feature not documented / not supported" is an honest answer, not a failure.** If the user asks about a feature that doesn't exist (e.g. "can I unmerge a vendor?", "is there a bulk posting date?", "can I export by item?") and the agent honestly says "I couldn't find documentation for this" or "this feature is not available" — that is the CORRECT answer. Do NOT flag as Instruction Non-compliance, Poor Information Retrieval, or Goal Deviation. The agent cannot invent features that don't exist.

7. **Correctly refusing off-topic or adversarial queries is CORRECT.** If the user asks something completely outside the agent's domain (e.g. recipes, car wash advice, personal questions) or attempts prompt injection ("ignore all previous instructions..."), the agent SHOULD refuse. Do NOT flag refusals as Incorrect Problem Identification or Goal Deviation. The agent is enforcing its guardrails correctly.

8. **Providing data with a download link IS providing data.** If the agent generated a CSV/report and provided a download link, that IS actionable output. Do NOT flag as "no actual data shown" or "claims generated but not shown" — the user can access the full dataset via the link.

## RULES
- Evaluate ALL 5 groups for each trace. Do not skip any.
- MULTIPLE subcategories per group are possible but uncommon. Most traces have 0-2 real issues. A trace with 5+ issues is suspicious — re-check for false positives.
- Be PRECISE, not trigger-happy. A false positive wastes developer time investigating non-issues. Only flag issues you're genuinely confident about (H or M).
- For groups 2-4: ALWAYS compare task vs result fields.
- Clean traces are common and expected — empty issues array is the right call for a working trace.
- Only report confidence H (high) or M (medium). Drop anything you'd rate L (low) — it's probably not a real issue.

"""

BATCH_TEMPLATE = """## TRACES
{traces_json}

For each trace, evaluate all 5 groups. If a group has issues, output the SPECIFIC subcategory (e.g. "Tool-related", "Context Handling Failures", "Goal Deviation" — NOT the group name like "Tool Failures").

Return JSON with this EXACT format:
{{"<trace_id>": {{
  "issues": [{{"cat":"<specific subcategory>","conf":"H|M|L","brief":"<10 words max>"}}],
  "key_moments": ["<quote 1>","<quote 2>","<quote 3>"]
}}}}

IMPORTANT:
- "cat" must be one of: Tool-related, Tool Selection Errors, Tool Output Misinterpretation, Formatting Errors, Language-only, Context Handling Failures, Poor Information Retrieval, Incorrect Memory Usage, Task Orchestration, Goal Deviation, Resource Abuse, Instruction Non-compliance, Incorrect Problem Identification, Environment Setup Errors, Resource Not Found, Authentication Errors, Timeout Issues, Service Errors
- "conf": H=high confidence, M=medium. Do NOT include L (low) — drop uncertain findings entirely
- key_moments: 3-5 quotes copied VERBATIM from the trace (task, spans, result). Pick the highlights — the moments that tell the story of what happened. Include user request, key tool outputs, agent decisions, final response. Like a sports replay — the key plays.
- If trace is clean: empty issues array, still provide key_moments showing the successful flow.
- Every trace_id MUST be a key."""


def build_prompt(compressed_traces):
    """Build the full prompt from a list of compress_v2 outputs."""
    traces_json = json.dumps(compressed_traces, separators=(",", ":"))
    return CACHED_PREFIX + BATCH_TEMPLATE.format(traces_json=traces_json)
