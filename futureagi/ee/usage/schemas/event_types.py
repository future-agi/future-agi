"""Billing event type enum — the canonical set of event types for usage metering.

Derived from billing.yaml `api_call_types` keys. Every emit() call site
should use a member of this enum instead of raw strings.

The old APICallTypeChoices (usage.models.usage) is preserved for the legacy
APICallType / Pricing models. New metering code uses BillingEventType exclusively.
"""

from __future__ import annotations

from enum import Enum


class BillingEventType(str, Enum):
    """Event types for the usage metering pipeline.

    Each member maps to an `api_call_types` key in billing.yaml,
    which in turn maps to a billing dimension.
    """

    # ── AI Credits ──
    TURING_LARGE_EVALUATOR = "turing_large_evaluator"
    TURING_SMALL_EVALUATOR = "turing_small_evaluator"
    TURING_FLASH_EVALUATOR = "turing_flash_evaluator"
    PROTECT_EVALUATOR = "protect_evaluator"
    PROTECT_FLASH_EVALUATOR = "protect_flash_evaluator"
    CODE_EVALUATOR = "code_evaluator"
    SYNTHETIC_DATA_GENERATION = "synthetic_data_generation"
    ERROR_LOCALIZER = "error_localizer"
    TRACE_ERROR_ANALYSIS = "trace_error_analysis"
    AUTO_ANNOTATION = "auto_annotation"
    AI_PROMPT_CREATION = "ai_prompt_creation"
    AI_PROMPT_IMPROVEMENT = "ai_prompt_improvement"
    EVAL_EXPLANATION = "eval_explanation"
    FALCON_AI_CHAT = "falcon_ai_chat"

    # ── Storage ──
    OBSERVE_ADD = "observe_add"
    VOICE_RECORDING_STORAGE = "voice_recording_storage"
    KB_STORAGE = "kb_storage"

    # ── Gateway ──
    GATEWAY_REQUEST = "gateway_request"
    GATEWAY_CACHE_HIT = "gateway_cache_hit"

    # ── Simulation ──
    VOICE_CALL = "voice_call"
    TEXT_CALL = "text_call"

    # ── Tracing ──
    TRACING_EVENT = "tracing_event"
