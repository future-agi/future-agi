from typing import Any

from django.core.exceptions import ValidationError

from ai_tools.base import ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    markdown_table,
    section,
    truncate,
)
from ai_tools.tools.annotation_queues._utils import clean_ref, uuid_text


def candidate_users_result(
    context: ToolContext,
    title: str = "Candidate Users",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    from django.db.models import Q

    from accounts.models.user import User

    qs = User.objects.filter(organization=context.organization).order_by(
        "-is_active", "email"
    )
    search = clean_ref(search)
    if search:
        qs = qs.filter(Q(email__icontains=search) | Q(name__icontains=search))

    users = list(qs[:10])
    rows = []
    for user in users:
        status = "active" if user.is_active else "inactive"
        rows.append(
            [
                truncate(user.email, 42),
                truncate(user.name or "-", 32),
                f"`{user.id}`",
                user.organization_role or "-",
                format_status(status),
                format_datetime(user.created_at),
            ]
        )

    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Email", "Name", "ID", "Role", "Status", "Created"],
            rows,
        )
    else:
        body = body or "No users found in this organization."

    return ToolResult(
        content=section(title, body),
        data={
            "requires_user_id": True,
            "users": [
                {
                    "id": str(user.id),
                    "email": user.email,
                    "name": user.name,
                    "role": user.organization_role,
                    "is_active": user.is_active,
                }
                for user in users
            ],
        },
    )


def resolve_user(
    user_ref: Any,
    context: ToolContext,
    title: str = "Candidate Users",
) -> tuple[Any | None, ToolResult | None]:
    from django.db.models import Q

    from accounts.models.user import User

    ref = clean_ref(user_ref)
    if not ref:
        return None, candidate_users_result(context, title)

    qs = User.objects.filter(organization=context.organization).order_by(
        "-is_active", "email"
    )
    ref_uuid = uuid_text(ref)
    try:
        if ref_uuid:
            return qs.get(id=ref_uuid), None

        exact = qs.filter(Q(email__iexact=ref) | Q(name__iexact=ref))
        if exact.count() == 1:
            return exact.first(), None
        if exact.count() > 1:
            return None, candidate_users_result(
                context,
                "Multiple Users Matched",
                f"More than one user matched `{ref}`. Use one of these IDs.",
                search=ref,
            )

        fuzzy = qs.filter(Q(email__icontains=ref) | Q(name__icontains=ref))
        if fuzzy.count() == 1:
            return fuzzy.first(), None
    except (User.DoesNotExist, ValidationError, ValueError, TypeError):
        pass

    return None, candidate_users_result(
        context,
        "User Not Found",
        f"User `{ref}` was not found. Use one of these IDs instead.",
        search="" if ref_uuid else ref,
    )


def candidate_api_keys_result(
    context: ToolContext,
    title: str = "Candidate API Keys",
    detail: str = "",
    enabled_only: bool = False,
) -> ToolResult:
    from accounts.models.user import OrgApiKey

    qs = OrgApiKey.no_workspace_objects.filter(
        organization=context.organization
    ).order_by("-enabled", "-created_at")
    if enabled_only:
        qs = qs.filter(enabled=True)

    keys = list(qs[:10])
    rows = []
    for key in keys:
        status = "active" if key.enabled else "inactive"
        workspace_name = key.workspace.name if key.workspace else "-"
        masked_key = f"{key.api_key[:8]}..." if key.api_key else "-"
        rows.append(
            [
                truncate(key.name or "-", 32),
                f"`{key.id}`",
                key.type,
                masked_key,
                truncate(workspace_name, 32),
                format_status(status),
                format_datetime(key.created_at),
            ]
        )

    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["Name", "ID", "Type", "Key Prefix", "Workspace", "Status", "Created"],
            rows,
        )
    else:
        qualifier = "active " if enabled_only else ""
        body = body or f"No {qualifier}API keys found in this organization."

    return ToolResult(
        content=section(title, body),
        data={
            "requires_key_id": True,
            "api_keys": [
                {
                    "id": str(key.id),
                    "name": key.name,
                    "type": key.type,
                    "key_prefix": f"{key.api_key[:8]}..." if key.api_key else None,
                    "workspace": key.workspace.name if key.workspace else None,
                    "enabled": key.enabled,
                }
                for key in keys
            ],
        },
    )
