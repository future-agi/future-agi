from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Count, Q

from ai_tools.base import ToolContext, ToolResult
from ai_tools.formatting import format_datetime, format_status, markdown_table, section


def clean_ref(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def uuid_text(value: Any) -> str | None:
    ref = clean_ref(value)
    if not ref:
        return None
    try:
        return str(UUID(ref))
    except (TypeError, ValueError, AttributeError):
        return None


def project_queryset(context: ToolContext):
    from tracer.models.project import Project

    qs = Project.objects.filter(organization=context.organization, deleted=False)
    if context.workspace:
        qs = qs.filter(Q(workspace=context.workspace) | Q(workspace__isnull=True))
    return qs


def candidate_projects_result(
    context: ToolContext,
    title: str = "Project Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    qs = project_queryset(context).annotate(trace_count=Count("traces"))
    search = clean_ref(search)
    if search and not uuid_text(search):
        qs = qs.filter(name__icontains=search)
    projects = list(qs.order_by("-created_at")[:10])

    rows = [
        [
            project.name,
            f"`{project.id}`",
            project.trace_type or "-",
            str(project.trace_count),
            format_datetime(project.created_at),
        ]
        for project in projects
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Name", "Project ID", "Type", "Traces", "Created"],
            rows,
        )
    else:
        body = body or "No tracing projects found in this workspace."
    return ToolResult.needs_input(
        body,
        data={
            "requires_project_id": True,
            "projects": [
                {"id": str(project.id), "name": project.name}
                for project in projects
            ],
        },
        missing_fields=["project_id"],
    )


def resolve_project(
    project_ref: Any,
    context: ToolContext,
    title: str = "Project Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = clean_ref(project_ref)
    if not ref:
        return None, candidate_projects_result(context, title)

    qs = project_queryset(context)
    ref_uuid = uuid_text(ref)
    try:
        if ref_uuid:
            project = qs.filter(id=ref_uuid).first()
            if project:
                return project, None
        else:
            exact = list(qs.filter(name__iexact=ref).order_by("-created_at")[:2])
            if len(exact) == 1:
                return exact[0], None
            if len(exact) > 1:
                return None, candidate_projects_result(
                    context,
                    "Multiple Projects Matched",
                    f"More than one project matched `{ref}`. Use one project ID.",
                    search=ref,
                )
            fuzzy = list(qs.filter(name__icontains=ref).order_by("-created_at")[:2])
            if len(fuzzy) == 1:
                return fuzzy[0], None
    except (ValidationError, ValueError, TypeError):
        pass

    return None, candidate_projects_result(
        context,
        "Project Not Found",
        f"Project `{ref}` was not found. Use one of these project IDs.",
        search="" if ref_uuid else ref,
    )


def trace_queryset(context: ToolContext):
    from tracer.models.trace import Trace

    qs = Trace.objects.select_related("project").filter(
        project__organization=context.organization,
        project__deleted=False,
    )
    if context.workspace:
        qs = qs.filter(
            Q(project__workspace=context.workspace)
            | Q(project__workspace__isnull=True)
        )
    return qs


def candidate_traces_result(
    context: ToolContext,
    title: str = "Trace Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    qs = trace_queryset(context)
    search = clean_ref(search)
    if search and not uuid_text(search):
        qs = qs.filter(name__icontains=search)
    traces = list(qs.order_by("-created_at")[:10])
    rows = [
        [
            trace.name or "-",
            f"`{trace.id}`",
            trace.project.name if trace.project else "-",
            format_datetime(trace.created_at),
        ]
        for trace in traces
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Name", "Trace ID", "Project", "Created"],
            rows,
        )
    else:
        body = body or "No traces found in this workspace."
    return ToolResult.needs_input(
        body,
        data={
            "requires_trace_id": True,
            "traces": [
                {
                    "id": str(trace.id),
                    "name": trace.name,
                    "project": trace.project.name if trace.project else None,
                }
                for trace in traces
            ],
        },
        missing_fields=["trace_id"],
    )


def resolve_trace(
    trace_ref: Any,
    context: ToolContext,
    title: str = "Trace Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = clean_ref(trace_ref)
    if not ref:
        return None, candidate_traces_result(context, title)
    qs = trace_queryset(context)
    ref_uuid = uuid_text(ref)
    if ref_uuid:
        trace = qs.filter(id=ref_uuid).first()
        if trace:
            return trace, None
    else:
        exact = list(qs.filter(name__iexact=ref).order_by("-created_at")[:2])
        if len(exact) == 1:
            return exact[0], None
        if len(exact) > 1:
            return None, candidate_traces_result(
                context,
                "Multiple Traces Matched",
                f"More than one trace matched `{ref}`. Use a trace ID.",
                search=ref,
            )
    return None, candidate_traces_result(
        context,
        "Trace Not Found",
        f"Trace `{ref}` was not found. Use one of these trace IDs.",
        search="" if ref_uuid else ref,
    )


def span_queryset(context: ToolContext):
    from tracer.models.observation_span import ObservationSpan

    qs = ObservationSpan.objects.select_related("trace", "project").filter(
        deleted=False,
        project__organization=context.organization,
        project__deleted=False,
    )
    if context.workspace:
        qs = qs.filter(
            Q(project__workspace=context.workspace)
            | Q(project__workspace__isnull=True)
        )
    return qs


def candidate_spans_result(
    context: ToolContext,
    title: str = "Span Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    qs = span_queryset(context)
    search = clean_ref(search)
    if search:
        qs = qs.filter(Q(id__icontains=search) | Q(name__icontains=search))
    spans = list(qs.order_by("-created_at")[:10])
    rows = [
        [
            span.name or "-",
            f"`{span.id}`",
            span.observation_type or "-",
            span.project.name if span.project else "-",
            format_datetime(span.created_at),
        ]
        for span in spans
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Name", "Span ID", "Type", "Project", "Created"],
            rows,
        )
    else:
        body = body or "No spans found in this workspace."
    return ToolResult.needs_input(
        body,
        data={
            "requires_span_id": True,
            "spans": [
                {
                    "id": str(span.id),
                    "name": span.name,
                    "type": span.observation_type,
                    "trace_id": str(span.trace_id) if span.trace_id else None,
                }
                for span in spans
            ],
        },
        missing_fields=["span_id"],
    )


def resolve_span(
    span_ref: Any,
    context: ToolContext,
    title: str = "Span Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = clean_ref(span_ref)
    if not ref:
        return None, candidate_spans_result(context, title)
    qs = span_queryset(context)
    span = qs.filter(id=ref).first()
    if span:
        return span, None
    matches = list(qs.filter(name__icontains=ref).order_by("-created_at")[:2])
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, candidate_spans_result(
            context,
            "Multiple Spans Matched",
            f"More than one span matched `{ref}`. Use a span ID.",
            search=ref,
        )
    return None, candidate_spans_result(
        context,
        "Span Not Found",
        f"Span `{ref}` was not found. Use one of these span IDs.",
        search=ref,
    )


def trace_annotation_queryset(context: ToolContext):
    from tracer.models.trace_annotation import TraceAnnotation

    qs = TraceAnnotation.objects.select_related(
        "annotation_label",
        "trace",
        "trace__project",
        "observation_span",
        "observation_span__project",
    ).filter(deleted=False)
    qs = qs.filter(
        Q(trace__project__organization=context.organization)
        | Q(observation_span__project__organization=context.organization)
    )
    if context.workspace:
        qs = qs.filter(
            Q(trace__project__workspace=context.workspace)
            | Q(trace__project__workspace__isnull=True)
            | Q(observation_span__project__workspace=context.workspace)
            | Q(observation_span__project__workspace__isnull=True)
        )
    return qs


def candidate_trace_annotations_result(
    context: ToolContext,
    title: str = "Trace Annotation Required",
    detail: str = "",
) -> ToolResult:
    annotations = list(trace_annotation_queryset(context).order_by("-created_at")[:10])
    rows = [
        [
            f"`{annotation.id}`",
            annotation.annotation_label.name if annotation.annotation_label else "-",
            str(annotation.trace_id) if annotation.trace_id else "-",
            format_datetime(annotation.created_at),
        ]
        for annotation in annotations
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Annotation ID", "Label", "Trace", "Created"],
            rows,
        )
    else:
        body = body or "No trace annotations found in this workspace."
    return ToolResult.needs_input(
        body,
        data={
            "requires_annotation_id": True,
            "annotations": [
                {
                    "id": str(annotation.id),
                    "label": (
                        annotation.annotation_label.name
                        if annotation.annotation_label
                        else None
                    ),
                    "trace_id": (
                        str(annotation.trace_id) if annotation.trace_id else None
                    ),
                }
                for annotation in annotations
            ],
        },
        missing_fields=["annotation_id"],
    )


def resolve_trace_annotation(
    annotation_ref: Any,
    context: ToolContext,
    title: str = "Trace Annotation Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = clean_ref(annotation_ref)
    if not ref:
        return None, candidate_trace_annotations_result(context, title)
    ref_uuid = uuid_text(ref)
    if not ref_uuid:
        return None, candidate_trace_annotations_result(
            context,
            "Invalid Trace Annotation ID",
            f"`{ref}` is not a valid annotation UUID. Use one of these IDs.",
        )
    annotation = trace_annotation_queryset(context).filter(id=ref_uuid).first()
    if annotation:
        return annotation, None
    return None, candidate_trace_annotations_result(
        context,
        "Trace Annotation Not Found",
        f"Trace annotation `{ref}` was not found. Use one of these IDs.",
    )


def annotation_label_queryset(context: ToolContext):
    from model_hub.models.develop_annotations import AnnotationsLabels

    qs = AnnotationsLabels.objects.select_related("project").filter(
        organization=context.organization,
        deleted=False,
    )
    if context.workspace:
        qs = qs.filter(Q(workspace=context.workspace) | Q(workspace__isnull=True))
    return qs.only(
        "id",
        "name",
        "type",
        "settings",
        "description",
        "created_at",
        "project_id",
        "workspace_id",
        "organization_id",
        "project__id",
        "project__name",
    )


def candidate_annotation_labels_result(
    context: ToolContext,
    title: str = "Annotation Label Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    qs = annotation_label_queryset(context)
    search = clean_ref(search)
    if search and not uuid_text(search):
        qs = qs.filter(name__icontains=search)
    labels = list(qs.order_by("-created_at")[:10])
    rows = [
        [
            label.name,
            f"`{label.id}`",
            label.type,
            label.project.name if label.project else "-",
        ]
        for label in labels
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Name", "Label ID", "Type", "Project"],
            rows,
        )
    else:
        body = body or "No annotation labels found in this workspace."
    return ToolResult.needs_input(
        body,
        data={
            "requires_annotation_label_id": True,
            "labels": [
                {"id": str(label.id), "name": label.name, "type": label.type}
                for label in labels
            ],
        },
        missing_fields=["annotation_label_id"],
    )


def resolve_annotation_label(
    label_ref: Any,
    context: ToolContext,
    title: str = "Annotation Label Required",
) -> tuple[Any | None, ToolResult | None]:
    ref = clean_ref(label_ref)
    if not ref:
        return None, candidate_annotation_labels_result(context, title)
    qs = annotation_label_queryset(context)
    ref_uuid = uuid_text(ref)
    if ref_uuid:
        label = qs.filter(id=ref_uuid).first()
        if label:
            return label, None
    else:
        exact = list(qs.filter(name__iexact=ref).order_by("-created_at")[:2])
        if len(exact) == 1:
            return exact[0], None
        if len(exact) > 1:
            return None, candidate_annotation_labels_result(
                context,
                "Multiple Annotation Labels Matched",
                f"More than one label matched `{ref}`. Use a label ID.",
                search=ref,
            )
    return None, candidate_annotation_labels_result(
        context,
        "Annotation Label Not Found",
        f"Annotation label `{ref}` was not found. Use one of these label IDs.",
        search="" if ref_uuid else ref,
    )


def candidate_eval_tasks_result(
    context: ToolContext,
    title: str = "Eval Task Required",
    detail: str = "",
    project_ref: Any = "",
    search: str = "",
) -> ToolResult:
    from tracer.models.eval_task import EvalTask

    qs = EvalTask.objects.select_related("project").filter(
        project__organization=context.organization,
        project__deleted=False,
        deleted=False,
    )
    if context.workspace:
        qs = qs.filter(
            Q(project__workspace=context.workspace)
            | Q(project__workspace__isnull=True)
        )
    project, project_result = resolve_project(project_ref, context) if project_ref else (None, None)
    if project_result:
        return project_result
    if project:
        qs = qs.filter(project=project)
    search = clean_ref(search)
    if search and not uuid_text(search):
        qs = qs.filter(name__icontains=search)
    tasks = list(qs.order_by("-created_at")[:10])
    rows = [
        [
            task.name or "-",
            f"`{task.id}`",
            task.project.name if task.project else "-",
            format_status(task.status),
            format_datetime(task.created_at),
        ]
        for task in tasks
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Name", "Task ID", "Project", "Status", "Created"],
            rows,
        )
    else:
        body = body or "No eval tasks found in this workspace."
    return ToolResult.needs_input(
        body,
        data={
            "requires_eval_task_ids": True,
            "tasks": [
                {
                    "id": str(task.id),
                    "name": task.name,
                    "project": task.project.name if task.project else None,
                    "status": task.status,
                }
                for task in tasks
            ],
        },
        missing_fields=["eval_task_ids"],
    )


def resolve_eval_tasks(
    task_refs: list[Any],
    context: ToolContext,
    project_ref: Any = "",
) -> tuple[list[Any], list[str], ToolResult | None]:
    from tracer.models.eval_task import EvalTask

    project, project_result = resolve_project(project_ref, context) if project_ref else (None, None)
    if project_result:
        return [], [], project_result
    qs = EvalTask.objects.filter(
        project__organization=context.organization,
        project__deleted=False,
        deleted=False,
    )
    if context.workspace:
        qs = qs.filter(
            Q(project__workspace=context.workspace)
            | Q(project__workspace__isnull=True)
        )
    if project:
        qs = qs.filter(project=project)

    resolved = []
    missing = []
    seen = set()
    for ref_value in task_refs:
        ref = clean_ref(ref_value)
        if not ref:
            continue
        task = None
        ref_uuid = uuid_text(ref)
        if ref_uuid:
            task = qs.filter(id=ref_uuid).first()
        else:
            exact = list(qs.filter(name__iexact=ref).order_by("-created_at")[:2])
            if len(exact) == 1:
                task = exact[0]
            elif len(exact) > 1:
                missing.append(f"{ref} (multiple tasks matched; use a task ID)")
                continue
        if task is None:
            missing.append(ref)
            continue
        if str(task.id) not in seen:
            resolved.append(task)
            seen.add(str(task.id))
    return resolved, missing, None
