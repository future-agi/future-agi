from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.annotation_queues._utils import (
    clean_ref,
    resolve_labels,
    resolve_users,
    uuid_text,
)

VALID_STRATEGIES = {"manual", "round_robin", "load_balanced"}


class CreateAnnotationQueueInput(PydanticBaseModel):
    name: Optional[str] = Field(
        default=None,
        description="Name for the annotation queue",
        min_length=1,
        max_length=255,
    )
    description: Optional[str] = Field(
        default=None, description="Description of the queue"
    )
    instructions: Optional[str] = Field(
        default=None, description="Instructions for annotators"
    )
    assignment_strategy: str = Field(
        default="manual",
        description="Assignment strategy: manual, round_robin, or load_balanced",
    )
    annotations_required: int = Field(
        default=1, ge=1, le=10, description="Number of annotations required per item"
    )
    requires_review: bool = Field(
        default=False, description="Whether annotations require review"
    )
    project_id: Optional[str] = Field(
        default=None, description="Project UUID or exact project name to scope the queue to"
    )
    dataset_id: Optional[str] = Field(
        default=None, description="Dataset UUID or exact dataset name to scope the queue to"
    )
    agent_definition_id: Optional[str] = Field(
        default=None,
        description="Agent definition UUID or exact name to scope the queue to",
    )
    label_ids: Optional[list[str]] = Field(
        default=None,
        description="Annotation label UUIDs or exact names to attach to this queue",
    )
    annotator_ids: Optional[list[str]] = Field(
        default=None,
        description="User UUIDs, emails, or exact names to assign as annotators",
    )


@register_tool
class CreateAnnotationQueueTool(BaseTool):
    name = "create_annotation_queue"
    description = (
        "Creates an annotation queue for organizing annotation workflows. "
        "Queues can be scoped to a project, dataset, or agent definition (simulation). "
        "Items (traces, spans, dataset rows, call executions) can be added to the queue "
        "and assigned to annotators."
    )
    category = "annotations"
    input_model = CreateAnnotationQueueInput

    def execute(
        self, params: CreateAnnotationQueueInput, context: ToolContext
    ) -> ToolResult:
        from model_hub.models.annotation_queues import (
            AnnotationQueue,
            AnnotationQueueAnnotator,
            AnnotationQueueLabel,
        )

        warnings = []
        queue_name = clean_ref(params.name)
        if not queue_name:
            return ToolResult(
                content=section(
                    "Annotation Queue Details Required",
                    (
                        "Provide at least `name` before creating an annotation queue. "
                        "Optional fields include `description`, `instructions`, "
                        "`label_ids`, `annotator_ids`, and one scope field: "
                        "`project_id`, `dataset_id`, or `agent_definition_id`."
                    ),
                ),
                data={
                    "requires_name": True,
                    "required_fields": ["name"],
                    "optional_fields": [
                        "description",
                        "instructions",
                        "assignment_strategy",
                        "annotations_required",
                        "requires_review",
                        "project_id",
                        "dataset_id",
                        "agent_definition_id",
                        "label_ids",
                        "annotator_ids",
                    ],
                },
            )

        assignment_strategy = (
            clean_ref(params.assignment_strategy)
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
            or "manual"
        )
        if assignment_strategy not in VALID_STRATEGIES:
            warnings.append(
                f"Unknown assignment strategy `{params.assignment_strategy}`; used `manual`."
            )
            assignment_strategy = "manual"

        existing_queue = AnnotationQueue.objects.filter(
            name__iexact=queue_name,
            organization=context.organization,
            workspace=context.workspace,
            deleted=False,
        ).first()
        if existing_queue:
            info = key_value_block(
                [
                    ("ID", f"`{existing_queue.id}`"),
                    ("Name", existing_queue.name),
                    ("Strategy", existing_queue.assignment_strategy),
                    ("Annotations Required", str(existing_queue.annotations_required)),
                    ("Status", existing_queue.status),
                ]
            )
            return ToolResult(
                content=section("Annotation Queue Already Exists", info),
                data={
                    "queue_id": str(existing_queue.id),
                    "name": existing_queue.name,
                    "status": existing_queue.status,
                    "already_exists": True,
                },
            )

        project, unresolved = _resolve_project(params.project_id, context)
        if unresolved:
            return unresolved
        dataset, unresolved = _resolve_dataset(params.dataset_id, context)
        if unresolved:
            return unresolved
        agent_definition, unresolved = _resolve_agent_definition(
            params.agent_definition_id, context
        )
        if unresolved:
            return unresolved

        queue = AnnotationQueue(
            name=queue_name,
            description=params.description or "",
            instructions=params.instructions or "",
            assignment_strategy=assignment_strategy,
            annotations_required=params.annotations_required,
            requires_review=params.requires_review,
            organization=context.organization,
            workspace=context.workspace,
            project=project,
            dataset=dataset,
            agent_definition=agent_definition,
            created_by=context.user,
        )
        queue.save()

        # Attach labels
        labels_added = 0
        if params.label_ids:
            labels, missing_labels = resolve_labels(params.label_ids, context)
            if missing_labels:
                warnings.append(
                    "Labels not found: " + ", ".join(f"`{ref}`" for ref in missing_labels)
                )
            for idx, label in enumerate(labels):
                AnnotationQueueLabel.objects.create(queue=queue, label=label, order=idx)
                labels_added += 1

        # Attach annotators
        annotators_added = 0
        if params.annotator_ids:
            users, missing_users = resolve_users(params.annotator_ids, context)
            if missing_users:
                warnings.append(
                    "Annotators not found: "
                    + ", ".join(f"`{ref}`" for ref in missing_users)
                )
            for user in users:
                AnnotationQueueAnnotator.objects.create(queue=queue, user=user)
                annotators_added += 1

        scope = "—"
        if project:
            scope = f"Project: {project.name}"
        elif dataset:
            scope = f"Dataset: {dataset.name}"
        elif agent_definition:
            scope = f"Agent: {agent_definition.agent_name}"

        info = key_value_block(
            [
                ("ID", f"`{queue.id}`"),
                ("Name", queue.name),
                ("Strategy", queue.assignment_strategy),
                ("Annotations Required", str(queue.annotations_required)),
                ("Scope", scope),
                ("Labels", str(labels_added)),
                ("Annotators", str(annotators_added)),
                ("Status", "draft"),
                ("Warnings", "; ".join(warnings) if warnings else "None"),
            ]
        )

        content = section("Annotation Queue Created", info)

        return ToolResult(
            content=content,
            data={
                "queue_id": str(queue.id),
                "name": queue.name,
                "status": queue.status,
                "labels_added": labels_added,
                "annotators_added": annotators_added,
                "warnings": warnings,
            },
        )


def _not_found_result(entity: str, ref: str, lookup_tool: str) -> ToolResult:
    return ToolResult(
        content=section(
            f"{entity} Not Found",
            f"`{ref}` was not found. Use `{lookup_tool}` first, then retry with an ID from the result.",
        ),
        data={
            "requires_lookup": True,
            "entity": entity,
            "provided": ref,
            "lookup_tool": lookup_tool,
        },
    )


def _resolve_project(project_ref: Optional[str], context: ToolContext):
    ref = clean_ref(project_ref)
    if not ref:
        return None, None
    from tracer.models.project import Project

    ref_uuid = uuid_text(ref)
    qs = Project.objects.filter(organization=context.organization)
    if ref_uuid:
        project = qs.filter(id=ref_uuid).first()
    else:
        project = qs.filter(name__iexact=ref).first() or qs.filter(
            name__icontains=ref
        ).first()
    if not project:
        return None, _not_found_result("Project", ref, "list_projects")
    return project, None


def _resolve_dataset(dataset_ref: Optional[str], context: ToolContext):
    ref = clean_ref(dataset_ref)
    if not ref:
        return None, None
    from model_hub.models.develop_dataset import Dataset

    ref_uuid = uuid_text(ref)
    qs = Dataset.objects.filter(organization=context.organization, deleted=False)
    if ref_uuid:
        dataset = qs.filter(id=ref_uuid).first()
    else:
        dataset = qs.filter(name__iexact=ref).first() or qs.filter(
            name__icontains=ref
        ).first()
    if not dataset:
        return None, _not_found_result("Dataset", ref, "list_datasets")
    return dataset, None


def _resolve_agent_definition(agent_ref: Optional[str], context: ToolContext):
    ref = clean_ref(agent_ref)
    if not ref:
        return None, None
    from simulate.models import AgentDefinition

    ref_uuid = uuid_text(ref)
    qs = AgentDefinition.objects.filter(organization=context.organization, deleted=False)
    if ref_uuid:
        agent_definition = qs.filter(id=ref_uuid).first()
    else:
        agent_definition = qs.filter(agent_name__iexact=ref).first() or qs.filter(
            agent_name__icontains=ref
        ).first()
    if not agent_definition:
        return None, _not_found_result("Agent Definition", ref, "list_agents")
    return agent_definition, None
