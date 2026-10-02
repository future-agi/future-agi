"""
EvalOrchestrator + EvalScout — Real Integration Tests
======================================================
Uses ACTUAL data from the local Postgres database:
  - Real spans (LLM, agent, evaluator, chain, retriever)
  - Real traces (multi-span conversations)
  - Real sessions (multi-turn customer service dialogues)
  - Real Turing API calls (Scout + Orchestrator loop)

Test cases range from SIMPLE (single span, clear criteria) to HARD
(full session with multi-turn context, ambiguous criteria).

Run from Docker:

    docker exec backend bash -c \
        "cd /app/backend && PYTHONPATH=/app/backend \
         python agentic_eval/agenthub/eval_orchestrator/run_real_integration_tests.py"

Logs are written to:
    agentic_eval/agenthub/eval_orchestrator/real_test_results.txt
"""

import json
import logging
import os
import sys
import textwrap
import time
from dataclasses import dataclass
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
# Logging — console (INFO) + full-detail file (DEBUG)
# ─────────────────────────────────────────────────────────────────────────────

LOG_FILE = os.path.join(os.path.dirname(__file__), "real_test_results.txt")

# NOTE: logging.basicConfig() is a no-op if the root logger already has handlers
# (Django's setup() adds them first). We bypass this by configuring our logger
# directly and setting propagate=False so Django's handlers don't intercept.
# File mode is set in _setup_logging() based on --filter flag.

_fmt = logging.Formatter("%(message)s")

log = logging.getLogger("real_tests")
log.setLevel(logging.DEBUG)
log.propagate = False          # don't forward to Django's root logger


def _setup_logging(append: bool = False):
    """Configure file + console handlers. append=True when --filter is used."""
    log.handlers = []              # clear any previously attached handlers

    _fh = logging.FileHandler(LOG_FILE, mode="a" if append else "w", encoding="utf-8")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(_fmt)
    log.addHandler(_fh)

    _ch = logging.StreamHandler(sys.stdout)
    _ch.setLevel(logging.DEBUG)
    _ch.setFormatter(_fmt)
    log.addHandler(_ch)

# Suppress noisy third-party loggers
for _noisy in ("structlog", "clickhouse_driver", "urllib3", "httpcore", "httpx",
               "django", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def _banner(title: str, char: str = "═", width: int = 72) -> str:
    pad = max(0, width - len(title) - 4)
    left = pad // 2
    right = pad - left
    return f"{char * left}  {title}  {char * right}"


def _section(title: str) -> str:
    return f"\n┌─ {title} {'─' * max(0, 68 - len(title))}┐"


def _wrap(text: str, prefix: str = "│  ", width: int = 70) -> str:
    lines = []
    for paragraph in str(text).splitlines():
        if not paragraph.strip():
            lines.append(f"{prefix}")
        else:
            for chunk in textwrap.wrap(paragraph, width=width):
                lines.append(f"{prefix}{chunk}")
    return "\n".join(lines) if lines else f"{prefix}(empty)"


def _json_block(obj, prefix: str = "│  ", max_chars: int = 4000) -> str:
    """Pretty-print a JSON-serialisable object, capped at max_chars."""
    try:
        text = json.dumps(obj, indent=2, default=str)
    except Exception:
        text = str(obj)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... [truncated — {len(text)} chars total]"
    return "\n".join(f"{prefix}{line}" for line in text.splitlines())


# ─────────────────────────────────────────────────────────────────────────────
# Real DB constants (from local Postgres)
# ─────────────────────────────────────────────────────────────────────────────

ORG_ID   = "8f22b8a3-f2c3-418e-880d-d6a72bf2b334"
WS_ID    = "1cf34c23-3c50-445d-98e6-acaa205ddba3"
PROJ_ID  = "40fc392d-cdbf-42a0-aaa9-8114b2e5474d"

# Individual spans (customer service dataset)
SPAN_CLAUDE_BILLING_APOLOGY   = "52f9f7958b644085"   # claude-3-opus: "I apologize for the duplicate charge…"
SPAN_CLAUDE_ESCALATION        = "fc26fb48b68a4f07"   # claude-3-opus: "I completely understand your concern. Let me escalate…"
SPAN_CLAUDE_SONNET_CLOSING    = "5d93982710754eec"   # claude-3-sonnet: "You're very welcome! Anything else I can help you with today?"
SPAN_GPT_CLASSIFIER           = "e575b0a2a3004d5b"   # gpt-3.5-turbo: "Category: account, Priority: medium"
SPAN_AGENT_BILLING_RESOLUTION = "d13831b44e524546"   # agent span: full billing dispute resolution
SPAN_AGENT_ESCALATE           = "4a9e06bd7c2c449e"   # agent span: "I completely understand… let me escalate…"
SPAN_EVALUATOR_HIGH_QUALITY   = "d59d4b55956b4220"   # evaluator span
SPAN_CHAIN_RETRIEVER_COMBO    = "63a00480667449ec"   # chain: retriever + LLM

# Traces (one turn of a customer conversation)
TRACE_APP_CRASH_TURN1         = "012cd701-eee7-4f48-afb5-8f4013aa844f"
TRACE_APP_CRASH_TURN2         = "b87ebe03-09fb-41f6-94dd-ba3d97d7bdca"
TRACE_BILLING_DISPUTE         = "acfd4b13-f169-4864-aff1-125fea18e594"

# Session — photo upload issue, 5 turns
SESSION_PHOTO_UPLOAD_ISSUE    = "e6f1003f-b750-4174-bb93-149e88f21b75"

DUMMY_EVAL_TEMPLATE_ID        = "ca6b1561-aea8-4b69-b719-208cbbdb8224"

# ── Dataset rows (model_hub_row) ─────────────────────────────────────────────
# TEXT: "hi my name is wow fuck you okay you are amaze balls"
ROW_TEXT_TOXIC                = "a97cd808-3e03-4602-8901-457ae95a3cd6"
DATASET_TOXIC                 = "e28c1e8d-dab0-4745-91f1-f81a4fe40a58"

# TEXT: benign travel story — "On our family trip across the deserts…"
ROW_TEXT_BENIGN               = "895005a9-2ce3-4192-b012-95c7607fc29d"
DATASET_BENIGN                = "9229f0b8-ba16-4cf1-907f-b9ce8dbcb29c"

# AUDIO: S3 audio file — severe electrical hum / noise (audio quality test)
# Transcript: "my name is rishav and my birthday is on 21st june 1996"
ROW_AUDIO_QUALITY             = "187d5f1c-2f0e-4459-a205-02e93af3a277"
DATASET_AUDIO_QUALITY         = "4142a17f-716c-4f7e-83f3-73ef2983b7f1"

# AUDIO: S3 audio — TTS digit pronunciation test (dcs eval)
ROW_AUDIO_TTS                 = "a2a0fb44-d8bb-4316-998e-8812bf38d9d5"
DATASET_AUDIO_TTS             = "727c2be7-9375-4d90-b181-a19689085839"

# IMAGE: "Give me something with a Renaissance manuscript vibe." + generated image URL
ROW_IMAGE_FONT                = "4d3ee3b6-0c42-461c-a16d-d913db9c688f"
DATASET_IMAGE_FONT            = "f7ee3669-b893-450c-a372-c0884b0eb941"

# ── Multimodal dataset (ff0f3091) — has image, audio, document columns ──────
DATASET_MULTIMODAL            = "ff0f3091-6203-4876-ba9f-6f74f284e580"
ROW_MULTIMODAL                = "e1743c11-f8a9-4484-9c76-76c0235c27e9"
CELL_MM_IMAGE                 = "680ed169-510c-4f4e-9e45-9bb966f0893e"  # Column 2 [image]
CELL_MM_AUDIO                 = "91ae45bd-693b-4261-9489-cca41604012f"  # Column 3 [audio]
CELL_MM_PDF                   = "29350c0b-6550-40f2-aa4a-6cdeccf82afe"  # pdf [document]
CELL_MM_TEXT                  = "55c79ee0-bac2-4bb6-a505-3ae5263b15ef"  # Column 1 [text]

# ── Knowledge bases (model_hub_knowledgebasefile) ────────────────────────────
KB_KN  = "38284dd6-4d22-4728-abdd-590be1d3b7e8"   # "kn"  — 137KB, Completed
KB_YTH = "54bcbd50-ee42-4253-b4ce-34811d66a08a"   # "yth" — 137KB, Completed

# ── Eval templates with human feedback in DB ─────────────────────────────────
EVAL_TEMPLATE_TOXICITY        = "afaecc83-0bb1-4e17-ae11-30453418a613"  # 1 feedback: "Passed"
EVAL_TEMPLATE_AUDIO_QUALITY   = "3b44caa3-9be8-407c-aa3a-e4f2f17c9079"  # 1 feedback: "40"
EVAL_TEMPLATE_DCS             = "3d520df4-09d0-4a49-80ca-fe74116ed6d6"   # 1 feedback: "40"
EVAL_TEMPLATE_SCD             = "6206b61c-eef4-43c4-8160-10fec4bcd62e"   # 1 feedback: "1"


# ── Cell lookup helpers (cell IDs resolved at runtime from known rows) ────────

def _get_cell_id_from_row(row_id: str, data_type: str = None) -> str | None:
    """Look up a Cell UUID from a known Row at runtime.

    If data_type is given (e.g. "text", "audio", "image", "document"),
    returns a cell whose column matches that type. Otherwise returns the
    first cell in the row.
    """
    from model_hub.models.develop_dataset import Cell
    qs = Cell.objects.filter(row_id=row_id).select_related("column")
    if data_type:
        qs = qs.filter(column__data_type=data_type)
    cell = qs.first()
    return str(cell.id) if cell else None


def _find_pdf_cell() -> str | None:
    """Find any cell with a DOCUMENT column in the test org's datasets."""
    from model_hub.models.develop_dataset import Cell
    cell = (
        Cell.objects
        .filter(
            column__data_type="document",
            dataset__organization_id=ORG_ID,
        )
        .select_related("column", "row")
        .first()
    )
    return str(cell.id) if cell else None


# ─────────────────────────────────────────────────────────────────────────────
# Result tracking
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    name: str
    difficulty: str
    scope: str
    source_id: str
    criteria: str
    choices: list
    result: Optional[str]
    confidence: float
    explanation: str
    eval_calls: int
    orchestrator_turns: int
    elapsed_s: float
    scout_complexity: str
    scout_model: str
    token_usage: dict
    error: str = ""


_results: list[CaseResult] = []


# ─────────────────────────────────────────────────────────────────────────────
# Core runner
# ─────────────────────────────────────────────────────────────────────────────

def run_case(
    name: str,
    difficulty: str,
    input_scope: str,
    source_id: str,
    criteria: str,
    choices: list,
    kb_id: Optional[str] = None,
    eval_template_id: Optional[str] = None,
) -> CaseResult:
    """Run one full Scout → Orchestrator pipeline against real DB data.

    Follows the same step order as BE_INTEGRATION.md:
      1. gather_available_resources
      2. EvalConfig
      3. build_input_summary
      4. EvalScout
      5. EvalOrchestrator
    """

    W = 72
    log.info("\n\n" + "█" * W)
    log.info(_banner(name, "█"))
    log.info("█" * W)
    log.info(f"  Difficulty : {difficulty}")
    log.info(f"  Scope      : {input_scope}")
    log.info(f"  Source ID  : {source_id}")
    log.info(f"  Choices    : {choices}")
    log.info(_section("EVALUATION CRITERIA"))
    log.info(_wrap(criteria))
    log.info("└" + "─" * 70 + "┘")

    t0 = time.time()
    try:
        # ══════════════════════════════════════════════════════════════════
        # STEP 1: Gather available resources
        # ══════════════════════════════════════════════════════════════════
        available_resources = gather_available_resources(
            organization_id=ORG_ID,
            eval_template_id=eval_template_id,
            kb_id=kb_id,
        )

        # ══════════════════════════════════════════════════════════════════
        # STEP 2: Build EvalConfig
        # ══════════════════════════════════════════════════════════════════
        eval_config = EvalConfig(
            criteria=criteria,
            input_scope=input_scope,
            source_id=source_id,
            choices=choices,
            eval_template_id=eval_template_id,
            kb_id=kb_id,
            available_resources=available_resources,
        )

        # ══════════════════════════════════════════════════════════════════
        # STEP 3: Build input summary from DB
        # ══════════════════════════════════════════════════════════════════
        log.info(_section("STAGE 1 · INPUT FROM DATABASE"))
        log.info(f"│  Fetching {input_scope} {source_id} from Postgres …")

        try:
            input_summary = build_input_summary(scope=input_scope, source_id=source_id)
        except Exception as e:
            log.warning(f"│  ⚠  build_input_summary failed: {e}")
            input_summary = f"{input_scope.upper()}: {source_id}"

        log.info("│")
        log.info("│  ── Raw Input Summary ──────────────────────────────────────────")
        log.info(_wrap(input_summary, prefix="│  "))
        log.info("└" + "─" * 70 + "┘")

        # ══════════════════════════════════════════════════════════════════
        # STEP 4: Scout analysis
        # ══════════════════════════════════════════════════════════════════
        log.info(_section("STAGE 2 · SCOUT ANALYSIS  (Haiku)"))
        log.info("│  Running Scout on criteria + input_summary …")

        brief = EvalScout().run(
            criteria=criteria,
            input_summary=input_summary,
            available_resources=available_resources,
        )
        brief["available_resources"] = available_resources

        log.info("│")
        log.info("│  ── Scout Brief ────────────────────────────────────────────────")
        log.info(f"│  complexity              : {brief.get('complexity')}")
        log.info(f"│  recommended_model       : {brief.get('recommended_model')}")
        log.info(f"│  criteria_needs_formalize: {brief.get('criteria_needs_formalization')}")
        log.info(f"│  suggested_tools         : {brief.get('suggested_tools', [])}")
        log.info("│")
        log.info("│  reasoning:")
        log.info(_wrap(brief.get("reasoning", "(none)"), prefix="│    "))
        if brief.get("formalized_criteria"):
            log.info("│")
            log.info("│  formalized_criteria:")
            log.info(_wrap(brief.get("formalized_criteria", ""), prefix="│    "))
        log.info("└" + "─" * 70 + "┘")

        # ══════════════════════════════════════════════════════════════════
        # STEP 5: Orchestrator agentic loop
        # ══════════════════════════════════════════════════════════════════
        log.info(_section("STAGE 3 · ORCHESTRATOR AGENTIC LOOP  (Sonnet)"))

        orch = EvalOrchestrator(eval_config=eval_config, scout_brief=brief)

        # ── Wrap _call_orchestrator_llm to log every model turn ──────────
        _real_llm_call = orch._call_orchestrator_llm
        _turn_counter = [0]

        def _logged_llm_call(messages):
            _turn_counter[0] += 1
            turn_n = _turn_counter[0]

            log.info(f"│")
            log.info(f"│  ┌── Turn {turn_n} · LLM call ──────────────────────────────────────")
            log.info(f"│  │  messages in context : {len(messages)}")

            # Show the last user/tool-result message so we know what prompted this turn
            for msg in reversed(messages):
                if msg["role"] in ("user", "tool"):
                    log.info(f"│  │  last {msg['role']} message (tail):")
                    tail = str(msg.get("content", ""))[-600:]
                    log.info(_wrap(tail, prefix="│  │    "))
                    break

            content = _real_llm_call(messages)

            log.info(f"│  │")
            log.info(f"│  │  ── Model Response ────────────────────────────────────────")
            log.info(_wrap(content, prefix="│  │  "))
            log.info(f"│  └───────────────────────────────────────────────────────────")
            return content

        orch._call_orchestrator_llm = _logged_llm_call

        # ── Wrap _execute_tool to log every tool call ────────────────────
        _real_execute = orch._execute_tool

        def _logged_execute(tool_name, args):
            log.info(f"│")
            log.info(f"│  ▶ TOOL CALL  →  {tool_name}")
            log.info("│    args:")
            log.info(_json_block(args, prefix="│      ", max_chars=2000))

            result = _real_execute(tool_name, args)

            log.info(f"│    result:")
            log.info(_json_block(result, prefix="│      ", max_chars=3000))
            return result

        orch._execute_tool = _logged_execute

        orch_result = orch.run()

        elapsed = time.time() - t0
        eval_result = orch_result["result"]

        # ── Final result ─────────────────────────────────────────────────
        log.info(f"│")
        log.info("└" + "─" * 70 + "┘")
        log.info(_section("RESULT"))
        log.info(f"│  result           : {eval_result.get('result')}")
        log.info(f"│  confidence       : {eval_result.get('confidence', 0):.2f}")
        log.info(f"│  orchestrator_turns: {orch_result.get('orchestrator_turns')}")
        log.info(f"│  eval_calls       : {orch_result.get('eval_calls')}")
        log.info(f"│  elapsed          : {elapsed:.1f}s")
        log.info("│")
        log.info("│  explanation:")
        log.info(_wrap(eval_result.get("explanation", "(none)"), prefix="│    "))

        tu = orch_result.get("token_usage", {})
        total = tu.get("total", {})
        scout_tu = tu.get("scout", {})
        orch_tu  = tu.get("orchestrator", {})
        log.info("│")
        log.info("│  token usage:")
        log.info(f"│    scout       : prompt={scout_tu.get('prompt_tokens', 0)}  completion={scout_tu.get('completion_tokens', 0)}")
        log.info(f"│    orchestrator: prompt={orch_tu.get('prompt_tokens', 0)}  completion={orch_tu.get('completion_tokens', 0)}")
        log.info(f"│    TOTAL       : prompt={total.get('prompt_tokens', 0)}  completion={total.get('completion_tokens', 0)}")
        log.info("└" + "─" * 70 + "┘")

        return CaseResult(
            name=name,
            difficulty=difficulty,
            scope=input_scope,
            source_id=source_id,
            criteria=criteria[:120],
            choices=choices,
            result=eval_result.get("result"),
            confidence=eval_result.get("confidence", 0.0),
            explanation=eval_result.get("explanation", ""),
            eval_calls=orch_result.get("eval_calls", 0),
            orchestrator_turns=orch_result.get("orchestrator_turns", 0),
            elapsed_s=elapsed,
            scout_complexity=brief.get("complexity", "?"),
            scout_model=brief.get("recommended_model", "?"),
            token_usage=tu,
        )

    except Exception as e:
        import traceback
        elapsed = time.time() - t0
        log.error(f"\n│  ❌ CASE FAILED: {e}")
        log.error(traceback.format_exc())
        return CaseResult(
            name=name, difficulty=difficulty, scope=input_scope,
            source_id=source_id, criteria=criteria[:120], choices=choices,
            result=None, confidence=0.0, explanation="",
            eval_calls=0, orchestrator_turns=0, elapsed_s=elapsed,
            scout_complexity="?", scout_model="?", token_usage={},
            error=str(e),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test cases — SIMPLE → HARD
# ─────────────────────────────────────────────────────────────────────────────

def define_cases():
    cases = [
        # ── SIMPLE ──────────────────────────────────────────────────────
        dict(
            name="Simple-1 | LLM span | toxicity check",
            difficulty="SIMPLE",
            input_scope="span",
            source_id=SPAN_CLAUDE_BILLING_APOLOGY,
            criteria=(
                "Is the response toxic, harmful, or offensive? "
                "A customer service response that is professional and polite should pass."
            ),
            choices=["Toxic", "Not Toxic"],
        ),
        dict(
            name="Simple-2 | LLM span | politeness / empathy",
            difficulty="SIMPLE",
            input_scope="span",
            source_id=SPAN_CLAUDE_SONNET_CLOSING,
            criteria=(
                "Is the customer service response polite, empathetic, and helpful? "
                "Evaluate the tone and quality of the agent's message."
            ),
            choices=["Passed", "Failed"],
        ),
        dict(
            name="Simple-3 | LLM span | raw classifier output",
            difficulty="SIMPLE",
            input_scope="span",
            source_id=SPAN_GPT_CLASSIFIER,
            criteria=(
                "Is the output a valid category classification? "
                "It should contain a category label and priority level."
            ),
            choices=["Passed", "Failed"],
        ),
        # ── MODERATE ────────────────────────────────────────────────────
        dict(
            name="Moderate-1 | Agent span | response quality rating",
            difficulty="MODERATE",
            input_scope="span",
            source_id=SPAN_AGENT_BILLING_RESOLUTION,
            criteria=(
                "Rate the quality of this customer service agent response. "
                "Consider: accuracy of the resolution, tone, empathy, and whether the customer's "
                "issue was fully addressed. 1=very poor, 5=excellent."
            ),
            choices=["1", "2", "3", "4", "5"],
        ),
        dict(
            name="Moderate-2 | Agent span | escalation decision",
            difficulty="MODERATE",
            input_scope="span",
            source_id=SPAN_AGENT_ESCALATE,
            criteria=(
                "Given this agent response, evaluate whether the agent correctly decided to escalate "
                "the customer issue to the billing team. An escalation is appropriate when the customer "
                "is urgently requesting faster resolution of a financial dispute."
            ),
            choices=["Passed", "Failed"],
        ),
        dict(
            name="Moderate-3 | Chain span | retriever + LLM groundedness",
            difficulty="MODERATE",
            input_scope="span",
            source_id=SPAN_CHAIN_RETRIEVER_COMBO,
            criteria=(
                "Evaluate whether the agent response is grounded in the retrieved knowledge. "
                "The output should be consistent with and informed by the retrieved support articles, "
                "not hallucinated or contradictory to the available context."
            ),
            choices=["Passed", "Failed"],
        ),
        # ── COMPLEX ─────────────────────────────────────────────────────
        dict(
            name="Complex-1 | Trace | app crash handling quality",
            difficulty="COMPLEX",
            input_scope="trace",
            source_id=TRACE_APP_CRASH_TURN1,
            criteria=(
                "Evaluate the customer service agent's handling of the customer's app crash issue. "
                "Consider: did the agent acknowledge the problem, provide actionable steps, "
                "and maintain a helpful tone throughout the interaction?"
            ),
            choices=["1", "2", "3", "4", "5"],
        ),
        dict(
            name="Complex-2 | Trace | billing dispute resolution",
            difficulty="COMPLEX",
            input_scope="trace",
            source_id=TRACE_BILLING_DISPUTE,
            criteria=(
                "Given this customer service trace where a customer reports being charged twice, "
                "evaluate whether the agent: (1) acknowledged the error, (2) initiated a refund, "
                "and (3) provided a clear timeline. All three must be present to pass."
            ),
            choices=["Passed", "Failed", "Partial"],
        ),
        dict(
            name="Complex-3 | Trace | human escalation needed?",
            difficulty="COMPLEX",
            input_scope="trace",
            source_id=TRACE_APP_CRASH_TURN2,
            criteria=(
                "Given the conversation in this trace where the customer says their app is still crashing "
                "after updating to the latest version, evaluate whether the agent should escalate this "
                "issue to the technical support team (human escalation). "
                "Escalation is warranted when: the issue persists after standard troubleshooting, "
                "the customer is frustrated, or the issue requires specialist knowledge."
            ),
            choices=["Passed", "Failed"],
        ),
        # ── HARD ────────────────────────────────────────────────────────
        dict(
            name="Hard-1 | Session | full conversation quality (1-5)",
            difficulty="HARD",
            input_scope="session",
            source_id=SESSION_PHOTO_UPLOAD_ISSUE,
            criteria=(
                "Evaluate the overall quality of this multi-turn customer service session. "
                "The session involves a customer reporting an app that crashes when uploading photos. "
                "Consider across ALL turns: (1) problem resolution effectiveness, (2) consistency "
                "of support quality across turns, (3) empathy and tone, (4) technical accuracy, "
                "(5) whether the issue was ultimately resolved. "
                "1=very poor, 2=poor, 3=acceptable, 4=good, 5=excellent."
            ),
            choices=["1", "2", "3", "4", "5"],
        ),
        dict(
            name="Hard-2 | Session | context retention across turns",
            difficulty="HARD",
            input_scope="session",
            source_id=SESSION_PHOTO_UPLOAD_ISSUE,
            criteria=(
                "Evaluate whether the customer service agent consistently retained context across "
                "ALL turns of this multi-turn session. Specifically: did the agent remember "
                "what was discussed in earlier turns, avoid repeating solutions already tried, "
                "and build on the conversation history to reach a resolution? "
                "Pass only if context was maintained throughout; Fail if the agent lost track "
                "of prior conversation; Partial if context was mostly maintained but with lapses."
            ),
            choices=["Passed", "Failed", "Partial"],
        ),
        dict(
            name="Hard-3 | Session | vague criteria — overall helpfulness",
            difficulty="HARD",
            input_scope="session",
            source_id=SESSION_PHOTO_UPLOAD_ISSUE,
            criteria="Was the customer service helpful?",
            choices=["Yes", "No", "Somewhat"],
        ),

        # ── DATASET ROWS — TEXT modality ─────────────────────────────────
        dict(
            name="Dataset-Text-1 | toxic input | toxicity check",
            difficulty="SIMPLE",
            input_scope="dataset_row",
            source_id=ROW_TEXT_TOXIC,
            criteria=(
                "Is this text toxic, harmful, or offensive? "
                "Look for profanity, insults, slurs, or content that demeans others."
            ),
            choices=["Toxic", "Not Toxic"],

        ),
        dict(
            name="Dataset-Text-2 | benign travel story | toxicity + sentiment",
            difficulty="SIMPLE",
            input_scope="dataset_row",
            source_id=ROW_TEXT_BENIGN,
            criteria=(
                "Evaluate both: (1) Is the text toxic or harmful? "
                "(2) What is the overall sentiment (positive/negative/neutral)? "
                "Pass only if the text is non-toxic AND positive in sentiment."
            ),
            choices=["Passed", "Failed"],

        ),

        # ── DATASET ROWS — AUDIO modality ────────────────────────────────
        dict(
            name="Dataset-Audio-1 | audio quality | noise assessment",
            difficulty="MODERATE",
            input_scope="dataset_row",
            source_id=ROW_AUDIO_QUALITY,
            criteria=(
                "Evaluate the audio quality of this recording. "
                "Consider: background noise levels, signal clarity, hum or hiss artefacts, "
                "speech intelligibility, and overall recording quality. "
                "Score 0-100 where 100 is perfect studio quality and 0 is unintelligible."
            ),
            choices=[str(i) for i in range(0, 110, 10)],  # 0, 10, 20, ... 100

        ),
        dict(
            name="Dataset-Audio-2 | TTS dcs | digit pronunciation accuracy",
            difficulty="MODERATE",
            input_scope="dataset_row",
            source_id=ROW_AUDIO_TTS,
            criteria=(
                "Evaluate whether the TTS (text-to-speech) audio correctly pronounces dates and numbers. "
                "The transcript is: 'my name is rishav and my birthday is on 21st june 1996'. "
                "Score 0-100: deduct points for unnatural digit pronunciation (e.g. '21st' said as 'two one st', "
                "'1996' said as 'one nine nine six' instead of 'nineteen ninety-six')."
            ),
            choices=[str(i) for i in range(0, 110, 10)],

        ),

        # ── DATASET ROWS — IMAGE modality ────────────────────────────────
        dict(
            name="Dataset-Image-1 | generated image | Renaissance font style",
            difficulty="MODERATE",
            input_scope="dataset_row",
            source_id=ROW_IMAGE_FONT,
            criteria=(
                "A user requested: 'Give me something with a Renaissance manuscript vibe.' "
                "Evaluate whether the generated image matches this request. "
                "Look for: illuminated manuscript style, calligraphic letterforms, medieval-style decorations, "
                "parchment/vellum texture, ornate initials, or period-appropriate visual motifs. "
                "1=completely off-style, 5=perfectly matches Renaissance manuscript aesthetic."
            ),
            choices=["1", "2", "3", "4", "5"],

        ),

        # ── CELL-LEVEL evaluation (single cell from a dataset row) ────────
        # Cell IDs are resolved at runtime from known rows.
    ]

    # ── Resolve cell IDs at runtime ──────────────────────────────────────
    cell_text_id  = _get_cell_id_from_row(ROW_TEXT_TOXIC, data_type="text")
    cell_audio_id = _get_cell_id_from_row(ROW_AUDIO_QUALITY, data_type="audio")
    cell_image_id = _get_cell_id_from_row(ROW_IMAGE_FONT, data_type="image")
    cell_pdf_id   = _find_pdf_cell()

    if cell_text_id:
        cases.append(dict(
            name="Cell-Text-1 | text cell | toxicity check",
            difficulty="SIMPLE",
            input_scope="cell",
            source_id=cell_text_id,
            criteria=(
                "Evaluate whether this text cell contains toxic, harmful, or offensive "
                "language. Look for profanity, insults, slurs, threats, or discriminatory "
                "content. Pass if clean, Fail if toxic."
            ),
            choices=["Passed", "Failed"],
        ))
    else:
        log.warning("SKIP Cell-Text-1: no text cell found in ROW_TEXT_TOXIC")

    if cell_audio_id:
        cases.append(dict(
            name="Cell-Audio-1 | audio cell | recording quality",
            difficulty="MODERATE",
            input_scope="cell",
            source_id=cell_audio_id,
            criteria=(
                "Evaluate the audio quality of this single audio cell. "
                "Check for background noise, electrical hum, clipping, distortion, "
                "and speech clarity. Score 0-100 where 100 is studio quality."
            ),
            choices=[str(i) for i in range(0, 110, 10)],
        ))
    else:
        log.warning("SKIP Cell-Audio-1: no audio cell found in ROW_AUDIO_QUALITY")

    if cell_image_id:
        cases.append(dict(
            name="Cell-Image-1 | image cell | visual style assessment",
            difficulty="MODERATE",
            input_scope="cell",
            source_id=cell_image_id,
            criteria=(
                "Evaluate the visual quality and style of this image cell. "
                "The user requested a Renaissance manuscript style. "
                "Does the image match this aesthetic? Look for calligraphic "
                "letterforms, ornate decorations, and period-appropriate motifs. "
                "1=completely off-style, 5=perfectly matches."
            ),
            choices=["1", "2", "3", "4", "5"],
        ))
    else:
        log.warning("SKIP Cell-Image-1: no image cell found in ROW_IMAGE_FONT")

    if cell_pdf_id:
        cases.append(dict(
            name="Cell-PDF-1 | document cell | content quality check",
            difficulty="MODERATE",
            input_scope="cell",
            source_id=cell_pdf_id,
            criteria=(
                "Evaluate the content of this PDF/document cell. "
                "Is the document well-structured, readable, and does it contain "
                "meaningful content? Check for proper formatting, logical flow, "
                "and completeness. Pass if the document is coherent and useful, "
                "Fail if it is garbled, empty, or poorly structured."
            ),
            choices=["Passed", "Failed"],
        ))
    else:
        log.warning("SKIP Cell-PDF-1: no document/PDF cell found in org datasets")

    # ── MULTIMODAL cells (dataset ff0f3091 — known IDs, no runtime lookup) ──
    cases += [
        dict(
            name="MM-Image-1 | image cell | visual content assessment",
            difficulty="MODERATE",
            input_scope="cell",
            source_id=CELL_MM_IMAGE,
            criteria=(
                "Evaluate the visual content of this image. "
                "Describe what you see in the image and assess whether the image "
                "is clear, well-composed, and contains meaningful visual content. "
                "Pass if the image is clear and contains identifiable content, "
                "Fail if it is blank, corrupted, or unintelligible."
            ),
            choices=["Passed", "Failed"],
        ),
        dict(
            name="MM-Audio-1 | audio cell | audio content assessment",
            difficulty="MODERATE",
            input_scope="cell",
            source_id=CELL_MM_AUDIO,
            criteria=(
                "Evaluate the audio content of this recording. "
                "Assess whether the speech is intelligible, the audio quality "
                "is acceptable, and the recording contains meaningful spoken content. "
                "Pass if the audio is clear enough to understand, "
                "Fail if it is unintelligible noise or silence."
            ),
            choices=["Passed", "Failed"],
        ),
        dict(
            name="MM-PDF-1 | document cell | PDF content assessment",
            difficulty="MODERATE",
            input_scope="cell",
            source_id=CELL_MM_PDF,
            criteria=(
                "Evaluate the content of this PDF document. "
                "Assess whether the document is readable, well-structured, "
                "and contains meaningful content. "
                "Pass if the document is coherent and contains useful information, "
                "Fail if it is empty, corrupted, or unreadable."
            ),
            choices=["Passed", "Failed"],
        ),
    ]

    cases += [
        # ── KB-ASSISTED evaluation ────────────────────────────────────────
        dict(
            name="KB-1 | span + KB kn | response grounded in KB?",
            difficulty="MODERATE",
            input_scope="span",
            source_id=SPAN_CLAUDE_BILLING_APOLOGY,
            criteria=(
                "Using the connected knowledge base, evaluate whether the agent's response about the billing "
                "issue is grounded in documented support policies. "
                "Search the KB for refund policy and dispute resolution procedures. "
                "Pass if the response aligns with documented policy; Fail if it contradicts or invents policy."
            ),
            choices=["Passed", "Failed"],
            kb_id=KB_KN,
        ),
        dict(
            name="KB-2 | trace + KB yth | customer issue vs KB docs",
            difficulty="COMPLEX",
            input_scope="trace",
            source_id=TRACE_BILLING_DISPUTE,
            criteria=(
                "Using the connected knowledge base, evaluate whether the agent in this trace "
                "correctly followed the documented billing dispute resolution process. "
                "Search the KB for billing dispute, refund, and charge reversal documentation. "
                "The agent should have: acknowledged the duplicate charge, initiated a refund, "
                "and given a timeline consistent with KB policy."
            ),
            choices=["Fully Compliant", "Partially Compliant", "Non-Compliant"],
            kb_id=KB_YTH,
        ),

        # ── FEEDBACK-ASSISTED evaluation ──────────────────────────────────
        dict(
            name="Feedback-1 | span + toxicity feedback | calibrated toxicity",
            difficulty="MODERATE",
            input_scope="span",
            source_id=SPAN_CLAUDE_BILLING_APOLOGY,
            # Original template choices: ["Passed", "Failed"] — human feedback value was "Passed"
            # Choices MUST match the template so the feedback value maps directly to a valid choice.
            choices=["Passed", "Failed"],
            criteria=(
                "Evaluate whether the text contains any harmful, offensive, or toxic language. "
                "This includes checking for hate speech, insults, threats, profanity, discriminatory "
                "remarks, or language that promotes harm, exclusion, or violence. "
                "Output Passed if the text is clean and non-toxic; Failed if it is toxic. "
                "Use the available past human feedback examples (on this same Passed/Failed scale) "
                "to calibrate your judgment."
            ),
            eval_template_id=EVAL_TEMPLATE_TOXICITY,
        ),
        dict(
            name="Feedback-2 | audio row + audio_quality feedback | calibrated score",
            difficulty="HARD",
            input_scope="dataset_row",
            source_id=ROW_AUDIO_QUALITY,
            # Template 3b44caa3 (audio_quality) has 3 embeddings in ClickHouse AND 1 row in
            # Postgres feedback. Both RAG path and Postgres fallback will return examples.
            # Original scale: numeric 0-100. Human feedback value: "40".
            choices=[str(i) for i in range(0, 110, 10)],
            criteria=(
                "Evaluate whether the audio demonstrates high technical quality and clarity, free from "
                "distracting flaws or distortions. Check for unwanted noise such as microphone hiss, "
                "electrical hum, clipping, distortion, or compression artefacts. "
                "Score 0–100 (higher = better quality, 0 = unintelligible noise, 100 = studio perfect). "
                "The available human feedback examples use this SAME 0–100 numeric scale — "
                "use them to anchor your score to the human's calibration."
            ),
            eval_template_id=EVAL_TEMPLATE_AUDIO_QUALITY,
        ),
        # ── MCP tool cases ─────────────────────────────────────────────────
        dict(
            name="MCP-1 | span + grammar tool | grammar quality check",
            difficulty="MODERATE",
            input_scope="span",
            source_id=SPAN_CLAUDE_BILLING_APOLOGY,
            choices=["Passed", "Failed"],
            criteria=(
                "Evaluate whether the agent's response is grammatically correct and "
                "well-written. Use the available grammar checking tool to verify — "
                "check for spelling mistakes, punctuation errors, capitalization issues, "
                "and repeated words. Pass if the grammar quality is 'good' or 'excellent', "
                "fail if it is 'fair' or 'poor'."
            ),
        ),
        dict(
            name="MCP-2 | span + readability tool | readability level check",
            difficulty="MODERATE",
            input_scope="span",
            source_id=SPAN_CLAUDE_BILLING_APOLOGY,
            choices=["Passed", "Failed"],
            criteria=(
                "Evaluate whether the agent's response is written at an appropriate "
                "readability level for a customer service interaction. Use the available "
                "readability analysis tool to check the Flesch reading ease score. "
                "The response should be at a 'standard' or easier reading level "
                "(Flesch score >= 60). Pass if the readability is appropriate for "
                "a general audience, fail if it is too difficult to understand."
            ),
        ),
        dict(
            name="Feedback-3 | image row + scd feedback | calibrated style score",
            difficulty="HARD",
            input_scope="dataset_row",
            source_id=ROW_IMAGE_FONT,
            # Original template choices: ["1","2","3","4","5"] — 1=least suitable, 5=most suitable.
            # Human feedback value: "1" on this exact scale.  Scale is identical — no mismatch.
            choices=["1", "2", "3", "4", "5"],
            criteria=(
                "Does the font shown in this image suit the user's prompt? "
                "The prompt was: 'Give me something with a Renaissance manuscript vibe.' "
                "Rate from 1 to 5, where 1 = least suitable (font does not match the prompt at all) "
                "and 5 = most suitable (font perfectly matches the Renaissance manuscript aesthetic). "
                "The available human feedback examples use this SAME 1–5 scale — "
                "use them to anchor your score to the human's calibration."
            ),
            eval_template_id=EVAL_TEMPLATE_SCD,
        ),
    ]

    return cases


# ─────────────────────────────────────────────────────────────────────────────
# Summary report
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(results: list[CaseResult]):
    W = 72
    log.info("\n\n" + "━" * W)
    log.info(_banner("FINAL SUMMARY — ALL TEST CASES", "━"))
    log.info("━" * W)

    passed  = [r for r in results if r.result is not None and not r.error]
    errored = [r for r in results if r.error]

    log.info(f"\n  Total cases  : {len(results)}")
    log.info(f"  Completed    : {len(passed)}")
    log.info(f"  Errored      : {len(errored)}")

    # Per-case table
    log.info(f"\n  {'Case':<45} {'Diff':<8} {'Result':<12} {'Conf':>5} {'Turns':>5} {'Time':>7}")
    log.info(f"  {'─'*45} {'─'*8} {'─'*12} {'─'*5} {'─'*5} {'─'*7}")
    for r in results:
        res_str  = str(r.result)[:12] if r.result else ("ERROR" if r.error else "—")
        conf_str = f"{r.confidence:.2f}" if r.result else "—"
        log.info(
            f"  {r.name[:45]:<45} {r.difficulty:<8} "
            f"{res_str:<12} {conf_str:>5} {r.orchestrator_turns:>5} {r.elapsed_s:>6.1f}s"
        )

    # Scout analysis
    log.info(f"\n  {'Case':<45} {'Complexity':<12} {'Model':<15} {'Formalize'}")
    log.info(f"  {'─'*45} {'─'*12} {'─'*15} {'─'*9}")
    for r in results:
        if not r.error:
            log.info(f"  {r.name[:45]:<45} {r.scout_complexity:<12} {r.scout_model:<15}")

    # Token usage
    log.info(f"\n  {'Case':<45} {'Scout':>8} {'Orch':>8} {'Total':>8}")
    log.info(f"  {'─'*45} {'─'*8} {'─'*8} {'─'*8}")
    grand = 0
    for r in results:
        if not r.error and r.token_usage:
            tu = r.token_usage
            s = tu.get("scout", {})
            o = tu.get("orchestrator", {})
            t = tu.get("total", {})
            st = s.get("prompt_tokens", 0) + s.get("completion_tokens", 0)
            ot = o.get("prompt_tokens", 0) + o.get("completion_tokens", 0)
            tt = t.get("prompt_tokens", 0) + t.get("completion_tokens", 0)
            grand += tt
            log.info(f"  {r.name[:45]:<45} {st:>8} {ot:>8} {tt:>8}")
    log.info(f"  {'Grand Total':<45} {'':>8} {'':>8} {grand:>8}")

    if errored:
        log.info("\n  ERRORS:")
        for r in errored:
            log.info(f"    [{r.name}]")
            log.info(f"      {r.error}")

    log.info(f"\n  Log file: {LOG_FILE}")
    log.info("━" * W + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--filter", default="", help="Only run cases whose name contains this substring (case-insensitive)")
    args = parser.parse_args()

    # Append to results file when running filtered subset; overwrite for full suite
    _setup_logging(append=bool(args.filter))

    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    W = 72
    log.info("=" * W)
    log.info(_banner("EvalOrchestrator — REAL Integration Tests", "="))
    log.info("=" * W)
    log.info(f"  Started : {start_ts}")
    log.info(f"  Project : {PROJ_ID}")
    log.info(f"  Org     : {ORG_ID}")
    log.info(f"  Log     : {LOG_FILE}")
    log.info("=" * W)

    cases = define_cases()
    if args.filter:
        cases = [c for c in cases if args.filter.lower() in c.get("name", "").lower()]
        log.info(f"\n  Running {len(cases)} filtered cases (filter: '{args.filter}') …\n")
    else:
        log.info(f"\n  Running {len(cases)} test cases (SIMPLE → HARD) …\n")

    for case_def in cases:
        result = run_case(**case_def)
        _results.append(result)

    print_summary(_results)


if __name__ == "__main__":
    main()
