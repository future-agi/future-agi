from difflib import SequenceMatcher
import os
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


def _find_best_match(key: str, available_attributes: list[str]) -> tuple[str, float]:
    """Find the best matching attribute for a given eval template key."""
    best_match = ""
    best_score = 0.0

    for attr in available_attributes:
        score = _similarity_score(key, attr)
        if score > best_score:
            best_score = score
            best_match = attr

    return best_match, best_score


def _auto_map_keys(
    required_keys: list[str],
    optional_keys: list[str],
    available_attributes: list[str],
) -> dict[str, str]:
    """Auto-map eval template keys to the closest matching span attributes.

    Uses string similarity to find the best match for each required/optional key.
    Only includes optional keys if a sufficiently good match (>= 0.4) is found.
    """
    mapping = {}
    min_score_required = 0.3
    min_score_optional = 0.4

    for key in required_keys:
        best_match, score = _find_best_match(key, available_attributes)
        if score >= min_score_required and best_match:
            mapping[key] = best_match
        # Still include with empty value so caller knows it was not matched
        elif best_match:
            mapping[key] = best_match

    for key in optional_keys:
        best_match, score = _find_best_match(key, available_attributes)
        if score >= min_score_optional and best_match:
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
        default=None,
        description=(
            "Model to use for evaluation. Defaults to FALCON_AI_MODEL when configured, "
            "otherwise turing_large."
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

        # Fetch available eval attributes for the project
        available_attributes = SQL_query_handler.get_span_attributes_for_project(
            str(params.project_id)
        )

        # Get required and optional keys from the eval template config
        template_config = template.config or {}
        required_keys = template_config.get("required_keys", []) or []
        optional_keys = template_config.get("optional_keys", []) or []

        if params.mapping:
            # --- User provided mapping: validate attribute values exist ---
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
            model=params.model or os.environ.get("FALCON_AI_MODEL") or "turing_large",
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
