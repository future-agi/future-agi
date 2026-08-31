import json
import traceback
import uuid as uuid_module
from collections import Counter, defaultdict
from datetime import datetime

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
from tracer.constants.eval_task_usage import UsagePeriod
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
    EvalTaskUsageQuerySerializer,
)
from tracer.services.eval_tasks import usage
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
    # GET /tracer/eval-task/get_usage/
    #
    # Stats row + time-series chart + paginated logs for one eval task.
    # Mirrors `EvalUsageStatsView`'s response shape so the frontend reuses
    # `UsageChart`, `DataTable` and `DataTablePagination` unchanged. The
    # computations live in `services/eval_tasks/usage.py`.
    # ──────────────────────────────────────────────────────────────────
    @action(detail=False, methods=["get"])
    @validated_request(
        query_serializer=EvalTaskUsageQuerySerializer,
        framework_query_params=("page", "limit"),
    )
    def get_usage(self, request, *args, **kwargs):
        try:
            query = request.validated_query_data
            eval_task_id = str(query["eval_task_id"])
            eval_id_filter = query.get("eval_id")

            if (
                not self._scope_eval_task_queryset(EvalTask.objects)
                .filter(id=eval_task_id)
                .exists()
            ):
                return self._gm.bad_request(
                    f"EvalTask with id {eval_task_id} not found."
                )

            if query["eval_aggregation"] or query["span_aggregation"]:
                return self._usage_aggregation_response(
                    query, eval_task_id, eval_id_filter
                )

            # Match get_eval_task_logs' filter exactly — a task that shows
            # logs must also show usage, so `deleted` is not excluded here.
            base_qs = EvalLogger.objects.filter(eval_task_id=eval_task_id)
            if eval_id_filter:
                base_qs = base_qs.filter(custom_eval_config_id=eval_id_filter)

            total_runs = base_qs.count()
            period_qs, runs_period, window = usage.apply_window(
                base_qs,
                usage.resolve_window(
                    UsagePeriod(query["period"]),
                    query.get("start_date"),
                    query.get("end_date"),
                ),
                total_runs,
            )

            # Eager-load the related span, eval config (and through it the
            # template) and session, or the row loop below goes N+1.
            logs_qs = period_qs.select_related(
                "observation_span",
                "custom_eval_config",
                "custom_eval_config__eval_template",
                "trace_session",
            ).order_by("-created_at")

            paginator = ExtendedPageNumberPagination()
            paginator.page_size = query["page_size"]
            logs_page = paginator.paginate_queryset(logs_qs, self.request, view=self)
            log_items = [
                usage.build_log_item(
                    log,
                    _resolve_input_variables(
                        log.custom_eval_config, log.observation_span
                    ),
                )
                for log in logs_page
            ]

            response = {
                "eval_task_id": eval_task_id,
                "stats": usage.build_stats(period_qs, total_runs, runs_period),
                "evals": usage.list_configured_evals(eval_task_id),
                "chart": usage.build_chart(period_qs, window, runs_period),
                # Paginator native shape (matches eval_logs):
                # {count, next, previous, results, total_pages, current_page}
                "logs": paginator.get_paginated_response(log_items).data,
                # `period_used` diverges from `period_requested` when the
                # requested window held no runs and we widened to all-time;
                # the frontend shows a hint on that divergence.
                "period_requested": window.requested.value,
                "period_used": window.used.value,
                "start_date_used": window.start_date.isoformat(),
                "end_date_used": window.end_date.isoformat(),
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

    def _usage_aggregation_response(self, query, eval_task_id, eval_id_filter):
        """Aggregation-only payload — no chart, no logs, `period` not applied.

        Soft-deleted and session-target rows are excluded so the row set is
        the same whether or not a date range is supplied. Either bound may be
        given on its own; both scope on the span's `created_at`.
        """
        agg_qs = EvalLogger.objects.filter(
            eval_task_id=eval_task_id,
            deleted=False,
            observation_span_id__isnull=False,
        )
        if eval_id_filter:
            agg_qs = agg_qs.filter(custom_eval_config_id=eval_id_filter)
        if query.get("start_date"):
            agg_qs = agg_qs.filter(
                observation_span__created_at__gte=query["start_date"]
            )
        if query.get("end_date"):
            agg_qs = agg_qs.filter(
                observation_span__created_at__lte=query["end_date"]
            )

        response = {"eval_task_id": eval_task_id}
        if query["eval_aggregation"]:
            response["eval_aggregation"] = _compute_eval_aggregation(agg_qs)
        if query["span_aggregation"]:
            response["span_aggregation"] = _compute_span_aggregation(agg_qs)
        return self._gm.success_response(response)

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
