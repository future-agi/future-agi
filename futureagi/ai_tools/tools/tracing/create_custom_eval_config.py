from difflib import SequenceMatcher
from typing import Optional
from uuid import UUID

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from model_hub.utils.eval_mapping import non_path_mapping_keys

logger = structlog.get_logger(__name__)


def _similarity_score(a: str, b: str) -> float:
    """Compute similarity between two strings using SequenceMatcher
    plus a bonus for substring containment."""
    a_lower = a.lower()
    b_lower = b.lower()

    # Exact match
    if a_lower == b_lower:
        return 1.0

    # SequenceMatcher ratio
    ratio = SequenceMatcher(None, a_lower, b_lower).ratio()

    # Bonus if one is a substring of the other
    if a_lower in b_lower or b_lower in a_lower:
        ratio = max(ratio, 0.8)

    return ratio


# Leaf segments that never carry the text an eval reads. `input` scores 0.8
# against `llm.input_messages.4.message.role` on containment alone, and that
# path resolves to the literal string "assistant".
_NON_CONTENT_LEAVES = frozenset(
    {"role", "id", "tool_call_id", "mime_type", "json_schema", "index", "type"}
)


def _match_rank(key: str, attr: str) -> tuple:
    """Rank one candidate attribute for a template key. Larger is better.

    The tier outranks the similarity score so an exact name always wins, and
    the last two members break ties deterministically: the candidate list comes
    from an unordered SQL DISTINCT, so ranking on the score alone let the same
    call map differently from one run to the next.
    """
    k, a = key.lower(), attr.lower()
    leaf = a.rsplit(".", 1)[-1]
    if a == k:
        tier = 4
    elif a == f"{k}.value":
        tier = 3
    elif leaf == k:
        tier = 2
    elif leaf not in _NON_CONTENT_LEAVES:
        tier = 1
    else:
        tier = 0
    return (tier, _similarity_score(k, a), -len(a), a)


def _find_best_match(key: str, available_attributes: list[str]) -> tuple[str, tuple]:
    """Best matching attribute for an eval template key, with its rank."""
    if not available_attributes:
        return "", (0, 0.0, 0, "")
    best = max(available_attributes, key=lambda a: _match_rank(key, a))
    return best, _match_rank(key, best)


def _auto_map_keys(
    required_keys: list[str],
    optional_keys: list[str],
    available_attributes: list[str],
) -> dict[str, str]:
    """Auto-map eval template keys to the closest matching span attributes.

    A structural match (tier 2 and up) is taken on its shape. Anything else has
    to clear a real similarity floor, and a key that clears nothing is left
    unmapped: the caller fails closed on a missing required key, which the
    caller can recover from, where a wrong binding is not recoverable.
    """
    mapping = {}
    min_similarity = 0.6

    for key in required_keys + optional_keys:
        best_match, (tier, score, _, _) = _find_best_match(key, available_attributes)
        if best_match and (tier >= 2 or (tier == 1 and score >= min_similarity)):
            mapping[key] = best_match

    return mapping


class CreateCustomEvalConfigInput(PydanticBaseModel):
    project_id: UUID = Field(
        description="The UUID of the project to add the eval config to"
    )
    eval_template_id: UUID = Field(
        description=(
            "The UUID of the eval template to use. "
            "Use list_eval_templates to find available templates."
        )
    )
    name: str = Field(
        description="Name for this eval config",
        min_length=1,
        max_length=255,
    )
    model: Optional[str] = Field(
        default="turing_large",
        description=(
            "Model to use for evaluation. Options: 'turing_large', 'turing_small', 'turing_flash'. "
            "Default: 'turing_large'."
        ),
    )
    mapping: Optional[dict] = Field(
        default=None,
        description=(
            "Mapping of template input keys to span attribute keys. "
            "Values must be valid attribute keys that exist in the project's spans. "
            "Use get_project_eval_attributes to see available keys. "
            "If not provided, the tool will auto-map template keys to the closest "
            "matching span attributes. "
            "Example: {'input': 'llm.input_messages', 'output': 'llm.output_messages'}"
        ),
    )
    config: Optional[dict] = Field(
        default=None,
        description="Runtime config overrides for the eval template",
    )
    error_localizer: bool = Field(
        default=False,
        description="Whether to enable error localizer for this eval",
    )


@register_tool
class CreateCustomEvalConfigTool(BaseTool):
    name = "create_custom_eval_config"
    description = (
        "Creates an evaluation config on a tracing project. "
        "This configures an eval template to run on spans in the project. "
        "Once created, use create_eval_task to run the eval on historical or incoming spans. "
        "If mapping is not provided, it auto-maps template keys to the closest matching "
        "span attributes in the project. If mapping is provided, it validates that all "
        "attribute values exist in the project's spans."
    )
    category = "tracing"
    input_model = CreateCustomEvalConfigInput

    def execute(
        self, params: CreateCustomEvalConfigInput, context: ToolContext
    ) -> ToolResult:

        from django.db.models import Q

        from model_hub.models.evals_metric import EvalTemplate
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.project import Project
        from tracer.utils.eval import with_span_body_fields
        from tracer.utils.sql_queries import SQL_query_handler

        # Validate project
        try:
            project = Project.objects.get(
                id=params.project_id, organization=context.organization
            )
        except Project.DoesNotExist:
            return ToolResult.not_found("Project", str(params.project_id))

        # Validate eval template
        try:
            template = EvalTemplate.no_workspace_objects.get(
                Q(organization=context.organization) | Q(organization__isnull=True),
                id=params.eval_template_id,
            )
        except EvalTemplate.DoesNotExist:
            return ToolResult.not_found("EvalTemplate", str(params.eval_template_id))

        # Check for duplicate name on this project
        if CustomEvalConfig.objects.filter(
            project=project, name=params.name, deleted=False
        ).exists():
            return ToolResult.error(
                f"An eval config named '{params.name}' already exists on this project.",
                error_code="VALIDATION_ERROR",
            )

        # Everything the eval runner can resolve on a span: the project's own
        # span_attributes keys plus the span body fields (`input`, `output`, …).
        available_attributes = with_span_body_fields(
            SQL_query_handler.get_span_attributes_for_project(str(params.project_id))
        )

        # Get required and optional keys from the eval template config
        template_config = template.config or {}
        required_keys = template_config.get("required_keys", []) or []
        optional_keys = template_config.get("optional_keys", []) or []

        if params.mapping:
            # --- User provided mapping: validate attribute values exist ---
            # Fourth write path into CustomEvalConfig.mapping. The
            # attribute-membership check below only sees truthy values, so a
            # falsy non-string ({} or []) used to slip through and get stored;
            # a truthy one was rejected with a misleading "not a valid span
            # attribute". The shared predicate answers the type question first.
            bad_mapping_keys = non_path_mapping_keys(params.mapping)
            if bad_mapping_keys:
                return ToolResult.error(
                    "Mapping values must be attribute path strings. "
                    f"Non-string values for: {', '.join(bad_mapping_keys)}.",
                    error_code="VALIDATION_ERROR",
                )

            invalid_attrs = []
            for key, attr_value in params.mapping.items():
                if attr_value and attr_value not in available_attributes:
                    invalid_attrs.append(attr_value)

            if invalid_attrs:
                attr_list = ", ".join(f"`{a}`" for a in invalid_attrs)
                available_list = ", ".join(
                    f"`{a}`" for a in sorted(available_attributes)
                )
                return ToolResult.error(
                    f"The following mapping values are not valid span attributes "
                    f"in this project: {attr_list}.\n\n"
                    f"Available attributes: {available_list}\n\n"
                    f"Use `get_project_eval_attributes` to see all available attribute keys.",
                    error_code="VALIDATION_ERROR",
                )

            final_mapping = params.mapping

        else:
            # --- No mapping provided: auto-map using similarity ---
            if available_attributes and (required_keys or optional_keys):
                final_mapping = _auto_map_keys(
                    required_keys, optional_keys, available_attributes
                )
                logger.info(
                    "auto_mapped_eval_config",
                    project_id=str(params.project_id),
                    template_id=str(params.eval_template_id),
                    mapping=final_mapping,
                )
                unmapped = [k for k in required_keys if k not in final_mapping]
                if unmapped:
                    lines = []
                    for key in unmapped:
                        ranked = sorted(
                            available_attributes,
                            key=lambda a: _match_rank(key, a),
                            reverse=True,
                        )[:5]
                        lines.append(
                            f"- `{key}`: closest are "
                            + ", ".join(f"`{a}`" for a in ranked)
                        )
                    return ToolResult.error(
                        "Could not auto-map every required key for template "
                        f"'{template.name}'. Call again with an explicit "
                        "`mapping` for:\n" + "\n".join(lines),
                        error_code="VALIDATION_ERROR",
                    )
            else:
                final_mapping = {}

        # Clean up optional keys with empty values from mapping
        if optional_keys:
            for key in optional_keys:
                if key in final_mapping and (
                    final_mapping[key] is None or final_mapping[key] == ""
                ):
                    final_mapping.pop(key)

        # Build config
        eval_config = params.config or {}

        # Handle tone template special case
        if template.name == "tone":
            eval_config["choices"] = template.choices

        # Normalize config against eval template config
        from model_hub.utils.function_eval_params import normalize_eval_runtime_config

        eval_config = normalize_eval_runtime_config(template.config, eval_config)

        # Create the config
        custom_config = CustomEvalConfig.objects.create(
            eval_template=template,
            name=params.name,
            project=project,
            model=params.model or "turing_large",
            mapping=final_mapping,
            config=eval_config,
            error_localizer=params.error_localizer,
        )

        info_pairs = [
            ("Config ID", f"`{custom_config.id}`"),
            ("Name", custom_config.name),
            ("Template", template.name),
            ("Project", project.name),
            ("Model", custom_config.model),
            ("Error Localizer", str(custom_config.error_localizer)),
            ("Created", format_datetime(custom_config.created_at)),
        ]

        if final_mapping:
            mapping_lines = ", ".join(
                f"`{k}` -> `{v}`" for k, v in final_mapping.items()
            )
            info_pairs.append(("Mapping", mapping_lines))
            if not params.mapping:
                info_pairs.append(
                    ("Mapping Source", "auto-mapped from span attributes")
                )

        info = key_value_block(info_pairs)

        content = section("Custom Eval Config Created", info)
        content += (
            "\n\n_Use `create_eval_task` with this config ID to run the eval on spans._"
        )

        return ToolResult(
            content=content,
            data={
                "id": str(custom_config.id),
                "name": custom_config.name,
                "eval_template_id": str(template.id),
                "project_id": str(project.id),
                "model": custom_config.model,
                "mapping": final_mapping,
            },
        )
