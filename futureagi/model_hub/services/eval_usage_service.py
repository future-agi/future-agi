"""Eval-usage service — business logic for the Usage tab.

Per coding-standards/03-architecture-and-layers.md: views must be thin
(route + deserialize + delegate). Every Usage-tab response is built here.
The three callers (`EvalUsageStatsView`, `EvalFeedbackListView`,
`GetAPICallLogDetailsView`) hand in already-resolved objects (template,
organization, workspace) and a validated query dict, and receive a dict
that conforms to the result serializer in `model_hub.serializers.contracts`.

Why a service layer:
  - The hand-built response dicts inside the views had drifted from the
    serializer enough times that we briefly wrapped each `success_response`
    in a runtime `validate_response_contract(...)` guard. That guard was
    fail-open in prod, which only delayed the drift bug; the correct fix
    is to keep business logic out of the view and route the output through
    the serializer (`Serializer(instance=result).data`) at the boundary.
  - These same shapes are reachable from background tasks and contract
    tests; pulling the logic out makes those callers symmetric with the
    HTTP path and removes the need to instantiate an `APIView` to test
    the chart aggregator.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import structlog
from django.db.models import Q, QuerySet, TextField
from django.db.models.functions import Cast
from django.utils import timezone

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, Feedback
from tfc.constants.api_calls import APICallStatusChoices

try:
    from ee.usage.models.usage import APICallLog
except ImportError:  # pragma: no cover - OSS build
    APICallLog = None

logger = structlog.get_logger(__name__)


# Period → time window. Used when the request doesn't carry an explicit
# start_date/end_date pair.
_PERIOD_MAP: dict[str, timedelta] = {
    "30m": timedelta(minutes=30),
    "6h": timedelta(hours=6),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "180d": timedelta(days=180),
    "365d": timedelta(days=365),
}

# Period → chart bucket size (minutes). Picked so each period gets ~30 buckets.
_BUCKET_MINUTES: dict[str, int] = {
    "30m": 10,
    "6h": 60,
    "1d": 360,
}
_DEFAULT_BUCKET_MINUTES = 1440  # 1 day

# Mapping keys that aren't user-facing input variables. Used when surfacing
# the eval's actual inputs into the log table.
_NON_INPUT_MAPPING_KEYS = frozenset(
    {
        "call_type",
        "image_urls",
        "input_data_types",
        "config",
        "params",
        "model",
        "choices",
        "multi_choice",
        "mapping",
        "mappings",
        "source",
        "reference_id",
        "is_futureagi_eval",
        "required_keys",
        "error_localizer",
        "kb_id",
        "row_context",
        "result",
    }
)


# ── Internal helpers ─────────────────────────────────────────────────────────


def _parse_config(raw: Any) -> dict:
    """Return `raw` as a dict, parsing if it's a JSON-encoded string.

    Pre-PR #747-migration rows persisted `config` as a double-encoded JSON
    string ({"k": "v"} as `"{\"k\": \"v\"}"`). The migration unwraps every
    such row, but this function exists for the small window between deploy
    and migration completion. Returns `{}` on any failure so a malformed
    config can't break the whole response.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _resolve_date_range(
    period: str,
    start_date: datetime | None,
    end_date: datetime | None,
) -> tuple[datetime, datetime]:
    """Return (start, end) — explicit pair wins, else derive from `period`.

    The query serializer guarantees start/end are either both present or
    both absent (EvalUsageQuerySerializer.validate), so we don't have to
    repeat that symmetry check here.
    """
    if start_date and end_date:
        return start_date, end_date
    end = timezone.now()
    delta = _PERIOD_MAP.get(period, timedelta(days=30))
    return end - delta, end


def _bucket_minutes_for(period: str) -> int:
    return _BUCKET_MINUTES.get(period, _DEFAULT_BUCKET_MINUTES)


def _round_to_bucket(ts: datetime, bucket_minutes: int) -> datetime:
    """Round `ts` down to a bucket boundary.

    Rounding must match between the per-log key computation and the
    zero-fill loop — without that, a log at 14:35 keys to ``14:00`` while
    the zero-fill walks ``0:00 / 6:00 / 12:00 / 18:00`` and no log ever
    matches a bucket.
    """
    if bucket_minutes >= 1440:
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket_minutes >= 60:
        hour_size = bucket_minutes // 60
        rounded_hour = (ts.hour // hour_size) * hour_size
        return ts.replace(hour=rounded_hour, minute=0, second=0, microsecond=0)
    rounded_minute = (ts.minute // bucket_minutes) * bucket_minutes
    return ts.replace(minute=rounded_minute, second=0, microsecond=0)


def _bucket_step(bucket_minutes: int) -> timedelta:
    return (
        timedelta(days=1)
        if bucket_minutes >= 1440
        else timedelta(minutes=bucket_minutes)
    )


def _accumulate_score(
    output: dict,
    bucket_key: str,
    is_composite: bool,
    buckets_scores: dict[str, list[float]],
    buckets_pass: dict[str, int],
    buckets_fail: dict[str, int],
) -> None:
    """Mutate the bucket counters from a single output payload.

    Output shape varies per eval type — numeric (regression), {label, score}
    (choice), bare "Passed"/"Failed" string (pass-fail), or composite
    aggregate_pass.
    """
    score = output.get("output")
    if isinstance(score, (int, float)):
        buckets_scores[bucket_key].append(float(score))
        if is_composite:
            agg_pass = output.get("aggregate_pass")
            if agg_pass is True:
                buckets_pass[bucket_key] += 1
            elif agg_pass is False:
                buckets_fail[bucket_key] += 1
        return
    if isinstance(score, dict):
        numeric = score.get("score")
        if isinstance(numeric, (int, float)):
            buckets_scores[bucket_key].append(float(numeric))
        label = score.get("label", "")
        if label in ("Passed", "Pass"):
            buckets_pass[bucket_key] += 1
        elif label in ("Failed", "Fail"):
            buckets_fail[bucket_key] += 1
        return
    if score in ("Passed", "Pass"):
        buckets_pass[bucket_key] += 1
        buckets_scores[bucket_key].append(1.0)
    elif score in ("Failed", "Fail"):
        buckets_fail[bucket_key] += 1
        buckets_scores[bucket_key].append(0.0)


def _build_chart(
    period_qs: QuerySet,
    period: str,
    start_date: datetime,
    end_date: datetime,
) -> list[dict]:
    """Aggregate logs in `period_qs` into chart buckets across [start, end]."""
    bucket_minutes = _bucket_minutes_for(period)
    buckets_calls: dict[str, int] = defaultdict(int)
    buckets_latency: dict[str, list[float]] = defaultdict(list)
    buckets_scores: dict[str, list[float]] = defaultdict(list)
    buckets_pass: dict[str, int] = defaultdict(int)
    buckets_fail: dict[str, int] = defaultdict(int)

    for log in period_qs.values("created_at", "config", "status"):
        bucket_key = _round_to_bucket(log["created_at"], bucket_minutes).isoformat()
        buckets_calls[bucket_key] += 1

        config = _parse_config(log.get("config"))
        if not config:
            continue

        duration = config.get("duration") or config.get("response_time")
        if duration is not None:
            try:
                buckets_latency[bucket_key].append(float(duration))
            except (TypeError, ValueError):
                pass

        output = config.get("output", {})
        if isinstance(output, dict):
            _accumulate_score(
                output,
                bucket_key,
                bool(config.get("composite")),
                buckets_scores,
                buckets_pass,
                buckets_fail,
            )

    # Zero-fill so the chart shows empty intervals.
    chart: list[dict] = []
    current = _round_to_bucket(start_date, bucket_minutes)
    step = _bucket_step(bucket_minutes)
    while current <= end_date:
        key = current.isoformat()
        latencies = buckets_latency.get(key, [])
        scores = buckets_scores.get(key, [])
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        avg_score = sum(scores) / len(scores) if scores else None
        chart.append(
            {
                "timestamp": key,
                "calls": buckets_calls.get(key, 0),
                "avg_latency_ms": (
                    round(avg_latency * 1000)
                    if avg_latency < 100
                    else round(avg_latency)
                ),
                "avg_score": (
                    round(avg_score, 3) if avg_score is not None else None
                ),
                "pass_count": buckets_pass.get(key, 0),
                "fail_count": buckets_fail.get(key, 0),
            }
        )
        current += step
    return chart


def _summarise_input_value(value: Any) -> str | None:
    """Render a single mapping value for the input-summary string.

    Returns None when the value should be skipped (empty / placeholder /
    not scalar).
    """
    if isinstance(value, (dict, list)) or value is None:
        return None
    val_str = str(value)
    if not val_str or val_str.startswith("There seems to be"):
        return None
    if val_str.startswith("http"):
        lower = val_str.lower()
        return (
            "[image]"
            if any(ext in lower for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"))
            else "[url]"
        )
    return val_str


def _extract_input_vars(mappings: Any) -> dict[str, str]:
    """Pull user-facing input variables out of a config.mappings payload."""
    if not isinstance(mappings, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in mappings.items():
        if key in _NON_INPUT_MAPPING_KEYS:
            continue
        rendered = _summarise_input_value(value)
        if rendered is not None:
            out[key] = rendered
    return out


def _format_input_summary(input_vars: dict, fallback: Any) -> str:
    """First three input variables as a 'k: v, k: v' string."""
    if input_vars:
        return ", ".join(f"{k}: {v}" for k, v in list(input_vars.items())[:3])
    if isinstance(fallback, dict):
        parts = [
            f"{k}: {v}"
            for k, v in fallback.items()
            if v and k not in _NON_INPUT_MAPPING_KEYS
        ]
        return ", ".join(parts[:3])
    if isinstance(fallback, str):
        return fallback
    return ""


def _extract_score_and_result(output_data: Any) -> tuple[float | None, str]:
    """Return (score, result_label) from the eval's output payload.

    Handles all four output shapes the runner emits: numeric, choice dict,
    bare pass/fail string, or empty.
    """
    if not isinstance(output_data, dict):
        return None, ""
    raw = output_data.get("output")
    if isinstance(raw, dict):
        return raw.get("score"), raw.get("label", "")
    if isinstance(raw, (int, float)):
        return raw, ""
    if isinstance(raw, str):
        if raw in ("Passed", "Pass"):
            return 1.0, raw
        if raw in ("Failed", "Fail"):
            return 0.0, raw
        return None, raw
    return None, ""


def _build_log_row(log, template: EvalTemplate, feedback_map: dict) -> dict:
    """Render a single APICallLog into the FE-table cell-object shape."""
    config = _parse_config(getattr(log, "config", None))
    is_composite_log = bool(config.get("composite"))
    output_data = config.get("output", {}) if isinstance(config, dict) else {}
    source = config.get("source", "") if isinstance(config, dict) else ""
    mappings = config.get("mappings", {}) if isinstance(config, dict) else {}
    warnings = output_data.get("warnings") if isinstance(output_data, dict) else None

    input_vars = _extract_input_vars(mappings)
    fallback_input = config.get("input", {}) if isinstance(config, dict) else {}
    input_str = _format_input_summary(input_vars, fallback_input)
    score, result_label = _extract_score_and_result(output_data)

    # Composite logs surface their aggregate pass/fail as the result label.
    if is_composite_log and isinstance(output_data, dict):
        agg_pass = output_data.get("aggregate_pass")
        if agg_pass is True:
            result_label = "Passed"
        elif agg_pass is False:
            result_label = "Failed"

    row: dict[str, Any] = {
        "row_id": str(log.log_id),
        "score": {"cell_value": score},
        "result": {"cell_value": result_label},
        "input": {"cell_value": input_str},
        "reason": {
            "cell_value": (
                output_data.get("reason", "")
                if isinstance(output_data, dict)
                else ""
            )
        },
        "source": {"cell_value": source},
        "version": {
            "cell_value": (
                ""
                if template.owner == OwnerChoices.SYSTEM.value
                else config.get("version_number")
            )
        },
        "feedback": {"cell_value": feedback_map.get(str(log.log_id))},
        "created_at": {
            "cell_value": log.created_at.isoformat() if log.created_at else ""
        },
        "status": {"cell_value": log.status},
        "warnings": {"cell_value": warnings or []},
    }

    # Per-variable cell column so the FE can sort / show / hide each one.
    all_vars = input_vars or (fallback_input if isinstance(fallback_input, dict) else {})
    for var_key, var_val in all_vars.items():
        row[f"input_var_{var_key}"] = {"cell_value": var_val}

    detail = {
        "input_variables": all_vars,
        "output": output_data,
        "warnings": warnings or [],
        "mappings": mappings if isinstance(mappings, dict) else {},
        "model": config.get("model"),
        "version_id": config.get("version_id"),
        "version_number": config.get("version_number"),
    }
    if is_composite_log:
        detail.update(
            {
                "children": config.get("children", []),
                "aggregation_function": config.get("aggregation_function"),
                "total_children": config.get("total_children"),
                "completed_children": config.get("completed_children"),
                "failed_children": config.get("failed_children"),
            }
        )
        row["composite"] = True
        row["aggregate_pass"] = (
            output_data.get("aggregate_pass")
            if isinstance(output_data, dict)
            else None
        )

    row["detail"] = detail
    return row


def _fetch_feedback_map(log_ids: list[str], organization) -> dict[str, dict]:
    """Latest feedback per log_id for the given organization."""
    if not log_ids:
        return {}
    feedback_map: dict[str, dict] = {}
    feedbacks = (
        Feedback.objects.filter(
            source_id__in=log_ids, organization=organization, deleted=False
        )
        .order_by("-created_at")
    )
    for fb in feedbacks:
        if fb.source_id not in feedback_map:
            feedback_map[fb.source_id] = {
                "id": str(fb.id),
                "value": fb.value,
                "explanation": fb.explanation or "",
                "action_type": fb.action_type or "",
                "created_at": fb.created_at.isoformat() if fb.created_at else "",
                "user": fb.user.email if fb.user else "",
            }
    return feedback_map


def _build_feedback_item(fb: Feedback) -> dict:
    user_name = ""
    if fb.user:
        user_name = getattr(fb.user, "name", "") or fb.user.email
    return {
        "id": str(fb.id),
        "value": str(fb.value),
        "explanation": fb.explanation or "",
        "source": fb.source or "",
        "source_id": fb.source_id or "",
        "action_type": fb.action_type or "",
        "user_name": user_name,
        "created_at": fb.created_at.isoformat() if fb.created_at else "",
    }


def _scoped_log_queryset(template_id: str, organization, workspace) -> QuerySet:
    """APICallLog queryset filtered to the active workspace.

    Caller MUST have already authorised access to `template_id`; this
    function only handles the workspace scope, not the org/user check.
    """
    qs = APICallLog.objects.filter(
        organization=organization,
        source_id=str(template_id),
        deleted=False,
    )
    if workspace:
        qs = qs.filter(workspace=workspace)
    return qs


# ── Public service entry points ──────────────────────────────────────────────


def empty_eval_usage_stats(template_id: str) -> dict:
    """Empty response shape (OSS build, no usage app)."""
    return {
        "template_id": str(template_id),
        "is_composite": False,
        "stats": {
            "total_runs": 0,
            "runs_period": 0,
            "success_count": 0,
            "error_count": 0,
            "pass_rate": 0.0,
        },
        "chart": [],
        "table": [],
        "logs": {"total": 0, "page": 0, "page_size": 25},
    }


def empty_eval_feedback_list(template_id: str) -> dict:
    """Empty response shape (OSS build, no usage app)."""
    return {
        "template_id": str(template_id),
        "items": [],
        "total": 0,
        "page": 0,
        "page_size": 25,
    }


def empty_api_call_log_details(column_config: list | None = None) -> dict:
    """Empty response shape (OSS build, or no logs found)."""
    return {"table": [], "column_config": column_config or []}


def compute_eval_usage_stats(
    *,
    template: EvalTemplate,
    organization,
    workspace,
    query: dict,
) -> dict:
    """Top-level entry: build the full Usage-tab payload for one template.

    Shape matches `EvalUsageStatsResponseResultSerializer`. The view runs
    the result through that serializer (`.data`) before returning it; this
    function does not concern itself with the wire format beyond producing
    a dict that the serializer can render.
    """
    if APICallLog is None:
        return empty_eval_usage_stats(str(template.id))

    page = query["page"]
    page_size = query["page_size"]
    period = query["period"]
    start_date, end_date = _resolve_date_range(
        period, query.get("start_date"), query.get("end_date")
    )

    base_qs = _scoped_log_queryset(template.id, organization, workspace)
    period_qs = base_qs.filter(created_at__gte=start_date, created_at__lte=end_date)

    total_runs = base_qs.count()
    runs_period = period_qs.count()
    success_count = period_qs.filter(status=APICallStatusChoices.SUCCESS.value).count()
    error_count = period_qs.filter(status=APICallStatusChoices.ERROR.value).count()

    chart = _build_chart(period_qs, period, start_date, end_date) if runs_period else []

    logs_qs = period_qs.order_by("-created_at")
    total_logs = logs_qs.count()
    logs_page = list(logs_qs[page * page_size : (page + 1) * page_size])

    feedback_map = _fetch_feedback_map(
        [str(log.log_id) for log in logs_page], organization
    )
    table = [_build_log_row(log, template, feedback_map) for log in logs_page]

    return {
        "template_id": str(template.id),
        "is_composite": template.template_type == "composite",
        "stats": {
            "total_runs": total_runs,
            "runs_period": runs_period,
            "success_count": success_count,
            "error_count": error_count,
            "pass_rate": round(
                (success_count / runs_period * 100) if runs_period else 0, 2
            ),
        },
        "chart": chart,
        "table": table,
        "logs": {"total": total_logs, "page": page, "page_size": page_size},
    }


def compute_api_call_log_details(
    *,
    eval_template: EvalTemplate,
    organization,
    user,
    query: dict,
) -> dict:
    """Logs table for the eval-playground / feedback drawer.

    Caller has already authorised `eval_template`. We need `user` here only
    because the per-user column-config is stored on `EvalSettings(user=user)`.

    Existing module-level helpers do the heavy lifting:
      - `get_column_data` (column config + per-user persistence)
      - `populate_log_row_data` (per-row construction, threaded by the view
        caller; left where it is to minimise diff)
      - `apply_created_at_filters`, `apply_filters`, `apply_search`
    """
    from concurrent.futures import ThreadPoolExecutor

    # Local imports keep the service free of module-level coupling to the
    # views file. These helpers are extraction targets for a follow-up PR;
    # this PR only relocates the view-level orchestration.
    from model_hub.views.separate_evals import (  # noqa: PLC0415
        apply_created_at_filters,
        apply_filters,
        apply_search,
        batch_queryset,
        get_column_data,
        populate_log_row_data,
    )
    from tfc.telemetry import wrap_for_thread  # noqa: PLC0415

    if APICallLog is None:
        return empty_api_call_log_details()

    eval_template_id = str(eval_template.id)
    page_size = query["page_size"]
    current_page = query["current_page_index"]
    source = query["source"]
    search = query["search"]
    sort_config = query["sort"]
    filters = query["filters"]

    logs = APICallLog.objects.filter(
        source_id=eval_template_id,
        organization=organization,
        status__in=[
            APICallStatusChoices.SUCCESS.value,
            APICallStatusChoices.ERROR.value,
        ],
        deleted=False,
    ).order_by("-created_at")
    if source in ("feedback", "eval_playground"):
        logs = logs.filter(source=source)

    column_data = get_column_data(eval_template_id, source, user)

    if filters:
        logs, new_filters = apply_created_at_filters(logs, filters)
    else:
        new_filters = []

    if not logs.exists():
        return empty_api_call_log_details(column_data)

    key_map = {col.get("id"): col.get("name") for col in column_data}
    wrapped_populate = wrap_for_thread(populate_log_row_data)
    row_data: list = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        # Preserve original batch order — iterate `futures` directly rather
        # than `as_completed`, which yields in completion order.
        futures = [
            executor.submit(wrapped_populate, eval_template, batch, key_map)
            for batch in batch_queryset(logs, 10)
        ]
        for future in futures:
            row_data.extend(future.result())

    if new_filters:
        row_data = apply_filters(row_data, new_filters)

    if sort_config and row_data:
        for sort_item in sort_config:
            col_id = sort_item.get("column_id")
            reverse = sort_item.get("type") == "descending"
            if not col_id:
                continue

            def _sort_key(item, _col_id=col_id):
                try:
                    value = item.get(_col_id, {}).get("cell_value", "")
                    if not isinstance(value, str):
                        value = str(value)
                    return value.lower() if isinstance(value, str) else (value or 0)
                except (AttributeError, TypeError):
                    return ""

            row_data.sort(key=_sort_key, reverse=reverse)

    if search:
        row_data = apply_search(row_data, search, column_data)

    total_rows = len(row_data) if row_data else 0
    start = current_page * page_size
    end = start + page_size
    return {
        "table": row_data[start:end] if row_data else [],
        "column_config": column_data,
        "metadata": {
            "total_rows": total_rows,
            "total_pages": (total_rows + page_size - 1) // page_size if page_size else 0,
        },
    }


def compute_eval_feedback_list(
    *,
    template_id: str,
    organization,
    workspace,
    query: dict,
) -> dict:
    """Paginated feedback list across direct-template + log-attached rows.

    Caller has already authorised access to `template_id` (so we can take a
    UUID/str here rather than the template object — there's no per-template
    field needed downstream other than its id).
    """
    if APICallLog is None:
        return empty_eval_feedback_list(str(template_id))

    page = query["page"]
    page_size = query["page_size"]

    log_id_qs = (
        APICallLog.objects.filter(
            source_id=str(template_id),
            organization=organization,
            deleted=False,
        )
        .annotate(log_id_str=Cast("log_id", TextField()))
        .values("log_id_str")
    )
    if workspace:
        log_id_qs = log_id_qs.filter(workspace=workspace)

    base_qs = (
        Feedback.objects.filter(organization=organization, deleted=False)
        .filter(Q(eval_template_id=template_id) | Q(source_id__in=log_id_qs))
        .select_related("user")
        .order_by("-created_at")
    )

    total = base_qs.count()
    items = [
        _build_feedback_item(fb)
        for fb in base_qs[page * page_size : (page + 1) * page_size]
    ]
    return {
        "template_id": str(template_id),
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
