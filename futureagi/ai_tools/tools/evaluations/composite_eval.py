import json
import logging
import os
import re
from typing import Any

from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid

logger = logging.getLogger(__name__)


AGGREGATION_FUNCTIONS = {"weighted_avg", "avg", "min", "max", "pass_rate"}
COMPOSITE_CHILD_AXES = {"", "pass_fail", "percentage", "choices", "code"}


def _parse_jsonish(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return fallback
        if stripped.startswith(("[", "{")):
            try:
                return json.loads(stripped)
            except (TypeError, ValueError):
                return fallback
        return value
    return value


def _normalize_list(value: Any) -> list[str]:
    parsed = _parse_jsonish(value, [])
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, str):
        return [part.strip() for part in parsed.split(",") if part.strip()]
    return []


def _normalize_tags(value: Any) -> list[str]:
    parsed = _parse_jsonish(value, [])
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, str):
        return [part.strip() for part in parsed.split(",") if part.strip()]
    return []


def _normalize_mapping(value: Any) -> dict[str, Any]:
    parsed = _parse_jsonish(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _normalize_weights(value: Any) -> dict[str, float] | None:
    parsed = _parse_jsonish(value, None)
    if not isinstance(parsed, dict):
        return None
    weights: dict[str, float] = {}
    for key, raw_value in parsed.items():
        try:
            weights[str(key)] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return weights


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _clean_name(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "_", (name or "").strip().lower())
    normalized = normalized.strip("_-")
    if not normalized:
        raise ValueError("Name is required.")
    if not re.match(r"^[a-z0-9_-]+$", normalized):
        raise ValueError(
            "Name can only contain lowercase letters, numbers, hyphens, or underscores."
        )
    return normalized


def _accessible_template_scope(context: ToolContext) -> Q:
    from model_hub.models.choices import OwnerChoices

    return (
        Q(owner=OwnerChoices.SYSTEM.value)
        | Q(organization=context.organization)
        | Q(organization__isnull=True)
    )


def _composite_queryset(context: ToolContext):
    from model_hub.models.evals_metric import EvalTemplate

    return EvalTemplate.no_workspace_objects.filter(
        _accessible_template_scope(context),
        deleted=False,
        template_type="composite",
    )


def _child_template_queryset(context: ToolContext):
    from model_hub.models.evals_metric import EvalTemplate

    return (
        EvalTemplate.no_workspace_objects.filter(
            _accessible_template_scope(context),
            deleted=False,
        )
        .exclude(template_type="composite")
        .order_by("-created_at")
    )


def _user_composite_queryset(context: ToolContext):
    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate

    return EvalTemplate.objects.filter(
        organization=context.organization,
        owner=OwnerChoices.USER.value,
        deleted=False,
        template_type="composite",
    )


def _resolve_from_queryset(qs, ref: str, entity_name: str):
    ref = (ref or "").strip()
    if not ref:
        return None, f"{entity_name} identifier is required."

    if is_uuid(ref):
        obj = qs.filter(id=ref).first()
        if obj:
            return obj, None
        return None, f"{entity_name} with ID `{ref}` was not found."

    exact = qs.filter(name__iexact=ref)
    exact_count = exact.count()
    if exact_count == 1:
        return exact.first(), None
    if exact_count > 1:
        choices = [f"`{obj.name}` (`{obj.id}`)" for obj in exact[:5]]
        return (
            None,
            f"Multiple {entity_name.lower()}s match `{ref}`: {', '.join(choices)}.",
        )

    fuzzy = qs.filter(name__icontains=ref)
    fuzzy_count = fuzzy.count()
    if fuzzy_count == 1:
        return fuzzy.first(), None
    if fuzzy_count > 1:
        choices = [f"`{obj.name}` (`{obj.id}`)" for obj in fuzzy[:5]]
        return None, f"No exact match for `{ref}`. Did you mean: {', '.join(choices)}?"

    return None, f"No {entity_name.lower()} found matching `{ref}`."


def _template_required_keys(template) -> list[str]:
    config = template.config or {}
    if not isinstance(config, dict):
        return []
    required = config.get("required_keys") or []
    return [str(item) for item in required] if isinstance(required, list) else []


def _template_output_type(template) -> str:
    config = template.config or {}
    if isinstance(config, dict):
        return (
            template.output_type_normalized
            or config.get("output")
            or config.get("output_type")
            or "-"
        )
    return template.output_type_normalized or "-"


def _format_template_candidates(templates) -> str:
    rows = [
        [
            f"`{template.id}`",
            truncate(template.name, 44),
            template.owner or "-",
            truncate(_template_output_type(template), 20),
            truncate(", ".join(_template_required_keys(template)) or "-", 48),
            format_datetime(template.created_at),
        ]
        for template in templates
    ]
    return markdown_table(
        ["ID", "Name", "Owner", "Output", "Required", "Created"], rows
    )


def _format_composite_rows(composites) -> str:
    from model_hub.models.evals_metric import CompositeEvalChild

    child_counts = {}
    for link in CompositeEvalChild.objects.filter(parent__in=composites, deleted=False):
        child_counts[link.parent_id] = child_counts.get(link.parent_id, 0) + 1

    rows = [
        [
            f"`{composite.id}`",
            truncate(composite.name, 44),
            str(child_counts.get(composite.id, 0)),
            "yes" if composite.aggregation_enabled else "no",
            composite.aggregation_function or "-",
            composite.composite_child_axis or "-",
            format_datetime(composite.created_at),
        ]
        for composite in composites
    ]
    return markdown_table(
        ["ID", "Name", "Children", "Aggregates", "Function", "Axis", "Created"],
        rows,
    )


def _candidate_composites_result(
    context: ToolContext,
    title: str,
    detail: str = "",
    *,
    search: str | None = None,
) -> ToolResult:
    qs = _composite_queryset(context).order_by("-created_at")
    if search:
        qs = qs.filter(name__icontains=search)
    composites = list(qs[:10])
    body = detail or ""
    if composites:
        body = (body + "\n\n" if body else "") + _format_composite_rows(composites)
    else:
        body = body or "No composite evals found."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_composite_eval_id": True,
            "composite_evals": [
                {"id": str(composite.id), "name": composite.name}
                for composite in composites
            ],
        },
    )


def _candidate_children_result(
    context: ToolContext,
    title: str,
    detail: str = "",
    *,
    search: str | None = None,
) -> ToolResult:
    qs = _child_template_queryset(context)
    if search:
        qs = qs.filter(name__icontains=search)
    templates = list(qs[:12])
    body = detail or ""
    if templates:
        body = (body + "\n\n" if body else "") + _format_template_candidates(templates)
    else:
        body = body or "No non-composite eval templates found."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_child_template_ids": True,
            "templates": [
                {"id": str(template.id), "name": template.name}
                for template in templates
            ],
        },
    )


def _resolve_child_templates(context: ToolContext, refs: list[str]):
    qs = _child_template_queryset(context)
    resolved = []
    errors = []
    for ref in refs:
        template, err = _resolve_from_queryset(qs, ref, "Eval template")
        if err:
            errors.append(err)
            continue
        if template and template.template_type == "composite":
            errors.append(
                f"`{template.name}` is itself a composite eval. Nested composites are not supported."
            )
            continue
        if template:
            resolved.append(template)
    return resolved, errors


def _validate_axis(axis: str | None) -> str:
    axis = axis or ""
    if axis not in COMPOSITE_CHILD_AXES:
        raise ValueError(
            "Invalid composite_child_axis. Use one of: "
            + ", ".join(sorted(value for value in COMPOSITE_CHILD_AXES if value))
            + "."
        )
    return axis


def _validate_aggregation(function: str | None) -> str:
    function = function or "weighted_avg"
    aliases = {
        "average": "avg",
        "mean": "avg",
        "weighted_average": "weighted_avg",
        "weighted average": "weighted_avg",
    }
    function = aliases.get(function.strip().lower(), function)
    if function not in AGGREGATION_FUNCTIONS:
        raise ValueError(
            "Invalid aggregation_function. Use one of: "
            + ", ".join(sorted(AGGREGATION_FUNCTIONS))
            + "."
        )
    return function


def _validate_children_match_axis(children, axis: str) -> str | None:
    if not axis:
        return None
    try:
        from model_hub.views.separate_evals import _validate_child_matches_axis
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not import composite axis validator: %s", exc)
        return None
    for child in children:
        try:
            _validate_child_matches_axis(child, axis)
        except ValueError as exc:
            return str(exc)
    return None


def _version_snapshot(parent, links) -> dict[str, Any]:
    return {
        "aggregation_enabled": parent.aggregation_enabled,
        "aggregation_function": parent.aggregation_function,
        "composite_child_axis": parent.composite_child_axis or "",
        "children": [
            {
                "child_id": str(link.child_id),
                "child_name": link.child.name,
                "order": link.order,
                "weight": link.weight,
                "pinned_version_id": (
                    str(link.pinned_version_id) if link.pinned_version_id else None
                ),
            }
            for link in links
        ],
    }


def _create_composite_version(parent, context: ToolContext, links) -> int | None:
    try:
        from model_hub.models.evals_metric import EvalTemplateVersion

        version = EvalTemplateVersion.objects.create_version(
            eval_template=parent,
            prompt_messages=[],
            config_snapshot=_version_snapshot(parent, links),
            criteria=parent.description or "",
            model="",
            user=context.user,
            organization=context.organization,
            workspace=context.workspace,
        )
        return version.version_number
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to create composite eval version: %s", exc)
        return None


def _detail_result(
    parent, context: ToolContext, title: str | None = None
) -> ToolResult:
    from model_hub.models.evals_metric import CompositeEvalChild

    from model_hub.utils.eval_list import derive_eval_type

    links = list(
        CompositeEvalChild.objects.filter(parent=parent, deleted=False)
        .select_related("child", "pinned_version")
        .order_by("order")
    )

    info = key_value_block(
        [
            ("ID", f"`{parent.id}`"),
            ("Name", parent.name),
            ("Description", truncate(parent.description or "-", 500)),
            ("Aggregation Enabled", "yes" if parent.aggregation_enabled else "no"),
            ("Aggregation Function", parent.aggregation_function or "-"),
            ("Child Axis", parent.composite_child_axis or "-"),
            ("Tags", ", ".join(parent.eval_tags or []) or "-"),
            ("Created", format_datetime(parent.created_at)),
        ]
    )

    rows = []
    children_data = []
    required_keys: set[str] = set()
    for link in links:
        child_required = _template_required_keys(link.child)
        required_keys.update(child_required)
        rows.append(
            [
                str(link.order),
                f"`{link.child_id}`",
                truncate(link.child.name, 44),
                derive_eval_type(link.child),
                format_number(link.weight),
                truncate(", ".join(child_required) or "-", 48),
            ]
        )
        children_data.append(
            {
                "child_id": str(link.child_id),
                "child_name": link.child.name,
                "order": link.order,
                "eval_type": derive_eval_type(link.child),
                "weight": link.weight,
                "required_keys": child_required,
                "pinned_version_id": (
                    str(link.pinned_version_id) if link.pinned_version_id else None
                ),
            }
        )

    content = section(title or f"Composite Eval: {parent.name}", info)
    content += "\n\n### Children\n\n" + markdown_table(
        ["Order", "ID", "Name", "Type", "Weight", "Required Keys"],
        rows,
    )
    if required_keys:
        content += "\n\n### Combined Mapping Keys\n\n" + ", ".join(
            f"`{key}`" for key in sorted(required_keys)
        )

    return ToolResult(
        content=content,
        data={
            "id": str(parent.id),
            "name": parent.name,
            "template_type": "composite",
            "aggregation_enabled": parent.aggregation_enabled,
            "aggregation_function": parent.aggregation_function,
            "composite_child_axis": parent.composite_child_axis or "",
            "children": children_data,
            "required_keys": sorted(required_keys),
        },
    )


class ListCompositeEvalsInput(PydanticBaseModel):
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    search: str | None = Field(
        default=None, description="Search composite evals by name"
    )


@register_tool
class ListCompositeEvalsTool(BaseTool):
    name = "list_composite_evals"
    description = (
        "Lists Composite Evals, the current aggregated-eval object. "
        "Use this instead of eval groups when users ask for aggregated, bundled, "
        "or multi-metric evaluations."
    )
    category = "evaluations"
    input_model = ListCompositeEvalsInput

    def execute(
        self, params: ListCompositeEvalsInput, context: ToolContext
    ) -> ToolResult:
        qs = _composite_queryset(context).order_by("-created_at")
        if params.search:
            qs = qs.filter(name__icontains=params.search)
        total = qs.count()
        composites = list(qs[params.offset : params.offset + params.limit])
        table = _format_composite_rows(composites)
        showing = f"Showing {len(composites)} of {total}"
        if params.search:
            showing += f" (search: `{params.search}`)"
        content = section("Composite Evals", f"{showing}\n\n{table}")
        if total > params.offset + params.limit:
            content += (
                f"\n\nUse offset={params.offset + params.limit} to see more results."
            )
        return ToolResult(
            content=content,
            data={
                "composite_evals": [
                    {"id": str(composite.id), "name": composite.name}
                    for composite in composites
                ],
                "total": total,
            },
        )


class GetCompositeEvalInput(PydanticBaseModel):
    composite_eval_id: str = Field(
        default="",
        description="Composite eval name or UUID. If omitted, candidates are returned.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["composite_eval_id"] = (
            normalized.get("composite_eval_id")
            or normalized.get("eval_template_id")
            or normalized.get("template_id")
            or normalized.get("id")
            or normalized.get("name")
            or ""
        )
        return normalized


@register_tool
class GetCompositeEvalTool(BaseTool):
    name = "get_composite_eval"
    description = (
        "Shows the children, aggregation settings, weights, and required mapping "
        "keys for a Composite Eval."
    )
    category = "evaluations"
    input_model = GetCompositeEvalInput

    def execute(
        self, params: GetCompositeEvalInput, context: ToolContext
    ) -> ToolResult:
        ref = (params.composite_eval_id or "").strip()
        if not ref:
            return _candidate_composites_result(
                context,
                "Composite Eval Required",
                "Provide `composite_eval_id` to inspect a Composite Eval.",
            )
        parent, err = _resolve_from_queryset(
            _composite_queryset(context), ref, "Composite eval"
        )
        if err:
            return _candidate_composites_result(
                context,
                "Composite Eval Candidates",
                err,
                search=ref if not is_uuid(ref) else None,
            )
        return _detail_result(parent, context)


class CreateCompositeEvalInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(
        default="",
        description="Name for the Composite Eval. It will be normalized to lowercase snake style.",
    )
    description: str | None = Field(default=None, description="Description")
    tags: list[str] = Field(default_factory=list, description="Tags")
    child_template_ids: list[str] = Field(
        default_factory=list,
        description="Child eval template names or UUIDs to include, in order",
    )
    aggregation_enabled: bool = Field(default=True)
    aggregation_function: str = Field(default="weighted_avg")
    child_weights: dict[str, float] | None = Field(default=None)
    composite_child_axis: str = Field(
        default="",
        description="Optional axis: pass_fail, percentage, choices, or code",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["name"] = (
            normalized.get("name")
            or normalized.get("composite_name")
            or normalized.get("eval_name")
            or ""
        )
        child_refs = (
            normalized.get("child_template_ids")
            or normalized.get("eval_template_ids")
            or normalized.get("template_ids")
            or normalized.get("children")
            or []
        )
        normalized["child_template_ids"] = _normalize_list(child_refs)
        normalized["tags"] = _normalize_tags(normalized.get("tags"))
        normalized["child_weights"] = _normalize_weights(
            normalized.get("child_weights")
        )
        return normalized


@register_tool
class CreateCompositeEvalTool(BaseTool):
    name = "create_composite_eval"
    description = (
        "Creates a Composite Eval, the current aggregated-eval object that combines "
        "multiple eval templates with optional weighted aggregation. Use this instead "
        "of create_eval_group."
    )
    category = "evaluations"
    input_model = CreateCompositeEvalInput

    def execute(
        self, params: CreateCompositeEvalInput, context: ToolContext
    ) -> ToolResult:
        from django.db import transaction
        from model_hub.models.choices import OwnerChoices
        from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate

        if not params.name:
            return _candidate_children_result(
                context,
                "Composite Eval Name Required",
                (
                    "Provide `name` and `child_template_ids` to create a Composite Eval. "
                    "Here are usable child eval templates."
                ),
            )
        if not params.child_template_ids:
            return _candidate_children_result(
                context,
                "Composite Eval Children Required",
                (
                    "Provide at least one child eval template ID or exact name in "
                    "`child_template_ids`."
                ),
            )

        try:
            name = _clean_name(params.name)
            aggregation_function = _validate_aggregation(params.aggregation_function)
            axis = _validate_axis(params.composite_child_axis)
        except ValueError as exc:
            return ToolResult.validation_error(str(exc))

        existing = EvalTemplate.objects.filter(
            name=name,
            organization=context.organization,
            deleted=False,
        ).first()
        if existing and existing.template_type == "composite":
            result = _detail_result(existing, context, "Composite Eval Already Exists")
            result.data = result.data or {}
            result.data["already_exists"] = True
            return result
        if existing:
            return ToolResult.validation_error(
                f"An evaluation named `{name}` already exists in this organization."
            )

        children, errors = _resolve_child_templates(context, params.child_template_ids)
        if errors:
            return _candidate_children_result(
                context,
                "Composite Eval Child Candidates",
                "\n".join(f"- {error}" for error in errors),
            )
        if not children:
            return _candidate_children_result(
                context,
                "Composite Eval Children Required",
                "No valid child eval templates were provided.",
            )

        axis_error = _validate_children_match_axis(children, axis)
        if axis_error:
            return ToolResult.validation_error(axis_error)

        weights = params.child_weights or {}
        with transaction.atomic():
            parent = EvalTemplate.objects.create(
                name=name,
                organization=context.organization,
                owner=OwnerChoices.USER.value,
                eval_tags=params.tags or [],
                config={},
                description=params.description or "",
                template_type="composite",
                visible_ui=True,
                aggregation_enabled=params.aggregation_enabled,
                aggregation_function=aggregation_function,
                composite_child_axis=axis,
            )
            links = []
            for order, (ref, child) in enumerate(
                zip(params.child_template_ids, children, strict=False)
            ):
                weight = weights.get(str(child.id), weights.get(ref, 1.0))
                link = CompositeEvalChild.objects.create(
                    parent=parent,
                    child=child,
                    order=order,
                    weight=float(weight),
                )
                links.append(link)
            version_number = _create_composite_version(parent, context, links)

        result = _detail_result(parent, context, "Composite Eval Created")
        if version_number:
            result.data = result.data or {}
            result.data["version_number"] = version_number
        return result


class UpdateCompositeEvalInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    composite_eval_id: str = Field(
        default="", description="Composite eval name or UUID to update"
    )
    name: str | None = Field(default=None, description="New name")
    description: str | None = Field(default=None, description="New description")
    tags: list[str] | None = Field(default=None, description="Replacement tags")
    aggregation_enabled: bool | None = Field(default=None)
    aggregation_function: str | None = Field(default=None)
    child_template_ids: list[str] | None = Field(
        default=None,
        description="Replacement child eval template names or UUIDs, in order",
    )
    child_weights: dict[str, float] | None = Field(default=None)
    composite_child_axis: str | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["composite_eval_id"] = (
            normalized.get("composite_eval_id")
            or normalized.get("eval_template_id")
            or normalized.get("template_id")
            or normalized.get("id")
            or ""
        )
        if "child_template_ids" in normalized or "eval_template_ids" in normalized:
            normalized["child_template_ids"] = _normalize_list(
                normalized.get("child_template_ids")
                or normalized.get("eval_template_ids")
                or normalized.get("template_ids")
                or []
            )
        if "tags" in normalized and normalized.get("tags") is not None:
            normalized["tags"] = _normalize_tags(normalized.get("tags"))
        if "child_weights" in normalized:
            normalized["child_weights"] = _normalize_weights(
                normalized.get("child_weights")
            )
        return normalized


@register_tool
class UpdateCompositeEvalTool(BaseTool):
    name = "update_composite_eval"
    description = (
        "Updates a user-owned Composite Eval. Can rename it, change aggregation, "
        "replace children, or adjust child weights."
    )
    category = "evaluations"
    input_model = UpdateCompositeEvalInput

    def execute(
        self, params: UpdateCompositeEvalInput, context: ToolContext
    ) -> ToolResult:
        from django.db import transaction
        from model_hub.models.evals_metric import CompositeEvalChild, EvalTemplate

        ref = (params.composite_eval_id or "").strip()
        if not ref:
            return _candidate_composites_result(
                context,
                "Composite Eval Required",
                "Provide `composite_eval_id` to update a Composite Eval.",
            )

        parent, err = _resolve_from_queryset(
            _user_composite_queryset(context), ref, "Composite eval"
        )
        if err:
            return _candidate_composites_result(
                context,
                "Composite Eval Candidates",
                err,
                search=ref if not is_uuid(ref) else None,
            )

        changed_fields: list[str] = []
        try:
            new_name = _clean_name(params.name) if params.name is not None else None
            new_aggregation = (
                _validate_aggregation(params.aggregation_function)
                if params.aggregation_function is not None
                else None
            )
            new_axis = (
                _validate_axis(params.composite_child_axis)
                if params.composite_child_axis is not None
                else None
            )
        except ValueError as exc:
            return ToolResult.validation_error(str(exc))

        children = None
        if params.child_template_ids is not None:
            children, errors = _resolve_child_templates(
                context, params.child_template_ids
            )
            if errors:
                return _candidate_children_result(
                    context,
                    "Composite Eval Child Candidates",
                    "\n".join(f"- {error}" for error in errors),
                )
            axis_for_children = (
                new_axis
                if new_axis is not None
                else (parent.composite_child_axis or "")
            )
            axis_error = _validate_children_match_axis(children, axis_for_children)
            if axis_error:
                return ToolResult.validation_error(axis_error)
        elif new_axis is not None and new_axis != (parent.composite_child_axis or ""):
            existing_children = [
                link.child
                for link in CompositeEvalChild.objects.filter(
                    parent=parent, deleted=False
                ).select_related("child")
            ]
            axis_error = _validate_children_match_axis(existing_children, new_axis)
            if axis_error:
                return ToolResult.validation_error(
                    f"Cannot switch to `{new_axis}` axis: {axis_error}"
                )

        if (
            new_name is None
            and params.description is None
            and params.tags is None
            and params.aggregation_enabled is None
            and new_aggregation is None
            and params.child_template_ids is None
            and params.child_weights is None
            and new_axis is None
        ):
            result = _detail_result(parent, context, "Composite Eval Update Preview")
            result.data = result.data or {}
            result.data["requires_update_fields"] = True
            return result

        if new_name is not None and new_name != parent.name:
            if (
                EvalTemplate.objects.filter(
                    name=new_name,
                    organization=context.organization,
                    deleted=False,
                )
                .exclude(id=parent.id)
                .exists()
            ):
                return ToolResult.validation_error(
                    f"An evaluation named `{new_name}` already exists."
                )

        with transaction.atomic():
            if new_name is not None and new_name != parent.name:
                parent.name = new_name
                changed_fields.append("name")
            if params.description is not None:
                parent.description = params.description
                changed_fields.append("description")
            if params.tags is not None:
                parent.eval_tags = params.tags
                changed_fields.append("eval_tags")
            if params.aggregation_enabled is not None:
                parent.aggregation_enabled = params.aggregation_enabled
                changed_fields.append("aggregation_enabled")
            if new_aggregation is not None:
                parent.aggregation_function = new_aggregation
                changed_fields.append("aggregation_function")
            if new_axis is not None:
                parent.composite_child_axis = new_axis
                changed_fields.append("composite_child_axis")

            parent.updated_at = timezone.now()
            save_fields = [*set(changed_fields), "updated_at"]
            parent.save(update_fields=save_fields)

            if children is not None:
                CompositeEvalChild.objects.filter(
                    parent=parent,
                    deleted=False,
                ).update(deleted=True)
                weights = params.child_weights or {}
                for order, (ref_item, child) in enumerate(
                    zip(params.child_template_ids or [], children, strict=False)
                ):
                    weight = weights.get(str(child.id), weights.get(ref_item, 1.0))
                    CompositeEvalChild.objects.create(
                        parent=parent,
                        child=child,
                        order=order,
                        weight=float(weight),
                    )
                changed_fields.append("children")
            elif params.child_weights is not None:
                links_to_update = CompositeEvalChild.objects.filter(
                    parent=parent,
                    deleted=False,
                ).select_related("child")
                for link in links_to_update:
                    cid = str(link.child_id)
                    if (
                        cid in params.child_weights
                        or link.child.name in params.child_weights
                    ):
                        link.weight = float(
                            params.child_weights.get(
                                cid,
                                params.child_weights.get(link.child.name, link.weight),
                            )
                        )
                        link.save(update_fields=["weight"])
                changed_fields.append("child_weights")

            links = list(
                CompositeEvalChild.objects.filter(parent=parent, deleted=False)
                .select_related("child", "pinned_version")
                .order_by("order")
            )
            version_number = _create_composite_version(parent, context, links)

        result = _detail_result(parent, context, "Composite Eval Updated")
        result.data = result.data or {}
        result.data["updated_fields"] = sorted(set(changed_fields))
        if version_number:
            result.data["version_number"] = version_number
        return result


class DeleteCompositeEvalInput(PydanticBaseModel):
    composite_eval_id: str = Field(
        default="", description="User-owned composite eval name or UUID to delete"
    )
    confirm_delete: bool = Field(
        default=False,
        description="Set true only after the user confirms this deletion",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["composite_eval_id"] = (
            normalized.get("composite_eval_id")
            or normalized.get("eval_template_id")
            or normalized.get("template_id")
            or normalized.get("id")
            or normalized.get("name")
            or ""
        )
        return normalized


@register_tool
class DeleteCompositeEvalTool(BaseTool):
    name = "delete_composite_eval"
    description = (
        "Soft-deletes a user-owned Composite Eval after explicit confirmation. "
        "Returns a preview first when confirm_delete is false."
    )
    category = "evaluations"
    input_model = DeleteCompositeEvalInput

    def execute(
        self, params: DeleteCompositeEvalInput, context: ToolContext
    ) -> ToolResult:
        from django.db import transaction
        from model_hub.models.evals_metric import CompositeEvalChild

        ref = (params.composite_eval_id or "").strip()
        if not ref:
            return _candidate_composites_result(
                context,
                "Composite Eval Required",
                "Provide `composite_eval_id` to preview deletion.",
            )

        parent, err = _resolve_from_queryset(
            _user_composite_queryset(context), ref, "Composite eval"
        )
        if err:
            return _candidate_composites_result(
                context,
                "Composite Eval Candidates",
                err,
                search=ref if not is_uuid(ref) else None,
            )

        if not params.confirm_delete:
            result = _detail_result(parent, context, "Composite Eval Delete Preview")
            result.data = result.data or {}
            result.data["requires_confirmation"] = True
            return result

        name = parent.name
        with transaction.atomic():
            parent.deleted = True
            parent.deleted_at = timezone.now()
            parent.save(update_fields=["deleted", "deleted_at"])
            CompositeEvalChild.objects.filter(parent=parent, deleted=False).update(
                deleted=True
            )

        return ToolResult(
            content=section(
                "Composite Eval Deleted",
                f"Composite Eval **{name}** (`{parent.id}`) has been deleted.",
            ),
            data={"id": str(parent.id), "name": name},
        )


class ExecuteCompositeEvalInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    composite_eval_id: str = Field(
        default="", description="Composite eval name or UUID to execute"
    )
    mapping: dict[str, Any] | str = Field(
        default_factory=dict,
        description="Shared input mapping for all child eval required keys",
    )
    model: str | None = Field(default=None, description="Optional model override")
    config: dict[str, Any] = Field(default_factory=dict)
    error_localizer: bool = Field(default=False)
    input_data_types: dict[str, str] = Field(default_factory=dict)
    span_context: dict | None = Field(default=None)
    trace_context: dict | None = Field(default=None)
    session_context: dict | None = Field(default=None)
    call_context: dict | None = Field(default=None)
    row_context: dict | None = Field(default=None)
    run_if_mapping_empty: bool = Field(
        default=False,
        description="Set true only when the composite intentionally needs no mapping.",
    )
    run_now: bool = Field(
        default=False,
        description=(
            "Set true only after explicit user confirmation to run child evals. "
            "The default preflights mapping and child compatibility without "
            "starting provider-backed execution."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["composite_eval_id"] = (
            normalized.get("composite_eval_id")
            or normalized.get("eval_template_id")
            or normalized.get("template_id")
            or normalized.get("id")
            or normalized.get("name")
            or ""
        )
        mapping = (
            normalized.get("mapping")
            or normalized.get("input_data")
            or normalized.get("sample_data")
            or normalized.get("inputs")
            or {}
        )
        normalized["mapping"] = _normalize_mapping(mapping)
        normalized["config"] = _normalize_mapping(normalized.get("config"))
        normalized["input_data_types"] = _normalize_mapping(
            normalized.get("input_data_types")
        )
        normalized["run_now"] = bool(
            normalized.get("run_now")
            or normalized.get("execute_now")
            or normalized.get("confirm_execute")
            or normalized.get("allow_long_running_execution")
            or normalized.get("run") is True
        )
        return normalized


@register_tool
class ExecuteCompositeEvalTool(BaseTool):
    name = "execute_composite_eval"
    description = (
        "Preflights or executes a Composite Eval with a shared mapping. By default "
        "it validates required keys and child compatibility without starting "
        "provider-backed child eval execution; set run_now=true after explicit "
        "confirmation to run all child evals and persist aggregate results."
    )
    category = "evaluations"
    input_model = ExecuteCompositeEvalInput

    def execute(
        self, params: ExecuteCompositeEvalInput, context: ToolContext
    ) -> ToolResult:
        from model_hub.models.evals_metric import CompositeEvalChild

        from model_hub.utils.composite_execution import execute_composite_children_sync
        from model_hub.views.separate_evals import _persist_composite_evaluation

        ref = (params.composite_eval_id or "").strip()
        if not ref:
            return _candidate_composites_result(
                context,
                "Composite Eval Required",
                "Provide `composite_eval_id` to execute a Composite Eval.",
            )

        parent, err = _resolve_from_queryset(
            _composite_queryset(context), ref, "Composite eval"
        )
        if err:
            return _candidate_composites_result(
                context,
                "Composite Eval Candidates",
                err,
                search=ref if not is_uuid(ref) else None,
            )

        links = list(
            CompositeEvalChild.objects.filter(parent=parent, deleted=False)
            .select_related("child", "pinned_version")
            .order_by("order")
        )
        if not links:
            return ToolResult.validation_error("Composite eval has no children.")

        required_keys: set[str] = set()
        for link in links:
            required_keys.update(_template_required_keys(link.child))
        mapping = _normalize_mapping(params.mapping)
        missing_keys = sorted(key for key in required_keys if key not in mapping)
        if (not mapping and not params.run_if_mapping_empty) or missing_keys:
            result = _detail_result(parent, context, "Composite Eval Mapping Required")
            result.data = result.data or {}
            result.data["requires_mapping"] = True
            result.data["missing_keys"] = missing_keys
            result.status = "needs_input"
            result.data["tool_status"] = "needs_input"
            result.content += (
                "\n\nProvide `mapping` with the required keys before execution."
            )
            if missing_keys:
                result.content += (
                    "\n\nMissing keys: "
                    + ", ".join(f"`{key}`" for key in missing_keys)
                    + "."
                )
            return result

        if parent.composite_child_axis:
            axis_error = _validate_children_match_axis(
                [link.child for link in links], parent.composite_child_axis
            )
            if axis_error:
                return ToolResult.validation_error(
                    f"Composite cannot run: {axis_error}"
                )

        execution_enabled = _env_flag("FALCON_AI_ALLOW_COMPOSITE_EXECUTION")
        if not params.run_now or not execution_enabled:
            title = (
                "Composite Eval Execution Disabled"
                if params.run_now and not execution_enabled
                else "Composite Eval Execution Preview"
            )
            result = _detail_result(parent, context, title)
            result.data = result.data or {}
            result.data.update(
                {
                    "ready_to_execute": True,
                    "execution_started": False,
                    "requires_run_now": True,
                    "execution_enabled": execution_enabled,
                    "execution_blocked_by_environment": (
                        params.run_now and not execution_enabled
                    ),
                    "mapping_keys_present": sorted(mapping.keys()),
                    "missing_keys": [],
                }
            )
            result.status = (
                "blocked" if params.run_now and not execution_enabled else "ready"
            )
            result.data["tool_status"] = result.status
            if params.run_now and not execution_enabled:
                result.data["blocked_reason"] = "composite_execution_disabled"
            if params.run_now and not execution_enabled:
                result.content += (
                    "\n\nMapping covers the required keys and child compatibility "
                    "checks passed, but execution is disabled in this environment. "
                    "Set `FALCON_AI_ALLOW_COMPOSITE_EXECUTION=true` only for an "
                    "environment with provider credentials and enough runtime for "
                    "child eval execution."
                )
            else:
                result.content += (
                    "\n\nMapping covers the required keys and child compatibility "
                    "checks passed. Execution was not started because child evals can "
                    "call external providers and exceed Falcon's tool timeout. Set "
                    "`run_now=true` after explicit confirmation in an execution-enabled "
                    "environment to persist the aggregate result."
                )
            return result

        model = params.model or os.environ.get("FALCON_AI_MODEL") or None
        outcome = execute_composite_children_sync(
            parent=parent,
            child_links=links,
            mapping=mapping,
            config=params.config,
            org=context.organization,
            workspace=context.workspace,
            model=model,
            input_data_types=params.input_data_types,
            row_context=params.row_context,
            span_context=params.span_context,
            trace_context=params.trace_context,
            session_context=params.session_context,
            call_context=params.call_context,
            error_localizer=params.error_localizer,
            source="falcon_ai_tool_composite_eval",
        )

        evaluation_id = _persist_composite_evaluation(
            user=context.user,
            org=context.organization,
            workspace=context.workspace,
            parent_template=parent,
            child_links=links,
            outcome=outcome,
            mapping=mapping,
            model=model,
        )

        completed = sum(1 for cr in outcome.child_results if cr.status == "completed")
        failed = sum(1 for cr in outcome.child_results if cr.status == "failed")
        rows = [
            [
                str(cr.order),
                truncate(cr.child_name, 44),
                cr.status,
                format_number(cr.score),
                truncate(cr.output, 80),
                truncate(cr.error or cr.reason or "-", 100),
            ]
            for cr in outcome.child_results
        ]
        info = key_value_block(
            [
                ("Composite", f"{parent.name} (`{parent.id}`)"),
                ("Aggregate Score", format_number(outcome.aggregate_score)),
                ("Aggregate Pass", outcome.aggregate_pass),
                ("Aggregation Function", parent.aggregation_function or "-"),
                ("Completed Children", f"{completed}/{len(outcome.child_results)}"),
                ("Failed Children", str(failed)),
                ("Evaluation ID", f"`{evaluation_id}`" if evaluation_id else "-"),
                ("Summary", truncate(outcome.summary or "-", 500)),
            ]
        )
        content = section("Composite Eval Executed", info)
        content += "\n\n### Child Results\n\n" + markdown_table(
            ["Order", "Child", "Status", "Score", "Output", "Reason/Error"],
            rows,
        )
        return ToolResult(
            content=content,
            data={
                "composite_id": str(parent.id),
                "composite_name": parent.name,
                "aggregation_enabled": parent.aggregation_enabled,
                "aggregation_function": (
                    parent.aggregation_function if parent.aggregation_enabled else None
                ),
                "aggregate_score": outcome.aggregate_score,
                "aggregate_pass": outcome.aggregate_pass,
                "total_children": len(outcome.child_results),
                "completed_children": completed,
                "failed_children": failed,
                "evaluation_id": evaluation_id,
                "children": [cr.model_dump() for cr in outcome.child_results],
            },
            is_error=failed == len(outcome.child_results),
            error_code="EXECUTION_FAILED"
            if failed == len(outcome.child_results)
            else None,
        )
