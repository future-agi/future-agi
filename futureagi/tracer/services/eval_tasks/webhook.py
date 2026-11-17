"""Best-effort notifications for completed continuous span/trace evaluations."""

import logging
import math

from tfc.utils.webhook_delivery import post_webhook
from tracer.models.eval_task import RunType
from tracer.models.observation_span import EvalEntryStatus, EvalTargetType

logger = logging.getLogger(__name__)


def _numeric(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _matches(condition, value):
    operator = condition.get("operator")
    threshold = condition.get("value")
    if operator == "fail":
        # A failed evaluation means a boolean Fail, not an execution error.
        return value is False
    if operator == "eq":
        # A multi-choice result matches a selected label, as choice filters do.
        if isinstance(value, list) and isinstance(threshold, str):
            return threshold in value
        # JSON booleans must not compare equal to numeric 0/1 in Python.
        return value == threshold and (
            type(value) is type(threshold)
            or (_numeric(value) and _numeric(threshold))
        )
    if not (_numeric(value) and _numeric(threshold)):
        return False
    if operator == "gt":
        return value > threshold
    if operator == "lt":
        return value < threshold
    return False


def notify_eval_task_result(task, entry, config):
    """Send at most twice; notification failures never change an eval verdict.

    Delivery is best effort (not an outbox). A timeout may have reached the
    recipient, so receivers must tolerate a duplicate on the single retry.
    The shared transport pins public DNS destinations and refuses redirects.
    """
    try:
        condition = task.webhook_condition
        if (
            not task.webhook_url
            or task.run_type != RunType.CONTINUOUS
            or not isinstance(condition, dict)
            or str(condition.get("eval_config_id")) != str(config.id)
            or entry.target_type not in (EvalTargetType.SPAN, EvalTargetType.TRACE)
            or entry.status != EvalEntryStatus.COMPLETED
            or entry.error
        ):
            return
        # The evaluator dual-writes choice envelopes into output_str and the
        # selected labels into output_str_list. Prefer those typed values over
        # the serialized envelope, just as the app's choice-result readers do.
        value = next(
            (value for value in (entry.output_bool, entry.output_float)
             if value is not None),
            entry.output_str_list or entry.output_str,
        )
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if not _matches(condition, value):
            return
        payload = {
            "project": str(task.project_id),
            "eval_task": str(task.id),
            "source_type": entry.target_type,
            "source_id": str(entry.observation_span_id if entry.target_type == EvalTargetType.SPAN else entry.trace_id),
            "trace_id": str(entry.trace_id),
            "eval_config_id": str(config.id),
            "eval_name": config.name,
            "score": entry.output_float,
            "value": value,
            "explanation": entry.eval_explanation,
            "created_at": entry.created_at.isoformat(),
        }
        for attempt in range(2):
            try:
                post_webhook(
                    task.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )
                return
            except Exception:
                if attempt == 1:
                    raise
    except Exception:
        # Do not log URLs, response bodies, payloads or exception text: they may
        # contain credentials or private evaluation content.
        logger.warning("eval_task_webhook_failed task=%s entry=%s", task.id, entry.id)
