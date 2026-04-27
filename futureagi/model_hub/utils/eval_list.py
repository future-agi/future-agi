"""
Utility functions for the eval template list API (Phase 1).

Handles: queryset building, eval type derivation, output type derivation,
30-day chart data computation.
"""

from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING, Iterable

from django.db.models import Q, QuerySet
from django.utils import timezone

from agentic_eval.core_evals.fi_evals.eval_type import (
    FunctionEvalTypeId,
    FutureAgiEvalTypeId,
    GroundedEvalTypeId,
    LlmEvalTypeId,
)
from model_hub.models.choices import EvalOutputType, EvalTemplateType, OwnerChoices
from model_hub.types import EvalListFilters, ThirtyDayDataPoint
try:
    from ee.usage.models.usage import APICallStatusChoices
except ImportError:
    APICallStatusChoices = None

if TYPE_CHECKING:
    from model_hub.models.evals_metric import EvalTemplate

# Pre-compute sets for fast lookup
_FUNCTION_EVAL_IDS = {e.value for e in FunctionEvalTypeId}
_LLM_EVAL_IDS = {e.value for e in LlmEvalTypeId}
_FUTUREAGI_EVAL_IDS = {e.value for e in FutureAgiEvalTypeId}
_GROUNDED_EVAL_IDS = {e.value for e in GroundedEvalTypeId}

# LLM-based evaluators that use an LLM to judge (even if they have deterministic output)
# DeterministicEvaluator and RankingEvaluator are in FutureAgiEvalTypeId but they're
# LLM-based evaluators that use structured prompts — NOT code/function evals.
_LLM_BASED_EVAL_IDS = (
    _LLM_EVAL_IDS
    | _FUTUREAGI_EVAL_IDS  # DeterministicEvaluator, RankingEvaluator
    | _GROUNDED_EVAL_IDS  # AnswerSimilarity
    | {
        # Additional LLM-based evaluators not in the enum files
        "PerplexityEvaluator",
        "OutputEvaluator",
        "ChunkUtilization",
        "ChunkAttribution",
        "ConversationResolution",
        "ImageInstructionEvaluator",
        "AudioTranscriptionEvaluator",
        "ContextSimilarity",
        "CustomPrompt",
    }
)

# Tags that indicate agent-type evals
_AGENT_TAGS = {"agent", "agentic", "agent_eval"}
# Tags that indicate code/function-type evals (NOT "deterministic" — that's an LLM output type)
_CODE_TAGS = {"code", "function"}

# Mapping from config output values to our normalized output types
_OUTPUT_TYPE_MAP = {
    EvalOutputType.PASS_FAIL.value: "pass_fail",
    EvalOutputType.SCORE.value: "percentage",
    EvalOutputType.NUMERIC.value: "percentage",
    EvalOutputType.REASON.value: "percentage",
    EvalOutputType.CHOICES.value: "deterministic",
    EvalOutputType.EMPTY.value: "percentage",
}


def derive_eval_type(template: "EvalTemplate") -> str:
    """
    Derive the eval type (llm/code/agent) from an EvalTemplate.

    Uses the dedicated eval_type field if set.
    Falls back to tag/config-based detection for backward compatibility.
    For composites, returns the union of child eval types (e.g. "code, llm").
    """
    # Composite: return union of child eval types
    if getattr(template, "template_type", "single") == "composite":
        return _derive_composite_eval_type(template)

    # Prefer the dedicated field (set by migration 0077+)
    if hasattr(template, "eval_type") and template.eval_type:
        return template.eval_type

    # Fallback: derive from tags and config (pre-migration records)
    config = template.config or {}
    tags = {t.lower() for t in (template.eval_tags or [])}
    eval_type_id = config.get("eval_type_id", "")

    if tags & _AGENT_TAGS or eval_type_id == "AgentEvaluator":
        return "agent"

    if eval_type_id:
        if eval_type_id in _FUNCTION_EVAL_IDS:
            return "code"
        if eval_type_id in _LLM_BASED_EVAL_IDS:
            return "llm"

    if tags & _CODE_TAGS:
        return "code"

    return "llm"


def _derive_composite_eval_type(template: "EvalTemplate") -> str:
    """Return comma-separated union of child eval types for a composite."""
    from model_hub.models.evals_metric import CompositeEvalChild

    child_types = list(
        CompositeEvalChild.objects.filter(parent=template, deleted=False)
        .select_related("child")
        .values_list("child__eval_type", flat=True)
    )
    unique = sorted(set(t or "llm" for t in child_types))
    return ", ".join(unique) if unique else "composite"


def derive_output_type(template: "EvalTemplate") -> str:
    """
    Derive the normalized output type from an EvalTemplate's config.

    Maps:
    - "Pass/Fail" -> "pass_fail"
    - "score" / "numeric" / "reason" / "" -> "percentage"
    - "choices" -> "deterministic"
    For composites, returns the composite_child_axis mapped to output type.
    """
    # Composite: use the axis as the output type
    if getattr(template, "template_type", "single") == "composite":
        axis = getattr(template, "composite_child_axis", "") or ""
        axis_map = {
            "pass_fail": "pass_fail",
            "percentage": "percentage",
            "choices": "deterministic",
            "code": "pass_fail",
        }
        return axis_map.get(axis, "percentage")

    config = template.config or {}
    output = config.get("output", "")
    return _OUTPUT_TYPE_MAP.get(output, "percentage")


def get_created_by_name(template: "EvalTemplate") -> str:
    """
    Get display name for the template creator.

    Returns "System" for system-owned templates, or the user's name/email
    for user-owned templates. Falls back to checking EvalTemplateVersion.created_by.
    """
    if template.owner == OwnerChoices.SYSTEM.value:
        return "System"

    # Try to get user from evaluators linked to this template
    evaluators = getattr(template, "_prefetched_evaluators", None)
    if evaluators is not None:
        for evaluator in evaluators:
            if evaluator.user:
                name = getattr(evaluator.user, "name", "") or ""
                if name.strip():
                    return name.strip()
                return evaluator.user.email
    else:
        # Fallback: query the evaluator relationship
        evaluator = (
            template.evaluators.select_related("user")
            .filter(user__isnull=False)
            .first()
        )
        if evaluator and evaluator.user:
            name = getattr(evaluator.user, "name", "") or ""
            if name.strip():
                return name.strip()
            return evaluator.user.email

    # Fallback: check EvalTemplateVersion for creator (v2 API path)
    try:
        from model_hub.models.evals_metric import EvalTemplateVersion

        version = (
            EvalTemplateVersion.objects.filter(eval_template=template)
            .select_related("created_by")
            .order_by("version_number")
            .first()
        )
        if version and version.created_by:
            name = getattr(version.created_by, "name", "") or ""
            if name.strip():
                return name.strip()
            return version.created_by.email
    except Exception:
        pass

    return "User"


def build_user_eval_list_items(
    user_evals: Iterable, *, is_experiment_scope: bool = False
) -> list[dict]:
    """Build the canonical user-eval item shape used by get_evals_list."""
    from model_hub.models.develop_dataset import Column, SourceChoices
    from model_hub.utils.evals import NOT_UI_EVALS

    user_evals = list(user_evals)
    column_qs = Column.objects.filter(
        source_id__in=[str(user_eval.id) for user_eval in user_evals],
        deleted=False,
    )
    if is_experiment_scope:
        column_qs = column_qs.filter(
            source=SourceChoices.EXPERIMENT_EVALUATION.value
        )
    column_rows = list(column_qs.values("source_id", "id", "status"))
    column_map = {row["source_id"]: row["id"] for row in column_rows}
    column_status_map = {row["source_id"]: row["status"] for row in column_rows}

    run_evals: list[dict] = []

    for user_eval in user_evals:
        template = user_eval.template

        if not template or template.name in NOT_UI_EVALS:
            continue

        run_config = (user_eval.config or {}).get("run_config", {}) or {}
        summary = run_config.get("summary", "concise")
        if isinstance(summary, dict):
            summary = summary.get("type", "concise")

        item = {
            "id": user_eval.id,
            "name": user_eval.name,
            "template_name": template.name,
            "eval_template_name": template.name,
            "eval_required_keys": (template.config or {}).get("required_keys", []),
            "eval_template_tags": template.eval_tags,
            "description": template.description,
            "model": run_config.get("model")
            or (user_eval.config or {}).get("config", {}).get("model", ""),
            "column_id": column_map.get(str(user_eval.id)),
            "updated_at": user_eval.updated_at,
            "eval_group": user_eval.eval_group.name if user_eval.eval_group else None,
            "status": (
                column_status_map.get(str(user_eval.id)) or user_eval.status
                if is_experiment_scope
                else user_eval.status
            ),
            "eval_type": template.eval_type or "agent",
            "template_type": template.template_type or "single",
            "template_id": str(template.id),
            "owner": template.owner or "user",
            "mapping": (user_eval.config or {}).get("mapping", {}),
            "params": (user_eval.config or {}).get("params", {}),
            "error_localizer": user_eval.error_localizer,
            "run_config": {
                "agent_mode": run_config.get("agent_mode", "agent"),
                "check_internet": run_config.get("check_internet", False),
                "summary": summary,
                "pass_threshold": run_config.get("pass_threshold", 0.5),
                "error_localizer_enabled": user_eval.error_localizer,
            },
            "output_type": template.output_type_normalized or "pass_fail",
        }

        if template.template_type == "composite":
            item.update(
                {
                    "aggregation_function": template.aggregation_function,
                    "aggregation_enabled": template.aggregation_enabled,
                    "children_count": template.composite_children.filter(
                        deleted=False
                    ).count(),
                }
            )

        run_evals.append(item)

    return run_evals


def build_eval_list_queryset(
    organization,
    workspace,
    owner_filter: str = "all",
    search: str | None = None,
    filters: dict | EvalListFilters | None = None,
) -> QuerySet:
    """
    Build a filtered, scoped QuerySet for EvalTemplate.

    Args:
        organization: Organization instance
        workspace: Workspace instance (optional)
        owner_filter: "all", "user", or "system"
        search: Search string for name filtering
        filters: Advanced filters (eval_type, output_type, tags)

    Returns:
        Filtered QuerySet of EvalTemplate
    """
    from model_hub.models.evals_metric import EvalTemplate

    # Use no_workspace_objects to bypass the BaseModelManager's automatic
    # workspace filtering — system evals have no workspace/org and would
    # be excluded by the manager. We handle scoping manually below.
    qs = EvalTemplate.no_workspace_objects.filter(
        visible_ui=True,
    )

    # Scoping:
    # - System evals: always visible, NO workspace filter
    # - User evals: scoped to org + workspace
    if owner_filter == "system":
        qs = qs.filter(owner=OwnerChoices.SYSTEM.value)
    elif owner_filter == "user":
        user_q = Q(owner=OwnerChoices.USER.value, organization=organization)
        if workspace:
            user_q &= Q(workspace=workspace) | Q(workspace__isnull=True)
        qs = qs.filter(user_q)
    else:
        # "all" - system evals (no workspace filter) + user evals (workspace filtered)
        system_q = Q(owner=OwnerChoices.SYSTEM.value)
        user_q = Q(owner=OwnerChoices.USER.value, organization=organization)
        if workspace:
            user_q &= Q(workspace=workspace) | Q(workspace__isnull=True)
        qs = qs.filter(system_q | user_q)

    # Search by name
    if search:
        qs = qs.filter(name__icontains=search.strip())

    # Advanced filters
    if filters:
        # Support both dict (from DRF serializer) and Pydantic object
        _f = lambda key: (
            filters.get(key)
            if isinstance(filters, dict)
            else getattr(filters, key, None)
        )

        # Output type filter
        if _f("output_type"):
            selected_output_types = set(_f("output_type"))

            # Build raw output values from the canonical normalization map.
            # Avoid JSONField __in here (can collapse/behave inconsistently);
            # explicit OR keeps filtering predictable.
            normalized_to_raw: dict[str, set[str]] = {}
            for raw_value, normalized in _OUTPUT_TYPE_MAP.items():
                normalized_to_raw.setdefault(normalized, set()).add(raw_value)

            output_q = Q(output_type_normalized__in=list(selected_output_types))

            raw_values = set()
            for normalized in selected_output_types:
                raw_values.update(normalized_to_raw.get(normalized, set()))
            for raw_value in raw_values:
                output_q |= Q(config__output=raw_value)

            # Legacy rows may have empty output in config but render as percentage.
            if "percentage" in selected_output_types:
                output_q |= Q(config__output__isnull=True) | Q(config={})

            # Composite rows derive output type from axis.
            composite_axis_by_output = {
                "pass_fail": ["pass_fail", "code"],
                "percentage": ["percentage", ""],
                "deterministic": ["choices"],
            }
            composite_axes = []
            for normalized in selected_output_types:
                composite_axes.extend(composite_axis_by_output.get(normalized, []))
            if composite_axes:
                output_q |= Q(
                    template_type="composite",
                    composite_child_axis__in=list(set(composite_axes)),
                )

            qs = qs.filter(output_q)

        # Output type negation filter
        if _f("output_type_not"):
            excluded = set(_f("output_type_not"))
            normalized_to_raw_exc: dict[str, set[str]] = {}
            for raw_value, normalized in _OUTPUT_TYPE_MAP.items():
                normalized_to_raw_exc.setdefault(normalized, set()).add(raw_value)
            exc_q = Q(output_type_normalized__in=list(excluded))
            raw_vals = set()
            for n in excluded:
                raw_vals.update(normalized_to_raw_exc.get(n, set()))
            for rv in raw_vals:
                exc_q |= Q(config__output=rv)
            if "percentage" in excluded:
                exc_q |= Q(config__output__isnull=True) | Q(config={})
            composite_axis_by_output = {
                "pass_fail": ["pass_fail", "code"],
                "percentage": ["percentage", ""],
                "deterministic": ["choices"],
            }
            comp_axes = []
            for n in excluded:
                comp_axes.extend(composite_axis_by_output.get(n, []))
            if comp_axes:
                exc_q |= Q(
                    template_type="composite",
                    composite_child_axis__in=list(set(comp_axes)),
                )
            qs = qs.exclude(exc_q)

        # Tags filter
        if _f("tags"):
            qs = qs.filter(eval_tags__overlap=_f("tags"))

        # Template type filter (single/composite)
        if _f("template_type"):
            qs = qs.filter(template_type__in=_f("template_type"))

        # Exact-name multi-select (dropdown picker)
        if _f("names"):
            qs = qs.filter(name__in=_f("names"))

        # Created by filter (user names)
        if _f("created_by"):
            from model_hub.models.evals_metric import EvalTemplateVersion

            created_by_list = _f("created_by")
            version_template_ids = EvalTemplateVersion.all_objects.filter(
                is_default=True,
                deleted=False,
                created_by__name__in=created_by_list,
            ).values_list("eval_template_id", flat=True)
            version_template_ids_email = EvalTemplateVersion.all_objects.filter(
                is_default=True,
                deleted=False,
                created_by__email__in=created_by_list,
            ).values_list("eval_template_id", flat=True)
            if "System" in created_by_list:
                qs = qs.filter(
                    Q(id__in=version_template_ids)
                    | Q(id__in=version_template_ids_email)
                    | Q(owner="system")
                )
            else:
                qs = qs.filter(
                    Q(id__in=version_template_ids)
                    | Q(id__in=version_template_ids_email)
                )

        # Note: eval_type filter is applied in-memory after fetching because
        # eval_type is derived from multiple fields (config + tags), not a single
        # DB column. For better performance with large datasets, consider adding
        # a denormalized eval_type field to EvalTemplate in a future phase.

    return qs


def compute_thirty_day_data(
    template_id: str,
    logs_map: dict,
    start_date,
    template=None,
) -> tuple[list[ThirtyDayDataPoint], list[ThirtyDayDataPoint], int]:
    """
    Compute 30-day chart data and error rate for a template.

    Args:
        template_id: String UUID of the template
        logs_map: Dict mapping template_id -> list of log dicts
        start_date: Start date for the 30-day window
        template: EvalTemplate instance (for average calculation)

    Returns:
        Tuple of (chart_data, error_rate_data, run_count)
    """
    template_logs = logs_map.get(template_id, [])
    run_count = len(template_logs)

    # Group logs by date
    daily_counts: dict = defaultdict(int)
    daily_errors: dict = defaultdict(int)

    for log in template_logs:
        day = log["created_at"].date()
        daily_counts[day] += 1
        if log.get("status") == APICallStatusChoices.ERROR.value:
            daily_errors[day] += 1

    # Generate 31-day time series
    chart_data = []
    error_data = []
    current = start_date

    for _ in range(31):
        day = current.date() if hasattr(current, "date") else current
        ts = day.strftime("%Y-%m-%dT00:00:00")
        chart_data.append(
            ThirtyDayDataPoint(timestamp=ts, value=daily_counts.get(day, 0))
        )
        error_data.append(
            ThirtyDayDataPoint(timestamp=ts, value=daily_errors.get(day, 0))
        )
        current += timedelta(days=1)

    return chart_data, error_data, run_count
