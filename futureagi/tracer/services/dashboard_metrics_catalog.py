"""Business logic for the dashboard metrics catalog endpoint.

HTTP-free layer between the request boundary and the response: assembles the
unified list of system / eval / annotation / custom-attribute / custom-column
metrics for a workspace, wraps the assembly in a short-TTL cache so
search-bar keystrokes on the frontend don't re-scan ClickHouse per
keystroke (TH-6519). ``DashboardViewSet.metrics`` keeps only auth, param
extraction, filter/paginate, and response building.
"""

from concurrent.futures import ThreadPoolExecutor

import structlog
from django.core.cache import cache

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project, ProjectSourceChoices
from tracer.services.clickhouse.client import (
    get_clickhouse_client,
    is_clickhouse_enabled,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.utils.sql_queries import SQL_query_handler

logger = structlog.get_logger(__name__)


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


def build_eval_metric_entries(eval_templates, project_ids, workspace, per_eval_config):
    """Build eval metric entries per template or per configured eval."""
    entries = []

    if per_eval_config:
        eval_cfg_qs = CustomEvalConfig.objects.filter(deleted=False).select_related(
            "eval_template"
        )
        if project_ids:
            eval_cfg_qs = eval_cfg_qs.filter(project_id__in=project_ids)
        else:
            eval_cfg_qs = eval_cfg_qs.filter(project__workspace=workspace)

        for cfg in eval_cfg_qs:
            tmpl = cfg.eval_template
            if not tmpl or getattr(tmpl, "deleted", False):
                continue
            output_type = _normalize_eval_output_type(tmpl.config or {})
            entry = {
                "name": str(cfg.id),
                "display_name": cfg.name or tmpl.name,
                "category": "eval_metric",
                "source": "all",
                "sources": ["all"],
                "output_type": output_type,
                "eval_template_id": str(tmpl.id),
            }
            if output_type in ("CHOICE", "CHOICES") and tmpl.choices:
                entry["choices"] = tmpl.choices
            elif output_type == "PASS_FAIL":
                entry["choices"] = ["Passed", "Failed"]
            entries.append(entry)
        return entries

    for et in eval_templates:
        output_type = _normalize_eval_output_type(et["config"] or {})
        entry = {
            "name": str(et["id"]),
            "display_name": et["name"],
            "category": "eval_metric",
            "source": "all",
            "sources": ["all"],
            "output_type": output_type,
        }
        choices = et.get("choices") or []
        if output_type in ("CHOICE", "CHOICES") and choices:
            entry["choices"] = choices
        elif output_type == "PASS_FAIL":
            entry["choices"] = ["Passed", "Failed"]
        entries.append(entry)
    return entries


def build_simulation_eval_metric_entries(agent_definition_id, workspace):
    """Build simulation eval filter entries scoped to an agent definition."""
    if not agent_definition_id:
        return []

    from simulate.models import SimulateEvalConfig

    entries = []
    eval_configs = (
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
    )

    for cfg in eval_configs:
        tmpl = cfg.eval_template
        if not tmpl or getattr(tmpl, "deleted", False):
            continue
        output_type = _normalize_eval_output_type(tmpl.config or {})
        entry = {
            "name": str(cfg.id),
            "display_name": cfg.name or tmpl.name,
            "category": "eval_metric",
            "source": "simulation",
            "sources": ["simulation"],
            "output_type": output_type,
            "eval_template_id": str(tmpl.id),
        }
        if output_type in ("CHOICE", "CHOICES") and tmpl.choices:
            entry["choices"] = tmpl.choices
        elif output_type == "PASS_FAIL":
            entry["choices"] = ["Passed", "Failed"]
        entries.append(entry)
    return entries


def build_metrics_catalog(
    workspace,
    project_ids_param: str = "",
    agent_definition_id: str = "",
    per_eval_config: bool = False,
):
    """Assemble the full unified metrics catalog for the workspace.

    Extracted from ``metrics()`` so the endpoint can wrap this call in a
    short-TTL Django cache — search-bar keystrokes and pagination reuse
    the cached list instead of re-scanning ClickHouse for span attribute
    keys, eval templates, annotation labels, and custom columns on every
    request (TH-6519).
    """

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

    # Project IDs for trace-scoped metrics (custom attrs, evals, annotations)
    # If caller passes project_ids, scope to those; otherwise all workspace projects.
    req_project_ids_str = project_ids_param
    req_project_ids = [
        pid.strip() for pid in req_project_ids_str.split(",") if pid.strip()
    ]

    workspace_project_ids = {
        str(pid)
        for pid in Project.objects.filter(workspace=workspace).values_list(
            "id", flat=True
        )
    }
    if req_project_ids:
        project_ids = [pid for pid in req_project_ids if pid in workspace_project_ids]
    else:
        project_ids = list(workspace_project_ids)

    filter_by_project = bool(req_project_ids and project_ids)

    if (
        filter_by_project
        and not Project.objects.filter(
            id__in=project_ids,
        )
        .exclude(
            source=ProjectSourceChoices.SIMULATOR.value,
        )
        .exists()
    ):
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

    # 3-6: Eval metrics, annotations, and span attributes are independent.
    # Span attribute discovery (CH) runs concurrently with the PG lookups.
    def _discover_span_attributes():
        attrs = []
        try:
            if is_clickhouse_enabled() and project_ids:
                analytics = AnalyticsQueryService()
                rows = analytics.get_span_attribute_keys_ch_for_projects(
                    project_ids,
                    recent_days=None,
                    timeout_ms=15000,
                    outer_limit=2000,
                )
                for r in rows:
                    k = r.get("key", "")
                    t = r.get("type", "string")
                    if k:
                        attrs.append({"key": k, "type": t})
            elif project_ids:
                for pid in project_ids:
                    keys = SQL_query_handler.get_span_attributes_for_project(pid)
                    for key in keys:
                        k = key if isinstance(key, str) else str(key)
                        if k not in [
                            a.get("key") if isinstance(a, dict) else a for a in attrs
                        ]:
                            attrs.append({"key": k, "type": "string"})
        except Exception as exc:
            logger.warning(
                "dashboard_span_attribute_discovery_failed",
                error=str(exc)[:200],
            )
        return attrs

    with ThreadPoolExecutor(max_workers=1) as pool:
        f_attrs = pool.submit(_discover_span_attributes)

    # 3. Eval metrics (PG-heavy chain, runs in main thread)
    try:
        from model_hub.models.evals_metric import EvalTemplate

        used_template_ids = []
        if is_clickhouse_enabled():
            from tracer.services.clickhouse.client import (
                get_clickhouse_client,
            )

            ch = get_clickhouse_client()

            if filter_by_project:
                result = ch.execute_read(
                    "SELECT DISTINCT toString(custom_eval_config_id) AS tid "
                    "FROM tracer_eval_logger "
                    "WHERE _peerdb_is_deleted = 0 AND deleted = 0 "
                    "AND custom_eval_config_id != toUUID('00000000-0000-0000-0000-000000000000') "
                    "AND created_at >= now() - INTERVAL 90 DAY "
                    "AND dictGet('trace_dict', 'project_id', trace_id) IN %(project_ids)s",
                    {"project_ids": project_ids},
                    timeout_ms=5000,
                )
            else:
                ws_id = str(workspace.id)
                result = ch.execute_read(
                    "SELECT DISTINCT source_id FROM usage_apicalllog "
                    "WHERE workspace_id = toUUID(%(ws_id)s) "
                    "AND status = 'success' AND length(source_id) > 0 "
                    "AND _peerdb_is_deleted = 0",
                    {"ws_id": ws_id},
                    timeout_ms=5000,
                )

            raw_rows = result[0] if isinstance(result, tuple) else result
            used_template_ids = [
                (
                    r[0]
                    if isinstance(r, (list, tuple))
                    else r.get("tid", r.get("source_id", ""))
                )
                for r in raw_rows
            ]

        if not used_template_ids and filter_by_project:
            used_template_ids = list(
                CustomEvalConfig.objects.filter(
                    project_id__in=project_ids,
                    deleted=False,
                )
                .values_list("eval_template_id", flat=True)
                .distinct()
            )
        elif used_template_ids and filter_by_project:
            used_template_ids = list(
                CustomEvalConfig.objects.filter(
                    id__in=used_template_ids,
                ).values_list("eval_template_id", flat=True)
            )

        if used_template_ids:
            eval_templates = EvalTemplate.no_workspace_objects.filter(
                id__in=used_template_ids,
                deleted=False,
            ).values("id", "name", "config", "choices")
        elif filter_by_project:
            eval_templates = EvalTemplate.objects.none().values(
                "id", "name", "config", "choices"
            )
        else:
            eval_templates = EvalTemplate.objects.filter(
                organization=workspace.organization,
                deleted=False,
            ).values("id", "name", "config", "choices")

        per_eval_config = per_eval_config
        metrics.extend(
            build_eval_metric_entries(
                eval_templates=eval_templates,
                project_ids=project_ids if filter_by_project else [],
                workspace=workspace,
                per_eval_config=per_eval_config,
            )
        )
    except (ImportError, Exception) as e:
        logger.warning(f"Failed to load eval templates: {e}")

    # 3b. Simulation eval metrics
    agent_definition_id = agent_definition_id or None
    if agent_definition_id:
        try:
            metrics.extend(
                build_simulation_eval_metric_entries(
                    agent_definition_id,
                    workspace,
                )
            )
        except (ImportError, Exception) as e:
            logger.warning(f"Failed to load simulation eval configs: {e}")

    # 4. Annotation metrics
    try:
        from django.db.models import Q

        from model_hub.models.develop_annotations import AnnotationsLabels

        if filter_by_project:
            from model_hub.models.score import Score

            used_label_ids = list(
                Score.objects.filter(
                    Q(project_id__in=project_ids)
                    | Q(trace__project_id__in=project_ids)
                    | Q(observation_span__project_id__in=project_ids)
                    | Q(trace_session__project_id__in=project_ids),
                    deleted=False,
                )
                .values_list("label_id", flat=True)
                .distinct()
            )
            if used_label_ids:
                annotation_labels = AnnotationsLabels.no_workspace_objects.filter(
                    id__in=used_label_ids,
                ).values("id", "name", "type", "settings")
            else:
                annotation_labels = (
                    AnnotationsLabels.no_workspace_objects.none().values(
                        "id", "name", "type", "settings"
                    )
                )
        else:
            annotation_labels = AnnotationsLabels.no_workspace_objects.filter(
                Q(organization=workspace.organization),
                Q(workspace__isnull=True) | Q(workspace=workspace),
            ).values("id", "name", "type", "settings")

        for al in annotation_labels:
            label_type = al.get("type", "numeric")
            settings = al.get("settings") or {}

            metric_entry = {
                "name": str(al["id"]),
                "display_name": al["name"],
                "category": "annotation_metric",
                "source": "both",
                "sources": ["datasets", "traces"],
                "output_type": label_type,
            }

            if label_type == "categorical":
                options = settings.get("options", [])
                metric_entry["choices"] = [
                    opt.get("label", "")
                    for opt in options
                    if isinstance(opt, dict) and opt.get("label")
                ]
            elif label_type == "thumbs_up_down":
                metric_entry["choices"] = ["Thumbs Up", "Thumbs Down"]

            metrics.append(metric_entry)
    except Exception:
        logger.exception("annotation_metrics_failed")

    # Collect span attributes from background thread
    custom_attributes = f_attrs.result()
    for attr in custom_attributes:
        k = attr["key"] if isinstance(attr, dict) else attr
        t = attr.get("type", "string") if isinstance(attr, dict) else "string"
        metrics.append(
            {
                "name": k,
                "display_name": k,
                "category": "custom_attribute",
                "source": "traces",
                "type": t,
            }
        )

    # 7. Custom columns (datasets only)
    try:
        from model_hub.models.develop_dataset import Column

        cols = (
            Column.no_workspace_objects.filter(
                dataset__workspace=workspace,
                dataset__deleted=False,
                data_type__in=["float", "integer", "boolean"],
            )
            .values("id", "name", "data_type")
            .distinct()
        )
        seen_names = set()
        for col in cols:
            if col["name"] in seen_names:
                continue
            seen_names.add(col["name"])
            metrics.append(
                {
                    "name": str(col["id"]),
                    "display_name": col["name"],
                    "category": "custom_column",
                    "source": "datasets",
                    "type": ("number" if col["data_type"] != "boolean" else "boolean"),
                    "data_type": col["data_type"],
                }
            )
    except (ImportError, Exception):
        pass

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

    Derived from ``type`` (not a name whitelist) so a new string-typed
    dimension added later can't silently become a selectable Y-axis metric.
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
        m["role"] = "dimension" if m.get("type") == "string" else "metric"
    return metrics


def get_cached_metrics_catalog(
    workspace,
    project_ids_param: str = "",
    agent_definition_id: str = "",
    per_eval_config: bool = False,
    ttl: int = 60,
):
    """Return the metrics catalog, using a short-TTL Django cache.

    The catalog derives from workspace-scoped data (projects, eval templates,
    annotation labels, dataset columns, CH span-attribute keys) that evolves
    on the order of minutes, not seconds. A 60s TTL keeps search-bar
    keystrokes near-instant (~2-10 ms warm vs ~1.7 s cold) while still
    surfacing newly-added evals / labels / projects quickly enough for the
    metric picker.
    """
    pids_key = ",".join(
        sorted(p.strip() for p in project_ids_param.split(",") if p.strip())
    )
    cache_key = (
        f"dashboard:metrics_catalog:v2:{workspace.id}:"
        f"{pids_key}:{agent_definition_id}:{int(per_eval_config)}"
    )
    try:
        metrics = cache.get(cache_key)
    except Exception:
        logger.warning("metrics_catalog_cache_get_failed", exc_info=True)
        metrics = None
    if metrics is None:
        metrics = build_metrics_catalog(
            workspace,
            project_ids_param=project_ids_param,
            agent_definition_id=agent_definition_id,
            per_eval_config=per_eval_config,
        )
        try:
            cache.set(cache_key, metrics, timeout=ttl)
        except Exception:
            logger.warning("metrics_catalog_cache_set_failed", exc_info=True)
    return metrics
