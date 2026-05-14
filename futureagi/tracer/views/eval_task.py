import json
import traceback
import uuid as uuid_module
from collections import defaultdict
from datetime import datetime, timedelta
from random import sample

import structlog
from django.db import models, transaction
from django.db.models import Count, F, Func, Max, Q, Value
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet


class _RegexpReplace(Func):
    """
    PostgreSQL `regexp_replace(string, pattern, replacement, flags)`.

    Used by get_eval_task_logs to normalize raw error strings inside the
    database so we can GROUP BY a canonical form and collapse thousands of
    near-duplicate errors (which only differ by span UUID) into a small
    set of distinct error groups.

    `output_field` is set explicitly because Django can't infer the
    result type when mixing a TextField source (`eval_explanation`) with
    Value() literal CharFields — it raises "Expression contains mixed
    types: TextField, CharField" otherwise.
    """

    function = "regexp_replace"
    arity = 4
    output_field = models.TextField()


# Re-exported for back-compat; canonical definition lives in `tracer.utils.eval`.
from tracer.utils.eval import _walk_dotted_path  # noqa: E402, F401


# Per-variable size cap to keep the panel payload bounded — a single
# log row that maps a giant JSON document into the eval would otherwise
# bloat the response. 8KB per variable is enough for typical
# prompts/messages while protecting against pathological inputs.
_INPUT_VAR_MAX_BYTES = 8 * 1024


def _resolve_input_variables(custom_eval_config, obs_span):
    """
    Resolve the eval mapping against the span to produce a
    `{var_name: value}` dict for the side panel's "Input Variables"
    section. Values can be strings, numbers, dicts, or lists — the
    frontend renders them through JsonValueTree so nested objects are
    browsable in the same way as the trace detail drawer.
    """
    if not custom_eval_config or not obs_span:
        return {}
    mapping = custom_eval_config.mapping or {}
    if not isinstance(mapping, dict):
        return {}
    span_attrs = obs_span.span_attributes or {}
    resolved = {}
    for var_name, field_path in mapping.items():
        if not field_path:
            continue
        value = _walk_dotted_path(span_attrs, field_path)
        # Soft-flatten fallback — mirror the frontend behavior in
        # `TaskLivePreview.resolveMapping`: if the user mapped to a bare
        # name like "input" but the actual data is nested under
        # `span_attributes.input`, the SDK convention is to expose both.
        # On the backend our `span_attrs` IS the span_attributes dict,
        # so the bare name lookup already works — no extra step needed.
        if value is None:
            continue
        # Cap per-variable size — drop the value entirely if it's huge
        # rather than truncating into invalid JSON.
        try:
            serialized_size = len(json.dumps(value, default=str))
            if serialized_size > _INPUT_VAR_MAX_BYTES:
                resolved[var_name] = (
                    f"[truncated — {serialized_size:,} bytes, exceeds "
                    f"{_INPUT_VAR_MAX_BYTES:,} byte limit]"
                )
                continue
        except (TypeError, ValueError):
            # Non-serializable value (rare for span data); just stringify.
            resolved[var_name] = str(value)[:_INPUT_VAR_MAX_BYTES]
            continue
        resolved[var_name] = value
    return resolved


logger = structlog.get_logger(__name__)
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.serializers.eval_task import (
    EditEvalTaskSerializer,
    EvalTaskSerializer,
    PaginationQuerySerializer,
)
from tracer.utils.eval_tasks import parsing_evaltask_filters, run_for_processed_spans
from tracer.utils.filters import FilterEngine
from tracer.utils.helper import get_default_eval_task_config


class EvalTaskView(BaseModelViewSetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = EvalTaskSerializer

    def get_queryset(self):
        eval_task_id = self.kwargs.get("pk")
        organization_id = (
            getattr(self.request, "organization", None)
            or self.request.user.organization
        ).id

        # Get base queryset with automatic filtering from mixin
        queryset = (
            super()
            .get_queryset()
            .filter(project__organization_id=organization_id, project__deleted=False)
        )
        queryset = queryset.select_related("project")
        queryset = queryset.prefetch_related("evals")

        if eval_task_id:
            queryset = queryset.filter(id=eval_task_id)

        project_id = self.request.query_params.get(
            "project_id"
        ) or self.request.query_params.get("projectId")
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        search_name = self.request.query_params.get("name")
        if search_name:
            queryset = queryset.filter(name__icontains=search_name)

        return queryset

    def create(self, request, *args, **kwargs):
        try:
            data = request.data
            data["status"] = EvalTaskStatus.PENDING
            filters = data.get("filters", {})
            project_id = data.get("project")
            if project_id:
                filters["project_id"] = project_id
            data["filters"] = filters

            data["last_run"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            eval_task = serializer.save()

            return self._gm.success_response({"id": eval_task.id})

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"])
    def list_eval_tasks(self, request, *args, **kwargs):
        """
        List Eval Tasks filtered
        """
        try:
            queryset = self.get_queryset()
            serializer = self.get_serializer(queryset, many=True)
            eval_tasks = serializer.data

            # Collect all eval IDs to batch query CustomEvalConfig (avoids N+1)
            all_eval_ids = set()
            for eval_task in eval_tasks:
                all_eval_ids.update(eval_task.get("evals", []))

            # Single query to fetch all CustomEvalConfigs
            eval_configs = CustomEvalConfig.objects.filter(
                id__in=all_eval_ids, deleted=False
            ).values("id", "name")
            eval_name_lookup = {str(ec["id"]): ec["name"] for ec in eval_configs}

            result = []

            for eval_task in eval_tasks:
                eval_ids = eval_task.get("evals", [])
                if not eval_ids:
                    continue

                # Use the lookup instead of querying in loop
                eval_names = [
                    eval_name_lookup.get(str(eval_id))
                    for eval_id in eval_ids
                    if str(eval_id) in eval_name_lookup
                ]

                parsed_data = {
                    "id": str(eval_task["id"]),
                    "name": eval_task["name"],
                    "status": eval_task["status"],
                    "filters_applied": eval_task["filters"],
                    "created_at": eval_task["created_at"],
                    "evals_applied": eval_names,
                    "sampling_rate": eval_task["sampling_rate"],
                    "last_run": eval_task["last_run"],
                }
                result.append(parsed_data)

            filters = self.request.data.get("filters", [])
            if filters:
                filter_engine = FilterEngine(result)
                result = filter_engine.apply_filters(filters)

            sort_params = self.request.data.get("sort_params", [])
            if sort_params:
                for sort_param in reversed(sort_params):
                    sort_key = sort_param.get("column_id")
                    sort_direction = sort_param.get("direction", "asc")
                    reverse = sort_direction == "desc"

                    def sort_key_func(x):
                        value = x.get(sort_key)  # noqa: B023
                        return (value is None, value)

                    result.sort(key=sort_key_func, reverse=reverse)

            total_rows = len(result)
            page_number = self.request.query_params.get("page_number", 0)
            page_size = self.request.query_params.get("page_size", 30)
            start = int(page_number) * int(page_size)
            end = start + int(page_size)
            result = result[start:end]

            # Update config to include project name
            config = get_default_eval_task_config()

            response = {
                "metadata": {
                    "total_rows": total_rows,
                },
                "table": result,
                "config": config,
            }

            return self._gm.success_response(response)

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(f"error fetching the eval tasks list {str(e)}")

    # Maximum number of distinct error groups returned per task. Most tasks
    # produce 1-5 distinct error types; this cap is a safety net for tasks
    # with many varied custom-eval failures and keeps the payload bounded.
    _ERROR_GROUPS_LIMIT = 50

    @action(detail=False, methods=["get"])
    def get_eval_task_logs(self, request, *args, **kwargs):
        try:
            eval_task_id = self.request.query_params.get("eval_task_id")
            eval_task = EvalTask.objects.get(
                id=eval_task_id,
                project__organization=getattr(self.request, "organization", None)
                or self.request.user.organization,
            )

            # Pass/fail counts — cheap aggregate, two indexed COUNTs.
            counts = EvalLogger.objects.filter(eval_task_id=eval_task_id).aggregate(
                errors_count=Count("id", filter=Q(error=True)),
                success_count=Count("id", filter=Q(error=False)),
            )

            # ── Pre-aggregate error groups in SQL ──
            #
            # Previously this endpoint returned a raw ArrayAgg of every
            # error string — for tasks with thousands of failures that's
            # multi-MB of payload, slow to serialize, and forced the
            # frontend to walk every string just to count duplicates.
            #
            # Instead we normalize each error in the DB (strip the
            # uniform "Error during evaluation: " prefix and the trailing
            # " for span <uuid>" so duplicates collapse), GROUP BY the
            # normalized form, and return one row per distinct error type
            # with a count and one sample. The payload becomes ~100 bytes
            # per group instead of ~200 bytes per error row.
            #
            # The frontend's classifier (classifyTaskError.js) does a
            # second pattern-match pass on the sample to attach a title,
            # icon, severity, and "How to fix" hints. The normalization
            # rules here are kept in sync with that classifier — see
            # core-frontend/src/sections/common/EvalsTasks/classifyTaskError.js
            normalized_expr = _RegexpReplace(
                _RegexpReplace(
                    F("eval_explanation"),
                    Value(r"^Error during evaluation:\s*"),
                    Value(""),
                    Value(""),
                ),
                Value(r" for span [a-f0-9-]+$"),
                Value(""),
                Value(""),
            )

            error_groups_qs = (
                EvalLogger.objects.filter(eval_task_id=eval_task_id, error=True)
                .annotate(normalized=normalized_expr)
                .values("normalized")
                .annotate(
                    count=Count("id"),
                    # Max() picks one representative explanation per group
                    # without a window function — cheap and deterministic.
                    sample=Max("eval_explanation"),
                )
                .order_by("-count")[: self._ERROR_GROUPS_LIMIT]
            )

            error_groups = [
                {
                    "normalized": row["normalized"] or "Unknown error",
                    "count": row["count"],
                    "sample": row["sample"] or "",
                }
                for row in error_groups_qs
            ]

            total_count = counts["errors_count"] + counts["success_count"]

            result = {
                "start_time": eval_task.start_time,
                "end_time": eval_task.end_time,
                "errors_count": counts["errors_count"],
                "success_count": counts["success_count"],
                "total_count": total_count,
                "error_groups": error_groups,
                # Indicates whether we capped at _ERROR_GROUPS_LIMIT — the
                # frontend can show a "showing top 50 error types" hint.
                "error_groups_truncated": len(error_groups) == self._ERROR_GROUPS_LIMIT,
                "row_type": eval_task.row_type,
            }

            return self._gm.success_response(result)

        except EvalTask.DoesNotExist:
            return self._gm.bad_request(f"EvalTask with id {eval_task_id} not found.")

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(str(e))

    # ──────────────────────────────────────────────────────────────────
    # GET /tracer/eval-task/get_usage/?eval_task_id=<id>&period=<>&...
    #
    # Replaces the old "stat cards + config snapshot" Usage tab with the
    # eval-style usage view: a top stats row, a time-series chart, and a
    # paginated logs table that opens a side panel on click. Mirrors the
    # response shape of `EvalUsageStatsView` so the frontend can reuse
    # `UsageChart`, `DataTable`, and `DataTablePagination` directly.
    # ──────────────────────────────────────────────────────────────────
    _USAGE_PERIOD_MAP = {
        "30m": timedelta(minutes=30),
        "6h": timedelta(hours=6),
        "1d": timedelta(days=1),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "90d": timedelta(days=90),
        "180d": timedelta(days=180),
        "365d": timedelta(days=365),
    }

    @action(detail=False, methods=["get"])
    def get_usage(self, request, *args, **kwargs):
        try:
            eval_task_id = self.request.query_params.get("eval_task_id")
            if not eval_task_id:
                return self._gm.bad_request("eval_task_id is required")

            organization = (
                getattr(self.request, "organization", None)
                or self.request.user.organization
            )

            try:
                eval_task = EvalTask.objects.get(
                    id=eval_task_id, project__organization=organization
                )
            except EvalTask.DoesNotExist:
                return self._gm.bad_request(
                    f"EvalTask with id {eval_task_id} not found."
                )

            # ── Query params ──
            qp = PaginationQuerySerializer(data=self.request.query_params)
            qp.is_valid(raise_exception=True)
            page_size = qp.validated_data["page_size"]
            period = self.request.query_params.get("period", "30d")
            # Optional eval filter — tasks may run multiple evals; the UI
            # passes this when the user picks one from the dropdown.
            eval_id_filter = self.request.query_params.get("eval_id")

            period_delta = self._USAGE_PERIOD_MAP.get(period, timedelta(days=30))
            end_date = timezone.now()
            start_date = end_date - period_delta

            # ── Configured evals on this task (drives the filter dropdown) ──
            # Each EvalLogger row links to a CustomEvalConfig, which links
            # to an EvalTemplate that carries `output_type_normalized`
            # ("pass_fail" | "percentage" | "deterministic"). We follow
            # the FK chain in a single query via .values() join syntax.
            configured_eval_configs = list(
                CustomEvalConfig.objects.filter(eval_loggers__eval_task_id=eval_task_id)
                .distinct()
                .values(
                    "id",
                    "name",
                    "model",
                    "eval_template_id",
                    "eval_template__output_type_normalized",
                )
            )
            evals_meta = [
                {
                    "id": str(c["id"]),
                    "name": c.get("name") or "Evaluation",
                    "output_type": c.get("eval_template__output_type_normalized")
                    or "pass_fail",
                    "template_id": (
                        str(c["eval_template_id"])
                        if c.get("eval_template_id")
                        else None
                    ),
                    "model": c.get("model"),
                }
                for c in configured_eval_configs
            ]

            # ── Base queryset ──
            # Match the existing get_eval_task_logs filter exactly so any
            # task that shows logs also shows usage. We do NOT filter by
            # `deleted` here because get_eval_task_logs doesn't either —
            # the two endpoints must agree on what counts as a "run".
            base_qs = EvalLogger.objects.filter(eval_task_id=str(eval_task_id))
            if eval_id_filter:
                base_qs = base_qs.filter(custom_eval_config_id=eval_id_filter)

            total_runs = base_qs.count()
            period_qs = base_qs.filter(created_at__gte=start_date)
            runs_period = period_qs.count()

            # Fallback: if the user picked a period that excludes every
            # run but the task DOES have runs, widen the window to "all
            # time" so they aren't staring at an empty chart. The
            # `period_used` field tells the frontend which period was
            # actually applied so it can surface a hint.
            period_used = period
            if runs_period == 0 and total_runs > 0:
                period_qs = base_qs
                runs_period = total_runs
                period_used = "all"
                # Recompute start_date to the earliest run so the
                # zero-fill chart loop covers the right range.
                earliest = (
                    base_qs.order_by("created_at")
                    .values_list("created_at", flat=True)
                    .first()
                )
                if earliest:
                    start_date = earliest

            success_count = period_qs.filter(error=False).count()
            error_count = period_qs.filter(error=True).count()
            pass_rate = (
                round((success_count / runs_period * 100), 2) if runs_period > 0 else 0
            )

            # ── Chart data — bucket by period and aggregate ──
            chart_data = []
            if runs_period > 0:
                # Bucket size: minutes for short periods, days for long.
                if period == "30m":
                    bucket_minutes = 5
                elif period == "6h":
                    bucket_minutes = 30
                elif period == "1d":
                    bucket_minutes = 60
                elif period == "7d":
                    bucket_minutes = 360  # 6h
                else:
                    bucket_minutes = 1440  # 1 day

                buckets_calls = defaultdict(int)
                buckets_pass = defaultdict(int)
                buckets_fail = defaultdict(int)
                buckets_scores = defaultdict(list)

                for log in period_qs.values(
                    "created_at", "error", "output_bool", "output_float"
                ):
                    ts = log["created_at"]
                    # Round down to bucket boundary
                    if bucket_minutes >= 1440:
                        bucket_ts = ts.replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                    elif bucket_minutes >= 60:
                        hours_per_bucket = bucket_minutes // 60
                        bucket_ts = ts.replace(
                            hour=(ts.hour // hours_per_bucket) * hours_per_bucket,
                            minute=0,
                            second=0,
                            microsecond=0,
                        )
                    else:
                        bucket_ts = ts.replace(
                            minute=(ts.minute // bucket_minutes) * bucket_minutes,
                            second=0,
                            microsecond=0,
                        )
                    bucket_key = bucket_ts.isoformat()
                    buckets_calls[bucket_key] += 1

                    if log["error"]:
                        buckets_fail[bucket_key] += 1
                    else:
                        bool_val = log["output_bool"]
                        float_val = log["output_float"]
                        if bool_val is True:
                            buckets_pass[bucket_key] += 1
                            buckets_scores[bucket_key].append(1.0)
                        elif bool_val is False:
                            buckets_fail[bucket_key] += 1
                            buckets_scores[bucket_key].append(0.0)
                        if float_val is not None:
                            buckets_scores[bucket_key].append(float(float_val))

                # Zero-fill all buckets in the range so the chart line is
                # continuous instead of skipping empty days.
                if bucket_minutes >= 1440:
                    current_bucket = start_date.replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                else:
                    current_bucket = start_date.replace(
                        minute=(start_date.minute // bucket_minutes) * bucket_minutes,
                        second=0,
                        microsecond=0,
                    )
                while current_bucket <= end_date:
                    key = current_bucket.isoformat()
                    scores = buckets_scores.get(key, [])
                    avg_score = sum(scores) / len(scores) if scores else None
                    chart_data.append(
                        {
                            "timestamp": key,
                            "calls": buckets_calls.get(key, 0),
                            "pass_count": buckets_pass.get(key, 0),
                            "fail_count": buckets_fail.get(key, 0),
                            "avg_score": (
                                round(avg_score, 3) if avg_score is not None else None
                            ),
                            "avg_latency_ms": 0,  # not tracked at logger level
                        }
                    )
                    current_bucket += timedelta(minutes=bucket_minutes)

            # ── Paginated logs ──
            # Eager-load the related ObservationSpan + CustomEvalConfig
            # (and through that, the EvalTemplate for output_type) in a
            # single query — without this we'd hit N+1 inside the loop.
            # PR3: also eager-load trace_session so session-target rows can
            # surface session_id / session_name without an extra query.
            logs_qs = period_qs.select_related(
                "observation_span",
                "custom_eval_config",
                "custom_eval_config__eval_template",
                "trace_session",
            ).order_by("-created_at")

            paginator = ExtendedPageNumberPagination()
            paginator.page_size = page_size
            logs_page = paginator.paginate_queryset(
                logs_qs, self.request, view=self
            )

            log_items = []
            for log in logs_page:
                # Derive a Pass/Fail label and a normalized 0-1 score from
                # the typed output columns. EvalLogger splits output across
                # output_bool / output_float / output_str depending on the
                # eval template's output type — see the model definition.
                if log.error:
                    result_label = "Error"
                    score = None
                    status = "error"
                elif log.output_bool is True:
                    result_label = "Passed"
                    score = 1.0
                    status = "success"
                elif log.output_bool is False:
                    result_label = "Failed"
                    score = 0.0
                    status = "success"
                elif log.output_float is not None:
                    score = float(log.output_float)
                    result_label = "Passed" if score >= 0.5 else "Failed"
                    status = "success"
                elif log.output_str:
                    result_label = log.output_str[:50]
                    score = None
                    status = "success"
                else:
                    result_label = ""
                    score = None
                    status = "success"

                obs_span = log.observation_span
                trace_session = log.trace_session
                config = log.custom_eval_config
                target_type = log.target_type

                # Build a short input summary. Span-target and trace-target
                # rows both have an observation_span (trace target = root
                # span); session-target rows fall back to the session name.
                input_str = ""
                if obs_span:
                    span_attrs = obs_span.span_attributes or {}
                    input_val = (
                        span_attrs.get("input")
                        or span_attrs.get("input.value")
                        or obs_span.name
                        or ""
                    )
                    if isinstance(input_val, dict):
                        input_str = json.dumps(input_val)[:200]
                    else:
                        input_str = str(input_val)[:200]
                elif trace_session:
                    input_str = (trace_session.name or "")[:200]

                reason = log.eval_explanation or log.error_message or ""

                log_items.append(
                    {
                        "id": str(log.id),
                        "input": input_str,
                        "result": result_label,
                        "score": score,
                        "reason": reason,
                        "status": status,
                        "source": "eval_task",
                        "created_at": (
                            log.created_at.isoformat() if log.created_at else ""
                        ),
                        # Cross-references for the side panel — let users
                        # jump back to the source span/trace/session in the
                        # observe page. Span and trace rows expose span/trace
                        # IDs (trace target = root span); session rows expose
                        # session_id with both other IDs NULL.
                        "span_id": str(obs_span.id) if obs_span else None,
                        "trace_id": (
                            str(obs_span.trace_id)
                            if obs_span and obs_span.trace_id
                            else None
                        ),
                        "session_id": (
                            str(trace_session.id) if trace_session else None
                        ),
                        "eval_id": str(config.id) if config else None,
                        "eval_name": config.name if config else None,
                        "model": config.model if config else None,
                        "detail": {
                            "eval_name": config.name if config else None,
                            "model": config.model if config else None,
                            "output_type": (
                                config.eval_template.output_type_normalized
                                if config and config.eval_template
                                else None
                            ),
                            # PR3: target_type lets the FE side panel switch
                            # labels per row (Span ID vs Session ID etc.)
                            # without having to look up the parent EvalTask.
                            "target_type": target_type,
                            "span_name": obs_span.name if obs_span else None,
                            "span_id": str(obs_span.id) if obs_span else None,
                            "trace_id": (
                                str(obs_span.trace_id)
                                if obs_span and obs_span.trace_id
                                else None
                            ),
                            "session_id": (
                                str(trace_session.id) if trace_session else None
                            ),
                            "session_name": (
                                trace_session.name if trace_session else None
                            ),
                            "output_bool": log.output_bool,
                            "output_float": log.output_float,
                            "output_str": log.output_str,
                            "results_explanation": log.results_explanation,
                            "error_message": log.error_message,
                            # Eval mapping resolved against the span — the
                            # side panel renders these as {var: value}
                            # rows with JsonValueTree for object values.
                            "input_variables": _resolve_input_variables(
                                config, obs_span
                            ),
                        },
                    }
                )

            response = {
                "eval_task_id": str(eval_task_id),
                "stats": {
                    "total_runs": total_runs,
                    "runs_period": runs_period,
                    "success_count": success_count,
                    "error_count": error_count,
                    "pass_rate": pass_rate,
                },
                "evals": evals_meta,
                "chart": chart_data,
                # Paginator native shape (matches eval_logs):
                # {count, next, previous, results, total_pages, current_page}
                "logs": paginator.get_paginated_response(log_items).data,
                # Echo back the period actually used. If the user picked
                # "30D" but the task only has older runs, this will be
                # "all" — the frontend can show a hint explaining why.
                "period_requested": period,
                "period_used": period_used,
            }
            return self._gm.success_response(response)

        except Exception as e:
            traceback.print_exc()
            logger.error(
                "eval_task.get_usage failed",
                error=str(e),
                eval_task_id=request.query_params.get("eval_task_id"),
            )
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["post"])
    def mark_eval_tasks_deleted(self, request, *args, **kwargs):
        try:
            eval_task_ids = self.request.data.get("eval_task_ids", [])
            if not eval_task_ids:
                return self._gm.bad_request("No eval task IDs provided")

            if not isinstance(eval_task_ids, list):
                return self._gm.bad_request("eval_task_ids must be a list")

            for eid in eval_task_ids:
                try:
                    uuid_module.UUID(str(eid))
                except (ValueError, AttributeError):
                    return self._gm.bad_request(f"Invalid UUID: {eid}")

            eval_tasks = EvalTask.objects.filter(
                id__in=eval_task_ids,
                project__organization=getattr(request, "organization", None)
                or request.user.organization,
            )
            if not eval_tasks.exists():
                return self._gm.bad_request("No eval tasks found for the provided IDs")

            running_tasks = eval_tasks.filter(status=EvalTaskStatus.RUNNING)
            if running_tasks.exists():
                return self._gm.bad_request(
                    "Cannot delete running eval tasks. Pause them first."
                )

            eval_tasks.update(
                deleted=True, deleted_at=timezone.now(), status=EvalTaskStatus.DELETED
            )

            EvalTaskLogger.objects.filter(eval_task_id__in=eval_task_ids).update(
                deleted=True, deleted_at=timezone.now()
            )
            EvalLogger.objects.filter(eval_task_id__in=eval_task_ids).update(
                deleted=True, deleted_at=timezone.now()
            )

            return self._gm.success_response(
                {"message": "Eval tasks marked as deleted successfully"}
            )

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["post"])
    def pause_eval_task(self, request, *args, **kwargs):
        try:
            eval_task_id = self.request.query_params.get("eval_task_id")
            if not eval_task_id:
                return self._gm.bad_request("Eval task ID is required")

            try:
                eval_task = EvalTask.objects.get(
                    id=eval_task_id,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except EvalTask.DoesNotExist:
                return self._gm.bad_request("Eval task not found")

            if eval_task.status != EvalTaskStatus.RUNNING:
                return self._gm.bad_request(
                    f"Cannot pause eval task with status '{eval_task.status}'. "
                    "Only running tasks can be paused."
                )

            eval_task.status = EvalTaskStatus.PAUSED
            eval_task.save()

            return self._gm.success_response(
                {"message": "Eval task paused successfully"}
            )

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["post"])
    def unpause_eval_task(self, request, *args, **kwargs):
        try:
            eval_task_id = self.request.query_params.get("eval_task_id")
            if not eval_task_id:
                return self._gm.bad_request("Eval task ID is required")

            try:
                eval_task = EvalTask.objects.get(
                    id=eval_task_id,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except EvalTask.DoesNotExist:
                return self._gm.bad_request("Eval task not found")

            if eval_task.status != EvalTaskStatus.PAUSED:
                return self._gm.bad_request(
                    f"Cannot unpause eval task with status '{eval_task.status}'. "
                    "Only paused tasks can be resumed."
                )

            eval_task.status = EvalTaskStatus.PENDING
            filters = eval_task.filters.copy() if eval_task.filters else {}
            filters["created_at"] = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            eval_task.filters = filters
            eval_task.save()

            try:
                eval_task_logger = EvalTaskLogger.objects.get(eval_task_id=eval_task_id)
            except EvalTaskLogger.DoesNotExist:
                eval_task_logger = EvalTaskLogger.objects.create(
                    eval_task_id=eval_task_id, offset=0, status=EvalTaskStatus.PENDING
                )
            eval_task_logger.offset = 0
            eval_task_logger.save()

            return self._gm.success_response(
                {"message": "Eval task unpaused successfully"}
            )

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"])
    def list_eval_tasks_with_project_name(self, request, *args, **kwargs):
        """
        List Eval Tasks filtered
        """
        try:
            queryset = self.get_queryset()

            result = []
            for eval_task in queryset:
                # ``evals`` is prefetched in ``get_queryset`` — calling
                # ``.exists()`` would fire a fresh COUNT(*) query per row
                # and bypass the cache. Check the prefetched list directly.
                if not eval_task.evals.all():
                    continue

                parsed_data = {
                    "id": str(eval_task.id),
                    "name": eval_task.name,
                    "project_name": eval_task.project.name,
                    "status": eval_task.status,
                    "filters_applied": eval_task.filters,
                    "created_at": eval_task.created_at,
                    "evals_applied": [eval.name for eval in eval_task.evals.all()],
                    "sampling_rate": eval_task.sampling_rate,
                    "last_run": eval_task.last_run,
                }
                result.append(parsed_data)

            filters = self.request.query_params.get("filters", [])
            if filters:
                filters = json.loads(filters)
            if filters:
                filter_engine = FilterEngine(result)
                result = filter_engine.apply_filters(filters)

            sort_params = self.request.query_params.get(
                "sort_params", []
            ) or self.request.query_params.get("sortParams", [])
            if sort_params:
                sort_params = json.loads(sort_params)
            if sort_params:
                for sort_param in reversed(sort_params):
                    sort_key = sort_param.get("column_id")
                    sort_direction = sort_param.get("direction", "asc")
                    reverse = sort_direction == "desc"

                    def sort_key_func(x):
                        value = x.get(sort_key)  # noqa: B023
                        # Return a tuple where the first element indicates if the value is None
                        # This ensures None values are consistently sorted to the end
                        return (value is None, value)

                    result.sort(key=sort_key_func, reverse=reverse)

            total_rows = len(result)
            page_number = self.request.query_params.get(
                "page_number", 0
            ) or self.request.query_params.get("pageNumber", 0)
            page_size = self.request.query_params.get(
                "page_size", 10
            ) or self.request.query_params.get("pageSize", 10)
            start = int(page_number) * int(page_size)
            end = start + int(page_size)
            result = result[start:end]

            # Update config to include project name
            config = get_default_eval_task_config(is_project_name_visible=True)

            response = {
                "metadata": {
                    "total_rows": total_rows,
                },
                "table": result,
                "config": config,
            }

            return self._gm.success_response(response)

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(f"error fetching the traces list {str(e)}")

    @action(detail=False, methods=["patch"])
    def update_eval_task(self, request, *args, **kwargs):
        """
        Update an evaluation task with either fresh run or edit & re-run logic.

        Fresh Run: Deletes all previous results and starts completely fresh
        Edit & Re-run: Preserves existing results and only runs missing evaluations
        """
        try:
            eval_task_id = self.request.data.get("eval_task_id")
            if not eval_task_id:
                return self._gm.bad_request("Eval task ID is required")

            # Validate input data
            serializer = EditEvalTaskSerializer(data=self.request.data)
            if not serializer.is_valid():
                logger.error(
                    f"Invalid data for eval task update {eval_task_id}: {serializer.errors}"
                )
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
            edit_type = validated_data["edit_type"]

            # Get eval task with row-level locking to prevent concurrent modifications
            with transaction.atomic():
                try:
                    # Use no_workspace_objects manager to avoid the outer join issue with select_for_update
                    eval_task = (
                        EvalTask.no_workspace_objects.select_for_update()
                        .prefetch_related("evals")
                        .get(
                            id=eval_task_id,
                            project__organization=getattr(request, "organization", None)
                            or request.user.organization,
                        )
                    )
                except EvalTask.DoesNotExist:
                    return self._gm.bad_request("Eval task not found")

                # Validate task state
                if eval_task.status == EvalTaskStatus.RUNNING:
                    return self._gm.bad_request(
                        "Cannot update a running evaluation task. Please pause it first."
                    )

                if eval_task.status == EvalTaskStatus.DELETED:
                    return self._gm.bad_request(
                        "Cannot update a deleted evaluation task."
                    )

                # Get or create eval task logger
                eval_task_logger, created = EvalTaskLogger.objects.get_or_create(
                    eval_task_id=eval_task.id,
                    defaults={
                        "offset": 0,
                        "status": EvalTaskStatus.PENDING,
                        "spanids_processed": [],
                    },
                )

                # Store original state for comparison and logging
                original_state = {
                    "evals": set(eval_task.evals.values_list("id", flat=True)),
                    "name": eval_task.name,
                    "filters": eval_task.filters,
                    "sampling_rate": eval_task.sampling_rate,
                    "spans_limit": eval_task.spans_limit,
                    "run_type": eval_task.run_type,
                }

                spanids_processed = eval_task_logger.spanids_processed or []

                # Extract and validate update fields
                update_fields = self._extract_update_fields(validated_data)

                # Handle evaluation changes first
                new_evals = set(validated_data.get("evals", []))
                if new_evals and new_evals != original_state["evals"]:
                    self._update_eval_assignments(eval_task, new_evals)

                # Process update based on edit type
                if edit_type == "fresh_run":
                    self._handle_fresh_run(eval_task, eval_task_logger)
                    logger.info(f"Fresh run initiated for eval task {eval_task_id}")

                elif edit_type == "edit_rerun":
                    changes_made = self._handle_edit_rerun(
                        eval_task,
                        eval_task_logger,
                        update_fields,
                        original_state,
                        new_evals,
                        spanids_processed,
                    )
                    logger.info(
                        f"Edit & re-run completed for eval task {eval_task_id}, changes: {changes_made}"
                    )

                # Apply field updates to eval task
                updated_instance = None
                if update_fields:
                    update_fields.update(
                        {"status": EvalTaskStatus.PENDING, "last_run": timezone.now()}
                    )

                    task_serializer = self.get_serializer(
                        eval_task, data=update_fields, partial=True
                    )
                    task_serializer.is_valid(raise_exception=True)
                    updated_instance = task_serializer.save()

                # Log the update for audit purposes
                self._log_eval_task_update(
                    eval_task_id, edit_type, original_state, update_fields, request.user
                )

                task_name = (
                    updated_instance.name if updated_instance else eval_task.name
                )
                eval_task.status = EvalTaskStatus.PENDING
                eval_task.save(update_fields=["status"])
                return self._gm.success_response(
                    {
                        "message": f"Evaluation task '{task_name}' has been updated successfully.",
                        "edit_type": edit_type,
                        "task_id": str(eval_task_id),
                    }
                )

        except Exception as e:
            logger.error(
                f"Error updating eval task {eval_task_id}: {str(e)}", exc_info=True
            )
            return self._gm.bad_request(f"Error updating evaluation task: {str(e)}")

    def _extract_update_fields(self, validated_data):
        """Extract valid update fields from validated data.

        ``row_type`` is intentionally absent from the allow-list — it's
        immutable after task creation (the serializer rejects it earlier,
        this is a belt-and-braces guard so any future code path that
        bypasses the serializer still can't write it through).
        """
        update_fields = {}
        allowed_fields = [
            "name",
            "filters",
            "sampling_rate",
            "spans_limit",
            "evals",
            "run_type",
        ]

        for field in allowed_fields:
            value = validated_data.get(field)
            if value is not None:
                update_fields[field] = value

        return update_fields

    def _update_eval_assignments(self, eval_task, new_evals):
        """Update evaluation assignments and mark removed ones as deleted"""
        if not new_evals:
            return

        # Mark evaluations not in new set as deleted
        deleted_count = (
            EvalLogger.objects.filter(eval_task_id=eval_task.id, deleted=False)
            .exclude(custom_eval_config_id__in=new_evals)
            .update(deleted=True, deleted_at=timezone.now())
        )

        # Update eval task assignments
        eval_task.evals.set(new_evals)

        if deleted_count > 0:
            logger.info(
                f"Marked {deleted_count} evaluation results as deleted for task {eval_task.id}"
            )

    def _handle_fresh_run(self, eval_task, eval_task_logger):
        """Handle fresh run logic - delete all results and reset state"""
        # Mark all existing evaluation results as deleted
        deleted_count = EvalLogger.objects.filter(
            eval_task_id=eval_task.id, deleted=False
        ).update(deleted=True, deleted_at=timezone.now())

        # Reset logger state
        eval_task_logger.spanids_processed = []
        eval_task_logger.offset = 0
        eval_task_logger.status = EvalTaskStatus.PENDING
        eval_task_logger.save(update_fields=["spanids_processed", "offset", "status"])

        logger.info(
            f"Fresh run: Deleted {deleted_count} evaluation results for task {eval_task.id}"
        )

    def _handle_edit_rerun(
        self,
        eval_task,
        eval_task_logger,
        update_fields,
        original_state,
        new_evals,
        spanids_processed,
    ):
        """Handle edit and rerun logic with intelligent evaluation scheduling"""

        changes_made = []

        # Only process historical runs with existing processed spans
        if (
            update_fields.get("run_type", eval_task.run_type) != RunType.HISTORICAL
            or not spanids_processed
        ):
            return changes_made

        try:
            # Calculate new sampling parameters
            new_span_limit = update_fields.get("spans_limit", eval_task.spans_limit)
            new_sampling_rate = update_fields.get(
                "sampling_rate", eval_task.sampling_rate
            )
            filters = update_fields.get("filters") or eval_task.filters

            # Validate filters and get total spans count
            parsed_filters = parsing_evaltask_filters(filters)
            total_spans = ObservationSpan.objects.filter(parsed_filters).count()

            if total_spans == 0:
                logger.warning(
                    f"No spans found for eval task {eval_task.id} with current filters"
                )
                return changes_made

            # Calculate target sample parameters
            target_sample_size = int((new_sampling_rate / 100) * total_spans)
            max_spans = min(new_span_limit or float("inf"), target_sample_size)

            # Determine final span set to work with
            if max_spans >= len(spanids_processed):
                final_spans = spanids_processed
            else:
                final_spans = sample(spanids_processed, int(max_spans))
                changes_made.append(f"Resampled to {len(final_spans)} spans")

            if not final_spans:
                logger.info(f"No spans to process for eval task {eval_task.id}")
                return changes_made

            # Handle existing evaluations - fill gaps
            existing_eval_ids = list(original_state["evals"].intersection(new_evals))
            if existing_eval_ids:
                missing_count = self._schedule_missing_evaluations(
                    eval_task.id, existing_eval_ids, final_spans
                )
                if missing_count > 0:
                    changes_made.append(
                        f"Scheduled {missing_count} missing evaluations"
                    )

            # Handle completely new evaluations
            new_eval_ids = list(new_evals - original_state["evals"])
            if new_eval_ids:
                self._schedule_new_evaluations(final_spans, new_eval_ids, eval_task.id)
                changes_made.append(
                    f"Scheduled {len(new_eval_ids)} new evaluation types"
                )

        except Exception as e:
            logger.error(f"Error in edit_rerun for task {eval_task.id}: {str(e)}")
            raise

        return changes_made

    def _schedule_missing_evaluations(self, eval_task_id, eval_ids, target_spans):
        """Schedule evaluations only for spans that haven't been evaluated yet"""

        total_missing = 0

        for eval_id in eval_ids:
            try:
                # Get spans already evaluated for this eval (not deleted)
                evaluated_spans = set(
                    EvalLogger.objects.filter(
                        eval_task_id=eval_task_id,
                        custom_eval_config_id=eval_id,
                        deleted=False,
                    ).values_list("observation_span_id", flat=True)
                )

                # Find spans that need evaluation
                missing_spans = [
                    span_id
                    for span_id in target_spans
                    if span_id not in evaluated_spans
                ]

                if missing_spans:
                    # Schedule evaluation for missing spans
                    run_for_processed_spans.delay(
                        missing_spans, [eval_id], eval_task_id
                    )
                    total_missing += len(missing_spans)
                    logger.info(
                        f"Scheduled {len(missing_spans)} missing evaluations for eval {eval_id}"
                    )

            except Exception as e:
                logger.error(
                    f"Error scheduling missing evaluations for eval {eval_id}: {str(e)}"
                )
                continue

        return total_missing

    def _schedule_new_evaluations(self, span_ids, eval_ids, eval_task_id):
        """Schedule evaluations for completely new evaluation types"""
        try:
            if span_ids and eval_ids:
                run_for_processed_spans.delay(span_ids, eval_ids, eval_task_id)
                logger.info(
                    f"Scheduled {len(span_ids)} spans for {len(eval_ids)} new evaluations"
                )
        except Exception as e:
            logger.error(f"Error scheduling new evaluations: {str(e)}")
            raise

    def _log_eval_task_update(
        self, eval_task_id, edit_type, original_state, update_fields, user
    ):
        """Log evaluation task updates for audit purposes"""
        try:
            changes = []
            for field, new_value in update_fields.items():
                if field in original_state:
                    old_value = original_state[field]
                    if old_value != new_value:
                        changes.append(f"{field}: {old_value} -> {new_value}")
                else:
                    changes.append(f"{field}: -> {new_value}")

            log_message = (
                f"Eval task {eval_task_id} updated by {user.email} "
                f"(edit_type: {edit_type})"
            )

            if changes:
                log_message += f" - Changes: {'; '.join(changes)}"

            logger.info(log_message)

        except Exception as e:
            logger.error(f"Error logging eval task update: {str(e)}")

    @action(detail=False, methods=["get"])
    def get_eval_details(self, request, *args, **kwargs):
        try:
            eval_id = self.request.query_params.get("eval_id")

            queryset = (
                EvalTask.objects.select_related("project")
                .prefetch_related("evals")
                .get(
                    id=eval_id,
                    project__organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            )

            if not queryset:
                return self._gm.bad_request("Eval task not found")

            # Build rich eval objects so the frontend can render eval cards
            # with name, mapping, model, template info — not just bare UUIDs.
            from tracer.serializers.custom_eval_config import is_rule_prompt_customized

            evals_rich = []
            for eval_config in queryset.evals.select_related("eval_template").all():
                template = eval_config.eval_template
                evals_rich.append(
                    {
                        "id": str(eval_config.id),
                        "name": eval_config.name,
                        "template_id": str(template.id) if template else None,
                        "templateId": str(template.id) if template else None,
                        "mapping": eval_config.mapping or {},
                        "model": eval_config.model,
                        "config": eval_config.config or {},
                        "error_localizer": eval_config.error_localizer,
                        "evalType": template.eval_type if template else None,
                        "templateType": (
                            template.template_type if template else "single"
                        ),
                        "outputType": (
                            template.output_type_normalized if template else None
                        ),
                        "is_customized": is_rule_prompt_customized(eval_config),
                    }
                )

            result = {
                "id": str(queryset.id),
                "name": queryset.name,
                "project_id": queryset.project.id,
                "project_name": queryset.project.name,
                "status": queryset.status,
                "filters_applied": queryset.filters,
                "created_at": queryset.created_at,
                "evals_applied": evals_rich,
                "spans_limit": queryset.spans_limit,
                "sampling_rate": queryset.sampling_rate,
                "last_run": queryset.last_run,
                "run_type": queryset.run_type,
                "row_type": queryset.row_type,
            }

            return self._gm.success_response(result)

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(f"Error fetching eval task details {str(e)}")
