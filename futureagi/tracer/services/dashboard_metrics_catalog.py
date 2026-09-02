"""Business logic for the dashboard metrics catalog endpoint.

HTTP-free layer between the request boundary and the response: assembles the
unified list of system / eval / annotation / custom-attribute / custom-column
metrics for a workspace, and uses a short-TTL process-local cache when one is
configured. ``DashboardViewSet.metrics`` keeps only auth, param extraction,
filter/paginate, and response building.
"""

from collections.abc import Callable
from concurrent.futures import (
    ThreadPoolExecutor,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)
from contextlib import nullcontext
from dataclasses import dataclass
from uuid import UUID

import structlog
from django.conf import settings
from django.core.cache import cache, caches
from django.core.cache.backends.locmem import LocMemCache
from django.db import DatabaseError, connection, transaction

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project, ProjectSourceChoices
from tracer.services.annotation_label_source import AnnotationLabelScoresProjectPG
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

logger = structlog.get_logger(__name__)


METRICS_CATALOG_TIMEOUT_MS = settings.INTERACTIVE_READ_DEFAULT_WALL_MS
METRICS_CATALOG_EVAL_USAGE_QUERY_TIMEOUT_MS = (
    settings.DASHBOARD_METRICS_EVAL_USAGE_QUERY_TIMEOUT_MS
)
METRICS_CATALOG_EVAL_USAGE_LOOKBACK_DAYS = (
    settings.DASHBOARD_METRICS_EVAL_USAGE_LOOKBACK_DAYS
)


class MetricsCatalogUnavailable(RuntimeError):
    """A requested catalog family could not be read completely."""

    def __init__(self, family: str):
        super().__init__(f"dashboard metrics catalog family unavailable: {family}")
        self.family = family


@dataclass(frozen=True)
class _CatalogPageFamily:
    """One already-ordered, independently countable catalog segment.

    Families are arranged in the same order as ``_metric_catalog_sort_key``.
    ``count_rows`` must not materialize row payloads and ``read_rows`` must
    apply the requested database offset/limit before evaluation.
    """

    name: str
    count_rows: Callable[[], int]
    read_rows: Callable[[int, int], list[dict]]


def _execute_metrics_catalog_pg_query_with_deadline(
    deadline: ReadDeadline,
    execute,
    sql,
    params,
    many,
    context,
):
    """Shrink PostgreSQL's timeout before every catalog SQL statement."""

    remaining_ms = deadline.remaining_ms(floor_ms=1)
    # Bypass Django's wrapper stack for the control statement so this helper
    # does not recursively wrap its own timeout-control query.
    context["cursor"].cursor.execute(
        "SELECT set_config('statement_timeout', %s, true)",
        (str(remaining_ms),),
    )
    result = execute(sql, params, many, context)
    deadline.remaining_ms(floor_ms=1)
    return result


def _run_metrics_catalog_pg_read(deadline: ReadDeadline, family: str, read):
    """Materialize one PostgreSQL phase inside the request-owned deadline."""

    deadline.remaining_ms(METRICS_CATALOG_TIMEOUT_MS)
    if connection.vendor != "postgresql":
        try:
            result = read()
            deadline.remaining_ms(floor_ms=1)
            return result
        except ReadDeadlineExceeded:
            raise
        except MetricsCatalogUnavailable:
            raise
        except Exception as exc:
            raise MetricsCatalogUnavailable(family) from exc

    already_in_atomic_block = connection.in_atomic_block
    transaction_context = (
        nullcontext() if already_in_atomic_block else transaction.atomic()
    )

    def execute_with_remaining_timeout(execute, sql, params, many, context):
        return _execute_metrics_catalog_pg_query_with_deadline(
            deadline,
            execute,
            sql,
            params,
            many,
            context,
        )

    try:
        with transaction_context:
            with connection.execute_wrapper(execute_with_remaining_timeout):
                if not already_in_atomic_block:
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                result = read()
                deadline.remaining_ms(floor_ms=1)
        deadline.remaining_ms(floor_ms=1)
        return result
    except ReadDeadlineExceeded:
        raise
    except MetricsCatalogUnavailable:
        raise
    except DatabaseError as exc:
        raise MetricsCatalogUnavailable(family) from exc
    except Exception as exc:
        raise MetricsCatalogUnavailable(family) from exc


def _run_metrics_catalog_pg_snapshot(deadline: ReadDeadline, read):
    """Keep definition-family counts and slices on one stable PG snapshot."""

    deadline.remaining_ms(METRICS_CATALOG_TIMEOUT_MS)
    if connection.vendor != "postgresql" or connection.in_atomic_block:
        result = read()
        deadline.remaining_ms(floor_ms=1)
        return result

    try:
        with transaction.atomic():
            # This must be the transaction's first statement. Individual
            # family reads subsequently install the shrinking statement wall.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
            result = read()
            deadline.remaining_ms(floor_ms=1)
        deadline.remaining_ms(floor_ms=1)
        return result
    except (MetricsCatalogUnavailable, ReadDeadlineExceeded):
        raise
    except DatabaseError as exc:
        raise MetricsCatalogUnavailable("catalog_page") from exc
    except Exception as exc:
        raise MetricsCatalogUnavailable("catalog_page") from exc


def _can_use_metrics_catalog_cache() -> bool:
    """Only use the non-blocking, process-local cache on this request path.

    Production's remote cache can spend an unbounded socket wait outside the
    PostgreSQL statement deadline. Skipping it preserves the single 8.5-second
    interactive wall; the catalog remains correct and cache failure is
    intentionally degradable. Cache I/O is also skipped when a caller already
    owns a database transaction so no cache wait can hold that transaction.
    """

    try:
        return not connection.in_atomic_block and isinstance(
            caches["default"], LocMemCache
        )
    except Exception:
        logger.warning("metrics_catalog_cache_backend_check_failed", exc_info=True)
        return False


def _metric_matches_scope(metric: dict, *, category: str, source: str) -> bool:
    if category and metric.get("category") != category:
        return False
    if not source:
        return True
    return metric.get("source") == source or source in (metric.get("sources") or ())


_METRIC_CATEGORY_ORDER = {
    "system_metric": 0,
    "eval_metric": 1,
    "annotation_metric": 2,
    "custom_attribute": 3,
    "custom_column": 4,
}


def _metric_catalog_sort_key(metric: dict) -> tuple:
    return (
        _METRIC_CATEGORY_ORDER.get(str(metric.get("category") or ""), 99),
        str(metric.get("source") or ""),
        str(metric.get("display_name") or metric.get("name") or "").casefold(),
        str(metric.get("name") or ""),
        str(metric.get("property_id") or ""),
    )


_PROPERTY_KIND_BY_CATEGORY = {
    "system_metric": "system_attribute",
    "custom_attribute": "custom_attribute",
    # The default dashboard catalog is template-based. Per-project Observe
    # discovery overrides this with eval_config below.
    "eval_metric": "eval_template",
    "annotation_metric": "annotation",
    "custom_column": "dataset_column",
}


def _annotate_property_registry_identity(metrics: list[dict]) -> list[dict]:
    """Attach the stable logical identity shared by every property consumer.

    The legacy ``name``/``category`` pair remains the native filter compiler
    contract.  ``property_id`` prevents a system property and a customer
    attribute with the same display key (for example ``model``) from being
    treated as the same definition by pickers, saved-value hydration, AI
    grounding, or dashboard/widget builders.
    """

    for metric in metrics:
        category = str(metric.get("category") or "")
        property_kind = metric.pop(
            "_property_kind", None
        ) or _PROPERTY_KIND_BY_CATEGORY.get(category)
        name = str(metric.get("name") or "")
        if not property_kind or not name:
            continue
        if property_kind == "system_attribute":
            namespace = str(metric.get("source") or "all")
            property_id = f"{property_kind}:{namespace}:{name}"
        else:
            property_id = f"{property_kind}:{name}"
        metric["property_id"] = property_id
        metric["property_kind"] = property_kind
    return metrics


def _customer_attribute_metric_aliases():
    from tracer.utils.filters import FilterEngine

    aliases = {}
    for metric_id, definition in FilterEngine.VOICE_METRIC_DEFINITIONS.items():
        json_keys = definition.get("json_keys") or []
        if len(json_keys) == 1:
            aliases[json_keys[0]] = metric_id
    return aliases


def _suppress_customer_attribute_metric_aliases(metric_entries):
    aliases = _customer_attribute_metric_aliases()
    exposed_metric_names = {
        metric.get("name")
        for metric in metric_entries
        if metric.get("category") != "custom_attribute"
    }
    return [
        metric
        for metric in metric_entries
        if not (
            metric.get("category") == "custom_attribute"
            and aliases.get(metric.get("name")) in exposed_metric_names
        )
    ]


def _normalize_eval_output_type(template_config):
    """Normalize EvalTemplate config.output to the filter output type enum."""
    if not isinstance(template_config, dict):
        return "SCORE"
    output_type = (
        (template_config.get("output") or "")
        .upper()
        .replace("/", "_")
        .replace(" ", "_")
    )
    return (
        output_type
        if output_type in ("PASS_FAIL", "CHOICE", "CHOICES", "SCORE")
        else "SCORE"
    )


def _eval_template_metric_entry(template: dict) -> dict:
    output_type = _normalize_eval_output_type(template.get("config") or {})
    entry = {
        "name": str(template["id"]),
        "display_name": template["name"],
        "category": "eval_metric",
        "source": "all",
        "sources": ["all"],
        "output_type": output_type,
        "_property_kind": "eval_template",
    }
    choices = template.get("choices") or []
    if output_type in ("CHOICE", "CHOICES") and choices:
        entry["choices"] = choices
    elif output_type == "PASS_FAIL":
        entry["choices"] = ["Passed", "Failed"]
    return entry


def _eval_config_metric_entry(config: dict, *, source: str = "all") -> dict:
    template_config = config.get("eval_template__config") or {}
    output_type = _normalize_eval_output_type(template_config)
    entry = {
        "name": str(config["id"]),
        "display_name": config.get("_catalog_display_name")
        or config.get("name")
        or config.get("eval_template__name")
        or "",
        "category": "eval_metric",
        "source": source,
        "sources": [source],
        "output_type": output_type,
        "eval_template_id": str(config["eval_template_id"]),
        "_property_kind": "eval_config",
    }
    choices = config.get("eval_template__choices") or []
    if output_type in ("CHOICE", "CHOICES") and choices:
        entry["choices"] = choices
    elif output_type == "PASS_FAIL":
        entry["choices"] = ["Passed", "Failed"]
    return entry


def _annotation_label_metric_entry(annotation_label: dict) -> dict:
    label_type = annotation_label.get("type", "numeric")
    label_settings = annotation_label.get("settings") or {}
    metric_entry = {
        "name": str(annotation_label["id"]),
        "display_name": annotation_label["name"],
        "category": "annotation_metric",
        "source": "both",
        "sources": ["datasets", "traces"],
        "output_type": label_type,
    }
    if label_type == "categorical":
        legacy_choices = []
        choice_options = []
        for option in label_settings.get("options", []):
            if not isinstance(option, dict):
                continue
            raw_value = option.get("value")
            if raw_value in (None, ""):
                raw_value = option.get("label") or option.get("name")
            if raw_value in (None, ""):
                continue
            raw_label = option.get("label") or option.get("name")
            label = str(raw_label if raw_label not in (None, "") else raw_value)
            if raw_label not in (None, ""):
                legacy_choices.append(label)
            choice_options.append({"value": raw_value, "label": label})
        metric_entry["choices"] = legacy_choices
        metric_entry["choice_options"] = choice_options
    elif label_type == "thumbs_up_down":
        metric_entry["choices"] = ["Thumbs Up", "Thumbs Down"]
    return metric_entry


def _custom_column_metric_entry(column: dict) -> dict:
    return {
        "name": str(column["id"]),
        "display_name": column["name"],
        "category": "custom_column",
        "source": "datasets",
        "type": "number" if column["data_type"] != "boolean" else "boolean",
        "data_type": column["data_type"],
    }


def build_eval_metric_entries(
    eval_templates,
    project_ids,
    workspace,
    per_eval_config,
    *,
    deadline: ReadDeadline,
    filter_by_project: bool = False,
):
    """Build eval metric entries per template or per configured eval."""
    entries = []

    if per_eval_config:
        eval_cfg_qs = CustomEvalConfig.objects.filter(deleted=False).select_related(
            "eval_template"
        )
        if filter_by_project:
            eval_cfg_qs = eval_cfg_qs.filter(project_id__in=project_ids)
        else:
            eval_cfg_qs = eval_cfg_qs.filter(project__workspace=workspace)

        eval_configs = _run_metrics_catalog_pg_read(
            deadline,
            "eval_metrics",
            lambda: list(eval_cfg_qs.order_by("name", "id")),
        )
        for cfg in eval_configs:
            tmpl = cfg.eval_template
            if not tmpl or getattr(tmpl, "deleted", False):
                continue
            entries.append(
                _eval_config_metric_entry(
                    {
                        "id": cfg.id,
                        "name": cfg.name,
                        "eval_template_id": tmpl.id,
                        "eval_template__name": tmpl.name,
                        "eval_template__config": tmpl.config,
                        "eval_template__choices": tmpl.choices,
                    }
                )
            )
        return entries

    for et in eval_templates:
        entries.append(_eval_template_metric_entry(et))
    return entries


def build_simulation_eval_metric_entries(
    agent_definition_id,
    workspace,
    *,
    deadline: ReadDeadline,
):
    """Build simulation eval filter entries scoped to an agent definition."""
    if not agent_definition_id:
        return []

    from simulate.models import SimulateEvalConfig

    entries = []
    eval_configs = _run_metrics_catalog_pg_read(
        deadline,
        "simulation_eval_metrics",
        lambda: list(
            SimulateEvalConfig.objects.filter(
                run_test__agent_definition_id=agent_definition_id,
                run_test__organization=workspace.organization,
                run_test__workspace=workspace,
                run_test__deleted=False,
                deleted=False,
            )
            .select_related("eval_template")
            .order_by("name", "eval_template__name", "id")
            .distinct()
        ),
    )

    for cfg in eval_configs:
        tmpl = cfg.eval_template
        if not tmpl or getattr(tmpl, "deleted", False):
            continue
        entries.append(
            _eval_config_metric_entry(
                {
                    "id": cfg.id,
                    "name": cfg.name,
                    "eval_template_id": tmpl.id,
                    "eval_template__name": tmpl.name,
                    "eval_template__config": tmpl.config,
                    "eval_template__choices": tmpl.choices,
                },
                source="simulation",
            )
        )
    return entries


def _resolve_metrics_catalog_project_scope(
    workspace,
    project_ids_param: str,
    *,
    include_workspace_projects: bool,
    deadline: ReadDeadline,
    eligible_trace_type: str | None = None,
) -> tuple[list[str], bool]:
    """Resolve explicit project ids once without widening an empty match.

    The boolean records whether the caller supplied an explicit scope.  It is
    deliberately independent from the number of authorized matches so an
    all-foreign request cannot fall through to workspace-wide definitions.
    """

    requested_project_ids = [
        value.strip() for value in project_ids_param.split(",") if value.strip()
    ]
    # A workspace foreign key is not sufficient tenant authority on legacy or
    # inconsistent rows.  Every catalog scope must prove the denormalized
    # project organization matches the already-authorized workspace too.
    project_filters: dict[str, object] = {
        "workspace": workspace,
        "organization_id": workspace.organization_id,
    }
    if eligible_trace_type:
        project_filters["trace_type"] = eligible_trace_type
    if requested_project_ids:
        workspace_project_ids = set(
            _run_metrics_catalog_pg_read(
                deadline,
                "project_scope",
                lambda: [
                    str(project_id)
                    for project_id in Project.no_workspace_objects.filter(
                        **project_filters,
                        id__in=requested_project_ids,
                    )
                    .order_by("id")
                    .values_list("id", flat=True)
                ],
            )
        )
        return (
            [
                project_id
                for project_id in requested_project_ids
                if project_id in workspace_project_ids
            ],
            True,
        )

    if include_workspace_projects:
        return (
            _run_metrics_catalog_pg_read(
                deadline,
                "project_scope",
                lambda: [
                    str(project_id)
                    for project_id in Project.no_workspace_objects.filter(
                        **project_filters
                    )
                    .order_by("id")
                    .values_list("id", flat=True)
                ],
            ),
            False,
        )
    return [], False


def resolve_property_catalog_project_scope(
    workspace,
    project_ids: list[str] | tuple[str, ...],
    *,
    include_workspace_projects: bool = False,
    deadline: ReadDeadline,
) -> list[str]:
    """Authorize a unified-catalog project scope without silent narrowing.

    The ClickHouse definition reader owns no authorization logic. Every UUID
    carried into its visibility predicate must first be proven to belong to
    the already-authorized workspace. Unlike the legacy catalog builder,
    malformed, oversized, and mixed valid/foreign scopes are rejected instead
    of silently narrowed. This rollout is qualified for Observe projects;
    workspace reads materialize that complete eligible PG set so the
    activation can prove full coverage.
    """

    raw_project_ids = list(project_ids)
    if len(raw_project_ids) > RUNTIME_LIMITS.max_projects:
        raise ValueError(
            f"At most {RUNTIME_LIMITS.max_projects} project_ids may be searched at once"
        )
    try:
        requested = list(
            dict.fromkeys(str(UUID(str(project_id))) for project_id in raw_project_ids)
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Some project_ids are invalid") from exc
    if not requested:
        if not include_workspace_projects:
            return []
        resolved, explicit = _resolve_metrics_catalog_project_scope(
            workspace,
            "",
            include_workspace_projects=True,
            deadline=deadline,
            eligible_trace_type="observe",
        )
        if explicit:
            raise MetricsCatalogUnavailable("project_scope")
        return sorted(resolved)
    resolved, explicit = _resolve_metrics_catalog_project_scope(
        workspace,
        ",".join(requested),
        include_workspace_projects=False,
        deadline=deadline,
        eligible_trace_type="observe",
    )
    if not explicit or set(resolved) != set(requested):
        raise ValueError("Some project_ids are invalid")
    return sorted(resolved)


def resolve_property_catalog_agent_scope(
    workspace,
    agent_definition_id: str,
    *,
    deadline: ReadDeadline,
) -> str:
    """Authorize one simulation-agent visibility ID before ClickHouse use."""

    if not agent_definition_id:
        return ""

    from simulate.models import AgentDefinition

    resolved = _run_metrics_catalog_pg_read(
        deadline,
        "agent_definition_scope",
        lambda: list(
            AgentDefinition.objects.filter(
                id=agent_definition_id,
                organization=workspace.organization,
                workspace=workspace,
                deleted=False,
            )
            .order_by("id")
            .values_list("id", flat=True)[:1]
        ),
    )
    if len(resolved) != 1 or str(resolved[0]) != str(agent_definition_id):
        raise ValueError("agent_definition_id is invalid")
    return str(resolved[0])


def build_metrics_catalog(
    workspace,
    project_ids_param: str = "",
    agent_definition_id: str = "",
    per_eval_config: bool = False,
    include_custom_attributes: bool = True,
    category: str = "",
    source: str = "",
    deadline: ReadDeadline | None = None,
    _resolved_project_scope: tuple[list[str], bool] | None = None,
):
    """Assemble the full unified metrics catalog for the workspace.

    Retained for the deprecated unpaged compatibility response and for the
    bounded custom-attribute compatibility segment. Explicit finite-definition
    pages use ``build_metrics_catalog_page`` and never assemble these dynamic
    families before slicing.
    """

    deadline = deadline or ReadDeadline.start(METRICS_CATALOG_TIMEOUT_MS)
    deadline.remaining_ms(METRICS_CATALOG_TIMEOUT_MS)
    category = str(category or "")
    source = str(source or "")

    def category_matches(value: str) -> bool:
        return not category or category == value

    def source_matches(*values: str) -> bool:
        return not source or source in values

    want_trace_system_metrics = category_matches("system_metric") and source_matches(
        "traces"
    )
    want_eval_metrics = category_matches("eval_metric") and source_matches("all")
    want_simulation_eval_metrics = category_matches("eval_metric") and source_matches(
        "simulation"
    )
    want_annotation_metrics = category_matches("annotation_metric") and source_matches(
        "traces", "datasets", "both"
    )
    want_custom_attributes = (
        include_custom_attributes
        and category_matches("custom_attribute")
        and source_matches("traces")
    )
    want_custom_columns = category_matches("custom_column") and source_matches(
        "datasets"
    )

    metrics = []

    # 1. Trace system metrics
    metrics.extend(
        [
            {
                "name": "project",
                "display_name": "Project",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            {
                "name": "latency",
                "display_name": "Latency",
                "category": "system_metric",
                "source": "traces",
                "type": "number",
                "unit": "ms",
            },
            {
                "name": "error_rate",
                "display_name": "Error Rate",
                "category": "system_metric",
                "source": "traces",
                "type": "number",
                "unit": "%",
            },
            {
                "name": "tokens",
                "display_name": "Tokens",
                "category": "system_metric",
                "source": "traces",
                "type": "number",
                "unit": "tokens",
            },
            {
                "name": "input_tokens",
                "display_name": "Input Tokens",
                "category": "system_metric",
                "source": "traces",
                "type": "number",
                "unit": "tokens",
            },
            {
                "name": "output_tokens",
                "display_name": "Output Tokens",
                "category": "system_metric",
                "source": "traces",
                "type": "number",
                "unit": "tokens",
            },
            {
                "name": "time_to_first_token",
                "display_name": "Time to First Token",
                "category": "system_metric",
                "source": "traces",
                "type": "number",
                "unit": "ms",
            },
            {
                "name": "cost",
                "display_name": "Cost",
                "category": "system_metric",
                "source": "traces",
                "type": "number",
                "unit": "$",
            },
            # Trace numeric: session & user counts
            {
                "name": "session_count",
                "display_name": "Session Count",
                "category": "system_metric",
                "source": "traces",
                "type": "number",
                "unit": "",
            },
            {
                "name": "user_count",
                "display_name": "User Count",
                "category": "system_metric",
                "source": "traces",
                "type": "number",
                "unit": "",
            },
            {
                "name": "trace_count",
                "display_name": "Trace Count",
                "category": "system_metric",
                "source": "traces",
                "type": "number",
                "unit": "",
            },
            {
                "name": "span_count",
                "display_name": "Span Count",
                "category": "system_metric",
                "source": "traces",
                "type": "number",
                "unit": "",
            },
            # Trace string dimensions for breakdown/filter
            {
                "name": "model",
                "display_name": "Model",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            {
                "name": "status",
                "display_name": "Status",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            {
                "name": "service_name",
                "display_name": "Service Name",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            {
                "name": "span_kind",
                "display_name": "Span Kind",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            {
                "name": "provider",
                "display_name": "Provider",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            {
                "name": "session",
                "display_name": "Session",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            {
                "name": "user",
                "display_name": "User",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            {
                "name": "user_id_type",
                "display_name": "User ID Type",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            # Prompt dimensions
            {
                "name": "prompt_name",
                "display_name": "Prompt Name",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            {
                "name": "prompt_version",
                "display_name": "Prompt Version",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            {
                "name": "prompt_label",
                "display_name": "Prompt Label",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            {
                "name": "tag",
                "display_name": "Tag",
                "category": "system_metric",
                "source": "traces",
                "type": "string",
                "unit": "",
            },
            # Relational boolean pseudo-columns. They are filter-only
            # dimensions: the dashboard compiler resolves them against the
            # authoritative eval/annotation stores rather than a spans field.
            {
                "name": "has_eval",
                "display_name": "Has Evaluation",
                "category": "system_metric",
                "source": "traces",
                "type": "boolean",
                "unit": "",
                "role": "dimension",
            },
            {
                "name": "has_annotation",
                "display_name": "Has Annotation",
                "category": "system_metric",
                "source": "traces",
                "type": "boolean",
                "unit": "",
                "role": "dimension",
            },
        ]
    )

    # Eval-specific dimensions (available across all sources)
    metrics.extend(
        [
            {
                "name": "dataset",
                "display_name": "Dataset",
                "category": "system_metric",
                "source": "all",
                "sources": ["all"],
                "type": "string",
                "unit": "",
            },
            {
                "name": "eval_source",
                "display_name": "Eval Source",
                "category": "system_metric",
                "source": "all",
                "sources": ["all"],
                "type": "string",
                "unit": "",
            },
        ]
    )

    # 2. Dataset system metrics
    metrics.extend(
        [
            {
                "name": "row_count",
                "display_name": "Row Count",
                "category": "system_metric",
                "source": "datasets",
                "type": "number",
                "unit": "",
            },
            {
                "name": "prompt_tokens",
                "display_name": "Prompt Tokens",
                "category": "system_metric",
                "source": "datasets",
                "type": "number",
                "unit": "tokens",
            },
            {
                "name": "completion_tokens",
                "display_name": "Completion Tokens",
                "category": "system_metric",
                "source": "datasets",
                "type": "number",
                "unit": "tokens",
            },
            {
                "name": "total_tokens",
                "display_name": "Total Tokens",
                "category": "system_metric",
                "source": "datasets",
                "type": "number",
                "unit": "tokens",
            },
            {
                "name": "response_time",
                "display_name": "Response Time",
                "category": "system_metric",
                "source": "datasets",
                "type": "number",
                "unit": "ms",
            },
            {
                "name": "cell_error_rate",
                "display_name": "Cell Error Rate",
                "category": "system_metric",
                "source": "datasets",
                "type": "number",
                "unit": "%",
            },
        ]
    )

    # 2b. Dataset breakdown/filter dimensions (string)
    metrics.extend(
        [
            {
                "name": "dataset",
                "display_name": "Dataset",
                "category": "system_metric",
                "source": "datasets",
                "type": "string",
                "unit": "",
            },
            {
                "name": "eval_template",
                "display_name": "Eval Template",
                "category": "system_metric",
                "source": "datasets",
                "type": "string",
                "unit": "",
            },
            {
                "name": "column_name",
                "display_name": "Column Name",
                "category": "system_metric",
                "source": "datasets",
                "type": "string",
                "unit": "",
            },
            {
                "name": "column_source",
                "display_name": "Column Source",
                "category": "system_metric",
                "source": "datasets",
                "type": "string",
                "unit": "",
            },
            {
                "name": "cell_status",
                "display_name": "Cell Status",
                "category": "system_metric",
                "source": "datasets",
                "type": "string",
                "unit": "",
            },
        ]
    )

    # Resolve the request scope before loading dynamic families. An explicit
    # project scope remains explicit even when every id is unauthorized: it
    # must never fall through to workspace-wide definitions.
    if _resolved_project_scope is None:
        project_ids, filter_by_project = _resolve_metrics_catalog_project_scope(
            workspace,
            project_ids_param,
            include_workspace_projects=want_custom_attributes,
            deadline=deadline,
        )
    else:
        project_ids, filter_by_project = _resolved_project_scope
    if want_trace_system_metrics and filter_by_project and project_ids:
        has_non_simulator_project = _run_metrics_catalog_pg_read(
            deadline,
            "project_scope",
            lambda: (
                Project.objects.filter(id__in=project_ids)
                .exclude(source=ProjectSourceChoices.SIMULATOR.value)
                .exists()
            ),
        )
        if not has_non_simulator_project:
            metrics.append(
                {
                    "name": "agent_talk_percentage",
                    "display_name": "Agent Talk %",
                    "category": "system_metric",
                    "source": "traces",
                    "type": "number",
                    "unit": "%",
                }
            )

    # Span attributes are independent of the definition families, so retain
    # their concurrency while binding both the CH timeout and Future wait to
    # this same request-owned wall.
    def _discover_span_attributes():
        analytics = V2AnalyticsQueryService()
        rows = analytics.get_span_attribute_keys_ch_for_projects(
            project_ids,
            recent_days=None,
            timeout_ms=deadline.remaining_ms(METRICS_CATALOG_TIMEOUT_MS),
            outer_limit=settings.DASHBOARD_METRICS_ATTRIBUTE_KEY_LIMIT,
        )
        attrs = []
        for row in rows:
            key = row.get("key", "")
            if key:
                attrs.append({"key": key, "type": row.get("type", "string")})
        deadline.remaining_ms(floor_ms=1)
        return sorted(attrs, key=lambda item: (item["key"], item["type"]))

    attribute_executor = None
    attribute_future = None
    if want_custom_attributes and project_ids:
        try:
            attribute_executor = ThreadPoolExecutor(
                max_workers=settings.DASHBOARD_METRICS_ATTRIBUTE_WORKERS
            )
            attribute_future = attribute_executor.submit(_discover_span_attributes)
        except Exception as exc:
            if attribute_executor is not None:
                attribute_executor.shutdown(wait=False, cancel_futures=True)
            raise MetricsCatalogUnavailable("custom_attributes") from exc

    try:
        # Eval definitions. Usage discovery is only an optimization: a failure
        # is observable but falls back to the complete configured definition
        # set, subject to the same remaining wall.
        if want_eval_metrics:
            try:
                from model_hub.models.evals_metric import EvalTemplate

                used_template_ids = []
                if filter_by_project and not per_eval_config:
                    candidate_config_ids = _run_metrics_catalog_pg_read(
                        deadline,
                        "eval_metrics",
                        lambda: list(
                            CustomEvalConfig.objects.filter(
                                project_id__in=project_ids,
                                deleted=False,
                            )
                            .order_by("id")
                            .values_list("id", flat=True)
                        ),
                    )
                    if candidate_config_ids:
                        try:
                            analytics = V2AnalyticsQueryService()
                            used_template_ids = analytics.get_eval_config_ids_for_candidates_ch(
                                [str(value) for value in candidate_config_ids],
                                timeout_ms=deadline.remaining_ms(
                                    METRICS_CATALOG_EVAL_USAGE_QUERY_TIMEOUT_MS
                                ),
                                window_days=METRICS_CATALOG_EVAL_USAGE_LOOKBACK_DAYS,
                            )
                            deadline.remaining_ms(floor_ms=1)
                        except Exception as exc:
                            logger.warning(
                                "dashboard_metrics_catalog_optimization_fallback",
                                optimization="eval_usage",
                                fallback="configured_eval_definitions",
                                error_type=type(exc).__name__,
                            )
                            used_template_ids = []

                if not per_eval_config:
                    if not used_template_ids and filter_by_project:
                        used_template_ids = _run_metrics_catalog_pg_read(
                            deadline,
                            "eval_metrics",
                            lambda: list(
                                CustomEvalConfig.objects.filter(
                                    project_id__in=project_ids,
                                    deleted=False,
                                )
                                .order_by("eval_template_id")
                                .values_list("eval_template_id", flat=True)
                                .distinct()
                            ),
                        )
                    elif used_template_ids and filter_by_project:
                        used_template_ids = _run_metrics_catalog_pg_read(
                            deadline,
                            "eval_metrics",
                            lambda: list(
                                CustomEvalConfig.objects.filter(
                                    id__in=used_template_ids,
                                    deleted=False,
                                )
                                .order_by("eval_template_id")
                                .values_list("eval_template_id", flat=True)
                                .distinct()
                            ),
                        )

                    if used_template_ids:
                        eval_template_query = (
                            EvalTemplate.no_workspace_objects.filter(
                                id__in=used_template_ids,
                                deleted=False,
                            )
                            .order_by("name", "id")
                            .values("id", "name", "config", "choices")
                        )
                    elif filter_by_project:
                        eval_template_query = EvalTemplate.objects.none().values(
                            "id", "name", "config", "choices"
                        )
                    else:
                        eval_template_query = (
                            EvalTemplate.objects.filter(
                                organization=workspace.organization,
                                deleted=False,
                            )
                            .order_by("name", "id")
                            .values("id", "name", "config", "choices")
                        )
                    eval_templates = _run_metrics_catalog_pg_read(
                        deadline,
                        "eval_metrics",
                        lambda: list(eval_template_query),
                    )
                else:
                    eval_templates = []

                metrics.extend(
                    build_eval_metric_entries(
                        eval_templates=eval_templates,
                        project_ids=project_ids,
                        workspace=workspace,
                        per_eval_config=per_eval_config,
                        deadline=deadline,
                        filter_by_project=filter_by_project,
                    )
                )
            except (MetricsCatalogUnavailable, ReadDeadlineExceeded):
                raise
            except Exception as exc:
                raise MetricsCatalogUnavailable("eval_metrics") from exc

        if want_simulation_eval_metrics and agent_definition_id:
            try:
                metrics.extend(
                    build_simulation_eval_metric_entries(
                        agent_definition_id,
                        workspace,
                        deadline=deadline,
                    )
                )
            except (MetricsCatalogUnavailable, ReadDeadlineExceeded):
                raise
            except Exception as exc:
                raise MetricsCatalogUnavailable("simulation_eval_metrics") from exc

        if want_annotation_metrics:
            try:
                from django.db.models import Q

                from model_hub.models.develop_annotations import AnnotationsLabels

                if filter_by_project:
                    label_source = AnnotationLabelScoresProjectPG()
                    used_label_ids: set = set()
                    for project_id in project_ids:
                        used_label_ids.update(
                            _run_metrics_catalog_pg_read(
                                deadline,
                                "annotation_metrics",
                                lambda project_id=project_id: list(
                                    label_source.label_ids_for_project(project_id)
                                ),
                            )
                        )
                    annotation_label_query = (
                        AnnotationsLabels.no_workspace_objects.filter(
                            Q(organization=workspace.organization),
                            Q(workspace__isnull=True) | Q(workspace=workspace),
                        )
                        .filter(
                            Q(id__in=used_label_ids) | Q(project_id__in=project_ids),
                        )
                        .distinct()
                        .order_by("name", "id")
                        .values("id", "name", "type", "settings")
                    )
                else:
                    annotation_label_query = (
                        AnnotationsLabels.no_workspace_objects.filter(
                            Q(organization=workspace.organization),
                            Q(workspace__isnull=True) | Q(workspace=workspace),
                        )
                        .order_by("name", "id")
                        .values("id", "name", "type", "settings")
                    )
                annotation_labels = _run_metrics_catalog_pg_read(
                    deadline,
                    "annotation_metrics",
                    lambda: list(annotation_label_query),
                )

                for annotation_label in annotation_labels:
                    metrics.append(_annotation_label_metric_entry(annotation_label))
            except (MetricsCatalogUnavailable, ReadDeadlineExceeded):
                raise
            except Exception as exc:
                raise MetricsCatalogUnavailable("annotation_metrics") from exc

        if attribute_future is not None:
            try:
                custom_attributes = attribute_future.result(
                    timeout=deadline.remaining_ms(METRICS_CATALOG_TIMEOUT_MS) / 1_000
                )
            except (FutureTimeoutError, ReadDeadlineExceeded) as exc:
                raise MetricsCatalogUnavailable("custom_attributes") from exc
            except Exception as exc:
                raise MetricsCatalogUnavailable("custom_attributes") from exc
            for attribute in custom_attributes:
                metrics.append(
                    {
                        "name": attribute["key"],
                        "display_name": attribute["key"],
                        "category": "custom_attribute",
                        "source": "traces",
                        "type": attribute.get("type", "string"),
                    }
                )

        if want_custom_columns:
            try:
                from model_hub.models.develop_dataset import Column

                columns = _run_metrics_catalog_pg_read(
                    deadline,
                    "custom_columns",
                    lambda: list(
                        Column.no_workspace_objects.filter(
                            dataset__workspace=workspace,
                            dataset__deleted=False,
                            data_type__in=["float", "integer", "boolean"],
                        )
                        .order_by("name", "id")
                        .values("id", "name", "data_type")
                        .distinct()
                    ),
                )
                for column in columns:
                    metrics.append(_custom_column_metric_entry(column))
            except (MetricsCatalogUnavailable, ReadDeadlineExceeded):
                raise
            except Exception as exc:
                raise MetricsCatalogUnavailable("custom_columns") from exc
    finally:
        if attribute_executor is not None:
            attribute_executor.shutdown(wait=False, cancel_futures=True)

    # 8. Simulation system metrics (numeric — for aggregation)
    metrics.extend(
        [
            {
                "name": "call_count",
                "display_name": "Call Count",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "",
            },
            {
                "name": "success_rate",
                "display_name": "Success Rate",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "%",
            },
            {
                "name": "failure_rate",
                "display_name": "Failure Rate",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "%",
            },
            {
                "name": "duration",
                "display_name": "Duration",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "s",
            },
            {
                "name": "response_time",
                "display_name": "Response Time",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "ms",
            },
            {
                "name": "agent_latency",
                "display_name": "Agent Latency",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "ms",
            },
            {
                "name": "stt_latency",
                "display_name": "STT Latency",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "ms",
            },
            {
                "name": "tts_latency",
                "display_name": "TTS Latency",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "ms",
            },
            {
                "name": "llm_latency",
                "display_name": "LLM Latency",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "ms",
            },
            {
                "name": "total_cost",
                "display_name": "Total Cost",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "cents",
            },
            {
                "name": "stt_cost",
                "display_name": "STT Cost",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "cents",
            },
            {
                "name": "tts_cost",
                "display_name": "TTS Cost",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "cents",
            },
            {
                "name": "llm_cost",
                "display_name": "LLM Cost",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "cents",
            },
            {
                "name": "customer_cost",
                "display_name": "Customer Cost",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "cents",
            },
            {
                "name": "overall_score",
                "display_name": "Overall Score",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "",
            },
            {
                "name": "message_count",
                "display_name": "Message Count",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "",
            },
            {
                "name": "user_interruptions",
                "display_name": "User Interruptions",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "",
            },
            {
                "name": "user_interruption_rate",
                "display_name": "User Interruption Rate",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "/min",
            },
            {
                "name": "ai_interruptions",
                "display_name": "AI Interruptions",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "",
            },
            {
                "name": "ai_interruption_rate",
                "display_name": "AI Interruption Rate",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "/min",
            },
            {
                "name": "stop_time_after_interruption",
                "display_name": "Stop Time After Interruption",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "ms",
            },
            {
                "name": "user_wpm",
                "display_name": "User WPM",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "wpm",
            },
            {
                "name": "bot_wpm",
                "display_name": "Bot WPM",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "wpm",
            },
            {
                "name": "talk_ratio",
                "display_name": "Talk Ratio",
                "category": "system_metric",
                "source": "simulation",
                "type": "number",
                "unit": "%",
            },
        ]
    )

    # 8b. Simulation breakdown/filter dimensions (string — for grouping & filtering)
    metrics.extend(
        [
            {
                "name": "simulation",
                "display_name": "Simulation",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "scenario",
                "display_name": "Scenario",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "agent_definition",
                "display_name": "Agent",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "agent_version",
                "display_name": "Agent Version",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "persona",
                "display_name": "Persona",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "call_type",
                "display_name": "Call Type",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "status",
                "display_name": "Status",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "scenario_type",
                "display_name": "Scenario Type",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "ended_reason",
                "display_name": "Ended Reason",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "run_test",
                "display_name": "Test",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "test_execution",
                "display_name": "Test Run",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            # Persona attributes for breakdown/filtering
            {
                "name": "persona_gender",
                "display_name": "Persona Gender",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "persona_age_group",
                "display_name": "Persona Age Group",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "persona_location",
                "display_name": "Persona Location",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "persona_profession",
                "display_name": "Persona Profession",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "persona_personality",
                "display_name": "Persona Personality",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "persona_communication_style",
                "display_name": "Persona Communication Style",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "persona_accent",
                "display_name": "Persona Accent",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "persona_language",
                "display_name": "Persona Language",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
            {
                "name": "persona_conversation_speed",
                "display_name": "Persona Conversation Speed",
                "category": "system_metric",
                "source": "simulation",
                "type": "string",
                "unit": "",
            },
        ]
    )

    for metric in metrics:
        if (
            metric.get("source") in ("simulation", "datasets")
            and metric.get("type") == "string"
        ):
            metric["allowed_aggregations"] = ["count", "count_distinct"]

    metrics = _suppress_customer_attribute_metric_aliases(metrics)
    metrics = _annotate_metric_roles(metrics)
    metrics = _annotate_property_registry_identity(metrics)
    metrics = [
        metric
        for metric in metrics
        if _metric_matches_scope(metric, category=category, source=source)
    ]
    metrics.sort(key=_metric_catalog_sort_key)
    deadline.remaining_ms(floor_ms=1)

    return metrics


_COUNT_METRIC_RENAMES: dict[str, str] = {
    "user_count": "Users",
    "session_count": "Sessions",
    "trace_count": "Traces",
    "span_count": "Spans",
}


def _annotate_metric_roles(metrics: list[dict]) -> list[dict]:
    """Tag every catalog entry with a ``role`` so the frontend picker can
    filter metric-mode results down to Y-axis-suitable entries.

    ``metric``    — numeric aggregatable, shows in the metric picker.
    ``dimension`` — string-typed breakdown/filter target, hidden from the
                    metric picker.

    Derived from ``type`` (not a name whitelist) unless an entry explicitly
    declares a role. This keeps relational boolean filter-only definitions out
    of the Y-axis picker without pretending they are strings.
    Entries without ``type`` (eval / annotation / custom_column) default to
    ``metric`` — they are all numeric aggregatable today.

    Also applies the ``user_count → Users`` family of display renames — the
    frontend already groups these under a "Users"/"Sessions" tab, so the
    old ``… Count`` suffix just doubled up on the tab label.
    """
    for m in metrics:
        name = m.get("name", "")
        if name in _COUNT_METRIC_RENAMES:
            m["display_name"] = _COUNT_METRIC_RENAMES[name]
        m["role"] = m.get("role") or (
            "dimension" if m.get("type") == "string" else "metric"
        )
    return metrics


def _catalog_family_requested(
    *,
    category: str,
    source: str,
    family_category: str,
    family_source: str,
    family_sources: tuple[str, ...] = (),
) -> bool:
    """Apply the public category/source semantics before building a family."""

    return _metric_matches_scope(
        {
            "category": family_category,
            "source": family_source,
            "sources": family_sources,
        },
        category=category,
        source=source,
    )


def _queryset_catalog_family(
    *,
    name: str,
    queryset,
    fields: tuple[str, ...],
    convert: Callable[[dict], dict],
    deadline: ReadDeadline,
) -> _CatalogPageFamily:
    """Adapt an ordered queryset without evaluating it before pagination."""

    row_queryset = queryset.values(*fields)

    def count_rows() -> int:
        return int(
            _run_metrics_catalog_pg_read(
                deadline,
                name,
                queryset.count,
            )
        )

    def read_rows(offset: int, limit: int) -> list[dict]:
        if limit <= 0:
            return []
        rows = _run_metrics_catalog_pg_read(
            deadline,
            name,
            lambda: list(row_queryset[offset : offset + limit]),
        )
        return [convert(row) for row in rows]

    return _CatalogPageFamily(
        name=name,
        count_rows=count_rows,
        read_rows=read_rows,
    )


def _in_memory_catalog_family(name: str, rows: list[dict]) -> _CatalogPageFamily:
    """Adapt the finite hard-coded system segment (and legacy attributes)."""

    return _CatalogPageFamily(
        name=name,
        count_rows=lambda: len(rows),
        read_rows=lambda offset, limit: rows[offset : offset + limit],
    )


def _paginate_catalog_families(
    families: list[_CatalogPageFamily],
    *,
    page: int,
    page_size: int,
    deadline: ReadDeadline,
) -> tuple[list[dict], int, bool]:
    """Count every requested family, then read only page-overlapping slices.

    Counting all families before fetching any payload preserves strict failure
    semantics: the API never returns a plausible partial page when a required
    family cannot prove its exact total.  Family ordering is the leading
    ``category, source`` portion of ``_metric_catalog_sort_key``; every family
    supplies its remaining name/id ordering at the database boundary.
    """

    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be positive")

    counts: list[int] = []
    for family in families:
        deadline.remaining_ms(floor_ms=1)
        try:
            count = int(family.count_rows())
        except (MetricsCatalogUnavailable, ReadDeadlineExceeded):
            raise
        except Exception as exc:
            raise MetricsCatalogUnavailable(family.name) from exc
        if count < 0:
            raise MetricsCatalogUnavailable(family.name)
        counts.append(count)

    total = sum(counts)
    page_start = (page - 1) * page_size
    page_end = min(page_start + page_size, total)
    if page_start >= total:
        deadline.remaining_ms(floor_ms=1)
        return [], total, False

    page_rows: list[dict] = []
    family_start = 0
    for family, count in zip(families, counts, strict=True):
        family_end = family_start + count
        overlap_start = max(page_start, family_start)
        overlap_end = min(page_end, family_end)
        if overlap_start < overlap_end:
            offset = overlap_start - family_start
            limit = overlap_end - overlap_start
            deadline.remaining_ms(floor_ms=1)
            try:
                rows = family.read_rows(offset, limit)
            except (MetricsCatalogUnavailable, ReadDeadlineExceeded):
                raise
            except Exception as exc:
                raise MetricsCatalogUnavailable(family.name) from exc
            # Definitions can change between COUNT and SELECT under the
            # default transaction isolation. Never label a short page exact.
            if len(rows) != limit:
                raise MetricsCatalogUnavailable(family.name)
            page_rows.extend(rows)
        family_start = family_end

    deadline.remaining_ms(floor_ms=1)
    return page_rows, total, page_end < total


def _catalog_search_and_order(queryset, *, display_name, search: str):
    """Attach the shared display/id keys used for SQL count and page slices."""

    from django.db.models import CharField, Q
    from django.db.models.functions import Cast, Lower

    queryset = queryset.annotate(
        _catalog_display_name=display_name,
        _catalog_name_text=Cast("id", output_field=CharField()),
    )
    if search:
        queryset = queryset.filter(
            Q(_catalog_display_name__icontains=search)
            | Q(_catalog_name_text__icontains=search)
        )
    return queryset.order_by(Lower("_catalog_display_name"), "id")


def build_metrics_catalog_page(
    workspace,
    *,
    page: int,
    page_size: int,
    project_ids_param: str = "",
    agent_definition_id: str = "",
    per_eval_config: bool = False,
    include_custom_attributes: bool = True,
    search: str = "",
    category: str = "",
    source: str = "",
    deadline: ReadDeadline | None = None,
) -> tuple[list[dict], int, bool]:
    """Build one exact finite-definition page without preloading all rows.

    The hard-coded system segment is small and remains in memory. Eval
    templates/configs, simulation evals, annotation labels, and dataset
    columns contribute an exact ``COUNT(*)`` but only issue a payload SELECT
    when their globally ordered segment overlaps the requested page. Active
    first-party callers exclude custom attributes because those are served by
    the signed cursor inventory; the compatibility path for callers that still
    request them retains the legacy bounded ClickHouse inventory.
    """

    from django.db.models import CharField, Exists, F, OuterRef, Q, Value
    from django.db.models.functions import Coalesce, NullIf

    deadline = deadline or ReadDeadline.start(METRICS_CATALOG_TIMEOUT_MS)
    deadline.remaining_ms(METRICS_CATALOG_TIMEOUT_MS)
    search = str(search or "").strip()
    category = str(category or "")
    source = str(source or "")

    want_system = _catalog_family_requested(
        category=category,
        source="",
        family_category="system_metric",
        family_source="",
    )
    want_eval = _catalog_family_requested(
        category=category,
        source=source,
        family_category="eval_metric",
        family_source="all",
        family_sources=("all",),
    )
    want_simulation_eval = _catalog_family_requested(
        category=category,
        source=source,
        family_category="eval_metric",
        family_source="simulation",
        family_sources=("simulation",),
    )
    want_annotations = _catalog_family_requested(
        category=category,
        source=source,
        family_category="annotation_metric",
        family_source="both",
        family_sources=("datasets", "traces"),
    )
    want_custom_attributes = include_custom_attributes and _catalog_family_requested(
        category=category,
        source=source,
        family_category="custom_attribute",
        family_source="traces",
    )
    want_custom_columns = _catalog_family_requested(
        category=category,
        source=source,
        family_category="custom_column",
        family_source="datasets",
    )

    project_ids, filter_by_project = _resolve_metrics_catalog_project_scope(
        workspace,
        project_ids_param,
        include_workspace_projects=want_custom_attributes,
        deadline=deadline,
    )

    families: list[_CatalogPageFamily] = []

    # Category 0: bounded hard-coded system definitions. ``build_metrics_catalog``
    # is deliberately scoped to this category, so it cannot evaluate any
    # definition queryset or ClickHouse attribute inventory.
    if want_system:
        system_metrics = build_metrics_catalog(
            workspace,
            project_ids_param=project_ids_param,
            agent_definition_id=agent_definition_id,
            per_eval_config=per_eval_config,
            include_custom_attributes=False,
            category="system_metric",
            source=source,
            deadline=deadline,
            _resolved_project_scope=(project_ids, filter_by_project),
        )
        if search:
            folded_search = search.casefold()
            system_metrics = [
                metric
                for metric in system_metrics
                if folded_search in str(metric.get("display_name") or "").casefold()
                or folded_search in str(metric.get("name") or "").casefold()
            ]
        families.append(_in_memory_catalog_family("system_metrics", system_metrics))

    # Category 1, source "all": configured eval identities. The page-first
    # endpoint intentionally uses authoritative PG definitions, including
    # definitions ready before their first historical result; it does not
    # materialize config ids merely to run the legacy CH usage optimization.
    if want_eval:
        if per_eval_config:
            eval_queryset = CustomEvalConfig.objects.filter(
                deleted=False,
                eval_template__deleted=False,
            )
            if filter_by_project:
                eval_queryset = eval_queryset.filter(project_id__in=project_ids)
            else:
                eval_queryset = eval_queryset.filter(project__workspace=workspace)
            display_name = Coalesce(
                NullIf("name", Value("")),
                "eval_template__name",
                output_field=CharField(),
            )
            eval_queryset = _catalog_search_and_order(
                eval_queryset,
                display_name=display_name,
                search=search,
            )
            families.append(
                _queryset_catalog_family(
                    name="eval_metrics",
                    queryset=eval_queryset,
                    fields=(
                        "id",
                        "name",
                        "eval_template_id",
                        "eval_template__name",
                        "eval_template__config",
                        "eval_template__choices",
                        "_catalog_display_name",
                    ),
                    convert=_eval_config_metric_entry,
                    deadline=deadline,
                )
            )
        else:
            from model_hub.models.evals_metric import EvalTemplate

            if filter_by_project:
                configured_template = CustomEvalConfig.objects.filter(
                    project_id__in=project_ids,
                    deleted=False,
                    eval_template_id=OuterRef("pk"),
                )
                # System templates can be organization-null. The configured
                # project is the tenant boundary here, matching the legacy
                # no-workspace template lookup without an id materialization.
                eval_queryset = EvalTemplate.no_workspace_objects.filter(
                    Exists(configured_template)
                )
            else:
                eval_queryset = EvalTemplate.objects.filter(
                    organization=workspace.organization,
                    deleted=False,
                )
            eval_queryset = _catalog_search_and_order(
                eval_queryset,
                display_name=F("name"),
                search=search,
            )
            families.append(
                _queryset_catalog_family(
                    name="eval_metrics",
                    queryset=eval_queryset,
                    fields=("id", "name", "config", "choices"),
                    convert=_eval_template_metric_entry,
                    deadline=deadline,
                )
            )

    # Category 1, source "simulation" follows source="all" globally.
    if want_simulation_eval and agent_definition_id:
        from simulate.models import SimulateEvalConfig

        simulation_queryset = SimulateEvalConfig.objects.filter(
            run_test__agent_definition_id=agent_definition_id,
            run_test__organization=workspace.organization,
            run_test__workspace=workspace,
            run_test__deleted=False,
            deleted=False,
            eval_template__deleted=False,
        )
        simulation_queryset = _catalog_search_and_order(
            simulation_queryset,
            display_name=Coalesce(
                NullIf("name", Value("")),
                "eval_template__name",
                output_field=CharField(),
            ),
            search=search,
        )
        families.append(
            _queryset_catalog_family(
                name="simulation_eval_metrics",
                queryset=simulation_queryset,
                fields=(
                    "id",
                    "name",
                    "eval_template_id",
                    "eval_template__name",
                    "eval_template__config",
                    "eval_template__choices",
                    "_catalog_display_name",
                ),
                convert=lambda row: _eval_config_metric_entry(
                    row,
                    source="simulation",
                ),
                deadline=deadline,
            )
        )

    # Category 2: label definitions. Project usage is expressed as an EXISTS
    # subquery against authoritative Score.project instead of materializing a
    # label-id set for every requested project.
    if want_annotations:
        from model_hub.models.develop_annotations import AnnotationsLabels

        annotation_queryset = AnnotationsLabels.no_workspace_objects.filter(
            Q(organization=workspace.organization),
            Q(workspace__isnull=True) | Q(workspace=workspace),
        )
        if filter_by_project:
            from model_hub.models.score import Score

            matching_score = Score.no_workspace_objects.filter(
                AnnotationLabelScoresProjectPG._trace_span_scope(),
                tracer_project_id__in=project_ids,
                label_id=OuterRef("pk"),
            )
            annotation_queryset = annotation_queryset.filter(
                Q(project_id__in=project_ids) | Exists(matching_score)
            )
        annotation_queryset = _catalog_search_and_order(
            annotation_queryset,
            display_name=F("name"),
            search=search,
        )
        families.append(
            _queryset_catalog_family(
                name="annotation_metrics",
                queryset=annotation_queryset,
                fields=("id", "name", "type", "settings"),
                convert=_annotation_label_metric_entry,
                deadline=deadline,
            )
        )

    # Category 3: compatibility only. First-party callers set
    # exclude_custom_attributes=true and use the exact signed cursor API.
    if want_custom_attributes:
        attribute_metrics = build_metrics_catalog(
            workspace,
            project_ids_param=project_ids_param,
            include_custom_attributes=True,
            category="custom_attribute",
            source="traces",
            deadline=deadline,
            _resolved_project_scope=(project_ids, filter_by_project),
        )
        if search:
            folded_search = search.casefold()
            attribute_metrics = [
                metric
                for metric in attribute_metrics
                if folded_search in str(metric.get("display_name") or "").casefold()
                or folded_search in str(metric.get("name") or "").casefold()
            ]
        families.append(
            _in_memory_catalog_family("custom_attributes", attribute_metrics)
        )

    # Category 4: numeric/boolean dataset columns.
    if want_custom_columns:
        from model_hub.models.develop_dataset import Column

        column_queryset = Column.no_workspace_objects.filter(
            dataset__workspace=workspace,
            dataset__deleted=False,
            data_type__in=["float", "integer", "boolean"],
        )
        column_queryset = _catalog_search_and_order(
            column_queryset,
            display_name=F("name"),
            search=search,
        )
        families.append(
            _queryset_catalog_family(
                name="custom_columns",
                queryset=column_queryset,
                fields=("id", "name", "data_type"),
                convert=_custom_column_metric_entry,
                deadline=deadline,
            )
        )

    metrics, total, has_more = _run_metrics_catalog_pg_snapshot(
        deadline,
        lambda: _paginate_catalog_families(
            families,
            page=page,
            page_size=page_size,
            deadline=deadline,
        ),
    )
    # Query-backed page rows have not passed through the legacy finalization
    # tail. These operations are idempotent for the already-finalized system
    # and compatibility-attribute rows.
    metrics = _annotate_metric_roles(metrics)
    metrics = _annotate_property_registry_identity(metrics)
    # Preserve the authoritative pre-slice family/SQL order. Re-sorting only
    # this page with Python ``casefold`` can disagree with PostgreSQL
    # ``Lower``/collation (for example, ``ss`` versus ``ß``), making the
    # concatenated result depend on page size.
    deadline.remaining_ms(floor_ms=1)
    return metrics, total, has_more


def get_cached_metrics_catalog(
    workspace,
    project_ids_param: str = "",
    agent_definition_id: str = "",
    per_eval_config: bool = False,
    include_custom_attributes: bool = True,
    category: str = "",
    source: str = "",
    deadline: ReadDeadline | None = None,
    ttl: int = 60,
):
    """Return the metrics catalog, using a short-TTL process-local cache.

    The catalog derives from workspace-scoped data (projects, eval templates,
    annotation labels, dataset columns, CH span-attribute keys) that evolves
    on the order of minutes, not seconds. Remote cache backends are skipped on
    this strict-deadline request path because their socket wait is outside the
    database statement budget.
    """
    deadline = deadline or ReadDeadline.start(METRICS_CATALOG_TIMEOUT_MS)
    deadline.remaining_ms(METRICS_CATALOG_TIMEOUT_MS)
    pids_key = ",".join(
        sorted(p.strip() for p in project_ids_param.split(",") if p.strip())
    )
    cache_key = (
        f"dashboard:metrics_catalog:v6:{workspace.id}:"
        f"{pids_key}:{agent_definition_id}:{int(per_eval_config)}:"
        f"{int(include_custom_attributes)}:{category}:{source}"
    )
    use_cache = _can_use_metrics_catalog_cache()
    metrics = None
    if use_cache:
        try:
            metrics = cache.get(cache_key)
        except Exception:
            logger.warning("metrics_catalog_cache_get_failed", exc_info=True)
    deadline.remaining_ms(floor_ms=1)
    if metrics is None:
        metrics = build_metrics_catalog(
            workspace,
            project_ids_param=project_ids_param,
            agent_definition_id=agent_definition_id,
            per_eval_config=per_eval_config,
            include_custom_attributes=include_custom_attributes,
            category=category,
            source=source,
            deadline=deadline,
        )
        # ``build_metrics_catalog`` has no partial-success return path. Only a
        # complete, deadline-proven catalog reaches this best-effort cache set.
        deadline.remaining_ms(floor_ms=1)
        if use_cache:
            try:
                cache.set(cache_key, metrics, timeout=ttl)
            except Exception:
                logger.warning("metrics_catalog_cache_set_failed", exc_info=True)
    deadline.remaining_ms(floor_ms=1)
    return metrics
