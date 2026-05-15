"""
Tracer Tasks - Backward Compatibility Module

This module re-exports all tasks from the new organized structure.
Import tasks directly from tracer.tasks submodules for new code.

Example:
    # Old way (still works for backward compatibility)
    from tracer.tasks import process_external_evals

    # New way (preferred)
    from tracer.tasks.external_eval import process_external_evals
    from tracer.tasks.error_analysis import check_and_process_trace_errors
"""

import base64

# Also keep some utility functions that weren't tasks
import re

import structlog
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import F
from django.utils import timezone

from accounts.models.user import User

# Activity-aware stub: these tasks run inside Temporal trace-analysis
# activities — invocations should fail non-retryably when ee is absent.
from tfc.ee_stub import _ee_activity_stub as _ee_stub

try:
    from ee.agenthub.traceerroragent.traceerror import TraceErrorAnalysisAgent
    from ee.agenthub.traceerroragent.voice_compass import VoiceCompassAgent
except ImportError:
    TraceErrorAnalysisAgent = _ee_stub("TraceErrorAnalysisAgent")
    VoiceCompassAgent = _ee_stub("VoiceCompassAgent")

logger = structlog.get_logger(__name__)
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.prompt_label import PromptLabel
from model_hub.models.run_prompt import PromptVersion
from tfc.temporal import temporal_activity
from tracer.models.external_eval_config import ExternalEvalConfig, StatusChoices
from tracer.models.observation_span import EndUser, ObservationSpan, Project, Trace
from tracer.models.trace import TraceErrorAnalysisStatus, TraceSession
from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.models.trace_error_analysis_task import (
    TraceErrorAnalysisTask,
    TraceErrorTaskStatus,
)
from tracer.queries.error_analysis import TraceErrorAnalysisDB
from tracer.queries.error_clustering import ErrorClusteringDB
from tracer.queries.helpers import get_default_workspace_for_project

# Re-export everything from the tasks package
from tracer.tasks import *  # noqa: F401, F403
from tracer.utils.external_eval import run_external_eval_config
from tracer.utils.helper import get_default_project_session_config
from tracer.utils.otel import SpanAttributes, convert_otel_span_to_observation_span
try:
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    from futureagi._ee_stubs import APICallStatusChoices, APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_api_request, refund_cost_for_api_call
except ImportError:
    from futureagi._ee_stubs import log_and_deduct_cost_for_api_request, refund_cost_for_api_call


def _convert_attributes(attributes):
    """Convert a list of key-value pairs to a dictionary."""
    if not attributes:
        return {}
    return {
        item["key"]: item["value"].get(list(item["value"].keys())[0])
        for item in attributes
        if "key" in item and "value" in item and item["value"]
    }


def _format_id(id_str: str) -> str:
    """Convert base64 encoded ID to hex."""
    if not id_str:
        return None
    return base64.b64decode(id_str).hex()


def _is_hex(s):
    return re.fullmatch(r"^[0-9a-fA-F]+$", s or "") is not None


def _format_if_needed(raw: str) -> str | None:
    if not raw:
        return None
    return raw if _is_hex(raw) else _format_id(raw)
