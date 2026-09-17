"""
Chauffeur Agent — Explores traces and acts as an on-demand reader for the Judge.

Two roles:
1. Explorer (pre-pass): Reads the full trace, identifies logical sub-flows,
   summarizes what happened in each, and flags unusual observations.
2. Reader (on-demand): When the Judge encounters a large span, the Chauffeur
   reads it and returns a factual bullet-point summary.
"""

import json
import re

import structlog

from ee.agenthub.traceerroragent.prompts_v2 import (
    CHAUFFEUR_READER_PROMPT,
    CHAUFFEUR_SYSTEM_PROMPT,
)
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs

logger = structlog.get_logger(__name__)


class ChauffeurAgent:
    """Explores traces and acts as an on-demand reader for the Judge."""

    def __init__(self, trace_execution_summary: str):
        """
        Args:
            trace_execution_summary: Full trace execution text (outline + details)
                built by TraceErrorAnalysisAgent.create_trace_execution_summary().
        """
        self.trace_execution_summary = trace_execution_summary
        config = ModelConfigs.HAIKU_4_5_BEDROCK_ARN
        self.llm = LLM(
            provider=config.provider,
            model_name=config.model_name,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )

    def run(self) -> dict:
        """
        Run the Chauffeur to explore the trace and identify sub-flows.

        Returns:
            dict with "sub_flows" and "trace_overview" keys.
            Each sub-flow has "name", "spans", "summary", and "flags".
        """
        messages = [
            {"role": "system", "content": CHAUFFEUR_SYSTEM_PROMPT},
            {"role": "user", "content": self.trace_execution_summary},
        ]

        try:
            response = self.llm._get_completion_content(
                messages=messages,
                model=self.llm.model_name,
            )
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"Chauffeur agent failed: {str(e)}")
            return {"sub_flows": [], "trace_overview": ""}

    def summarize_content(self, span_id: str, content: str) -> str:
        """
        On-demand reader: summarize large span content into bullet points.

        Called by the Judge when a span's content exceeds the size threshold.
        Uses a separate LLM call (cheap Haiku) to produce a factual summary.

        Args:
            span_id: The span ID (for logging/context).
            content: The raw span content to summarize.

        Returns:
            Bullet-point summary string.
        """
        messages = [
            {"role": "system", "content": CHAUFFEUR_READER_PROMPT},
            {"role": "user", "content": f"Span ID: {span_id}\n\n{content}"},
        ]

        try:
            summary = self.llm._get_completion_content(
                messages=messages,
                model=self.llm.model_name,
            )
            return summary
        except Exception as e:
            logger.error(f"Chauffeur reader failed for span {span_id}: {str(e)}")
            # Fallback: return truncated content
            return content[:2000] + "\n... [Chauffeur summary failed, showing truncated content]"

    def _parse_response(self, response: str) -> dict:
        """Parse the Chauffeur's JSON response for sub-flows."""
        try:
            # Try to extract JSON from markdown code blocks
            if "```json" in response:
                match = re.search(r"```json\s*(.*?)\s*```", response, re.DOTALL)
                if match:
                    return self._validate_sub_flows(json.loads(match.group(1)))
            elif "```" in response:
                match = re.search(r"```\s*(.*?)\s*```", response, re.DOTALL)
                if match:
                    return self._validate_sub_flows(json.loads(match.group(1)))

            # Try direct JSON parse
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                return self._validate_sub_flows(parsed)

            logger.warning("Chauffeur response did not contain valid JSON")
            return {"sub_flows": [], "trace_overview": ""}

        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Failed to parse Chauffeur response: {str(e)}")
            return {"sub_flows": [], "trace_overview": ""}

    def _validate_sub_flows(self, parsed: dict) -> dict:
        """Validate and normalize the parsed response into sub_flows format."""
        # Handle both old format (span_groups) and new format (sub_flows)
        if "sub_flows" in parsed:
            return {
                "sub_flows": parsed["sub_flows"],
                "trace_overview": parsed.get("trace_overview", ""),
            }
        elif "span_groups" in parsed:
            # Backward compat: convert old format to new
            sub_flows = []
            for group in parsed["span_groups"]:
                sub_flows.append({
                    "name": group.get("signal", "unnamed group"),
                    "spans": group.get("spans", []),
                    "summary": group.get("signal", ""),
                    "flags": [],
                })
            return {"sub_flows": sub_flows, "trace_overview": ""}

        logger.warning("Chauffeur response missing sub_flows key")
        return {"sub_flows": [], "trace_overview": ""}

    def get_token_usage(self) -> dict:
        """Return token usage from the Chauffeur's LLM calls."""
        return self.llm.token_usage
