"""Scoped discovery for the author composer; IDs are selected, never transcribed."""

from django.utils import timezone

from accounts.models.organization_membership import OrganizationMembership
from model_hub.models.skills_hub import HubSkillVersion
from model_hub.models.skills_hub_r3 import (
    HubSkillNativeEvidence,
    HubSkillReleaseRequest,
)
from model_hub.models.skills_hub_v11 import HubSkillTestRun
from model_hub.models.skills_hub_v12 import HubSkillContextBinding
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.human_review import evidence_reference
from model_hub.services.skills_hub.r3.release_requests import (
    _authorize,
    _recipient_principal,
    _scope,
    _skill,
)


def release_options(*, principal, resource_id, kind, candidate_version_id):
    _authorize(principal, write=True)
    skill = _skill(principal, resource_id, kind)
    candidate = HubSkillVersion.objects.filter(
        skill=skill, pk=candidate_version_id
    ).first()
    if candidate is None:
        raise SkillsHubError("version_not_found", "Candidate not found.", status=404)
    recipients = []
    members = (
        OrganizationMembership.no_workspace_objects.filter(
            organization_id=principal.scope.organization_id,
            is_active=True,
        )
        .exclude(user_id=principal.user_id)
        .select_related("user")
        .order_by("user_id")
    )
    for member in members.iterator(chunk_size=100):
        try:
            _authorize(_recipient_principal(principal, member.user_id), write=True)
        except SkillsHubError:
            continue
        recipients.append(
            {"id": member.user_id, "name": member.user.name or member.user.email}
        )
    bindings = (
        HubSkillContextBinding.objects.filter(
            **_scope(principal),
            context=skill,
            retired_at__isnull=True,
        )
        .exclude(configuration_digest="")
        .order_by("name", "id")
    )
    targets = [
        {
            "name": row.name,
            "target": {
                "binding_id": str(row.pk),
                "environment_id": None,
                "revision": row.revision,
                "configuration_digest": row.configuration_digest,
            },
        }
        for row in bindings
    ]
    now = timezone.now()
    text = (
        HubSkillTestRun.objects.filter(
            job__skill=skill,
            job__organization_id=principal.scope.organization_id,
            job__workspace_id=principal.scope.workspace_id,
            candidate_digest=candidate.digest,
            payload_purged_at__isnull=True,
            payload_expires_at__gt=now,
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    native = (
        HubSkillNativeEvidence.objects.filter(
            **_scope(principal),
            skill=skill,
            candidate_digest=candidate.digest,
            payload_purged_at__isnull=True,
            payload_expires_at__gt=now,
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    evidence = []
    for source, ids in (("skills_text", text), ("simulation_native", native)):
        for source_id in ids.iterator(chunk_size=100):
            try:
                evidence.append(
                    evidence_reference(
                        principal=principal, source=source, source_id=source_id
                    )
                )
            except SkillsHubError as exc:
                if exc.code not in {
                    "resource_not_found",
                    "evidence_expired",
                    "evidence_mismatch",
                }:
                    raise
    # The composer lists each run with its own reduced outcome (the same summary the
    # review packet shows), so a verified run never reads as "result not recorded".
    from model_hub.services.skills_hub.r3.release_inspection import _summary

    for ref in evidence:
        ref["outcome"], ref["counts"] = None, None
        summary = _summary(principal, ref)
        if ref["source"] == "skills_text":
            if summary["availability"] != "available":
                continue
            ref["outcome"] = summary["answer"]
        else:
            if summary["result"] is None:
                continue
            ref["outcome"] = summary["result"]["state"]
        ref["counts"] = {
            "graded": summary["graded"],
            "planned": summary["planned"],
            "regressed": summary["regressions"],
            "disagreements": summary["disagreements"],
        }
    return {"recipients": recipients, "targets": targets, "evidence": evidence}


def authored_requests(*, principal, page=1, page_size=25, status=None):
    _authorize(principal, write=True)
    rows = (
        HubSkillReleaseRequest.objects.filter(
            **_scope(principal),
            author_id=principal.user_id,
            skill__deleted=False,
        )
        .select_related("skill")
        .order_by("-updated_at", "-id")
    )
    if status:
        rows = rows.filter(status=status)
    count = rows.count()
    # Only metadata, not private packet/evidence bodies. Opening remains authorized
    # by get_release_request and cannot expose another owner's private evidence.
    results = [
        {
            "id": row.pk,
            "revision": row.revision,
            "status": row.status,
            "subject_id": row.skill_id,
            "subject_kind": row.skill.kind,
            "name": row.skill.name,
            "author_id": row.author_id,
            "updated_at": row.updated_at,
            "unread": False,
        }
        for row in rows[(page - 1) * page_size : page * page_size]
    ]
    return {
        "results": results,
        "count": count,
        "unread_count": 0,
        "page": page,
        "page_size": page_size,
        "total_pages": (count + page_size - 1) // page_size,
    }
