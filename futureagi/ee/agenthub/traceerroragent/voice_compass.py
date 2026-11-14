"""
Voice Agent Compass — Preplanner + Evaluator + Scorer pipeline for
analysing voice/audio calls stored as one-trace-one-span conversations.

Architecture
============
1. **Preplanner** (Gemini 3 Pro, 1 call) — reads audio + transcript,
   selects 2-7 error categories that need deeper evaluation.
2. **Evaluator** (Gemini 3 Flash, N calls) — one call per selected
   category, finds concrete errors with evidence.
3. **Scorer** (Gemini 3 Flash, 1 call) — scores the call on 4 quality
   dimensions using the aggregated findings.

Output matches the same schema as text Agent Compass so
``TraceErrorAnalysisService.save_analysis_result()`` works unchanged.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import structlog

from ee.agenthub.traceerroragent.analysis_service import (
    TraceErrorAnalysisService,
)
from agentic_eval.core_evals.fi_utils.json import JsonHelper
from ee.agenthub.traceerroragent.voice_prompts import (
    VOICE_ERROR_TAXONOMY,
    VOICE_EVALUATOR_SYSTEM_PROMPT,
    VOICE_EVALUATOR_USER_PROMPT_TEMPLATE,
    VOICE_PREPLANNER_SYSTEM_PROMPT,
    VOICE_PREPLANNER_USER_PROMPT_TEMPLATE,
    VOICE_SCORER_SYSTEM_PROMPT,
    VOICE_SCORER_USER_PROMPT_TEMPLATE,
    _format_taxonomy_overview,
    format_voice_category_taxonomy,
)
from ee.agenthub.traceerroragent.voice_summary_builder import (
    VoiceCallData,
    VoiceSummaryBuilder,
)
from agentic_eval.core.llm.audio_utils import download_audio_url_to_base64
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from tracer.models.observation_span import ObservationSpan
from tracer.queries.error_analysis import TraceErrorAnalysisDB

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SCORE_DIMENSIONS = [
    "factual_grounding",
    "privacy_and_safety",
    "instruction_adherence",
    "optimal_plan_execution",
]


# ============================================================================
# Preplanner
# ============================================================================


class VoicePreplanner:
    """
    Reads the full voice call (audio + transcript + metrics) and selects
    which error categories need deeper evaluation.

    Uses Gemini 3 Pro for native audio understanding.
    """

    def __init__(self) -> None:
        cfg = ModelConfigs.VERTEX_GEMINI_3_PRO
        self.llm = LLM(
            provider=cfg.provider,
            model_name=cfg.model_name,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )

    def run(self, voice_data: VoiceCallData) -> dict[str, Any]:
        """
        Analyze the voice call and select relevant categories.

        Returns dict with keys:
            call_summary, key_observations, selected_categories,
            per_category_hints
        """
        transcript_text = VoiceSummaryBuilder.format_transcript_for_prompt(voice_data)
        metrics_text = VoiceSummaryBuilder.format_metrics_for_prompt(voice_data)
        metadata_text = VoiceSummaryBuilder.format_call_metadata_for_prompt(voice_data)
        taxonomy_overview = _format_taxonomy_overview()

        user_text = VOICE_PREPLANNER_USER_PROMPT_TEMPLATE.format(
            transcript=transcript_text,
            metrics_summary=metrics_text,
            call_metadata=metadata_text,
            taxonomy_overview=taxonomy_overview,
        )

        user_content = _build_audio_content(user_text, voice_data)

        messages = [
            {"role": "system", "content": VOICE_PREPLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        response = self.llm._get_completion_content(messages=messages)
        result = _safe_extract_json(response)

        # Defaults
        if not result.get("selected_categories"):
            logger.warning("Preplanner returned no selected_categories, using defaults")
            result["selected_categories"] = ["Response Quality", "Task Completion"]
        if not result.get("call_summary"):
            result["call_summary"] = "Call summary not available."
        if not result.get("key_observations"):
            result["key_observations"] = []
        if not result.get("per_category_hints"):
            result["per_category_hints"] = {
                cat: "" for cat in result["selected_categories"]
            }

        return result


# ============================================================================
# Evaluator (per-category)
# ============================================================================


class VoiceCategoryEvaluator:
    """
    Evaluates a single error category for a voice call using Gemini 3 Flash.
    """

    def __init__(self) -> None:
        cfg = ModelConfigs.VERTEX_GEMINI_3_FLASH
        self.llm = LLM(
            provider=cfg.provider,
            model_name=cfg.model_name,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )
        self._error_counter = 0

    def run(
        self,
        category_name: str,
        voice_data: VoiceCallData,
        call_summary: str,
        category_hint: str,
    ) -> list[dict[str, Any]]:
        """
        Evaluate a single category and return list of error dicts.

        Each error matches the TraceErrorDetail schema used by
        ``save_analysis_result()``.
        """
        category_rubric = format_voice_category_taxonomy(category_name)
        transcript_text = VoiceSummaryBuilder.format_transcript_for_prompt(voice_data)
        metrics_text = VoiceSummaryBuilder.format_metrics_for_prompt(voice_data)

        user_text = VOICE_EVALUATOR_USER_PROMPT_TEMPLATE.format(
            category_name=category_name,
            category_rubric=category_rubric,
            category_hint=category_hint,
            call_summary=call_summary,
            transcript=transcript_text,
            metrics=metrics_text,
        )

        messages = [
            {"role": "system", "content": VOICE_EVALUATOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ]

        response = self.llm._get_completion_content(messages=messages)
        raw = _safe_extract_json(response)

        errors_detected = raw.get("errors_detected", [])
        if not isinstance(errors_detected, list):
            return []
        
        span_id = voice_data.span_id

        # Normalize to DB schema
        normalized: list[dict[str, Any]] = []
        for err in errors_detected:
            confidence = err.get("confidence", 0)
            if isinstance(confidence, str):
                try:
                    confidence = float(confidence)
                except ValueError:
                    confidence = 0.5
            if confidence < 0.7:
                continue

            self._error_counter += 1
            error_id = f"E{self._error_counter:03d}"
            cluster_id = f"C{self._error_counter:03d}"

            normalized.append(
                {
                    "error_id": error_id,
                    "cluster_id": cluster_id,
                    "category": err.get("category", category_name),
                    "impact": err.get("impact", "MEDIUM"),
                    "urgency_to_fix": err.get("urgency_to_fix", "MEDIUM"),
                    "location_spans": [span_id],  # Voice has a single conversation span
                    "evidence_snippets": err.get("evidence_snippets", []),
                    "description": err.get("description", ""),
                    "root_causes": err.get("root_causes", []),
                    "recommendation": err.get("recommendation", ""),
                    "immediate_fix": err.get("immediate_fix", ""),
                    "trace_impact": err.get("description", ""),
                    "trace_assessment": err.get("confidence_basis", ""),
                    "llm_analysis": {
                        "confidence": confidence,
                        "confidence_basis": err.get("confidence_basis", ""),
                    },
                    "memory_enhanced": False,
                }
            )

        return normalized


# ============================================================================
# Orchestrator (VoiceCompassAgent)
# ============================================================================


class VoiceCompassAgent:
    """
    End-to-end orchestrator: Preplanner → Evaluator(s) → Scorer → DB.
    """

    def __init__(self, trace_id: str, save_to_db: bool = True) -> None:
        self.trace_id = trace_id
        self.save_to_db = save_to_db
        self.db = TraceErrorAnalysisDB()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def summarize(self) -> dict[str, Any]:
        """Run the full voice compass pipeline and return the analysis result."""
        logger.info("Voice compass starting", trace_id=self.trace_id)

        # 1. Find the conversation span
        span = self._find_conversation_span()
        if span is None:
            logger.warning(
                "No conversation span found for trace", trace_id=self.trace_id
            )
            return self._empty_result()

        # 2. Extract structured data
        voice_data = VoiceSummaryBuilder.extract(span)
        logger.info(
            "Extracted voice data",
            trace_id=self.trace_id,
            transcript_turns=len(voice_data.transcript),
            has_audio=voice_data.has_audio,
            has_tool_calls=voice_data.has_tool_calls,
        )

        # 3. Preplanner
        preplanner = VoicePreplanner()
        plan = preplanner.run(voice_data)
        selected_categories = plan["selected_categories"]
        call_summary = plan["call_summary"]
        logger.info(
            "Preplanner complete",
            trace_id=self.trace_id,
            selected_categories=selected_categories,
            preplanner_tokens=preplanner.llm.token_usage,
        )

        # 4. Evaluator (one call per category)
        evaluator = VoiceCategoryEvaluator()
        all_errors: list[dict[str, Any]] = []
        for cat_name in selected_categories:
            if cat_name not in VOICE_ERROR_TAXONOMY:
                logger.warning("Unknown category from preplanner, skipping", category=cat_name)
                continue
            hint = plan.get("per_category_hints", {}).get(cat_name, "")
            errors = evaluator.run(cat_name, voice_data, call_summary, hint)
            all_errors.extend(errors)
            logger.info(
                "Evaluator complete for category",
                trace_id=self.trace_id,
                category=cat_name,
                errors_found=len(errors),
            )

        # 5. Scorer
        scores, scorer_llm = self._run_scorer(all_errors, call_summary, voice_data)

        # 6. Assemble result
        result = self._assemble_result(plan, all_errors, scores)

        # 7. Aggregate token usage
        total_tokens = {
            "preplanner": dict(preplanner.llm.token_usage),
            "evaluator": dict(evaluator.llm.token_usage),
            "scorer": dict(scorer_llm.token_usage),
        }
        result["token_usage"] = total_tokens
        logger.info(
            "Voice compass complete",
            trace_id=self.trace_id,
            total_errors=len(all_errors),
            overall_score=scores.get("overall_score"),
            token_usage=total_tokens,
        )

        # 8. Save to DB
        if self.save_to_db:
            try:
                project = span.project
                service = TraceErrorAnalysisService(project_id=str(project.id))
                service.save_analysis_result(self.trace_id, result)
                logger.info("Analysis saved to DB", trace_id=self.trace_id)
            except Exception as e:
                logger.error(
                    "Failed to save analysis to DB",
                    trace_id=self.trace_id,
                    error=str(e),
                )

        return result

    @staticmethod
    def is_voice_trace(trace_id: str) -> bool:
        """
        Check if a trace is a voice call (one span with
        observation_type='conversation').
        """
        return ObservationSpan.objects.filter(
            trace_id=trace_id,
            observation_type="conversation",
        ).exists()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_conversation_span(self) -> Optional[ObservationSpan]:
        """Find the conversation span for the trace."""
        return (
            ObservationSpan.objects.filter(
                trace_id=self.trace_id,
                observation_type="conversation",
            )
            .select_related("project")
            .first()
        )

    def _run_scorer(
        self,
        errors: list[dict[str, Any]],
        call_summary: str,
        voice_data: VoiceCallData,
    ) -> tuple[dict[str, Any], LLM]:
        """Score the call on 4 dimensions using Gemini 3 Flash."""
        cfg = ModelConfigs.VERTEX_GEMINI_3_FLASH
        llm = LLM(
            provider=cfg.provider,
            model_name=cfg.model_name,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )

        import json

        errors_json = json.dumps(errors, indent=2, default=str)
        metrics_summary = VoiceSummaryBuilder.format_metrics_for_prompt(voice_data)

        user_text = VOICE_SCORER_USER_PROMPT_TEMPLATE.format(
            call_summary=call_summary,
            error_count=len(errors),
            errors_json=errors_json,
            metrics_summary=metrics_summary,
        )

        messages = [
            {"role": "system", "content": VOICE_SCORER_SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ]

        response = llm._get_completion_content(messages=messages)
        raw = _safe_extract_json(response)

        # Normalize scores — model sometimes returns bare ints
        scores: dict[str, Any] = {}
        for dim in _SCORE_DIMENSIONS:
            val = raw.get(dim, {})
            if isinstance(val, dict):
                score = val.get("score", 3)
                reason = val.get("reason", "")
            elif isinstance(val, (int, float)):
                score = val
                reason = ""
            else:
                score = 3
                reason = ""
            # Clamp to 1-5
            try:
                score = max(1, min(5, int(score)))
            except (ValueError, TypeError):
                score = 3
            scores[dim] = {"score": score, "reason": reason}

        scores["overall_score"] = raw.get("overall_score", 3.0)
        try:
            scores["overall_score"] = round(float(scores["overall_score"]), 2)
        except (ValueError, TypeError):
            scores["overall_score"] = 3.0

        scores["error_count"] = raw.get("error_count", len(errors))
        scores["insights"] = raw.get("insights", "")
        scores["recommended_priority"] = raw.get("recommended_priority", "MEDIUM")

        return scores, llm

    def _assemble_result(
        self,
        plan: dict[str, Any],
        errors: list[dict[str, Any]],
        scores: dict[str, Any],
    ) -> dict[str, Any]:
        """Assemble the final result dict for ``save_analysis_result()``."""
        return {
            "summary": {
                "overall_score": scores.get("overall_score", 3.0),
                "error_count": len(errors),
                "insights": scores.get("insights", ""),
                "recommended_priority": scores.get("recommended_priority", "MEDIUM"),
                "call_summary": plan.get("call_summary", ""),
                "key_observations": plan.get("key_observations", []),
            },
            "scores": {
                dim: scores.get(dim, {"score": 3, "reason": ""})
                for dim in _SCORE_DIMENSIONS
            },
            "errors": errors,
            "grouped_errors": _group_errors(errors),
            "memory_context": {},
        }

    @staticmethod
    def _empty_result() -> dict[str, Any]:
        """Return a minimal result when no conversation span is found."""
        return {
            "summary": {
                "overall_score": None,
                "error_count": 0,
                "insights": "No conversation span found for this trace.",
                "recommended_priority": "LOW",
            },
            "scores": {
                dim: {"score": None, "reason": "No data available"}
                for dim in _SCORE_DIMENSIONS
            },
            "errors": [],
            "grouped_errors": [],
            "memory_context": {},
        }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _safe_extract_json(response: str) -> dict:
    """Extract JSON from LLM response, returning {} on failure."""
    try:
        return JsonHelper.extract_json_from_text(response) or {}
    except Exception as e:
        logger.warning("Failed to extract JSON from response", error=str(e))
        return {}


def _build_audio_content(
    user_text: str, voice_data: VoiceCallData
) -> str | list[dict[str, Any]]:
    """
    Build user message content. Returns multimodal list if audio is available,
    otherwise plain text.
    """
    audio_url = VoiceSummaryBuilder.get_best_audio_url(voice_data)
    if not audio_url:
        return user_text

    try:
        base64_data, audio_format = download_audio_url_to_base64(audio_url)
        data_uri = f"data:audio/{audio_format};base64,{base64_data}"
        return [
            {"type": "text", "text": user_text},
            {
                "type": "audio_content",
                "audio_content": {"url": data_uri, "format": audio_format},
            },
        ]
    except Exception as e:
        logger.warning(
            "Failed to download audio, continuing with transcript only",
            audio_url=audio_url[:100],
            error=str(e),
        )
        return user_text


def _group_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group errors by top-level category for the AnalysisErrorGroup table."""
    from collections import defaultdict

    buckets: dict[str, list[dict]] = defaultdict(list)
    for err in errors:
        # Use the first segment of the 3-level path as group key
        cat_path = err.get("category", "Unknown")
        top_level = cat_path.split(" > ")[0] if " > " in cat_path else cat_path
        buckets[top_level].append(err)

    groups = []
    for group_name, group_errors in buckets.items():
        impacts = [e.get("impact", "MEDIUM") for e in group_errors]
        combined_impact = "HIGH" if "HIGH" in impacts else ("MEDIUM" if "MEDIUM" in impacts else "LOW")
        groups.append(
            {
                "cluster_id": f"G-{uuid.uuid4().hex[:8]}",
                "error_type": group_name,
                "error_ids": [e["error_id"] for e in group_errors],
                "affected_spans": [],
                "combined_impact": combined_impact,
                "combined_description": f"{len(group_errors)} error(s) in {group_name}",
                "error_count": len(group_errors),
                "trace_impact": f"{group_name}: {len(group_errors)} errors found",
            }
        )
    return groups
