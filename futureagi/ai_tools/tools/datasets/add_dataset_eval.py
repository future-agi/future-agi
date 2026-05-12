import os
from typing import Optional

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    key_value_block,
    markdown_table,
    section,
)
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)


def _get_template_required_keys(template) -> list[str]:
    """Return required mapping keys for both current and legacy template shapes."""
    config = getattr(template, "config", None)
    if isinstance(config, dict):
        required_keys = config.get("required_keys") or []
        if required_keys:
            return list(required_keys)
    return list(getattr(template, "required_fields", None) or [])


def _template_candidates_result(
    context: ToolContext,
    title: str = "Eval Template Required",
    detail: str = "",
) -> ToolResult:
    from django.db.models import Q
    from model_hub.models.evals_metric import EvalTemplate

    templates = list(
        EvalTemplate.no_workspace_objects.filter(
            Q(organization=context.organization) | Q(organization__isnull=True)
        ).order_by("-created_at")[:8]
    )
    template_rows = [
        [f"`{template.id}`", template.name, template.owner or "—"]
        for template in templates
    ]
    body = detail or "Choose one of these eval templates, then call `add_dataset_eval` again."
    body += "\n\n"
    body += (
        markdown_table(["ID", "Name", "Owner"], template_rows)
        if template_rows
        else "No eval templates found."
    )
    return ToolResult(
        content=section(title, body),
        data={
            "requires_template_id": True,
            "templates": [
                {"id": str(template.id), "name": template.name}
                for template in templates
            ],
        },
    )


class AddDatasetEvalInput(PydanticBaseModel):
    dataset_id: str = Field(
        default="",
        description="Dataset name or UUID. If omitted, dataset candidates are returned.",
    )
    template_id: str = Field(
        default="",
        description="Eval template name or UUID (e.g. 'faithfulness' or 'hallucination_detection')"
    )
    name: Optional[str] = Field(
        default=None,
        description="Display name for this eval instance. Auto-generated from template name if not provided.",
        max_length=50,
    )
    mapping: Optional[dict[str, str]] = Field(
        default=None,
        description=(
            "Maps eval template keys to dataset column names. "
            "Example: {'output': 'response', 'expected': 'expected_answer'}. "
            "If omitted, auto-detected from column names."
        ),
    )
    config: Optional[dict] = Field(
        default=None,
        description="Optional config overrides (e.g. {'threshold': 0.8})",
    )
    model: Optional[str] = Field(
        default=None,
        description="LLM model for evaluation (default: turing_small)",
    )
    run: bool = Field(
        default=False,
        description="If true, immediately run the eval on all rows",
    )


@register_tool
class AddDatasetEvalTool(BaseTool):
    name = "add_dataset_eval"
    description = (
        "Configures an evaluation template on a dataset by mapping template "
        "input fields to dataset columns. Optionally runs the eval immediately. "
        "Use list_eval_templates to find available templates and their required keys. "
        "Use get_dataset to see available column IDs."
    )
    category = "datasets"
    input_model = AddDatasetEvalInput

    def execute(self, params: AddDatasetEvalInput, context: ToolContext) -> ToolResult:

        from ai_tools.resolvers import resolve_dataset, resolve_eval_template
        from model_hub.models.develop_dataset import Column, Dataset, Row
        from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
        from model_hub.utils.eval_validators import validate_eval_template_org_access

        if not params.dataset_id or not params.template_id:
            from django.db.models import Q

            datasets = list(
                Dataset.objects.filter(
                    organization=context.organization,
                    deleted=False,
                ).order_by("-created_at")[:8]
            )
            templates = list(
                EvalTemplate.no_workspace_objects.filter(
                    Q(organization=context.organization) | Q(organization__isnull=True)
                ).order_by("-created_at")[:8]
            )
            dataset_rows = [
                [
                    f"`{dataset.id}`",
                    dataset.name,
                    str(Row.objects.filter(dataset=dataset, deleted=False).count()),
                ]
                for dataset in datasets
            ]
            template_rows = [
                [
                    f"`{template.id}`",
                    template.name,
                    template.owner or "—",
                ]
                for template in templates
            ]
            content = section(
                "Dataset Eval Requirements",
                "Provide both `dataset_id` and `template_id` to configure an eval.",
            )
            content += "\n\n### Dataset Candidates\n"
            content += (
                markdown_table(["ID", "Name", "Rows"], dataset_rows)
                if dataset_rows
                else "No datasets found."
            )
            content += "\n\n### Eval Template Candidates\n"
            content += (
                markdown_table(["ID", "Name", "Owner"], template_rows)
                if template_rows
                else "No eval templates found."
            )
            return ToolResult(
                content=content,
                data={
                    "requires_dataset_id": not bool(params.dataset_id),
                    "requires_template_id": not bool(params.template_id),
                    "datasets": [
                        {"id": str(dataset.id), "name": dataset.name}
                        for dataset in datasets
                    ],
                    "templates": [
                        {"id": str(template.id), "name": template.name}
                        for template in templates
                    ],
                },
            )

        # Resolve dataset by name or UUID
        dataset, error = resolve_dataset(
            params.dataset_id, context.organization, context.workspace
        )
        if error:
            return ToolResult.error(error, error_code="NOT_FOUND")

        # Resolve eval template by name or UUID
        template_obj, error = resolve_eval_template(
            params.template_id, context.organization, context.workspace
        )
        if template_obj:
            try:
                template = EvalTemplate.objects.get(id=template_obj.id)
            except EvalTemplate.DoesNotExist:
                template = EvalTemplate.objects.filter(name=template_obj.name).first()
                if not template:
                    return _template_candidates_result(
                        context,
                        "Eval Template Not Found",
                        f"Eval template `{params.template_id}` was not found.",
                    )
        else:
            # Try direct EvalTemplate lookup (system templates)
            try:
                from ai_tools.resolvers import is_uuid

                if is_uuid(params.template_id):
                    template = EvalTemplate.objects.get(id=params.template_id)
                else:
                    template = EvalTemplate.objects.filter(
                        name__iexact=params.template_id
                    ).first()
                    if not template:
                        return _template_candidates_result(
                            context,
                            "Eval Template Not Found",
                            error
                            or f"Eval template `{params.template_id}` was not found.",
                        )
            except EvalTemplate.DoesNotExist:
                return _template_candidates_result(
                    context,
                    "Eval Template Not Found",
                    f"Eval template `{params.template_id}` was not found.",
                )

        # Auto-generate name if not provided
        eval_name = params.name or f"{template.name}"

        # Check for duplicate name on this dataset
        if UserEvalMetric.objects.filter(
            dataset=dataset, name=eval_name, deleted=False
        ).exists():
            return ToolResult.error(
                f"An eval named '{eval_name}' already exists on this dataset.",
                error_code="VALIDATION_ERROR",
            )

        # Build column mapping
        dataset_columns = Column.objects.filter(dataset=dataset)
        col_name_to_id = {col.name.lower(): str(col.id) for col in dataset_columns}
        col_name_to_id_exact = {col.name: str(col.id) for col in dataset_columns}
        col_ids = {str(col.id) for col in dataset_columns}

        # Auto-detect mapping if not provided
        if not params.mapping:
            # Try to auto-map eval template keys to dataset columns by name similarity
            required_keys = _get_template_required_keys(template)
            auto_mapping = {}
            for key in required_keys:
                key_lower = key.lower()
                # Exact match
                if key_lower in col_name_to_id:
                    auto_mapping[key] = col_name_to_id[key_lower]
                # Common aliases
                elif key_lower in ("output", "response", "answer", "generated"):
                    for col_name in [
                        "output",
                        "response",
                        "answer",
                        "generated_answer",
                        "generated",
                    ]:
                        if col_name in col_name_to_id:
                            auto_mapping[key] = col_name_to_id[col_name]
                            break
                elif key_lower in (
                    "expected",
                    "expected_output",
                    "ground_truth",
                    "reference",
                ):
                    for col_name in [
                        "expected_output",
                        "expected",
                        "expected_answer",
                        "ground_truth",
                        "reference",
                    ]:
                        if col_name in col_name_to_id:
                            auto_mapping[key] = col_name_to_id[col_name]
                            break
                elif key_lower in ("input", "query", "question", "prompt"):
                    for col_name in ["input", "query", "question", "prompt", "text"]:
                        if col_name in col_name_to_id:
                            auto_mapping[key] = col_name_to_id[col_name]
                            break

            if len(auto_mapping) < len(required_keys):
                missing = set(required_keys) - set(auto_mapping.keys())
                return ToolResult.error(
                    f"Could not auto-map eval fields: {', '.join(missing)}. "
                    f"Available columns: {', '.join(col_name_to_id_exact.keys())}. "
                    f"Please provide the 'mapping' parameter explicitly.",
                    error_code="VALIDATION_ERROR",
                )
            logger.info(
                "auto_mapped_eval_columns",
                dataset=str(dataset.id),
                template=template.name,
                mapping=auto_mapping,
            )
            resolved_mapping = auto_mapping
        else:
            # Resolve provided mapping (accept column names or UUIDs)
            resolved_mapping = {}
            for key, col_ref in params.mapping.items():
                if col_ref in col_ids:
                    resolved_mapping[key] = col_ref
                elif col_ref.lower() in col_name_to_id:
                    resolved_mapping[key] = col_name_to_id[col_ref.lower()]
                elif col_ref in col_name_to_id_exact:
                    resolved_mapping[key] = col_name_to_id_exact[col_ref]
                else:
                    return ToolResult.error(
                        f"Column '{col_ref}' not found in dataset. "
                        f"Available columns: {', '.join(col_name_to_id_exact.keys())}",
                        error_code="VALIDATION_ERROR",
                    )

        # Validate all required template keys are mapped
        from model_hub.utils.eval_validators import validate_required_key_mapping

        required_keys = _get_template_required_keys(template)
        missing_keys = validate_required_key_mapping(resolved_mapping, required_keys)
        if missing_keys:
            return ToolResult.error(
                f"Missing required mapping keys: {', '.join(f'`{k}`' for k in missing_keys)}. "
                f"Required: {', '.join(f'`{k}`' for k in required_keys)}",
                error_code="VALIDATION_ERROR",
            )

        # Build config — always enable reason_column so eval runner creates
        # reason cells (MCP tool always creates reason columns)
        eval_config = {
            "mapping": resolved_mapping,
            "config": params.config or {},
            "reason_column": True,
        }

        # Determine status (must match StatusType enum values exactly)
        from model_hub.models.choices import StatusType

        status = (
            StatusType.NOT_STARTED.value if params.run else StatusType.INACTIVE.value
        )

        # Create UserEvalMetric
        user_eval = UserEvalMetric(
            name=eval_name,
            template=template,
            dataset=dataset,
            config=eval_config,
            status=status,
            model=params.model or template.model or os.environ.get("FALCON_AI_MODEL") or "turing_large",
            organization=context.organization,
            workspace=context.workspace,
            user=context.user,
        )
        user_eval.save()

        # If run=True, create output column + optional reason column
        column_created = False
        if params.run:
            from django.db import transaction

            from model_hub.models.choices import DataTypeChoices, SourceChoices

            with transaction.atomic():
                # Re-fetch dataset for concurrent safety
                from model_hub.models.develop_dataset import Dataset

                dataset = Dataset.objects.get(id=dataset.id)

                # Determine output data type from template
                output_type = "boolean"
                if template.config and isinstance(template.config, dict):
                    output_map = {
                        "reason": "text",
                        "score": "float",
                        "choices": "array",
                        "Pass/Fail": "boolean",
                    }
                    output_type = output_map.get(
                        template.config.get("output", ""), "boolean"
                    )

                col = Column(
                    name=eval_name,
                    data_type=output_type,
                    dataset=dataset,
                    source=SourceChoices.EVALUATION.value,
                    source_id=str(user_eval.id),
                )
                col.save()

                order = dataset.column_order or []
                order.append(str(col.id))

                # Update column_config for visibility
                config = dataset.column_config or {}
                config[str(col.id)] = {"is_visible": True, "is_frozen": None}

                # Only create reason column if template config requests it
                should_create_reason = True
                if template.config and isinstance(template.config, dict):
                    should_create_reason = template.config.get("reason_column", True)

                if should_create_reason:
                    reason_source_id = f"{col.id}-sourceid-{user_eval.id}"
                    reason_col = Column(
                        name=f"{eval_name}-reason",
                        data_type=DataTypeChoices.TEXT.value,
                        dataset=dataset,
                        source=SourceChoices.EVALUATION_REASON.value,
                        source_id=reason_source_id,
                    )
                    reason_col.save()
                    order.append(str(reason_col.id))
                    config[str(reason_col.id)] = {"is_visible": True, "is_frozen": None}

                dataset.column_order = order
                dataset.column_config = config
                dataset.save(update_fields=["column_order", "column_config"])
                column_created = True

        info = key_value_block(
            [
                ("Eval ID", f"`{user_eval.id}`"),
                ("Name", user_eval.name),
                ("Template", template.name),
                ("Model", user_eval.model),
                ("Status", status),
                ("Column Created", "Yes" if column_created else "No (eval not queued)"),
                (
                    "Dataset",
                    dashboard_link("dataset", str(dataset.id), label=dataset.name),
                ),
            ]
        )

        content = section("Dataset Eval Added", info)
        if params.run:
            content += "\n\n_Evaluation queued. Results will appear in the new column._"
        else:
            content += "\n\n_Eval configured but not running. Use `run_dataset_evals` to start._"

        return ToolResult(
            content=content,
            data={
                "eval_id": str(user_eval.id),
                "name": user_eval.name,
                "template": template.name,
                "status": status,
            },
        )
