"""Pure projections shared by Observe evaluation list and detail tiles.

The warehouse readers deliberately keep their authorization, bounds, and
physical-version selection concerns outside this module.  These helpers only
turn already-authorized eval rows into stable display values.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

_NON_COMPLETED_STATUSES = frozenset({"pending", "running", "skipped", "errored"})


def _output_kind(output_type: str | None) -> str:
    return (output_type or "").replace("/", "_").replace(" ", "_").upper()


def _row_status(row: Mapping[str, Any]) -> str:
    return str(row.get("status") or "").strip().lower()


def _is_error(row: Mapping[str, Any]) -> bool:
    return (
        bool(row.get("error"))
        or _row_status(row) == "errored"
        or str(row.get("output_str") or "").upper() == "ERROR"
    )


def _is_completed(row: Mapping[str, Any]) -> bool:
    """Whether a row is eligible as a completed eval result.

    Older rows may not have a status.  Empty / missing status therefore stays
    eligible, while a current lifecycle marker does not become a synthetic
    completed score.
    """

    return not _is_error(row) and _row_status(row) not in _NON_COMPLETED_STATUSES


def _stable_log_id(row: Mapping[str, Any]) -> str:
    for key in ("log_id", "eval_log_id", "id", "grouped_eval_id"):
        value = row.get(key)
        if value is not None:
            return str(value)
    return ""


def _created_at_key(row: Mapping[str, Any]) -> str:
    """Use ISO strings (or a stable string fallback) without mixed-type sorts."""

    value = row.get("created_at")
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _latest(rows: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    materialized = list(rows)
    return max(materialized, key=lambda row: (_created_at_key(row), _stable_log_id(row))) if materialized else None


def select_latest_completed(rows: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Select the newest completed, non-error row by time then stable log id."""

    return _latest(row for row in rows if _is_completed(row))


def normalize_verdict(value: Any) -> str | None:
    """Normalize true/false and Pass/Fail labels without treating rates as bools."""

    if isinstance(value, bool):
        return "pass" if value else "fail"
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"pass", "true"}:
            return "pass"
        if normalized in {"fail", "false"}:
            return "fail"
    return None


def _choice_values(row: Mapping[str, Any]) -> list[str]:
    raw = row.get("output_str_list")
    if raw is None:
        raw = row.get("score_items")
    if raw is None and isinstance(row.get("result"), (list, tuple)):
        raw = row["result"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.lstrip().startswith("[") else []
        except json.JSONDecodeError:
            raw = []
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [str(value) for value in raw if value not in (None, "")]


def union_choice_labels(declared: Iterable[Any] | None, observed: Iterable[Any] | None) -> list[str]:
    """Return declared labels first, then any observed labels in stable order."""

    labels: list[str] = []
    for value in [*(declared or []), *(observed or [])]:
        if value in (None, ""):
            continue
        label = str(value)
        if label not in labels:
            labels.append(label)
    return labels


def _marker(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    materialized = list(rows)
    if not materialized:
        return None
    if any(_is_error(row) for row in materialized):
        return {"error": True}
    for status in ("skipped", "running", "pending"):
        matching = [row for row in materialized if _row_status(row) == status]
        if matching:
            marker: dict[str, Any] = {"status": status}
            if status == "skipped":
                reason = _latest(matching).get("skipped_reason") if _latest(matching) else None
                if reason:
                    marker["skipped_reason"] = reason
            return marker
    return None


def _numeric_value(row: Mapping[str, Any]) -> float | None:
    value = row.get("output_float")
    if value is None and isinstance(row.get("score"), (int, float)):
        # Existing detail arrays carry already-scaled scores.  Raw warehouse
        # rows always provide output_float, so only use score as a fallback.
        return float(row["score"])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return round(float(value) * 100, 2)


def _verdict_value(row: Mapping[str, Any]) -> str | None:
    for key in ("output_bool", "result", "output_str", "score_label"):
        verdict = normalize_verdict(row.get(key))
        if verdict is not None:
            return verdict
    return None


def count_mode_cell(
    rows: Iterable[Mapping[str, Any]],
    output_type: str | None,
    declared_labels: Iterable[Any] | None,
    choices_map: Mapping[str, Any] | None,
) -> dict[str, Any] | float | None:
    """Build an Observe count cell from every completed attempt in *rows*.

    Count cells intentionally differ from the detail rollup: a rerun counts
    here every time, while detail later selects just one completed row per
    physical span.  Lifecycle/error markers are only emitted when no completed
    row is available for the config.
    """

    materialized = list(rows)
    completed = [row for row in materialized if _is_completed(row)]
    if not completed:
        return _marker(materialized)

    kind = _output_kind(output_type)
    if kind == "PASS_FAIL":
        counts = {"pass": 0, "fail": 0}
        for row in completed:
            verdict = _verdict_value(row)
            if verdict:
                counts[verdict] += 1
        return counts

    if kind == "CHOICES":
        observed = [label for row in completed for label in _choice_values(row)]
        labels = union_choice_labels(declared_labels, observed)
        counts = {label: 0 for label in labels}
        for row in completed:
            # A label counts once per completed result, not once per duplicate
            # in output_str_list.
            for label in set(_choice_values(row)):
                counts.setdefault(label, 0)
                counts[label] += 1
        return counts

    values = [value for row in completed if (value := _numeric_value(row)) is not None]
    return round(sum(values) / len(values), 2) if values else None


def _config_value(configs: Mapping[str, Any] | Iterable[Any], config_id: str) -> Any:
    if isinstance(configs, Mapping):
        return configs.get(config_id, {})
    for config in configs:
        value = config.get("id") if isinstance(config, Mapping) else getattr(config, "id", None)
        if str(value) == config_id:
            return config
    return {}


def _config_get(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, Mapping):
        return config.get(key, default)
    return getattr(config, key, default)


def _detail_value(row: Mapping[str, Any], output_type: str | None) -> Any:
    kind = _output_kind(output_type)
    if kind == "PASS_FAIL":
        return _verdict_value(row)
    if kind == "CHOICES":
        return _choice_values(row)
    return _numeric_value(row)


def _choices_map_with_unknowns(
    choices_map: Mapping[str, Any] | None, observed: Iterable[str]
) -> dict[str, Any]:
    result = deepcopy(dict(choices_map or {}))
    for label in observed:
        result.setdefault(label, "neutral")
    return result


def _span_row(
    row: Mapping[str, Any], output_type: str | None, *, error: bool = False
) -> dict[str, Any]:
    span_id = str(row.get("span_id") or row.get("observation_span_id") or "")
    return {
        "span_id": span_id,
        "span_name": row.get("span_name") or row.get("observation_span_name") or span_id,
        "value": None if error else _detail_value(row, output_type),
        "explanation": row.get("eval_explanation") or row.get("explanation") or None,
        "error": error,
        "status": _row_status(row) or None,
        "eval_config_id": str(row.get("eval_config_id") or ""),
    }


def _eval_projection(
    config_id: str,
    configs: Mapping[str, Any] | Iterable[Any],
    selected: list[Mapping[str, Any]],
    fallback_rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    config = _config_value(configs, config_id)
    output_type = _config_get(config, "output_type") or _config_get(config, "output")
    declared = _config_get(config, "choices") or []
    choices_map = _config_get(config, "choices_map") or {}
    successful_rows = [row for row in selected if row is not None]
    span_rows = [_span_row(row, output_type) for row in successful_rows]
    selected_span_ids = {row["span_id"] for row in span_rows}

    # A span with no completed result can still communicate a real execution
    # failure. It must not borrow a previous successful explanation, even when
    # another span in a trace-scope aggregate did complete successfully.
    fallback_by_span: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in fallback_rows:
        fallback_by_span[
            str(row.get("span_id") or row.get("observation_span_id") or "")
        ].append(row)
    for span_id, span_fallback_rows in fallback_by_span.items():
        if not span_id or span_id in selected_span_ids:
            continue
        latest_error = _latest(row for row in span_fallback_rows if _is_error(row))
        if latest_error is not None:
            span_rows.append(_span_row(latest_error, output_type, error=True))
            continue
        latest_lifecycle = _latest(span_fallback_rows)
        if latest_lifecycle is not None:
            span_rows.append(_span_row(latest_lifecycle, output_type))

    observed = [
        label
        for span in span_rows
        for label in (span["value"] if isinstance(span["value"], list) else [])
    ]
    aggregate = count_mode_cell(successful_rows, output_type, declared, choices_map)
    if aggregate is None:
        aggregate = {}
    projection = {
        "eval_config_id": config_id,
        "eval_name": _config_get(config, "name", config_id),
        "output_type": output_type,
        "template_type": _config_get(config, "template_type"),
        "target_type": next(
            (
                str(row.get("target_type"))
                for row in [*successful_rows, *fallback_rows]
                if row.get("target_type") in {"span", "trace"}
            ),
            None,
        ),
        "choices_map": _choices_map_with_unknowns(choices_map, observed),
        "aggregate": aggregate,
        "spans": span_rows,
    }
    if span_rows and all(row["error"] for row in span_rows):
        projection["error"] = True
    return projection


def build_eval_rollup(
    rows: Iterable[Mapping[str, Any]],
    configs: Mapping[str, Any] | Iterable[Any],
    root_span_id: str | None,
) -> dict[str, dict[str, Any]]:
    """Build trace-root and own-span eval rollups from authorized raw rows.

    The first parentless span passed as ``root_span_id`` represents trace scope:
    it includes one latest completed row for every span.  Every other span gets
    only its own latest row.  Missing entries are intentionally left to the
    caller to fill with an empty scoped rollup for spans without eval rows.
    """

    by_span_config: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(
        list
    )
    config_ids: list[str] = []
    for row in rows:
        span_id = str(row.get("span_id") or row.get("observation_span_id") or "")
        config_id = str(row.get("eval_config_id") or "")
        if not span_id or not config_id:
            continue
        by_span_config[(span_id, config_id)].append(row)
        if config_id not in config_ids:
            config_ids.append(config_id)

    # Include config metadata even when the authorized result set has no row;
    # it only affects scopes already represented by eval data.
    span_ids = list(dict.fromkeys(span_id for span_id, _ in by_span_config))
    # The canonical tree root may have no direct eval row while child spans do.
    # It still owns the trace-scoped aggregate.
    if root_span_id and root_span_id not in span_ids:
        span_ids.insert(0, root_span_id)
    result: dict[str, dict[str, Any]] = {}
    for scope_span_id in span_ids:
        scoped_evals = []
        for config_id in config_ids:
            if scope_span_id == root_span_id:
                keys = [
                    (span_id, config_id)
                    for span_id in span_ids
                    if (span_id, config_id) in by_span_config
                ]
            else:
                keys = (
                    [(scope_span_id, config_id)]
                    if (scope_span_id, config_id) in by_span_config
                    else []
                )
            if not keys:
                continue
            selected = [select_latest_completed(by_span_config[key]) for key in keys]
            selected = [row for row in selected if row is not None]
            fallback = [row for key in keys for row in by_span_config[key]]
            scoped_evals.append(_eval_projection(config_id, configs, selected, fallback))
        result[scope_span_id] = {
            "scope": "trace" if scope_span_id == root_span_id else "span",
            "evals": scoped_evals,
        }
    return result
