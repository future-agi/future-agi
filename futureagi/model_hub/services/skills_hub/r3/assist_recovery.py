"""Recover original assist jobs without starting paid work. Pilot apply stays off."""

from __future__ import annotations

from uuid import UUID

from django.db.models import QuerySet

from model_hub.models.skills_hub_v11 import HubSkillAssistProposal
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal


def _owned(principal: SkillPrincipal) -> QuerySet[HubSkillAssistProposal]:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=False)
    return HubSkillAssistProposal.objects.filter(
        job__organization_id=principal.scope.organization_id,
        job__workspace_id=principal.scope.workspace_id,
        job__owner_user_id=principal.user_id,
        job__kind="assist",
        skill__organization_id=principal.scope.organization_id,
        skill__workspace_id=principal.scope.workspace_id,
        skill__deleted=False,
        skill__kind="skill",
    ).select_related("job")


def _receipt(proposal: HubSkillAssistProposal) -> dict:
    return {
        "id": str(proposal.pk),
        "skill_id": str(proposal.skill_id),
        "kind": proposal.kind,
        "status": proposal.job.status,
        "idempotency_key": str(proposal.job.idempotency_key),
        "base_revision": proposal.base_revision,
        "base_digest": proposal.base_digest,
        "payload_expires_at": proposal.payload_expires_at,
        "application_receipt": proposal.application_receipt,
        "created_at": proposal.job.created_at,
    }


def recover_assist_operation(
    *, principal: SkillPrincipal, skill_id: UUID, idempotency_key: UUID
) -> dict:
    proposal = (
        _owned(principal)
        .filter(skill_id=skill_id, job__idempotency_key=idempotency_key)
        .first()
    )
    if proposal is None:
        raise SkillsHubError(
            "resource_not_found",
            "No original assistance operation was found.",
            status=404,
        )
    return _receipt(proposal)


def list_assist_operations(
    *, principal: SkillPrincipal, skill_id: UUID, page: int = 1, page_size: int = 25
) -> dict:
    rows = (
        _owned(principal)
        .filter(skill_id=skill_id)
        .order_by("-job__created_at", "-job_id")
    )
    if (
        type(page) is not int
        or page < 1
        or type(page_size) is not int
        or not 1 <= page_size <= 100
    ):
        raise SkillsHubError(
            "invalid_selector", "Use a valid page and page size.", status=400
        )
    count = rows.count()
    return {
        "results": [
            _receipt(row) for row in rows[(page - 1) * page_size : page * page_size]
        ],
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": (count + page_size - 1) // page_size,
    }


def require_qualified_assistance() -> None:
    """No flag alone enables the unqualified deletion/apply/worker recovery journey."""
    raise SkillsHubError(
        "skills_unavailable",
        "Paid assistance remains unavailable during the supported-subset pilot.",
        status=503,
    )
