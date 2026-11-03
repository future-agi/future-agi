"""
Verbose Agent Compass test — shows the full thinking + tool call trace.

Works for BOTH text (v2 Judge & Chauffeur) and voice (Voice Compass).

Outputs:
  1. Colored console output showing every LLM call, tool call, reasoning
  2. JSON chat history log at /tmp/agent_compass_chat_log_<trace_id>.json

Usage (inside Docker):
    # Text Agent Compass v2 (verbose)
    docker exec -e PYTHONPATH=/app/backend backend python \
        agentic_eval/agenthub/traceerroragent/test_verbose.py <trace_id>

    # Voice Agent Compass (verbose)
    docker exec -e PYTHONPATH=/app/backend -e MODE=voice backend python \
        agentic_eval/agenthub/traceerroragent/test_verbose.py <trace_id>

    # Save to DB
    docker exec -e PYTHONPATH=/app/backend -e SAVE_TO_DB=1 backend python \
        agentic_eval/agenthub/traceerroragent/test_verbose.py <trace_id>

Environment variables:
    MODE         — "text" (default) or "voice"
    SAVE_TO_DB   — "1" to save to DB (default: "0")
    MAX_TURNS    — Override Judge max turns (default: use config)
"""

import json
import os
import sys
import textwrap
import time
from datetime import datetime, timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")

import django

django.setup()

# ── ANSI colors ─────────────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"


# ── Chat history logger ─────────────────────────────────────────────────────

class ChatLog:
    """Records full chat history of all LLM interactions for JSON export."""

    def __init__(self, trace_id: str, mode: str):
        self.trace_id = trace_id
        self.mode = mode
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.stages: list[dict] = []
        self._current_stage = None

    def start_stage(self, name: str, model: str = ""):
        self._current_stage = {
            "stage": name,
            "model": model,
            "started_at": time.time(),
            "messages": [],
            "tool_calls": [],
            "token_usage": {},
        }

    def add_message(self, role: str, content, **kwargs):
        if self._current_stage is None:
            return
        entry = {"role": role, "content": _truncate_for_log(content), **kwargs}
        self._current_stage["messages"].append(entry)

    def add_tool_call(self, turn: int, tool_name: str, args: dict, result: dict, duration_s: float = 0):
        if self._current_stage is None:
            return
        self._current_stage["tool_calls"].append({
            "turn": turn,
            "tool": tool_name,
            "args": _truncate_for_log(args, max_len=2000),
            "result": _truncate_for_log(result, max_len=2000),
            "duration_s": round(duration_s, 2),
        })

    def add_reasoning(self, turn: int, text: str):
        if self._current_stage is None:
            return
        self._current_stage["tool_calls"].append({
            "turn": turn,
            "type": "reasoning",
            "text": text[:3000],
        })

    def end_stage(self, token_usage: dict = None, output: dict = None):
        if self._current_stage is None:
            return
        self._current_stage["duration_s"] = round(time.time() - self._current_stage["started_at"], 2)
        self._current_stage["started_at"] = None  # Remove raw timestamp
        if token_usage:
            self._current_stage["token_usage"] = token_usage
        if output:
            self._current_stage["output"] = _truncate_for_log(output, max_len=5000)
        self.stages.append(self._current_stage)
        self._current_stage = None

    def save(self, result: dict = None):
        log = {
            "trace_id": self.trace_id,
            "mode": self.mode,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "stages": self.stages,
        }
        if result:
            log["final_result"] = {
                "summary": result.get("summary", {}),
                "scores": result.get("scores", {}),
                "error_count": len(result.get("errors", [])),
                "errors": [
                    {
                        "error_id": e.get("error_id"),
                        "category": e.get("category"),
                        "impact": e.get("impact"),
                        "confidence": e.get("confidence"),
                        "description": e.get("description", "")[:300],
                        "evidence_snippets": [s[:200] for s in e.get("evidence_snippets", [])[:2]],
                    }
                    for e in result.get("errors", [])
                ],
                "token_usage": result.get("token_usage", {}),
            }

        path = f"/tmp/agent_compass_chat_log_{self.trace_id[:8]}.json"
        with open(path, "w") as f:
            json.dump(log, f, indent=2, default=str)
        print(f"\n  {BOLD}Chat history log saved to: {path}{RESET}")
        return path


def _truncate_for_log(obj, max_len=4000):
    """Truncate strings in obj for JSON log (keep it readable)."""
    if isinstance(obj, str):
        return obj[:max_len] + ("..." if len(obj) > max_len else "")
    if isinstance(obj, dict):
        return {k: _truncate_for_log(v, max_len) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_for_log(v, max_len) for v in obj[:50]]  # Cap list items
    return obj


# Global chat log instance (set in main)
_chat_log: ChatLog = None


def hline(char="─", width=80):
    print(f"{DIM}{char * width}{RESET}")


def header(text, color=CYAN):
    hline("═")
    print(f"{color}{BOLD}{text}{RESET}")
    hline("═")


def subheader(text, color=YELLOW):
    print(f"\n{color}{BOLD}▶ {text}{RESET}")
    hline("─")


def indent(text, prefix="  ", max_width=120):
    lines = text.split("\n")
    out = []
    for line in lines:
        if len(line) > max_width:
            wrapped = textwrap.wrap(line, width=max_width, subsequent_indent=prefix + "  ")
            out.extend(wrapped)
        else:
            out.append(line)
    return "\n".join(f"{prefix}{l}" for l in out)


# ── Monkey-patch Judge to be verbose ────────────────────────────────────────

def patch_judge_verbose():
    """Patch JudgeAgent.run() to print each turn, tool call, and reasoning."""
    from ee.agenthub.traceerroragent.judge import JudgeAgent

    _original_run = JudgeAgent.run

    def verbose_run(self, trace_outline, chauffeur_report, memory_context, trace_id, total_spans, user_query, chauffeur_failed=False):
        global _chat_log
        from ee.agenthub.traceerroragent.prompts_v2 import (
            build_judge_user_prompt,
            get_judge_system_prompt,
        )

        system_prompt = get_judge_system_prompt()
        user_prompt = build_judge_user_prompt(
            trace_outline=trace_outline,
            chauffeur_report=chauffeur_report,
            memory_context=memory_context,
            trace_id=trace_id,
            total_spans=total_spans,
            user_query=user_query,
            chauffeur_failed=chauffeur_failed,
        )

        # Start chat log stage
        if _chat_log:
            _chat_log.start_stage("judge", model="Claude Sonnet 4.5 (Bedrock)")
            _chat_log.add_message("system", system_prompt)
            _chat_log.add_message("user", user_prompt)

        # Print prompts summary
        subheader("Judge System Prompt", MAGENTA)
        print(f"  {DIM}({len(system_prompt)} chars — includes ERROR_TAXONOMY){RESET}")

        subheader("Judge User Prompt", MAGENTA)
        # Show first 800 chars
        print(indent(user_prompt[:800], "  "))
        if len(user_prompt) > 800:
            print(f"  {DIM}... ({len(user_prompt)} chars total){RESET}")

        messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt, "cache_control": {"type": "ephemeral"}}],
            },
        ]

        subheader(f"Judge Agentic Loop (max {self.max_turns} turns)", GREEN)

        for turn in range(self.max_turns):
            turn_start = time.time()

            try:
                from ee.agenthub.traceerroragent.judge import JUDGE_TOOLS
                response = self.llm._get_completion_with_tools(
                    messages=messages,
                    tools=JUDGE_TOOLS,
                )

                choice = response.choices[0]
                turn_time = time.time() - turn_start

                print(f"\n  {CYAN}{BOLD}╔══ Turn {turn + 1} ({turn_time:.1f}s) ══╗{RESET}")
                print(f"  {DIM}finish_reason: {choice.finish_reason}{RESET}")

                # Print model's text reasoning (thinking)
                if choice.message.content:
                    content = choice.message.content
                    if isinstance(content, list):
                        for block in content:
                            if hasattr(block, "text"):
                                content = block.text
                                break
                            elif isinstance(block, dict) and block.get("text"):
                                content = block["text"]
                                break
                    if isinstance(content, str) and content.strip():
                        print(f"  {YELLOW}💭 Reasoning:{RESET}")
                        print(indent(content[:600], f"  {DIM}"))
                        if len(content) > 600:
                            print(f"  {DIM}... ({len(content)} chars){RESET}")
                        if _chat_log:
                            _chat_log.add_reasoning(turn + 1, content)

                if choice.finish_reason in ("stop", "end_turn"):
                    print(f"  {GREEN}✓ Judge finished (no more tool calls){RESET}")
                    break

                if choice.message.tool_calls:
                    messages.append(choice.message.model_dump())

                    for tc in choice.message.tool_calls:
                        tool_name = tc.function.name
                        try:
                            tool_args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            tool_args = {}

                        # Print tool call
                        if tool_name == "submit_finding":
                            cat = tool_args.get("category", "?")
                            impact = tool_args.get("impact", "?")
                            conf = tool_args.get("confidence", "?")
                            print(f"  {RED}🔧 {tool_name}({RESET}")
                            print(f"    {BOLD}category{RESET}: {cat}")
                            print(f"    {BOLD}impact{RESET}: {impact}, confidence: {conf}")
                            desc = tool_args.get("description", "")
                            if desc:
                                print(f"    {BOLD}description{RESET}: {desc[:200]}")
                            evidence = tool_args.get("evidence_snippets", [])
                            if evidence:
                                print(f"    {BOLD}evidence{RESET}: {evidence[0][:150]}")
                        elif tool_name == "submit_scores":
                            print(f"  {GREEN}🔧 {tool_name}({RESET}")
                            for dim in ["factual_grounding", "privacy_and_safety", "instruction_adherence", "optimal_plan_execution"]:
                                val = tool_args.get(dim, {})
                                if isinstance(val, dict):
                                    print(f"    {dim}: {val.get('score', '?')} — {val.get('reason', '')[:80]}")
                        elif tool_name == "submit_summary":
                            print(f"  {GREEN}🔧 {tool_name}({RESET}")
                            print(f"    overall_score: {tool_args.get('overall_score', '?')}")
                            print(f"    priority: {tool_args.get('recommended_priority', '?')}")
                            ins = tool_args.get("insights", "")
                            if ins:
                                print(f"    insights: {ins[:200]}")
                        else:
                            args_str = json.dumps(tool_args, default=str)
                            if len(args_str) > 200:
                                args_str = args_str[:200] + "..."
                            print(f"  {BLUE}🔧 {tool_name}{RESET}({args_str})")

                        tc_start = time.time()
                        result = self._execute_tool(tool_name, tool_args)
                        tc_elapsed = time.time() - tc_start

                        # Print tool result (compact)
                        result_str = json.dumps(result, default=str)
                        if tool_name in ("submit_finding", "submit_scores", "submit_summary"):
                            print(f"    {DIM}→ {result_str}{RESET}")
                        elif len(result_str) > 300:
                            print(f"    {DIM}→ {result_str[:300]}...{RESET}")
                        else:
                            print(f"    {DIM}→ {result_str}{RESET}")

                        # Log to chat history
                        if _chat_log:
                            _chat_log.add_tool_call(
                                turn=turn + 1,
                                tool_name=tool_name,
                                args=tool_args,
                                result=result,
                                duration_s=tc_elapsed,
                            )

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result, default=str),
                        })

                    self._compact_old_tool_results(messages)

                    if self._investigation_complete:
                        print(f"\n  {GREEN}{BOLD}✓ Investigation complete{RESET}")
                        break
                else:
                    print(f"  {YELLOW}⚠ No tool calls and didn't stop{RESET}")
                    break

            except Exception as e:
                print(f"  {RED}✗ Turn {turn + 1} error: {e}{RESET}")
                break

        # Build fallbacks
        if not self.scores:
            self.scores = self._compute_fallback_scores()
        if not self.summary:
            self.summary = self._compute_fallback_summary()

        result = {
            "errors": self.errors,
            "scores": self.scores,
            "summary": self.summary,
        }

        if _chat_log:
            _chat_log.end_stage(
                token_usage=dict(self.llm.token_usage),
                output=result,
            )

        return result

    JudgeAgent.run = verbose_run


def patch_chauffeur_verbose():
    """Patch ChauffeurAgent.run() to print its output."""
    from ee.agenthub.traceerroragent.chauffeur import ChauffeurAgent

    _original_run = ChauffeurAgent.run

    def verbose_run(self):
        global _chat_log
        subheader("Chauffeur (Haiku) — Reading Full Trace", CYAN)
        print(f"  {DIM}Trace execution summary: {len(self.trace_execution_summary)} chars{RESET}")

        if _chat_log:
            _chat_log.start_stage("chauffeur", model="Claude Haiku 4.5 (Bedrock)")
            _chat_log.add_message("user", self.trace_execution_summary)

        start = time.time()
        result = _original_run(self)
        elapsed = time.time() - start

        sub_flows = result.get("sub_flows", [])
        overview = result.get("trace_overview", "")

        print(f"  {GREEN}Completed in {elapsed:.1f}s{RESET}")
        print(f"  Sub-flows found: {len(sub_flows)}")

        if overview:
            print(f"\n  {BOLD}Trace Overview:{RESET}")
            print(indent(overview[:500], "  "))

        for i, sf in enumerate(sub_flows):
            name = sf.get("name", "?")
            spans = sf.get("spans", [])
            summary = sf.get("summary", "")
            flags = sf.get("flags", [])
            print(f"\n  {YELLOW}Sub-flow {i + 1}: {name}{RESET} ({len(spans)} spans)")
            if summary:
                print(indent(summary[:300], f"    {DIM}"))
                if len(summary) > 300:
                    print(f"    {DIM}...{RESET}")
            if flags:
                for flag in flags:
                    print(f"    {RED}⚑ {flag[:150]}{RESET}")

        if _chat_log:
            _chat_log.add_message("assistant", json.dumps(result, default=str))
            _chat_log.end_stage(
                token_usage=self.get_token_usage(),
                output=result,
            )

        return result

    ChauffeurAgent.run = verbose_run


def patch_voice_verbose():
    """Patch VoiceCompassAgent to show intermediate steps."""
    from ee.agenthub.traceerroragent.voice_compass import (
        VoiceCompassAgent,
        VoicePreplanner,
        VoiceCategoryEvaluator,
    )

    # Patch Preplanner
    _original_preplanner_run = VoicePreplanner.run

    def verbose_preplanner_run(self, voice_data):
        global _chat_log
        subheader("Preplanner (Gemini 3 Pro) — Analyzing Call", CYAN)
        print(f"  {DIM}Has audio: {voice_data.has_audio}{RESET}")
        print(f"  {DIM}Transcript turns: {len(voice_data.transcript)}{RESET}")

        if _chat_log:
            _chat_log.start_stage("voice_preplanner", model="Gemini 3 Pro (Vertex AI)")

        start = time.time()
        result = _original_preplanner_run(self, voice_data)
        elapsed = time.time() - start

        print(f"  {GREEN}Completed in {elapsed:.1f}s{RESET}")
        print(f"\n  {BOLD}Call Summary:{RESET}")
        print(indent(result.get("call_summary", "N/A")[:400], "  "))

        observations = result.get("key_observations", [])
        if observations:
            print(f"\n  {BOLD}Key Observations:{RESET}")
            for obs in observations:
                print(f"    • {obs[:150]}")

        categories = result.get("selected_categories", [])
        hints = result.get("per_category_hints", {})
        print(f"\n  {BOLD}Selected Categories ({len(categories)}):{RESET}")
        for cat in categories:
            hint = hints.get(cat, "")
            print(f"    {YELLOW}▸ {cat}{RESET}")
            if hint:
                print(f"      {DIM}{hint[:150]}{RESET}")

        tokens = dict(self.llm.token_usage)
        print(f"\n  {DIM}Tokens: {tokens.get('total_tokens', 0)} "
              f"(prompt={tokens.get('prompt_tokens', 0)}, "
              f"completion={tokens.get('completion_tokens', 0)}){RESET}")

        if _chat_log:
            _chat_log.add_message("assistant", json.dumps(result, default=str))
            _chat_log.end_stage(token_usage=tokens, output=result)

        return result

    VoicePreplanner.run = verbose_preplanner_run

    # Patch Evaluator
    _original_evaluator_run = VoiceCategoryEvaluator.run

    def verbose_evaluator_run(self, category_name, voice_data, call_summary, category_hint, error_offset=0):
        global _chat_log
        print(f"\n  {BLUE}▸ Evaluating: {category_name}{RESET}")
        print(f"    {DIM}Hint: {category_hint[:120]}{RESET}")

        if _chat_log:
            _chat_log.start_stage(f"voice_evaluator:{category_name}", model="Gemini 3 Flash (Vertex AI)")

        start = time.time()
        errors = _original_evaluator_run(self, category_name, voice_data, call_summary, category_hint, error_offset)
        elapsed = time.time() - start

        print(f"    {GREEN}{len(errors)} error(s) found ({elapsed:.1f}s){RESET}")
        for err in errors:
            impact = err.get("impact", "?")
            cat = err.get("category", "?")
            conf = err.get("confidence", "?")
            desc = err.get("description", "")[:150]
            color = RED if impact == "HIGH" else YELLOW if impact == "MEDIUM" else DIM
            print(f"    {color}[{impact}] {cat} (conf={conf}){RESET}")
            print(f"      {DIM}{desc}{RESET}")

        if _chat_log:
            _chat_log.add_message("assistant", json.dumps(errors, default=str))
            _chat_log.end_stage(
                token_usage=dict(self.llm.token_usage),
                output={"category": category_name, "errors": errors},
            )

        return errors

    VoiceCategoryEvaluator.run = verbose_evaluator_run


# ── Main test runners ───────────────────────────────────────────────────────


def run_text_v2(trace_id, save_to_db=False):
    """Run text Agent Compass v2 with verbose output."""
    global _chat_log
    from ee.agenthub.traceerroragent.traceerror import TraceErrorAnalysisAgent

    _chat_log = ChatLog(trace_id, mode="text_v2")
    patch_chauffeur_verbose()
    patch_judge_verbose()

    header(f"Text Agent Compass v2 — Trace: {trace_id}")

    start = time.time()
    agent = TraceErrorAnalysisAgent(
        trace_id=trace_id,
        enable_memory=False,
        save_to_db=save_to_db,
        token_budget=500000,
    )

    if not agent.spans:
        print(f"{RED}ERROR: No spans found for trace {trace_id}{RESET}")
        return

    print(f"  Loaded {len(agent.spans)} spans")
    result = agent.summarize()
    elapsed = time.time() - start

    # Print final results
    header(f"RESULTS ({elapsed:.1f}s)", GREEN)
    summary = result.get("summary", {})
    print(f"  Overall score: {summary.get('overall_score', '?')}")
    print(f"  Error count:   {summary.get('error_count', '?')}")
    print(f"  Priority:      {summary.get('recommended_priority', '?')}")

    insights = summary.get("insights", "")
    if insights:
        print(f"\n  {BOLD}Insights:{RESET}")
        print(indent(insights[:400], "  "))

    scores = result.get("scores", {})
    print(f"\n  {BOLD}Scores:{RESET}")
    for dim in ["factual_grounding", "privacy_and_safety", "instruction_adherence", "optimal_plan_execution"]:
        s = scores.get(dim, {})
        if isinstance(s, dict):
            print(f"    {dim}: {s.get('score', '?')} — {s.get('reason', '')[:100]}")

    errors = result.get("errors", [])
    print(f"\n  {BOLD}Errors ({len(errors)}):{RESET}")
    for err in errors:
        impact = err.get("impact", "?")
        color = RED if impact == "HIGH" else YELLOW if impact == "MEDIUM" else DIM
        print(f"    {color}[{impact}] {err.get('category', '?')}{RESET}")
        print(f"      {DIM}{err.get('description', '')[:150]}{RESET}")

    # Token usage
    tu = result.get("token_usage", {})
    if tu:
        print(f"\n  {BOLD}Token Usage:{RESET}")
        for stage, usage in tu.items():
            if isinstance(usage, dict) and usage.get("total_tokens"):
                total = usage.get("total_tokens", 0)
                prompt = usage.get("prompt_tokens", 0)
                completion = usage.get("completion_tokens", 0)
                cached = usage.get("cache_read_input_tokens", 0)
                print(f"    {stage}: {total} tokens (prompt={prompt}, completion={completion}, cached={cached})")

    # Save JSON
    output_path = f"/tmp/agent_compass_v2_{trace_id[:8]}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\n  Full result saved to: {output_path}")

    # Save chat history
    _chat_log.save(result)

    return result


def run_voice(trace_id, save_to_db=False):
    """Run Voice Compass with verbose output."""
    global _chat_log
    from ee.agenthub.traceerroragent.voice_compass import VoiceCompassAgent

    _chat_log = ChatLog(trace_id, mode="voice")
    patch_voice_verbose()

    header(f"Voice Agent Compass — Trace: {trace_id}")

    if not VoiceCompassAgent.is_voice_trace(trace_id):
        print(f"{RED}ERROR: Trace {trace_id} is not a voice trace{RESET}")
        return

    start = time.time()
    agent = VoiceCompassAgent(trace_id=trace_id, save_to_db=save_to_db)

    subheader("Extraction", CYAN)
    # Quick extraction preview
    from ee.agenthub.traceerroragent.voice_summary_builder import VoiceSummaryBuilder
    from tracer.models.observation_span import ObservationSpan

    span = ObservationSpan.objects.filter(
        trace_id=trace_id, observation_type="conversation"
    ).first()
    data = VoiceSummaryBuilder.extract(span)
    print(f"  Transcript turns: {len(data.transcript)}")
    print(f"  Has audio: {data.has_audio}")
    print(f"  Has tool calls: {data.has_tool_calls}")
    print(f"  Ended reason: {data.ended_reason}")
    print(f"  LLM model: {data.llm_model_name}")

    subheader("Running Full Pipeline", GREEN)
    result = agent.summarize()
    elapsed = time.time() - start

    # Print final results
    header(f"RESULTS ({elapsed:.1f}s)", GREEN)
    summary = result.get("summary", {})
    print(f"  Overall score: {summary.get('overall_score', '?')}")
    print(f"  Error count:   {summary.get('error_count', '?')}")
    print(f"  Priority:      {summary.get('recommended_priority', '?')}")

    insights = summary.get("insights", "")
    if insights:
        print(f"\n  {BOLD}Insights:{RESET}")
        print(indent(insights[:400], "  "))

    scores = result.get("scores", {})
    print(f"\n  {BOLD}Scores:{RESET}")
    for dim in ["factual_grounding", "privacy_and_safety", "instruction_adherence", "optimal_plan_execution"]:
        s = scores.get(dim, {})
        if isinstance(s, dict):
            print(f"    {dim}: {s.get('score', '?')} — {s.get('reason', '')[:100]}")

    errors = result.get("errors", [])
    print(f"\n  {BOLD}Errors ({len(errors)}):{RESET}")
    for err in errors:
        impact = err.get("impact", "?")
        color = RED if impact == "HIGH" else YELLOW if impact == "MEDIUM" else DIM
        print(f"    {color}[{impact}] {err.get('category', '?')}{RESET}")
        print(f"      {DIM}{err.get('description', '')[:150]}{RESET}")

    tu = result.get("token_usage", {})
    if tu:
        print(f"\n  {BOLD}Token Usage:{RESET}")
        grand_total = 0
        for stage, usage in tu.items():
            if isinstance(usage, dict) and usage.get("total_tokens"):
                total = usage.get("total_tokens", 0)
                prompt = usage.get("prompt_tokens", 0)
                completion = usage.get("completion_tokens", 0)
                cached = usage.get("cache_read_input_tokens", 0)
                print(f"    {stage}: {total} tokens (prompt={prompt}, completion={completion}, cached={cached})")
                grand_total += total
        print(f"    {BOLD}TOTAL: {grand_total} tokens{RESET}")

    output_path = f"/tmp/voice_compass_verbose_{trace_id[:8]}.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\n  Full result saved to: {output_path}")

    # Save chat history
    _chat_log.save(result)

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_verbose.py <trace_id> [--extract-only]")
        print("  MODE=voice for voice traces, MODE=text (default) for text traces")
        sys.exit(1)

    trace_id = sys.argv[1]
    mode = os.getenv("MODE", "text").lower()
    save_to_db = os.getenv("SAVE_TO_DB", "0") == "1"

    if mode == "voice":
        run_voice(trace_id, save_to_db)
    else:
        run_text_v2(trace_id, save_to_db)
