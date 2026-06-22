import json
from collections.abc import MutableMapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import structlog
from rest_framework import serializers

from model_hub.models.choices import AnnotationTypeChoices, DataTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from tracer.models.custom_eval_config import CustomEvalConfig, EvalOutputType
from tracer.utils.constants import (
    LIST_OPS,
    NO_VALUE_OPS,
    RANGE_OPS,
    SPAN_ATTR_ALLOWED_OPS,
)
from tracer.utils.filter_operators import FILTER_TYPE_ALLOWED_OPS

logger = structlog.get_logger(__name__)


@dataclass
class FieldConfig:
    id: str
    name: str
    is_visible: bool
    group_by: str | None = None
    output_type: str | None = None
    reverse_output: bool | None = None
    annotation_label_type: AnnotationTypeChoices | None = None
    choices: list[str] | None = (None,)
    settings: dict | None = None
    choices_map: dict | None = None
    eval_template_id: str | None = None
    annotators: dict | None = None
    # When set, this column renders a sub-field (e.g. "reason") of a parent
    # eval column identified by parent_eval_id. Lets the frontend pull the
    # value from eval_outputs without parsing the id.
    source_field: str | None = None
    parent_eval_id: str | None = None
    # Clusters eval columns by the eval_task that ran the config, and records
    # the target_type (span/trace/session) the config was applied at. Populated
    # only for eval columns when an eval_task_map is supplied; None otherwise.
    eval_task_id: str | None = None
    eval_task_name: str | None = None
    target_type: str | None = None


def get_sort_query(sort_by, sort_order="desc"):
    """
    Returns sort query based on sort_by parameter and sort order
    Args:
        sort_by (str): Field to sort by
        sort_order (str): Sort order ('asc' or 'desc'), defaults to 'desc'
    Returns:
        str: Sort query string with appropriate prefix
    """
    prefix = "" if sort_order == "asc" else "-"

    match sort_by:
        case "created_at":
            return f"{prefix}created_at"
        case "updated_at":
            return f"{prefix}updated_at"
        case "name":
            return f"{prefix}name"
        case _:
            return f"{prefix}created_at"  # Default sort by created_at


def get_default_trace_config():
    """Default columns for trace list — ordered by usefulness.

    Priority logic:
    1. Identity — what is this trace?
    2. Status — did it work?
    3. Performance — how long, how much?
    4. Content — what went in/out?
    5. Context — who, when, tags
    """
    config = [
        FieldConfig(id="trace_name", name="Trace Name", is_visible=True, group_by=None),
        FieldConfig(id="input", name="Input", is_visible=True, group_by=None),
        FieldConfig(id="output", name="Output", is_visible=True, group_by=None),
        FieldConfig(id="start_time", name="Timestamp", is_visible=True, group_by=None),
        FieldConfig(id="status", name="Status", is_visible=True, group_by=None),
        FieldConfig(id="latency", name="Latency", is_visible=True, group_by=None),
        FieldConfig(id="total_tokens", name="Tokens", is_visible=True, group_by=None),
        FieldConfig(id="cost", name="Total Cost", is_visible=True, group_by=None),
        FieldConfig(id="model", name="Model", is_visible=True, group_by=None),
        FieldConfig(id="tags", name="Tags", is_visible=True, group_by=None),
        FieldConfig(id="user_id", name="User Id", is_visible=True, group_by=None),
        # Hidden by default — available via Display > View columns
        FieldConfig(id="trace_id", name="Trace Id", is_visible=False, group_by=None),
        FieldConfig(
            id="prompt_tokens", name="Prompt Tokens", is_visible=False, group_by=None
        ),
        FieldConfig(
            id="completion_tokens",
            name="Completion Tokens",
            is_visible=False,
            group_by=None,
        ),
        FieldConfig(id="provider", name="Provider", is_visible=False, group_by=None),
        FieldConfig(
            id="session_id", name="Session Id", is_visible=False, group_by=None
        ),
    ]

    parsed_config = list(map(asdict, config))
    return parsed_config


def get_default_span_config():
    config = [
        FieldConfig(id="span_name", name="Span Name", is_visible=True, group_by=None),
        FieldConfig(id="status", name="Status", is_visible=True, group_by=None),
        FieldConfig(id="input", name="Input", is_visible=True, group_by=None),
        FieldConfig(id="output", name="Output", is_visible=True, group_by=None),
        FieldConfig(id="latency_ms", name="Duration", is_visible=True, group_by=None),
        FieldConfig(id="total_tokens", name="Tokens", is_visible=True, group_by=None),
        FieldConfig(id="cost", name="Total Cost", is_visible=True, group_by=None),
        FieldConfig(id="model", name="Model", is_visible=True, group_by=None),
        FieldConfig(id="start_time", name="Timestamp", is_visible=True, group_by=None),
        # Hidden by default
        FieldConfig(id="span_id", name="Span Id", is_visible=False, group_by=None),
        FieldConfig(id="trace_id", name="Trace Id", is_visible=False, group_by=None),
        FieldConfig(
            id="prompt_tokens", name="Prompt Tokens", is_visible=False, group_by=None
        ),
        FieldConfig(
            id="completion_tokens",
            name="Completion Tokens",
            is_visible=False,
            group_by=None,
        ),
        FieldConfig(id="provider", name="Provider", is_visible=False, group_by=None),
    ]

    parsed_config = list(map(asdict, config))
    return parsed_config


def get_default_project_version_config():
    config = [
        FieldConfig(id="run_name", name="Run Name", is_visible=True, group_by=None),
        FieldConfig(
            id="avg_cost", name="Avg. Cost", is_visible=True, group_by="System Metrics"
        ),
        FieldConfig(
            id="avg_latency",
            name="Avg. Latency",
            is_visible=True,
            group_by="System Metrics",
        ),
        FieldConfig(id="rank", name="Rank", is_visible=False, group_by=None),
    ]

    parsed_config = list(map(asdict, config))
    return parsed_config


def get_default_project_session_config():
    config = [
        FieldConfig(id="session_id", name="Session Id", is_visible=True, group_by=None),
        FieldConfig(
            id="first_message", name="First Message", is_visible=True, group_by=None
        ),
        FieldConfig(
            id="last_message", name="Last Message", is_visible=True, group_by=None
        ),
        FieldConfig(id="duration", name="Duration", is_visible=True, group_by=None),
        FieldConfig(id="total_cost", name="Total Cost", is_visible=True, group_by=None),
        FieldConfig(
            id="total_traces_count", name="Total Traces", is_visible=True, group_by=None
        ),
        FieldConfig(id="start_time", name="Start Time", is_visible=True, group_by=None),
        FieldConfig(id="end_time", name="End Time", is_visible=True, group_by=None),
        FieldConfig(id="user_id", name="User Id", is_visible=True, group_by=None),
        FieldConfig(
            id="user_id_type", name="User Id Type", is_visible=False, group_by=None
        ),
        FieldConfig(
            id="user_id_hash", name="User Id Hash", is_visible=False, group_by=None
        ),
        FieldConfig(
            id="total_tokens", name="Total Tokens", is_visible=False, group_by=None
        ),
    ]

    parsed_config = list(map(asdict, config))
    return parsed_config


def get_default_eval_task_config(is_project_name_visible=True):
    config = [
        FieldConfig(id="name", name="Task Name", is_visible=True, group_by=None),
        FieldConfig(
            id="filters_applied", name="Filters Applied", is_visible=True, group_by=None
        ),
        FieldConfig(
            id="created_at", name="Date Created", is_visible=True, group_by=None
        ),
        FieldConfig(
            id="evals_applied", name="Evals Applied", is_visible=True, group_by=None
        ),
        FieldConfig(
            id="sampling_rate", name="Sampling Rate", is_visible=True, group_by=None
        ),
        FieldConfig(id="last_run", name="Last Run", is_visible=True, group_by=None),
        FieldConfig(id="status", name="Status", is_visible=True, group_by=None),
    ]

    if is_project_name_visible:
        config.insert(
            1,
            FieldConfig(
                id="project_name", name="Project Name", is_visible=True, group_by=None
            ),
        )

    parsed_config = list(map(asdict, config))
    return parsed_config


def is_json(value: str) -> bool:
    try:
        json.loads(value)
        return True
    except json.JSONDecodeError:
        return False


def is_datetime(value: str) -> bool:
    try:
        pd.to_datetime(value)
        return True
    except (ValueError, TypeError):
        return False


def is_image(value: str) -> bool:
    return value.startswith(("data:image", "iVBORw0KGgo"))


def determine_value_type(value):
    # Determine data type based on value
    if isinstance(value, bool):
        return DataTypeChoices.BOOLEAN.value
    elif isinstance(value, int):
        return DataTypeChoices.INTEGER.value
    elif isinstance(value, float):
        return DataTypeChoices.FLOAT.value
    elif isinstance(value, list | tuple):
        return DataTypeChoices.ARRAY.value
    elif isinstance(value, dict):
        return DataTypeChoices.JSON.value
    elif isinstance(value, datetime):
        return DataTypeChoices.DATETIME.value
    elif isinstance(value, str):
        if is_json(value):
            return DataTypeChoices.JSON.value
        elif is_datetime(value):
            return DataTypeChoices.DATETIME.value
        elif is_image(value):
            return DataTypeChoices.IMAGE.value
        return DataTypeChoices.TEXT.value
    else:
        return DataTypeChoices.OTHERS.value


def update_column_config_based_on_eval_config(
    column_config: list[FieldConfig],
    custom_eval_configs: list[CustomEvalConfig],
    skip_choices: bool | None = False,
    is_simulator: bool = False,
    eval_task_map: dict[str, dict] | None = None,
):
    """Append one column per eval config (or per choice for CHOICES evals).

    ``eval_task_map`` optionally clusters each eval column under the eval_task
    that ran it: ``{config_id: {"eval_task_id", "eval_task_name",
    "target_type"}}``. When provided, those three fields are stamped onto each
    eval column (CHOICES sub-columns inherit the parent config's mapping) so
    the frontend can group eval results by task and show the applied
    target_type. Configs absent from the map get ``None`` for all three.
    """
    if not column_config:
        column_config = []

    eval_task_map = eval_task_map or {}

    for item in custom_eval_configs:
        eval_template_config = item.eval_template.config or {}
        output_type = eval_template_config.get("output", "score")
        choices = item.eval_template.choices if item.eval_template.choices else None
        choices_map = item.eval_template.config.get("choices_map", {})

        # For simulator projects, don't add "Avg." prefix
        name_prefix = "" if is_simulator else "Avg. "

        eval_template_id = str(item.eval_template.id)

        task_info = eval_task_map.get(str(item.id), {})
        eval_task_id = task_info.get("eval_task_id")
        eval_task_name = task_info.get("eval_task_name")
        target_type = task_info.get("target_type")

        if choices and output_type == EvalOutputType.CHOICES.value and not skip_choices:
            for choice in choices:
                present_config = FieldConfig(
                    id=str(item.id) + "**" + choice,
                    name=f"{name_prefix}{choice} ({item.name})",
                    group_by="Evaluation Metrics",
                    is_visible=True,
                    output_type=output_type,
                    reverse_output=item.eval_template.config.get(
                        "reverse_output", False
                    ),
                    choices_map=choices_map,
                    eval_template_id=eval_template_id,
                    eval_task_id=eval_task_id,
                    eval_task_name=eval_task_name,
                    target_type=target_type,
                )
                present_config = asdict(present_config)
                if not any(
                    config["id"] == present_config["id"] for config in column_config
                ):
                    column_config.append(present_config)
        else:
            present_config = FieldConfig(
                id=str(item.id),
                name=f"{name_prefix}{item.name}",
                group_by="Evaluation Metrics",
                is_visible=True,
                output_type=output_type,
                reverse_output=item.eval_template.config.get("reverse_output", False),
                choices_map=choices_map,
                choices=choices,
                eval_template_id=eval_template_id,
                eval_task_id=eval_task_id,
                eval_task_name=eval_task_name,
                target_type=target_type,
            )
            present_config = asdict(present_config)
            if not any(
                config["id"] == present_config["id"] for config in column_config
            ):
                column_config.append(present_config)

    return column_config


def _eval_chip_value(cell, output_type, choices):
    """Map a count cell to its chip value given an output type + choice labels.

    Single source of truth for the Pass/Fail + Choices "count" rendering, used
    by both ``eval_count_cell`` (list endpoints) and
    ``build_task_grouped_eval_scores`` (trace detail). ``cell`` is a dict with
    ``choice_counts`` (Choices) or ``pass_count``/``fail_count`` (Pass/Fail) or
    ``avg_score`` (Score). A non-dict ``cell`` is returned unchanged.
    """
    if not isinstance(cell, dict):
        return cell

    if output_type == EvalOutputType.CHOICES.value:
        counts = cell.get("choice_counts", {}) or {}
        if choices:
            return {str(choice): int(counts.get(str(choice), 0)) for choice in choices}
        return {str(k): int(v) for k, v in counts.items()}

    if output_type == EvalOutputType.PASS_FAIL.value:
        return {
            "pass": int(cell.get("pass_count", 0) or 0),
            "fail": int(cell.get("fail_count", 0) or 0),
        }

    return cell.get("avg_score")


def eval_count_cell(scores, eval_config):
    """Chip-style value for one count-mode eval cell.

    Shared by the trace/voice/span list endpoints so the Pass/Fail + Choices
    "count" rendering lives in one place. Given a count-mode pivot cell
    (``pivot_eval_results(..., count_mode=True)``) and its ``CustomEvalConfig``,
    returns the value to render:

      * Choices   -> ``{label: count}`` zero-filled across the template's
                     declared choices (one chip per label).
      * Pass/Fail -> ``{"pass": n, "fail": n}`` (exact appearance counts).
      * Score     -> the numeric average (or ``None``).

    Callers must handle the ``{"error": True}`` marker before calling this; a
    non-dict ``scores`` is returned unchanged.
    """
    if not isinstance(scores, dict):
        return scores

    template = getattr(eval_config, "eval_template", None)
    output_type = (getattr(template, "config", None) or {}).get(
        "output", EvalOutputType.SCORE.value
    )
    choices = getattr(template, "choices", None) or []
    return _eval_chip_value(scores, output_type, choices)


# ---------------------------------------------------------------------------
# Trace-detail: eval scores grouped eval_task -> eval -> {aggregate, spans}.
# ---------------------------------------------------------------------------


def _eval_row_is_error(row):
    """True when an EvalLogger row represents an error (mirrors the list
    endpoints' ``error = 1 OR output_str = 'ERROR'`` guard)."""
    if row.get("error"):
        return True
    return (row.get("output_str") or "") == "ERROR"


def _eval_row_bool(value):
    """Normalise an ``output_bool`` (CH ``Nullable(UInt8)``) to True/False/None."""
    if value is None:
        return None
    return bool(value)


def _eval_row_choice_labels(value):
    """Parse an ``output_str_list`` (CH ``String DEFAULT '[]'`` or a list) into
    a list of label strings; empty list when absent/unparseable."""
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    return []


def _aggregate_eval_cell(rows, output_type):
    """Aggregate non-errored EvalLogger rows into a count cell for one eval.

    Score -> ``{"avg_score": mean(output_float)*100}``; Pass/Fail ->
    ``{"pass_count", "fail_count"}``; Choices -> ``{"choice_counts": {...}}``.
    """
    live = [r for r in rows if not _eval_row_is_error(r)]

    if output_type == EvalOutputType.CHOICES.value:
        counts: dict = {}
        for row in live:
            for label in set(_eval_row_choice_labels(row.get("output_str_list"))):
                counts[label] = counts.get(label, 0) + 1
        return {"choice_counts": counts}

    if output_type == EvalOutputType.PASS_FAIL.value:
        pass_count = sum(
            1 for r in live if _eval_row_bool(r.get("output_bool")) is True
        )
        fail_count = sum(
            1 for r in live if _eval_row_bool(r.get("output_bool")) is False
        )
        return {"pass_count": pass_count, "fail_count": fail_count}

    vals = [
        r.get("output_float")
        for r in live
        if isinstance(r.get("output_float"), (int, float))
        and not isinstance(r.get("output_float"), bool)
    ]
    avg = round(sum(vals) / len(vals) * 100, 2) if vals else None
    return {"avg_score": avg}


def _per_span_eval_value(rows, output_type):
    """Raw per-span value (one span's rows): Score -> number, Pass/Fail ->
    ``"pass"``/``"fail"``, Choices -> ``[labels]``. Uses the latest non-errored
    row (re-runs); ``None`` when every row errored."""
    live = [r for r in rows if not _eval_row_is_error(r)]
    if not live:
        return None
    row = live[-1]

    if output_type == EvalOutputType.CHOICES.value:
        return _eval_row_choice_labels(row.get("output_str_list"))

    if output_type == EvalOutputType.PASS_FAIL.value:
        b = _eval_row_bool(row.get("output_bool"))
        return None if b is None else ("pass" if b else "fail")

    f = row.get("output_float")
    if isinstance(f, (int, float)) and not isinstance(f, bool):
        return round(f * 100, 2)
    return None


def build_task_grouped_eval_scores(
    rows, config_lookup, task_lookup, span_name_map, scope
):
    """Group span-level EvalLogger rows into the task -> eval -> {aggregate,
    spans} structure used by the trace-detail response.

    ``rows``: already-filtered EvalLogger-shaped dicts (caller passes all trace
    rows for the root span, or one span's rows for a child). Each dict needs
    ``span_id``, ``eval_config_id``, ``eval_task_id``, ``output_float``,
    ``output_bool``, ``output_str``, ``output_str_list``, ``error``,
    ``explanation``. ``config_lookup``: ``{cid: {"name","output","choices"}}``.
    ``task_lookup``: ``{eval_task_id: name}``. ``span_name_map``:
    ``{span_id: name}``. ``scope``: ``"trace"`` (root) or ``"span"``.

    Pure in-memory, single pass over ``rows`` — no DB access.
    """
    # eval_task_id -> config_id -> [rows]; dict preserves first-seen order.
    tasks: dict = {}
    for row in rows:
        cid = row.get("eval_config_id") or ""
        if not cid or cid not in config_lookup or not row.get("span_id"):
            continue
        tid = row.get("eval_task_id") or None
        tasks.setdefault(tid, {}).setdefault(cid, []).append(row)

    eval_tasks = []
    for tid, configs in tasks.items():
        evals = []
        for cid, eval_rows in configs.items():
            info = config_lookup[cid]
            output_type = info.get("output") or EvalOutputType.SCORE.value
            choices = info.get("choices") or []

            aggregate = _eval_chip_value(
                _aggregate_eval_cell(eval_rows, output_type), output_type, choices
            )

            # target_type (span/trace/session) the eval was applied at — same
            # discriminator the list endpoints carry per (config, task). Rows in
            # one (task, config) group share it; take the first non-null.
            target_type = next(
                (r.get("target_type") for r in eval_rows if r.get("target_type")),
                None,
            )

            # One entry per span (group this eval's rows by span_id).
            by_span: dict = {}
            for row in eval_rows:
                by_span.setdefault(row.get("span_id"), []).append(row)
            spans = []
            for sid, span_rows in by_span.items():
                explanation = next(
                    (r.get("explanation") for r in span_rows if r.get("explanation")),
                    None,
                )
                spans.append(
                    {
                        "span_id": sid,
                        "span_name": span_name_map.get(sid),
                        "value": _per_span_eval_value(span_rows, output_type),
                        "explanation": explanation,
                        "error": all(_eval_row_is_error(r) for r in span_rows),
                    }
                )

            evals.append(
                {
                    "eval_config_id": cid,
                    "eval_name": info.get("name", cid),
                    "output_type": output_type,
                    "target_type": target_type,
                    "aggregate": aggregate,
                    "spans": spans,
                }
            )

        eval_tasks.append(
            {
                "eval_task_id": tid,
                "eval_task_name": (task_lookup.get(tid) if tid else "Ungrouped"),
                "evals": evals,
            }
        )

    return {"scope": scope, "eval_tasks": eval_tasks}


def fetch_grouped_eval_rows(analytics, trace_id):
    """Fetch non-deleted span-level eval rows for a trace + the batched name
    lookups needed to group them.

    Shared by the trace-detail and voice-call-detail endpoints. Runs ONE CH
    query against the eval-logger table plus two batched PG lookups (config
    names/output/choices via ``select_related`` — no extra query; task names) —
    soft-deleted rows, configs and tasks are all excluded. Trace/session-level
    rows (no ``observation_span_id``) are skipped. Any CH/PG failure is logged
    and returns empty structures (non-fatal).

    Returns ``(eval_rows, rows_by_span, config_lookup, task_lookup)``:
      * ``eval_rows``    — normalised dicts (``span_id``, ``eval_config_id``,
        ``eval_task_id``, ``output_float``/``bool``/``str``/``str_list``,
        ``error``, ``explanation``)
      * ``rows_by_span`` — ``{span_id: [rows]}``
      * ``config_lookup``— ``{config_id: {"name", "output", "choices"}}``
      * ``task_lookup``  — ``{eval_task_id: name}``
    """
    from tracer.models.eval_task import EvalTask
    from tracer.services.clickhouse.eval_logger_table import eval_logger_source

    eval_rows: list[dict] = []
    rows_by_span: dict[str, list[dict]] = {}
    config_lookup: dict[str, dict] = {}
    task_lookup: dict[str, str] = {}
    try:
        eval_table, eval_nd = eval_logger_source()
        eval_query = f"""
        SELECT
            toString(observation_span_id) AS span_id,
            toString(custom_eval_config_id) AS eval_config_id,
            eval_task_id,
            target_type,
            output_float,
            output_bool,
            output_str,
            output_str_list,
            error,
            eval_explanation
        FROM {eval_table} FINAL
        WHERE trace_id = %(trace_id)s
          AND {eval_nd}
        """
        eval_result = analytics.execute_ch_query(
            eval_query, {"trace_id": str(trace_id)}, timeout_ms=30000
        )

        # Collect unique config + task IDs for batched name lookups.
        config_ids_set = set()
        task_ids_set = set()
        for row in eval_result.data:
            if not row.get("span_id"):
                continue  # span-level evals only (skip trace/session rows)
            cid = row.get("eval_config_id", "")
            if cid:
                config_ids_set.add(cid)
            tid = row.get("eval_task_id")
            if tid:
                task_ids_set.add(str(tid))

        if config_ids_set:
            configs = CustomEvalConfig.objects.filter(
                id__in=list(config_ids_set), deleted=False
            ).select_related("eval_template")
            config_lookup = {
                str(c.id): {
                    # Prefer the CustomEvalConfig's user-given name, fall back
                    # to the template name only if unset.
                    "name": c.name
                    or (c.eval_template.name if c.eval_template else str(c.id)),
                    "output": (
                        (c.eval_template.config or {}).get(
                            "output", EvalOutputType.SCORE.value
                        )
                        if c.eval_template
                        else EvalOutputType.SCORE.value
                    ),
                    "choices": (
                        (c.eval_template.choices or []) if c.eval_template else []
                    ),
                    "template_type": (
                        getattr(c.eval_template, "template_type", None)
                        if c.eval_template
                        else None
                    ),
                }
                for c in configs
            }

        if task_ids_set:
            task_lookup = {
                str(tid): name
                for tid, name in EvalTask.objects.filter(
                    id__in=task_ids_set, deleted=False
                ).values_list("id", "name")
            }

        # Normalise rows once; bucket by span for per-span child structures.
        for row in eval_result.data:
            sid = row.get("span_id", "")
            cid = row.get("eval_config_id", "")
            if not sid or not cid or cid not in config_lookup:
                continue
            tid = row.get("eval_task_id")
            normalized = {
                "span_id": sid,
                "eval_config_id": cid,
                "eval_task_id": str(tid) if tid else None,
                "target_type": row.get("target_type") or None,
                "output_float": row.get("output_float"),
                "output_bool": row.get("output_bool"),
                "output_str": row.get("output_str"),
                "output_str_list": row.get("output_str_list"),
                "error": row.get("error"),
                "explanation": row.get("eval_explanation") or None,
                "template_type": row.get("template_type"),
            }
            eval_rows.append(normalized)
            rows_by_span.setdefault(sid, []).append(normalized)
    except Exception as e:
        logger.warning("fetch_grouped_eval_rows_failed", error=str(e))

    return eval_rows, rows_by_span, config_lookup, task_lookup


def attach_grouped_eval_scores(
    span_targets, eval_rows, rows_by_span, config_lookup, task_lookup
):
    """Attach grouped ``eval_scores`` to span target dicts (in place) and
    return the trace-level structure.

    Shared by the trace-detail and voice-call-detail endpoints so the "root
    span gets the trace view, every other span gets its own scope" wiring lives
    in one place. ``span_targets`` is an iterable of
    ``(span_id, span_name, is_root, target)`` where ``target`` is the dict that
    receives ``target['eval_scores']``. Root spans (``is_root`` true) get the
    trace-level view (aggregate + span-wise across ALL spans); every other span
    gets the same structure scoped to just itself. The trace-level structure is
    returned so callers can also surface it at the top level.
    """
    span_targets = list(span_targets)
    span_name_map = {str(sid): name for sid, name, _is_root, _t in span_targets}
    trace_level = build_task_grouped_eval_scores(
        eval_rows, config_lookup, task_lookup, span_name_map, "trace"
    )
    for sid, _name, is_root, target in span_targets:
        if is_root:
            target["eval_scores"] = trace_level
        else:
            target["eval_scores"] = build_task_grouped_eval_scores(
                rows_by_span.get(str(sid), []),
                config_lookup,
                task_lookup,
                span_name_map,
                "span",
            )
    return trace_level


def build_eval_task_map(discovery_rows, alive_config_ids):
    """Cluster eval configs by the eval_task that ran them + the target_type.

    Shared by the trace/span/voice list endpoints to enrich each eval column
    with the eval_task it belongs to and the target_type it was applied at.

    ``discovery_rows``: iterable of ``(config_id, eval_task_id, target_type,
    last_seen)`` tuples — typically the ``(config, task, target_type)`` groups
    of NON-DELETED rows in the eval_logger table, each carrying that group's
    ``max(created_at)`` as ``last_seen``. Because the not-deleted filter is
    applied before grouping, a config whose loggers under a given task are all
    soft-deleted produces no row for that task and so drops out of it.
    ``alive_config_ids``: ids whose ``CustomEvalConfig`` is not soft-deleted;
    rows for any other config are ignored.

    Returns ``{config_id: {"eval_task_id", "eval_task_name", "target_type"}}``
    keeping the most-recent surviving ``(task, target_type)`` per config
    (single task + single target_type per eval column).
    """
    from tracer.models.eval_task import EvalTask

    alive = {str(c) for c in alive_config_ids}
    # config_id -> (eval_task_id, target_type, last_seen)
    chosen: dict[str, tuple] = {}
    for config_id, eval_task_id, target_type, last_seen in discovery_rows:
        if not config_id:
            continue
        config_id = str(config_id)
        if config_id not in alive:
            continue
        existing = chosen.get(config_id)
        if existing is None or (
            last_seen is not None
            and existing[2] is not None
            and last_seen > existing[2]
        ):
            chosen[config_id] = (
                str(eval_task_id) if eval_task_id else None,
                target_type or None,
                last_seen,
            )

    task_ids = {v[0] for v in chosen.values() if v[0]}
    task_names = (
        {
            str(tid): name
            for tid, name in EvalTask.objects.filter(id__in=task_ids).values_list(
                "id", "name"
            )
        }
        if task_ids
        else {}
    )
    return {
        cid: {
            "eval_task_id": eval_task_id,
            "eval_task_name": task_names.get(eval_task_id) if eval_task_id else None,
            "target_type": target_type,
        }
        for cid, (eval_task_id, target_type, _last_seen) in chosen.items()
    }


def _validate_span_attribute_filter(column_id, filter_config):
    """Enforce the SPAN_ATTRIBUTE type/op/value contract; raise on mismatch."""
    ftype = (filter_config.get("filter_type") or "").lower()
    fop = filter_config.get("filter_op")
    fval = filter_config.get("filter_value")

    if ftype not in SPAN_ATTR_ALLOWED_OPS:
        raise serializers.ValidationError(
            f"Filter {column_id!r}: unsupported filter_type {ftype!r} "
            f"for SPAN_ATTRIBUTE (expected one of {sorted(SPAN_ATTR_ALLOWED_OPS)})."
        )

    allowed = SPAN_ATTR_ALLOWED_OPS[ftype]
    if fop not in allowed:
        raise serializers.ValidationError(
            f"Filter {column_id!r}: filter_op {fop!r} is not valid for "
            f"filter_type {ftype!r}. Allowed: {sorted(allowed)}."
        )

    if fop in NO_VALUE_OPS:
        return

    if fop in RANGE_OPS:
        if not isinstance(fval, list) or len(fval) != 2:
            raise serializers.ValidationError(
                f"Filter {column_id!r}: {fop!r} requires a 2-element list, "
                f"got {fval!r}."
            )
        values_to_check = fval
    elif fop in LIST_OPS:
        if not isinstance(fval, list) or not fval:
            raise serializers.ValidationError(
                f"Filter {column_id!r}: {fop!r} requires a non-empty list, "
                f"got {fval!r}."
            )
        values_to_check = fval
    else:
        if fval is None:
            raise serializers.ValidationError(
                f"Filter {column_id!r}: {fop!r} requires a value."
            )
        values_to_check = [fval]

    if ftype == "number":
        for v in values_to_check:
            try:
                float(v)
            except (TypeError, ValueError):
                raise serializers.ValidationError(
                    f"Filter {column_id!r}: numeric filter_value must be "
                    f"coercible to float, got {v!r}."
                )
    elif ftype == "boolean":
        # Strict native bool only.
        for v in values_to_check:
            if not isinstance(v, bool):
                raise serializers.ValidationError(
                    f"Filter {column_id!r}: boolean filter_value must be a "
                    f"native true/false, got {v!r}."
                )


def validate_filters_helper(value):
    if not value:
        return []

    REQUIRED_FILTER_KEYS = ["column_id", "filter_config"]
    VALID_FILTER_KEYS = {"column_id", "display_name", "filter_config"}
    REQUIRED_CONFIG_KEYS = ["filter_type", "filter_op"]
    VALID_CONFIG_KEYS = {"filter_type", "filter_op", "filter_value", "col_type"}

    for filter_item in value:
        if not isinstance(filter_item, dict):
            raise serializers.ValidationError("Each filter must be a dictionary.")

        missing_keys = [key for key in REQUIRED_FILTER_KEYS if key not in filter_item]
        if missing_keys:
            raise serializers.ValidationError(
                f"Missing required filter keys: {', '.join(missing_keys)}"
            )
        extra_keys = sorted(set(filter_item) - VALID_FILTER_KEYS)
        if extra_keys:
            raise serializers.ValidationError(
                f"Unknown filter keys: {', '.join(extra_keys)}"
            )

        filter_config = filter_item.get("filter_config")
        if not isinstance(filter_config, dict):
            raise serializers.ValidationError("Filter config must be a dictionary.")

        missing_keys = [key for key in REQUIRED_CONFIG_KEYS if key not in filter_config]
        if missing_keys:
            raise serializers.ValidationError(
                f"Missing required filter config keys: {', '.join(missing_keys)}"
            )
        extra_config_keys = sorted(set(filter_config) - VALID_CONFIG_KEYS)
        if extra_config_keys:
            raise serializers.ValidationError(
                f"Unknown filter config keys: {', '.join(extra_config_keys)}"
            )

        filter_type = filter_config.get("filter_type")
        filter_op = filter_config.get("filter_op")
        allowed_ops = FILTER_TYPE_ALLOWED_OPS.get(filter_type)
        if allowed_ops is None:
            raise serializers.ValidationError(
                f"Unsupported filter_type {filter_type!r}."
            )
        if filter_op not in allowed_ops:
            raise serializers.ValidationError(
                f"Unsupported filter_op {filter_op!r} for filter_type {filter_type!r}."
            )
        if filter_op in RANGE_OPS:
            filter_value = filter_config.get("filter_value")
            if not isinstance(filter_value, list) or len(filter_value) != 2:
                raise serializers.ValidationError(
                    f"Filter {filter_item.get('column_id')!r}: {filter_op!r} "
                    "requires a 2-element filter_value list."
                )
        elif filter_op in LIST_OPS:
            filter_value = filter_config.get("filter_value")
            if not isinstance(filter_value, list) or not filter_value:
                raise serializers.ValidationError(
                    f"Filter {filter_item.get('column_id')!r}: {filter_op!r} "
                    "requires a non-empty filter_value list."
                )
        elif filter_op not in NO_VALUE_OPS and "filter_value" not in filter_config:
            raise serializers.ValidationError(
                f"Filter {filter_item.get('column_id')!r}: {filter_op!r} requires filter_value."
            )

        col_type = filter_config.get("col_type")
        if col_type == "SPAN_ATTRIBUTE":
            _validate_span_attribute_filter(filter_item.get("column_id"), filter_config)

    return value


def validate_sort_params_helper(value):
    """Validate that each sort parameter has the required keys."""
    REQUIRED_SORT_KEYS = ["column_id", "direction"]
    VALID_DIRECTIONS = ["asc", "desc"]

    if not value:
        return []

    for sort_item in value:
        if not isinstance(sort_item, dict):
            raise serializers.ValidationError(
                "Each sort parameter must be a dictionary."
            )

        missing_keys = [key for key in REQUIRED_SORT_KEYS if key not in sort_item]
        if missing_keys:
            raise serializers.ValidationError(
                f"Missing required sort keys: {', '.join(missing_keys)}"
            )

        if "direction" in sort_item and sort_item["direction"] not in VALID_DIRECTIONS:
            raise serializers.ValidationError(
                f"Sort direction must be one of {VALID_DIRECTIONS}, got {sort_item['direction']}"
            )

    return value


def get_annotation_labels_for_project(project_id, organization=None):
    """Find annotation labels that have at least one Score in a project.

    Labels may not have a direct ``project`` FK set (e.g. org-wide centralized
    labels), so we look for labels referenced by Score records whose trace or
    observation_span belongs to the project.

    Pre-deprecation this method also union'd in ``TraceAnnotation``-referenced
    labels. Score is the unified store now (the dual-write mirrors every
    TraceAnnotation write to Score, so any label in TraceAnnotation is also
    reachable via Score). Reading both was redundant; reading Score alone
    is the path forward toward fully retiring TraceAnnotation.
    """
    from django.db.models import Q

    from model_hub.models.score import Score

    # Labels with scores for this project
    score_label_ids = (
        Score.objects.filter(
            Q(trace__project_id=project_id)
            | Q(observation_span__project_id=project_id),
            deleted=False,
        )
        .values("label_id")
        .distinct()
    )

    return AnnotationsLabels.objects.filter(
        Q(project_id=project_id) | Q(id__in=score_label_ids),
        deleted=False,
    ).distinct()


def update_span_column_config_based_on_annotations(
    column_config: list[FieldConfig], annotation_labels: list[AnnotationsLabels]
):
    from model_hub.models.score import Score

    if not column_config:
        column_config = []

    # Batch-fetch distinct annotators for all labels in one query
    label_ids = [label.id for label in annotation_labels]
    annotator_rows = (
        Score.objects.filter(label_id__in=label_ids, deleted=False)
        .values("label_id", "annotator_id", "annotator__name", "annotator__email")
        .distinct()
    )

    # Build a map: label_id → {user_id: {userId, userName}}
    label_annotators_map: dict[str, dict] = {}
    for row in annotator_rows:
        lid = str(row["label_id"])
        uid = str(row["annotator_id"])
        if lid not in label_annotators_map:
            label_annotators_map[lid] = {}
        label_annotators_map[lid][uid] = {
            "user_id": uid,
            "user_name": row["annotator__name"] or row["annotator__email"] or "Unknown",
        }

    for label in annotation_labels:
        choices = []
        if label.type == AnnotationTypeChoices.CATEGORICAL.value:
            choices = [option["label"] for option in label.settings["options"]]

        label_type = label.type
        output_type = float

        if label_type == AnnotationTypeChoices.CATEGORICAL.value:
            output_type = "list"
        elif label_type == AnnotationTypeChoices.TEXT.value:
            output_type = "text"
        elif label_type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
            output_type = "boolean"
        else:
            output_type = "float"

        present_config = FieldConfig(
            id=str(label.id),
            name=f"{label.name}",
            group_by="Annotation Metrics",
            is_visible=True,
            output_type=output_type,
            reverse_output=False,
            annotation_label_type=label.type,
            choices=choices if len(choices) > 0 else None,
            settings=label.settings,
            annotators=label_annotators_map.get(str(label.id)),
        )
        present_config = asdict(present_config)
        if not any(config["id"] == present_config["id"] for config in column_config):
            column_config.append(present_config)

    return column_config


def update_run_column_config_based_on_annotations(
    column_config: list[FieldConfig], annotation_labels: list[AnnotationsLabels]
):
    if not column_config:
        column_config = []

    for label in annotation_labels:
        choices = []
        if label.type == AnnotationTypeChoices.CATEGORICAL.value:
            choices = [option["label"] for option in label.settings["options"]]

        if choices and len(choices) > 0:
            for choice in choices:
                present_config = FieldConfig(
                    id=str(label.id) + "**" + choice,
                    name=f"Avg. {choice} ({label.name})",
                    group_by="Annotation Metrics",
                    is_visible=True,
                    output_type="float",
                    reverse_output=False,
                    choices=choices,
                    settings=label.settings,
                )
                present_config = asdict(present_config)
                if not any(
                    config["id"] == present_config["id"] for config in column_config
                ):
                    column_config.append(present_config)
        else:
            present_config = FieldConfig(
                id=str(label.id),
                name=f"Avg. {label.name}",
                group_by="Annotation Metrics",
                is_visible=True,
                output_type="float",
                reverse_output=False,
                settings=label.settings,
            )
            present_config = asdict(present_config)
            if not any(
                config["id"] == present_config["id"] for config in column_config
            ):
                column_config.append(present_config)

    return column_config


def generate_timestamps(interval, start_date, end_date):
    timestamps = []
    current = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= end_date:
        timestamps.append({"timestamp": current, "value": 0})
        if interval == "hour":
            current += timedelta(hours=1)
        elif interval == "day":
            current += timedelta(days=1)
        elif interval == "week":
            current += timedelta(weeks=1)
        elif interval == "month":
            current += timedelta(days=30)
        else:
            break  # Invalid interval, just stop
    return timestamps


def format_datetime_to_iso(val):
    """Convert a single datetime value to an ISO 8601 UTC string with 'Z' suffix."""
    if not val:
        return None


def flatten_dict(
    d: MutableMapping[str, Any],
    prefix: str = "",
    sep: str = ".",
) -> dict[str, Any]:
    """
    Flattens a nested dictionary into a single-level dictionary.

    Args:
        d (MutableMapping[str, Any]): The dictionary to flatten.
        prefix (str): The prefix for the keys in the flattened dictionary.
        sep (str): The separator to use between parent and child keys.

    Returns:
        dict[str, Any]: The flattened dictionary.
    """
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        new_key = f"{prefix}{sep}{k}" if prefix else k
        if isinstance(v, MutableMapping):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)
    # Use strftime to produce a consistent UTC format, avoiding double-offset
    # when val is already timezone-aware (e.g. "2024-01-01T00:00:00+00:00Z").
    return val.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def format_datetime_fields_to_iso(rows, fields):
    """Convert datetime fields to ISO 8601 strings with 'Z' suffix in-place."""
    for item in rows:
        for field in fields:
            item[field] = format_datetime_to_iso(item.get(field))


# Helper function to extract date from datetime value
def extract_date(value):
    if isinstance(value, datetime):
        return value.date()
    elif isinstance(value, date):
        return value
    elif isinstance(value, str):
        # Try to parse as datetime string
        try:
            # Try ISO format first
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.date()
        except (ValueError, AttributeError):
            try:
                # Try common datetime formats
                dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                return dt.date()
            except ValueError:
                try:
                    dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
                    return dt.date()
                except ValueError:
                    # If all parsing fails, try date format
                    return datetime.strptime(value, "%Y-%m-%d").date()
    return None
