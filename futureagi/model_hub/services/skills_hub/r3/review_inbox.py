"""Skills-specific recipient inbox. Reading is not approval or an automatic seen write."""

from __future__ import annotations

from uuid import UUID

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest

from model_hub.models.skills_hub_r3 import HubSkillReleaseRecipient
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.release_requests import get_release_request
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal


def list_review_inbox(
    *,
    principal: SkillPrincipal,
    page: int = 1,
    page_size: int = 25,
    status: str | None = None,
) -> dict:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    if (
        type(page) is not int
        or page < 1
        or type(page_size) is not int
        or not 1 <= page_size <= 100
    ):
        raise SkillsHubError(
            "invalid_selector", "Use a valid inbox page and page size.", status=400
        )
    if status is not None and status not in {
        "pending",
        "approved",
        "changes_requested",
        "released",
    }:
        raise SkillsHubError(
            "invalid_selector", "Choose a supported request status.", status=400
        )
    rows = (
        HubSkillReleaseRecipient.objects.filter(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            recipient_id=principal.user_id,
            request__skill__deleted=False,
        )
        .select_related("request", "request__skill")
        .order_by("-request__updated_at", "-id")
    )
    count, unread, results = 0, 0, []
    # Stream bounded batches, not all packet bodies. Current evidence access must be
    # checked before count/pagination; filtering only the returned page leaks totals.
    for recipient in rows.iterator(chunk_size=100):
        try:
            request, packet = get_release_request(
                principal=principal, request_id=recipient.request_id
            )
        except SkillsHubError as exc:
            if exc.code not in {
                "resource_not_found",
                "evidence_expired",
                "evidence_share_required",
                "evidence_mismatch",
            }:
                raise
            continue
        unseen = recipient.last_seen_revision < request.revision
        unread += int(unseen)
        if status is not None and request.status != status:
            continue
        if (page - 1) * page_size <= count < page * page_size:
            results.append(
                {
                    "id": str(request.pk),
                    "subject_id": str(request.skill_id),
                    "subject_kind": request.skill.kind,
                    "name": request.skill.name,
                    "author_id": str(request.author_id),
                    "revision": request.revision,
                    "status": request.status,
                    "unread": unseen,
                    "fingerprint": packet.fingerprint,
                    "candidate_version_id": str(packet.candidate_id),
                    "updated_at": request.updated_at,
                }
            )
        count += 1
    return {
        "results": results,
        "count": count,
        "unread_count": unread,
        "page": page,
        "page_size": page_size,
        "total_pages": (count + page_size - 1) // page_size,
    }


@transaction.atomic
def mark_review_seen(
    *, principal: SkillPrincipal, request_id: UUID, revision: int
) -> None:
    request, _ = get_release_request(principal=principal, request_id=request_id)
    if type(revision) is not int or revision < 1 or revision > request.revision:
        raise SkillsHubError(
            "review_conflict",
            "Only an observed packet revision can be marked seen.",
            status=409,
        )
    changed = HubSkillReleaseRecipient.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        request=request,
        recipient_id=principal.user_id,
    ).update(last_seen_revision=Greatest(F("last_seen_revision"), revision))
    if not changed:
        raise SkillsHubError("resource_not_found", "Inbox item not found.", status=404)
