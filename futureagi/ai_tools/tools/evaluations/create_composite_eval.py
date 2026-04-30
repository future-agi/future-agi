from typing import Literal, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import format_datetime, key_value_block, section
from ai_tools.registry import register_tool


class CreateCompositeEvalInput(PydanticBaseModel):
    name: str = Field(
        description=(
            "Name for the composite evaluation. Must be lowercase alphanumeric "
            "with hyphens or underscores only."
        ),
        min_length=1,
        max_length=255,
    )

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        from model_hub.utils.eval_validators import validate_eval_name

        return validate_eval_name(v)

    description: Optional[str] = Field(
        default=None,
        description="Description of what this composite evaluation measures.",
    )
    child_template_ids: list[str] = Field(
        description=(
            "List of eval template UUIDs to include as children in this composite. "
            "Use list_eval_templates to find template IDs. Minimum 1, maximum 50."
        ),
        min_length=1,
        max_length=50,
    )
    aggregation_enabled: bool = Field(
        default=True,
        description="Whether to aggregate child scores into a single composite score.",
    )
    aggregation_function: Literal[
        "weighted_avg", "avg", "min", "max", "pass_rate"
    ] = Field(
        default="weighted_avg",
        description=(
            "How to aggregate child scores: 'weighted_avg' (default, uses child_weights), "
            "'avg' (simple average), 'min' (lowest score), 'max' (highest score), "
            "'pass_rate' (percentage of children that passed)."
        ),
    )
    child_weights: Optional[dict] = Field(
        default=None,
        description=(
            "Weight per child template for weighted_avg aggregation. "
            "Keys are child template UUIDs, values are float weights. "
            "Example: {'uuid1': 0.6, 'uuid2': 0.4}. "
            "If not provided, equal weights are used."
        ),
    )
    composite_child_axis: Optional[str] = Field(
        default="",
        description=(
            "Enforce homogeneity: all children must have this output type. "
            "Options: 'pass_fail', 'percentage', 'choices', 'code', or '' (no enforcement)."
        ),
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="Tags to categorize this composite evaluation.",
    )


@register_tool
class CreateCompositeEvalTool(BaseTool):
    name = "create_composite_eval"
    description = (
        "Creates a composite evaluation that bundles multiple eval templates into one. "
        "When run, each child eval executes independently on the same data, and their "
        "scores are aggregated into a single result using the chosen function "
        "(weighted_avg, avg, min, max, or pass_rate). "
        "Use this to combine checks like toxicity + relevance + hallucination into one eval. "
        "Use list_eval_templates to find child template IDs first. "
        "All children must have compatible output types (enforced by composite_child_axis)."
    )
    category = "evaluations"
    input_model = CreateCompositeEvalInput

    def execute(
        self, params: CreateCompositeEvalInput, context: ToolContext
    ) -> ToolResult:
        import re

        from django.db.models import Q

        from model_hub.models.choices import OwnerChoices
        from model_hub.models.evals_metric import (
            CompositeEvalChild,
            EvalTemplate,
            EvalTemplateVersion,
        )

        # ── 1. Validate name uniqueness ──
        if EvalTemplate.objects.filter(
            name=params.name,
            organization=context.organization,
            deleted=False,
        ).exists():
            return ToolResult.error(
                f"An eval template named '{params.name}' already exists.",
                error_code="VALIDATION_ERROR",
            )
        if EvalTemplate.no_workspace_objects.filter(
            name=params.name,
            owner=OwnerChoices.SYSTEM.value,
            deleted=False,
        ).exists():
            return ToolResult.error(
                f"A system eval template named '{params.name}' already exists.",
                error_code="VALIDATION_ERROR",
            )

        # ── 2. Verify all child templates exist and are accessible ──
        children = list(
            EvalTemplate.no_workspace_objects.filter(
                id__in=params.child_template_ids, deleted=False
            ).filter(
                Q(owner=OwnerChoices.SYSTEM.value)
                | Q(owner=OwnerChoices.USER.value, organization=context.organization)
            )
        )

        found_ids = {str(c.id) for c in children}
        missing = [cid for cid in params.child_template_ids if cid not in found_ids]
        if missing:
            return ToolResult.error(
                f"Child template(s) not found or not accessible: {', '.join(missing)}",
                error_code="NOT_FOUND",
            )

        # ── 3. Reject nested composites ──
        for child in children:
            if child.template_type == "composite":
                return ToolResult.error(
                    f"Child '{child.name}' is itself a composite. Nested composites are not allowed.",
                    error_code="VALIDATION_ERROR",
                )

        # ── 4. Enforce child axis homogeneity ──
        if params.composite_child_axis:
            axis_to_output = {
                "pass_fail": "pass_fail",
                "percentage": "percentage",
                "choices": "deterministic",
                "code": "code",
            }
            expected = axis_to_output.get(params.composite_child_axis)
            if expected:
                for child in children:
                    child_output = child.output_type_normalized or "pass_fail"
                    if child_output != expected:
                        return ToolResult.error(
                            f"Child '{child.name}' has output type '{child_output}' "
                            f"but composite axis requires '{expected}'.",
                            error_code="VALIDATION_ERROR",
                        )

        # ── 5. Create composite template ──
        try:
            # Infer eval_type from children
            child_types = list({c.eval_type or "llm" for c in children})
            composite_eval_type = child_types[0] if len(child_types) == 1 else "llm"

            template = EvalTemplate.objects.create(
                name=params.name,
                organization=context.organization,
                workspace=context.workspace,
                owner=OwnerChoices.USER.value,
                template_type="composite",
                eval_type=composite_eval_type,
                description=params.description or "",
                eval_tags=list(params.tags) if params.tags else [],
                aggregation_enabled=params.aggregation_enabled,
                aggregation_function=params.aggregation_function,
                composite_child_axis=params.composite_child_axis or "",
                config={},
                proxy_agi=True,
                visible_ui=True,
            )
        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            return ToolResult.error(
                f"Failed to create composite eval: {str(e)}",
                error_code=code_from_exception(e),
            )

        # ── 6. Create child links ──
        weights = params.child_weights or {}
        child_items = []
        for order, child in enumerate(children):
            cec = CompositeEvalChild.objects.create(
                parent=template,
                child=child,
                order=order,
                weight=weights.get(str(child.id), 1.0),
            )
            child_items.append({
                "name": child.name,
                "eval_type": child.eval_type or "llm",
                "weight": cec.weight,
            })

        # ── 7. Create initial version ──
        try:
            EvalTemplateVersion.objects.create_version(
                eval_template=template,
                prompt_messages=[],
                config_snapshot={
                    "aggregation_enabled": params.aggregation_enabled,
                    "aggregation_function": params.aggregation_function,
                    "composite_child_axis": params.composite_child_axis or "",
                    "children": [str(c.id) for c in children],
                },
                criteria="",
                model="",
                user=context.user,
                organization=context.organization,
                workspace=context.workspace,
            )
        except Exception:
            pass  # Non-fatal

        # ── 8. Build response ──
        children_summary = "\n".join(
            f"  - {c['name']} ({c['eval_type']}, weight: {c['weight']})"
            for c in child_items
        )

        info = key_value_block(
            [
                ("ID", f"`{template.id}`"),
                ("Name", template.name),
                ("Type", "Composite"),
                ("Children", str(len(child_items))),
                ("Aggregation", params.aggregation_function),
                ("Child Axis", params.composite_child_axis or "any"),
                ("Tags", ", ".join(template.eval_tags) if template.eval_tags else "—"),
                ("Created", format_datetime(template.created_at)),
            ]
        )

        content = section("Composite Eval Created", info)
        content += f"\n\n### Children\n\n{children_summary}"
        content += "\n\n_Use `add_dataset_eval` to add this composite eval to a dataset, then `run_dataset_evals` to execute._"

        return ToolResult(
            content=content,
            data={
                "id": str(template.id),
                "name": template.name,
                "template_type": "composite",
                "children": child_items,
                "aggregation_function": params.aggregation_function,
            },
        )
