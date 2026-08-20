"""
EvalOrchestrator + EvalScout — Stress Test Runner
==================================================
Standalone script (no pytest needed). Run from inside the Docker container:

    docker exec backend bash -c \
        "cd /app/backend && PYTHONPATH=/app/backend \
         python agentic_eval/agenthub/eval_orchestrator/run_stress_tests.py"

Two test groups:
  1. UNIT  — mocked LLM, instant, covers edge-cases / failure modes
  2. LIVE  — real Turing API calls (Scout + Orchestrator), set LIVE=1 env var to enable

At the end, a table is printed with PASS / FAIL / SKIP for every test case.
"""

import json
import os
import sys
import textwrap
import traceback
import types
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

# ── Django bootstrap ─────────────────────────────────────────────────────────
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()
# ─────────────────────────────────────────────────────────────────────────────

from ee.agenthub.eval_orchestrator.orchestrator import (
    EvalConfig,
    EvalOrchestrator,

)
from ee.agenthub.eval_orchestrator.scout import EvalScout, _SAFE_DEFAULTS

LIVE = os.environ.get("LIVE", "").lower() in ("1", "true", "yes")

# ─────────────────────────────────────────────────────────────────────────────
# Result tracking
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    group: str
    passed: bool
    skipped: bool = False
    error: str = ""
    notes: str = ""


_results: List[TestResult] = []


def _run(name: str, group: str, fn) -> TestResult:
    sys.stdout.write(f"  {name} ... ")
    sys.stdout.flush()
    try:
        notes = fn() or ""
        r = TestResult(name=name, group=group, passed=True, notes=str(notes))
        print("PASS" + (f"  ({notes})" if notes else ""))
    except _Skip as e:
        r = TestResult(name=name, group=group, passed=False, skipped=True, notes=str(e))
        print(f"SKIP  ({e})")
    except Exception as e:
        r = TestResult(name=name, group=group, passed=False, error=str(e),
                       notes=traceback.format_exc())
        print(f"FAIL  {e}")
    _results.append(r)
    return r


class _Skip(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_config(**kw) -> EvalConfig:
    defaults = dict(
        criteria="Is the response polite?",
        input_scope="span",
        source_id="span-001",
        eval_template_id="tmpl-001",
        choices=["Passed", "Failed"],
    )
    defaults.update(kw)
    return EvalConfig(**defaults)


def _make_brief(**kw) -> dict:
    b = {
        "complexity": "simple",
        "recommended_model": "small",
        "criteria_needs_formalization": False,
        "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
        "resource_plan": {"use_kb": False, "kb_query_hints": [], "use_feedback": False, "use_mcp_tools": []},
        "reasoning": "Simple check.",
        "available_resources": {},
    }
    b.update(kw)
    return b


def _make_orch(config=None, brief=None, **kw) -> EvalOrchestrator:
    if config is None:
        config = _make_config(**kw)
    if brief is None:
        brief = _make_brief()
    return EvalOrchestrator(eval_config=config, scout_brief=brief)


def _mock_turing(orch: EvalOrchestrator, responses: list):
    """Patch orch.turing_client to return scripted responses."""
    idx = [0]

    def _fake(model, messages, **kwargs):
        r = responses[idx[0]] if idx[0] < len(responses) else "done."
        idx[0] += 1
        return r

    orch.turing_client = MagicMock()
    orch.turing_client.chat_completion.side_effect = _fake
    orch.turing_client.token_usage = {"prompt_tokens": 100, "completion_tokens": 50}


def _tc(payload: dict) -> str:
    """Wrap a tool-call dict into the expected <tool_call> string."""
    return f'<tool_call>\n{json.dumps(payload)}\n</tool_call>'


# ─────────────────────────────────────────────────────────────────────────────
# ── GROUP 1: Scout unit tests ────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _scout_bare_json():
    scout = EvalScout.__new__(EvalScout)
    payload = {
        "complexity": "simple", "recommended_model": "flash",
        "criteria_needs_formalization": False,
        "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
        "resource_plan": {"use_kb": False, "kb_query_hints": [], "use_feedback": False, "use_mcp_tools": []},
        "reasoning": "ok",
    }
    r = scout._parse_response(json.dumps(payload))
    assert r["complexity"] == "simple"
    assert r["recommended_model"] == "flash"


def _scout_markdown_fenced():
    scout = EvalScout.__new__(EvalScout)
    payload = {
        "complexity": "moderate", "recommended_model": "small",
        "criteria_needs_formalization": True,
        "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
        "resource_plan": {"use_kb": False, "kb_query_hints": [], "use_feedback": False, "use_mcp_tools": []},
        "reasoning": "needs work",
    }
    fenced = f"```json\n{json.dumps(payload)}\n```"
    r = scout._parse_response(fenced)
    assert r["complexity"] == "moderate"
    assert r["criteria_needs_formalization"] is True


def _scout_prose_embedded():
    scout = EvalScout.__new__(EvalScout)
    inner = json.dumps({
        "complexity": "complex", "recommended_model": "large",
        "criteria_needs_formalization": False,
        "data_plan": {"sufficient": False, "needs_drill_down": ["t1"], "relevant_areas": []},
        "resource_plan": {"use_kb": True, "kb_query_hints": ["policy"], "use_feedback": False, "use_mcp_tools": []},
        "reasoning": "complex",
    })
    r = scout._parse_response(f"Here:\n{inner}\nEnd.")
    assert r["complexity"] == "complex"
    assert r["resource_plan"]["use_kb"] is True


def _scout_invalid_complexity_coerced():
    scout = EvalScout.__new__(EvalScout)
    r = scout._parse_response(json.dumps({
        "complexity": "ultra_hard", "recommended_model": "small",
        "criteria_needs_formalization": False,
        "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
        "resource_plan": {"use_kb": False, "kb_query_hints": [], "use_feedback": False, "use_mcp_tools": []},
        "reasoning": "",
    }))
    assert r["complexity"] == "moderate"


def _scout_invalid_model_coerced():
    scout = EvalScout.__new__(EvalScout)
    r = scout._parse_response(json.dumps({
        "complexity": "simple", "recommended_model": "giant",
        "criteria_needs_formalization": False,
        "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
        "resource_plan": {"use_kb": False, "kb_query_hints": [], "use_feedback": False, "use_mcp_tools": []},
        "reasoning": "",
    }))
    assert r["recommended_model"] == "small"


def _scout_empty_string_safe_defaults():
    scout = EvalScout.__new__(EvalScout)
    r = scout._parse_response("")
    assert r["complexity"] == _SAFE_DEFAULTS["complexity"]
    assert "data_plan" in r


def _scout_garbage_safe_defaults():
    scout = EvalScout.__new__(EvalScout)
    r = scout._parse_response("LOL this is not JSON at all!!!")
    assert r["complexity"] == _SAFE_DEFAULTS["complexity"]


def _scout_prompt_has_criteria():
    scout = EvalScout.__new__(EvalScout)
    p = scout._build_prompt(
        "Is it polite?", "User message span",
        {"knowledge_bases": [], "feedback_count": 0, "mcp_tools": []},
    )
    assert "Is it polite?" in p
    assert "User message span" in p
    assert "Knowledge Bases: none connected" in p


def _scout_prompt_includes_kb():
    scout = EvalScout.__new__(EvalScout)
    p = scout._build_prompt(
        "Check policy", "span",
        {"knowledge_bases": [{"id": "kb1", "name": "PII Policy", "description": "handles PII"}],
         "feedback_count": 5, "mcp_tools": []},
    )
    assert "PII Policy" in p
    assert "Feedback examples: 5" in p


# ─────────────────────────────────────────────────────────────────────────────
# ── GROUP 2: Orchestrator parse helpers ──────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _parse_tc_valid():
    content = 'I will inspect.\n<tool_call>\n{"tool": "inspect_data", "params": {"target_type": "span", "target_id": "abc", "depth": "detail"}}\n</tool_call>'
    r = EvalOrchestrator._parse_tool_call_from_response(content)
    assert r is not None
    assert r["tool"] == "inspect_data"
    assert r["params"]["depth"] == "detail"


def _parse_tc_no_call():
    r = EvalOrchestrator._parse_tool_call_from_response("Based on analysis, clearly toxic.")
    assert r is None


def _parse_tc_bad_json():
    r = EvalOrchestrator._parse_tool_call_from_response('<tool_call>\n{bad json\n</tool_call>')
    assert r is None


def _parse_tc_extra_whitespace():
    payload = {"tool": "submit_result", "params": {"result": "Passed", "confidence": 0.95, "explanation": "ok"}}
    content = f'<tool_call>   \n\n{json.dumps(payload)}\n\n   </tool_call>'
    r = EvalOrchestrator._parse_tool_call_from_response(content)
    assert r is not None and r["tool"] == "submit_result"


def _json_parse_bare():
    orch = _make_orch()
    r = orch._parse_json_response('{"result": "Passed", "confidence": 0.9, "explanation": "ok"}')
    assert r["result"] == "Passed"


def _json_parse_fenced():
    orch = _make_orch()
    r = orch._parse_json_response('```json\n{"result": "Failed", "confidence": 0.8, "explanation": "bad"}\n```')
    assert r["result"] == "Failed"


def _json_parse_empty():
    orch = _make_orch()
    assert orch._parse_json_response("") == {}


def _json_parse_garbage():
    orch = _make_orch()
    assert orch._parse_json_response("not json!") == {}


def _eval_parse_valid():
    orch = _make_orch()
    r = orch._parse_eval_response('{"result": "Passed", "confidence": 0.95, "explanation": "polite"}', ["Passed", "Failed"])
    assert r["result"] == "Passed"
    assert abs(r["confidence"] - 0.95) < 1e-6


def _eval_parse_case_insensitive():
    orch = _make_orch()
    r = orch._parse_eval_response('{"result": "passed", "confidence": 0.8, "explanation": "ok"}', ["Passed", "Failed"])
    assert r["result"] == "Passed"


def _eval_parse_unknown_falls_back():
    orch = _make_orch()
    r = orch._parse_eval_response('{"result": "Maybe", "confidence": 0.7, "explanation": "unsure"}', ["Passed", "Failed"])
    assert r["result"] == "Passed"
    assert r["confidence"] <= 0.5


def _eval_parse_confidence_clamped_high():
    orch = _make_orch()
    r = orch._parse_eval_response('{"result": "Passed", "confidence": 2.5, "explanation": "great"}', ["Passed", "Failed"])
    assert r["confidence"] <= 1.0


def _eval_parse_confidence_clamped_low():
    orch = _make_orch()
    r = orch._parse_eval_response('{"result": "Failed", "confidence": -0.5, "explanation": "bad"}', ["Passed", "Failed"])
    assert r["confidence"] >= 0.0


# ─────────────────────────────────────────────────────────────────────────────
# ── GROUP 3: Orchestrator tool dispatch ──────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _tool_unknown_returns_error():
    orch = _make_orch()
    r = orch._execute_tool("nonexistent_tool", {})
    assert "error" in r
    assert "Unknown tool" in r["error"]


def _tool_submit_sets_final_result():
    orch = _make_orch()
    r = orch._execute_tool("submit_result", {"result": "Passed", "confidence": 0.9, "explanation": "Looks polite."})
    assert r == {"status": "submitted"}
    assert orch._investigation_complete
    assert orch._final_result["result"] == "Passed"


def _tool_submit_marks_complete():
    orch = _make_orch()
    orch._execute_tool("submit_result", {"result": "Failed", "confidence": 0.6, "explanation": "Rude tone."})
    assert orch._investigation_complete


def _tool_run_eval_blocked_at_limit():
    orch = _make_orch()
    orch._eval_calls = EvalOrchestrator.MAX_EVAL_CALLS
    r = orch._tool_run_eval(criteria="Is it polite?", data="Hello!", choices=["Passed", "Failed"], model="small")
    assert "error" in r
    assert "Maximum eval calls" in r["error"]


def _tool_submit_confidence_as_string():
    orch = _make_orch()
    orch._execute_tool("submit_result", {"result": "Passed", "confidence": "0.85", "explanation": "ok"})
    assert isinstance(orch._final_result["confidence"], float)
    assert abs(orch._final_result["confidence"] - 0.85) < 1e-6


def _tool_missing_required_args_returns_error():
    orch = _make_orch()
    r = orch._execute_tool("inspect_data", {})
    assert "error" in r


# ─────────────────────────────────────────────────────────────────────────────
# ── GROUP 4: Tool list & description builders ─────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _tool_list_base_tools_present():
    orch = _make_orch()
    names = [t["function"]["name"] for t in orch.tools]
    for required in ["inspect_data", "search_data", "formalize_criteria", "run_eval", "submit_result"]:
        assert required in names, f"Missing required tool: {required}"


def _tool_list_kb_tool_added():
    config = _make_config(kb_id="kb-123")
    orch = _make_orch(config=config)
    names = [t["function"]["name"] for t in orch.tools]
    assert "search_kb" in names


def _tool_list_kb_tool_absent():
    orch = _make_orch()
    names = [t["function"]["name"] for t in orch.tools]
    assert "search_kb" not in names


def _tool_list_feedback_tool_added():
    brief = _make_brief(available_resources={"feedback_count": 5, "mcp_tools": []})
    orch = _make_orch(brief=brief)
    names = [t["function"]["name"] for t in orch.tools]
    assert "get_feedback_examples" in names


def _tool_list_feedback_tool_absent():
    brief = _make_brief(available_resources={"feedback_count": 0, "mcp_tools": []})
    orch = _make_orch(brief=brief)
    names = [t["function"]["name"] for t in orch.tools]
    assert "get_feedback_examples" not in names


def _tool_list_mcp_tool_added():
    brief = _make_brief(available_resources={"feedback_count": 0, "mcp_tools": [{"name": "Grammarly"}]})
    orch = _make_orch(brief=brief)
    names = [t["function"]["name"] for t in orch.tools]
    assert "call_mcp_tool" in names


def _tools_desc_names_appear():
    orch = _make_orch()
    desc = orch._build_tools_description(orch.tools)
    assert "inspect_data" in desc
    assert "run_eval" in desc
    assert "submit_result" in desc


def _tools_desc_required_marked():
    orch = _make_orch()
    desc = orch._build_tools_description(orch.tools)
    assert "(required)" in desc


def _tools_desc_optional_marked():
    orch = _make_orch()
    desc = orch._build_tools_description(orch.tools)
    assert "(optional)" in desc


# ─────────────────────────────────────────────────────────────────────────────
# ── GROUP 5: Context compaction ──────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _compaction_no_compaction_under_limit():
    orch = _make_orch()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "start"}]
    for i in range(EvalOrchestrator.COMPACT_KEEP_RECENT - 1):
        msgs.append({"role": "assistant", "content": "..."})
        msgs.append({"role": "user", "content": f"Tool result for inspect_data:\n" + ("x" * 500)})
    original = [m["content"] for m in msgs]
    orch._compact_old_tool_results(msgs)
    for orig, m in zip(original, msgs):
        assert orig == m["content"]


def _compaction_at_limit():
    orch = _make_orch()
    keep = EvalOrchestrator.COMPACT_KEEP_RECENT
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "start"}]
    for i in range(keep + 2):
        msgs.append({"role": "assistant", "content": "..."})
        msgs.append({"role": "user", "content": f"Tool result for inspect_data:\n" + ("x" * 500)})
    orch._compact_old_tool_results(msgs)
    compacted = [m for m in msgs
                 if m.get("role") == "user" and "Tool result for" in m.get("content", "")
                 and "_compacted" in m.get("content", "")]
    assert len(compacted) > 0


# ─────────────────────────────────────────────────────────────────────────────
# ── GROUP 6: _build_final_result ─────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _final_result_with_submission():
    orch = _make_orch()
    orch._final_result = {"result": "Passed", "confidence": 0.9, "explanation": "Great."}
    orch._model_used = "small"
    orch._eval_calls = 1
    orch._orchestrator_turns = 3
    out = orch._build_final_result()
    assert out["result"]["result"] == "Passed"
    assert out["model_used"] == "small"
    assert out["eval_calls"] == 1
    assert out["orchestrator_turns"] == 3


def _final_result_without_submission():
    orch = _make_orch()
    out = orch._build_final_result()
    assert out["result"]["result"] is None
    assert out["result"]["confidence"] == 0.0
    assert "did not complete" in out["result"]["explanation"]


def _final_result_token_usage_keys():
    orch = _make_orch()
    out = orch._build_final_result()
    for key in ("scout", "formalization", "orchestrator", "eval_calls", "total"):
        assert key in out["token_usage"], f"Missing token_usage key: {key}"


# ─────────────────────────────────────────────────────────────────────────────
# ── GROUP 7: Full loop (mocked LLM) ──────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _loop_inspect_run_eval_submit():
    orch = _make_orch()
    responses = [
        _tc({"tool": "inspect_data", "params": {"target_type": "span", "target_id": "span-001", "depth": "detail"}}),
        _tc({"tool": "run_eval", "params": {"criteria": "Is it polite?", "data": "Hello!", "choices": ["Passed", "Failed"], "model": "small"}}),
        _tc({"tool": "submit_result", "params": {"result": "Passed", "confidence": 0.95, "explanation": "Warm and helpful."}}),
    ]
    _mock_turing(orch, responses)
    orch._tool_inspect_data = MagicMock(return_value={"output": "Hello there!"})
    orch._tool_run_eval = MagicMock(return_value={"result": "Passed", "confidence": 0.95, "explanation": "Polite.", "model_used": "turing_small"})
    result = orch.run()
    assert result["result"]["result"] == "Passed"
    assert abs(result["result"]["confidence"] - 0.95) < 1e-6
    assert result["orchestrator_turns"] >= 3
    return f"turns={result['orchestrator_turns']}"


def _loop_no_tool_call_terminates():
    orch = _make_orch()
    _mock_turing(orch, ["I have finished my analysis. The response is polite."])
    result = orch.run()
    assert result["orchestrator_turns"] == 1
    assert result["result"]["result"] is None
    return "graceful, no tool call"


def _loop_max_turns_graceful():
    orch = _make_orch()
    orch.MAX_TURNS = 3
    _mock_turing(orch, [_tc({"tool": "inspect_data", "params": {"target_type": "span", "target_id": "x", "depth": "overview"}})] * 10)
    orch._tool_inspect_data = MagicMock(return_value={"span_id": "x", "status": "success"})
    result = orch.run()
    assert result["orchestrator_turns"] == 3
    assert result["result"]["result"] is None
    return "max turns hit, graceful"


def _loop_llm_exception_graceful():
    orch = _make_orch()
    orch.turing_client = MagicMock()
    orch.turing_client.chat_completion.side_effect = RuntimeError("Network error")
    orch.turing_client.token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    result = orch.run()
    assert result["orchestrator_turns"] == 1
    assert result["result"]["result"] is None
    return "exception caught, graceful"


def _loop_formalize_then_submit():
    brief = _make_brief(criteria_needs_formalization=True)
    orch = _make_orch(brief=brief)
    responses = [
        _tc({"tool": "formalize_criteria", "params": {"raw_criteria": "Is it good?", "data_summary": "a span", "choices": ["Passed", "Failed"], "response_format": "boolean"}}),
        _tc({"tool": "run_eval", "params": {"criteria": "Detailed criteria", "data": "Hello!", "choices": ["Passed", "Failed"], "model": "small"}}),
        _tc({"tool": "submit_result", "params": {"result": "Passed", "confidence": 0.88, "explanation": "Good."}}),
    ]
    _mock_turing(orch, responses)
    orch._tool_formalize_criteria = MagicMock(return_value={
        "formalized_criteria": "Evaluate whether the response is helpful and polite.",
        "choices": ["Passed", "Failed"], "response_format": "boolean", "reasoning": "Expanded.",
    })
    orch._tool_run_eval = MagicMock(return_value={"result": "Passed", "confidence": 0.88, "explanation": "Good.", "model_used": "turing_small"})
    result = orch.run()
    orch._tool_formalize_criteria.assert_called_once()
    orch._tool_run_eval.assert_called_once()
    assert result["result"]["result"] == "Passed"
    return f"formalize→run_eval→submit, turns={result['orchestrator_turns']}"


def _loop_token_usage_accumulates():
    orch = _make_orch()
    responses = [_tc({"tool": "submit_result", "params": {"result": "Passed", "confidence": 0.9, "explanation": "ok"}})]
    _mock_turing(orch, responses)
    orch.run()
    assert orch._orchestrator_token_usage["prompt_tokens"] > 0
    return f"prompt_tokens={orch._orchestrator_token_usage['prompt_tokens']}"


def _loop_single_choice():
    orch = _make_orch(choices=["Yes"])
    r = orch._parse_eval_response('{"result": "Yes", "confidence": 0.99, "explanation": "clearly"}', ["Yes"])
    assert r["result"] == "Yes"


def _loop_empty_choices_no_crash():
    orch = _make_orch()
    r = orch._parse_eval_response('{"result": "Maybe", "confidence": 0.5, "explanation": "unsure"}', [])
    assert "result" in r


def _loop_kb_search_dispatched():
    config = _make_config(kb_id="kb-test-001")
    orch = _make_orch(config=config)
    responses = [
        _tc({"tool": "search_kb", "params": {"query": "toxicity policy", "kb_id": "kb-test-001"}}),
        _tc({"tool": "submit_result", "params": {"result": "Passed", "confidence": 0.8, "explanation": "checked kb"}}),
    ]
    _mock_turing(orch, responses)
    orch._tool_search_kb = MagicMock(return_value={"results": [{"content": "Policy doc..."}]})
    result = orch.run()
    orch._tool_search_kb.assert_called_once()
    assert result["result"]["result"] == "Passed"
    return "search_kb dispatched"


def _loop_multiple_inspect_then_submit():
    """Model calls inspect_data twice (different depths) before submitting."""
    orch = _make_orch()
    responses = [
        _tc({"tool": "inspect_data", "params": {"target_type": "span", "target_id": "span-001", "depth": "overview"}}),
        _tc({"tool": "inspect_data", "params": {"target_type": "span", "target_id": "span-001", "depth": "detail"}}),
        _tc({"tool": "submit_result", "params": {"result": "Failed", "confidence": 0.92, "explanation": "Offensive language."}}),
    ]
    _mock_turing(orch, responses)
    orch._tool_inspect_data = MagicMock(return_value={"output": "You are worthless!"})
    result = orch.run()
    assert result["result"]["result"] == "Failed"
    assert orch._tool_inspect_data.call_count == 2
    return f"2x inspect_data, turns={result['orchestrator_turns']}"


def _loop_run_eval_multiple_choices():
    """5-way classification (sentiment labels)."""
    config = _make_config(choices=["Very Positive", "Positive", "Neutral", "Negative", "Very Negative"])
    orch = _make_orch(config=config)
    responses = [
        _tc({"tool": "run_eval", "params": {"criteria": "Sentiment", "data": "Great product!", "choices": ["Very Positive", "Positive", "Neutral", "Negative", "Very Negative"], "model": "small"}}),
        _tc({"tool": "submit_result", "params": {"result": "Very Positive", "confidence": 0.9, "explanation": "Enthusiastic praise."}}),
    ]
    _mock_turing(orch, responses)
    orch._tool_run_eval = MagicMock(return_value={"result": "Very Positive", "confidence": 0.9, "explanation": "Enthusiastic.", "model_used": "turing_small"})
    result = orch.run()
    assert result["result"]["result"] == "Very Positive"
    return "5-way classification works"


# ─────────────────────────────────────────────────────────────────────────────
# ── GROUP 8: LIVE tests (Turing API) ─────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def _live_scout_toxicity():
    if not LIVE:
        raise _Skip("LIVE=0")
    scout = EvalScout()
    brief = scout.run(
        criteria="Is the text toxic?",
        input_summary="SPAN: msg\nOutput: You are worthless.",
        available_resources={"knowledge_bases": [], "feedback_count": 0, "mcp_tools": []},
    )
    assert "complexity" in brief
    assert "recommended_model" in brief
    return f"complexity={brief['complexity']}, model={brief['recommended_model']}"


def _live_scout_politeness():
    if not LIVE:
        raise _Skip("LIVE=0")
    scout = EvalScout()
    brief = scout.run(
        criteria="Is the response polite and helpful?",
        input_summary="SPAN: chat\nOutput: Thank you for your patience!",
        available_resources={"knowledge_bases": [], "feedback_count": 0, "mcp_tools": []},
    )
    assert "complexity" in brief
    return f"complexity={brief['complexity']}"


def _live_orchestrator_toxic_text():
    if not LIVE:
        raise _Skip("LIVE=0")

    SPAN_OUTPUT = "You are absolutely worthless and I hope something terrible happens to you."
    config = EvalConfig(
        criteria="Is the text toxic?",
        input_scope="span", source_id="fake-span-001",
        eval_template_id="fake-tmpl-001", choices=["Toxic", "Not Toxic"],
    )
    brief = _make_brief(criteria_needs_formalization=True, available_resources={
        "knowledge_bases": [], "feedback_count": 0, "mcp_tools": [],
    })
    orch = EvalOrchestrator(eval_config=config, scout_brief=brief)

    def _mock_inspect(self, target_type, target_id, depth):
        if depth == "detail":
            self._drill_down_performed = True
        return {"span_id": target_id, "type": "llm", "status": "success",
                "input": "Rate this message", "output": SPAN_OUTPUT, "model": "gpt-4"}

    orch._tool_inspect_data = types.MethodType(_mock_inspect, orch)
    result = orch.run()
    assert result["result"]["result"] in ["Toxic", "Not Toxic"]
    r = result["result"]["result"]
    c = result["result"]["confidence"]
    t = result["orchestrator_turns"]
    e = result["eval_calls"]
    return f"result={r} | confidence={c:.2f} | turns={t} | eval_calls={e}"


def _live_orchestrator_polite_response():
    if not LIVE:
        raise _Skip("LIVE=0")

    SPAN_OUTPUT = "Thank you for contacting us. I'd be happy to help you resolve this issue right away!"
    config = EvalConfig(
        criteria="Is the customer service response polite and helpful?",
        input_scope="span", source_id="fake-span-002",
        eval_template_id="fake-tmpl-002", choices=["Passed", "Failed"],
    )
    brief = _make_brief(available_resources={"knowledge_bases": [], "feedback_count": 0, "mcp_tools": []})
    orch = EvalOrchestrator(eval_config=config, scout_brief=brief)
    orch._tool_inspect_data = MagicMock(return_value={
        "span_id": "fake-span-002", "type": "llm", "status": "success",
        "input": "Can you help me?", "output": SPAN_OUTPUT,
    })
    result = orch.run()
    assert result["result"]["result"] in ["Passed", "Failed"]
    r = result["result"]["result"]
    c = result["result"]["confidence"]
    return f"result={r} | confidence={c:.2f}"


def _live_orchestrator_ambiguous_criteria():
    if not LIVE:
        raise _Skip("LIVE=0")
    config = EvalConfig(
        criteria="Is this good?",
        input_scope="span", source_id="fake-span-003",
        eval_template_id="fake-tmpl-003", choices=["Yes", "No"],
    )
    brief = _make_brief(criteria_needs_formalization=True, available_resources={
        "knowledge_bases": [], "feedback_count": 0, "mcp_tools": [],
    })
    orch = EvalOrchestrator(eval_config=config, scout_brief=brief)
    orch._tool_inspect_data = MagicMock(return_value={"output": "The product quality exceeded expectations."})
    result = orch.run()
    assert result["result"]["result"] in ["Yes", "No", None]
    r = result["result"]["result"]
    t = result["orchestrator_turns"]
    return f"result={r} | turns={t} (ambiguous criteria test)"


# ─────────────────────────────────────────────────────────────────────────────
# ── Main: register and run all tests ─────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

TESTS = [
    # (group, name, fn)
    ("Scout",       "bare JSON",                            _scout_bare_json),
    ("Scout",       "markdown fenced JSON",                 _scout_markdown_fenced),
    ("Scout",       "JSON embedded in prose",               _scout_prose_embedded),
    ("Scout",       "invalid complexity → coerced",         _scout_invalid_complexity_coerced),
    ("Scout",       "invalid model → coerced",              _scout_invalid_model_coerced),
    ("Scout",       "empty string → safe defaults",         _scout_empty_string_safe_defaults),
    ("Scout",       "garbage → safe defaults",              _scout_garbage_safe_defaults),
    ("Scout",       "prompt includes criteria",             _scout_prompt_has_criteria),
    ("Scout",       "prompt includes KB names",             _scout_prompt_includes_kb),
    ("ParseHelpers","tool_call valid",                      _parse_tc_valid),
    ("ParseHelpers","tool_call absent → None",              _parse_tc_no_call),
    ("ParseHelpers","tool_call bad JSON → None",            _parse_tc_bad_json),
    ("ParseHelpers","tool_call extra whitespace",           _parse_tc_extra_whitespace),
    ("ParseHelpers","json_parse bare",                      _json_parse_bare),
    ("ParseHelpers","json_parse fenced",                    _json_parse_fenced),
    ("ParseHelpers","json_parse empty → {}",                _json_parse_empty),
    ("ParseHelpers","json_parse garbage → {}",              _json_parse_garbage),
    ("ParseHelpers","eval_parse valid",                     _eval_parse_valid),
    ("ParseHelpers","eval_parse case-insensitive",          _eval_parse_case_insensitive),
    ("ParseHelpers","eval_parse unknown → first choice",    _eval_parse_unknown_falls_back),
    ("ParseHelpers","eval_parse confidence clamped high",   _eval_parse_confidence_clamped_high),
    ("ParseHelpers","eval_parse confidence clamped low",    _eval_parse_confidence_clamped_low),
    ("ToolDispatch","unknown tool → error",                 _tool_unknown_returns_error),
    ("ToolDispatch","submit_result sets final result",      _tool_submit_sets_final_result),
    ("ToolDispatch","submit_result marks complete",         _tool_submit_marks_complete),
    ("ToolDispatch","run_eval blocked at MAX_EVAL_CALLS",   _tool_run_eval_blocked_at_limit),
    ("ToolDispatch","submit confidence as string",          _tool_submit_confidence_as_string),
    ("ToolDispatch","missing required args → error",        _tool_missing_required_args_returns_error),
    ("ToolList",    "base tools always present",            _tool_list_base_tools_present),
    ("ToolList",    "search_kb added when kb_id set",       _tool_list_kb_tool_added),
    ("ToolList",    "search_kb absent without kb_id",       _tool_list_kb_tool_absent),
    ("ToolList",    "feedback tool added when count > 0",   _tool_list_feedback_tool_added),
    ("ToolList",    "feedback tool absent when count = 0",  _tool_list_feedback_tool_absent),
    ("ToolList",    "mcp tool added when mcp_tools present",_tool_list_mcp_tool_added),
    ("ToolList",    "desc includes tool names",             _tools_desc_names_appear),
    ("ToolList",    "desc marks required params",           _tools_desc_required_marked),
    ("ToolList",    "desc marks optional params",           _tools_desc_optional_marked),
    ("Compaction",  "no compaction under limit",            _compaction_no_compaction_under_limit),
    ("Compaction",  "compacts messages at limit",           _compaction_at_limit),
    ("FinalResult", "with submission",                      _final_result_with_submission),
    ("FinalResult", "without submission → graceful failure",_final_result_without_submission),
    ("FinalResult", "token usage keys present",             _final_result_token_usage_keys),
    ("FullLoop",    "inspect → run_eval → submit",          _loop_inspect_run_eval_submit),
    ("FullLoop",    "no tool call → terminates",            _loop_no_tool_call_terminates),
    ("FullLoop",    "max turns → graceful failure",         _loop_max_turns_graceful),
    ("FullLoop",    "LLM exception → graceful failure",     _loop_llm_exception_graceful),
    ("FullLoop",    "formalize → run_eval → submit",        _loop_formalize_then_submit),
    ("FullLoop",    "token usage accumulates",              _loop_token_usage_accumulates),
    ("FullLoop",    "single-choice eval",                   _loop_single_choice),
    ("FullLoop",    "empty choices — no crash",             _loop_empty_choices_no_crash),
    ("FullLoop",    "search_kb dispatched",                 _loop_kb_search_dispatched),
    ("FullLoop",    "multiple inspect_data calls",          _loop_multiple_inspect_then_submit),
    ("FullLoop",    "5-way classification",                 _loop_run_eval_multiple_choices),
    ("Live",        "Scout — toxicity",                     _live_scout_toxicity),
    ("Live",        "Scout — politeness",                   _live_scout_politeness),
    ("Live",        "Orchestrator — toxic text",            _live_orchestrator_toxic_text),
    ("Live",        "Orchestrator — polite response",       _live_orchestrator_polite_response),
    ("Live",        "Orchestrator — ambiguous criteria",    _live_orchestrator_ambiguous_criteria),
]


def _print_summary():
    passed = [r for r in _results if r.passed]
    failed = [r for r in _results if not r.passed and not r.skipped]
    skipped = [r for r in _results if r.skipped]
    total = len(_results)

    print()
    print("━" * 70)
    print("  TEST SUMMARY")
    print("━" * 70)
    print(f"  Total   : {total}")
    print(f"  Passed  : {len(passed)}")
    print(f"  Failed  : {len(failed)}")
    print(f"  Skipped : {len(skipped)}")
    print()

    if failed:
        print("  FAILURES:")
        for r in failed:
            print(f"    [FAIL] [{r.group}] {r.name}")
            if r.error:
                for line in r.error.splitlines()[:3]:
                    print(f"           {line}")
        print()

    # Group summary table
    groups = {}
    for r in _results:
        groups.setdefault(r.group, []).append(r)

    print(f"  {'Group':<16} {'Pass':>5} {'Fail':>5} {'Skip':>5}")
    print(f"  {'-'*16} {'-'*5} {'-'*5} {'-'*5}")
    for g, rs in groups.items():
        p = sum(1 for r in rs if r.passed)
        f = sum(1 for r in rs if not r.passed and not r.skipped)
        s = sum(1 for r in rs if r.skipped)
        print(f"  {g:<16} {p:>5} {f:>5} {s:>5}")

    print("━" * 70)
    status = "ALL PASSED" if not failed else f"{len(failed)} FAILED"
    print(f"  Result  : {status}")
    print("━" * 70)
    return len(failed)


def main():
    print("=" * 70)
    print("  EvalOrchestrator + EvalScout — Stress Tests")
    print(f"  Mode: {'LIVE (Turing API)' if LIVE else 'UNIT (mocked LLM)'}")
    print("=" * 70)
    print()

    current_group = None
    for group, name, fn in TESTS:
        if group != current_group:
            current_group = group
            print(f"\n── {group} {'─' * (65 - len(group))}")
        _run(name, group, fn)

    n_failed = _print_summary()
    sys.exit(0 if n_failed == 0 else 1)


if __name__ == "__main__":
    main()
