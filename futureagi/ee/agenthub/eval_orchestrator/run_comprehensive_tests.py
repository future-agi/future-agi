"""
EvalOrchestrator — Comprehensive Resource Selection Tests
==========================================================
Every test case has ALL resources available (KB + MCP + feedback).
The goal: observe whether the agent selectively uses only what it needs,
or blindly calls every tool.

Produces a qualitative report at the end analyzing tool selection decisions.

Prerequisites:
    1. MCP server running:   cd test_mcp_server && node server.mjs
    2. Gateway configured:   text-quality server in agentcc-gateway/config.yaml
    3. AgentccOrgConfig:       MCP enabled for ORG_ID

Run:
    docker exec backend bash -c \\
        "cd /app/backend && \\
         AGENTCC_GATEWAY_URL=http://agentcc-gateway:8090 \\
         AGENTCC_ADMIN_TOKEN=agentcc-admin-secret \\
         PYTHONPATH=/app/backend \\
         python agentic_eval/agenthub/eval_orchestrator/run_comprehensive_tests.py"
"""

import json
import logging
import os
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ── Django bootstrap ─────────────────────────────────────────────────────────
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()
# ─────────────────────────────────────────────────────────────────────────────

from ee.agenthub.eval_orchestrator.orchestrator import (
    EvalConfig,
    EvalOrchestrator,
)
from ee.agenthub.eval_orchestrator.scout import EvalScout
from ee.agenthub.eval_orchestrator.utils import (
    build_input_summary,
    gather_available_resources,
)

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

LOG_FILE = os.path.join(os.path.dirname(__file__), "comprehensive_test_results.txt")

_fmt = logging.Formatter("%(message)s")

_fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(_fmt)

_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.DEBUG)
_ch.setFormatter(_fmt)

log = logging.getLogger("comprehensive_tests")
log.setLevel(logging.DEBUG)
log.propagate = False
log.handlers = []
log.addHandler(_fh)
log.addHandler(_ch)

for _noisy in ("structlog", "clickhouse_driver", "urllib3", "httpcore", "httpx",
               "django", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def _banner(title, char="═", width=72):
    pad = max(0, width - len(title) - 4)
    return f"{char * (pad // 2)}  {title}  {char * (pad - pad // 2)}"


def _section(title):
    return f"\n┌─ {title} {'─' * max(0, 68 - len(title))}┐"


def _wrap(text, prefix="│  ", width=70):
    lines = []
    for paragraph in str(text).splitlines():
        if not paragraph.strip():
            lines.append(f"{prefix}")
        else:
            for chunk in textwrap.wrap(paragraph, width=width):
                lines.append(f"{prefix}{chunk}")
    return "\n".join(lines) if lines else f"{prefix}(empty)"


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

ORG_ID  = "8f22b8a3-f2c3-418e-880d-d6a72bf2b334"

# Spans
SPAN_BILLING_APOLOGY   = "52f9f7958b644085"  # "I apologize for the duplicate charge…"
SPAN_ESCALATION        = "fc26fb48b68a4f07"  # "I completely understand… let me escalate…"
SPAN_CLASSIFIER        = "e575b0a2a3004d5b"  # gpt-3.5: "Category: account, Priority: medium"
SPAN_CHAIN_RETRIEVER   = "63a00480667449ec"  # chain: retriever + LLM combo

# Traces
TRACE_APP_CRASH        = "012cd701-eee7-4f48-afb5-8f4013aa844f"
TRACE_BILLING          = "acfd4b13-f169-4864-aff1-125fea18e594"

# Session
SESSION_PHOTO_UPLOAD   = "e6f1003f-b750-4174-bb93-149e88f21b75"

# KB — "kn" has customer service policy docs
KB_POLICY = "38284dd6-4d22-4728-abdd-590be1d3b7e8"

# Eval template for comprehensive tests (we create this)
EVAL_TEMPLATE_COMPREHENSIVE = "11111111-1111-1111-1111-111111111111"


# ─────────────────────────────────────────────────────────────────────────────
# Result tracking
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ComprehensiveResult:
    name: str
    scope: str
    source_id: str
    criteria: str
    choices: list
    # What was available
    available_tools: list  # ["kb", "mcp", "feedback"]
    # What the agent actually used
    tools_used: list
    tools_ignored: list
    # Expected tool usage
    expected_tools: list
    # Results
    result: Optional[str] = None
    confidence: float = 0.0
    explanation: str = ""
    turns: int = 0
    elapsed_s: float = 0.0
    scout_complexity: str = ""
    scout_model: str = ""
    # Qualitative assessment
    tool_selection_correct: Optional[bool] = None
    tool_selection_notes: str = ""
    error: str = ""


_results: list[ComprehensiveResult] = []


# ─────────────────────────────────────────────────────────────────────────────
# Core runner
# ─────────────────────────────────────────────────────────────────────────────

def run_case(
    name: str,
    input_scope: str,
    source_id: str,
    criteria: str,
    choices: list,
    expected_tools: list,
    kb_id: str = KB_POLICY,
    eval_template_id: str = EVAL_TEMPLATE_COMPREHENSIVE,
) -> ComprehensiveResult:
    """
    Run one test case with ALL resources available.
    KB, MCP, and feedback are always provided — we observe what the agent picks.
    """

    W = 72
    log.info("\n\n" + "█" * W)
    log.info(_banner(name, "█"))
    log.info("█" * W)
    log.info(f"  Scope      : {input_scope}")
    log.info(f"  Source ID  : {source_id}")
    log.info(f"  Choices    : {choices}")
    log.info(f"  Expected   : {expected_tools}")
    log.info(_section("EVALUATION CRITERIA"))
    log.info(_wrap(criteria))
    log.info("└" + "─" * 70 + "┘")

    t0 = time.time()
    try:
        # Step 1: gather — always pass KB + eval_template so everything is available
        available_resources = gather_available_resources(
            organization_id=ORG_ID,
            eval_template_id=eval_template_id,
            kb_id=kb_id,
        )

        available_tools = []
        if available_resources.get("knowledge_bases"):
            available_tools.append("kb")
        if available_resources.get("mcp_tools"):
            available_tools.append("mcp")
        if available_resources.get("feedback_count", 0) > 0:
            available_tools.append("feedback")

        log.info(_section("AVAILABLE RESOURCES"))
        log.info(f"│  KB:       {len(available_resources.get('knowledge_bases', []))} knowledge base(s)")
        log.info(f"│  MCP:      {len(available_resources.get('mcp_tools', []))} tool(s)")
        log.info(f"│  Feedback: {available_resources.get('feedback_count', 0)} example(s)")
        log.info(f"│  → Available: {available_tools}")
        log.info(f"│  → Expected:  {expected_tools}")
        log.info("└" + "─" * 70 + "┘")

        # Step 2: EvalConfig
        config = EvalConfig(
            criteria=criteria,
            input_scope=input_scope,
            source_id=source_id,
            choices=choices if choices else None,
            eval_template_id=eval_template_id,
            kb_id=kb_id,
            available_resources=available_resources,
        )

        # Step 3: build_input_summary
        input_summary = build_input_summary(input_scope, source_id)

        log.info(_section("STAGE 1 · INPUT FROM DATABASE"))
        log.info(_wrap(input_summary[:1500]))
        log.info("└" + "─" * 70 + "┘")

        # Step 4: Scout
        scout = EvalScout()
        scout_result = scout.run(
            criteria=criteria,
            input_summary=input_summary,
            available_resources=available_resources,
        )

        log.info(_section("STAGE 2 · SCOUT ANALYSIS"))
        log.info(f"│  complexity      : {scout_result.get('complexity', '?')}")
        log.info(f"│  model           : {scout_result.get('recommended_model', '?')}")
        log.info(f"│  suggested_tools : {scout_result.get('suggested_tools', [])}")
        log.info(f"│  reasoning       :")
        log.info(_wrap(scout_result.get("reasoning", ""), prefix="│    "))
        log.info("└" + "─" * 70 + "┘")

        scout_result["available_resources"] = available_resources

        # Step 5: Orchestrator
        orchestrator = EvalOrchestrator(eval_config=config, scout_brief=scout_result)
        output = orchestrator.run()

        elapsed = time.time() - t0
        eval_result = output.get("result", {})
        resources_used = output.get("resources_used", [])

        # Determine what was used vs ignored
        tools_used = resources_used
        tools_ignored = [t for t in available_tools if t not in tools_used]

        # Assess tool selection correctness
        expected_set = set(expected_tools)
        used_set = set(tools_used)
        selection_correct = used_set == expected_set
        if not selection_correct:
            extra = used_set - expected_set
            missing = expected_set - used_set
            notes_parts = []
            if extra:
                notes_parts.append(f"Unexpectedly used: {sorted(extra)}")
            if missing:
                notes_parts.append(f"Expected but skipped: {sorted(missing)}")
            selection_notes = "; ".join(notes_parts)
        else:
            selection_notes = "Correct — used exactly what was needed"

        log.info(_section("STAGE 3 · RESULT"))
        log.info(f"│  result     : {eval_result.get('result')}")
        log.info(f"│  confidence : {eval_result.get('confidence')}")
        log.info(f"│  turns      : {output.get('orchestrator_turns')}")
        log.info(f"│  elapsed    : {elapsed:.1f}s")
        log.info(f"│")
        log.info(f"│  TOOL SELECTION:")
        log.info(f"│    available : {available_tools}")
        log.info(f"│    expected  : {expected_tools}")
        log.info(f"│    used      : {tools_used}")
        log.info(f"│    ignored   : {tools_ignored}")
        log.info(f"│    correct?  : {'✓ YES' if selection_correct else '✗ NO'}")
        if not selection_correct:
            log.info(f"│    notes     : {selection_notes}")
        log.info(f"│")
        log.info(f"│  explanation:")
        log.info(_wrap(eval_result.get("explanation", ""), prefix="│    "))
        log.info("└" + "─" * 70 + "┘")

        result = ComprehensiveResult(
            name=name,
            scope=input_scope,
            source_id=source_id,
            criteria=criteria,
            choices=choices,
            available_tools=available_tools,
            tools_used=tools_used,
            tools_ignored=tools_ignored,
            expected_tools=expected_tools,
            result=eval_result.get("result"),
            confidence=eval_result.get("confidence", 0),
            explanation=eval_result.get("explanation", ""),
            turns=output.get("orchestrator_turns", 0),
            elapsed_s=elapsed,
            scout_complexity=scout_result.get("complexity", ""),
            scout_model=scout_result.get("recommended_model", ""),
            tool_selection_correct=selection_correct,
            tool_selection_notes=selection_notes,
        )
        _results.append(result)
        return result

    except Exception as e:
        elapsed = time.time() - t0
        log.info(f"\n│  ✗ ERROR: {e}")
        import traceback; traceback.print_exc(file=sys.stdout)
        result = ComprehensiveResult(
            name=name,
            scope=input_scope,
            source_id=source_id,
            criteria=criteria,
            choices=choices,
            available_tools=[],
            tools_used=[],
            tools_ignored=[],
            expected_tools=expected_tools,
            elapsed_s=elapsed,
            error=str(e),
        )
        _results.append(result)
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Test cases
# ─────────────────────────────────────────────────────────────────────────────

def define_cases():
    return [
        # ── Case 1: Should use NOTHING extra ──────────────────────────────
        # Simple binary check — the answer is obvious from the data alone.
        # Agent should NOT use KB, MCP, or feedback.
        dict(
            name="C1 | span | simple binary — is it an apology?",
            input_scope="span",
            source_id=SPAN_BILLING_APOLOGY,
            choices=["Yes", "No"],
            criteria=(
                "Does the agent's response contain an apology to the customer? "
                "Answer Yes if the response includes any form of apology "
                "(e.g., 'I apologize', 'sorry', 'we regret'), No otherwise."
            ),
            expected_tools=[],
        ),

        # ── Case 2: Should use ONLY KB ────────────────────────────────────
        # Policy compliance check — needs KB to know the actual policy.
        # MCP grammar tools and feedback are irrelevant.
        dict(
            name="C2 | span | KB only — policy compliance check",
            input_scope="span",
            source_id=SPAN_BILLING_APOLOGY,
            choices=["Passed", "Failed"],
            criteria=(
                "Does the agent's response follow the company's documented refund policy? "
                "Search the knowledge base to find the refund policy, then check whether "
                "the response aligns with it (correct timeline, correct process, "
                "correct customer communication guidelines)."
            ),
            expected_tools=["kb"],
        ),

        # ── Case 3: Should use ONLY MCP ──────────────────────────────────
        # Grammar quality — needs MCP grammar_checker tool.
        # KB and feedback are irrelevant.
        dict(
            name="C3 | span | MCP only — grammar quality",
            input_scope="span",
            source_id=SPAN_BILLING_APOLOGY,
            choices=["Passed", "Failed"],
            criteria=(
                "Is the agent's response free of grammar errors? "
                "Use the grammar checking tool to analyze the response text. "
                "Pass if quality is 'good' or 'excellent', fail otherwise."
            ),
            expected_tools=["mcp"],
        ),

        # ── Case 4: Should use ONLY feedback ─────────────────────────────
        # Subjective quality — needs feedback to calibrate scoring.
        # KB and MCP tools are irrelevant for tone calibration.
        dict(
            name="C4 | span | feedback only — calibrated tone check",
            input_scope="span",
            source_id=SPAN_ESCALATION,
            choices=["Passed", "Failed"],
            criteria=(
                "Does this response meet the expected standard of empathy and "
                "professionalism for customer service? Use the available human feedback "
                "examples to calibrate your judgment — match the quality bar set by "
                "the human labelers."
            ),
            expected_tools=["feedback"],
        ),

        # ── Case 5: Should use KB + MCP ──────────────────────────────────
        # Policy compliance AND readability — needs KB for policy, MCP for readability.
        dict(
            name="C5 | span | KB + MCP — policy compliant AND readable",
            input_scope="span",
            source_id=SPAN_BILLING_APOLOGY,
            choices=["Passed", "Failed"],
            criteria=(
                "Evaluate this response on TWO dimensions: "
                "(1) Does it follow the company's refund policy from the knowledge base? "
                "(2) Is it written at an appropriate readability level for customers? "
                "Use the readability analysis tool to check — Flesch reading ease should "
                "be above 50. Pass only if BOTH criteria are met."
            ),
            expected_tools=["kb", "mcp"],
        ),

        # ── Case 6: Should use feedback + MCP ────────────────────────────
        # Grammar + calibration — MCP for objective grammar score, feedback for subjective calibration.
        dict(
            name="C6 | span | feedback + MCP — grammar with human calibration",
            input_scope="span",
            source_id=SPAN_CLASSIFIER,
            choices=["Passed", "Failed"],
            criteria=(
                "Evaluate the quality of this classifier output. "
                "First, use the grammar checking tool to verify the output is well-formatted. "
                "Then, check the available human feedback examples to understand what quality "
                "bar has been set for similar outputs. Pass if both the technical formatting "
                "and the human-calibrated quality standard are met."
            ),
            expected_tools=["feedback", "mcp"],
        ),

        # ── Case 7: Should use ALL three ─────────────────────────────────
        # The kitchen sink — policy + grammar + calibration.
        dict(
            name="C7 | trace | ALL tools — comprehensive quality audit",
            input_scope="trace",
            source_id=TRACE_BILLING,
            choices=["Passed", "Failed"],
            criteria=(
                "Perform a comprehensive quality audit of this customer service trace: "
                "(1) Check the knowledge base to verify all stated policies and procedures are accurate. "
                "(2) Use the grammar and readability tools to verify the responses are well-written "
                "and accessible to a general audience. "
                "(3) Check human feedback examples to calibrate your overall quality judgment. "
                "Pass only if the trace meets all three standards."
            ),
            expected_tools=["kb", "mcp", "feedback"],
        ),

        # ── Case 8: Nothing needed — complex but self-contained ──────────
        # Session analysis — complex task but all info is in the data.
        # Agent should NOT use external tools despite their availability.
        dict(
            name="C8 | session | NO tools — context retention analysis",
            input_scope="session",
            source_id=SESSION_PHOTO_UPLOAD,
            choices=["Passed", "Failed"],
            criteria=(
                "Did the agent maintain context across all turns of this multi-turn session? "
                "Check whether the agent remembered what was discussed in earlier turns, "
                "avoided repeating solutions already tried, and built on conversation history. "
                "This is purely about conversational coherence — no external references needed."
            ),
            expected_tools=[],
        ),

        # ── Case 9: Scorer mode + MCP ────────────────────────────────────
        # Numeric scoring with MCP tool for objective measurement.
        dict(
            name="C9 | span | scorer + MCP — readability score",
            input_scope="span",
            source_id=SPAN_CHAIN_RETRIEVER,
            choices=None,  # scorer mode: 0.0 - 1.0
            criteria=(
                "Score how readable this response is for a general audience. "
                "Use the readability analysis tool to get the Flesch reading ease score, "
                "then map it to a 0.0-1.0 scale where 0.0 = very difficult to read "
                "and 1.0 = very easy to read."
            ),
            expected_tools=["mcp"],
        ),

        # ── Case 10: KB + feedback, MCP irrelevant ───────────────────────
        # Needs policy context and human calibration, but grammar tools are useless.
        dict(
            name="C10 | span | KB + feedback — policy with calibration",
            input_scope="span",
            source_id=SPAN_ESCALATION,
            choices=["Passed", "Failed"],
            criteria=(
                "Does this escalation response follow the company's escalation procedures "
                "documented in the knowledge base? Search the KB for escalation guidelines, "
                "then check the human feedback examples to calibrate your judgment on what "
                "constitutes proper escalation handling. Pass only if the response matches "
                "both the documented procedure and the quality bar from human feedback."
            ),
            expected_tools=["kb", "feedback"],
        ),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Qualitative report
# ─────────────────────────────────────────────────────────────────────────────

def print_qualitative_report(results: list[ComprehensiveResult]):
    W = 72
    log.info("\n\n" + "━" * W)
    log.info(_banner("QUALITATIVE REPORT", "━"))
    log.info("━" * W)

    total = len(results)
    errored = [r for r in results if r.error]
    completed = [r for r in results if not r.error]
    correct_selection = [r for r in completed if r.tool_selection_correct]
    incorrect_selection = [r for r in completed if not r.tool_selection_correct]

    # ── Overview ──────────────────────────────────────────────────────────
    log.info(f"\n  Total cases       : {total}")
    log.info(f"  Completed         : {len(completed)}")
    log.info(f"  Errored           : {len(errored)}")
    log.info(f"  Correct selection : {len(correct_selection)}/{len(completed)}")
    accuracy = len(correct_selection) / len(completed) * 100 if completed else 0
    log.info(f"  Selection accuracy: {accuracy:.0f}%")

    # ── Per-case summary ─────────────────────────────────────────────────
    log.info(f"\n  {'Case':<50} {'Result':<8} {'Sel':>5} {'Turns':>5} {'Time':>6}")
    log.info(f"  {'─'*50} {'─'*8} {'─'*5} {'─'*5} {'─'*6}")
    for r in results:
        res = str(r.result)[:8] if r.result else ("ERR" if r.error else "—")
        sel = "✓" if r.tool_selection_correct else ("ERR" if r.error else "✗")
        log.info(
            f"  {r.name[:50]:<50} {res:<8} {sel:>5} {r.turns:>5} {r.elapsed_s:>5.1f}s"
        )

    # ── Tool selection detail ────────────────────────────────────────────
    log.info(f"\n\n  {'─'*70}")
    log.info(f"  TOOL SELECTION ANALYSIS")
    log.info(f"  {'─'*70}")

    log.info(f"\n  {'Case':<35} {'Available':<20} {'Expected':<20} {'Used':<20} {'Match'}")
    log.info(f"  {'─'*35} {'─'*20} {'─'*20} {'─'*20} {'─'*5}")
    for r in completed:
        avail = ",".join(r.available_tools) or "none"
        exp = ",".join(r.expected_tools) or "none"
        used = ",".join(r.tools_used) or "none"
        match = "✓" if r.tool_selection_correct else "✗"
        log.info(f"  {r.name[:35]:<35} {avail:<20} {exp:<20} {used:<20} {match}")

    # ── Incorrect selections detail ──────────────────────────────────────
    if incorrect_selection:
        log.info(f"\n\n  {'─'*70}")
        log.info(f"  INCORRECT SELECTIONS — DETAILS")
        log.info(f"  {'─'*70}")
        for r in incorrect_selection:
            log.info(f"\n  [{r.name}]")
            log.info(f"    Expected : {r.expected_tools}")
            log.info(f"    Actual   : {r.tools_used}")
            log.info(f"    Notes    : {r.tool_selection_notes}")
            log.info(f"    Criteria : {r.criteria[:150]}...")

    # ── Tool usage stats ─────────────────────────────────────────────────
    log.info(f"\n\n  {'─'*70}")
    log.info(f"  TOOL USAGE STATISTICS")
    log.info(f"  {'─'*70}")

    tool_names = ["kb", "mcp", "feedback"]
    for tool in tool_names:
        times_available = sum(1 for r in completed if tool in r.available_tools)
        times_expected = sum(1 for r in completed if tool in r.expected_tools)
        times_used = sum(1 for r in completed if tool in r.tools_used)
        times_correctly_used = sum(
            1 for r in completed
            if tool in r.tools_used and tool in r.expected_tools
        )
        times_correctly_skipped = sum(
            1 for r in completed
            if tool not in r.tools_used and tool not in r.expected_tools
        )
        times_over_used = sum(
            1 for r in completed
            if tool in r.tools_used and tool not in r.expected_tools
        )
        times_under_used = sum(
            1 for r in completed
            if tool not in r.tools_used and tool in r.expected_tools
        )

        log.info(f"\n  {tool.upper()}")
        log.info(f"    Available in  : {times_available}/{len(completed)} cases")
        log.info(f"    Expected in   : {times_expected}/{len(completed)} cases")
        log.info(f"    Actually used : {times_used}/{len(completed)} cases")
        log.info(f"    Correct use   : {times_correctly_used} (used when needed)")
        log.info(f"    Correct skip  : {times_correctly_skipped} (skipped when not needed)")
        log.info(f"    Over-used     : {times_over_used} (used when NOT needed)")
        log.info(f"    Under-used    : {times_under_used} (skipped when needed)")

    # ── Qualitative verdicts ─────────────────────────────────────────────
    log.info(f"\n\n  {'─'*70}")
    log.info(f"  QUALITATIVE VERDICTS")
    log.info(f"  {'─'*70}")

    for r in completed:
        log.info(f"\n  [{r.name}]")
        log.info(f"    Result     : {r.result} (confidence: {r.confidence})")
        log.info(f"    Selection  : {'✓ CORRECT' if r.tool_selection_correct else '✗ INCORRECT'}")
        if not r.tool_selection_correct:
            log.info(f"    Issue      : {r.tool_selection_notes}")
        log.info(f"    Explanation: {r.explanation[:200]}...")

    # ── Final grade ──────────────────────────────────────────────────────
    log.info(f"\n\n  {'═'*70}")
    if accuracy >= 90:
        grade = "A — Excellent tool selection"
    elif accuracy >= 70:
        grade = "B — Good, minor over/under-use"
    elif accuracy >= 50:
        grade = "C — Fair, needs prompt tuning"
    else:
        grade = "D — Poor, significant selection issues"

    log.info(f"  OVERALL GRADE: {grade}")
    log.info(f"  Selection accuracy: {accuracy:.0f}% ({len(correct_selection)}/{len(completed)})")
    log.info(f"  {'═'*70}")

    if errored:
        log.info("\n  ERRORS:")
        for r in errored:
            log.info(f"    [{r.name}] {r.error}")

    log.info(f"\n  Log file: {LOG_FILE}")
    log.info("━" * W + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--filter", default="", help="Only run cases matching this substring")
    args = parser.parse_args()

    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    W = 72
    log.info("=" * W)
    log.info(_banner("Comprehensive Resource Selection Tests", "="))
    log.info("=" * W)
    log.info(f"  Started : {start_ts}")
    log.info(f"  Org     : {ORG_ID}")
    log.info(f"  KB      : {KB_POLICY}")
    log.info(f"  Template: {EVAL_TEMPLATE_COMPREHENSIVE}")
    log.info(f"  Log     : {LOG_FILE}")
    log.info("=" * W)

    cases = define_cases()
    if args.filter:
        cases = [c for c in cases if args.filter.lower() in c.get("name", "").lower()]
        log.info(f"\n  Running {len(cases)} filtered cases (filter: '{args.filter}') …\n")
    else:
        log.info(f"\n  Running {len(cases)} test cases …\n")

    for case_def in cases:
        run_case(**case_def)

    print_qualitative_report(_results)


if __name__ == "__main__":
    main()
