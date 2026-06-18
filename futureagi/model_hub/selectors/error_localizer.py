"""ErrorLocalizerTask read helpers."""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID

from model_hub.models.error_localizer_model import (
    ErrorLocalizerSource,
    ErrorLocalizerTask,
)


class ErrorLocalizerState(TypedDict, total=False):
    error_analysis: Any
    error_localizer_status: str
    error_localizer_message: str | None
    selected_input_key: str | None
    input_data: Any
    input_types: Any


class ErrorLocalizerTaskPayload(TypedDict, total=False):
    task_id: str
    eval_config_id: str | None
    status: str
    eval_result: Any
    eval_explanation: str | None
    input_data: Any
    input_keys: list[str]
    input_types: Any
    rule_prompt: str | None
    error_analysis: Any
    selected_input_key: str | None
    error_message: str | None
    created_at: str | None
    updated_at: str | None
    eval_template_name: str | None
    eval_template_id: str | None


def serialize_error_localizer_task(
    task: ErrorLocalizerTask, *, include_eval_template: bool = False
) -> ErrorLocalizerTaskPayload:
    payload: ErrorLocalizerTaskPayload = {
        "task_id": str(task.id),
        "eval_config_id": (task.metadata or {}).get("eval_config_id"),
        "status": task.status,
        "eval_result": task.eval_result,
        "eval_explanation": task.eval_explanation,
        "input_data": task.input_data,
        "input_keys": task.input_keys,
        "input_types": task.input_types,
        "rule_prompt": task.rule_prompt,
        "error_analysis": task.error_analysis,
        "selected_input_key": task.selected_input_key,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }
    if include_eval_template:
        template = getattr(task, "eval_template", None)
        payload["eval_template_name"] = template.name if template else None
        payload["eval_template_id"] = str(template.id) if template else None
    return payload


def list_error_localizer_tasks_for_call_execution(
    call_execution_id: UUID | str,
    *,
    eval_config_id: UUID | str | None = None,
    workspace: Any = None,
    source: str = ErrorLocalizerSource.SIMULATE.value,
    order_latest_first: bool = False,
    skip_workspace_scope: bool = False,
):
    manager = (
        ErrorLocalizerTask.no_workspace_objects
        if skip_workspace_scope
        else ErrorLocalizerTask.objects
    )
    if not call_execution_id:
        return manager.none()

    query_filter: dict[str, Any] = {
        "source": source,
        "metadata__call_execution_id": str(call_execution_id),
    }
    if eval_config_id:
        query_filter["metadata__eval_config_id"] = str(eval_config_id)

    qs = manager.filter(**query_filter)
    if workspace is not None:
        qs = qs.filter(workspace=workspace)
    if order_latest_first:
        qs = qs.order_by("-created_at")
    return qs


def get_error_localizer_state_by_eval_config(
    call_execution_id: UUID | str,
    eval_config_ids: list[str],
    workspace=None,
) -> dict[str, ErrorLocalizerState]:
    """Return EL state keyed by ``eval_config_id`` for a simulate call execution."""
    if not call_execution_id or not eval_config_ids:
        return {}

    qs = ErrorLocalizerTask.objects.filter(
        source=ErrorLocalizerSource.SIMULATE,
        metadata__call_execution_id=str(call_execution_id),
        metadata__eval_config_id__in=eval_config_ids,
    )
    if workspace is not None:
        qs = qs.filter(workspace=workspace)
    qs = qs.only(
        "metadata",
        "status",
        "error_analysis",
        "error_message",
        "selected_input_key",
        "input_data",
        "input_types",
    )

    state_by_eval_config: dict[str, ErrorLocalizerState] = {}
    for task in qs:
        eval_config_id = (task.metadata or {}).get("eval_config_id")
        if not eval_config_id:
            continue
        state_by_eval_config[eval_config_id] = {
            "error_analysis": task.error_analysis or None,
            "error_localizer_status": task.status,
            "error_localizer_message": task.error_message,
            "selected_input_key": task.selected_input_key,
            "input_data": task.input_data,
            "input_types": task.input_types,
        }
    return state_by_eval_config
