"""
EvalScout — cheap pre-pass that scans eval criteria + input data + available resources
and produces a structured brief for the EvalOrchestrator.

Inspired by ChauffeurAgent in traceerroragent/chauffeur.py:
  - Runs on a cheap/fast model (TURING_FLASH)
  - Single LLM call (no agentic loop)
  - Produces a structured JSON brief consumed by the Orchestrator
"""

import json
import re
from typing import Optional

import structlog

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from ee.turing.client import TuringClient
from ee.agenthub.eval_orchestrator.prompts import SCOUT_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

# Safe defaults returned when the Scout's response cannot be parsed.
_SAFE_DEFAULTS = {
    "complexity": "moderate",
    "recommended_model": "small",
    "criteria_needs_formalization": False,
    "data_plan": {
        "sufficient": True,
        "needs_drill_down": [],
        "relevant_areas": [],
    },
    "resource_plan": {
        "use_kb": False,
        "kb_query_hints": [],
        "use_feedback": False,
        "use_mcp_tools": [],
    },
    "reasoning": "Scout response parsing failed — conservative defaults applied.",
}


class EvalScout:
    """
    Cheap pre-pass that scans eval criteria + input data + available resources
    and produces a structured brief for the EvalOrchestrator.

    Mirrors ChauffeurAgent: cheap model, single completion call, structured output.
    """

    # Turing routing — mirrors DeterministicAgent
    TURING_MODEL_PREFIXES = ("turing_", "turing-")
    TURING_PROVIDER = ModelConfigs.TURING_FLASH.provider

    def __init__(self) -> None:
        config = ModelConfigs.TURING_FLASH  # same model as ChauffeurAgent
        self._model_name = config.model_name
        self._is_turing = (
            any(config.model_name.lower().startswith(p) for p in self.TURING_MODEL_PREFIXES)
            or config.provider == self.TURING_PROVIDER
        )

        if self._is_turing:
            self.turing_client: Optional[TuringClient] = TuringClient()
            self.llm: Optional[LLM] = None
        else:
            self.turing_client = None
            self.llm = LLM(
                provider=config.provider,
                model_name=config.model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )

    def run(
        self,
        criteria: str,
        input_summary: str,
        available_resources: dict,
    ) -> dict:
        """
        Analyse the eval task and produce a structured brief.

        Args:
            criteria:            The eval criteria/rule prompt (may be vague)
            input_summary:       Surface-level summary produced by build_input_summary()
            available_resources: {
                "knowledge_bases": [{"id": "...", "name": "...", "description": "..."}],
                "feedback_count":  12,
                "mcp_tools":       [{"name": "Grammarly", "description": "..."}],
            }

        Returns:
            {
                "complexity":                  "simple" | "moderate" | "complex",
                "recommended_model":           "flash" | "small" | "large",
                "criteria_needs_formalization": True/False,
                "data_plan": {
                    "sufficient":        True/False,
                    "needs_drill_down":  [ids],
                    "relevant_areas":    [descriptions],
                },
                "resource_plan": {
                    "use_kb":          True/False,
                    "kb_query_hints":  [queries],
                    "use_feedback":    True/False,
                    "use_mcp_tools":   [tool names],
                },
                "reasoning": "...",
            }
        """
        prompt = self._build_prompt(criteria, input_summary, available_resources)
        messages = [
            {"role": "system", "content": SCOUT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            if self._is_turing:
                response = self.turing_client.chat_completion(
                    model=self._model_name,
                    messages=messages,
                )
            else:
                response = self.llm._get_completion_content(
                    messages=messages,
                    model=self.llm.model_name,
                )
            brief = self._parse_response(response)
        except Exception as e:
            logger.error("EvalScout.run failed", error=str(e))
            brief = dict(_SAFE_DEFAULTS)

        # Enforce minimum model tier based on media types in the input
        brief["recommended_model"] = self._enforce_model_for_media(
            brief["recommended_model"], input_summary
        )
        return brief

    def get_token_usage(self) -> dict:
        if self._is_turing:
            return self.turing_client.token_usage
        return self.llm.token_usage if self.llm else {}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        criteria: str,
        input_summary: str,
        available_resources: dict,
    ) -> str:
        kb_list = available_resources.get("knowledge_bases", [])
        feedback_count = available_resources.get("feedback_count", 0)
        mcp_tools = available_resources.get("mcp_tools", [])

        parts = [
            "## EVAL CRITERIA",
            criteria.strip(),
            "",
            "## INPUT SUMMARY",
            input_summary.strip(),
            "",
            "## AVAILABLE RESOURCES",
        ]

        if kb_list:
            parts.append(f"Knowledge Bases ({len(kb_list)}):")
            for kb in kb_list:
                parts.append(f"  - {kb.get('name', kb.get('id', '?'))}: {kb.get('description', '')}")
        else:
            parts.append("Knowledge Bases: none connected")

        parts.append(f"Feedback examples: {feedback_count}")

        if mcp_tools:
            parts.append(f"MCP Tools ({len(mcp_tools)}):")
            for tool in mcp_tools:
                parts.append(f"  - {tool.get('name', '?')}: {tool.get('description', '')}")
        else:
            parts.append("MCP Tools: none connected")

        parts.append("")
        parts.append(
            "Produce the eval brief JSON as specified in your instructions. "
            "Respond with ONLY the JSON object, no markdown fences."
        )

        return "\n".join(parts)

    def _parse_response(self, response: str) -> dict:
        """
        Extract and validate the Scout's JSON brief from the LLM response.
        Falls back to safe defaults on any parse failure.
        """
        # 1. Strip markdown fences if present
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if fenced:
            candidate = fenced.group(1)
        else:
            # 2. Try the entire response as JSON
            candidate = response.strip()

        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            # 3. Find any JSON object in the response
            obj_match = re.search(r"\{.*\}", response, re.DOTALL)
            if obj_match:
                try:
                    parsed = json.loads(obj_match.group())
                except json.JSONDecodeError:
                    logger.warning("EvalScout: could not parse JSON from response")
                    return dict(_SAFE_DEFAULTS)
            else:
                logger.warning("EvalScout: no JSON object found in response")
                return dict(_SAFE_DEFAULTS)

        return self._validate_brief(parsed)

    def _validate_brief(self, parsed: dict) -> dict:
        """
        Ensure all required keys are present with sensible values.
        Missing keys are filled from _SAFE_DEFAULTS.
        """
        defaults = dict(_SAFE_DEFAULTS)

        result = {
            "complexity": parsed.get("complexity", defaults["complexity"]),
            "recommended_model": parsed.get("recommended_model", defaults["recommended_model"]),
            "criteria_needs_formalization": bool(
                parsed.get("criteria_needs_formalization", defaults["criteria_needs_formalization"])
            ),
            "data_plan": {
                "sufficient": bool(
                    parsed.get("data_plan", {}).get("sufficient", True)
                ),
                "needs_drill_down": parsed.get("data_plan", {}).get("needs_drill_down", []),
                "relevant_areas": parsed.get("data_plan", {}).get("relevant_areas", []),
            },
            "resource_plan": {
                "use_kb": bool(parsed.get("resource_plan", {}).get("use_kb", False)),
                "kb_query_hints": parsed.get("resource_plan", {}).get("kb_query_hints", []),
                "use_feedback": bool(parsed.get("resource_plan", {}).get("use_feedback", False)),
                "use_mcp_tools": parsed.get("resource_plan", {}).get("use_mcp_tools", []),
            },
            "reasoning": parsed.get("reasoning", defaults["reasoning"]),
        }

        # Coerce enums to valid values
        if result["complexity"] not in ("simple", "moderate", "complex"):
            result["complexity"] = "moderate"
        if result["recommended_model"] not in ("flash", "small", "large"):
            result["recommended_model"] = "small"

        return result

    # Model capability tiers:
    #   flash: text only
    #   small: text + image
    #   large: text + image + audio + pdf
    _MODEL_TIER = {"flash": 0, "small": 1, "large": 2}

    @classmethod
    def _enforce_model_for_media(cls, recommended: str, input_summary: str) -> str:
        """Upgrade model if the input contains media the recommended tier can't handle.

        Detection is based on [data_type] tags in the input summary produced by
        build_input_summary (e.g. "[image]", "[audio]", "[document]").
        """
        summary_lower = input_summary.lower()

        # Determine minimum required tier based on media types present
        required = "flash"  # text-only baseline
        if "[image]" in summary_lower:
            required = "small"
        if "[audio]" in summary_lower or "[document]" in summary_lower:
            required = "large"

        # Upgrade if recommended tier is too low
        if cls._MODEL_TIER.get(required, 0) > cls._MODEL_TIER.get(recommended, 1):
            logger.info(
                "Scout model upgraded for media support",
                recommended=recommended,
                upgraded_to=required,
                reason="input contains media that requires a higher-tier model",
            )
            return required
        return recommended
