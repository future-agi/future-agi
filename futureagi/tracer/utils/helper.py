import json
import math
from collections.abc import MutableMapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, TypedDict, Union

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
    # The EvalTargetType the eval was applied at ("span" / "trace" /
    # "session") — drives the S/T source glyph on eval columns. Populated
    # only for eval columns when an eval_target_map is supplied; None
    # otherwise.
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
    eval_target_map: dict[str, str | None] | None = None,
):
    """Append one column per eval config (or per choice for CHOICES evals).

    ``eval_target_map`` optionally records, per config id, the target_type
    ("span"/"trace"/"session") the eval was applied at — see
    ``build_eval_target_map``. When provided it is stamped onto each eval
    column (CHOICES sub-columns inherit the parent config's value) so the
    frontend can render the S/T source glyph. Configs absent from the map get
    ``None``.
    """
    if not column_config:
        column_config = []

    eval_target_map = eval_target_map or {}

    for item in custom_eval_configs:
        eval_template_config = item.eval_template.config or {}
        output_type = eval_template_config.get("output", "score")
        choices = item.eval_template.choices if item.eval_template.choices else None
        choices_map = item.eval_template.config.get("choices_map", {})

        # For simulator projects, don't add "Avg." prefix
        name_prefix = "" if is_simulator else "Avg. "

        eval_template_id = str(item.eval_template.id)

        target_type = eval_target_map.get(str(item.id))

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
                target_type=target_type,
            )
            present_config = asdict(present_config)
            if not any(
                config["id"] == present_config["id"] for config in column_config
            ):
                column_config.append(present_config)

    return column_config


def _normalize_eval_output_type(output_type: str | None) -> str:
    """Normalize an eval ``output`` type for comparison (``Pass/Fail`` → ``PASS_FAIL``)."""
    return (output_type or "").replace("/", "_").replace(" ", "_").upper()


class EvalErrorScore(TypedDict):
    """All eval rows for a ``(trace, config)`` pair errored."""

    error: bool


class EvalChoicesScore(TypedDict):
    """CHOICES eval — ``{choice: percentage}`` across non-errored rows."""

    per_choice: dict[str, float]


class EvalMarkerScore(TypedDict, total=False):
    """Non-terminal / skipped lifecycle marker (no completed score, no error)."""

    status: str
    skipped_reason: str


class EvalNumericScore(TypedDict):
    """Completed numeric score — ``avg_score``/``pass_rate`` pre-scaled ×100."""

    avg_score: float | None
    pass_rate: float | None
    count: int


# Closed set of shapes emitted by ``pivot_eval_results`` per (trace, config).
PivotEvalScore = Union[
    EvalErrorScore, EvalChoicesScore, EvalMarkerScore, EvalNumericScore
]


def flatten_eval_score_into_entry(
    entry: dict,
    config_id: str,
    scores: PivotEvalScore | Any,
    output_type: str | None,
) -> None:
    """Flatten one pivoted ``(trace, config)`` eval score onto a list-grid row.

    ``scores`` is the per-``(trace, config)`` value from
    ``TraceListQueryBuilder.pivot_eval_results`` — already averaged across the
    trace's spans (SQL ``avgIf``/``groupArray`` grouped by
    ``trace_id, custom_eval_config_id``). The list grids bind eval columns to
    flat row keys, so spread it:

    * CHOICES   → ``{config_id}**{choice}`` = per-choice percentage.
    * PASS_FAIL → ``{config_id}`` = pass rate (avg of ``output_bool``).
    * SCORE     → ``{config_id}`` = numeric avg (avg of ``output_float`` × 100).

    PASS_FAIL must use the pass rate, never the score: an eval that also wrote an
    ``output_float`` (e.g. deterministic evaluators) would otherwise surface the
    score field — frequently inverted vs the real pass/fail result. Non-dict /
    error / non-terminal markers are passed through under ``{config_id}`` so the
    grid can render an error/loading state.
    """
    if not isinstance(scores, dict):
        entry[config_id] = scores
        return
    if scores.get("per_choice"):
        for choice, pct in scores["per_choice"].items():
            entry[f"{config_id}**{choice}"] = pct
        return
    if "avg_score" in scores or "pass_rate" in scores:
        entry[config_id] = select_eval_score(scores, output_type)
        return
    entry[config_id] = scores


def select_eval_score(
    scores: EvalNumericScore, output_type: str | None
) -> float | None:
    """Pick the output-type-aware scalar from a pivoted score dict.

    PASS_FAIL → ``pass_rate`` (rate), everything else → ``avg_score``. Returns
    the value as-is (may be ``0.0``); ``None`` only when the field is absent.
    """
    if _normalize_eval_output_type(output_type) == "PASS_FAIL":
        return scores.get("pass_rate")
    return scores.get("avg_score")


def eval_output_type_for_config(config: CustomEvalConfig) -> str | None:
    """Read an eval config's configured ``output`` type from its template."""
    template = getattr(config, "eval_template", None)
    if template is None:
        return None
    return (getattr(template, "config", None) or {}).get("output")


def get_project_eval_configs(
    project_id,
) -> tuple[list[CustomEvalConfig], list[str]]:
    """Non-deleted eval configs for a project, read from PG (no ClickHouse).

    Replaces the CH ``dictGet('trace_dict',...)`` discovery scan on the voice
    endpoints. Uses the ``(project, created_at)`` index. Returns
    ``(eval_configs, eval_config_ids)``.
    """
    qs = CustomEvalConfig.objects.filter(
        project_id=project_id, deleted=False
    ).select_related("eval_template")
    configs = list(qs)
    return configs, [str(c.id) for c in configs]


def build_eval_target_map(
    discovery_rows,
    alive_config_ids,
) -> dict[str, str | None]:
    """Resolve the target_type each eval config was most recently applied at.

    Shared by the trace/span Observe list endpoints to stamp the S/T source
    glyph onto each eval column. ``discovery_rows``: iterable of
    ``(config_id, target_type, last_seen)`` tuples — the ``(config,
    target_type)`` groups of non-deleted eval_logger rows, each carrying the
    group's ``max(created_at)`` as ``last_seen``. ``alive_config_ids``: ids
    whose ``CustomEvalConfig`` is not soft-deleted; rows for any other config
    are ignored.

    Returns ``{config_id: target_type | None}`` keeping the most-recent
    surviving target_type per config (one identifier per eval column).
    """
    alive = {str(c) for c in alive_config_ids}
    # config_id -> (target_type, last_seen)
    chosen: dict[str, tuple] = {}
    for config_id, target_type, last_seen in discovery_rows:
        if not config_id:
            continue
        config_id = str(config_id)
        if config_id not in alive:
            continue
        existing = chosen.get(config_id)
        if existing is None or (
            last_seen is not None
            and existing[1] is not None
            and last_seen > existing[1]
        ):
            chosen[config_id] = (target_type or None, last_seen)
    return {cid: target_type for cid, (target_type, _last_seen) in chosen.items()}


def _finite_number(value) -> bool:
    """True iff ``value`` is a finite int/float (excludes bool, NaN, inf).

    Guard for the count-mode cell builders: ClickHouse ``avgIf`` returns NaN
    when no rows match, and ``bool(float('nan'))`` is True, so a plain
    truthiness check leaks NaN into the JSON response. Also: a real 0.0 must
    survive (an ``avg_score != 0`` style guard blanks it).
    """
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def build_count_eval_cell(
    *,
    avg_score,
    pass_count,
    fail_count,
    pass_rate=None,
    eval_count=None,
) -> dict[str, Any]:
    """Single source of truth for the count-mode pivot cell shape.

    Shared by ``TraceListQueryBuilder.pivot_eval_results`` and
    ``SpanListQueryBuilder.pivot_eval_results`` so the two count-mode cells
    cannot drift. ``avg_score`` / ``pass_rate`` arrive already rounded (or
    ``None``) from the pivot's finite-guard. ``pass_rate`` and ``eval_count``
    are surfaced only by the trace-list pivot, which keeps them additive for
    parity with its non-count cell.
    """
    cell: dict[str, Any] = {
        "avg_score": avg_score,
        "pass_count": int(pass_count or 0),
        "fail_count": int(fail_count or 0),
    }
    if pass_rate is not None or eval_count is not None:
        cell["pass_rate"] = pass_rate
        cell["count"] = int(eval_count or 0)
    return cell


def _eval_chip_value(cell: dict, output_type: str | None, choices) -> Any:
    """Map a count cell to its chip value given an output type + choice labels.

    Single source of truth for the Pass/Fail + Choices "count" rendering, used
    by both ``eval_count_cell`` (list endpoints) and
    ``build_grouped_eval_scores`` (trace detail). ``cell`` carries
    ``choice_counts`` (Choices), ``pass_count``/``fail_count`` (Pass/Fail) or
    ``avg_score`` (Score).
    """
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


def eval_count_cell(scores, eval_config) -> Any:
    """Chip-style value for one count-mode eval cell.

    Shared by the trace/span Observe list endpoints so the Pass/Fail + Choices
    "count" rendering lives in one place. Given a count-mode pivot cell
    (``pivot_eval_results(..., count_mode=True)``) and its ``CustomEvalConfig``,
    returns the value to render:

      * Choices   -> ``{label: count}`` zero-filled across the template's
                     declared choices (one chip per label).
      * Pass/Fail -> ``{"pass": n, "fail": n}`` (exact appearance counts).
      * Score     -> the numeric average (or ``None``).

    The ``{"error": True}`` and lifecycle ``{"status": ...}`` markers pass
    through unchanged — mapping a marker through the Pass/Fail branch would
    fabricate a ``{"pass": 0, "fail": 0}`` cell out of a still-running eval.
    A non-dict ``scores`` is returned unchanged.
    """
    if not isinstance(scores, dict):
        return scores
    if scores.get("error") or isinstance(scores.get("status"), str):
        return scores

    template = getattr(eval_config, "eval_template", None)
    output_type = (getattr(template, "config", None) or {}).get(
        "output", EvalOutputType.SCORE.value
    )
    choices = getattr(template, "choices", None) or []
    return _eval_chip_value(scores, output_type, choices)


# ---------------------------------------------------------------------------
# Trace-detail: eval scores grouped eval -> {aggregate, spans}.
# ---------------------------------------------------------------------------


def _eval_row_is_error(row) -> bool:
    """True when an EvalLogger row represents an error (mirrors the list
    endpoints' ``error = 1 OR output_str = 'ERROR' OR status = 'errored'``
    guard)."""
    if row.get("error"):
        return True
    if (row.get("output_str") or "") == "ERROR":
        return True
    return (row.get("status") or "").lower() == "errored"


def _eval_row_is_non_terminal(row) -> bool:
    """True for pending/running/skipped rows — no completed result to show.

    Legacy rows whose mirrored ``status`` is empty/NULL count as completed,
    matching the list endpoints' ``status NOT IN (...)`` predicate.
    """
    return (row.get("status") or "").lower() in ("pending", "running", "skipped")


def _eval_row_bool(value):
    """Normalise an ``output_bool`` (CH ``Nullable(UInt8)``) to True/False/None."""
    if value is None:
        return None
    return bool(value)


def _eval_row_choice_labels(value) -> list[str]:
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


def _aggregate_eval_cell(rows, output_type: str | None) -> dict[str, Any]:
    """Aggregate non-errored EvalLogger rows into a count cell for one eval.

    Score -> ``{"avg_score": mean(output_float)*100}``; Pass/Fail ->
    ``{"pass_count", "fail_count"}``; Choices -> ``{"choice_counts": {...}}``.
    Errored and non-terminal (pending/running/skipped) rows are excluded, the
    same population the list endpoints aggregate over.
    """
    live = [
        r
        for r in rows
        if not _eval_row_is_error(r) and not _eval_row_is_non_terminal(r)
    ]

    if output_type == EvalOutputType.CHOICES.value:
        counts: dict[str, int] = {}
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
        r.get("output_float") for r in live if _finite_number(r.get("output_float"))
    ]
    avg = round(sum(vals) / len(vals) * 100, 2) if vals else None
    return {"avg_score": avg}


def _per_span_eval_value(rows, output_type: str | None):
    """Raw per-span value (one span's rows): Score -> number, Pass/Fail ->
    ``"pass"``/``"fail"``, Choices -> ``[labels]``. Uses the latest non-errored
    completed row (re-runs); ``None`` when every row errored.

    "Latest" is determined by ``created_at`` explicitly — the fetch orders by
    it, but a rerun could still arrive out of order from CH, so pick the
    max-created row here rather than relying on scan order.
    """
    live = [
        r
        for r in rows
        if not _eval_row_is_error(r) and not _eval_row_is_non_terminal(r)
    ]
    if not live:
        return None

    def _created_key(r):
        # None ``created_at`` sorts before any value (older).
        ts = r.get("created_at")
        return (ts is not None, ts)

    row = max(live, key=_created_key)

    if output_type == EvalOutputType.CHOICES.value:
        return _eval_row_choice_labels(row.get("output_str_list"))

    if output_type == EvalOutputType.PASS_FAIL.value:
        b = _eval_row_bool(row.get("output_bool"))
        return None if b is None else ("pass" if b else "fail")

    f = row.get("output_float")
    if _finite_number(f):
        return round(f * 100, 2)
    return None


def build_grouped_eval_scores(
    rows, config_lookup, span_name_map, scope
) -> dict[str, Any]:
    """Group span-level EvalLogger rows into the eval -> {aggregate, spans}
    structure used by the trace-detail response.

    ``rows``: already-filtered EvalLogger-shaped dicts (caller passes all trace
    rows for the root span, or one span's rows for a child). Each dict needs
    ``span_id``, ``eval_config_id``, ``output_float``, ``output_bool``,
    ``output_str``, ``output_str_list``, ``error``, ``status``,
    ``explanation``, ``created_at``, ``target_type``. ``config_lookup``:
    ``{cid: {"name", "output", "choices", "choices_map"}}``. ``span_name_map``:
    ``{span_id: name}``. ``scope``: ``"trace"`` (root) or ``"span"``.

    Pure in-memory, single pass over ``rows`` — no DB access. A config that
    ran under several eval tasks folds into ONE eval entry (task grouping is
    out of scope — TH-7610).
    """
    # config_id -> [rows]; dict preserves first-seen order.
    by_config: dict[str, list] = {}
    for row in rows:
        cid = row.get("eval_config_id") or ""
        if not cid or cid not in config_lookup or not row.get("span_id"):
            continue
        by_config.setdefault(cid, []).append(row)

    evals = []
    for cid, eval_rows in by_config.items():
        info = config_lookup[cid]
        output_type = info.get("output") or EvalOutputType.SCORE.value
        choices = info.get("choices") or []

        aggregate = _eval_chip_value(
            _aggregate_eval_cell(eval_rows, output_type), output_type, choices
        )

        # target_type ("span"/"trace"/"session") the eval was applied at —
        # the same discriminator the list columns carry. Take the first
        # non-null; a config's rows share it in practice.
        target_type = next(
            (r.get("target_type") for r in eval_rows if r.get("target_type")),
            None,
        )

        # One entry per span (group this eval's rows by span_id).
        by_span: dict[str, list] = {}
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
                # Same shape the observe column config already sends:
                # ``{"<label>": "pass" | "fail" | "neutral"}`` — drives
                # choice-chip colouring in the trace drawer.
                "choices_map": info.get("choices_map", {}),
                "aggregate": aggregate,
                "spans": spans,
            }
        )

    return {"scope": scope, "evals": evals}


class EvalFetchError(Exception):
    """Raised by ``fetch_grouped_eval_rows`` when the CH read fails.

    The detail endpoint catches this and surfaces the failure to the client
    rather than rendering "no eval scores" — a silent fail-open here is
    indistinguishable from a trace that genuinely has no evals."""


def fetch_grouped_eval_rows(analytics, trace_id):
    """Fetch non-deleted, completed-or-errored span-level eval rows for a
    trace + the batched config lookup needed to group them.

    Runs ONE CH query against the eval-logger table plus one batched PG config
    lookup (``select_related`` — no extra query). Soft-deleted rows and
    configs are excluded; session-level rows (no ``observation_span_id``) are
    skipped (trace-level rows anchor to the root span, so they are retained);
    non-terminal rows (pending/running/skipped) carry no result and are
    skipped. The CH query orders by ``created_at`` ASC so per-span "latest
    rerun" logic downstream can pick ``max(created_at)`` deterministically.

    Returns ``(eval_rows, rows_by_span, config_lookup)``:
      * ``eval_rows``    — normalised dicts (``span_id``, ``eval_config_id``,
        ``target_type``, ``output_float``/``bool``/``str``/``str_list``,
        ``error``, ``status``, ``explanation``, ``created_at``)
      * ``rows_by_span`` — ``{span_id: [rows]}``
      * ``config_lookup``— ``{config_id: {"name", "output", "choices",
        "choices_map"}}``

    Raises:
        EvalFetchError: the CH read failed. Callers should render an explicit
            error state — silently returning empty would look like "no evals
            ran" to the client.
    """
    from tracer.services.clickhouse.eval_logger_table import eval_logger_source

    eval_rows: list[dict] = []
    rows_by_span: dict[str, list[dict]] = {}
    config_lookup: dict[str, dict] = {}
    try:
        eval_table, eval_nd = eval_logger_source()
        eval_query = f"""
        SELECT
            toString(observation_span_id) AS span_id,
            toString(custom_eval_config_id) AS eval_config_id,
            target_type,
            output_float,
            output_bool,
            output_str,
            output_str_list,
            error,
            status,
            eval_explanation,
            created_at
        FROM {eval_table} FINAL
        WHERE trace_id = %(trace_id)s
          AND {eval_nd}
        ORDER BY created_at ASC
        """
        eval_result = analytics.execute_ch_query(
            eval_query, {"trace_id": str(trace_id)}, timeout_ms=30000
        )

        # Collect unique config IDs for the batched name lookup.
        config_ids_set = set()
        for row in eval_result.data:
            if not row.get("span_id"):
                continue  # session-level rows have no span anchor
            cid = row.get("eval_config_id", "")
            if cid:
                config_ids_set.add(cid)

        if config_ids_set:
            configs = CustomEvalConfig.objects.filter(
                id__in=list(config_ids_set), deleted=False
            ).select_related("eval_template")
            config_lookup = {
                str(c.id): {
                    # Prefer the CustomEvalConfig's user-given name, fall back
                    # to the template name only if unset — keeps the drawer
                    # labels in sync with the list column headers.
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
                    # ``choices_map`` colours each chip in the FE drawer
                    # ({"<label>": "pass"|"fail"|"neutral"}); without it the
                    # grouped eval_scores chips render neutral.
                    "choices_map": (
                        (c.eval_template.config or {}).get("choices_map", {})
                        if c.eval_template
                        else {}
                    ),
                }
                for c in configs
            }

        # Normalise rows once; bucket by span for per-span child structures.
        for row in eval_result.data:
            sid = row.get("span_id", "")
            cid = row.get("eval_config_id", "")
            if not sid or not cid or cid not in config_lookup:
                continue
            if _eval_row_is_non_terminal(row):
                continue  # pending/running/skipped: no result to show yet
            normalized = {
                "span_id": sid,
                "eval_config_id": cid,
                "target_type": row.get("target_type") or None,
                "output_float": row.get("output_float"),
                "output_bool": row.get("output_bool"),
                "output_str": row.get("output_str"),
                "output_str_list": row.get("output_str_list"),
                "error": row.get("error"),
                "status": row.get("status"),
                "explanation": row.get("eval_explanation") or None,
                # ``created_at`` lets the per-span value picker resolve "latest
                # rerun" deterministically rather than relying on scan order.
                "created_at": row.get("created_at"),
            }
            eval_rows.append(normalized)
            rows_by_span.setdefault(sid, []).append(normalized)
    except EvalFetchError:
        raise
    except Exception as e:
        # CH-read failure is a data read, not a best-effort side effect;
        # converting it to ``EvalFetchError`` lets the view distinguish
        # "fetch broke" from "no evals".
        logger.error("fetch_grouped_eval_rows_failed", error=str(e))
        raise EvalFetchError(str(e)) from e

    return eval_rows, rows_by_span, config_lookup


def attach_grouped_eval_scores(span_targets, eval_rows, rows_by_span, config_lookup):
    """Attach grouped ``eval_scores`` to span target dicts (in place) and
    return the trace-level structure.

    ``span_targets`` is an iterable of ``(span_id, span_name, is_root,
    target)`` where ``target`` is the dict that receives
    ``target['eval_scores']``. Root spans (``is_root`` true) get the
    trace-level view (aggregate + span-wise across ALL spans); every other
    span gets the same structure scoped to just itself. The trace-level
    structure is returned so callers can also surface it at the top level.
    """
    span_targets = list(span_targets)
    span_name_map = {str(sid): name for sid, name, _is_root, _t in span_targets}
    trace_level = build_grouped_eval_scores(
        eval_rows, config_lookup, span_name_map, "trace"
    )
    for sid, _name, is_root, target in span_targets:
        if is_root:
            target["eval_scores"] = trace_level
        else:
            target["eval_scores"] = build_grouped_eval_scores(
                rows_by_span.get(str(sid), []),
                config_lookup,
                span_name_map,
                "span",
            )
    return trace_level


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


def get_annotation_labels_for_project(project_id, organization=None, project_ids=None):
    """Find annotation labels that have at least one Score in a project.

    Labels may not have a direct ``project`` FK set (e.g. org-wide centralized
    labels), so we also look for labels referenced by Score records in the project.

    Pass ``project_ids`` (a list) instead of ``project_id`` to scope across
    multiple projects (org-scoped span listing).

    The score→project lookup is routed via ``_REGISTRY["ANNOTATION_LABELS"]``:
    V1_ONLY reads PG (Score joins legacy trace/observation_span), V2_ONLY reads
    CH (model_hub_score scoped via spans). See ``annotation_label_source``.
    """
    from django.db.models import Q

    from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

    SourceCls = get_query_builder_class("ANNOTATION_LABELS")  # noqa: N806

    if project_ids is not None:
        score_label_ids = set()
        for pid in project_ids:
            score_label_ids.update(SourceCls().label_ids_for_project(pid))
        owner_q = Q(project_id__in=project_ids)
    else:
        score_label_ids = SourceCls().label_ids_for_project(project_id)
        owner_q = Q(project_id=project_id)

    return AnnotationsLabels.objects.filter(
        owner_q | Q(id__in=score_label_ids),
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
