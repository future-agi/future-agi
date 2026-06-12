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
    selected_input_key: str | None
    input_data: Any
    input_types: Any


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
            "selected_input_key": task.selected_input_key,
            "input_data": task.input_data,
            "input_types": task.input_types,
        }
    return state_by_eval_config
