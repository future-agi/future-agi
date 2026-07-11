import json
import traceback
import uuid as uuid_module
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import structlog
from django.db import models, transaction
from django.db.models import Avg, Count, F, Func, Max, Q, Value
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from model_hub.models.evals_metric import EvalTemplate
from tfc.temporal.eval_tasks.client import (
    signal_pause_eval_task_workflow,
    start_eval_task_workflow_sync,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import EmptyRequestSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.models.project import Project
from tracer.serializers.eval_task import (
    EditEvalTaskSerializer,
    EvalTaskCreateResponseSerializer,
    EvalTaskDeleteRequestSerializer,
    EvalTaskIdQuerySerializer,
    EvalTaskListQuerySerializer,
    EvalTaskListWithProjectNameQuerySerializer,
    EvalTaskMessageResponseSerializer,
    EvalTaskSerializer,
    EvalTaskUpdateRequestSerializer,
    EvalTaskUpdateResponseSerializer,
    PaginationQuerySerializer,
)
from tracer.services.eval_tasks.edit_options import validate_edit_action
from tracer.services.eval_tasks.entries import soft_delete_live
from tracer.utils.filters import FilterEngine
from tracer.utils.helper import get_default_eval_task_config

logger = structlog.get_logger(__name__)


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


def _extract_partial_input_warnings(output_metadata):
    if not isinstance(output_metadata, dict):
        return []
    warnings = output_metadata.get("warnings") or []
    if isinstance(warnings, dict):
        warnings = [warnings]
    if not isinstance(warnings, list):
        return []
    return [
        warning
        for warning in warnings
        if isinstance(warning, dict) and warning.get("type") == "partial_input"
    ]


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


def _truthy(v):
    """Match common DRF bool query-param conventions."""
    return str(v).lower() in ("true", "1", "yes")


def _compute_eval_aggregation(base_qs):
    """Per-eval-config rollup for one eval task.

    Returns a dict keyed by ``CustomEvalConfig.name`` so the FE can render
    one row per configured eval. Value shape:

        {"id": str, "name": str, "output_type": str, "aggregated_score": ...}

    ``aggregated_score`` depends on the eval's ``output_type_normalized``:
      * ``percentage``    → ``Avg(output_float)``, rounded to 4 dp.
      * ``pass_fail``     → pass-rate as 0–100 pct, 2 dp (matches the
        ``pass_rate`` field on the legacy ``get_usage`` shape).
      * ``deterministic`` → ``{choice: pct}`` dict, 2 dp. Only choices that
        actually appeared in the data are included.

    The deterministic branch iterates rows in Python because PostgreSQL
    JSONB array unnesting isn't expressible cleanly through the ORM and
    the row count per (eval_task × eval_config) is bounded.
    """
    # Imported lazily to avoid the module-import cycle bite (tracer.views
    # → tracer.models pulls things that import this view at import time).
    from tracer.models.custom_eval_config import CustomEvalConfig

    config_ids = list(
        base_qs.values_list("custom_eval_config_id", flat=True).distinct()
    )
    configs = CustomEvalConfig.objects.filter(id__in=config_ids).select_related(
        "eval_template"
    )

    result = {}
    for cfg in configs:
        output_type = (
            cfg.eval_template.output_type_normalized
            if cfg.eval_template
            else "pass_fail"
        )
        rows = base_qs.filter(custom_eval_config_id=cfg.id, error=False)

        aggregated_score = None
        if output_type == "percentage":
            avg = (
                rows.exclude(output_float__isnull=True)
                .aggregate(avg=Avg("output_float"))
                .get("avg")
            )
            aggregated_score = round(avg, 4) if avg is not None else None
        elif output_type == "pass_fail":
            bool_rows = rows.exclude(output_bool__isnull=True)
            total = bool_rows.count()
            passed = bool_rows.filter(output_bool=True).count()
            aggregated_score = round(passed / total * 100, 2) if total else None
        elif output_type == "deterministic":
            counter = Counter()
            tally = 0
            for lst in rows.values_list("output_str_list", flat=True):
                if not lst:
                    continue
                tally += 1
                # One count per choice per row — a multi-choice row that
                # picks {"A","B"} contributes 1 to each, not 2 to one.
                counter.update(set(lst))
            aggregated_score = (
                {c: round(n / tally * 100, 2) for c, n in counter.items()}
                if tally
                else {}
            )

        result[cfg.name] = {
            "id": str(cfg.id),
            "name": cfg.name,
            "output_type": output_type,
            "aggregated_score": aggregated_score,
        }
    return result


def _compute_span_aggregation(base_qs):
    """Per-span pivot of raw eval values for one eval task.

    Returns ``{span_id → {eval_name → {id, name, output_type, value}}}``.
    ``value`` is the raw column read for the eval's output type — no
    averaging. Session/trace-target rows (``observation_span_id IS NULL``)
    are filtered out.

    When the same ``(span, eval_config)`` has multiple rows (re-runs),
    the latest by ``created_at`` wins via the ORDER BY + first-seen set.
    """
    qs = (
        base_qs.filter(observation_span_id__isnull=False, error=False)
        .select_related("custom_eval_config__eval_template")
        .order_by("observation_span_id", "custom_eval_config_id", "-created_at")
    )

    result = defaultdict(dict)
    seen = set()
    for log in qs.iterator(chunk_size=1000):
        key = (log.observation_span_id, log.custom_eval_config_id)
        if key in seen:
            continue
        seen.add(key)

        cfg = log.custom_eval_config
        if cfg is None:
            continue
        output_type = (
            cfg.eval_template.output_type_normalized
            if cfg.eval_template
            else "pass_fail"
        )
        if output_type == EvalTemplate.OutputTypeNormalized.PERCENTAGE:
            value = log.output_float
        elif output_type == EvalTemplate.OutputTypeNormalized.PASS_FAIL:
            value = log.output_bool
        elif output_type == EvalTemplate.OutputTypeNormalized.DETERMINISTIC:
            value = log.output_str_list
        else:
            value = None

        result[str(log.observation_span_id)][cfg.name] = {
            "id": str(cfg.id),
            "name": cfg.name,
            "output_type": output_type,
            "value": value,
        }
    return dict(result)


class EvalTaskView(BaseModelViewSetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = EvalTaskSerializer

    def _get_request_organization(self):
        # Returns None for unauthenticated requests (e.g. drf-yasg's fake view
        # during OpenAPI generation) instead of raising on AnonymousUser, which
        # would otherwise silently drop request bodies from the generated schema.
        org = getattr(self.request, "organization", None)
        if org is not None:
            return org
        user = getattr(self.request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        return getattr(user, "organization", None)

    def _project_workspace_scope_q(self, organization_id):
        workspace = getattr(self.request, "workspace", None)
        if not workspace:
            return Q()
        if getattr(workspace, "is_default", False):
            return (
                Q(project__workspace=workspace)
                | Q(
                    project__workspace__is_default=True,
                    project__workspace__organization_id=organization_id,
                )
                | Q(
                    project__workspace__isnull=True,
                    project__organization_id=organization_id,
                )
            )
        return Q(project__workspace=workspace)

    def _scope_eval_task_queryset(self, queryset):
        organization = self._get_request_organization()
        if organization is None:
            return queryset.none()
        organization_id = organization.id
        return queryset.filter(
            project__organization_id=organization_id,
            project__deleted=False,
        ).filter(self._project_workspace_scope_q(organization_id))

    def _scope_project_queryset(self, queryset):
        organization = self._get_request_organization()
        if organization is None:
            return queryset.none()
        organization_id = organization.id
        workspace = getattr(self.request, "workspace", None)
        queryset = queryset.filter(organization_id=organization_id, deleted=False)
        if not workspace:
            return queryset
        if getattr(workspace, "is_default", False):
            return queryset.filter(
                Q(workspace=workspace)
                | Q(
                    workspace__is_default=True,
                    workspace__organization_id=organization_id,
                )
                | Q(workspace__isnull=True, organization_id=organization_id)
            )
        return queryset.filter(workspace=workspace)

    def _scope_custom_eval_config_queryset(self, queryset, project_id=None):
        organization = self._get_request_organization()
        if organization is None:
            return queryset.none()
        organization_id = organization.id
        queryset = queryset.filter(
            deleted=False,
            project__organization_id=organization_id,
            project__deleted=False,
        ).filter(self._project_workspace_scope_q(organization_id))
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset

    def _invalid_eval_ids_for_project(self, eval_ids, project_id):
        requested_ids = {str(eval_id) for eval_id in (eval_ids or [])}
        if not requested_ids:
            return []
        visible_ids = {
            str(eval_id)
            for eval_id in self._scope_custom_eval_config_queryset(
                CustomEvalConfig.objects.all(), project_id=project_id
            )
            .filter(id__in=requested_ids)
            .values_list("id", flat=True)
        }
        return sorted(requested_ids - visible_ids)

    def get_serializer(self, *args, **kwargs):
        serializer = super().get_serializer(*args, **kwargs)
        fields = getattr(serializer, "fields", None)
        if fields is None and getattr(serializer, "child", None) is not None:
            fields = getattr(serializer.child, "fields", None)
        if not fields:
            return serializer
        if "project" in fields:
            fields["project"].queryset = self._scope_project_queryset(
                Project.objects.all()
            )
        if "evals" in fields:
            fields["evals"].queryset = self._scope_custom_eval_config_queryset(
                CustomEvalConfig.objects.all()
            )
        return serializer

    def get_queryset(self):
        eval_task_id = self.kwargs.get("pk")

        # Get base queryset with automatic filtering from mixin
        queryset = self._scope_eval_task_queryset(super().get_queryset())
        queryset = queryset.select_related("project")
        queryset = queryset.prefetch_related("evals")

        if eval_task_id:
            queryset = queryset.filter(id=eval_task_id)

        project_id = self.request.query_params.get("project_id")
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        search_name = self.request.query_params.get("name")
        if search_name:
            queryset = queryset.filter(name__icontains=search_name)

        return queryset

    @validated_request(request_serializer=EvalTaskSerializer)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @validated_request(
        request_serializer=EvalTaskSerializer,
        partial_request_validation=True,
        strict_request_validation=False,
    )
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        # Cascade soft-delete to the task's loggers and eval results so they
        # don't outlive the deleted task (mirrors mark_eval_tasks_deleted).
        now = timezone.now()
        EvalTaskLogger.objects.filter(eval_task_id=instance.id).update(
            deleted=True, deleted_at=now
        )
        EvalLogger.objects.filter(eval_task_id=instance.id).update(
            deleted=True, deleted_at=now
        )
        instance.delete()

    @validated_request(
        request_serializer=EvalTaskSerializer,
        responses={200: EvalTaskCreateResponseSerializer},
    )
    def create(self, request, *args, **kwargs):
        try:
            data = request.data
            data["status"] = EvalTaskStatus.PENDING
            filters = data.get("filters", {})
            project_id = data.get("project")
            if (
                project_id
                and not self._scope_project_queryset(Project.objects.all())
                .filter(id=project_id)
                .exists()
            ):
                return self._gm.bad_request("Project not found")
            if project_id:
                filters["project_id"] = project_id
            data["filters"] = filters

            data["last_run"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            invalid_eval_ids = self._invalid_eval_ids_for_project(
                [eval_config.id for eval_config in serializer.validated_data["evals"]],
                project_id,
            )
            if invalid_eval_ids:
                return self._gm.bad_request(
                    "Eval configs not found for project: " + ", ".join(invalid_eval_ids)
                )
            eval_task = serializer.save()

            # The workflow's first step materializes entries, so create returns
            # immediately even for large tasks.
            start_eval_task_workflow_sync(eval_task)

            return self._gm.success_response({"id": eval_task.id})

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"], pagination_class=None)
    @validated_request(query_serializer=EvalTaskListQuerySerializer)
    def list_eval_tasks(self, request, *args, **kwargs):
        """
        List Eval Tasks filtered
        """
        try:
            query_data = request.validated_query_data

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
                    "run_type": eval_task.get("run_type"),
                    "filters_applied": eval_task["filters"],
                    "created_at": eval_task["created_at"],
                    "evals_applied": eval_names,
                    "sampling_rate": eval_task["sampling_rate"],
                    "last_run": eval_task["last_run"],
                }
                result.append(parsed_data)

            filters = query_data.get("filters", [])
            if filters:
                filter_engine = FilterEngine(result)
                result = filter_engine.apply_filters(filters)

            sort_params = query_data.get("sort_params", [])
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
            page_number = query_data.get("page_number", 0)
            page_size = query_data.get("page_size", 30)
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
    _WARNING_GROUPS_LIMIT = 20
    _WARNING_LOG_SCAN_LIMIT = 1000

    @action(detail=False, methods=["get"])
    def get_eval_task_logs(self, request, *args, **kwargs):
        try:
            eval_task_id = self.request.query_params.get("eval_task_id")
            eval_task = self._scope_eval_task_queryset(EvalTask.objects).get(
                id=eval_task_id,
            )

            # Progress counts — cheap aggregate, indexed COUNTs. Counted by the
            # entry's lifecycle ``status`` (not the ``error``/``skipped_reason``
            # result columns): a pending/running entry has error=False and
            # skipped_reason=null, so result-column counting would tally every
            # not-yet-run entry as a success. ``total_count`` is every
            # materialized entry (the manager already excludes soft-deleted),
            # so while a task is pending Total shows the full set and success/
            # errors start at 0 and climb as the drain executes.
            counts = EvalLogger.objects.filter(eval_task_id=eval_task_id).aggregate(
                total_count=Count("id"),
                success_count=Count("id", filter=Q(status=EvalEntryStatus.COMPLETED)),
                errors_count=Count("id", filter=Q(status=EvalEntryStatus.ERRORED)),
                # Skipped: the eval never ran (e.g. a mapped span attribute
                # was absent). Counted separately so it stays out of the
                # success and failure tallies.
                skipped_count=Count("id", filter=Q(status=EvalEntryStatus.SKIPPED)),
                # Partial-input warnings live in
                # output_metadata.warnings as a JSON array. has_key on
                # the JSONField gives us a cheap "any warnings?" filter
                # without scanning the contents.
                warnings_count=Count(
                    "id", filter=Q(output_metadata__has_key="warnings")
                ),
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

            warning_groups_by_key = {}
            warning_logs_qs = (
                EvalLogger.objects.filter(
                    eval_task_id=eval_task_id,
                    output_metadata__has_key="warnings",
                )
                .order_by("-created_at")
                .values_list("output_metadata", flat=True)[
                    : self._WARNING_LOG_SCAN_LIMIT
                ]
            )
            for output_metadata in warning_logs_qs:
                for warning in _extract_partial_input_warnings(output_metadata):
                    empty_keys = sorted(warning.get("empty_keys") or [])
                    filled_keys = sorted(warning.get("filled_keys") or [])
                    key = tuple(empty_keys)
                    if key not in warning_groups_by_key:
                        warning_groups_by_key[key] = {
                            "type": "partial_input",
                            "empty_keys": empty_keys,
                            "filled_keys": filled_keys,
                            "message": warning.get("message")
                            or (
                                "Eval ran with some inputs empty. "
                                "Result may be less reliable. "
                                "Ignore if this is intentional."
                            ),
                            "count": 0,
                        }
                    warning_groups_by_key[key]["count"] += 1

            warning_groups = sorted(
                warning_groups_by_key.values(),
                key=lambda group: group["count"],
                reverse=True,
            )[: self._WARNING_GROUPS_LIMIT]

            result = {
                "start_time": eval_task.start_time,
                "end_time": eval_task.end_time,
                # Task status travels with the counts (same response) so the
                # frontend can keep polling until it observes a terminal status,
                # and the fetch that first sees "completed" already carries the
                # final tallies — no off-by-one-tick stale count.
                "status": eval_task.status,
                # Duration is only meaningful for historical runs (which finalize
                # with an end_time). Continuous tasks never end, so the frontend
                # hides the Duration card based on this.
                "run_type": eval_task.run_type,
                "errors_count": counts["errors_count"],
                "success_count": counts["success_count"],
                "skipped_count": counts["skipped_count"],
                "warnings_count": counts["warnings_count"],
                "total_count": counts["total_count"],
                "error_groups": error_groups,
                "warning_groups": warning_groups,
                # Indicates whether we capped at _ERROR_GROUPS_LIMIT — the
                # frontend can show a "showing top 50 error types" hint.
                "error_groups_truncated": len(error_groups) == self._ERROR_GROUPS_LIMIT,
                "warning_groups_truncated": counts["warnings_count"]
                > self._WARNING_LOG_SCAN_LIMIT
                or len(warning_groups_by_key) > self._WARNING_GROUPS_LIMIT,
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

            if (
                not self._scope_eval_task_queryset(EvalTask.objects)
                .filter(id=eval_task_id)
                .exists()
            ):
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

            # ── Aggregation short-circuit ──
            # When either flag is set, return ONLY the aggregated payload.
            # Soft-deleted rows are excluded (intentional departure from the
            # legacy path) so rollups reflect the user's current view of
            # the data. ``period`` is not applied — these are task-wide.
            #
            # Spans-only semantics: session-target rows (``observation_span_id
            # IS NULL``) are excluded from both aggregations so the row set
            # is consistent whether or not a date range is supplied.
            #
            # Optional ``start_date`` / ``end_date`` (ISO-8601) scope the
            # rollup to spans whose ``observation_span.created_at`` falls
            # in the range.
            eval_aggregation = _truthy(
                self.request.query_params.get("eval_aggregation")
            )
            span_aggregation = _truthy(
                self.request.query_params.get("span_aggregation")
            )
            if eval_aggregation or span_aggregation:
                agg_base_qs = EvalLogger.objects.filter(
                    eval_task_id=str(eval_task_id),
                    deleted=False,
                    observation_span_id__isnull=False,
                )
                if eval_id_filter:
                    agg_base_qs = agg_base_qs.filter(
                        custom_eval_config_id=eval_id_filter
                    )

                start_date_str = self.request.query_params.get("start_date")
                end_date_str = self.request.query_params.get("end_date")
                if start_date_str:
                    agg_base_qs = agg_base_qs.filter(
                        observation_span__created_at__gte=datetime.fromisoformat(
                            start_date_str
                        )
                    )
                if end_date_str:
                    agg_base_qs = agg_base_qs.filter(
                        observation_span__created_at__lte=datetime.fromisoformat(
                            end_date_str
                        )
                    )

                agg_response = {"eval_task_id": str(eval_task_id)}
                if eval_aggregation:
                    agg_response["eval_aggregation"] = _compute_eval_aggregation(
                        agg_base_qs
                    )
                if span_aggregation:
                    agg_response["span_aggregation"] = _compute_span_aggregation(
                        agg_base_qs
                    )
                return self._gm.success_response(agg_response)

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

            skipped_count = period_qs.filter(status="skipped").count()
            success_count = period_qs.filter(error=False).exclude(status="skipped").count()
            error_count = period_qs.filter(error=True).count()
            scorable = runs_period - skipped_count
            pass_rate = (
                round((success_count / scorable * 100), 2) if scorable > 0 else 0
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
            logs_page = paginator.paginate_queryset(logs_qs, self.request, view=self)

            # Peerdb-less deploys never populate tracer_observation_span, so
            # log.observation_span is None. Batch-fetch this page's spans from
            # CH so the Input column has something to render.
            _ch_span_attrs: dict[str, dict] = {}
            _ch_span_names: dict[str, str] = {}
            _missing_span_ids = list(
                {
                    log.observation_span_id
                    for log in logs_page
                    if log.observation_span_id and log.observation_span is None
                }
            )
            if _missing_span_ids:
                try:
                    from tracer.services.clickhouse.v2 import get_reader

                    with get_reader() as _reader:
                        for _ch in _reader.list_by_ids(
                            _missing_span_ids, include_heavy=False
                        ):
                            _ch_span_attrs[_ch.id] = _ch.attrs_string or {}
                            _ch_span_names[_ch.id] = _ch.name or ""
                except Exception as _e:  # noqa: BLE001
                    logger.warning(
                        "eval_task_usage_ch_span_fallback_failed", error=str(_e)
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
                elif log.status == "skipped":
                    result_label = "Skipped"
                    score = None
                    status = "skipped"
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
                _span_attrs: dict = {}
                _span_name = ""
                if obs_span:
                    _span_attrs = obs_span.span_attributes or {}
                    _span_name = obs_span.name or ""
                elif log.observation_span_id in _ch_span_attrs:
                    _span_attrs = _ch_span_attrs[log.observation_span_id]
                    _span_name = _ch_span_names.get(log.observation_span_id, "")

                if _span_attrs or _span_name:
                    input_val = (
                        _span_attrs.get("input")
                        or _span_attrs.get("input.value")
                        or _span_attrs.get("gen_ai.input.messages.0.message.content")
                        or _span_name
                        or ""
                    )
                    if isinstance(input_val, dict):
                        input_str = json.dumps(input_val)[:200]
                    else:
                        input_str = str(input_val)[:200]
                elif trace_session:
                    input_str = (trace_session.name or "")[:200]

                reason = (
                    log.eval_explanation
                    or log.error_message
                    or log.skipped_reason
                    or ""
                )

                # Partial-input warnings stored on output_metadata by the
                # tracer eval path. Surface at the row level so the FE
                # can render a yellow indicator alongside other status.
                _output_meta = log.output_metadata or {}
                warnings = (
                    _output_meta.get("warnings")
                    if isinstance(_output_meta, dict)
                    else None
                )

                log_items.append(
                    {
                        "id": str(log.id),
                        "input": input_str,
                        "result": result_label,
                        "score": score,
                        "reason": reason,
                        "status": status,
                        "skipped_reason": log.skipped_reason or None,
                        "source": "eval_task",
                        "warnings": warnings or [],
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
                            "warnings": warnings or [],
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
                    "skipped_count": skipped_count,
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

    @validated_request(
        request_serializer=EvalTaskDeleteRequestSerializer,
        responses={200: EvalTaskMessageResponseSerializer},
    )
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

            eval_tasks = self._scope_eval_task_queryset(EvalTask.objects).filter(
                id__in=eval_task_ids,
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

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        query_serializer=EvalTaskIdQuerySerializer,
        responses={200: EvalTaskMessageResponseSerializer},
    )
    @action(detail=False, methods=["post"])
    def pause_eval_task(self, request, *args, **kwargs):
        try:
            eval_task_id = self.request.query_params.get("eval_task_id")
            if not eval_task_id:
                return self._gm.bad_request("Eval task ID is required")

            try:
                eval_task = self._scope_eval_task_queryset(EvalTask.objects).get(
                    id=eval_task_id,
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

            # Nudge the running workflow to stop launching new evals immediately.
            # Best-effort: the paused status above is the durable signal the
            # workflow also honours at its next batch boundary.
            signal_pause_eval_task_workflow(eval_task.id)

            return self._gm.success_response(
                {"message": "Eval task paused successfully"}
            )

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(str(e))

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        query_serializer=EvalTaskIdQuerySerializer,
        responses={200: EvalTaskMessageResponseSerializer},
    )
    @action(detail=False, methods=["post"])
    def unpause_eval_task(self, request, *args, **kwargs):
        try:
            eval_task_id = self.request.query_params.get("eval_task_id")
            if not eval_task_id:
                return self._gm.bad_request("Eval task ID is required")

            try:
                eval_task = self._scope_eval_task_queryset(EvalTask.objects).get(
                    id=eval_task_id,
                )
            except EvalTask.DoesNotExist:
                return self._gm.bad_request("Eval task not found")

            if eval_task.status != EvalTaskStatus.PAUSED:
                return self._gm.bad_request(
                    f"Cannot unpause eval task with status '{eval_task.status}'. "
                    "Only paused tasks can be resumed."
                )

            eval_task.status = EvalTaskStatus.PENDING
            eval_task.save(update_fields=["status"])

            # Pause exits the workflow; resuming starts a fresh run that picks up
            # the remaining pending/running entries.
            start_eval_task_workflow_sync(eval_task)

            return self._gm.success_response(
                {"message": "Eval task unpaused successfully"}
            )

        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"], pagination_class=None)
    @validated_request(query_serializer=EvalTaskListWithProjectNameQuerySerializer)
    def list_eval_tasks_with_project_name(self, request, *args, **kwargs):
        """
        List Eval Tasks filtered
        """
        try:
            query_data = request.validated_query_data

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
                    "run_type": eval_task.run_type,
                    "filters_applied": eval_task.filters,
                    "created_at": eval_task.created_at,
                    "evals_applied": [eval.name for eval in eval_task.evals.all()],
                    "sampling_rate": eval_task.sampling_rate,
                    "last_run": eval_task.last_run,
                }
                result.append(parsed_data)

            filters = query_data.get("filters", [])
            if filters:
                filter_engine = FilterEngine(result)
                result = filter_engine.apply_filters(filters)

            sort_params = query_data.get("sort_params", [])
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
            page_number = query_data.get("page_number", 0)
            page_size = query_data.get("page_size", 10)
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

    @validated_request(
        request_serializer=EvalTaskUpdateRequestSerializer,
        responses={200: EvalTaskUpdateResponseSerializer},
    )
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
                    # Lock only the EvalTask row. Workspace scoping joins through
                    # nullable Project.workspace for legacy rows, and PostgreSQL
                    # rejects FOR UPDATE on the nullable side of that outer join.
                    eval_task = (
                        self._scope_eval_task_queryset(
                            EvalTask.no_workspace_objects.select_for_update(
                                of=("self",)
                            )
                        )
                        .prefetch_related("evals")
                        .get(id=eval_task_id)
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

                original_evals = set(eval_task.evals.values_list("id", flat=True))
                original_run_type = eval_task.run_type
                update_fields = self._extract_update_fields(validated_data)

                # Validate the requested evals belong to the task's project.
                requested_evals = validated_data.get("evals")
                if requested_evals is not None:
                    invalid_eval_ids = self._invalid_eval_ids_for_project(
                        requested_evals, eval_task.project_id
                    )
                    if invalid_eval_ids:
                        return self._gm.bad_request(
                            "Eval configs not found for task project: "
                            + ", ".join(invalid_eval_ids)
                        )

                new_evals = (
                    set(requested_evals)
                    if requested_evals is not None
                    else original_evals
                )
                evals_changed = (
                    requested_evals is not None and new_evals != original_evals
                )
                rows_changed = any(
                    field in update_fields
                    and update_fields[field] != getattr(eval_task, field)
                    for field in ("filters", "sampling_rate", "spans_limit")
                )
                new_run_type = update_fields.get("run_type")

                # Enforce which rerun action is allowed for what changed.
                action_error = validate_edit_action(
                    edit_type,
                    original_run_type=original_run_type,
                    new_run_type=new_run_type,
                    evals_changed=evals_changed,
                    rows_changed=rows_changed,
                )
                if action_error:
                    return self._gm.bad_request(action_error)

                # Switching continuous -> historical needs a row limit (continuous
                # never had one).
                if (
                    new_run_type == RunType.HISTORICAL
                    and original_run_type == RunType.CONTINUOUS
                    and not update_fields.get("spans_limit")
                    and not eval_task.spans_limit
                ):
                    return self._gm.bad_request(
                        "Switching to a historical task requires a row limit."
                    )

                # Write the desired config (evals are an m2m the serializer sets).
                update_fields["status"] = EvalTaskStatus.PENDING
                update_fields["last_run"] = timezone.now()
                task_serializer = self.get_serializer(
                    eval_task, data=update_fields, partial=True
                )
                task_serializer.is_valid(raise_exception=True)
                eval_task = task_serializer.save()

                # Delete & rerun wipes live entries first; the workflow then
                # reconciles (materialize/diff) and drains for both cases, so the
                # request returns without doing that work synchronously.
                if edit_type == "fresh_run":
                    soft_delete_live(eval_task)
                start_eval_task_workflow_sync(eval_task)

                return self._gm.success_response(
                    {
                        "message": (
                            f"Evaluation task '{eval_task.name}' has been "
                            "updated successfully."
                        ),
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

    @action(detail=False, methods=["get"])
    def get_eval_details(self, request, *args, **kwargs):
        try:
            eval_id = self.request.query_params.get("eval_id")
            if not eval_id:
                return self._gm.bad_request("eval_id is required")

            queryset = self._scope_eval_task_queryset(
                EvalTask.objects.select_related("project").prefetch_related("evals")
            ).get(id=eval_id)

            # Build rich eval objects so the frontend can render eval cards
            # with name, mapping, model, template info — not just bare UUIDs.
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

        except EvalTask.DoesNotExist:
            return self._gm.not_found("Eval task not found")
        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(f"Error fetching eval task details {str(e)}")
