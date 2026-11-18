"""Public task-log reasons. Keep this catalog free of Django for Temporal replay."""

from enum import StrEnum


class SelectionFailure(StrEnum):
    READ_BUDGET = (
        "Evaluation task row selection exceeded its read budget. "
        "Narrow the time range and retry."
    )
    ROW_LIMIT = (
        "This evaluation task is too large for safe row selection. "
        "Reduce the row limit and retry."
    )
    AMBIGUOUS_SPAN = (
        "Evaluation task row selection could not safely distinguish one or more "
        "spans. Narrow the filters and retry."
    )
    UNSUPPORTED_FILTER = (
        "Evaluation task row selection contains a filter that cannot be resolved "
        "safely. Update the filters and retry."
    )
    ANNOTATION_METADATA = (
        "Evaluation task annotation metadata is temporarily unavailable. Retry."
    )
    EVAL_METADATA = (
        "Evaluation task evaluation metadata is temporarily unavailable. Retry."
    )
    OTHER = (
        "The evaluation task could not select rows. Check the task filters and retry."
    )
