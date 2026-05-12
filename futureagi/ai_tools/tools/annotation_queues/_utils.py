from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError

from ai_tools.base import ToolContext, ToolResult
from ai_tools.formatting import markdown_table, section


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


def candidate_queues_result(
    context: ToolContext,
    title: str = "Annotation Queue Candidates",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    from model_hub.models.annotation_queues import AnnotationQueue

    queues_qs = AnnotationQueue.objects.filter(
        organization=context.organization,
        deleted=False,
    )
    search = clean_ref(search)
    if search:
        queues_qs = queues_qs.filter(name__icontains=search)
    queues = queues_qs.order_by("-created_at")[:10]
    rows = [
        [
            queue.name,
            f"`{queue.id}`",
            queue.status,
            str(queue.items.filter(deleted=False).count()),
        ]
        for queue in queues
    ]

    if not rows:
        body = detail or "No annotation queues found in this workspace."
    else:
        body = (detail + "\n\n" if detail else "") + markdown_table(
            ["Name", "ID", "Status", "Items"],
            rows,
        )

    return ToolResult(
        content=section(title, body),
        data={
            "requires_queue_id": True,
            "queues": [
                {
                    "id": str(queue.id),
                    "name": queue.name,
                    "status": queue.status,
                }
                for queue in queues
            ],
        },
    )


def resolve_queue(
    queue_ref: Any,
    context: ToolContext,
    title: str = "Annotation Queue Candidates",
) -> tuple[Any | None, ToolResult | None]:
    from model_hub.models.annotation_queues import AnnotationQueue

    ref = clean_ref(queue_ref)
    if not ref:
        return None, candidate_queues_result(context, title)

    queues_qs = AnnotationQueue.objects.filter(
        organization=context.organization,
        deleted=False,
    )
    ref_uuid = uuid_text(ref)
    try:
        if ref_uuid:
            return queues_qs.get(id=ref_uuid), None

        exact = queues_qs.filter(name__iexact=ref)
        exact_count = exact.count()
        if exact_count == 1:
            return exact.first(), None
        if exact_count > 1:
            return None, candidate_queues_result(
                context,
                "Multiple Annotation Queues Matched",
                f"More than one annotation queue matched `{ref}`. Use one of these IDs.",
                search=ref,
            )

        fuzzy = queues_qs.filter(name__icontains=ref)
        fuzzy_count = fuzzy.count()
        if fuzzy_count == 1:
            return fuzzy.first(), None
    except (AnnotationQueue.DoesNotExist, ValidationError, ValueError, TypeError):
        pass

    return None, candidate_queues_result(
        context,
        "Annotation Queue Not Found",
        f"Annotation queue `{ref}` was not found. Use one of these queue IDs instead.",
        search="" if ref_uuid else ref,
    )


def resolve_labels(
    label_refs: list[Any] | None, context: ToolContext
) -> tuple[list[Any], list[str]]:
    if not label_refs:
        return [], []

    from model_hub.models.develop_annotations import AnnotationsLabels

    labels = []
    missing = []
    seen = set()
    base_qs = AnnotationsLabels.objects.filter(
        organization=context.organization,
        deleted=False,
    )
    for value in label_refs:
        ref = clean_ref(value)
        if not ref:
            continue
        ref_uuid = uuid_text(ref)
        label = None
        if ref_uuid:
            label = base_qs.filter(id=ref_uuid).first()
        else:
            exact = list(base_qs.filter(name__iexact=ref).order_by("-created_at")[:2])
            if len(exact) == 1:
                label = exact[0]
            elif len(exact) > 1:
                missing.append(f"{ref} (multiple labels matched; use a label ID)")
                continue
            else:
                fuzzy = list(
                    base_qs.filter(name__icontains=ref).order_by("-created_at")[:2]
                )
                if len(fuzzy) == 1:
                    label = fuzzy[0]
                elif len(fuzzy) > 1:
                    missing.append(f"{ref} (multiple labels matched; use a label ID)")
                    continue
        if label is None:
            missing.append(ref)
            continue
        if label.id not in seen:
            labels.append(label)
            seen.add(label.id)
    return labels, missing


def resolve_users(user_refs: list[Any] | None, context: ToolContext) -> tuple[list[Any], list[str]]:
    if not user_refs:
        return [], []

    from django.db.models import Q

    from accounts.models.user import User

    users = []
    missing = []
    seen = set()
    base_qs = User.objects.filter(organization=context.organization)
    for value in user_refs:
        ref = clean_ref(value)
        if not ref:
            continue
        ref_uuid = uuid_text(ref)
        user = None
        if ref_uuid:
            user = base_qs.filter(id=ref_uuid).first()
        else:
            user = base_qs.filter(
                Q(email__iexact=ref) | Q(name__iexact=ref)
            ).first()
        if user is None:
            missing.append(ref)
            continue
        if user.id not in seen:
            users.append(user)
            seen.add(user.id)
    return users, missing


def candidate_queue_items_result(
    queue: Any,
    title: str = "Queue Item Candidates",
    detail: str = "",
) -> ToolResult:
    items = queue.items.filter(deleted=False).order_by("order", "created_at")[:10]
    rows = [
        [
            str(item.order),
            f"`{item.id}`",
            item.source_type,
            item.status,
        ]
        for item in items
    ]
    if not rows:
        body = detail or "No items are currently available in this queue."
    else:
        body = (detail + "\n\n" if detail else "") + markdown_table(
            ["Order", "Item ID", "Source Type", "Status"],
            rows,
        )
    return ToolResult(
        content=section(title, body),
        data={
            "requires_item_id": True,
            "queue_id": str(queue.id),
            "items": [
                {
                    "id": str(item.id),
                    "source_type": item.source_type,
                    "status": item.status,
                    "order": item.order,
                }
                for item in items
            ],
        },
    )


def resolve_queue_item(item_ref: Any, queue: Any) -> tuple[Any | None, ToolResult | None]:
    from model_hub.models.annotation_queues import QueueItem

    ref = clean_ref(item_ref)
    if not ref:
        return None, candidate_queue_items_result(queue)
    ref_uuid = uuid_text(ref)
    if not ref_uuid:
        return None, candidate_queue_items_result(
            queue,
            "Queue Item Not Found",
            f"`{ref}` is not a valid queue item ID. Use one of these items instead.",
        )
    try:
        return QueueItem.objects.get(id=ref_uuid, queue=queue, deleted=False), None
    except (QueueItem.DoesNotExist, ValidationError, ValueError, TypeError):
        return None, candidate_queue_items_result(
            queue,
            "Queue Item Not Found",
            f"Queue item `{ref}` was not found in `{queue.name}`. Use one of these items instead.",
        )
