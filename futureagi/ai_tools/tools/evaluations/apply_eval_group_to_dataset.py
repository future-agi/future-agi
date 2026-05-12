import json
from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import key_value_block, markdown_table, section
from ai_tools.registry import register_tool


def _get_template_required_keys(template) -> list[str]:
    config = getattr(template, "config", None)
    if not isinstance(config, dict):
        return []
    return list(config.get("required_keys") or [])


def _auto_map_required_keys(required_keys: list[str], col_name_to_id: dict[str, str]):
    aliases = {
        "input": [
            "input",
            "query",
            "question",
            "prompt",
            "text",
            "user_input",
            "request",
        ],
        "query": ["query", "question", "input", "prompt", "text", "user_input"],
        "question": ["question", "query", "input", "prompt", "text"],
        "prompt": ["prompt", "input", "query", "question", "text"],
        "output": [
            "output",
            "response",
            "answer",
            "generated",
            "generated_answer",
            "completion",
            "prediction",
            "actual_output",
            "model_output",
        ],
        "response": [
            "response",
            "output",
            "answer",
            "generated",
            "completion",
            "prediction",
            "model_output",
        ],
        "answer": ["answer", "response", "output", "expected_answer"],
        "expected": [
            "expected",
            "expected_output",
            "expected_answer",
            "ground_truth",
            "reference",
            "label",
            "target",
        ],
        "expected_output": [
            "expected_output",
            "expected",
            "expected_answer",
            "ground_truth",
            "reference",
            "label",
            "target",
        ],
        "ground_truth": [
            "ground_truth",
            "expected",
            "expected_output",
            "expected_answer",
            "reference",
            "label",
            "target",
        ],
        "reference": [
            "reference",
            "ground_truth",
            "expected",
            "expected_output",
            "expected_answer",
        ],
        "context": [
            "context",
            "contexts",
            "document",
            "documents",
            "source",
            "sources",
            "retrieved_context",
            "retrieved_contexts",
        ],
    }

    resolved = {}
    for key in required_keys:
        key_lower = str(key).lower()
        candidates = [key_lower]
        candidates.extend(aliases.get(key_lower, []))
        for candidate in candidates:
            if candidate in col_name_to_id:
                resolved[key] = col_name_to_id[candidate]
                break
    return resolved


def _normalize_mapping(raw_mapping, col_ids, col_name_to_id, col_name_to_id_exact):
    if not raw_mapping:
        return {}, []
    if isinstance(raw_mapping, str):
        try:
            raw_mapping = json.loads(raw_mapping)
        except json.JSONDecodeError:
            return {}, [raw_mapping]
    if not isinstance(raw_mapping, dict):
        return {}, [str(raw_mapping)]

    resolved = {}
    unknown = []
    for key, col_ref in raw_mapping.items():
        ref = str(col_ref).strip()
        if ref in col_ids:
            resolved[key] = ref
        elif ref.lower() in col_name_to_id:
            resolved[key] = col_name_to_id[ref.lower()]
        elif ref in col_name_to_id_exact:
            resolved[key] = col_name_to_id_exact[ref]
        else:
            unknown.append(ref)
    return resolved, unknown


def _mapping_requirements_result(
    eval_group,
    dataset,
    eval_templates,
    required_keys,
    resolved_mapping,
    missing_keys,
    unknown_columns=None,
):
    from model_hub.models.develop_dataset import Column

    columns = list(Column.objects.filter(dataset=dataset, deleted=False))
    column_rows = [
        [f"`{column.id}`", column.name, column.data_type] for column in columns
    ]
    template_rows = [
        [
            f"`{template.id}`",
            template.name,
            ", ".join(_get_template_required_keys(template)) or "-",
        ]
        for template in eval_templates
    ]
    details = []
    if missing_keys:
        details.append(
            "Missing mapping keys: "
            + ", ".join(f"`{key}`" for key in sorted(set(missing_keys)))
        )
    if unknown_columns:
        details.append(
            "Unknown columns: "
            + ", ".join(f"`{column}`" for column in sorted(set(unknown_columns)))
        )
    if resolved_mapping:
        details.append(
            "Suggested partial mapping: "
            + ", ".join(
                f"`{key}` -> `{value}`"
                for key, value in sorted(resolved_mapping.items())
            )
        )
    if required_keys:
        details.append(
            "Required keys: "
            + ", ".join(f"`{key}`" for key in sorted(set(required_keys)))
        )

    content = section(
        "Eval Group Mapping Required",
        "\n\n".join(details)
        or "Provide a mapping from eval template keys to dataset column IDs.",
    )
    content += "\n\n### Eval Templates\n"
    content += (
        markdown_table(["ID", "Name", "Required Keys"], template_rows)
        if template_rows
        else "No templates found in this eval group."
    )
    content += "\n\n### Dataset Columns\n"
    content += (
        markdown_table(["ID", "Name", "Type"], column_rows)
        if column_rows
        else "No columns found in this dataset."
    )
    return ToolResult(
        content=content,
        data={
            "eval_group_id": str(eval_group.id),
            "eval_group_name": eval_group.name,
            "dataset_id": str(dataset.id),
            "dataset_name": dataset.name,
            "required_keys": sorted(set(required_keys)),
            "missing_keys": sorted(set(missing_keys)),
            "suggested_mapping": resolved_mapping,
            "unknown_columns": sorted(set(unknown_columns or [])),
            "requires_mapping": True,
        },
    )


class ApplyEvalGroupToDatasetInput(PydanticBaseModel):
    eval_group_id: str = Field(
        default="",
        description="Eval group name or UUID. If omitted, candidate groups are returned.",
    )
    dataset_id: str = Field(
        default="",
        description="Dataset name or UUID. If omitted, candidate datasets are returned.",
    )
    mapping: Optional[dict | str] = Field(
        default=None,
        description=(
            "Field mapping from eval template keys to dataset column IDs. "
            'Example: {"response": "<column_uuid>", "query": "<column_uuid>"}'
        ),
    )
    filters: Optional[dict] = Field(
        default=None,
        description=(
            "Additional filters: kb_id (knowledge base UUID), model (string), "
            "error_localizer (bool)"
        ),
    )
    params: Optional[dict] = Field(
        default=None,
        description="Additional parameters for the evaluation",
    )


@register_tool
class ApplyEvalGroupToDatasetTool(BaseTool):
    name = "apply_eval_group_to_dataset"
    description = (
        "Applies an evaluation group to a dataset. Creates UserEvalMetric "
        "entries for each eval template in the group, linking them to the dataset. "
        "Use list_eval_groups to find groups and list_datasets for dataset IDs."
    )
    category = "evaluations"
    input_model = ApplyEvalGroupToDatasetInput

    def execute(
        self, params: ApplyEvalGroupToDatasetInput, context: ToolContext
    ) -> ToolResult:

        from django.db.models import Q

        from ai_tools.resolvers import resolve_dataset
        from model_hub.models.develop_dataset import Dataset
        from model_hub.models.eval_groups import EvalGroup
        from model_hub.models.evals_metric import EvalTemplate
        from model_hub.services.eval_group import apply_eval_group

        if not params.eval_group_id or not params.dataset_id:
            from model_hub.models.develop_dataset import Row

            datasets = list(
                Dataset.objects.filter(
                    organization=context.organization,
                    deleted=False,
                ).order_by("-created_at")[:8]
            )
            groups = list(
                EvalGroup.no_workspace_objects.filter(
                    Q(organization=context.organization, workspace=context.workspace)
                    | Q(is_sample=True)
                )
                .prefetch_related("eval_templates")
                .order_by("-created_at")[:8]
            )
            dataset_rows = [
                [
                    f"`{dataset.id}`",
                    dataset.name,
                    str(Row.objects.filter(dataset=dataset, deleted=False).count()),
                ]
                for dataset in datasets
            ]
            group_rows = [
                [
                    f"`{group.id}`",
                    group.name,
                    str(group.eval_templates.count()),
                    "Yes" if group.is_sample else "No",
                ]
                for group in groups
            ]
            content = section(
                "Eval Group Application Requirements",
                "Provide both `eval_group_id` and `dataset_id` to apply an eval group.",
            )
            content += "\n\n### Dataset Candidates\n"
            content += (
                markdown_table(["ID", "Name", "Rows"], dataset_rows)
                if dataset_rows
                else "No datasets found."
            )
            content += "\n\n### Eval Group Candidates\n"
            content += (
                markdown_table(["ID", "Name", "Templates", "Sample"], group_rows)
                if group_rows
                else "No eval groups found."
            )
            return ToolResult(
                content=content,
                data={
                    "requires_eval_group_id": not bool(params.eval_group_id),
                    "requires_dataset_id": not bool(params.dataset_id),
                    "datasets": [
                        {"id": str(dataset.id), "name": dataset.name}
                        for dataset in datasets
                    ],
                    "groups": [
                        {"id": str(group.id), "name": group.name} for group in groups
                    ],
                },
            )

        # Validate eval group
        group_ref = params.eval_group_id.strip()
        try:
            query = (
                Q(
                    id=group_ref,
                    organization=context.organization,
                    workspace=context.workspace,
                )
                | Q(id=group_ref, is_sample=True)
                | Q(
                    name__iexact=group_ref,
                    organization=context.organization,
                    workspace=context.workspace,
                )
                | Q(name__iexact=group_ref, is_sample=True)
            )
            eval_group = EvalGroup.no_workspace_objects.get(query)
        except EvalGroup.DoesNotExist:
            return ToolResult.not_found("Eval Group", group_ref)
        except (ValueError, TypeError):
            return ToolResult.not_found("Eval Group", group_ref)

        # Validate dataset
        dataset, dataset_error = resolve_dataset(
            params.dataset_id, context.organization, context.workspace
        )
        if dataset_error:
            return ToolResult.error(dataset_error, error_code="NOT_FOUND")

        template_ids = list(
            eval_group.eval_templates.through.objects.filter(
                evalgroup_id=eval_group.id
            ).values_list("evaltemplate_id", flat=True)
        )
        eval_templates = list(
            EvalTemplate.no_workspace_objects.filter(id__in=template_ids)
        )

        # Build filters dict (apply_eval_group expects dataset_id in filters)
        filters = params.filters or {}
        filters["dataset_id"] = str(dataset.id)

        from model_hub.models.develop_dataset import Column

        dataset_columns = Column.objects.filter(dataset=dataset, deleted=False)
        col_name_to_id = {col.name.lower(): str(col.id) for col in dataset_columns}
        col_name_to_id_exact = {col.name: str(col.id) for col in dataset_columns}
        col_ids = {str(col.id) for col in dataset_columns}

        mapping, unknown_columns = _normalize_mapping(
            params.mapping, col_ids, col_name_to_id, col_name_to_id_exact
        )
        required_keys = []
        for template in eval_templates:
            required_keys.extend(_get_template_required_keys(template))
        required_keys = sorted(set(required_keys))

        if not mapping:
            mapping = _auto_map_required_keys(required_keys, col_name_to_id)
        else:
            auto_mapping = _auto_map_required_keys(required_keys, col_name_to_id)
            for key, value in auto_mapping.items():
                mapping.setdefault(key, value)

        missing_keys = [key for key in required_keys if key not in mapping]
        if missing_keys or unknown_columns:
            return _mapping_requirements_result(
                eval_group,
                dataset,
                eval_templates,
                required_keys,
                mapping,
                missing_keys,
                unknown_columns,
            )

        try:
            apply_eval_group(
                eval_group=eval_group,
                filters=filters,
                mapping=mapping,
                page_id="DATASET",
                user=context.user,
                workspace=context.workspace,
                deselected_evals=None,
                params=params.params,
            )
        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            if "not found in mapping" in str(e).lower():
                return _mapping_requirements_result(
                    eval_group,
                    dataset,
                    eval_templates,
                    required_keys,
                    mapping,
                    required_keys,
                )
            return ToolResult.error(
                f"Failed to apply eval group: {str(e)}",
                error_code=code_from_exception(e),
            )

        # Count templates in the group
        template_count = eval_group.eval_templates.count()

        info = key_value_block(
            [
                ("Eval Group", f"{eval_group.name} (`{str(eval_group.id)}`)"),
                ("Dataset", f"{dataset.name} (`{str(dataset.id)}`)"),
                ("Templates Applied", str(template_count)),
            ]
        )

        content = section("Eval Group Applied to Dataset", info)
        content += (
            "\n\n_Evaluation metrics have been created for each template in the group. "
            "Run evaluations on the dataset to see results._"
        )

        return ToolResult(
            content=content,
            data={
                "eval_group_id": str(eval_group.id),
                "eval_group_name": eval_group.name,
                "dataset_id": str(dataset.id),
                "dataset_name": dataset.name,
                "template_count": template_count,
            },
        )
