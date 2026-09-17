"""
Stress tests for EvalOrchestrator + EvalScout.

Covers:
  - Scout: JSON parsing, safe defaults, valid/invalid/malformed responses
  - Orchestrator: tool dispatch, loop termination, max turns/eval guards,
    context compaction, JSON parsing helper, prompt builders
  - Integration: full pipeline with mocked LLM (no network calls)

Run with:
    docker exec backend bash -c "cd /app/backend && PYTHONPATH=/app/backend \
        python -m pytest agentic_eval/agenthub/eval_orchestrator/test_orchestrator.py -v"
"""

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

# Django bootstrap is handled by conftest.py (pytest_configure hook).
from ee.agenthub.eval_orchestrator.orchestrator import (
    EvalConfig,
    EvalOrchestrator,
)
from ee.agenthub.eval_orchestrator.scout import EvalScout, _SAFE_DEFAULTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> EvalConfig:
    defaults = dict(
        criteria="Is the response polite?",
        input_scope="span",
        source_id="span-abc",
        eval_template_id="tmpl-1",
        choices=["Passed", "Failed"],
    )
    defaults.update(overrides)
    return EvalConfig(**defaults)


def _make_scout_brief(**overrides) -> dict:
    brief = {
        "complexity": "simple",
        "recommended_model": "small",
        "criteria_needs_formalization": False,
        "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
        "resource_plan": {
            "use_kb": False,
            "kb_query_hints": [],
            "use_feedback": False,
            "use_mcp_tools": [],
        },
        "reasoning": "Simple binary check.",
        "available_resources": {},
    }
    brief.update(overrides)
    return brief


def _make_orchestrator(config=None, brief=None, **oc_overrides) -> EvalOrchestrator:
    config = config or _make_config(**oc_overrides)
    brief = brief or _make_scout_brief()
    return EvalOrchestrator(eval_config=config, scout_brief=brief)


# ---------------------------------------------------------------------------
# ── Scout tests ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestEvalScoutParsing(unittest.TestCase):
    """EvalScout._parse_response / _validate_brief"""

    def setUp(self):
        self.scout = EvalScout.__new__(EvalScout)

    def _parse(self, text: str) -> dict:
        return self.scout._parse_response(text)

    # ── valid inputs ─────────────────────────────────────────────────────────

    def test_bare_json(self):
        payload = {
            "complexity": "simple",
            "recommended_model": "flash",
            "criteria_needs_formalization": False,
            "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
            "resource_plan": {"use_kb": False, "kb_query_hints": [], "use_feedback": False, "use_mcp_tools": []},
            "reasoning": "ok",
        }
        result = self._parse(json.dumps(payload))
        self.assertEqual(result["complexity"], "simple")
        self.assertEqual(result["recommended_model"], "flash")
        self.assertFalse(result["criteria_needs_formalization"])

    def test_markdown_fenced_json(self):
        payload = {"complexity": "moderate", "recommended_model": "small",
                   "criteria_needs_formalization": True,
                   "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
                   "resource_plan": {"use_kb": False, "kb_query_hints": [], "use_feedback": False, "use_mcp_tools": []},
                   "reasoning": "needs work"}
        fenced = f"```json\n{json.dumps(payload)}\n```"
        result = self._parse(fenced)
        self.assertEqual(result["complexity"], "moderate")
        self.assertTrue(result["criteria_needs_formalization"])

    def test_json_embedded_in_prose(self):
        inner = json.dumps({
            "complexity": "complex",
            "recommended_model": "large",
            "criteria_needs_formalization": False,
            "data_plan": {"sufficient": False, "needs_drill_down": ["t1"], "relevant_areas": []},
            "resource_plan": {"use_kb": True, "kb_query_hints": ["policy"], "use_feedback": False, "use_mcp_tools": []},
            "reasoning": "complex eval",
        })
        result = self._parse(f"Here is my analysis:\n{inner}\nEnd of analysis.")
        self.assertEqual(result["complexity"], "complex")
        self.assertEqual(result["recommended_model"], "large")

    # ── invalid enum values are coerced ──────────────────────────────────────

    def test_invalid_complexity_coerced_to_moderate(self):
        payload = {"complexity": "ultra_hard", "recommended_model": "small",
                   "criteria_needs_formalization": False,
                   "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
                   "resource_plan": {"use_kb": False, "kb_query_hints": [], "use_feedback": False, "use_mcp_tools": []},
                   "reasoning": ""}
        result = self._parse(json.dumps(payload))
        self.assertEqual(result["complexity"], "moderate")

    def test_invalid_model_coerced_to_small(self):
        payload = {"complexity": "simple", "recommended_model": "giant",
                   "criteria_needs_formalization": False,
                   "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
                   "resource_plan": {"use_kb": False, "kb_query_hints": [], "use_feedback": False, "use_mcp_tools": []},
                   "reasoning": ""}
        result = self._parse(json.dumps(payload))
        self.assertEqual(result["recommended_model"], "small")

    # ── missing keys filled from safe defaults ────────────────────────────────

    def test_missing_keys_use_defaults(self):
        result = self._parse('{"complexity": "simple"}')
        self.assertIn("recommended_model", result)
        self.assertIn("data_plan", result)
        self.assertIn("resource_plan", result)

    # ── unparseable → safe defaults ───────────────────────────────────────────

    def test_empty_string_returns_safe_defaults(self):
        result = self._parse("")
        self.assertEqual(result["complexity"], _SAFE_DEFAULTS["complexity"])

    def test_garbage_returns_safe_defaults(self):
        result = self._parse("LOL this is not JSON at all!!!")
        self.assertEqual(result["complexity"], _SAFE_DEFAULTS["complexity"])
        self.assertEqual(result["recommended_model"], _SAFE_DEFAULTS["recommended_model"])

    def test_partial_json_returns_safe_defaults(self):
        result = self._parse('{"complexity": "simple"')  # unclosed brace
        # Should fall back to defaults
        self.assertIn("complexity", result)

    # ── resource_plan flags ───────────────────────────────────────────────────

    def test_resource_plan_use_kb_true(self):
        payload = {"complexity": "complex", "recommended_model": "large",
                   "criteria_needs_formalization": False,
                   "data_plan": {"sufficient": True, "needs_drill_down": [], "relevant_areas": []},
                   "resource_plan": {"use_kb": True, "kb_query_hints": ["PII policy"], "use_feedback": True, "use_mcp_tools": ["grammarly"]},
                   "reasoning": "needs kb"}
        result = self._parse(json.dumps(payload))
        self.assertTrue(result["resource_plan"]["use_kb"])
        self.assertTrue(result["resource_plan"]["use_feedback"])
        self.assertEqual(result["resource_plan"]["use_mcp_tools"], ["grammarly"])


class TestEvalScoutBuildPrompt(unittest.TestCase):
    def setUp(self):
        self.scout = EvalScout.__new__(EvalScout)

    def test_prompt_contains_criteria(self):
        prompt = self.scout._build_prompt(
            "Is it polite?",
            "User message span",
            {"knowledge_bases": [], "feedback_count": 0, "mcp_tools": []},
        )
        self.assertIn("Is it polite?", prompt)
        self.assertIn("User message span", prompt)
        self.assertIn("Knowledge Bases: none connected", prompt)
        self.assertIn("MCP Tools: none connected", prompt)

    def test_prompt_includes_kb_names(self):
        prompt = self.scout._build_prompt(
            "Check policy",
            "span",
            {"knowledge_bases": [{"id": "kb1", "name": "PII Policy", "description": "handles PII"}],
             "feedback_count": 5,
             "mcp_tools": []},
        )
        self.assertIn("PII Policy", prompt)
        self.assertIn("Feedback examples: 5", prompt)

    def test_prompt_includes_mcp_tools(self):
        prompt = self.scout._build_prompt(
            "Check grammar",
            "span",
            {"knowledge_bases": [],
             "feedback_count": 0,
             "mcp_tools": [{"name": "Grammarly", "description": "grammar checker"}]},
        )
        self.assertIn("Grammarly", prompt)
        self.assertIn("grammar checker", prompt)


# ---------------------------------------------------------------------------
# ── Orchestrator unit tests ──────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestOrchestratorToolDispatch(unittest.TestCase):
    """_execute_tool dispatches correctly and handles errors."""

    def setUp(self):
        self.orch = _make_orchestrator()

    def test_unknown_tool_returns_error(self):
        result = self.orch._execute_tool("nonexistent_tool", {})
        self.assertIn("error", result)
        self.assertIn("Unknown tool", result["error"])

    def test_submit_result_sets_final_result(self):
        result = self.orch._execute_tool(
            "submit_result",
            {"result": "Passed", "confidence": 0.9, "explanation": "Looks polite."},
        )
        self.assertEqual(result, {"status": "submitted"})
        self.assertTrue(self.orch._investigation_complete)
        self.assertEqual(self.orch._final_result["result"], "Passed")
        self.assertAlmostEqual(self.orch._final_result["confidence"], 0.9)

    def test_submit_result_marks_investigation_complete(self):
        self.orch._execute_tool(
            "submit_result",
            {"result": "Failed", "confidence": 0.6, "explanation": "Rude tone."},
        )
        self.assertTrue(self.orch._investigation_complete)

    def test_tool_exception_returns_error_dict(self):
        def _bad_handler(**kwargs):
            raise ValueError("DB is down")

        self.orch._execute_tool.__func__  # ensure it's a bound method
        original = self.orch._tool_inspect_data
        self.orch._tool_inspect_data = lambda **kw: (_ for _ in ()).throw(ValueError("DB is down"))
        result = self.orch._execute_tool(
            "inspect_data",
            {"target_type": "span", "target_id": "x", "depth": "overview"},
        )
        self.assertIn("error", result)
        self.orch._tool_inspect_data = original  # restore


class TestOrchestratorMaxEvalGuard(unittest.TestCase):
    """_tool_run_eval respects MAX_EVAL_CALLS."""

    def setUp(self):
        self.orch = _make_orchestrator()
        self.orch._eval_calls = EvalOrchestrator.MAX_EVAL_CALLS  # already at limit

    def test_run_eval_blocked_at_limit(self):
        result = self.orch._tool_run_eval(
            criteria="Is it polite?",
            data="Hello there!",
            choices=["Passed", "Failed"],
            model="small",
        )
        self.assertIn("error", result)
        self.assertIn("Maximum eval calls", result["error"])


class TestOrchestratorParseToolCall(unittest.TestCase):
    """_parse_tool_call_from_response extracts correctly."""

    def test_valid_tool_call(self):
        content = 'I will now inspect the data.\n<tool_call>\n{"tool": "inspect_data", "params": {"target_type": "span", "target_id": "abc", "depth": "detail"}}\n</tool_call>'
        result = EvalOrchestrator._parse_tool_call_from_response(content)
        self.assertIsNotNone(result)
        self.assertEqual(result["tool"], "inspect_data")
        self.assertEqual(result["params"]["depth"], "detail")

    def test_no_tool_call_returns_none(self):
        content = "Based on the analysis, the text is clearly toxic. No further investigation needed."
        result = EvalOrchestrator._parse_tool_call_from_response(content)
        self.assertIsNone(result)

    def test_malformed_json_in_tool_call_returns_none(self):
        content = '<tool_call>\n{bad json here\n</tool_call>'
        result = EvalOrchestrator._parse_tool_call_from_response(content)
        self.assertIsNone(result)

    def test_nested_json_params(self):
        payload = {"tool": "run_eval", "params": {"criteria": "Is it OK?", "data": "Hello", "choices": ["Passed", "Failed"], "model": "flash"}}
        content = f'<tool_call>\n{json.dumps(payload)}\n</tool_call>'
        result = EvalOrchestrator._parse_tool_call_from_response(content)
        self.assertEqual(result["params"]["choices"], ["Passed", "Failed"])

    def test_extra_whitespace_around_json(self):
        payload = {"tool": "submit_result", "params": {"result": "Passed", "confidence": 0.95, "explanation": "Good"}}
        content = f'<tool_call>   \n\n{json.dumps(payload)}\n\n   </tool_call>'
        result = EvalOrchestrator._parse_tool_call_from_response(content)
        self.assertIsNotNone(result)
        self.assertEqual(result["tool"], "submit_result")


class TestOrchestratorJSONParsing(unittest.TestCase):
    """_parse_json_response handles all formats."""

    def setUp(self):
        self.orch = _make_orchestrator()

    def test_bare_json(self):
        r = self.orch._parse_json_response('{"result": "Passed", "confidence": 0.9, "explanation": "ok"}')
        self.assertEqual(r["result"], "Passed")

    def test_markdown_fenced(self):
        r = self.orch._parse_json_response('```json\n{"result": "Failed", "confidence": 0.8, "explanation": "bad"}\n```')
        self.assertEqual(r["result"], "Failed")

    def test_json_embedded_in_prose(self):
        r = self.orch._parse_json_response('After analysis: {"result": "Passed", "confidence": 0.7, "explanation": "fine"} That is my verdict.')
        self.assertEqual(r["result"], "Passed")

    def test_empty_string_returns_empty_dict(self):
        r = self.orch._parse_json_response("")
        self.assertEqual(r, {})

    def test_garbage_returns_empty_dict(self):
        r = self.orch._parse_json_response("This is definitely not JSON!")
        self.assertEqual(r, {})


class TestOrchestratorEvalResponseParsing(unittest.TestCase):
    """_parse_eval_response validates result against choices."""

    def setUp(self):
        self.orch = _make_orchestrator()

    def test_valid_result_passes_through(self):
        r = self.orch._parse_eval_response(
            '{"result": "Passed", "confidence": 0.95, "explanation": "polite"}',
            ["Passed", "Failed"],
        )
        self.assertEqual(r["result"], "Passed")
        self.assertAlmostEqual(r["confidence"], 0.95)

    def test_case_insensitive_match(self):
        r = self.orch._parse_eval_response(
            '{"result": "passed", "confidence": 0.8, "explanation": "ok"}',
            ["Passed", "Failed"],
        )
        self.assertEqual(r["result"], "Passed")  # corrected to canonical case

    def test_unknown_result_falls_back_to_first_choice(self):
        r = self.orch._parse_eval_response(
            '{"result": "Maybe", "confidence": 0.7, "explanation": "unsure"}',
            ["Passed", "Failed"],
        )
        self.assertEqual(r["result"], "Passed")
        self.assertLessEqual(r["confidence"], 0.5)

    def test_confidence_clamped_above_1(self):
        r = self.orch._parse_eval_response(
            '{"result": "Passed", "confidence": 1.5, "explanation": "great"}',
            ["Passed", "Failed"],
        )
        self.assertLessEqual(r["confidence"], 1.0)

    def test_confidence_clamped_below_0(self):
        r = self.orch._parse_eval_response(
            '{"result": "Failed", "confidence": -0.2, "explanation": "bad"}',
            ["Passed", "Failed"],
        )
        self.assertGreaterEqual(r["confidence"], 0.0)

    def test_missing_explanation_uses_raw_response(self):
        r = self.orch._parse_eval_response(
            '{"result": "Passed", "confidence": 0.9}',
            ["Passed", "Failed"],
        )
        self.assertIn("explanation", r)


class TestOrchestratorContextCompaction(unittest.TestCase):
    """_compact_old_tool_results trims old messages."""

    def setUp(self):
        self.orch = _make_orchestrator()

    def _make_messages(self, n_tool_results: int) -> list:
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "start"}]
        for i in range(n_tool_results):
            msgs.append({"role": "assistant", "content": f"<tool_call>{{\"tool\": \"inspect_data\", \"params\": {{}}}}</tool_call>"})
            msgs.append({"role": "user", "content": f"Tool result for inspect_data:\n" + ("x" * 500)})
        return msgs

    def test_no_compaction_when_under_limit(self):
        msgs = self._make_messages(EvalOrchestrator.COMPACT_KEEP_RECENT - 1)
        original_contents = [m["content"] for m in msgs]
        self.orch._compact_old_tool_results(msgs)
        for orig, msg in zip(original_contents, msgs):
            self.assertEqual(orig, msg["content"])

    def test_compacts_old_messages_at_limit(self):
        keep = EvalOrchestrator.COMPACT_KEEP_RECENT
        msgs = self._make_messages(keep + 2)
        self.orch._compact_old_tool_results(msgs)
        # At least the first tool-result user message should be compacted
        tool_result_msgs = [m for m in msgs if m.get("role") == "user" and "Tool result for" in m.get("content", "")]
        compacted = [m for m in tool_result_msgs if "_compacted" in m.get("content", "")]
        self.assertGreater(len(compacted), 0)

    def test_compacted_messages_contain_summary(self):
        keep = EvalOrchestrator.COMPACT_KEEP_RECENT
        msgs = self._make_messages(keep + 3)
        self.orch._compact_old_tool_results(msgs)
        for m in msgs:
            if m.get("role") == "user":
                try:
                    parsed = json.loads(m["content"])
                    if parsed.get("_compacted"):
                        self.assertIn("summary", parsed)
                        break
                except (json.JSONDecodeError, TypeError):
                    pass

    def test_already_compacted_not_recompacted(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "start"},
        ]
        for i in range(EvalOrchestrator.COMPACT_KEEP_RECENT + 2):
            msgs.append({"role": "assistant", "content": "..."})
            if i == 0:
                msgs.append({"role": "user", "content": json.dumps({"_compacted": True, "summary": "already done"})})
            else:
                msgs.append({"role": "user", "content": "Tool result for inspect_data:\n" + "y" * 500})
        self.orch._compact_old_tool_results(msgs)
        # The already-compacted message should still have _compacted=True
        for m in msgs:
            if m.get("role") == "user":
                try:
                    p = json.loads(m["content"])
                    if p.get("summary") == "already done":
                        self.assertTrue(p["_compacted"])
                except (json.JSONDecodeError, TypeError):
                    pass


class TestOrchestratorBuildToolList(unittest.TestCase):
    """_build_tool_list includes/excludes tools based on connected resources."""

    def test_base_tools_always_present(self):
        orch = _make_orchestrator()
        names = [t["function"]["name"] for t in orch.tools]
        for required in ["inspect_data", "search_data", "formalize_criteria", "run_eval", "submit_result"]:
            self.assertIn(required, names)

    def test_kb_tool_added_when_kb_id_set(self):
        config = _make_config(kb_id="kb-123")
        orch = _make_orchestrator(config=config)
        names = [t["function"]["name"] for t in orch.tools]
        self.assertIn("search_kb", names)

    def test_kb_tool_absent_without_kb_id(self):
        orch = _make_orchestrator()
        names = [t["function"]["name"] for t in orch.tools]
        self.assertNotIn("search_kb", names)

    def test_feedback_tool_added_when_feedback_count_positive(self):
        brief = _make_scout_brief(available_resources={"feedback_count": 5, "mcp_tools": []})
        orch = _make_orchestrator(brief=brief)
        names = [t["function"]["name"] for t in orch.tools]
        self.assertIn("get_feedback_examples", names)

    def test_feedback_tool_absent_without_feedback(self):
        brief = _make_scout_brief(available_resources={"feedback_count": 0, "mcp_tools": []})
        orch = _make_orchestrator(brief=brief)
        names = [t["function"]["name"] for t in orch.tools]
        self.assertNotIn("get_feedback_examples", names)

    def test_mcp_tool_added_when_mcp_tools_present(self):
        brief = _make_scout_brief(available_resources={"feedback_count": 0, "mcp_tools": [{"name": "Grammarly"}]})
        orch = _make_orchestrator(brief=brief)
        names = [t["function"]["name"] for t in orch.tools]
        self.assertIn("call_mcp_tool", names)


class TestOrchestratorToolsDescription(unittest.TestCase):
    """_build_tools_description serialises tool defs to readable text."""

    def test_tool_names_appear(self):
        orch = _make_orchestrator()
        desc = orch._build_tools_description(orch.tools)
        self.assertIn("inspect_data", desc)
        self.assertIn("run_eval", desc)
        self.assertIn("submit_result", desc)

    def test_required_params_marked(self):
        orch = _make_orchestrator()
        desc = orch._build_tools_description(orch.tools)
        self.assertIn("(required)", desc)

    def test_optional_params_marked(self):
        orch = _make_orchestrator()
        desc = orch._build_tools_description(orch.tools)
        self.assertIn("(optional)", desc)

    def test_enum_options_shown(self):
        orch = _make_orchestrator()
        desc = orch._build_tools_description(orch.tools)
        # run_eval model param has enum ["flash", "small", "large"]
        self.assertIn("flash", desc)


class TestOrchestratorBuildFinalResult(unittest.TestCase):
    """_build_final_result handles submitted and unsubmitted states."""

    def test_with_submitted_result(self):
        orch = _make_orchestrator()
        orch._final_result = {"result": "Passed", "confidence": 0.9, "explanation": "Great."}
        orch._model_used = "small"
        orch._eval_calls = 1
        orch._orchestrator_turns = 3
        out = orch._build_final_result()
        self.assertEqual(out["result"]["result"], "Passed")
        self.assertEqual(out["model_used"], "small")
        self.assertEqual(out["eval_calls"], 1)
        self.assertEqual(out["orchestrator_turns"], 3)

    def test_without_submit_result_returns_graceful_failure(self):
        orch = _make_orchestrator()
        out = orch._build_final_result()
        self.assertIsNone(out["result"]["result"])
        self.assertEqual(out["result"]["confidence"], 0.0)
        self.assertIn("did not complete", out["result"]["explanation"])

    def test_token_usage_keys_present(self):
        orch = _make_orchestrator()
        out = orch._build_final_result()
        for key in ("scout", "formalization", "orchestrator", "eval_calls", "total"):
            self.assertIn(key, out["token_usage"])


# ---------------------------------------------------------------------------
# ── Integration tests (mocked LLM) ──────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestOrchestratorFullLoopMocked(unittest.TestCase):
    """
    Run the full orchestrator loop with a mocked TuringClient.
    The mock drives the model through a realistic sequence:
      Turn 1: inspect_data
      Turn 2: run_eval → result Passed, confidence 0.9
      Turn 3: submit_result
    """

    def _run_with_sequence(self, responses: list) -> dict:
        """Run orchestrator with pre-scripted model responses."""
        orch = _make_orchestrator()

        call_idx = [0]

        def fake_chat_completion(model, messages, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            if idx < len(responses):
                return responses[idx]
            return "No more responses."

        orch.turing_client = MagicMock()
        orch.turing_client.chat_completion.side_effect = fake_chat_completion
        orch.turing_client.token_usage = {"prompt_tokens": 100, "completion_tokens": 50}

        # Patch inspect_data so it doesn't hit the DB
        orch._tool_inspect_data = MagicMock(return_value={
            "span_id": "span-abc",
            "name": "test_span",
            "type": "llm",
            "status": "success",
            "input": "Hello!",
            "output": "Hello there! How can I help?",
        })

        return orch.run()

    def test_inspect_then_run_eval_then_submit(self):
        responses = [
            # Turn 1: inspect data
            '<tool_call>\n{"tool": "inspect_data", "params": {"target_type": "span", "target_id": "span-abc", "depth": "detail"}}\n</tool_call>',
            # Turn 2: run eval
            '<tool_call>\n{"tool": "run_eval", "params": {"criteria": "Is the response polite?", "data": "Hello there! How can I help?", "choices": ["Passed", "Failed"], "model": "small"}}\n</tool_call>',
            # Turn 3: submit (orchestrator calls run_eval internally which also calls turing)
            # After run_eval returns, orchestrator gets another LLM turn:
            '<tool_call>\n{"tool": "submit_result", "params": {"result": "Passed", "confidence": 0.95, "explanation": "Response is warm and helpful."}}\n</tool_call>',
        ]
        # Patch run_eval to return a canned result (avoids second turing call)
        orch = _make_orchestrator()
        call_idx = [0]

        def fake_chat_completion(model, messages, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            resp = responses[idx] if idx < len(responses) else "done"
            return resp

        orch.turing_client = MagicMock()
        orch.turing_client.chat_completion.side_effect = fake_chat_completion
        orch.turing_client.token_usage = {"prompt_tokens": 200, "completion_tokens": 100}

        orch._tool_inspect_data = MagicMock(return_value={"output": "Hello there!"})
        orch._tool_run_eval = MagicMock(return_value={
            "result": "Passed", "confidence": 0.95,
            "explanation": "Polite and helpful.", "model_used": "turing_small",
        })

        result = orch.run()
        self.assertEqual(result["result"]["result"], "Passed")
        self.assertAlmostEqual(result["result"]["confidence"], 0.95)
        self.assertTrue(result["orchestrator_turns"] >= 3)

    def test_no_tool_call_terminates_loop(self):
        """If the model returns no <tool_call>, loop should terminate gracefully."""
        orch = _make_orchestrator()
        orch.turing_client = MagicMock()
        orch.turing_client.chat_completion.return_value = "I have completed my analysis. The response is polite."
        orch.turing_client.token_usage = {"prompt_tokens": 50, "completion_tokens": 20}

        result = orch.run()
        # Should terminate after 1 turn with no submitted result
        self.assertEqual(result["orchestrator_turns"], 1)
        self.assertIsNone(result["result"]["result"])

    def test_max_turns_reached_returns_graceful_failure(self):
        """Exceeding MAX_TURNS should not raise — returns graceful failure."""
        orch = _make_orchestrator()
        orch.MAX_TURNS = 3  # override for speed

        call_count = [0]

        def fake_keep_calling(model, messages, **kwargs):
            call_count[0] += 1
            return '<tool_call>\n{"tool": "inspect_data", "params": {"target_type": "span", "target_id": "x", "depth": "overview"}}\n</tool_call>'

        orch.turing_client = MagicMock()
        orch.turing_client.chat_completion.side_effect = fake_keep_calling
        orch.turing_client.token_usage = {"prompt_tokens": 10, "completion_tokens": 5}
        orch._tool_inspect_data = MagicMock(return_value={"span_id": "x", "status": "success"})

        result = orch.run()
        self.assertEqual(result["orchestrator_turns"], 3)
        self.assertIsNone(result["result"]["result"])

    def test_llm_exception_terminates_gracefully(self):
        """An exception from the LLM call should not propagate — graceful failure."""
        orch = _make_orchestrator()
        orch.turing_client = MagicMock()
        orch.turing_client.chat_completion.side_effect = RuntimeError("Network error")
        orch.turing_client.token_usage = {"prompt_tokens": 0, "completion_tokens": 0}

        result = orch.run()
        self.assertEqual(result["orchestrator_turns"], 1)
        self.assertIsNone(result["result"]["result"])

    def test_formalize_then_run_eval(self):
        """criteria_needs_formalization=True path: model calls formalize_criteria first."""
        brief = _make_scout_brief(criteria_needs_formalization=True)
        orch = _make_orchestrator(brief=brief)

        responses = [
            '<tool_call>\n{"tool": "formalize_criteria", "params": {"raw_criteria": "Is it good?", "data_summary": "a span", "choices": ["Passed", "Failed"], "response_format": "boolean"}}\n</tool_call>',
            '<tool_call>\n{"tool": "run_eval", "params": {"criteria": "Detailed criteria here", "data": "Hello!", "choices": ["Passed", "Failed"], "model": "small"}}\n</tool_call>',
            '<tool_call>\n{"tool": "submit_result", "params": {"result": "Passed", "confidence": 0.88, "explanation": "Good."}}\n</tool_call>',
        ]

        call_idx = [0]
        def fake_chat(model, messages, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return responses[idx] if idx < len(responses) else "done"

        orch.turing_client = MagicMock()
        orch.turing_client.chat_completion.side_effect = fake_chat
        orch.turing_client.token_usage = {"prompt_tokens": 100, "completion_tokens": 50}

        orch._tool_formalize_criteria = MagicMock(return_value={
            "formalized_criteria": "Evaluate whether the response is helpful and polite.",
            "choices": ["Passed", "Failed"],
            "response_format": "boolean",
            "reasoning": "Expanded.",
        })
        orch._tool_run_eval = MagicMock(return_value={
            "result": "Passed", "confidence": 0.88,
            "explanation": "Good response.", "model_used": "turing_small",
        })

        result = orch.run()
        orch._tool_formalize_criteria.assert_called_once()
        orch._tool_run_eval.assert_called_once()
        self.assertEqual(result["result"]["result"], "Passed")


class TestOrchestratorBuildEvalPrompt(unittest.TestCase):
    """_build_eval_prompt includes all sections."""

    def setUp(self):
        self.orch = _make_orchestrator()

    def test_prompt_includes_criteria_and_data(self):
        p = self.orch._build_eval_prompt("Be polite.", "Hello!", ["Passed", "Failed"])
        self.assertIn("Be polite.", p)
        self.assertIn("Hello!", p)
        self.assertIn("Passed", p)
        self.assertIn("Failed", p)

    def test_prompt_includes_supplementary_context(self):
        p = self.orch._build_eval_prompt("criteria", "data", ["Yes", "No"],
                                          supplementary_context="Policy doc section 3.")
        self.assertIn("Policy doc section 3.", p)

    def test_prompt_includes_fewshots(self):
        fewshots = [{"input": "Hi", "label": "Passed", "feedback": "Polite"}]
        p = self.orch._build_eval_prompt("criteria", "data", ["Passed", "Failed"],
                                          fewshots=fewshots)
        self.assertIn("Example 1", p)
        self.assertIn("Polite", p)

    def test_prompt_omits_empty_sections(self):
        p = self.orch._build_eval_prompt("criteria", "data", ["Passed", "Failed"])
        self.assertNotIn("Additional Context", p)
        self.assertNotIn("Reference Examples", p)


# ---------------------------------------------------------------------------
# ── Edge case / regression tests ────────────────────────────────────────────
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    def test_submit_result_confidence_as_string(self):
        """Confidence passed as string should be coerced to float."""
        orch = _make_orchestrator()
        orch._execute_tool(
            "submit_result",
            {"result": "Passed", "confidence": "0.85", "explanation": "ok"},
        )
        self.assertIsInstance(orch._final_result["confidence"], float)
        self.assertAlmostEqual(orch._final_result["confidence"], 0.85)

    def test_tool_call_with_empty_params(self):
        """Tool call with missing required params should return error, not raise."""
        orch = _make_orchestrator()
        result = orch._execute_tool("inspect_data", {})  # missing required args
        self.assertIn("error", result)

    def test_orchestrator_token_usage_accumulates(self):
        """Token usage should accumulate across multiple turing calls."""
        orch = _make_orchestrator()
        responses = [
            '<tool_call>\n{"tool": "submit_result", "params": {"result": "Passed", "confidence": 0.9, "explanation": "ok"}}\n</tool_call>',
        ]
        call_idx = [0]
        def fake_chat(model, messages, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return responses[idx] if idx < len(responses) else "done"

        orch.turing_client = MagicMock()
        orch.turing_client.chat_completion.side_effect = fake_chat
        orch.turing_client.token_usage = {"prompt_tokens": 300, "completion_tokens": 150}

        orch.run()
        self.assertGreater(orch._orchestrator_token_usage["prompt_tokens"], 0)

    def test_resolve_model_config_unknown_falls_back_to_small(self):
        orch = _make_orchestrator()
        cfg = orch._resolve_model_config("unknown_tier")
        from agentic_eval.core.utils.model_config import ModelConfigs
        self.assertEqual(cfg, ModelConfigs.TURING_SMALL)

    def test_choices_with_single_option(self):
        """Single-choice evals shouldn't crash the eval response parser."""
        orch = _make_orchestrator()
        r = orch._parse_eval_response(
            '{"result": "Yes", "confidence": 0.99, "explanation": "clearly"}',
            ["Yes"],
        )
        self.assertEqual(r["result"], "Yes")

    def test_empty_choices_list(self):
        """Empty choices should not crash — falls back gracefully."""
        orch = _make_orchestrator()
        r = orch._parse_eval_response(
            '{"result": "Maybe", "confidence": 0.5, "explanation": "unsure"}',
            [],
        )
        self.assertIn("result", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
