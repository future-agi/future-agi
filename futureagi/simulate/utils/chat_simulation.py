import json
import uuid
from datetime import datetime, timedelta
from typing import Any, List, Optional, Tuple, Union

import structlog
from django.db import connection, transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.traceerroragent.token_utils import estimate_tokens_text
except ImportError:
    def estimate_tokens_text(text) -> int:
        return 0

try:
    from ee.evals.futureagi.eval_deterministic.evaluator import DeterministicEvaluator
except ImportError:
    DeterministicEvaluator = _ee_stub("DeterministicEvaluator")
from simulate.constants.csat_score_prompt import CSAT_SCORE_PROMPT
from simulate.models import (  # SimulatorAgent
    AgentDefinition,
    CallExecution,
    TestExecution,
)
from simulate.models.chat_message import ChatMessageModel
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import EvalExplanationSummaryStatus
from simulate.pydantic_schemas.chat import (
    ChatMessage,
    ChatRole,
    ChatSessionResponse,
    ChatSessionSendMessageResponse,
    SendChatRequest,
    SimulationCallType,
)
from simulate.services.chat_initial_message import get_chat_initial_message
from simulate.services.test_executor import (
    TestExecutor,
    _run_simulate_evaluations_task,
)
from simulate.utils.sql_query import get_chat_metrics_aggregation_query
from simulate.utils.test_execution_utils import generate_simulator_agent_prompt

logger = structlog.get_logger(__name__)
from tfc.temporal.drop_in import temporal_activity


def _calculate_tokens_from_messages(messages: List[str], content: List[dict]) -> int:
    """
    Calculate total tokens from message content.

    Prefers the ``messages`` list (plain strings) when available because it
    avoids double-counting.  Falls back to the ``content`` list (ChatMessage
    dicts with a ``"content"`` key) only when ``messages`` is empty – this
    covers tool-call-only turns where the string extraction found nothing.
    """
    total_tokens = 0

    # Prefer messages field (list of strings) — one token count per message.
    if messages:
        for message_text in messages:
            if isinstance(message_text, str) and message_text.strip():
                total_tokens += estimate_tokens_text(message_text)
        return total_tokens

    # Fallback: calculate from content field (list of ChatMessage dicts)
    if content:
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    content_text = item.get("content", "")
                    if content_text and isinstance(content_text, str):
                        total_tokens += estimate_tokens_text(content_text)
                elif isinstance(item, str):
                    total_tokens += estimate_tokens_text(item)
        elif isinstance(content, dict):
            content_text = content.get("content", "")
            if content_text:
                total_tokens += estimate_tokens_text(content_text)
        elif isinstance(content, str):
            total_tokens += estimate_tokens_text(content)

    return total_tokens


def _build_chat_transcript(chat_messages: List[ChatMessageModel]) -> str:
    """
    Build a transcript string from chat messages for evaluation purposes.
    Format: "User: <message>\nAssistant: <message>\n..."
    """
    transcript_lines = []
    for msg in chat_messages:
        role_label = "User" if msg.role == ChatRole.USER else "Assistant"
        # Extract text content from messages list or content dict
        content_parts = []

        # First try to get from messages field (list of strings)
        if msg.messages and isinstance(msg.messages, list):
            for message_text in msg.messages:
                if isinstance(message_text, str) and message_text.strip():
                    content_parts.append(message_text)

        # If no content from messages, try content field (list of ChatMessage dicts)
        if not content_parts and msg.content:
            if isinstance(msg.content, list):
                # content is a list of message dicts
                for item in msg.content:
                    if isinstance(item, dict):
                        # Extract content field from ChatMessage dict
                        item_content = item.get("content", "")
                        if item_content and isinstance(item_content, str):
                            content_parts.append(item_content)
                    elif isinstance(item, str):
                        content_parts.append(item)
            elif isinstance(msg.content, dict):
                # Single dict
                content_text = msg.content.get("content", "")
                if content_text:
                    content_parts.append(content_text)
            elif isinstance(msg.content, str):
                content_parts.append(msg.content)

        # Join all content parts
        content = " ".join(content_parts)

        if content.strip():
            transcript_lines.append(f"{role_label}: {content}")

    return "\n".join(transcript_lines)


def _calculate_csat_score(transcript: str) -> Optional[float]:
    """
    Calculate CSAT score from chat transcript using DeterministicEvaluator.
    Returns a float between 1-10 or None if calculation fails.
    """
    try:
        if not transcript or not transcript.strip():
            logger.warning("empty_transcript_for_csat")
            return None

        csat_config = CSAT_SCORE_PROMPT

        evaluator = DeterministicEvaluator(
            multi_choice=csat_config["multi_choice"],
            choices=csat_config["choices"],
            rule_prompt=csat_config["criteria"],
            input=[transcript],
            input_type=["text"],
        )
        result = evaluator._evaluate()

        try:
            csat_score = result.get("data", [])
            if csat_score and len(csat_score) > 0:
                score_value = float(csat_score[0])
                logger.info(
                    "csat_evaluation_result",
                    csat_score=score_value,
                )
                return score_value
        except (ValueError, TypeError, IndexError) as e:
            logger.warning(
                "csat_evaluation_parse_failed",
                error=str(e),
            )
            return None

    except Exception as e:
        logger.exception(
            "error_calculating_csat",
            error=str(e),
        )
        return None


def _aggregate_chat_metrics(call_execution: CallExecution):
    """
    Aggregate metrics from all chat messages for a call execution using SQL queries.
    Calculates total tokens (input, output, total), average latency, turn count, and CSAT.
    Stores aggregated metrics in call_execution.conversation_metrics_data
    """
    try:
        # Use raw SQL to aggregate metrics from individual columns
        with connection.cursor() as cursor:
            query, params = get_chat_metrics_aggregation_query(
                call_execution.id,
                user_role=ChatRole.USER,
                assistant_role=ChatRole.ASSISTANT,
                turn_count_role=ChatRole.ASSISTANT,
            )
            cursor.execute(query, params)
            row = cursor.fetchone()
            total_tokens = row[0] or 0
            input_tokens = row[1] or 0
            output_tokens = row[2] or 0
            avg_latency_ms = int(row[3]) if row[3] is not None else None
            turn_count = row[4] or 0

        # Get all messages for transcript (needed for CSAT)
        chat_messages = list(
            ChatMessageModel.objects.filter(call_execution=call_execution).order_by(
                "created_at"
            )
        )

        # Build transcript and calculate CSAT
        transcript = _build_chat_transcript(chat_messages)
        csat_score = _calculate_csat_score(transcript)

        # For chat agents, overall_score is the CSAT score
        if csat_score is not None:
            call_execution.overall_score = csat_score

        # Store aggregated metrics in conversation_metrics_data
        call_execution.conversation_metrics_data = {
            "total_tokens": total_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "avg_latency_ms": avg_latency_ms,
            "turn_count": turn_count,
            "csat_score": csat_score,
        }

        logger.info(
            "chat_metrics_aggregated",
            call_execution_id=str(call_execution.id),
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            avg_latency_ms=avg_latency_ms,
            turn_count=turn_count,
            csat_score=csat_score,
        )

    except Exception as e:
        logger.exception(
            "error_aggregating_chat_metrics",
            call_execution_id=str(call_execution.id),
            error=str(e),
        )


# Normalize roles inside `content` to match our DB/dashboard convention.
# Wire roles:
# - input_messages_dict: agent-under-test payloads (typically role="user")
# - output_messages_dict: simulator payloads (typically role="assistant")
#
# Stored convention:
# - simulator/customer => role="user"
# - agent-under-test   => role="assistant"
def _swap_user_assistant_roles(items: list[dict]) -> list[dict]:
    swapped: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role == "user":
            swapped.append({**item, "role": "assistant"})
        elif role == "assistant":
            swapped.append({**item, "role": "user"})
        else:
            swapped.append(item)
    return swapped


def extract_message_from_chatmessages(
    messages: List[Union[ChatMessage, dict]], role: ChatRole
) -> list[str]:
    try:
        all_message_content = []

        for message in messages:
            # Handle both dict (from Temporal serialization) and ChatMessage objects
            if isinstance(message, dict):
                msg_role = message.get("role")
                msg_content = message.get("content")
            else:
                msg_role = message.role
                msg_content = message.content

            if (
                msg_role == role
                and isinstance(msg_content, str)
                and len(msg_content) > 0
            ):
                all_message_content.append(msg_content)

        return all_message_content
    except Exception as e:
        logger.exception(f"Error extracting message from chat messages: {str(e)}")
        return []
