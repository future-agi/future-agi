from typing import Any
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    markdown_table,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.annotations._utils import candidate_annotations_result


class AnnotationSummaryInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    dataset_id: str = Field(
        default="",
        description=(
            "Dataset name/UUID, or annotation task name/UUID. "
            "Omit it to list candidate datasets."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if not normalized.get("dataset_id"):
            normalized["dataset_id"] = (
                normalized.get("dataset")
                or normalized.get("annotation_id")
                or normalized.get("task_id")
                or normalized.get("id")
                or normalized.get("name")
                or ""
            )
        return normalized


@register_tool
class AnnotationSummaryTool(BaseTool):
    name = "annotation_summary"
    description = (
        "Returns annotation summary statistics for a dataset including "
        "per-label metrics (agreement scores, distributions), "
        "per-annotator stats (completion count, average time), "
        "and dataset-level coverage and progress."
    )
    category = "annotations"
    input_model = AnnotationSummaryInput

    def execute(
        self, params: AnnotationSummaryInput, context: ToolContext
    ) -> ToolResult:

        from model_hub.models.develop_annotations import Annotations
        from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

        target_ref = self._clean_ref(params.dataset_id)
        if not target_ref:
            datasets = Dataset.objects.filter(
                organization=context.organization,
                deleted=False,
            ).order_by("-created_at")[:10]
            rows = [
                [
                    f"`{dataset.id}`",
                    dataset.name,
                    str(Annotations.objects.filter(dataset=dataset).count()),
                ]
                for dataset in datasets
            ]
            if not rows:
                return ToolResult(
                    content=section(
                        "Annotation Summary Candidates", "No datasets found."
                    ),
                    data={"datasets": []},
                )
            return ToolResult(
                content=section(
                    "Annotation Summary Candidates",
                    markdown_table(["Dataset ID", "Name", "Annotation Tasks"], rows),
                ),
                data={
                    "datasets": [
                        {"id": str(dataset.id), "name": dataset.name}
                        for dataset in datasets
                    ]
                },
            )

        dataset = self._resolve_dataset(target_ref, Dataset, context)
        if dataset is None:
            annotation = self._resolve_annotation(target_ref, Annotations, context)
            dataset = annotation.dataset if annotation and annotation.dataset else None
        if dataset is None:
            return candidate_annotations_result(
                context,
                title="Annotation Summary Target Not Found",
                detail=(
                    f"`{target_ref}` is not a dataset or annotation task in this "
                    "workspace. Use one of these annotation task IDs, or call "
                    "without `dataset_id` to list candidate datasets."
                ),
            )

        total_rows = Row.objects.filter(dataset=dataset, deleted=False).count()

        # Get all annotations for this dataset
        annotations = Annotations.objects.filter(
            dataset=dataset,
        ).prefetch_related("labels", "assigned_users")

        if not annotations.exists():
            return ToolResult(
                content=section(
                    f"Annotation Summary: {dataset.name}",
                    "_No annotation tasks found for this dataset._",
                ),
                data={"annotations": [], "total_rows": total_rows},
            )

        # Build summary per annotation task
        ann_rows = []
        ann_data = []
        for ann in annotations:
            # Count completed rows
            ann_cols = Column.objects.filter(
                dataset=dataset,
                source="annotation_label",
                source_id__startswith=str(ann.id),
                deleted=False,
            )

            completed_cells = 0
            total_cells = 0
            for col in ann_cols:
                cells = Cell.objects.filter(column=col, dataset=dataset, deleted=False)
                total_cells += cells.count()
                completed_cells += (
                    cells.exclude(value="").exclude(value__isnull=True).count()
                )

            pct = (completed_cells / total_cells * 100) if total_cells > 0 else 0

            users = ann.assigned_users.all()
            label_names = [label.name for label in ann.labels.all()]

            ann_rows.append(
                [
                    ann.name,
                    str(users.count()),
                    ", ".join(label_names[:3])
                    + ("..." if len(label_names) > 3 else ""),
                    str(ann.responses),
                    f"{completed_cells}/{total_cells} ({pct:.0f}%)",
                    str(ann.lowest_unfinished_row or 0),
                ]
            )

            ann_data.append(
                {
                    "id": str(ann.id),
                    "name": ann.name,
                    "users": users.count(),
                    "labels": label_names,
                    "responses": ann.responses,
                    "completed_cells": completed_cells,
                    "total_cells": total_cells,
                    "completion_pct": round(pct, 1),
                }
            )

        table = markdown_table(
            [
                "Task",
                "Annotators",
                "Labels",
                "Responses/Row",
                "Progress",
                "Lowest Unfinished",
            ],
            ann_rows,
        )

        # Per-annotator stats
        annotator_rows = []
        all_users = set()
        for ann in annotations:
            for u in ann.assigned_users.all():
                all_users.add(u)

        for user in all_users:
            # Count cells annotated by this user
            user_count = 0
            for ann in annotations:
                ann_cols = Column.objects.filter(
                    dataset=dataset,
                    source="annotation_label",
                    source_id__startswith=str(ann.id),
                    deleted=False,
                )
                for col in ann_cols:
                    user_count += (
                        Cell.objects.filter(
                            column=col,
                            dataset=dataset,
                            deleted=False,
                            feedback_info__annotation__user_id=str(user.id),
                        )
                        .exclude(value="")
                        .count()
                    )

            if user_count > 0:
                annotator_rows.append(
                    [
                        user.email or user.name or str(user.id),
                        str(user_count),
                    ]
                )

        content = section(
            f"Annotation Summary: {dataset.name} ({total_rows} rows)",
            table,
        )

        if annotator_rows:
            annotator_table = markdown_table(
                ["Annotator", "Annotations"],
                annotator_rows,
            )
            content += f"\n\n### Per-Annotator Stats\n\n{annotator_table}"

        return ToolResult(
            content=content,
            data={"annotations": ann_data, "total_rows": total_rows},
        )

    @staticmethod
    def _clean_ref(value: str) -> str:
        return (value or "").strip().strip("`'\"“”‘’")

    @staticmethod
    def _looks_like_uuid(value: str) -> bool:
        try:
            UUID(str(value))
        except (TypeError, ValueError):
            return False
        return True

    @classmethod
    def _resolve_dataset(cls, target_ref: str, Dataset, context):
        base = Dataset.objects.filter(
            deleted=False,
            organization=context.organization,
        )
        if cls._looks_like_uuid(target_ref):
            dataset = base.filter(id=target_ref).first()
            if dataset:
                return dataset
        dataset = base.filter(name__iexact=target_ref).first()
        if dataset:
            return dataset
        return base.filter(name__icontains=target_ref).order_by("-created_at").first()

    @classmethod
    def _resolve_annotation(cls, target_ref: str, Annotations, context):
        base = Annotations.objects.select_related("dataset").filter(
            organization=context.organization,
            deleted=False,
            dataset__deleted=False,
            dataset__organization=context.organization,
        )
        if cls._looks_like_uuid(target_ref):
            annotation = base.filter(id=target_ref).first()
            if annotation:
                return annotation
        annotation = base.filter(name__iexact=target_ref).first()
        if annotation:
            return annotation
        return base.filter(name__icontains=target_ref).order_by("-created_at").first()
